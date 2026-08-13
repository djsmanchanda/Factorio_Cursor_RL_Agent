# Path: training/candidates/furnace_refining.py
# Purpose: Generate safe furnace and inserter alternatives for refinement cohorts.

from __future__ import annotations
from planners.plan_validation import occupied_tile_indices, validate_build_plan, actions
from training.canonical import plan_hash
from training.contracts import validate_scenario

_COST = {"electric-furnace": 50, "fast-inserter": 11, "medium-electric-pole": 5}

def _candidate(scenario, variant: int) -> dict:
    source = next(f for f in scenario["fixtures"] if f["kind"] == "item_source")["position"]
    y = float(source[1]); furnace_x = 0.0
    power = next(f for f in scenario["fixtures"] if f["kind"] == "power_source")["position"]
    source_pole = {"x": float(power[0]), "y": float(power[1] + 2)}
    middle_pole = {"x": 0.0, "y": -5.0}
    near_furnace_pole = {"x": 0.0, "y": 0.0}
    furnace_pole = {"x": 2.5 if variant == 0 else -2.5, "y": y + 3.0}
    plan = {"surface": scenario["environment"]["surface_name"], "force": scenario["environment"]["force_name"], "phases": [{"name": "furnace_refining", "actions": [
        {"action_type": "place_entity", "entity": "electric-furnace", "position": {"x": furnace_x, "y": y}, "direction": "east", "recipe": scenario["objective"]["item"]},
        {"action_type": "place_entity", "entity": "fast-inserter", "position": {"x": -2.0, "y": y}, "direction": "east"},
        {"action_type": "place_entity", "entity": "fast-inserter", "position": {"x": 2.0, "y": y}, "direction": "east"},
        {"action_type": "place_entity", "entity": "medium-electric-pole", "position": source_pole},
        {"action_type": "place_entity", "entity": "medium-electric-pole", "position": middle_pole},
        {"action_type": "place_entity", "entity": "medium-electric-pole", "position": near_furnace_pole},
        {"action_type": "place_entity", "entity": "medium-electric-pole", "position": furnace_pole},
    ]}]}
    validate_build_plan(plan)
    counts = {}
    for action in actions(plan): counts[action["entity"]] = counts.get(action["entity"], 0) + 1
    digest = plan_hash(plan)
    land = len(occupied_tile_indices([("candidate", plan)]))
    return {"action_id": f"furnace-direct-v{variant}-{digest[7:19]}", "plan_hash": digest, "features": {
        "predicted_completion": 1.0, "predicted_rate_per_tick": 1.2 / 60.0,
        "furnace_count": 1, "inserter_count": 2, "pole_count": counts["medium-electric-pole"],
        "material_cost": sum(_COST[k] * v for k, v in counts.items()), "occupied_land_tiles": land,
        "route_excess_tiles": 0, "route_efficiency": 1.0,
    }, "plan": plan}

def furnace_refining_candidates(scenario, *, validate_contract: bool = True) -> list[dict]:
    if validate_contract: validate_scenario(scenario)
    candidates = [_candidate(scenario, 0), _candidate(scenario, 1)]
    if candidates[0]["plan_hash"] == candidates[1]["plan_hash"]: raise ValueError("furnace alternatives must differ")
    return candidates
