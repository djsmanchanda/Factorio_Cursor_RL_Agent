# Path: tools/run_adaptive_training_batch.py
# Purpose: Run staged RL batches that scale shared Factorio slots from measured UPS.

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, Queue
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.rcon_client import RconClient
from tools.run_training_batch import (
    _finish_store,
    _checkpoint,
    _learn_policy,
    _load_policy,
    _rcon_password,
    _run_worker,
    EpisodeQueue,
)
from training.policies import policy_snapshot
from training.scheduler import (
    AdaptiveScaleState,
    UpsWindow,
    active_workers_by_instance,
    adjust_adaptive_slots,
    group_workers_by_instance,
    load_worker_specs,
)
from training.scenarios.mining_delivery import generate_mining_delivery_curriculum, generate_staged_mining_delivery_scenario
from training.store import TrainingStore


class UpsSampler:
    """Sample Factorio tick advancement without changing any training surface."""

    def __init__(self, host: str, port: int, password: str, interval_seconds: float):
        self.host = host
        self.port = port
        self.password = password
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._samples: list[tuple[float, float]] = []
        self._error: str | None = None
        self._thread = threading.Thread(target=self._run, name="ups-sampler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(2.0, self.interval_seconds * 2))

    @property
    def sample_count(self) -> int:
        with self._lock:
            return len(self._samples)

    def window(self, duration_seconds: float | None = None) -> UpsWindow | None:
        with self._lock:
            if duration_seconds is None:
                samples = tuple(value for _, value in self._samples)
            else:
                cutoff = time.monotonic() - duration_seconds
                samples = tuple(value for timestamp, value in self._samples if timestamp >= cutoff)
                # Do not open a gate until the full stability duration has elapsed.
                if not self._samples or self._samples[0][0] > cutoff:
                    return None
        return UpsWindow(samples) if len(samples) >= 3 else None

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    def _run(self) -> None:
        client = None
        try:
            client = RconClient(self.host, self.port, self.password, timeout=10.0)
            previous_tick = self._tick(client)
            previous_time = time.monotonic()
            while not self._stop.wait(self.interval_seconds):
                current_tick = self._tick(client)
                current_time = time.monotonic()
                elapsed = current_time - previous_time
                if elapsed > 0 and current_tick >= previous_tick:
                    sample = (current_tick - previous_tick) / elapsed
                    with self._lock:
                        self._samples.append((current_time, sample))
                previous_tick, previous_time = current_tick, current_time
        except Exception as exc:  # sampling must never terminate training
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"
        finally:
            if client is not None:
                client.close()

    @staticmethod
    def _tick(client: RconClient) -> int:
        response = client.command("/sc rcon.print(game.tick)").strip()
        return int(response)


def _jobs(scenarios: Sequence[dict], attempts: int) -> list[tuple]:
    result = []
    index = 0
    for attempt in range(attempts):
        for scenario in scenarios:
            episode_id = (
                f"episode-{scenario['scenario_id']}-a{attempt:04d}-"
                f"{uuid.uuid4().hex[:8]}"
            )
            result.append((episode_id, scenario, index))
            index += 1
    return result



def _refill_jobs(scenarios: Sequence[dict], count: int, start_seed: int) -> list[tuple]:
    """Pad the final policy cohort with fresh seeded attempts for every active slot."""
    if count < 0 or not scenarios:
        raise ValueError("refill count and scenarios must be valid")
    return [
        (
            f"episode-{scenario['scenario_id']}-refill-{uuid.uuid4().hex[:8]}",
            scenario,
            start_seed + index,
        )
        for index in range(count)
        for scenario in (scenarios[index % len(scenarios)],)
    ]


def _cohort_target(minimum: int, active_slots: int, episodes_per_slot: int) -> int:
    """Keep every active slot fed while retaining the requested 100-episode floor."""
    if minimum < 1 or active_slots < 1 or episodes_per_slot < 1:
        raise ValueError("cohort sizing values must be positive")
    return max(minimum, active_slots * episodes_per_slot)

def _assign(jobs: Sequence[tuple], workers) -> dict[str, list[tuple]]:
    """Keep repeated attempts of one scenario on one slot of a shared runtime.

    Scenario environments intentionally have stable names for readable Factorio
    surfaces. Affinity prevents two attempts from provisioning the same named
    surface concurrently while preserving parallelism across different scenarios.
    """
    assigned = {worker.worker_id: [] for worker in workers}
    scenario_workers: dict[str, str] = {}
    for job in jobs:
        scenario_id = str(job[1]["scenario_id"])
        worker_id = scenario_workers.get(scenario_id)
        if worker_id is None:
            worker_id = workers[len(scenario_workers) % len(workers)].worker_id
            scenario_workers[scenario_id] = worker_id
        assigned[worker_id].append(job)
    return assigned


def _write_controller_state(directory: Path, payload: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "adaptive-controller.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _existing_policy_parent(store: TrainingStore, policy_id: str) -> str | None:
    """Return a checkpoint's stored parent before its restart refresh replaces it."""
    for row in store.rows("policies"):
        if row["policy_id"] == policy_id:
            return row["parent_policy_id"]
    return None


def _retry_jobs(stage_jobs: Sequence[tuple], interrupted: set[str]) -> list[tuple]:
    """Requeue only capacity-aborted work with fresh immutable episode identity."""
    retried = []
    for episode_id, scenario, seed in stage_jobs:
        if episode_id in interrupted:
            retried.append((
                f"episode-{scenario['scenario_id']}-retry-{uuid.uuid4().hex[:8]}",
                scenario, seed,
            ))
    return retried


def _run_stage(
    workers, stage_jobs, password, policy, live_directory, store, state: AdaptiveScaleState,
    *, minimum_slots: int, maximum_slots: int, step: int, minimum_ups_p95: float,
    minimum_ups_p98: float, healthy_windows_to_grow: int, unhealthy_windows_to_shrink: int,
    sampler: UpsSampler | None = None, stability_window_seconds: float = 300.0,
):
    """Run a stage without interrupting live episodes for capacity changes."""
    events = Queue()
    shared_jobs = EpisodeQueue(list(stage_jobs))
    for episode_id, scenario, seed in stage_jobs:
        store.start_episode(
            episode_id, scenario["scenario_id"], policy.policy_id, "unassigned", seed,
            status="queued",
        )
    stop_event = threading.Event()
    owns_sampler = sampler is None
    if owns_sampler:
        sampler = UpsSampler(
            workers[0].host, workers[0].rcon_port, password, interval_seconds=5.0,
        )
    results, completed, failed = [], 0, 0
    expected, terminal, checked = {job[0] for job in stage_jobs}, set(), set()
    interrupted: set[str] = set()

    def persist(result: tuple) -> None:
        nonlocal completed, failed
        episode_id = result[0]
        if episode_id in terminal:
            raise RuntimeError(f"duplicate terminal result: {episode_id}")
        done, rejected = _finish_store(store, [result])
        results.append(result)
        terminal.add(episode_id)
        completed += done
        failed += rejected

    def abort(episode_id: str, reason: str) -> None:
        if episode_id in terminal:
            return
        store.abort_episode(episode_id, reason)
        interrupted.add(episode_id)
        terminal.add(episode_id)

    if owns_sampler:
        sampler.start()
    try:
        with ThreadPoolExecutor(max_workers=len(workers)) as pool:
            futures = {
                pool.submit(
                    _run_worker, worker, shared_jobs, password,
                    policy.to_dict(), live_directory, events, stop_event,
                ): worker.worker_id
                for worker in workers
            }
            while len(checked) < len(futures) or not events.empty():
                try:
                    kind, episode_id, transition, detail = events.get(timeout=0.2)
                except Empty:
                    for future in futures:
                        if future in checked or not future.done():
                            continue
                        checked.add(future)
                        worker_error = future.exception()
                        if worker_error is not None and not stop_event.is_set():
                            raise RuntimeError(
                                f"worker failed: {type(worker_error).__name__}: {worker_error}"
                            ) from worker_error
                    continue
                if kind == "running":
                    store.mark_episode_running(episode_id, worker_id=detail)
                elif kind == "interrupted":
                    abort(episode_id, str(detail or "capacity backoff"))
                else:
                    persist((episode_id, transition, detail))
            for episode_id in sorted(expected.difference(terminal)):
                persist((episode_id, None, "all assigned workers exited without a terminal episode result"))
    finally:
        if owns_sampler:
            sampler.stop()
    return (
        results, completed, failed, sampler.window(stability_window_seconds), sampler.error,
        interrupted, None, None,
    )


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an adaptive UPS-gated Factorio RL batch.")
    parser.add_argument("--workers", type=Path, required=True)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--start-seed", type=int, default=0)
    parser.add_argument("--attempts-per-scenario", type=int, default=1)
    parser.add_argument("--staged-demand", action="store_true", help="Run one persistent 10->30->60/s dual-sink demand ladder per scenario.")
    parser.add_argument("--staged-sustain-seconds", type=float, default=30.0, help="Required stable throughput at each staged demand level.")
    parser.add_argument(
        "--target-rates-per-second",
        help="Comma-separated fixed demand rates for stress phases, e.g. 3 or 10 or 30.",
    )
    parser.add_argument("--initial-slots", type=int, default=8)
    parser.add_argument("--minimum-slots", type=int, default=1)
    parser.add_argument("--maximum-slots", type=int, default=16)
    parser.add_argument("--step", type=int, default=1)
    parser.add_argument("--episodes-per-slot", type=int, default=1)
    parser.add_argument("--episodes-per-policy", type=int, default=100)
    parser.add_argument("--minimum-ups-p95", type=float, default=50.0)
    parser.add_argument("--minimum-ups-p98", type=float, default=45.0)
    parser.add_argument("--stability-window-seconds", type=float, default=300.0)
    parser.add_argument("--minimum-ups", type=float, dest="legacy_minimum_ups")
    parser.add_argument("--healthy-windows-to-grow", type=int, default=1)
    parser.add_argument("--unhealthy-windows-to-shrink", type=int, default=1)
    parser.add_argument(
        "--allow-episode-failures", action="store_true",
        help="Treat individual episode failures as training evidence so queued phases continue.",
    )
    parser.add_argument("--database", type=Path, default=Path("data/training/experience.db"))
    parser.add_argument("--checkpoint", type=Path, default=Path("data/training/policy.json"))
    parser.add_argument("--live-directory", type=Path, default=Path("data/training/live"))
    parser.add_argument("--password-env", default="FACTORIO_TRAINING_RCON_PASSWORD")
    parser.add_argument("--rcon-secret-file", type=Path)
    args = parser.parse_args(argv)
    if args.count < 1 or args.attempts_per_scenario < 1:
        parser.error("count and attempts-per-scenario must be positive")
    if args.minimum_slots < 1 or args.maximum_slots < args.minimum_slots:
        parser.error("slot bounds are invalid")
    if not args.minimum_slots <= args.initial_slots <= args.maximum_slots:
        parser.error("initial-slots must be inside the slot bounds")
    if args.step < 1 or args.episodes_per_slot < 1 or args.episodes_per_policy < 1:
        parser.error("step, episodes-per-slot, and episodes-per-policy must be positive")
    if args.staged_sustain_seconds < 1:
        parser.error("staged-sustain-seconds must be at least 1")
    if args.stability_window_seconds <= 0:
        parser.error("stability-window-seconds must be positive")
    if args.legacy_minimum_ups is not None:
        args.minimum_ups_p98 = args.legacy_minimum_ups
    if args.minimum_ups_p95 <= 0 or args.minimum_ups_p98 <= 0:
        parser.error("minimum UPS thresholds must be positive")
    return args


def _initial_instance_states(workers_by_instance, *, initial: int, minimum: int, maximum: int):
    """Give every Factorio server its own bounded initial slot budget."""
    states: dict[str, AdaptiveScaleState] = {}
    for instance_id, workers in workers_by_instance.items():
        if len(workers) < minimum:
            raise ValueError(
                f"instance {instance_id} has {len(workers)} slot(s), below minimum {minimum}"
            )
        limit = min(maximum, len(workers))
        states[instance_id] = AdaptiveScaleState(min(limit, max(minimum, initial)))
    return states


def _adjust_instance_capacity(
    states, workers_by_instance, windows, gate_started, *, now: float,
    stability_window_seconds: float, minimum_slots: int, maximum_slots: int, step: int,
    minimum_ups_p95: float, minimum_ups_p98: float,
    healthy_windows_to_grow: int, unhealthy_windows_to_shrink: int,
):
    """Apply independent UPS gates; one overloaded server cannot throttle another."""
    next_states = dict(states)
    next_gate_started = dict(gate_started)
    decisions: dict[str, str] = {}
    for instance_id, workers in workers_by_instance.items():
        if now - gate_started[instance_id] < stability_window_seconds:
            decisions[instance_id] = "hold_capacity_window"
            continue
        next_states[instance_id], decisions[instance_id] = adjust_adaptive_slots(
            states[instance_id], windows.get(instance_id), minimum=minimum_slots,
            maximum=min(maximum_slots, len(workers)), step=step,
            minimum_safe_ups_p95=minimum_ups_p95,
            minimum_safe_ups_p98=minimum_ups_p98,
            healthy_windows_to_grow=healthy_windows_to_grow,
            unhealthy_windows_to_shrink=unhealthy_windows_to_shrink,
        )
        if decisions[instance_id] != "no_ups_window":
            next_gate_started[instance_id] = now
    return next_states, decisions, next_gate_started


def _instance_capacity_evidence(workers_by_instance, states, windows, decisions) -> list[dict]:
    """Compact per-server evidence for the controller state and Observatory."""
    evidence = []
    for instance_id, workers in workers_by_instance.items():
        window = windows.get(instance_id)
        evidence.append({
            "instance_id": instance_id,
            "configured_slots": len(workers),
            "active_slots": states[instance_id].slots,
            "decision": decisions.get(instance_id),
            "mean_ups": round(window.mean_ups, 3) if window else None,
            "safe_ups_p95": round(window.safe_ups_p95, 3) if window else None,
            "safe_ups_p98": round(window.safe_ups_p98, 3) if window else None,
            "tick_time_p98_ms": round(window.tick_time_p98_ms, 3) if window else None,
        })
    return evidence

def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    workers = load_worker_specs(args.workers)
    workers_by_instance = group_workers_by_instance(workers)
    states = _initial_instance_states(
        workers_by_instance, initial=args.initial_slots,
        minimum=args.minimum_slots, maximum=args.maximum_slots,
    )
    target_rates = None
    if args.target_rates_per_second:
        try:
            target_rates = tuple(float(value.strip()) for value in args.target_rates_per_second.split(","))
        except ValueError as exc:
            raise SystemExit("--target-rates-per-second must be comma-separated numbers") from exc
        if not target_rates or any(rate <= 0 for rate in target_rates):
            raise SystemExit("--target-rates-per-second must contain positive rates")
    if args.staged_demand:
        if target_rates is not None and target_rates != (10.0, 30.0, 60.0):
            raise SystemExit("--staged-demand uses fixed rates 10,30,60")
        scenarios = [
            generate_staged_mining_delivery_scenario(
                seed, sustain_ticks=round(args.staged_sustain_seconds * 60),
            )
            for seed in range(args.start_seed, args.start_seed + args.count)
        ]
    else:
        scenarios = generate_mining_delivery_curriculum(
            args.count, args.start_seed, target_rates_per_second=target_rates,
        )
    jobs = _jobs(scenarios, args.attempts_per_scenario)
    password = _rcon_password(args)
    generation, policy = _load_policy(args.checkpoint)
    total_completed = total_failed = 0
    stage = cohort = 0
    gate_started = {instance_id: time.monotonic() for instance_id in workers_by_instance}
    ups_history: list[dict[str, object]] = []

    def state_payload(payload: dict) -> dict:
        return {
            **payload,
            "minimum_ups_p95": args.minimum_ups_p95,
            "minimum_ups_p98": args.minimum_ups_p98,
            "stability_window_seconds": args.stability_window_seconds,
            "ups_history": ups_history[-120:],
        }

    with TrainingStore(args.database) as store:
        for scenario in scenarios:
            store.save_scenario(scenario, "train", scenario["scenario_hash"])
        store.save_policy(
            policy.policy_id, "diagonal_linucb", generation, {}, policy_snapshot(policy),
            parent_policy_id=_existing_policy_parent(store, policy.policy_id),
        )
        samplers = {
            instance_id: UpsSampler(
                group[0].host, group[0].rcon_port, password, interval_seconds=5.0,
            )
            for instance_id, group in workers_by_instance.items()
        }
        for sampler in samplers.values():
            sampler.start()
        try:
            while jobs:
                cohort += 1
                cohort_target = _cohort_target(
                    args.episodes_per_policy, sum(state.slots for state in states.values()),
                    args.episodes_per_slot,
                )
                cohort_jobs = jobs[:cohort_target]
                del jobs[:len(cohort_jobs)]
                if len(cohort_jobs) < cohort_target:
                    cohort_jobs.extend(_refill_jobs(
                        scenarios, cohort_target - len(cohort_jobs),
                        args.start_seed + cohort * cohort_target,
                    ))
                cohort_results: list[tuple] = []
                cohort_completed = cohort_failed = 0
                while cohort_jobs:
                    stage += 1
                    active_workers = active_workers_by_instance(workers_by_instance, states)
                    stage_jobs = cohort_jobs[:len(active_workers) * args.episodes_per_slot]
                    del cohort_jobs[:len(stage_jobs)]
                    started = time.monotonic()
                    primary_sampler = next(iter(samplers.values()))
                    (
                        results, completed, failed, _window, _sample_error, interrupted,
                        _backoff_state, _backoff_reason,
                    ) = _run_stage(
                        active_workers, stage_jobs, password, policy, args.live_directory, store,
                        states[next(iter(states))], minimum_slots=args.minimum_slots,
                        maximum_slots=args.maximum_slots, step=args.step,
                        minimum_ups_p95=args.minimum_ups_p95,
                        minimum_ups_p98=args.minimum_ups_p98,
                        healthy_windows_to_grow=args.healthy_windows_to_grow,
                        unhealthy_windows_to_shrink=args.unhealthy_windows_to_shrink,
                        sampler=primary_sampler,
                        stability_window_seconds=args.stability_window_seconds,
                    )
                    elapsed = time.monotonic() - started
                    total_completed += completed
                    total_failed += failed
                    cohort_completed += completed
                    cohort_failed += failed
                    cohort_results.extend(results)
                    windows = {
                        instance_id: sampler.window(args.stability_window_seconds)
                        for instance_id, sampler in samplers.items()
                    }
                    now = time.monotonic()
                    states, decisions, gate_started = _adjust_instance_capacity(
                        states, workers_by_instance, windows, gate_started, now=now,
                        stability_window_seconds=args.stability_window_seconds,
                        minimum_slots=args.minimum_slots, maximum_slots=args.maximum_slots,
                        step=args.step, minimum_ups_p95=args.minimum_ups_p95,
                        minimum_ups_p98=args.minimum_ups_p98,
                        healthy_windows_to_grow=args.healthy_windows_to_grow,
                        unhealthy_windows_to_shrink=args.unhealthy_windows_to_shrink,
                    )
                    capacity_evidence = _instance_capacity_evidence(
                        workers_by_instance, states, windows, decisions,
                    )
                    valid_windows = [window for window in windows.values() if window is not None]
                    aggregate = {
                        "mean_ups": round(sum(window.mean_ups for window in valid_windows) / len(valid_windows), 3)
                        if valid_windows else None,
                        "safe_ups_p95": round(min(window.safe_ups_p95 for window in valid_windows), 3)
                        if valid_windows else None,
                        "safe_ups_p98": round(min(window.safe_ups_p98 for window in valid_windows), 3)
                        if valid_windows else None,
                    }
                    if valid_windows:
                        ups_history.append({
                            "stage": stage,
                            "slots": sum(state.slots for state in states.values()),
                            **aggregate,
                            "servers": capacity_evidence,
                            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        })
                    base_payload = {
                        "stage": stage, "policy_cohort": cohort,
                        "policy_episode_target": args.episodes_per_policy,
                        "policy_terminal_episodes": len(cohort_results),
                        "slots": sum(state.slots for state in states.values()),
                        "previous_slots": len(active_workers),
                        "stage_attempts": len(stage_jobs),
                        "cohort_target": cohort_target, "completed": completed,
                        "failed": failed, "elapsed_seconds": round(elapsed, 2),
                        "remaining_attempts": len(cohort_jobs) + len(jobs),
                        "generation": generation, "policy_id": policy.policy_id,
                        "servers": capacity_evidence,
                        **aggregate,
                    }
                    if interrupted:
                        retries = _retry_jobs(stage_jobs, interrupted)
                        cohort_jobs = retries + cohort_jobs
                        payload = {
                            **base_payload, "phase": "adaptive_capacity_backoff",
                            "interrupted": len(interrupted), "requeued": len(retries),
                            "decision": "capacity_backoff",
                        }
                    else:
                        payload = {
                            **base_payload, "phase": "adaptive_stage_finished",
                            "decision": decisions,
                            "capacity_mode": "per_server_independent_gates",
                        }
                    _write_controller_state(args.live_directory, state_payload(payload))
                    print(json.dumps(state_payload(payload), sort_keys=True), flush=True)
                if not cohort_results:
                    raise RuntimeError("policy cohort ended without terminal training evidence")
                learned = _learn_policy(policy, cohort_results)
                generation += 1
                _checkpoint(args.checkpoint, generation, learned)
                store.save_policy(
                    learned.policy_id, "diagonal_linucb", generation, {}, policy_snapshot(learned),
                    parent_policy_id=policy.policy_id,
                )
                policy = learned
                payload = {
                    "phase": "policy_cohort_finished", "policy_cohort": cohort,
                    "policy_episode_target": args.episodes_per_policy,
                    "policy_terminal_episodes": len(cohort_results),
                    "cohort_completed": cohort_completed, "cohort_failed": cohort_failed,
                    "slots": sum(state.slots for state in states.values()),
                    "remaining_attempts": len(jobs), "generation": generation,
                    "policy_id": policy.policy_id, "servers": _instance_capacity_evidence(
                        workers_by_instance, states,
                        {instance_id: sampler.window(args.stability_window_seconds)
                         for instance_id, sampler in samplers.items()}, {},
                    ),
                }
                _write_controller_state(args.live_directory, state_payload(payload))
                print(json.dumps(state_payload(payload), sort_keys=True), flush=True)
        finally:
            for sampler in samplers.values():
                sampler.stop()

    summary = {
        "attempts": total_completed + total_failed, "completed": total_completed,
        "failed": total_failed, "generation": generation, "policy_id": policy.policy_id,
        "final_slots": sum(state.slots for state in states.values()),
        "server_slots": {instance_id: state.slots for instance_id, state in states.items()},
        "stages": stage, "policy_cohorts": cohort,
    }
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if args.allow_episode_failures or total_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
