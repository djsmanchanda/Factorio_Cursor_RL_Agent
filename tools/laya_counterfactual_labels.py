#!/usr/bin/env python3
# Path: tools/laya_counterfactual_labels.py
# Purpose: Label serve_goal decisions by replaying a decision prefix, forcing each alternative, and measuring completion.

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_laya_workflow_batch import guideline_context, instance_slots, set_speed_all, set_stepped_all
from training.canonical import scenario_hash
from training.candidates.connected_workflow import BRANCH_IDS
from training.contracts import validate_scenario
from training.factorio_bridge import FactorioTrainingBridge
from training.laya_exploration import ExplorationDirector, ExplorationGate
from training.laya_runner import LayaPrimaryRunner, LayaRunnerConfig
from training.scenarios.connected_workflow import generate_connected_workflow_scenario

LABEL_VERSION = "laya-counterfactual-v3"
# The continuation after the forced choice: the label measures each option's
# value under it (one step of policy improvement), not a global optimum. Policy
# iteration replaces this scripted rollout with the current learned policy.
ROLLOUT = "build-first-ready-default-variant-else-wait-5s"


class ReplayDiverged(RuntimeError):
    """A forced prefix choice was not offered, so the replay is not faithful."""


def replica(scenario: dict[str, Any], tag: str) -> dict[str, Any]:
    """Same world on its own surface/force so alternatives can run concurrently."""
    copy = json.loads(json.dumps(scenario))
    copy["scenario_id"] = f"{scenario['scenario_id']}-{tag}"
    copy["environment"]["surface_name"] = f"{scenario['environment']['surface_name']}-{tag}"
    copy["environment"]["force_name"] = f"{scenario['environment']['force_name']}-{tag}"
    copy["scenario_hash"] = scenario_hash(copy)
    validate_scenario(copy)
    return copy


#  Level 1 only ever offers BRANCH_IDS, so appending the level-2 expansion
# branch here changes nothing for level-1 replays (its serve option is never
# in `ids`); it only gives the scripted rollout something to build when a
# high randomized level-2 target actually needs the second furnace. It is
# checked last so the core chain and construction are always finished first.
_ROLLOUT_BRANCH_PRIORITY = BRANCH_IDS + ("iron2",)


def rollout_choice(ids: Sequence[str]) -> str:
    for branch in _ROLLOUT_BRANCH_PRIORITY:
        if f"serve.{branch}" in ids:
            return f"serve.{branch}"
    for preferred in ("recovery.reselect", "wait.5s"):
        if preferred in ids:
            return preferred
    retry = [option for option in ids if option.startswith("recovery.retry.")]
    if retry:
        return retry[0]
    # Variant questions: the first listed variant is the default (am2/fast) layout.
    return next(option for option in ids if "escalate" not in option)


def greedy_non_escalation(predictor, state, questions) -> str:
    """The continuation policy's best option; data collection never escalates."""
    kind = next(iter(questions))
    probabilities = predictor.predict(state, questions)["answers"][kind]["probabilities"]
    allowed = [option for option in questions[kind]["criteria"] if "escalate" not in option]
    return max(allowed, key=lambda option: probabilities.get(option, 0.0))


class ScriptedPredictor:
    """Replay a prefix of option ids, then follow the continuation; records what was offered."""

    tokenizer = None

    def __init__(self, prefix: Sequence[str], continuation: Any = None):
        self.prefix = list(prefix)
        self.continuation = continuation
        self.trace: list[dict[str, Any]] = []

    def predict(self, state, questions):
        kind = next(iter(questions))
        ids = list(questions[kind]["criteria"])
        index = len(self.trace)
        if index < len(self.prefix):
            choice = self.prefix[index]
            if choice not in ids:
                raise ReplayDiverged(f"decision {index}: {choice} not offered in {ids}")
        elif self.continuation is not None:
            choice = greedy_non_escalation(self.continuation, state, questions)
        else:
            choice = rollout_choice(ids)
        digest = (state.get("observation_digest") or {}).get("work_branches")
        self.trace.append({"kind": kind, "options": ids, "choice": choice, "work_branches": digest})
        return {"answers": {kind: {"choice": choice, "confidence": 1.0,
                                   "probabilities": {option: float(option == choice) for option in ids}}}}


