# Path: tools/run_training_batch.py
# Purpose: Validate or execute repeatable mining-delivery batches on isolated workers.

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, Queue

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from training.candidates import mining_delivery_candidates
from training.canonical import canonical_sha256
from training.episode import EpisodeCapacityInterrupted, run_episode
from training.factorio_bridge import FactorioTrainingBridge
from training.features import MINING_DELIVERY_FEATURES_V2
from training.policies import (
    DiagonalLinUCB,
    policy_snapshot,
    transition_can_train_policy,
)
from training.scenarios.mining_delivery import generate_mining_delivery_curriculum
from training.scheduler import WorkerSpec, load_worker_specs
from training.store import TrainingStore
from training.telemetry import WorkerTelemetry, publish_best_effort


def _load_workers(path: Path) -> list[WorkerSpec]:
    return load_worker_specs(path)

def _jobs(scenarios: list[dict], attempts: int, workers: list[WorkerSpec]) -> dict[str, list[tuple]]:
    """Assign every attempt of a scenario to one worker for surface affinity."""
    assigned = {worker.worker_id: [] for worker in workers}
    scenario_workers = {
        scenario["scenario_id"]: workers[index % len(workers)].worker_id
        for index, scenario in enumerate(scenarios)
    }
    index = 0
    for attempt in range(attempts):
        for scenario in scenarios:
            worker_id = scenario_workers[scenario["scenario_id"]]
            episode_id = f"episode-{scenario['scenario_id']}-a{attempt:04d}-{uuid.uuid4().hex[:8]}"
            assigned[worker_id].append((episode_id, scenario, index))
            index += 1
    return assigned


def _run_worker(
    worker: WorkerSpec, jobs, password: str, policy_payload: dict,
    live_directory: Path, events: Queue, stop_event: threading.Event | None = None,
) -> None:
    bridge = FactorioTrainingBridge(
        worker.script_output, host=worker.host, port=worker.rcon_port, password=password,
    )
    policy = DiagonalLinUCB.from_dict(policy_payload)
    try:
        telemetry = WorkerTelemetry(live_directory, worker.worker_id)
    except OSError:
        telemetry = None
    try:
        bridge.handshake()
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            job = jobs.claim() if isinstance(jobs, EpisodeQueue) else (jobs.pop(0) if jobs else None)
            if job is None:
                break
            episode_id, scenario, selection_seed = job
            def publish(event: dict) -> None:
                publish_best_effort(telemetry, event)
                if event.get("phase") == "provisioned":
                    events.put(("running", episode_id, None, worker.worker_id))

            try:
                transition = run_episode(
                    bridge, scenario, mining_delivery_candidates(scenario), policy,
                    selection_seed, episode_id=episode_id, update_policy=False,
                    stop_event=stop_event, on_progress=publish,
                )
                events.put(("result", episode_id, transition, None))
            except EpisodeCapacityInterrupted as exc:
                publish_best_effort(telemetry, {
                    "phase": "capacity_interrupted", "episode_id": episode_id,
                    "scenario_id": scenario["scenario_id"], "policy_id": policy.policy_id,
                    "error": str(exc),
                })
                events.put(("interrupted", episode_id, None, str(exc)))
                return
            except Exception as exc:  # worker isolation preserves later jobs
                error = f"{type(exc).__name__}: {exc}"
                publish_best_effort(telemetry, {
                    "phase": "failed", "episode_id": episode_id,
                    "scenario_id": scenario["scenario_id"], "policy_id": policy.policy_id,
                    "error": error,
                })
                events.put(("result", episode_id, None, error))
            finally:
                if isinstance(jobs, EpisodeQueue):
                    jobs.release(str(scenario["scenario_id"]))
    except Exception as exc:
        publish_best_effort(telemetry, {
            "phase": "worker_failed", "error": f"{type(exc).__name__}: {exc}",
        })
        raise
    finally:
        bridge.close()


