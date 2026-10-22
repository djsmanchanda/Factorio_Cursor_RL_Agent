#!/usr/bin/env python3
# Path: tools/laya_host_scheduler.py | Purpose: One host-wide Laya slot budget with a measured sweet spot, power/thermal derating, and graceful drain.

"""Host-wide Laya slot scheduler.

The cohort supervisors each know their own queue; this scheduler owns how many
Factorio worlds the whole host may run. It publishes one atomic target that
every new cohort reads, so two cohorts cannot independently grow past the host.

The target starts at ``start_slots``. Each window it climbs toward
``max_slots`` while measured aggregate UPS keeps improving, and backs off on a
plateau, a low UPS tail, high package temperature, low memory, or coordinator
instability. A manual ``target`` is a drain ceiling: no new worlds start above
it, but running worlds are never killed. ``mode cool`` applies the same drain
to ``cool_max_slots`` and a lower temperature ceiling.

Run it under a user systemd unit on the host that owns the campaigns::

    python tools/laya_host_scheduler.py --root data/laya-host run --interval 60

Control it with::

    ... configure --start 24 --max 30 --balanced-max 26 --cool-max 20
    ... target 14          # gradual drain to 14
    ... mode cool          # or performance/balanced/auto
    ... status
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import statistics
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "data/laya-host"
DEFAULT_REGISTRY = ROOT / "data/laya-experiments"

OCCUPYING = {"preparing", "running"}
ACTIVE_QUEUE = {"queued", "preparing", "running"}
DECISION = re.compile(r"^\+(\d+)s DECISION \d+ .*?\btick=(\d+)", re.M)
TAIL_BYTES = 262144

DEFAULTS: dict[str, Any] = {
    "start_slots": 24,
    "max_slots": 30,
    "min_slots": 4,
    "step": 2,
    "cool_step": 1,
    "window_seconds": 180,
    "stale_seconds": 900,
    "min_ups_p95": 105.0,
    "min_ups_p98": 95.0,
    "min_total_ups_gain": 0.02,
    "temp_limit_c": 93.0,
    "temp_hard_c": 95.0,
    "cool_max_slots": 20,
    "balanced_max_slots": 26,
    "cool_temp_limit_c": 85.0,
    "temp_recover_margin_c": 2.0,
    "memory_min_gb": 4.5,
    "settle_windows": 4,
    "mode": "auto",
    "manual_target": None,
    "manual_target_until": None,
    "manual_previous_target": None,
    "publish_gate": True,
}


def _atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def _available_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1048576
    except OSError:
        pass
    return None


def _package_temp_c() -> float | None:
    try:
        result = subprocess.run(["sensors"], capture_output=True, text=True, timeout=3)
        match = re.search(r"Tctl:\s+\+([0-9.]+)", result.stdout)
        if match:
            return float(match.group(1))
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        zones = sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp"))
        if zones:
            return max(int(zone.read_text().strip()) / 1000.0 for zone in zones)
    except (OSError, ValueError):
        pass
    return None


def _power_profile() -> str:
    try:
        return subprocess.check_output(["powerprofilesctl", "get"], text=True, timeout=3).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _mode_from_profile(profile: str) -> str:
    return {
        "performance": "performance",
        "balanced": "balanced",
        "power-saver": "cool",
    }.get(profile, "balanced")


def _percentile(values: Iterable[float], quantile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _tail(path: Path, size: int = TAIL_BYTES) -> str:
    file_size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, file_size - size))
        return handle.read().decode(errors="replace")


def world_ups(world: Path, window_s: float) -> float | None:
    """Measured game updates per second from one runner's decision log."""
    log = world / "logs/laya-runner.log"
    heartbeat = world / "laya-runner.heartbeat.json"
    if not log.exists() or not heartbeat.exists():
        return None
    try:
        tail = _tail(log)
    except OSError:
        return None
    points = [(int(elapsed), int(tick)) for elapsed, tick in DECISION.findall(tail)]
    if not points:
        return None
    last_elapsed = points[-1][0]
    recent = [point for point in points if point[0] >= last_elapsed - window_s]
    span = recent[-1][0] - recent[0][0] if len(recent) > 1 else 0
    if span < min(60.0, window_s / 2) or recent[-1][1] <= recent[0][1]:
        return None
    return (recent[-1][1] - recent[0][1]) / span


