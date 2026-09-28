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
    # Either tier per branch; the budget admits any mix of variants.
    "assembling-machine-2": 2,
    "assembling-machine-1": 2,
    "fast-inserter": 7,
    "inserter": 7,
    "iron-chest": 2,
    "small-electric-pole": 4,
    "medium-electric-pole": 4,
}
# Level 2 (demand-scaling): a randomized belt target that sometimes exceeds
# what one furnace can sustain and sometimes does not, so building the iron2
# expansion branch (training/candidates/connected_workflow.py) is a real Laya
# choice rather than always useful. Measured live on 2.1.20 (scale-smoke, worker
# 05): one furnace sustains a completed belt objective up to at least ~0.53/s
# but not 0.605/s (plate/gear/belt plateau at ~0.6/0.2/0.4 per second); a second
# furnace lifts plate/gear to ~1.2-1.3/0.4 per second and belts comfortably past
# 0.605/s. The exact one-furnace ceiling was not pinned to the tile; this range
# straddles it either way, which is all the curriculum needs.
_BELT_TARGET_RANGE_LEVEL2 = (0.08, 0.70)
# Adds the iron2 expansion cell (a second furnace feeding the gear assembler
# directly) and its own ore-source fixture; no other branch needs a bigger cut.
BUDGET_LEVEL2 = {
    **BUDGET,
    "electric-furnace": BUDGET["electric-furnace"] + 1,
    "fast-inserter": BUDGET["fast-inserter"] + 2,
    "inserter": BUDGET["inserter"] + 2,
    "small-electric-pole": BUDGET["small-electric-pole"] + 2,
}
_REWARD_WEIGHTS = {
    "completion": 10.0, "throughput": 8.0, "elapsed_tick": -0.0001,
    "material_item": -0.01, "failed_placement": -1.0, "pole": -0.1,
    "route_excess": -0.02, "land": -0.001, "unproductive_drill_capacity": -0.1,
}


def generate_connected_workflow_scenario(seed: int, *, level: int = 1) -> dict:
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 65535:
        raise ValueError("seed must be an integer from 0 through 65535")
    if level not in (1, 2):
        raise ValueError("level must be 1 or 2")
    rng = random.Random(seed)
    origin_x, origin_y = rng.choice((-6, -3, 0, 3)), rng.choice((0, 3, 6))
    if level == 1:
        # Unchanged from the original single-level generator: existing level-1
        # seeds must keep producing byte-identical scenarios (same hash).
        belt_target = _BELT_TARGET_PER_SECOND
        budget = dict(BUDGET)
        tags = ["connected-workflow", "iron-gear-belt", "independent-construction"]
        slug = f"connected-workflow-{seed:04x}"
        extra_fixtures: list[dict] = []
    else:
        belt_target = round(rng.uniform(*_BELT_TARGET_RANGE_LEVEL2), 3)
        budget = dict(BUDGET_LEVEL2)
        tags = ["connected-workflow", "iron-gear-belt", "independent-construction", "demand-scaling"]
        slug = f"connected-workflow-l2-{seed:04x}"
        extra_fixtures = [
            {"id": "ore-source-2", "kind": "item_source", "entity": "infinity-chest",
             "position": [origin_x + 4, origin_y - 10], "protected": True},
        ]
    scenario = {
        "version": _VERSION,
        "scenario_id": slug,
        "family": "connected_workflow",
        "seed": seed,
        "curriculum": {"level": level, "tags": tags},
        "environment": {
            "surface_name": f"training/{slug}",
            "force_name": f"training-{slug}",
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
            *extra_fixtures,
        ],
        "construction_budget": budget,
        "objective": {
            "kind": "produce_item_rate", "input_item": "iron-ore", "item": "transport-belt",
            "target_rate_per_tick": belt_target / 60.0,
            "sustain_ticks": 1_800, "destination_fixture_id": "belt-sink",
            "workflow_items": ["iron-plate", "iron-gear-wheel", "transport-belt"],
        },
        "constraints": {
            "max_episode_ticks": 36_000, "allow_fixture_deconstruction": False,
            "allowed_build_area": dict(_BOUNDS), "allowed_entities": sorted(budget),
        },
        "reward_profile": "connected-workflow-v1",
        "reward_weights": dict(_REWARD_WEIGHTS),
    }
    scenario["scenario_hash"] = scenario_hash(scenario)
    validate_scenario(scenario)
    return scenario


__all__ = ["BUDGET", "BUDGET_LEVEL2", "generate_connected_workflow_scenario"]