class EpisodeQueue:
    """Share queued jobs while serializing attempts for each stable scenario surface."""

    def __init__(self, jobs: list[tuple]) -> None:
        self._jobs = list(jobs)
        self._locked_scenarios: set[str] = set()
        self._condition = threading.Condition()

    def claim(self) -> tuple | None:
        with self._condition:
            while self._jobs:
                for index, job in enumerate(self._jobs):
                    scenario_id = str(job[1]["scenario_id"])
                    if scenario_id not in self._locked_scenarios:
                        self._locked_scenarios.add(scenario_id)
                        return self._jobs.pop(index)
                self._condition.wait()
            return None

    def release(self, scenario_id: str) -> None:
        with self._condition:
            self._locked_scenarios.discard(str(scenario_id))
            self._condition.notify_all()


def _prepare_store(store, scenarios, jobs, policy, generation: int) -> None:
    for scenario in scenarios:
        store.save_scenario(scenario, "train", scenario["scenario_hash"])
    store.save_policy(
        policy.policy_id, "diagonal_linucb", generation, {}, policy_snapshot(policy),
    )
    for episode_id, scenario, seed in jobs:
        store.start_episode(
            episode_id, scenario["scenario_id"], policy.policy_id, "unassigned", seed,
            status="queued",
        )


def _finish_store(store: TrainingStore, results: list[tuple]) -> tuple[int, int]:
    completed = failed = 0
    for episode_id, transition, error in results:
        if transition is None:
            store.finish_episode(episode_id, "failed", 0, 0, {"error": error})
            failed += 1
            continue
        store.save_transition(transition)
        store.finish_episode(
            episode_id, transition["result"]["status"], transition["started_tick"],
            transition["ended_tick"], {"reward": transition["reward"]["total"]},
        )
        completed += transition["result"]["status"] == "completed"
        failed += transition["result"]["status"] != "completed"
    return completed, failed


def _collect_results(
    future_jobs: dict, events: Queue, store: TrainingStore, *,
    expected_episode_ids: list[str] | None = None,
) -> tuple[list, int, int]:
    results, completed, failed = [], 0, 0
    terminal, checked = set(), set()
    expected = set(expected_episode_ids or (
        episode_id for episode_ids in future_jobs.values() for episode_id in episode_ids
    ))
    queue_mode = expected_episode_ids is not None

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

    while len(checked) < len(future_jobs) or not events.empty():
        try:
            kind, episode_id, transition, error = events.get(timeout=0.2)
        except Empty:
            for future, episode_ids in future_jobs.items():
                if future in checked or not future.done():
                    continue
                checked.add(future)
                worker_error = future.exception()
                reason = "worker exited without a terminal episode result"
                if worker_error is not None:
                    reason = f"worker failed: {type(worker_error).__name__}: {worker_error}"
                if not queue_mode:
                    for unfinished_id in episode_ids:
                        if unfinished_id not in terminal:
                            persist((unfinished_id, None, reason))
            continue
        if kind == "running":
            store.mark_episode_running(episode_id, worker_id=error)
        else:
            persist((episode_id, transition, error))
    for unfinished_id in sorted(expected.difference(terminal)):
        persist((unfinished_id, None, "all assigned workers exited without a terminal episode result"))
    return results, completed, failed

def _offline(scenarios: list[dict], attempts: int) -> dict:
    catalogs = [mining_delivery_candidates(scenario) for scenario in scenarios]
    return {
        "mode": "offline", "scenarios": len(scenarios), "attempts": len(scenarios) * attempts,
        "candidates_validated": sum(len(catalog) for catalog in catalogs) * attempts,
        "factorio_mutated": False,
    }


def _load_policy(path: Path) -> tuple[int, DiagonalLinUCB]:
    if path.is_file():
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        return int(checkpoint["generation"]), DiagonalLinUCB.from_dict(checkpoint["policy"])
    return 0, DiagonalLinUCB("policy-g0000-initial", MINING_DELIVERY_FEATURES_V2)


def _learn_policy(base: DiagonalLinUCB, results: list[tuple]) -> DiagonalLinUCB:
    """Learn only from complete trajectories, crediting every staged decision.

    A terminal success is evidence for the sequence that built it, not only its
    final expansion action. Failures remain excluded by ``transition_can_train_policy``.
    """
    learned = DiagonalLinUCB.from_dict(base.to_dict())
    for _episode_id, transition, _error in sorted(results, key=lambda item: item[0]):
        if transition is None or not transition_can_train_policy(transition):
            continue
        trail = transition.get("stage_action_trail") or ()
        if trail:
            credit = float(transition["reward"]["total"]) / len(trail)
            for action in trail:
                learned.update(
                    action["pre_action_observation"],
                    action["selected_candidate"],
                    credit,
                )
            continue
        chosen = next(
            candidate for candidate in transition["candidates"]
            if candidate["action_id"] == transition["chosen_action_id"]
        )
        learned.update(transition["observation"], chosen, transition["reward"]["total"])
    return learned