def _world_unstable(world: Path, window_s: float) -> bool:
    log = world / "logs/laya-runner.log"
    heartbeat = world / "laya-runner.heartbeat.json"
    if not heartbeat.exists():
        return True
    try:
        if time.time() - heartbeat.stat().st_mtime > 60:
            return True
        beat = json.loads(heartbeat.read_text(encoding="utf-8"))
        last_elapsed = 0
        if log.exists():
            points = DECISION.findall(_tail(log))
            if points:
                last_elapsed = int(points[-1][0])
        gap = float(beat.get("elapsed_s", last_elapsed)) - last_elapsed
        if gap > 600:
            return True
    except (OSError, ValueError):
        return True
    return False


@dataclass
class Cohort:
    name: str
    path: Path
    updated: float
    running: int
    queued: int
    oldest: float
    worlds: list[Path] = field(default_factory=list)


def discover(registry: Path, *, now: float | None = None, stale_seconds: float = 900) -> list[Cohort]:
    now = time.time() if now is None else now
    cohorts: list[Cohort] = []
    for status_path in sorted(registry.glob("*/job-status.json")):
        if status_path.parent.name.startswith("_host-scheduler"):
            continue
        data = _read_json(status_path, None)
        if not isinstance(data, dict):
            continue
        jobs = data.get("jobs")
        if not isinstance(jobs, list) or not any(
            isinstance(job, dict) and (job.get("status") in ACTIVE_QUEUE or job.get("admission_reserved"))
            for job in jobs
        ):
            continue
        updated = float(data.get("updated") or status_path.stat().st_mtime)
        if now - updated > stale_seconds:
            continue
        running = [job for job in jobs if isinstance(job, dict) and
                   (job.get("status") in OCCUPYING or job.get("admission_reserved"))]
        queued = sum(1 for job in jobs if isinstance(job, dict) and job.get("status") == "queued")
        started = [float(job["started"]) for job in jobs if isinstance(job, dict) and job.get("started")]
        oldest = min(started) if started else updated
        worlds = [Path(job["world"]) for job in running if job.get("world")]
        cohorts.append(Cohort(
            name=status_path.parent.name,
            path=status_path.parent,
            updated=updated,
            running=len(running),
            queued=queued,
            oldest=oldest,
            worlds=worlds,
        ))
    return cohorts


def measure(cohorts: list[Cohort], *, window_s: float) -> dict[str, Any]:
    samples: list[float] = []
    unstable: list[str] = []
    for cohort in cohorts:
        for world in cohort.worlds:
            ups = world_ups(world, window_s)
            if ups is not None:
                samples.append(ups)
            if _world_unstable(world, window_s):
                unstable.append(f"{cohort.name}:{world.name}")
    return {
        "active_slots": sum(cohort.running for cohort in cohorts),
        "queued": sum(cohort.queued for cohort in cohorts),
        "ups_samples": len(samples),
        "ups_p95": _percentile(samples, 0.05),
        "ups_p98": _percentile(samples, 0.02),
        "ups_mean": statistics.fmean(samples) if samples else None,
        "total_ups": sum(samples) if samples else None,
        "unstable": unstable,
        "temp_c": _package_temp_c(),
        "available_gb": _available_gb(),
        "power_profile": _power_profile(),
    }


def effective_max(config: dict[str, Any], metrics: dict[str, Any], mode: str) -> tuple[int, str]:
    maximum = int(config["max_slots"])
    minimum = int(config["min_slots"])
    active = int(metrics["active_slots"])
    cap = maximum
    reason = mode
    if mode == "balanced":
        configured = config.get("balanced_max_slots")
        cap = max(minimum, int(configured) if configured is not None else round(maximum * 0.75))
    elif mode == "cool":
        cap = min(cap, int(config["cool_max_slots"]))
    temp = metrics.get("temp_c")
    limit = float(config["cool_temp_limit_c"] if mode == "cool" else config["temp_limit_c"])
    hard = float(config["temp_hard_c"])
    if temp is not None and temp >= hard:
        cap = min(cap, max(minimum, active - 2))
        reason = "thermal_hard"
    elif temp is not None and temp >= limit:
        cap = min(cap, max(minimum, active - 1))
        reason = "thermal"
    available = metrics.get("available_gb")
    if available is not None and available < float(config["memory_min_gb"]):
        cap = min(cap, max(minimum, active - 1))
        reason = "memory"
    if metrics.get("unstable"):
        cap = min(cap, max(minimum, active - 1))
        reason = "runner_unstable"
    return max(minimum, min(maximum, cap)), reason


