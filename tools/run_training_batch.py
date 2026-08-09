# Path: tools/run_training_batch.py
# Purpose: Validate or execute repeatable mining-delivery batches on isolated workers.

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from training.candidates import mining_delivery_candidates
from training.canonical import canonical_sha256
from training.episode import run_episode
from training.factorio_bridge import FactorioTrainingBridge
from training.features import MINING_DELIVERY_FEATURES_V1
from training.policies import DiagonalLinUCB, policy_snapshot
from training.scenarios.mining_delivery import generate_mining_delivery_curriculum
from training.scheduler import WorkerSpec, validate_worker_specs
from training.store import TrainingStore


def _load_workers(path: Path) -> list[WorkerSpec]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    workers = [WorkerSpec(
        worker_id=item["worker_id"], instance_id=item["instance_id"], host=item["host"],
        game_port=int(item["game_port"]), rcon_port=int(item["rcon_port"]),
        script_output=Path(item["script_output"]),
        surface_prefix=item.get("surface_prefix", "training/"),
        force_prefix=item.get("force_prefix", "training-"),
    ) for item in payload["workers"]]
    validate_worker_specs(workers)
    return workers


def _jobs(scenarios: list[dict], attempts: int, workers: list[WorkerSpec]) -> dict[str, list[tuple]]:
    assigned = {worker.worker_id: [] for worker in workers}
    index = 0
    for attempt in range(attempts):
        for scenario in scenarios:
            worker = workers[index % len(workers)]
            episode_id = f"episode-{scenario['scenario_id']}-a{attempt:04d}-{uuid.uuid4().hex[:8]}"
            assigned[worker.worker_id].append((episode_id, scenario, index))
            index += 1
    return assigned


def _run_worker(
    worker: WorkerSpec, jobs: list[tuple], password: str, policy_payload: dict,
) -> list[tuple]:
    bridge = FactorioTrainingBridge(
        worker.script_output, host=worker.host, port=worker.rcon_port, password=password,
    )
    results, policy = [], DiagonalLinUCB.from_dict(policy_payload)
    try:
        bridge.handshake()
        for episode_id, scenario, selection_seed in jobs:
            try:
                transition = run_episode(
                    bridge, scenario, mining_delivery_candidates(scenario), policy,
                    selection_seed, episode_id=episode_id, update_policy=False,
                )
                results.append((episode_id, transition, None))
            except Exception as exc:  # worker isolation preserves later jobs
                results.append((episode_id, None, f"{type(exc).__name__}: {exc}"))
    finally:
        bridge.close()
    return results


def _prepare_store(store, scenarios, assignments, policy, generation: int) -> None:
    for scenario in scenarios:
        store.save_scenario(scenario, "train", scenario["scenario_hash"])
    store.save_policy(
        policy.policy_id, "diagonal_linucb", generation, {}, policy_snapshot(policy),
    )
    for worker_id, jobs in assignments.items():
        for episode_id, scenario, seed in jobs:
            store.start_episode(episode_id, scenario["scenario_id"], policy.policy_id, worker_id, seed)


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
    return 0, DiagonalLinUCB("policy-g0000-initial", MINING_DELIVERY_FEATURES_V1)


def _learn_policy(base: DiagonalLinUCB, results: list[tuple]) -> DiagonalLinUCB:
    learned = DiagonalLinUCB.from_dict(base.to_dict())
    for _episode_id, transition, _error in sorted(results, key=lambda item: item[0]):
        if transition is None:
            continue
        chosen = next(
            candidate for candidate in transition["candidates"]
            if candidate["action_id"] == transition["chosen_action_id"]
        )
        learned.update(transition["observation"], chosen, transition["reward"]["total"])
    return learned

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
    parser.add_argument("--password-env", default="FACTORIO_TRAINING_RCON_PASSWORD")
    args = parser.parse_args(argv)
    if args.attempts_per_scenario < 1:
        parser.error("--attempts-per-scenario must be positive")
    return args


def _execute_live(args, scenarios: list[dict], password: str) -> dict:
    workers = _load_workers(args.workers)
    assignments = _jobs(scenarios, args.attempts_per_scenario, workers)
    generation, policy = _load_policy(args.checkpoint)
    with TrainingStore(args.database) as store:
        _prepare_store(store, scenarios, assignments, policy, generation)
        with ThreadPoolExecutor(max_workers=len(workers)) as pool:
            futures = [pool.submit(
                _run_worker, worker, assignments[worker.worker_id], password, policy.to_dict(),
            ) for worker in workers]
            batches = [future.result() for future in as_completed(futures)]
        results = [item for batch in batches for item in batch]
        completed, failed = _finish_store(store, results)
        learned = _learn_policy(policy, results)
        _checkpoint(args.checkpoint, generation + 1, learned)
        store.save_policy(
            learned.policy_id, "diagonal_linucb", generation + 1, {}, policy_snapshot(learned),
            parent_policy_id=policy.policy_id,
        )
    return {
        "attempts": len(results), "completed": completed, "failed": failed,
        "generation": generation + 1, "policy_id": learned.policy_id,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    scenarios = generate_mining_delivery_curriculum(args.count, args.start_seed)
    if args.workers is None:
        print(json.dumps(_offline(scenarios, args.attempts_per_scenario), indent=2))
        return 0
    password = os.environ.get(args.password_env)
    if password is None:
        raise SystemExit(f"RCON password environment variable is missing: {args.password_env}")
    result = _execute_live(args, scenarios, password)
    print(json.dumps(result, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())