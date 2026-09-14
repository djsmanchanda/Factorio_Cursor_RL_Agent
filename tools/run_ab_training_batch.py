# Path: tools/run_ab_training_batch.py
# Purpose: Run true alternating ore and furnace cohorts on shared disposable workers.

from __future__ import annotations

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue, Empty
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from training.alternating import AlternatingFamily, AlternatingPolicyScheduler
from training.candidates import furnace_refining_candidates, mining_delivery_candidates
from training.episode import run_episode
from training.factorio_bridge import FactorioTrainingBridge
from training.features import FURNACE_REFINING_FEATURES_V1, MINING_DELIVERY_FEATURES_V3
from training.policies import (
    DiagonalLinUCB, policy_snapshot,
    transition_can_train_policy, transition_can_update_policy,
)
from training.scenarios.furnace_refining import generate_furnace_refining_curriculum
from training.scenarios.mining_delivery import generate_mining_delivery_curriculum
from training.scheduler import load_worker_specs
from training.store import TrainingStore
from training.telemetry import WorkerTelemetry, publish_best_effort


def _catalog(scenario):
    return furnace_refining_candidates(scenario) if scenario["family"] == "furnace_refining" else mining_delivery_candidates(scenario)


def _policy(path: Path, family: str):
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return int(payload.get("generation", 0)), DiagonalLinUCB.from_dict(payload["policy"])
    features = FURNACE_REFINING_FEATURES_V1 if family == "furnace_refining" else MINING_DELIVERY_FEATURES_V3
    return 0, DiagonalLinUCB(f"policy-{family}-g0000-initial", features)


def _checkpoint(path: Path, generation: int, policy: DiagonalLinUCB) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generation": generation, "policy": policy.to_dict()}, indent=2) + "\
", encoding="utf-8")


class _Coordinator:
    def __init__(self, scheduler, policies, password, live_directory, store, stop_event):
        self.scheduler = scheduler
        self.policies = policies
        self.password = password
        self.live_directory = live_directory
        self.store = store
        self.stop_event = stop_event
        self.lock = threading.Lock()
        self.events = Queue()
        self.results = []

    def claim(self, worker_id):
        with self.lock:
            jobs = self.scheduler.claim([worker_id])
            if jobs:
                job = jobs[0]
                with TrainingStore(self.store.path) as worker_store:
                    worker_store.start_episode(job.episode_id, job.scenario["scenario_id"], self.policies[job.family].policy_id, worker_id, job.selection_seed, status="queued")
            return jobs[0] if jobs else None

    def complete(self, worker_id, job, transition, error):
        with self.lock:
            self.scheduler.complete(worker_id, success=transition is not None)
            self.results.append((job, transition, error))
            self.events.put((job, transition, error))
            # Stop after the requested A-B cycles once the B cohort has reached
            # its terminal budget; already-running A/B episodes are allowed to drain.
            if self.scheduler.terminal_count("furnace-refining", job.policy_index) >= self.scheduler.episodes_per_policy and job.family == "furnace-refining":
                if job.policy_index + 1 >= self.cycles:
                    self.stop_event.set()

    def run_worker(self, worker):
        bridge = FactorioTrainingBridge(worker.script_output, host=worker.host, port=worker.rcon_port, password=self.password)
        telemetry = None
        try:
            try: telemetry = WorkerTelemetry(self.live_directory, worker.worker_id)
            except OSError: pass
            bridge.handshake()
            while not self.stop_event.is_set():
                job = self.claim(worker.worker_id)
                if job is None:
                    if not self.scheduler.pending and not self.scheduler.active_workers: break
                    self.stop_event.wait(0.05); continue
                policy = self.policies[job.family]
                def progress(event):
                    publish_best_effort(telemetry, {**event, "family": job.family, "policy_index": job.policy_index})
                try:
                    transition = run_episode(bridge, job.scenario, _catalog(job.scenario), policy, job.selection_seed, episode_id=job.episode_id, update_policy=False, stop_event=self.stop_event, on_progress=progress)
                    self.complete(worker.worker_id, job, transition, None)
                except Exception as exc:
                    self.complete(worker.worker_id, job, None, f"{type(exc).__name__}: {exc}")
        finally:
            bridge.close()

    def run(self, workers):
        with ThreadPoolExecutor(max_workers=len(workers)) as pool:
            futures = [pool.submit(self.run_worker, worker) for worker in workers]
            for future in futures: future.result()