def allocate(target: int, cohorts: list[Cohort]) -> dict[str, int]:
    """Keep every running world; grow the least-loaded cohort with queued work."""
    allocation = {cohort.name: cohort.running for cohort in cohorts}
    remaining = max(0, target - sum(allocation.values()))
    while remaining:
        eligible = [
            cohort for cohort in cohorts
            if cohort.queued > 0 and allocation[cohort.name] < cohort.running + cohort.queued
        ]
        if not eligible:
            break
        cohort = min(eligible, key=lambda item: (allocation[item.name], item.oldest, item.name))
        allocation[cohort.name] += 1
        remaining -= 1
    return allocation


def choose(
    config: dict[str, Any],
    state: dict[str, Any],
    metrics: dict[str, Any],
    *,
    now: float | None = None,
) -> tuple[int, str, dict[str, Any]]:
    """Return the next host target, its reason, and updated hill-climb state."""
    now = time.time() if now is None else now
    minimum = int(config["min_slots"])
    maximum = int(config["max_slots"])
    target = int(state.get("target", config["start_slots"]))
    target = max(minimum, min(maximum, target))
    requested_mode = str(config.get("mode") or "auto")
    mode = _mode_from_profile(metrics.get("power_profile") or "unknown") if requested_mode == "auto" else requested_mode
    cap, cap_reason = effective_max(config, metrics, mode)
    manual = config.get("manual_target")

    def done(value: int, reason: str) -> tuple[int, str, dict[str, Any]]:
        state["target"] = value
        return value, reason, state

    def clear_trial() -> None:
        for key in ("trial_base", "trial_total_ups", "trial_started", "trial_ready_since"):
            state[key] = None

    if state.get("mode") != mode:
        state["mode"] = mode
        clear_trial()
        state["sweet_spot"] = None
        state["settle_until"] = 0.0

    if manual is not None:
        target = max(0, min(cap, int(manual)))
        clear_trial()
        return done(target, "manual_target")

    # Mode, temperature, memory and liveness caps apply even while a trial is
    # still filling its new slots; waiting for measurements must not override them.
    if target > cap:
        clear_trial()
        return done(cap, cap_reason if cap_reason != mode else "mode_cap")

    if now < float(state.get("settle_until") or 0.0):
        target = min(target, cap)
        return done(target, "settling")

    trial_base = state.get("trial_base")
    trial_total = state.get("trial_total_ups")
    if trial_base is not None and trial_total is not None:
        active = int(metrics["active_slots"])
        samples = int(metrics.get("ups_samples") or 0)
        started = state.get("trial_started")
        if active != target or samples != active:
            state["trial_ready_since"] = None
            if now - float(started if started is not None else now) < 3 * float(config["window_seconds"]):
                return done(target, "trial_wait")
            clear_trial()
            return done(int(trial_base), "trial_incomplete")
        if state.get("trial_ready_since") is None:
            state["trial_ready_since"] = now
        if now - state["trial_ready_since"] < float(config["window_seconds"]):
            return done(target, "trial_window")
        current_total = metrics.get("total_ups")
        improved = current_total is not None and current_total > float(trial_total) * (1.0 + float(config["min_total_ups_gain"]))
        if not improved:
            target = int(trial_base)
            state["sweet_spot"] = target
            state["settle_until"] = now + float(config["settle_windows"]) * float(config["window_seconds"])
            clear_trial()
            return done(max(minimum, min(target, cap)), "sweet_spot")
        state["sweet_spot"] = target
        clear_trial()

    p95 = metrics.get("ups_p95")
    p98 = metrics.get("ups_p98")
    if p95 is None or p98 is None:
        return done(min(target, cap), "no_measurement")
    healthy = (
        p95 is not None
        and p98 is not None
        and p95 >= float(config["min_ups_p95"])
        and p98 >= float(config["min_ups_p98"])
        and not metrics.get("unstable")
        and (metrics.get("temp_c") is None or metrics["temp_c"] < float(
            config["cool_temp_limit_c"] if mode == "cool" else config["temp_limit_c"]
        ))
        and (metrics.get("available_gb") is None or metrics["available_gb"] >= float(config["memory_min_gb"]))
    )
    if not healthy:
        state["unhealthy_windows"] = int(state.get("unhealthy_windows", 0)) + 1
        state["healthy_windows"] = 0
        if cap_reason in {"thermal_hard", "thermal", "memory", "runner_unstable"}:
            # The cap is already relative to the running count, so draining one
            # world per finish is enough; subtracting again every window would
            # collapse to the minimum while the same hot worlds are still active.
            return done(cap, cap_reason)
        target = max(minimum, int(state.get("target", target)) - int(config["step"]))
        return done(min(target, cap), "unhealthy")

    state["healthy_windows"] = int(state.get("healthy_windows", 0)) + 1
    state["unhealthy_windows"] = 0
    grow_after = 2 if mode == "cool" else 1
    if target > cap:
        return done(cap, cap_reason if cap_reason != mode else "mode_cap")
    limit = float(config["cool_temp_limit_c"] if mode == "cool" else config["temp_limit_c"])
    headroom = (
        metrics.get("temp_c") is None
        or metrics["temp_c"] < limit - float(config["temp_recover_margin_c"])
    )
    if (
        headroom
        and state["healthy_windows"] >= grow_after
        and target < cap
        and int(metrics["active_slots"]) >= target
        and int(metrics.get("ups_samples") or 0) == int(metrics["active_slots"])
        and int(metrics.get("queued") or 0) > 0
    ):
        state["trial_base"] = target
        state["trial_total_ups"] = metrics.get("total_ups")
        state["trial_started"] = now
        state["trial_ready_since"] = None
        step = int(config["cool_step"] if mode == "cool" else config["step"])
        return done(min(cap, target + step), "trial_increase")
    return done(target, "thermal_hold" if not headroom and metrics.get("temp_c") is not None else "hold")


