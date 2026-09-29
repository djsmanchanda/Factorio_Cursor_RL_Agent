#!/usr/bin/env python3
# Path: tools/run_laya_workflow_batch.py
# Purpose: Run many disposable Laya exploration episodes in parallel slots of one training worker at raised game speed.

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.rcon_client import RconClient
from training.factorio_bridge import FactorioTrainingBridge
from training.laya_exploration import ExplorationDirector, ExplorationGate
from training.laya_runner import LayaPrimaryRunner, LayaRunnerConfig, checkpoint_model_ref
from training.scheduler import load_worker_specs

_GENERATORS = {
    "connected_workflow": "training.scenarios.connected_workflow:generate_connected_workflow_scenario",
    "mining_delivery": "training.scenarios.mining_delivery:generate_mining_delivery_scenario",
    "furnace_refining": "training.scenarios.furnace_refining:generate_furnace_refining_scenario",
}


class LockedPredictor:
    """Share one GPU model across episode threads; inference is serialized."""

    def __init__(self, predictor: Any):
        self._predictor = predictor
        self._lock = threading.Lock()
        self.tokenizer = predictor.tokenizer

    def predict(self, state, questions):
        with self._lock:
            return self._predictor.predict(state, questions)


class RandomPredictor:
    """Seeded uniform choice among non-escalation options: the floor baseline."""

    tokenizer = None

    def __init__(self, seed: int):
        import random
        self._rng = random.Random(f"random-baseline:{seed}")

    def predict(self, state, questions):
        kind = next(iter(questions))
        ids = [option for option in questions[kind]["criteria"] if "escalate" not in option]
        choice = self._rng.choice(ids)
        return {"answers": {kind: {"choice": choice, "confidence": 1.0 / len(ids),
                                   "probabilities": {option: 1.0 / len(ids) for option in ids}}}}


def _policy_ref(name: str) -> dict[str, str]:
    import hashlib
    return {"model_id": f"baseline-{name}", "model_hash": "sha256:" + hashlib.sha256(name.encode()).hexdigest()}


def guideline_context(family: str):
    """The guideline rules a decision state carries. Labels and live runs must share
    it: Laya reads these rules, so a mismatch shifts its inputs between training and use."""
    from orchestrator.laya_knowledge import load_guideline_context
    return load_guideline_context(
        ROOT, scopes=(family, "serve_goal", "choose_operation", "select_candidate", "wait_or_act", "recovery"),
        max_rules=8,
    )


def _generator(family: str):
    module_name, function = _GENERATORS[family].split(":")
    module = __import__(module_name, fromlist=[function])
    return getattr(module, function)


def set_game_speed(host: str, port: int, password: str, speed: float) -> str:
    client = RconClient(host, port, password, timeout=15.0)
    try:
        return client.command(f"/sc game.speed = {float(speed)} rcon.print(game.speed)").strip()
    finally:
        client.close()


def set_stepped_mode(host: str, port: int, password: str, enabled: bool, speed: float = 1.0) -> str:
    """Pause/unpause an instance's clock for stepped (one-slot) episodes.

    Measured live on factorio-training-linux-06 (2.1.20): `ticks_to_run`
    bursts are paced by game.speed, not run "as fast as the CPU allows" (3000
    ticks at speed=64 took ~0.79s wall, matching 3000/60/64). So a high speed
    is still set for the paused instance; the win is that no clock advances
    at all between explicit advance_ticks bursts, so RCON/inference latency
    can no longer leak into the game clock. Unpausing restores game.speed to
    1, matching the normal-mode cleanup contract.
    """
    client = RconClient(host, port, password, timeout=15.0)
    try:
        if enabled:
            return client.command(
                f"/sc game.speed = {float(speed)} game.tick_paused = true rcon.print(game.tick_paused)"
            ).strip()
        return client.command(
            "/sc game.tick_paused = false game.speed = 1 rcon.print(game.tick_paused)"
        ).strip()
    finally:
        client.close()


def instance_slots(workers: Path, instances: list[str], per_instance: int) -> list[tuple[Any, str]]:
    """(slot, rcon password) for the first N slots of each named worker instance."""
    specs = load_worker_specs(workers)
    result = []
    for instance in instances:
        slots = [worker for worker in specs if worker.instance_id == instance]
        if not 1 <= per_instance <= len(slots):
            raise ValueError(f"instance {instance!r} has {len(slots)} configured slots, need {per_instance}")
        password = (Path(slots[0].script_output).parent / "rcon-password").read_text(encoding="utf-8").strip()
        result.extend((slot, password) for slot in slots[:per_instance])
    return result


