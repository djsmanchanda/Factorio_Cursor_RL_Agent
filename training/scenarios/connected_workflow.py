# Path: training/scenarios/connected_workflow.py
# Purpose: Generate connected disposable episodes where iron, gears, belts and an independent construction task coexist.

from __future__ import annotations

import random

from training.canonical import scenario_hash
from training.contracts import validate_scenario

_VERSION = "1.2.0"
_BOUNDS = {"x_min": -32, "y_min": -32, "x_max_exclusive": 32, "y_max_exclusive": 32}
# One electric furnace smelts 0.625 plates/s; gears and belts share that supply,
# so the belt target stays well below the ~0.3 belts/s the chain can sustain.
_BELT_TARGET_PER_SECOND = 0.15
BUDGET = {
    "electric-furnace": 1,
    "assembling-machine-2": 2,
    "fast-inserter": 7,
    "iron-chest": 2,
    "small-electric-pole": 4,
    "medium-electric-pole": 4,
}


def generate_connected_workflow_scenario(seed: int) -> dict:
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 65535:
        raise ValueError("seed must be an integer from 0 through 65535")
    rng = random.Random(seed)
    origin_x, origin_y = rng.choice((-6, -3, 0, 3)), rng.choice((0, 3, 6))
    scenario = {
        "version": _VERSION,
        "scenario_id": f"connected-workflow-{seed:04x}",
        "family": "connected_workflow",
        "seed": seed,
        "curriculum": {"level": 1, "tags": ["connected-workflow", "iron-gear-belt", "independent-construction"]},
        "environment": {
            "surface_name": f"training/connected-workflow-{seed:04x}",
            "force_name": f"training-connected-workflow-{seed:04x}",
            "isolated_force": True, "autoplace_enabled": False, "bounds": dict(_BOUNDS),
        },
        # Decorative patch kept clear of the layout; ore arrives from the source chest.
        "resource_patch": {"resource": "iron-ore", "bounds": {"x1": 20, "y1": 20, "x2": 23, "y2": 23}, "amount_per_tile": 100_000},
        "fixtures": [
            {"id": "power-source", "kind": "power_source", "entity": "electric-energy-interface",
             "position": [origin_x + 1, origin_y - 2], "protected": True},
            {"id": "ore-source", "kind": "item_source", "entity": "infinity-chest",
             "position": [origin_x - 3, origin_y], "protected": True},
            {"id": "belt-sink", "kind": "item_sink", "entity": "infinity-chest",
             "position": [origin_x + 7, origin_y + 3], "protected": True},
        ],
        "construction_budget": dict(BUDGET),
        "objective": {
            "kind": "produce_item_rate", "input_item": "iron-ore", "item": "transport-belt",
            "target_rate_per_tick": _BELT_TARGET_PER_SECOND / 60.0,
            "sustain_ticks": 1_800, "destination_fixture_id": "belt-sink",
            "workflow_items": ["iron-plate", "iron-gear-wheel", "transport-belt"],
        },
        "constraints": {
            "max_episode_ticks": 36_000, "allow_fixture_deconstruction": False,
            "allowed_build_area": dict(_BOUNDS), "allowed_entities": sorted(BUDGET),
        },
        "reward_profile": "connected-workflow-v1",
        "reward_weights": {
            "completion": 10.0, "throughput": 8.0, "elapsed_tick": -0.0001,
            "material_item": -0.01, "failed_placement": -1.0, "pole": -0.1,
            "route_excess": -0.02, "land": -0.001, "unproductive_drill_capacity": -0.1,
        },
    }
    scenario["scenario_hash"] = scenario_hash(scenario)
    validate_scenario(scenario)
    return scenario


__all__ = ["BUDGET", "generate_connected_workflow_scenario"]
