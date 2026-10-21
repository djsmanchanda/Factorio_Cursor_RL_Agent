# Path: tools/run_laya_batch.py | Purpose: Run a matched-seed Laya experiment batch (one factor per arm) at a fixed concurrency until a deadline, and report per-arm outcomes.
"""Fixed-concurrency experiment batch over frozen code arms.

Every arm runs the same seeds from the same base save. Jobs are ordered seed-major
and arm round-robin, so a deadline cut leaves the arms matched. Each trial is a
fresh `run_laya_clean prepare` + `run` with a game-tick window and a wall cap;
the world is stopped afterwards. Concurrency is a fixed worker-pool ceiling;
an optional host budget gates admissions across cohorts. Unstarted jobs stay
listed as such in job-status.json.

batch.json:
  {"name": str, "until": ISO time, "concurrency": int, "game_port_base": int, "rcon_port_base": int,
   "trial_wall_seconds": int, "max_game_ticks": int, "source_save": path, "python": path,
   "seeds": [int, ...],
   "host_budget": optional {"registry": path, "stale_seconds": 180,
                             "admit_interval_seconds": 10, "admit_min_available_gb": 4.5},
   "runtime": optional {"update_threads": 1..64, "cpu_affinity": "slot" | taskset list,
                        "nice": 0..19, "python_nice": 0..19},
   "arms": [{"id": str, "code": frozen worktree, "mods": staged mods dir, "revision": sha,
             "change": str, "variant": optional path, "extra_args": optional [str],
             "runtime": optional arm override of the batch runtime}]}
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import signal
import statistics
import subprocess
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path

PHASE = re.compile(r"^\+(\d+)s PHASE REACHED: (C\d) .* at tick (\d+)")
SUMMARY = re.compile(r"SUMMARY: stop=(\S+) decisions=(\d+) failures=(\d+) phases=(\S*) accepted=(\S*)")


def _atomic(path: Path, data: object) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


class HostBudget:
    """Serialize reservations against a fresh scheduler gate, counting legacy worlds too."""

    def __init__(self, root: Path, options: dict) -> None:
        self.registry = Path(options["registry"]).resolve()
        if root.resolve().parent != self.registry:
            raise ValueError("host-budget cohort must be directly inside its registry")
        self.stale_s = float(options.get("stale_seconds", 180))
        self.interval_s = float(options.get("admit_interval_seconds", 10))
        self.min_gb = float(options.get("admit_min_available_gb", 4.5))
        self.next_admit = 0.0
        self.reason = "waiting_for_gate"

    @contextmanager
    def lock(self):
        with (self.registry / ".host-admission.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def may_admit(self, now: float) -> bool:
        # A missing/stale gate blocks admissions, including after a timed pause.
        # Old cohort caps are not host limits; their actual worlds still count.
        try:
            gate = json.loads((self.registry / "_host-scheduler-gate/job-status.json").read_text())
            cap = gate["controller"]["slots"]
            age = now - float(gate["updated"])
            if type(cap) is not int or cap < 0 or not 0 <= age <= self.stale_s:
                raise ValueError("invalid or stale gate")
            occupied = 0
            for path in self.registry.glob("*/job-status.json"):
                if path.parent.name.startswith("_host-scheduler"):
                    continue
                data = json.loads(path.read_text())
                # Stale running records remain reserved until the owner reconciles
                # them; a dead supervisor may have left its Factorio worlds alive.
                occupied += sum(bool(job.get("admission_reserved")) or
                                job.get("status") in {"preparing", "running"}
                                for job in data["jobs"])
        except (OSError, ValueError, KeyError, TypeError):
            self.reason = "unavailable_host_budget"
            return False
        memory = None
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    memory = int(line.split()[1]) / 1048576
        except OSError:
            pass
        if occupied >= cap:
            self.reason = "host_full_or_paused"
        elif memory is not None and memory < self.min_gb:
            self.reason = "memory"
        elif now < self.next_admit:
            self.reason = "admission_pacing"
        else:
            self.reason = "admitted"
            return True
        return False


def summarize(world: Path) -> dict:
    """Outcome facts from the runner log: stop, phases with their seconds/ticks, strict acceptance."""
    log = world / "logs/laya-runner.log"
    if not log.exists():
        return {"runner_stop": None}
    start_tick, phases, out = None, {}, {"runner_stop": None}
    for line in log.read_text(errors="replace").splitlines():
        if (match := PHASE.match(line)):
            phases[match.group(2)] = {"seconds": int(match.group(1)), "tick": int(match.group(3))}
        elif (match := SUMMARY.search(line)):
            out.update(runner_stop=match.group(1), decisions=int(match.group(2)), failures=int(match.group(3)),
                       phases=[p for p in match.group(4).split(",") if p and p != "none"],
                       accepted=[p for p in match.group(5).split(",") if p and p != "none"])
        elif start_tick is None and " tick=" in line and "DECISION 0 " in line:
            start_tick = int(line.split(" tick=")[1].split()[0])
    for phase in phases.values():
        phase["game_seconds"] = (phase["tick"] - start_tick) / 60 if start_tick else None
    out["phase_times"] = phases
    out["untyped_stuck"] = log.read_text(errors="replace").count("OUTCOME: untyped_stuck")
    return out


class Batch:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.root = path.parent
        self.cfg = json.loads(path.read_text())
        self.until = dt.datetime.fromisoformat(self.cfg["until"]).timestamp()
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.jobs = [{"id": f"{arm['id']}-s{seed}", "arm": arm["id"], "seed": seed, "status": "queued"}
                     for seed in self.cfg["seeds"] for arm in self.cfg["arms"]]
        self.arms = {arm["id"]: arm for arm in self.cfg["arms"]}
        self.host = HostBudget(self.root, self.cfg["host_budget"]) if self.cfg.get("host_budget") else None

    def submit(self, pool, job: dict, slot: int):
        with self.host.lock() if self.host else nullcontext():
            if self.host and not self.host.may_admit(time.time()):
                return None
            job.update(status="preparing", admission_reserved=True)
            self.save()  # Publish the reservation before another cohort can admit.
            try:
                future = pool.submit(self.run_job, job, slot)
            except Exception:
                job.update(status="queued", admission_reserved=False)
                self.save()
                raise
            if self.host:
                self.host.next_admit = time.time() + self.host.interval_s
            return future

    def save(self) -> None:
        with self.lock:
            _atomic(self.root / "job-status.json", {"name": self.cfg["name"], "updated": time.time(), "jobs": self.jobs,
                    **({"host_budget": {"reason": self.host.reason}} if self.host else {})})

    def _runtime(self, arm: dict) -> dict:
        runtime = dict(self.cfg.get("runtime") or {})
        runtime.update(arm.get("runtime") or {})
        return runtime

    @staticmethod
    def _cpu_for_slot(slot: int) -> str:
        """Map a slot to an allowed logical CPU, respecting a cgroup/cpuset mask."""
        try:
            cpus = sorted(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            cpus = list(range(os.cpu_count() or 1))
        return str(cpus[slot % len(cpus)])

    def _cmd(self, arm: dict, *args: str) -> list[str]:
        command = [self.cfg["python"], str(Path(arm["code"]) / "tools/run_laya_clean.py"), *args]
        # Prepare launches Factorio, so lowering its Python priority would also
        # lower the engine's inherited priority. Apply this only to the runner.
        nice = int(self._runtime(arm).get("python_nice", 0)) if args and args[0] == "run" else 0
        return ["nice", "-n", str(nice), *command] if nice else command

    def runtime_args(self, slot: int, arm: dict | None = None) -> list[str]:
        """Per-world engine settings shared by every arm, with an optional arm override."""
        runtime = self._runtime(arm or {})
        args: list[str] = []
        threads = runtime.get("update_threads")
        if threads is not None:
            args += ["--update-threads", str(threads)]
        affinity = runtime.get("cpu_affinity")
        if affinity == "slot":
            affinity = self._cpu_for_slot(slot)
        if affinity:
            args += ["--cpu-affinity", str(affinity)]
        nice = int(runtime.get("nice", 0))
        if nice:
            args += ["--nice", str(nice)]
        return args

    def run_job(self, job: dict, slot: int) -> None:
        arm = self.arms[job["arm"]]
        code = Path(arm["code"])
        world = code / "data/laya-saved-world" / self.cfg["name"] / job["id"]
        job["world"] = str(world)
        log_path = self.root / "logs" / f"{job['id']}.out"
        env = dict(os.environ, PYTHONPATH=str(code), PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
        ports = ["--game-port", str(self.cfg["game_port_base"] + slot), "--rcon-port", str(self.cfg["rcon_port_base"] + slot)]
        try:
            with log_path.open("a") as log:
                job["status"] = "preparing"
                prepared = subprocess.run(
                    self._cmd(arm, "prepare", "--root", str(world), "--source-save", self.cfg["source_save"],
                              "--mods-source", arm["mods"], *ports, *self.runtime_args(slot, arm)),
                    cwd=code, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=240)
                if prepared.returncode:
                    raise RuntimeError(f"prepare exit {prepared.returncode}")
                run = self._cmd(arm, "run", "--root", str(world), "--seed", str(job["seed"]), *ports,
                                "--actions", "deterministic", "--policy", "guided", "--speed", "2", "--decisions", "0",
                                "--no-helper", "--max-game-ticks", str(self.cfg["max_game_ticks"]),
                                "--experiment", self.cfg["name"], *(["--variant", arm["variant"]] if arm.get("variant") else []),
                                *arm.get("extra_args", []))
                process = subprocess.Popen(run, cwd=code, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                job.update(status="running", started=time.time())
                self.save()
                end = min(job["started"] + self.cfg["trial_wall_seconds"], self.until)
                while process.poll() is None and time.time() < end and not self.stopping.is_set():
                    time.sleep(1)
                if process.poll() is None:  # The runner stops after its current decision on SIGTERM.
                    job["status"] = "interrupted" if self.stopping.is_set() else "wall_cap"
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=120)
                    except subprocess.TimeoutExpired:
                        process.kill()
                else:
                    job["status"] = "completed" if process.returncode == 0 else "runner_error"
                job["elapsed_s"] = round(time.time() - job["started"])
                job.update(summarize(world))
        except Exception as error:
            job.update(status="harness_error" if "started" in job else "prepare_failed", error=f"{type(error).__name__}: {error}")
        finally:
            try:
                subprocess.run(self._cmd(arm, "stop", "--root", str(world)), cwd=code, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=True)
                job["admission_reserved"] = False
            finally:
                self.save()  # A failed cleanup retains the reservation for reconciliation.

    def run(self) -> None:
        (self.root / "logs").mkdir(exist_ok=True)
        signal.signal(signal.SIGTERM, lambda *_: self.stopping.set())
        slots = list(range(self.cfg["concurrency"]))
        active: dict = {}
        with self.host.lock() if self.host else nullcontext():
            if (self.root / "job-status.json").exists():
                raise ValueError("existing cohort status: use its frozen supervisor; this harness starts fresh cohorts")
            self.save()
        with ThreadPoolExecutor(self.cfg["concurrency"]) as pool:
            queue = list(self.jobs)
            while (queue or active) and not (self.stopping.is_set() and not active):
                for future in [f for f in active if f.done()]:
                    future.result()
                    slots.append(active.pop(future))
                # A trial that cannot finish its wall window before the deadline is not started (censored, listed).
                while queue and slots and not self.stopping.is_set():
                    if time.time() + self.cfg["trial_wall_seconds"] > self.until:
                        for job in queue:
                            job["status"] = "not_started_deadline"
                        queue.clear()
                        break
                    job, slot = queue[0], slots[0]
                    future = self.submit(pool, job, slot)
                    if future is None:
                        break
                    queue.pop(0)
                    slots.pop(0)
                    active[future] = slot
                if self.stopping.is_set():
                    for job in queue:
                        job["status"] = "not_started_interrupted"
                    queue.clear()
                time.sleep(2)
                self.save()  # Keep queued and long-running cohorts visible to the scheduler.
        self.save()
        self.report()

    def report(self) -> None:
        rows = defaultdict(list)
        for job in self.jobs:
            rows[job["arm"]].append(job)
        lines = [f"# {self.cfg['name']}", "", f"Planned jobs: {len(self.jobs)}; statuses: {dict(Counter(j['status'] for j in self.jobs))}", "",
                 "| Arm | Run | Stops | Proxy C1/C2/C3/C4 | Median game-min to C2 | Strict accepted (any) | untyped_stuck |",
                 "|---|---:|---|---|---:|---|---:|"]
        for arm, jobs in rows.items():
            ran = [j for j in jobs if j.get("runner_stop") or j["status"] in ("completed", "wall_cap", "runner_error")]
            stops = dict(Counter(j.get("runner_stop") or j["status"] for j in ran))
            reached = [sum(p in (j.get("phases") or []) for j in ran) for p in ("C1", "C2", "C3", "C4")]
            c2 = [j["phase_times"]["C2"]["game_seconds"] / 60 for j in ran
                  if "C2" in (j.get("phase_times") or {}) and j["phase_times"]["C2"].get("game_seconds") is not None]
            accepted = dict(Counter(p for j in ran for p in (j.get("accepted") or [])))
            median_c2 = f"{statistics.median(c2):.1f} (n={len(c2)})" if c2 else "-"
            lines.append(f"| {arm} | {len(ran)}/{len(jobs)} | {stops} | {'/'.join(map(str, reached))} | {median_c2} | "
                         f"{accepted} | {sum(j.get('untyped_stuck', 0) for j in ran)} |")
        lines += ["", "Arms:"] + [f"- {a['id']} @ {a.get('revision', '?')[:9]}: {a.get('change', '')}" for a in self.cfg["arms"]]
        (self.root / "report.md").write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("batch", type=Path)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args(argv)
    batch = Batch(args.batch.resolve())
    if args.report_only:
        batch.jobs = json.loads((batch.root / "job-status.json").read_text())["jobs"]
        batch.report()
        return 0
    batch.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