def write_gate(registry: Path, target: int, now: float) -> None:
    """Publish the host target through the status file existing cohort budgets already read.

    Older frozen supervisors interpret a completed job carrying
    ``host_occupied_at_start`` as a host-wide cap. The gate occupies no world;
    it only makes every supervisor see the same ceiling.
    """
    gate = registry / "_host-scheduler-gate"
    _atomic(gate / "job-status.json", {
        "name": "_host-scheduler-gate",
        "updated": now,
        "controller": {"slots": target},
        "jobs": [{
            "id": "host-scheduler-gate",
            "status": "completed",
            "started": 0,
            "host_occupied_at_start": True,
        }],
    })


def _expire_manual(config: dict[str, Any], root: Path, now: float, write: bool) -> tuple[dict[str, Any], int | None]:
    until = config.get("manual_target_until")
    if until is None or now < float(until):
        return config, None
    previous = config.get("manual_previous_target")
    config["manual_target"] = None
    config["manual_target_until"] = None
    config["manual_previous_target"] = None
    if write:
        _atomic(root / "config.json", config)
    return config, int(previous) if previous is not None else None


def cycle(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    registry: Path,
    root: Path,
    now: float | None = None,
    write: bool = True,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    config, restored = _expire_manual(config, root, now, write)
    if restored is not None:
        state["target"] = restored
        state["trial_base"] = None
        state["trial_total_ups"] = None
        state["trial_started"] = None
        state["settle_until"] = 0.0
    cohorts = discover(registry, now=now, stale_seconds=float(config["stale_seconds"]))
    metrics = measure(cohorts, window_s=float(config["window_seconds"]))
    target, reason, state = choose(config, state, metrics, now=now)
    state["target"] = target
    allocation = allocate(target, cohorts)
    report = {
        "target_slots": target,
        "effective_max": int(effective_max(config, metrics, state.get("mode") or "performance")[0]),
        "mode": state.get("mode"),
        "reason": reason,
        "active_slots": metrics["active_slots"],
        "queued": metrics["queued"],
        "allocations": allocation,
        "cohorts": [
            {"name": cohort.name, "running": cohort.running, "queued": cohort.queued, "oldest": cohort.oldest}
            for cohort in cohorts
        ],
        "metrics": {key: value for key, value in metrics.items() if key != "unstable"},
        "unstable": metrics["unstable"],
        "sweet_spot": state.get("sweet_spot"),
        "manual_target": config.get("manual_target"),
        "manual_target_until": config.get("manual_target_until"),
        "updated": now,
    }
    if write:
        _atomic(root / "state.json", state)
        _atomic(root / "target.json", report)
        with (root / "history.jsonl").open("a", encoding="utf-8") as history:
            history.write(json.dumps(report, sort_keys=True) + "\n")
        if config.get("publish_gate", True):
            write_gate(registry, target, now)
    return report


def _expiry(duration: str | None, until: str | None, now: float) -> float | None:
    if duration and until:
        raise SystemExit("use --for or --until, not both")
    if duration:
        match = re.fullmatch(r"(\d+)([smhd])", duration.strip().lower())
        if not match:
            raise SystemExit("duration must look like 30m, 2h, or 1d")
        scale = {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
        return now + int(match.group(1)) * scale
    if until:
        parsed = dt.datetime.fromisoformat(until)
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.timestamp()
    return None


def _load_config(root: Path, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    config = dict(DEFAULTS)
    config.update(_read_json(root / "config.json", {}))
    if overrides:
        config.update({key: value for key, value in overrides.items() if value is not None})
    return config


def _load_state(root: Path) -> dict[str, Any]:
    state = _read_json(root / "state.json", {})
    return state if isinstance(state, dict) else {}


def _set_config(root: Path, **changes: Any) -> dict[str, Any]:
    config = _load_config(root)
    config.update({key: value for key, value in changes.items()})
    _atomic(root / "config.json", config)
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the control loop")
    run.add_argument("--interval", type=float, default=60.0)
    run.add_argument("--once", action="store_true")

    cycle_parser = sub.add_parser("cycle", help="run one control cycle")
    cycle_parser.add_argument("--dry-run", action="store_true")

    sub.add_parser("status", help="print the last published target")
    sub.add_parser("reset", help="restart the hill climb at start_slots")

    configure = sub.add_parser("configure", help="change start/max/cool limits")
    configure.add_argument("--start", type=int)
    configure.add_argument("--max", dest="max_slots", type=int)
    configure.add_argument("--min", dest="min_slots", type=int)
    configure.add_argument("--step", type=int)
    configure.add_argument("--cool-max", dest="cool_max_slots", type=int)
    configure.add_argument("--balanced-max", dest="balanced_max_slots", type=int)
    configure.add_argument("--cool-temp", dest="cool_temp_limit_c", type=float)
    configure.add_argument("--temp-limit", dest="temp_limit_c", type=float)
    configure.add_argument("--temp-hard", dest="temp_hard_c", type=float)

    target = sub.add_parser("target", help="set a manual drain ceiling")
    target.add_argument("slots", type=int)
    target.add_argument("--for", dest="duration", help="expire the ceiling after 30m, 2h, or 1d")
    target.add_argument("--until", help="expire at an ISO timestamp")
    pause = sub.add_parser("pause", help="stop new admissions (target 0)")
    pause.add_argument("--for", dest="duration", help="resume after 30m, 2h, or 1d")
    pause.add_argument("--until", help="resume at an ISO timestamp")
    bump = sub.add_parser("bump", help="restart the automatic search from a new target (20 or +2)")
    bump.add_argument("slots", help="absolute slots, or +N/-N relative to the current target")
    sub.add_parser("resume", help="clear a manual target and resume automatic growth")
    sub.add_parser("clear-target", help="alias for resume")

    mode = sub.add_parser("mode", help="override the power mode")
    mode.add_argument("mode", choices=["auto", "performance", "balanced", "cool"])

    args = parser.parse_args(argv)
    root = args.root.resolve()
    registry = args.registry.resolve()
    root.mkdir(parents=True, exist_ok=True)

    if args.command == "configure":
        changes = {
            "start_slots": args.start, "max_slots": args.max_slots, "min_slots": args.min_slots,
            "step": args.step, "cool_max_slots": args.cool_max_slots, "balanced_max_slots": args.balanced_max_slots,
            "cool_temp_limit_c": args.cool_temp_limit_c, "temp_limit_c": args.temp_limit_c,
            "temp_hard_c": args.temp_hard_c,
        }
        config = _set_config(root, **{key: value for key, value in changes.items() if value is not None})
        print(json.dumps(config, indent=2, sort_keys=True))
        return 0
    if args.command in {"target", "pause"}:
        state = _load_state(root)
        previous = int(state.get("target", _load_config(root)["start_slots"]))
        slots = 0 if args.command == "pause" else args.slots
        until = _expiry(args.duration, args.until, time.time())
        config = _set_config(root, manual_target=slots, manual_target_until=until,
                             manual_previous_target=previous)
        print(json.dumps({"manual_target": config["manual_target"],
                          "manual_target_until": config["manual_target_until"]}, sort_keys=True))
        return 0
    if args.command == "bump":
        # A one-off nudge, not a ceiling: the search continues from here and
        # still steps down on UPS, temperature, memory or liveness.
        config = _load_config(root)
        state = _load_state(root)
        current = int(state.get("target", config["start_slots"]))
        relative = args.slots[:1] in {"+", "-"}
        slots = current + int(args.slots) if relative else int(args.slots)
        slots = max(int(config["min_slots"]), min(int(config["max_slots"]), slots))
        state.update({"target": slots, "trial_base": None, "trial_total_ups": None, "trial_started": None,
                      "trial_ready_since": None, "settle_until": 0.0, "sweet_spot": None})
        _atomic(root / "state.json", state)
        _set_config(root, manual_target=None, manual_target_until=None, manual_previous_target=None)
        print(json.dumps({"target": slots, "previous": current}, sort_keys=True))
        return 0
    if args.command in {"resume", "clear-target"}:
        config = _load_config(root)
        previous = config.get("manual_previous_target")
        if previous is not None:
            state = _load_state(root)
            state.update({"target": int(previous), "trial_base": None, "trial_total_ups": None,
                          "trial_started": None, "settle_until": 0.0})
            _atomic(root / "state.json", state)
        config = _set_config(root, manual_target=None, manual_target_until=None,
                             manual_previous_target=None)
        print(json.dumps({"manual_target": config["manual_target"]}, sort_keys=True))
        return 0
    if args.command == "mode":
        config = _set_config(root, mode=args.mode)
        print(json.dumps({"mode": config["mode"]}))
        return 0
    if args.command == "status":
        print(json.dumps(_read_json(root / "target.json", {}), indent=2, sort_keys=True))
        return 0
    if args.command == "reset":
        config = _set_config(root, manual_target=None, manual_target_until=None,
                             manual_previous_target=None)
        state = {"target": int(config["start_slots"]), "mode": config.get("mode") or "auto"}
        _atomic(root / "state.json", state)
        print(json.dumps(state, sort_keys=True))
        return 0
    if args.command == "cycle":
        report = cycle(_load_config(root), _load_state(root), registry=registry, root=root, write=not args.dry_run)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    while True:
        try:
            config = _load_config(root)
            state = _load_state(root)
            report = cycle(config, state, registry=registry, root=root)
            print(json.dumps({"target": report["target_slots"], "reason": report["reason"],
                              "active": report["active_slots"], "queued": report["queued"],
                              "mode": report["mode"], "temp_c": report["metrics"].get("temp_c")}, sort_keys=True),
                  flush=True)
        except Exception as error:  # A transient cohort/log read must not kill the host budget.
            print(json.dumps({"error": f"{type(error).__name__}: {error}"}), flush=True)
        if args.once:
            return 0
        time.sleep(max(5.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