def run_forced(slot, password, scenario, prefix, *, run_id, args) -> dict[str, Any]:
    predictor = ScriptedPredictor(prefix, args.continuation_policy)
    gate = ExplorationGate(scenario["seed"], 0.0, allow_model_escalation=False)
    director = ExplorationDirector(predictor, gate, budget_profile="director-2048")
    model_ref = {"model_id": "counterfactual-rollout-v1",
                 "model_hash": "sha256:" + hashlib.sha256(f"{LABEL_VERSION}:{args.continuation_id}".encode()).hexdigest()}
    # Report polling latency becomes game-tick jitter at raised speed.
    bridge = FactorioTrainingBridge(slot.script_output, host=slot.host, port=slot.rcon_port,
                                    password=password, poll_interval=args.poll_interval)
    try:
        bridge.handshake()
        result = LayaPrimaryRunner(
            bridge, scenario, director,
            LayaRunnerConfig(worker_id=slot.worker_id, run_id=run_id, evidence_dir=args.evidence_dir,
                             max_decisions=args.max_decisions, max_no_progress=args.max_decisions,
                             game_speed=args.game_speed, stepped=args.stepped),
            model_ref=model_ref, knowledge_context=args.knowledge, exploration=gate,
        ).run()
    finally:
        bridge.close()
    elapsed, material = None, 0.0
    for line in Path(result["journal"]).read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") == "transaction_started" and event.get("candidate"):
            # Material committed by every executed build (a stale/failed start is rare).
            material += float((event["candidate"].get("features") or {}).get("material_cost") or 0.0)
        if event.get("type") == "termination":
            final = event.get("final_observation") or {}
            # The lab's own completion tick; observation time lags by up to a wait.
            elapsed = (final.get("metrics") or {}).get("completed_ticks", final.get("elapsed_ticks"))
    # The predictor raises before recording a decision it cannot replay.
    diverged = len(predictor.trace) < len(prefix)
    return {"run_id": run_id, "status": result["status"], "elapsed_ticks": elapsed, "material_cost": material,
            "decisions": result.get("decisions", len(predictor.trace)),
            "trace": predictor.trace, "diverged": diverged, "journal": result["journal"]}


# docs/rl/README.md soft rule (rule.sustained-efficiency): minimize completion
# time and materials. Material is priced in ticks so both share one scale.
MATERIAL_TICKS_PER_UNIT = 10.0
# Each decision costs a model call and a round trip; without a price, a policy
# can burn its whole decision budget on 1 s waits at no cost in the labels.
DECISION_TICKS = 60.0


def outcome_cost(run: dict[str, Any], max_ticks: int, material_weight: float = 0.0,
                 decision_ticks: float = 0.0) -> float:
    # Unfinished runs cost the full episode budget: slower than any completion.
    ticks = float(run["elapsed_ticks"]) if run["status"] == "completed" else float(max_ticks)
    return (ticks + material_weight * float(run.get("material_cost") or 0.0)
            + decision_ticks * float(run.get("decisions") or 0))


def soft_target(costs: dict[str, float], temperature_ticks: float) -> dict[str, float]:
    best = min(costs.values())
    weights = {option: math.exp(-(cost - best) / temperature_ticks) for option, cost in costs.items()}
    total = sum(weights.values())
    return {option: weight / total for option, weight in weights.items()}


def label_seed(seed, pool_slots, args) -> list[dict[str, Any]]:
    scenario = generate_connected_workflow_scenario(seed, level=args.level)
    max_ticks = int(scenario["constraints"]["max_episode_ticks"])
    def base_run(index):
        slot, password = pool_slots.acquire()
        try:
            return run_forced(slot, password, replica(scenario, f"b{index}"), [],
                              run_id=f"{args.run_prefix}-{seed}-base{index}", args=args)
        finally:
            pool_slots.release((slot, password))

    # Identical rollouts measure replay timing noise; the first is the trajectory.
    with ThreadPoolExecutor(max_workers=args.base_repeats) as pool:
        bases = list(pool.map(base_run, range(args.base_repeats)))
    base = bases[0]
    def cost(run):
        return outcome_cost(run, max_ticks, args.material_weight, args.decision_ticks)
    base_costs = [cost(run) for run in bases]
    labels = []
    depth = min(args.depth, len(base["trace"]))
    jobs = []
    for k in range(depth):
        point = base["trace"][k]
        prefix = [step["choice"] for step in base["trace"][:k]]
        for index, option in enumerate(point["options"]):
            if option == point["choice"] or "escalate" in option:
                continue
            jobs.append((k, option, prefix + [option], f"k{k}o{index}"))

    def job(item):
        k, option, prefix, tag = item
        slot, password = pool_slots.acquire()
        try:
            return k, option, run_forced(slot, password, replica(scenario, tag), prefix,
                                         run_id=f"{args.run_prefix}-{seed}-k{k}-{option}", args=args)
        finally:
            pool_slots.release((slot, password))

    pool = ThreadPoolExecutor(max_workers=args.slots)
    try:
        results = list(pool.map(job, jobs))
    finally:
        # On interrupt, drop queued replays; in-flight episodes still recycle.
        pool.shutdown(wait=True, cancel_futures=True)
    for k in range(depth):
        point = base["trace"][k]
        costs = {point["choice"]: cost(base)}
        faithful = True
        for kk, option, run in results:
            if kk != k:
                continue
            branch_state = run["trace"][k] if len(run["trace"]) > k else None
            # Faithful only if the replay reached the same branch point.
            if run["diverged"] or not branch_state or branch_state["options"] != point["options"] \
                    or branch_state["work_branches"] != point["work_branches"]:
                faithful = False
                continue
            costs[option] = cost(run)
        if len(costs) < 2:
            continue
        labels.append({
            "version": LABEL_VERSION, "rollout": args.continuation_id, "seed": seed,
            "scenario_id": scenario["scenario_id"], "decision_index": k,
            "decision_kind": point["kind"], "options": point["options"],
            "work_branches": point["work_branches"],
            "base_journal": base["journal"], "base_choice": point["choice"],
            "cost_ticks": costs, "all_alternatives_faithful": faithful,
            "base_repeat_costs": base_costs, "noise_ticks": max(base_costs) - min(base_costs),
            "cost_weights": {"completion_ticks": 1.0, "material_ticks_per_unit": args.material_weight,
                             "ticks_per_decision": args.decision_ticks, "guideline": "rule.sustained-efficiency"},
            "soft_target": soft_target(costs, args.temperature_ticks),
        })
    return labels


