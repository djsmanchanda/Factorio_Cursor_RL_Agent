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
from queue import Queue
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.rcon_client import RconClient
from tools.run_training_batch import (
    _collect_results,
    _checkpoint,
    _learn_policy,
    _load_policy,
    _rcon_password,
    _run_worker,
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
    assigned = {worker.worker_id: [] for worker in workers}
    for index, job in enumerate(jobs):
        assigned[workers[index % len(workers)].worker_id].append(job)
    return assigned


def _write_controller_state(directory: Path, payload: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "adaptive-controller.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _run_stage(workers, assignments, password, policy, live_directory, store):
    events = Queue()
    for worker_id, jobs in assignments.items():
        for episode_id, scenario, seed in jobs:
            store.start_episode(
                episode_id, scenario["scenario_id"], policy.policy_id, worker_id, seed,
                status="queued",
            )
    sampler = UpsSampler(
        workers[0].host, workers[0].rcon_port, password, interval_seconds=5.0,
    )
    sampler.start()
    try:
        with ThreadPoolExecutor(max_workers=len(workers)) as pool:
            future_jobs = {
                pool.submit(
                    _run_worker, worker, assignments[worker.worker_id], password,
                    policy.to_dict(), live_directory, events,
                ): [job[0] for job in assignments[worker.worker_id]]
                for worker in workers
            }
            results, completed, failed = _collect_results(future_jobs, events, store)
    finally:
        sampler.stop()
    return results, completed, failed, sampler.window(), sampler.error


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an adaptive UPS-gated Factorio RL batch.")
    parser.add_argument("--workers", type=Path, required=True)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--start-seed", type=int, default=0)
    parser.add_argument("--attempts-per-scenario", type=int, default=1)
    parser.add_argument("--initial-slots", type=int, default=4)
    parser.add_argument("--minimum-slots", type=int, default=4)
    parser.add_argument("--maximum-slots", type=int, default=32)
    parser.add_argument("--step", type=int, default=4)
    parser.add_argument("--episodes-per-slot", type=int, default=4)
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
            assignments = _assign(stage_jobs, active_workers)
            started = time.monotonic()
            results, completed, failed, window, sample_error = _run_stage(
                active_workers, assignments, password, policy, args.live_directory, store,
            )
            elapsed = time.monotonic() - started
            total_completed += completed
            total_failed += failed
            learned = _learn_policy(policy, results)
            generation += 1
            _checkpoint(args.checkpoint, generation, learned)
            store.save_policy(
                learned.policy_id, "diagonal_linucb", generation, {}, policy_snapshot(learned),
                parent_policy_id=policy.policy_id,
            )
            policy = learned
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
