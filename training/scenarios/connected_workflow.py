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
# Level 3: both iron furnaces feed a shared store, then separate plate taps
# serve gears/belts and circuits. Targets straddle the nominal one-furnace
# belt share (~0.21/s versus ~0.42/s with two), before transport losses.
# These are curriculum bounds, not live-verified capacity claims: run a
# stepped capacity sweep before enabling this revised level in rotation.
_CIRCUIT_TARGET_RANGE_LEVEL3 = (0.02, 0.18)
_BELT_TARGET_RANGE_LEVEL3 = (0.06, 0.35)
BUDGET_LEVEL3 = {
    **BUDGET_LEVEL2,
    "electric-furnace": BUDGET_LEVEL2["electric-furnace"] + 1,
    "assembling-machine-2": 4,
    "assembling-machine-1": 4,
    "fast-inserter": BUDGET_LEVEL2["fast-inserter"] + 9,
    "inserter": BUDGET_LEVEL2["inserter"] + 9,
    "iron-chest": BUDGET_LEVEL2["iron-chest"] + 4,
    # Includes independent power reach for the copper-first choice.
    "small-electric-pole": BUDGET_LEVEL2["small-electric-pole"] + 9,
}
_REWARD_WEIGHTS = {
    "completion": 10.0, "throughput": 8.0, "elapsed_tick": -0.0001,
    "material_item": -0.01, "failed_placement": -1.0, "pole": -0.1,
    "route_excess": -0.02, "land": -0.001, "unproductive_drill_capacity": -0.1,
}


def generate_connected_workflow_scenario(seed: int, *, level: int = 1) -> dict:
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 65535:
        raise ValueError("seed must be an integer from 0 through 65535")
    if level not in (1, 2, 3):
        raise ValueError("level must be 1, 2, or 3")
    rng = random.Random(seed)
    origin_x, origin_y = rng.choice((-6, -3, 0, 3)), rng.choice((0, 3, 6))
    objective_extra: dict = {}
    if level == 1:
        # Unchanged from the original single-level generator: existing level-1
        # seeds must keep producing byte-identical scenarios (same hash).
        belt_target = _BELT_TARGET_PER_SECOND
        budget = dict(BUDGET)
        tags = ["connected-workflow", "iron-gear-belt", "independent-construction"]
        slug = f"connected-workflow-{seed:04x}"
        extra_fixtures: list[dict] = []
        workflow_items = ["iron-plate", "iron-gear-wheel", "transport-belt"]
    elif level == 2:
        belt_target = round(rng.uniform(*_BELT_TARGET_RANGE_LEVEL2), 3)
        budget = dict(BUDGET_LEVEL2)
        tags = ["connected-workflow", "iron-gear-belt", "independent-construction", "demand-scaling"]
        slug = f"connected-workflow-l2-{seed:04x}"
        extra_fixtures = [
            {"id": "ore-source-2", "kind": "item_source", "entity": "infinity-chest",
             "position": [origin_x + 4, origin_y - 10], "protected": True},
        ]
        workflow_items = ["iron-plate", "iron-gear-wheel", "transport-belt"]
    else:
        belt_target = round(rng.uniform(*_BELT_TARGET_RANGE_LEVEL3), 3)
        circuit_target = round(rng.uniform(*_CIRCUIT_TARGET_RANGE_LEVEL3), 3)
        budget = dict(BUDGET_LEVEL3)
        tags = ["connected-workflow", "iron-gear-belt", "independent-construction",
                "demand-scaling", "second-product", "shared-plate-supply-v2", "joint-sustain-v2"]
        slug = f"connected-workflow-l3-v2-{seed:04x}"
        extra_fixtures = [
            {"id": "ore-source-2", "kind": "item_source", "entity": "infinity-chest",
             "position": [origin_x + 6, origin_y], "protected": True},
            {"id": "ore-source-copper", "kind": "item_source", "entity": "infinity-chest",
             "fill_item": "copper-ore", "position": [origin_x - 15, origin_y + 6], "protected": True},
            {"id": "circuit-sink", "kind": "item_sink", "entity": "infinity-chest",
             "position": [origin_x, origin_y + 8], "protected": True},
        ]
        workflow_items = [
            "iron-plate", "iron-gear-wheel", "transport-belt",
            "copper-ore", "copper-plate", "copper-cable", "electronic-circuit",
        ]
        objective_extra = {
            "requirements": [{
                "id": "circuits", "item": "electronic-circuit",
                "target_rate_per_tick": circuit_target / 60.0,
                "sustain_ticks": 1_800, "destination_fixture_id": "circuit-sink",
            }],
        }
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
             "position": [origin_x + (2 if level == 3 else 1), origin_y - 2], "protected": True},
            {"id": "ore-source", "kind": "item_source", "entity": "infinity-chest",
             "position": [origin_x - (6 if level == 3 else 3), origin_y], "protected": True},
            {"id": "belt-sink", "kind": "item_sink", "entity": "infinity-chest",
             "position": [origin_x - 6, origin_y - 3] if level == 3 else [origin_x + 7, origin_y + 3], "protected": True},
            *extra_fixtures,
        ],
        "construction_budget": budget,
        "objective": {
            "kind": "produce_item_rate", "input_item": "iron-ore", "item": "transport-belt",
            "target_rate_per_tick": belt_target / 60.0,
            "sustain_ticks": 1_800, "destination_fixture_id": "belt-sink",
            "workflow_items": workflow_items,
            **objective_extra,
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


__all__ = ["BUDGET", "BUDGET_LEVEL2", "BUDGET_LEVEL3", "generate_connected_workflow_scenario"]