def _policy_learning_count(results: list[tuple]) -> int:
    """Count successful attempts eligible to shape the next checkpoint."""
    return sum(
        transition is not None and transition_can_train_policy(transition)
        for _episode_id, transition, _error in results
    )


def _checkpoint(path: Path, generation: int, policy: DiagonalLinUCB) -> None:
    identity = {key: value for key, value in policy.to_dict().items() if key != "policy_id"}
    policy.policy_id = f"policy-g{generation:04d}-{canonical_sha256(identity)[7:19]}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"generation": generation, "policy": policy.to_dict()}, indent=2) + "\n",
        encoding="utf-8",
    )


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run isolated Factorio RL training attempts.")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--start-seed", type=int, default=0)
    parser.add_argument("--attempts-per-scenario", type=int, default=1)
    parser.add_argument("--workers", type=Path, help="Explicit worker JSON; omit for offline validation")
    parser.add_argument("--database", type=Path, default=Path("data/training/experience.db"))
    parser.add_argument("--checkpoint", type=Path, default=Path("data/training/policy.json"))
    parser.add_argument("--live-directory", type=Path, default=Path("data/training/live"))
    parser.add_argument("--password-env", default="FACTORIO_TRAINING_RCON_PASSWORD")
    parser.add_argument(
        "--rcon-secret-file", type=Path,
        help="Local training-worker RCON secret file; takes precedence over --password-env.",
    )
    args = parser.parse_args(argv)
    if args.attempts_per_scenario < 1:
        parser.error("--attempts-per-scenario must be positive")
    return args


def _rcon_password(args: argparse.Namespace) -> str:
    if args.rcon_secret_file is not None:
        try:
            password = args.rcon_secret_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SystemExit(f"RCON secret file is unavailable: {args.rcon_secret_file}") from exc
        if not password:
            raise SystemExit(f"RCON secret file is empty: {args.rcon_secret_file}")
        return password
    password = os.environ.get(args.password_env)
    if password is None:
        raise SystemExit(f"RCON password environment variable is missing: {args.password_env}")
    return password


def _execute_live(args, scenarios: list[dict], password: str) -> dict:
    workers = _load_workers(args.workers)
    static_assignments = _jobs(scenarios, args.attempts_per_scenario, workers)
    jobs = [job for assigned in static_assignments.values() for job in assigned]
    shared_jobs = EpisodeQueue(jobs)
    generation, policy = _load_policy(args.checkpoint)
    with TrainingStore(args.database) as store:
        _prepare_store(store, scenarios, jobs, policy, generation)
        events = Queue()
        with ThreadPoolExecutor(max_workers=len(workers)) as pool:
            future_jobs = {
                pool.submit(
                    _run_worker, worker, shared_jobs, password,
                    policy.to_dict(), args.live_directory, events,
                ): []
                for worker in workers
            }
            results, completed, failed = _collect_results(
                future_jobs, events, store,
                expected_episode_ids=[job[0] for job in jobs],
            )
        learning_count = _policy_learning_count(results)
        if learning_count:
            learned = _learn_policy(policy, results)
            generation += 1
            _checkpoint(args.checkpoint, generation, learned)
            store.save_policy(
                learned.policy_id, "diagonal_linucb", generation, {}, policy_snapshot(learned),
                parent_policy_id=policy.policy_id,
            )
        else:
            learned = policy
    return {
        "attempts": len(results), "completed": completed, "failed": failed,
        "policy_learning_episodes": learning_count,
        "policy_rejected_episodes": len(results) - learning_count,
        "policy_advanced": bool(learning_count),
        "generation": generation, "policy_id": learned.policy_id,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    scenarios = generate_mining_delivery_curriculum(args.count, args.start_seed)
    if args.workers is None:
        print(json.dumps(_offline(scenarios, args.attempts_per_scenario), indent=2))
        return 0
    password = _rcon_password(args)
    result = _execute_live(args, scenarios, password)
    print(json.dumps(result, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