def set_speed_all(slots: list[tuple[Any, str]], speed: float) -> dict[str, str]:
    """Game speed is per Factorio instance; set it once on each."""
    seen = {}
    for slot, password in slots:
        if slot.instance_id not in seen:
            seen[slot.instance_id] = set_game_speed(slot.host, slot.rcon_port, password, speed)
    return seen


def set_stepped_all(slots: list[tuple[Any, str]], enabled: bool, speed: float = 1.0) -> dict[str, str]:
    """tick_paused (and, while enabling, game.speed) is per instance; set it once on each."""
    seen = {}
    for slot, password in slots:
        if slot.instance_id not in seen:
            seen[slot.instance_id] = set_stepped_mode(slot.host, slot.rcon_port, password, enabled, speed)
    return seen


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--workers", type=Path, default=ROOT / "training-workers-linux.json")
    result.add_argument("--instance", required=True, action="append",
                        help="Worker instance_id, e.g. factorio-training-linux-01; repeat to spread across workers")
    result.add_argument("--slots", type=int, default=4, help="Concurrent episodes per instance")
    result.add_argument("--family", choices=sorted(_GENERATORS), default="connected_workflow")
    result.add_argument("--level", type=int, default=1,
                        help="connected_workflow curriculum level; 2 adds the demand-scaling iron2 "
                             "expansion, 3 adds a competing circuit product line")
    result.add_argument("--seed-start", type=int, required=True)
    result.add_argument("--episodes", type=int, required=True)
    result.add_argument("--checkpoint", type=Path, default=ROOT / "data" / "laya-base" / "typed-decisions")
    result.add_argument("--epsilon", type=float, default=0.3)
    result.add_argument("--game-speed", type=float, default=1.0)
    result.add_argument("--stepped", action="store_true",
                        help="Pause each instance's clock and advance ticks explicitly for waits; "
                             "eliminates RCON/inference latency as game-tick noise. Requires --slots 1.")
    result.add_argument("--max-len", type=int, choices=(1024, 2048), default=1024,
                        help="Inference context; 2048 on an untrained base is for data collection only")
    result.add_argument("--allow-model-escalation", action="store_true",
                        help="Let the (untrained) model end episodes; off by default for data collection")
    result.add_argument("--max-decisions", type=int, default=40)
    result.add_argument("--max-no-progress", type=int, default=12)
    result.add_argument("--evidence-dir", type=Path, default=ROOT / "data" / "laya-exploration")
    result.add_argument("--run-prefix", required=True)
    result.add_argument("--policy", choices=("laya", "rollout", "random", "features"), default="laya",
                        help="laya uses --checkpoint; rollout/random/features are baselines on the same seeds")
    result.add_argument("--features-model", type=Path, help="JSON model from training/laya_feature_baseline.py")
    return result


def run_episode(slot, scenario: dict, director, gate, model_ref, knowledge, args, password) -> dict:
    run_id = f"{args.run_prefix}-{scenario['seed']}"
    bridge = FactorioTrainingBridge(slot.script_output, host=slot.host, port=slot.rcon_port, password=password)
    started = time.monotonic()
    try:
        bridge.handshake()
        result = LayaPrimaryRunner(
            bridge, scenario, director,
            LayaRunnerConfig(
                worker_id=slot.worker_id, run_id=run_id, evidence_dir=args.evidence_dir,
                max_decisions=args.max_decisions, max_no_progress=args.max_no_progress,
                game_speed=args.game_speed, stepped=args.stepped,
            ),
            model_ref=model_ref, knowledge_context=knowledge, exploration=gate,
        ).run()
    except Exception as error:  # One episode's failure must not stop the batch.
        result = {"status": "error", "run_id": run_id, "error": f"{type(error).__name__}: {error}"[:400]}
    finally:
        bridge.close()
    result["seed"] = scenario["seed"]
    result["inference_max_len"] = args.max_len
    result["policy"] = args.policy
    result["wall_seconds"] = round(time.monotonic() - started, 1)
    journal = Path(result.get("journal", ""))
    if journal.is_file():
        result["material_cost"] = 0.0
        for line in journal.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("type") == "transaction_started" and event.get("candidate"):
                result["material_cost"] += float((event["candidate"].get("features") or {}).get("material_cost") or 0.0)
            if event.get("type") == "termination":
                final = event.get("final_observation") or {}
                # The lab's own completion tick when available; observation time lags.
                result["elapsed_ticks"] = (final.get("metrics") or {}).get("completed_ticks", final.get("elapsed_ticks"))
                graph = event.get("work_graph") or {}
                result["closed_branches"] = sorted(branch["branch_id"] for branch in graph.get("closed", []))
    return result


