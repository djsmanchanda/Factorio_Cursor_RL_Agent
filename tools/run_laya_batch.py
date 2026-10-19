# Path: tools/run_laya_batch.py | Purpose: Run a matched-seed Laya experiment batch (one factor per arm) at an adaptive concurrency until a deadline, and report per-arm outcomes.
"""Adaptive-concurrency experiment batch over frozen code arms.

Every arm runs the same seeds from the same base save. Jobs are ordered seed-major
with the arm order rotated per seed; the deadline admits whole seed blocks, so the arms stay matched. Each trial is a
fresh `run_laya_clean prepare` + `run` with a game-tick window and a wall cap;
the world is stopped afterwards. Unstarted jobs stay listed as such in job-status.json.

Concurrency adapts by itself (SlotController): every window it measures each running
world's UPS from its own runner log (game ticks per wall second between decisions)
and the Laya decision loop's health (fresh heartbeat, decisions still advancing, no
new tracebacks, timeouts or RCON connection errors), plus host memory. It grows one slot
after consecutive healthy windows and shrinks one on any unhealthy window, using the
RL scheduler's hysteresis. Shrinking only stops new starts; running trials finish.
Ports are reserved for `max_concurrency` slots. Every decision is journaled to
controller.jsonl and each job records the slots it started under. Seed-major order
keeps both arms of a seed under the same load, so the comparison stays matched.

batch.json:
  {"name": str, "until": ISO time, "concurrency": int, "game_port_base": int, "rcon_port_base": int,
   "trial_wall_seconds": int, "max_game_ticks": int, "source_save": path, "python": path,
   "seeds": [int, ...], "max_concurrency": optional int (default concurrency + 4),
   "adaptive": optional false | {"min", "window_seconds", "min_ups_p95", "min_ups_p98",
               "grow_after", "min_available_gb", "max_decision_gap_s", "target_ups"},
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

from training.scheduler import AdaptiveScaleState, UpsWindow, adjust_adaptive_slots

PHASE = re.compile(r"^\+(\d+)s PHASE REACHED: (C\d) .* at tick (\d+)")
SUMMARY = re.compile(r"SUMMARY: stop=(\S+) decisions=(\d+) failures=(\d+) phases=(\S*) accepted=(\S*)")
FINAL_ACCEPTANCE = re.compile(r"\baccepted_final=(\S+)")
DECISION = re.compile(r"^\+(\d+)s DECISION \d+ .*?\btick=(\d+)", re.M)
# Load symptoms only: a plain tool_error is usually a logic bug and must not ratchet the slot count down.
UNSTABLE = re.compile(r"Traceback|TimeoutError|timed out|Connection(?:Refused|Reset)Error")
_TAIL_BYTES = 262144


def _available_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1048576
    except OSError:
        pass
    return None


def world_signals(world: Path, window_s: float, offsets: dict[str, int]) -> dict:
    """One running world over the last window: UPS from its decisions, loop liveness, new error lines."""
    out: dict = {"ups": None, "decision_gap_s": None, "errors": 0}
    log = world / "logs/laya-runner.log"
    for path in (log, world / "laya-runner.heartbeat.json"):
        if not path.exists():
            return out
    size = log.stat().st_size
    with log.open("rb") as handle:
        handle.seek(max(0, size - _TAIL_BYTES))
        tail = handle.read().decode(errors="replace")
    points = [(int(e), int(t)) for e, t in DECISION.findall(tail)]
    if points:
        last_e = points[-1][0]
        recent = [p for p in points if p[0] >= last_e - window_s]
        span = recent[-1][0] - recent[0][0]
        if span >= min(60.0, window_s / 2) and recent[-1][1] > recent[0][1]:
            out["ups"] = (recent[-1][1] - recent[0][1]) / span
        try:
            beat = json.loads((world / "laya-runner.heartbeat.json").read_text())
            out["decision_gap_s"] = float(beat.get("elapsed_s", last_e)) - last_e
        except (OSError, ValueError):
            pass
    start = offsets.get(str(log), max(0, size - _TAIL_BYTES))
    if size > start:
        with log.open("rb") as handle:
            handle.seek(start)
            out["errors"] = len(UNSTABLE.findall(handle.read().decode(errors="replace")))
    offsets[str(log)] = size
    out["heartbeat_age_s"] = time.time() - (world / "laya-runner.heartbeat.json").stat().st_mtime
    return out


class SlotController:
    """Automatic slot count from UPS stability and Laya decision-loop health (no operator input)."""

    def __init__(self, cfg: dict, journal: Path) -> None:
        options = cfg.get("adaptive", {})
        self.enabled = options is not False
        options = options or {}
        start = int(cfg["concurrency"])
        self.maximum = int(cfg.get("max_concurrency", start + 4))
        self.minimum = int(options.get("min", max(1, start // 2)))
        target_ups = float(options.get("target_ups", 120.0))  # the batch runs every trial at speed 2
        self.window_s = float(options.get("window_seconds", 300))
        self.min_p95 = float(options.get("min_ups_p95", 0.85 * target_ups))
        self.min_p98 = float(options.get("min_ups_p98", 0.75 * target_ups))
        self.grow_after = int(options.get("grow_after", 2))
        self.min_gb = float(options.get("min_available_gb", 3.0))
        self.max_gap_s = float(options.get("max_decision_gap_s", 600))
        # After an up/down/up (or down/up/down) flip-flop, stop growing for this many windows.
        self.settle_windows = int(options.get("settle_windows", 6))
        self.moves: list[int] = []
        self.settle_until = 0.0
        self.state = AdaptiveScaleState(min(self.maximum, max(self.minimum, start)))
        self.journal = journal
        self.next_at = 0.0  # the first pass journals the starting state; worlds count once they ran a full window
        self.offsets: dict[str, int] = {}
        self.last: dict = {"decision": "start", "slots": self.state.slots}

    @property
    def slots(self) -> int:
        return self.state.slots

    def update(self, running: list[dict], queued: int, now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        if not self.enabled or now < self.next_at:
            return None
        self.next_at = now + self.window_s
        measured = [j for j in running if j.get("status") == "running" and now - j.get("started", now) >= self.window_s]
        signals = {j["id"]: world_signals(Path(j["world"]), self.window_s, self.offsets) for j in measured}
        unstable = sorted(i for i, s in signals.items() if s["errors"] or (s["decision_gap_s"] or 0) > self.max_gap_s
                          or s.get("heartbeat_age_s", 0) > 60)
        samples = [s["ups"] for s in signals.values() if s["ups"]]
        memory = _available_gb()
        before = self.state.slots
        if memory is not None and memory < self.min_gb:
            self.state, decision = AdaptiveScaleState(max(self.minimum, before - 1)), "decrease_memory"
        elif unstable:
            self.state, decision = AdaptiveScaleState(max(self.minimum, before - 1)), "decrease_laya_unstable"
        else:
            state, decision = adjust_adaptive_slots(
                self.state, UpsWindow(tuple(samples)) if samples else None, minimum=self.minimum,
                maximum=self.maximum, step=1, minimum_safe_ups_p95=self.min_p95, minimum_safe_ups_p98=self.min_p98,
                healthy_windows_to_grow=self.grow_after, unhealthy_windows_to_shrink=1)
            if decision == "increase_safe_ups" and (not queued or len(running) < before):
                # Grow only when the current slots are all in use and work is waiting; keep the healthy streak.
                state, decision = AdaptiveScaleState(before, self.state.healthy_windows + 1, 0), "hold_no_demand"
            self.state = state
        if self.state.slots > before and now < self.settle_until:
            self.state, decision = AdaptiveScaleState(before), "hold_settling"
        if self.state.slots != before:
            self.moves = (self.moves + [1 if self.state.slots > before else -1])[-3:]
            if len(self.moves) == 3 and self.moves[0] == self.moves[2] != self.moves[1]:
                self.settle_until, self.moves = now + self.settle_windows * self.window_s, []
                decision += "+settle"
        self.last = {"at": now, "decision": decision, "slots_before": before, "slots": self.state.slots,
                     "running": len(running), "measured": len(measured), "queued": queued,
                     "ups_min": min(samples) if samples else None,
                     "ups_mean": statistics.fmean(samples) if samples else None,
                     "unstable": unstable, "available_gb": memory,
                     "settling": now < self.settle_until}
        with self.journal.open("a") as out:
            out.write(json.dumps(self.last, sort_keys=True) + "\n")
        return decision


def _atomic(path: Path, data: object) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def summarize(world: Path) -> dict:
    """Outcome facts, separating ever-passed predicates from the final observation."""
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
            out["accepted_ever"] = list(out["accepted"])
            final = FINAL_ACCEPTANCE.search(line)
            out["accepted_final"] = (None if final is None or final.group(1) == "unknown" else
                                     [p for p in final.group(1).split(",") if p and p != "none"])
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
        # Seed-major, with the arm order rotated per seed so no arm always starts first (or last) in a block.
        arms = self.cfg["arms"]
        self.jobs = [{"id": f"{arm['id']}-s{seed}", "arm": arm["id"], "seed": seed, "status": "queued"}
                     for index, seed in enumerate(self.cfg["seeds"])
                     for arm in arms[index % len(arms):] + arms[:index % len(arms)]]
        self.arms = {arm["id"]: arm for arm in self.cfg["arms"]}
        self.controller = SlotController(self.cfg, self.root / "controller.jsonl")

    def save(self) -> None:
        controller = getattr(self, "controller", None)
        with self.lock:
            _atomic(self.root / "job-status.json", {"name": self.cfg["name"], "updated": time.time(), "jobs": self.jobs,
                                                    **({"controller": controller.last} if controller else {})})

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
                # Every trial gets the full wall window, also past the deadline: an admitted seed block finishes
                # with identical budgets (the unit's runtime cap must allow until + the wall window).
                end = job["started"] + self.cfg["trial_wall_seconds"]
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
        controller = self.controller
        slots = list(range(controller.maximum))
        active: dict = {}
        running: dict = {}
        self.save()
        with ThreadPoolExecutor(controller.maximum) as pool:
            queue = list(self.jobs)
            admitted: set = set()
            while (queue or active) and not (self.stopping.is_set() and not active):
                for future in [f for f in active if f.done()]:
                    future.result()
                    slots.append(active.pop(future))
                    running.pop(future, None)
                    slots.sort()
                if controller.update(list(running.values()), len(queue)) is not None:
                    self.save()
                # The deadline admits seed blocks, not jobs: a new seed starts only if its trials can finish their
                # wall window before the deadline, and every arm of an admitted seed then runs (complete blocks).
                # Seeds never admitted are censored and listed.
                while queue and slots and len(active) < controller.slots and not self.stopping.is_set():
                    if queue[0]["seed"] not in admitted and time.time() + self.cfg["trial_wall_seconds"] > self.until:
                        for job in queue:
                            if job["seed"] not in admitted:
                                job["status"] = "not_started_deadline"
                        queue[:] = [job for job in queue if job["seed"] in admitted]
                        continue
                    job, slot = queue.pop(0), slots.pop(0)
                    admitted.add(job["seed"])
                    job.update(slots_at_start=controller.slots, active_at_start=len(active) + 1)
                    future = pool.submit(self.run_job, job, slot)
                    active[future], running[future] = slot, job
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
                 "| Arm | Run | Stops | Proxy C1/C2/C3/C4 | Median game-min to C2 | Predicates ever passed | Predicates passed at end (observed runs) | untyped_stuck |",
                 "|---|---:|---|---|---:|---|---|---:|"]
        for arm, jobs in rows.items():
            ran = [j for j in jobs if j.get("runner_stop") or j["status"] in ("completed", "wall_cap", "runner_error")]
            stops = dict(Counter(j.get("runner_stop") or j["status"] for j in ran))
            reached = [sum(p in (j.get("phases") or []) for j in ran) for p in ("C1", "C2", "C3", "C4")]
            c2 = [j["phase_times"]["C2"]["game_seconds"] / 60 for j in ran
                  if "C2" in (j.get("phase_times") or {}) and j["phase_times"]["C2"].get("game_seconds") is not None]
            accepted = dict(Counter(p for j in ran for p in (j.get("accepted") or [])))
            final_observed = [j for j in ran if j.get("accepted_final") is not None]
            final = dict(Counter(p for j in final_observed for p in j["accepted_final"]))
            median_c2 = f"{statistics.median(c2):.1f} (n={len(c2)})" if c2 else "-"
            lines.append(f"| {arm} | {len(ran)}/{len(jobs)} | {stops} | {'/'.join(map(str, reached))} | {median_c2} | "
                         f"{accepted} | {final} (n={len(final_observed)}/{len(ran)}) | "
                         f"{sum(j.get('untyped_stuck', 0) for j in ran)} |")
        lines += ["", "Proxy phases and deterministic checkpoint predicates are separate measurements. "
                  "Ever-passed predicates may later regress; final observations do not prove sustained service. "
                  "Legacy logs without accepted_final have unknown final acceptance."]
        journal = self.root / "controller.jsonl"
        if journal.exists():
            events = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
            if events:
                lines += ["", f"Slots (automatic): {events[0]['slots_before']} -> {events[-1]['slots']}, range "
                          f"{min(e['slots'] for e in events)}-{max(e['slots'] for e in events)}; decisions "
                          f"{dict(Counter(e['decision'] for e in events))} (controller.jsonl)."]
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
