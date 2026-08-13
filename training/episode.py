# Path: training/episode.py
# Purpose: Execute one immutable candidate plan and emit one validated transition.

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence

from planners.plan_validation import actions
from planners.sandbox_infrastructure import build_layout_authorization
from training.canonical import policy_hash
from training.candidates import mining_delivery_candidates
from training.contracts import validate_transition
from training.policies import policy_snapshot
from training.rewards import reward_components


class EpisodeCapacityInterrupted(RuntimeError):
    """Stop a disposable episode because its shared runtime is overloaded."""


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
        "stage_index": float(metrics.get("stage_index", objective.get("stage_index", 1))),
        "stage_count": float(objective.get("stage_count", 1)),
    }


def _result(report: Mapping) -> dict:
    status = str(report["status"])
    failure = report.get("failure") or {"kind": "none", "reason": ""}
    return {"status": status, "failure_kind": failure["kind"], "reason": failure["reason"]}



def _candidate_record(candidate: Mapping) -> dict:
    return {key: candidate[key] for key in ("action_id", "plan_hash", "features")}


def _material_cost(candidate: Mapping) -> int:
    return int(candidate["features"].get("material_cost", 0))


def _stage_candidates(scenario: Mapping, report: Mapping) -> list[Mapping]:
    """Build a fresh action catalog when an in-place demand stage advances."""
    objective = dict(scenario["objective"])
    objective["target_rate_per_tick"] = float(
        (report.get("objective") or {}).get("target_rate_per_tick", objective["target_rate_per_tick"])
    )
    destinations = (report.get("objective") or {}).get("destination_fixture_ids")
    if destinations:
        objective["destination_fixture_id"] = destinations[0]
    staged = dict(scenario)
    staged["objective"] = objective
    # Candidate validation only needs the original immutable contract; the
    # generated plans retain the original surface, force, and protected fixtures.
    return mining_delivery_candidates(staged, validate_contract=False)


def run_episode(
    bridge, scenario: Mapping, candidates: Sequence[Mapping], policy, selection_seed: int,
    *, episode_id: str | None = None, poll_seconds: float = 5.0,
    stop_event=None,
    update_policy: bool = True, on_progress=None,
) -> dict:
    """Run one candidate, adapting in-place when an ordered demand stage advances."""
    identifier = episode_id or f"episode-{uuid.uuid4().hex}"
    provisioned = False
    context = {"scenario_id": scenario["scenario_id"], "policy_id": str(policy.policy_id)}
    all_candidates = list(candidates)
    chosen = None
    execution_total = {"succeeded_placements": 0, "failed_placements": 0}
    material_cost = 0
    try:
        if stop_event is not None and stop_event.is_set():
            raise EpisodeCapacityInterrupted("training capacity changed before provisioning")
        provision = bridge.provision(identifier, scenario)
        provisioned = True
        _progress(on_progress, "provisioned", identifier, {**context, "report": provision})
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
        execution_total["succeeded_placements"] += int(execution.get("succeeded_placements", 0))
        execution_total["failed_placements"] += int(execution.get("failed_placements", 0))
        material_cost += _material_cost(chosen)
        _progress(on_progress, "executed", identifier, {
            **context, "chosen_action_id": chosen["action_id"], "execution": execution,
        })
        report = bridge.observe(identifier)
        _progress(on_progress, "measuring", identifier, {**context, "report": report})
        current_stage = int(_observation(report).get("stage_index", 1))
        stages = scenario.get("objective", {}).get("stages") or []
        while report["status"] in {"ready", "running"}:
            if stop_event is not None:
                if stop_event.wait(poll_seconds):
                    raise EpisodeCapacityInterrupted("training capacity changed during measurement")
            else:
                time.sleep(poll_seconds)
            report = bridge.observe(identifier)
            observed_stage = int(_observation(report).get("stage_index", current_stage))
            if stages and observed_stage > current_stage and observed_stage <= len(stages):
                try:
                    stage_catalog = _stage_candidates(scenario, report)
                except (ValueError, KeyError):
                    stage_catalog = []
                if stage_catalog:
                    all_candidates.extend(
                        item for item in stage_catalog
                        if item["action_id"] not in {candidate["action_id"] for candidate in all_candidates}
                    )
                    stage_choice = policy.select(stage_catalog, _observation(report), selection_seed + observed_stage)
                    stage_execution = bridge.execute(
                        identifier, build_layout_authorization([stage_choice["plan"]]), stage_choice["plan"],
                    )
                    execution_total["succeeded_placements"] += int(stage_execution.get("succeeded_placements", 0))
                    execution_total["failed_placements"] += int(stage_execution.get("failed_placements", 0))
                    material_cost += _material_cost(stage_choice)
                    chosen = stage_choice
                    _progress(on_progress, "stage_adapted", identifier, {
                        **context, "stage_index": observed_stage,
                        "chosen_action_id": stage_choice["action_id"], "execution": stage_execution,
                    })
                current_stage = observed_stage
            _progress(on_progress, "measuring", identifier, {**context, "report": report})
        failed = execution_total["failed_placements"]
        live_metrics = report["metrics"]
        candidate_metrics = chosen["features"]
        transition = {
            "version": "1.2.0", "episode_id": identifier,
            "scenario_id": scenario["scenario_id"], "scenario_seed": scenario["seed"],
            "scenario_hash": scenario["scenario_hash"], "policy_id": str(policy.policy_id),
            "policy_hash": policy_hash(policy_snapshot(policy)),
            "started_tick": int(provision["started_tick"]), "ended_tick": int(report["tick"]),
            "observation": initial, "candidates": [_candidate_record(item) for item in all_candidates],
            "chosen_action_id": chosen["action_id"], "result": _result(report),
            "metrics": {
                "initial_rate_per_tick": initial["delivered_rate_per_tick"],
                "final_rate_per_tick": float(live_metrics.get("rate_per_tick", 0.0)),
                "delivered_items": int(live_metrics.get("delivered_items", 0)),
                "material_cost": material_cost,
                "placements_succeeded": execution_total["succeeded_placements"],
                "placements_failed": failed,
                "electric_pole_count": int(live_metrics.get("electric_pole_count", 0)),
                "collection_belt_tiles": int(candidate_metrics.get("collection_belt_tiles", 0)),
                "actual_delivery_route_tiles": int(candidate_metrics.get("actual_delivery_route_tiles", 0)),
                "shortest_delivery_route_tiles": int(candidate_metrics.get("shortest_delivery_route_tiles", 0)),
                "route_excess_tiles": int(candidate_metrics.get("route_excess_tiles", 0)),
                "route_efficiency": float(candidate_metrics.get("route_efficiency", 0.0)),
                "occupied_footprint_tiles": int(live_metrics.get("occupied_footprint_tiles", 0)),
                "placed_mining_drills": int(live_metrics.get("placed_mining_drills", 0)),
                "productive_mining_drills": int(live_metrics.get("productive_mining_drills", 0)),
                "productive_mining_drill_ratio": float(live_metrics.get("productive_mining_drill_ratio", 0.0)),
                "mining_drill_capacity_ticks": int(live_metrics.get("mining_drill_capacity_ticks", 0)),
                "mining_drill_working_ticks": int(live_metrics.get("mining_drill_working_ticks", 0)),
                "mining_drill_blocked_ticks": int(live_metrics.get("mining_drill_blocked_ticks", 0)),
                "mining_drill_idle_ticks": int(live_metrics.get("mining_drill_idle_ticks", 0)),
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
        if provisioned:
            bridge.recycle(identifier)