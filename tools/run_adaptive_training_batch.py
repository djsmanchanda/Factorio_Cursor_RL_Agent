# Path: tools/run_adaptive_training_batch.py
# Purpose: Run staged RL batches that scale shared Factorio slots from measured UPS.

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
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
    adjust_adaptive_slots,
    load_worker_specs,
)
from training.scenarios.mining_delivery import generate_mining_delivery_curriculum
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
        self._samples: list[float] = []
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

    def window(self) -> UpsWindow | None:
        with self._lock:
            samples = tuple(self._samples)
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
                        self._samples.append(sample)
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
    *, minimum_slots: int, maximum_slots: int, step: int, minimum_ups: float,
    healthy_windows_to_grow: int, unhealthy_windows_to_shrink: int,
):
    """Run one stage and interrupt/requeue it if its live UPS becomes unsafe."""
    events = Queue()
    shared_jobs = EpisodeQueue(list(stage_jobs))
    for episode_id, scenario, seed in stage_jobs:
        store.start_episode(
            episode_id, scenario["scenario_id"], policy.policy_id, "unassigned", seed,
            status="queued",
        )
    stop_event = threading.Event()
    sampler = UpsSampler(
        workers[0].host, workers[0].rcon_port, password, interval_seconds=5.0,
    )
    results, completed, failed = [], 0, 0
    expected, terminal, checked = {job[0] for job in stage_jobs}, set(), set()
    interrupted: set[str] = set()
    backoff_state: AdaptiveScaleState | None = None
    backoff_reason: str | None = None
    monitor_state, inspected_samples = state, 0

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
                if not stop_event.is_set() and sampler.sample_count > inspected_samples:
                    inspected_samples = sampler.sample_count
                    window = sampler.window()
                    if window is not None:
                        candidate_state, decision = adjust_adaptive_slots(
                            monitor_state, window, minimum=minimum_slots,
                            maximum=maximum_slots, step=step, minimum_safe_ups=minimum_ups,
                            healthy_windows_to_grow=healthy_windows_to_grow,
                            unhealthy_windows_to_shrink=unhealthy_windows_to_shrink,
                        )
                        monitor_state = candidate_state
                        if decision == "decrease_safe_ups":
                            backoff_state, backoff_reason = candidate_state, decision
                            stop_event.set()
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
            if stop_event.is_set():
                for episode_id in sorted(expected.difference(terminal)):
                    abort(episode_id, "capacity backoff before episode completion")
            else:
                for episode_id in sorted(expected.difference(terminal)):
                    persist((episode_id, None, "all assigned workers exited without a terminal episode result"))
    finally:
        sampler.stop()
    return (
        results, completed, failed, sampler.window(), sampler.error,
        interrupted, backoff_state, backoff_reason,
    )


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an adaptive UPS-gated Factorio RL batch.")
    parser.add_argument("--workers", type=Path, required=True)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--start-seed", type=int, default=0)
    parser.add_argument("--attempts-per-scenario", type=int, default=1)
    parser.add_argument("--initial-slots", type=int, default=4)
    parser.add_argument("--minimum-slots", type=int, default=4)
    parser.add_argument("--maximum-slots", type=int, default=80)
    parser.add_argument("--step", type=int, default=4)
    parser.add_argument("--episodes-per-slot", type=int, default=1)
    parser.add_argument("--minimum-ups", type=float, default=55.0)
    parser.add_argument("--healthy-windows-to-grow", type=int, default=2)
    parser.add_argument("--unhealthy-windows-to-shrink", type=int, default=1)
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
    if args.step < 1 or args.episodes_per_slot < 1:
        parser.error("step and episodes-per-slot must be positive")
    if args.minimum_ups <= 0:
        parser.error("minimum-ups must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    workers = load_worker_specs(args.workers)
    if len(workers) < args.maximum_slots:
        raise SystemExit(
            f"adaptive batch needs at least {args.maximum_slots} configured slots; "
            f"only {len(workers)} are available"
        )
    scenarios = generate_mining_delivery_curriculum(args.count, args.start_seed)
    jobs = _jobs(scenarios, args.attempts_per_scenario)
    password = _rcon_password(args)
    generation, policy = _load_policy(args.checkpoint)
    state = AdaptiveScaleState(args.initial_slots)
    total_completed = total_failed = 0
    stage = 0

    with TrainingStore(args.database) as store:
        for scenario in scenarios:
            store.save_scenario(scenario, "train", scenario["scenario_hash"])
        store.save_policy(policy.policy_id, "diagonal_linucb", generation, {}, policy_snapshot(policy))
        while jobs:
            stage += 1
            active_workers = workers[:state.slots]
            stage_jobs = jobs[: state.slots * args.episodes_per_slot]
            del jobs[: len(stage_jobs)]
            started = time.monotonic()
            (
                results, completed, failed, window, sample_error, interrupted,
                backoff_state, backoff_reason,
            ) = _run_stage(
                active_workers, stage_jobs, password, policy, args.live_directory, store, state,
                minimum_slots=args.minimum_slots, maximum_slots=args.maximum_slots,
                step=args.step, minimum_ups=args.minimum_ups,
                healthy_windows_to_grow=args.healthy_windows_to_grow,
                unhealthy_windows_to_shrink=args.unhealthy_windows_to_shrink,
            )
            elapsed = time.monotonic() - started
            total_completed += completed
            total_failed += failed
            if results:
                learned = _learn_policy(policy, results)
                generation += 1
                _checkpoint(args.checkpoint, generation, learned)
                store.save_policy(
                    learned.policy_id, "diagonal_linucb", generation, {}, policy_snapshot(learned),
                    parent_policy_id=policy.policy_id,
                )
                policy = learned
            if interrupted:
                retries = _retry_jobs(stage_jobs, interrupted)
                jobs = retries + jobs
                state = backoff_state or state
                payload = {
                    "phase": "adaptive_capacity_backoff", "stage": stage,
                    "slots": state.slots, "previous_slots": len(active_workers),
                    "stage_attempts": len(stage_jobs), "completed": completed, "failed": failed,
                    "interrupted": len(interrupted), "requeued": len(retries),
                    "elapsed_seconds": round(elapsed, 2), "decision": backoff_reason,
                    "safe_ups_p98": round(window.safe_ups_p98, 3) if window else None,
                    "tick_time_p98_ms": round(window.tick_time_p98_ms, 3) if window else None,
                    "mean_ups": round(window.mean_ups, 3) if window else None,
                    "sample_error": sample_error, "remaining_attempts": len(jobs),
                    "generation": generation, "policy_id": policy.policy_id,
                }
                _write_controller_state(args.live_directory, payload)
                print(json.dumps(payload, sort_keys=True), flush=True)
                continue
            next_state, decision = adjust_adaptive_slots(
                state, window, minimum=args.minimum_slots, maximum=args.maximum_slots,
                step=args.step, minimum_safe_ups=args.minimum_ups,
                healthy_windows_to_grow=args.healthy_windows_to_grow,
                unhealthy_windows_to_shrink=args.unhealthy_windows_to_shrink,
            )
            state = next_state
            payload = {
                "phase": "adaptive_stage_finished", "stage": stage,
                "slots": state.slots, "previous_slots": len(active_workers),
                "stage_attempts": len(stage_jobs), "completed": completed, "failed": failed,
                "elapsed_seconds": round(elapsed, 2), "decision": decision,
                "safe_ups_p98": round(window.safe_ups_p98, 3) if window else None,
                "tick_time_p98_ms": round(window.tick_time_p98_ms, 3) if window else None,
                "mean_ups": round(window.mean_ups, 3) if window else None,
                "sample_error": sample_error, "remaining_attempts": len(jobs),
                "generation": generation, "policy_id": policy.policy_id,
            }
            _write_controller_state(args.live_directory, payload)
            print(json.dumps(payload, sort_keys=True), flush=True)

    summary = {
        "attempts": total_completed + total_failed, "completed": total_completed,
        "failed": total_failed, "generation": generation, "policy_id": policy.policy_id,
        "final_slots": state.slots, "stages": stage,
    }
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