def main(argv: list[str] | None = None) -> int:
    from training.laya_runtime import LayaModelPredictor
    from training.laya_sequence import SEQUENCE_VERSION

    args = parser().parse_args(argv)
    try:
        slots = instance_slots(args.workers, args.instance, args.slots)
    except ValueError as error:
        parser().error(str(error))
    if not 1 <= args.episodes <= 10_000 or not 0.1 <= args.game_speed <= 64:
        parser().error("episodes must be 1..10000 and game speed 0.1..64")
    if args.stepped and args.slots != 1:
        parser().error("--stepped pauses the whole instance clock; each instance needs exactly --slots 1")
    generate = _generator(args.family)
    seeds = range(args.seed_start, args.seed_start + args.episodes)
    if args.family == "connected_workflow":
        scenarios = [generate(seed, level=args.level) for seed in seeds]
    else:
        if args.level != 1:
            parser().error("--level only applies to --family connected_workflow")
        scenarios = [generate(seed) for seed in seeds]

    budget_profile = "director-2048" if args.max_len == 2048 else "director-1024"
    shared = None
    if args.policy == "laya":
        predictor = LayaModelPredictor(
            args.checkpoint, explicit_sequence_version=SEQUENCE_VERSION, max_len=args.max_len,
        )
        shared = LockedPredictor(predictor)
        model_ref = checkpoint_model_ref(args.checkpoint)
    elif args.policy == "features":
        import hashlib
        from training.laya_feature_baseline import FeaturePredictor
        if args.features_model is None:
            parser().error("--policy features requires --features-model")
        features = FeaturePredictor.load(args.features_model)
        model_ref = {"model_id": "baseline-features",
                     "model_hash": "sha256:" + hashlib.sha256(args.features_model.read_bytes()).hexdigest()}
    else:
        model_ref = _policy_ref(args.policy)
    knowledge = guideline_context(args.family)
    summary_path = args.evidence_dir / "batches" / f"{args.run_prefix}.jsonl"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    if args.stepped:
        print(f"tick_paused -> {set_stepped_all(slots, True, args.game_speed)}", flush=True)
    else:
        print(f"game.speed -> {set_speed_all(slots, args.game_speed)}", flush=True)
    free = list(slots)
    free_lock = threading.Lock()

    def task(scenario):
        with free_lock:
            slot, password = free.pop()
        try:
            # Each episode has its own seeded gate; the model is shared.
            if args.policy == "laya":
                gate = ExplorationGate(scenario["seed"], args.epsilon, args.allow_model_escalation)
                director = ExplorationDirector(
                    shared, gate, request_tokenizer=shared.tokenizer, budget_profile=budget_profile,
                )
            else:
                from tools.laya_counterfactual_labels import ScriptedPredictor
                baseline = {"rollout": lambda: ScriptedPredictor([]),
                            "random": lambda: RandomPredictor(scenario["seed"]),
                            "features": lambda: features}[args.policy]()
                gate = ExplorationGate(scenario["seed"], 0.0, allow_model_escalation=False)
                director = ExplorationDirector(baseline, gate, budget_profile=budget_profile)
            return run_episode(slot, scenario, director, gate, model_ref, knowledge, args, password)
        finally:
            with free_lock:
                free.append((slot, password))

    counts: dict[str, int] = {}
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=len(slots)) as pool, summary_path.open("a", encoding="utf-8") as out:
            for future in as_completed([pool.submit(task, scenario) for scenario in scenarios]):
                result = future.result()
                counts[result["status"]] = counts.get(result["status"], 0) + 1
                out.write(json.dumps(result, sort_keys=True) + "\n")
                out.flush()
                print(f"{result.get('run_id')} {result['status']} decisions={result.get('decisions')} "
                      f"ticks={result.get('elapsed_ticks')} closed={result.get('closed_branches')} "
                      f"wall={result['wall_seconds']}s {result.get('error', '')}", flush=True)
    finally:
        set_stepped_all(slots, False) if args.stepped else set_speed_all(slots, 1.0)
    print(json.dumps({"episodes": len(scenarios), "statuses": counts,
                      "wall_seconds": round(time.monotonic() - started, 1), "summary": str(summary_path)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
