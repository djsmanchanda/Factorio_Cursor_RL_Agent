# Path: tools/run_laya_batch.py | Purpose: Run a matched-seed Laya experiment batch (one factor per arm) at a fixed concurrency until a deadline, and report per-arm outcomes.
"""Fixed-concurrency experiment batch over frozen code arms.

Every arm runs the same seeds from the same base save. Jobs are ordered seed-major
and arm round-robin, so a deadline cut leaves the arms matched. Each trial is a
fresh `run_laya_clean prepare` + `run` with a game-tick window and a wall cap;
the world is stopped afterwards. Concurrency is fixed (no adaptive ramp: the host
is a laptop). Unstarted jobs stay listed as such in job-status.json.

batch.json:
  {"name": str, "until": ISO time, "concurrency": int, "game_port_base": int, "rcon_port_base": int,
   "trial_wall_seconds": int, "max_game_ticks": int, "source_save": path, "python": path,
   "seeds": [int, ...],
   "arms": [{"id": str, "code": frozen worktree, "mods": staged mods dir, "revision": sha,
             "change": str, "variant": optional path, "extra_args": optional [str]}]}
"""

from __future__ import annotations

import argparse
import datetime as dt
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
from pathlib import Path

PHASE = re.compile(r"^\+(\d+)s PHASE REACHED: (C\d) .* at tick (\d+)")
SUMMARY = re.compile(r"SUMMARY: stop=(\S+) decisions=(\d+) failures=(\d+) phases=(\S*) accepted=(\S*)")


def _atomic(path: Path, data: object) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


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

    def save(self) -> None:
        with self.lock:
            _atomic(self.root / "job-status.json", {"name": self.cfg["name"], "updated": time.time(), "jobs": self.jobs})

    def _cmd(self, arm: dict, *args: str) -> list[str]:
        return [self.cfg["python"], str(Path(arm["code"]) / "tools/run_laya_clean.py"), *args]

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
                              "--mods-source", arm["mods"], *ports),
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
            subprocess.run(self._cmd(arm, "stop", "--root", str(world)), cwd=code, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            self.save()

    def run(self) -> None:
        (self.root / "logs").mkdir(exist_ok=True)
        signal.signal(signal.SIGTERM, lambda *_: self.stopping.set())
        slots = list(range(self.cfg["concurrency"]))
        active: dict = {}
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
                    job, slot = queue.pop(0), slots.pop(0)
                    active[pool.submit(self.run_job, job, slot)] = slot
                if self.stopping.is_set():
                    for job in queue:
                        job["status"] = "not_started_interrupted"
                    queue.clear()
                time.sleep(2)
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
