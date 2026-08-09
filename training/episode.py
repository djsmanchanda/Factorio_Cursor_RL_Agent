# Path: training/episode.py
# Purpose: Execute one immutable candidate plan and emit one validated transition.

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence

from planners.plan_validation import actions
from planners.sandbox_infrastructure import build_layout_authorization
from training.canonical import policy_hash
from training.contracts import validate_transition
from training.policies import policy_snapshot


def _observation(report: Mapping) -> dict[str, float]:
    metrics = report.get("metrics") or {}
    objective = report.get("objective") or {}
    return {
        "delivered_rate_per_tick": float(metrics.get("rate_per_tick", 0.0)),
        "target_rate_per_tick": float(objective.get("target_rate_per_tick", 0.0)),
        "sustained_ticks": float(metrics.get("sustained_ticks", 0)),
        "resource_remaining": float(metrics.get("resource_remaining", 0)),
    }


def _result(report: Mapping) -> dict:
    status = str(report["status"])
    failure = report.get("failure") or {"kind": "none", "reason": ""}
    return {"status": status, "failure_kind": failure["kind"], "reason": failure["reason"]}


def _reward(scenario: Mapping, report: Mapping, material_cost: int, failed: int) -> dict:
    weights = scenario["reward_weights"]
    metrics = report["metrics"]
    completed = report["status"] == "completed"
    components = {
        "completion": float(weights["completion"]) if completed else 0.0,
        "throughput": float(metrics["delivered_items"]) * float(weights["delivered_item"]),
        "elapsed_ticks": float(report["elapsed_ticks"]) * float(weights["elapsed_tick"]),
        "materials": material_cost * float(weights["material_item"]),
        "infrastructure": 0.0,
        "failed_placements": failed * float(weights["failed_placement"]),
    }
    components["total"] = sum(components.values())
    return components


def _candidate_record(candidate: Mapping) -> dict:
    return {key: candidate[key] for key in ("action_id", "plan_hash", "features")}


def _material_cost(candidate: Mapping) -> int:
    return int(candidate["features"].get("material_cost", 0))


def run_episode(
    bridge, scenario: Mapping, candidates: Sequence[Mapping], policy, selection_seed: int,
    *, episode_id: str | None = None, poll_seconds: float = 0.25,
    update_policy: bool = True,
) -> dict:
    """Run one candidate and always request disposal of its training world."""
    identifier = episode_id or f"episode-{uuid.uuid4().hex}"
    provision = bridge.provision(identifier, scenario)
    try:
        initial_report = bridge.observe(identifier)
        initial = _observation(initial_report)
        chosen = policy.select(candidates, initial, selection_seed)
        authorization = build_layout_authorization([chosen["plan"]])
        execution = bridge.execute(authorization, chosen["plan"])
        report = bridge.observe(identifier)
        while report["status"] in {"ready", "running"}:
            time.sleep(poll_seconds)
            report = bridge.observe(identifier)
        failed = int(execution.get("failed_placements", 0))
        material_cost = _material_cost(chosen)
        transition = {
            "version": "1.1.0", "episode_id": identifier,
            "scenario_id": scenario["scenario_id"], "scenario_seed": scenario["seed"],
            "scenario_hash": scenario["scenario_hash"], "policy_id": str(policy.policy_id),
            "policy_hash": policy_hash(policy_snapshot(policy)),
            "started_tick": int(provision["started_tick"]), "ended_tick": int(report["tick"]),
            "observation": initial, "candidates": [_candidate_record(item) for item in candidates],
            "chosen_action_id": chosen["action_id"], "result": _result(report),
            "metrics": {
                "initial_rate_per_tick": initial["delivered_rate_per_tick"],
                "final_rate_per_tick": float(report["metrics"]["rate_per_tick"]),
                "delivered_items": int(report["metrics"]["delivered_items"]),
                "material_cost": material_cost,
                "placements_succeeded": int(execution.get("succeeded_placements", 0)),
                "placements_failed": failed,
            },
            "reward": _reward(scenario, report, material_cost, failed),
            "next_observation": _observation(report),
        }
        validate_transition(transition)
        if update_policy and hasattr(policy, "update"):
            policy.update(initial, chosen, transition["reward"]["total"])
        return transition
    finally:
        bridge.recycle(identifier)
