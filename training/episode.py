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
from training.rewards import reward_components


def _progress(callback, phase: str, identifier: str, payload: Mapping) -> None:
    if callback is not None:
        callback({"phase": phase, "episode_id": identifier, **dict(payload)})


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



def _candidate_record(candidate: Mapping) -> dict:
    return {key: candidate[key] for key in ("action_id", "plan_hash", "features")}


def _material_cost(candidate: Mapping) -> int:
    return int(candidate["features"].get("material_cost", 0))


def run_episode(
    bridge, scenario: Mapping, candidates: Sequence[Mapping], policy, selection_seed: int,
    *, episode_id: str | None = None, poll_seconds: float = 0.25,
    update_policy: bool = True, on_progress=None,
) -> dict:
    """Run one candidate and always request disposal of its training world."""
    identifier = episode_id or f"episode-{uuid.uuid4().hex}"
    provision = bridge.provision(identifier, scenario)
    context = {"scenario_id": scenario["scenario_id"], "policy_id": str(policy.policy_id)}
    _progress(on_progress, "provisioned", identifier, {**context, "report": provision})
    try:
        initial_report = bridge.observe(identifier)
        _progress(on_progress, "observed", identifier, {**context, "report": initial_report})
        initial = _observation(initial_report)
        chosen = policy.select(candidates, initial, selection_seed)
        _progress(on_progress, "selected", identifier, {
            **context, "chosen_action_id": chosen["action_id"],
            "candidate_features": chosen["features"], "report": initial_report,
        })
        authorization = build_layout_authorization([chosen["plan"]])
        execution = bridge.execute(identifier, authorization, chosen["plan"])
        _progress(on_progress, "executed", identifier, {
            **context, "chosen_action_id": chosen["action_id"], "execution": execution,
        })
        report = bridge.observe(identifier)
        _progress(on_progress, "measuring", identifier, {**context, "report": report})
        while report["status"] in {"ready", "running"}:
            time.sleep(poll_seconds)
            report = bridge.observe(identifier)
            _progress(on_progress, "measuring", identifier, {**context, "report": report})
        failed = int(execution.get("failed_placements", 0))
        material_cost = _material_cost(chosen)
        live_metrics = report["metrics"]
        candidate_metrics = chosen["features"]
        transition = {
            "version": "1.2.0", "episode_id": identifier,
            "scenario_id": scenario["scenario_id"], "scenario_seed": scenario["seed"],
            "scenario_hash": scenario["scenario_hash"], "policy_id": str(policy.policy_id),
            "policy_hash": policy_hash(policy_snapshot(policy)),
            "started_tick": int(provision["started_tick"]), "ended_tick": int(report["tick"]),
            "observation": initial, "candidates": [_candidate_record(item) for item in candidates],
            "chosen_action_id": chosen["action_id"], "result": _result(report),
            "metrics": {
                "initial_rate_per_tick": initial["delivered_rate_per_tick"],
                "final_rate_per_tick": float(live_metrics["rate_per_tick"]),
                "delivered_items": int(live_metrics["delivered_items"]),
                "material_cost": material_cost,
                "placements_succeeded": int(execution.get("succeeded_placements", 0)),
                "placements_failed": failed,
                "electric_pole_count": int(live_metrics["electric_pole_count"]),
                "collection_belt_tiles": int(candidate_metrics["collection_belt_tiles"]),
                "actual_delivery_route_tiles": int(
                    candidate_metrics["actual_delivery_route_tiles"]
                ),
                "shortest_delivery_route_tiles": int(
                    candidate_metrics["shortest_delivery_route_tiles"]
                ),
                "route_excess_tiles": int(candidate_metrics["route_excess_tiles"]),
                "route_efficiency": float(candidate_metrics["route_efficiency"]),
                "occupied_footprint_tiles": int(live_metrics["occupied_footprint_tiles"]),
                "placed_mining_drills": int(live_metrics["placed_mining_drills"]),
                "productive_mining_drills": int(live_metrics["productive_mining_drills"]),
                "productive_mining_drill_ratio": float(
                    live_metrics["productive_mining_drill_ratio"]
                ),
                "mining_drill_capacity_ticks": int(
                    live_metrics["mining_drill_capacity_ticks"]
                ),
                "mining_drill_working_ticks": int(
                    live_metrics["mining_drill_working_ticks"]
                ),
                "mining_drill_blocked_ticks": int(
                    live_metrics["mining_drill_blocked_ticks"]
                ),
                "mining_drill_idle_ticks": int(live_metrics["mining_drill_idle_ticks"]),
            },
            "reward": reward_components(scenario, report, chosen, failed),
            "next_observation": _observation(report),
        }
        validate_transition(transition)
        if update_policy and hasattr(policy, "update"):
            policy.update(initial, chosen, transition["reward"]["total"])
        _progress(on_progress, "finished", identifier, {
            **context, "report": report, "reward_total": transition["reward"]["total"],
            "chosen_action_id": chosen["action_id"],
        })
        return transition
    finally:
        bridge.recycle(identifier)