def _parse(argv=None):
    p=argparse.ArgumentParser(description="Alternate ore-production and furnace-refining cohorts.")
    p.add_argument("--workers", type=Path, required=True)
    p.add_argument("--count", type=int, default=100)
    p.add_argument("--episodes-per-policy", type=int, default=1000)
    p.add_argument("--cycles", type=int, default=3, help="number of complete A->B policy cycles")
    p.add_argument("--max-workers", type=int, default=20)
    p.add_argument("--start-seed", type=int, default=0)
    p.add_argument("--database", type=Path, default=Path("data/training-abab/experience.db"))
    p.add_argument("--live-directory", type=Path, default=Path("data/training-abab/live"))
    p.add_argument("--checkpoint-directory", type=Path, default=Path("data/training-abab"))
    p.add_argument("--rcon-secret-file", type=Path, required=True)
    args=p.parse_args(argv)
    if args.count < 1 or args.episodes_per_policy < 1 or args.cycles < 1 or args.max_workers < 1: p.error("count, episodes-per-policy, cycles, and max-workers must be positive")
    return args


def main(argv=None):
    args=_parse(argv)
    workers=load_worker_specs(args.workers)[:args.max_workers]
    if not workers: raise SystemExit("no workers selected")
    password=args.rcon_secret_file.read_text(encoding="utf-8").strip()
    ore=generate_mining_delivery_curriculum(args.count, args.start_seed, target_rates_per_second=(1.0,))
    refinery=generate_furnace_refining_curriculum(args.count, args.start_seed)
    families=(AlternatingFamily("ore-production", tuple(ore)), AlternatingFamily("furnace-refining", tuple(refinery)))
    scheduler=AlternatingPolicyScheduler(families, episodes_per_policy=args.episodes_per_policy)
    policies={}
    generations={}
    for family, filename in (("ore-production", "policy-ore.json"), ("furnace-refining", "policy-refinery.json")):
        generations[family], policies[family] = _policy(args.checkpoint_directory / filename, family)
    args.live_directory.mkdir(parents=True, exist_ok=True)
    stop_event=threading.Event()
    with TrainingStore(args.database) as store:
        for scenario in ore + refinery: store.save_scenario(scenario, "train", scenario["scenario_hash"])
        for family, policy in policies.items(): store.save_policy(policy.policy_id, "diagonal_linucb", generations[family], {}, policy_snapshot(policy))
        coordinator=_Coordinator(scheduler, policies, password, args.live_directory, store, stop_event)
        coordinator.cycles=args.cycles
        coordinator.run(workers)
        learning_counts={family: 0 for family in policies}
        for job, transition, error in coordinator.results:
            if transition is not None:
                store.save_transition(transition)
                store.finish_episode(job.episode_id, transition["result"]["status"], transition["started_tick"], transition["ended_tick"], {"reward": transition["reward"]["total"], "family": job.family})
                if transition_can_update_policy(transition):
                    policies[job.family].update(transition["observation"], next(c for c in transition["candidates"] if c["action_id"] == transition["chosen_action_id"]), transition["reward"]["total"])
                    learning_counts[job.family] += 1
            else:
                store.finish_episode(job.episode_id, "failed", 0, 0, {"error": error, "family": job.family})
    for family, filename in (("ore-production", "policy-ore.json"), ("furnace-refining", "policy-refinery.json")):
        if learning_counts[family]:
            _checkpoint(args.checkpoint_directory / filename, generations[family] + 1, policies[family])
    completed = sum(
        transition is not None and transition_can_train_policy(transition)
        for _job, transition, _error in coordinator.results
    )
    summary={"family": scheduler.family, "policy_index": scheduler.policy_index, "completed": completed, "failed": len(coordinator.results) - completed, "policy_learning_episodes": learning_counts, "workers": len(workers), "cycles": args.cycles}
    print(json.dumps(summary, indent=2)); return 0

if __name__ == "__main__": raise SystemExit(main())