class SlotPool:
    def __init__(self, slots):
        self._free = list(slots)
        self._cond = threading.Condition()

    def acquire(self):
        with self._cond:
            while not self._free:
                self._cond.wait()
            return self._free.pop()

    def release(self, slot):
        with self._cond:
            self._free.append(slot)
            self._cond.notify()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=Path, default=ROOT / "training-workers-linux.json")
    parser.add_argument("--instance", required=True, action="append", help="Repeat to spread across workers")
    parser.add_argument("--slots", type=int, default=8, help="Concurrent episodes per instance")
    parser.add_argument("--level", type=int, default=1,
                        help="connected_workflow curriculum level; 2 adds the demand-scaling iron2 expansion")
    parser.add_argument("--seed-start", type=int, required=True)
    parser.add_argument("--seeds", type=int, required=True)
    parser.add_argument("--depth", type=int, default=6, help="Branch at the first K base decisions")
    parser.add_argument("--base-repeats", type=int, default=3, help="Repeat the base rollout to measure noise")
    parser.add_argument("--poll-interval", type=float, default=0.05, help="Bridge report polling, wall seconds")
    parser.add_argument("--game-speed", type=float, default=8.0)
    parser.add_argument("--stepped", action="store_true",
                        help="Pause each instance's clock and advance ticks explicitly for waits, "
                             "removing RCON/inference latency as replay noise. Requires --slots 1.")
    parser.add_argument("--max-decisions", type=int, default=60)
    parser.add_argument("--temperature-ticks", type=float, default=600.0)
    parser.add_argument("--material-weight", type=float, default=MATERIAL_TICKS_PER_UNIT,
                        help="Ticks charged per unit of built material (0 = time only)")
    parser.add_argument("--decision-ticks", type=float, default=DECISION_TICKS,
                        help="Ticks charged per decision the run took (0 = none)")
    parser.add_argument("--continuation", choices=("rollout", "features"), default="rollout",
                        help="Policy after the forced choice; features = policy iteration on that model")
    parser.add_argument("--features-model", type=Path, help="JSON model for --continuation features")
    parser.add_argument("--evidence-dir", type=Path, default=ROOT / "data" / "laya-counterfactual")
    parser.add_argument("--run-prefix", required=True)
    args = parser.parse_args(argv)
    if args.stepped and args.slots != 1:
        parser.error("--stepped pauses the whole instance clock; each instance needs exactly --slots 1")
    try:
        slots = instance_slots(args.workers, args.instance, args.slots)
    except ValueError as error:
        parser.error(str(error))
    args.continuation_policy, args.continuation_id = None, ROLLOUT
    if args.continuation == "features":
        from training.laya_feature_baseline import FeaturePredictor
        if args.features_model is None:
            parser.error("--continuation features requires --features-model")
        args.continuation_policy = FeaturePredictor.load(args.features_model)
        digest = hashlib.sha256(args.features_model.read_bytes()).hexdigest()[:16]
        args.continuation_id = f"policy:features:{digest}"
    args.slots = len(slots)
    # Same rules as live runs, so labelled states match what Laya sees in use.
    args.knowledge = guideline_context("connected_workflow")
    out = args.evidence_dir / "labels" / f"{args.run_prefix}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    pool_slots = SlotPool(slots)
    if args.stepped:
        print(f"tick_paused -> {set_stepped_all(slots, True, args.game_speed)}", flush=True)
    else:
        print(f"game.speed -> {set_speed_all(slots, args.game_speed)}", flush=True)
    started, written = time.monotonic(), 0
    try:
        # Seeds run concurrently; each seed's alternatives share the slot pool.
        seeds_pool = ThreadPoolExecutor(max_workers=max(1, args.slots // 2))
        try:
            with out.open("a", encoding="utf-8") as handle:
                futures = [seeds_pool.submit(label_seed, seed, pool_slots, args)
                           for seed in range(args.seed_start, args.seed_start + args.seeds)]
                for future in futures:
                    for label in future.result():
                        handle.write(json.dumps(label, sort_keys=True) + "\n")
                        handle.flush()
                        written += 1
                        print(f"seed {label['seed']} k{label['decision_index']} faithful={label['all_alternatives_faithful']} "
                              f"costs={label['cost_ticks']}", flush=True)
        finally:
            seeds_pool.shutdown(wait=True, cancel_futures=True)
    finally:
        set_stepped_all(slots, False) if args.stepped else set_speed_all(slots, 1.0)
    print(json.dumps({"labels": written, "wall_seconds": round(time.monotonic() - started, 1), "output": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
