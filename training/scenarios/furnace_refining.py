# Path: training/scenarios/furnace_refining.py
# Purpose: Generate isolated ore-to-furnace refinement tasks for alternating RL cohorts.

from __future__ import annotations

import random
from training.canonical import scenario_hash
from training.contracts import validate_scenario

_ORES = ("iron-ore", "copper-ore")
_OUTPUTS = {"iron-ore": "iron-plate", "copper-ore": "copper-plate"}
_VERSION = "1.2.0"
_BOUNDS = {"x_min": -32, "y_min": -32, "x_max_exclusive": 32, "y_max_exclusive": 32}


def generate_furnace_refining_scenario(seed: int, target_rate_per_second: float = 1.0) -> dict:
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 65535:
        raise ValueError("seed must be an integer from 0 through 65535")
    if target_rate_per_second not in (0.5, 1.0, 2.0):
        raise ValueError("target rate must be one of (0.5, 1.0, 2.0)")
    rng = random.Random(seed)
    ore = rng.choice(_ORES)
    output = _OUTPUTS[ore]
    source = [-3, rng.choice((-2, 2))]
    sink = [3, source[1]]
    power = [rng.choice((-2, 2)), -10]
    patch = {"x1": -4, "y1": -16, "x2": 3, "y2": -13}
    budget = {
        "electric-furnace": 1,
        "fast-inserter": 2,
        "medium-electric-pole": 4,
    }
    scenario = {
        "version": _VERSION,
        "scenario_id": f"furnace-refining-{seed:04x}",
        "family": "furnace_refining",
        "seed": seed,
        "curriculum": {"level": 1, "tags": ["furnace-refining", "supplied-ore", ore]},
        "environment": {
            "surface_name": f"training/furnace-refining-{seed:04x}",
            "force_name": f"training-furnace-refining-{seed:04x}",
            "isolated_force": True, "autoplace_enabled": False, "bounds": dict(_BOUNDS),
        },
        "resource_patch": {"resource": ore, "bounds": patch, "amount_per_tile": 100_000},
        "fixtures": [
            {"id": "power-source", "kind": "power_source", "entity": "electric-energy-interface", "position": power, "protected": True},
            {"id": "ore-source", "kind": "item_source", "entity": "infinity-chest", "position": source, "protected": True},
            {"id": "plate-sink", "kind": "item_sink", "entity": "infinity-chest", "position": sink, "protected": True},
        ],
        "construction_budget": budget,
        "objective": {
            "kind": "smelt_item_rate", "input_item": ore, "item": output,
            "target_rate_per_tick": target_rate_per_second / 60.0,
            "sustain_ticks": 1_800, "destination_fixture_id": "plate-sink",
        },
        "constraints": {
            "max_episode_ticks": 21_600, "allow_fixture_deconstruction": False,
            "allowed_build_area": dict(_BOUNDS), "allowed_entities": sorted(budget),
        },
        "reward_profile": "furnace-efficiency-v1",
        "reward_weights": {
            "completion": 10.0, "throughput": 8.0, "elapsed_tick": -0.0001,
            "material_item": -0.01, "failed_placement": -1.0, "pole": -0.1,
            "route_excess": -0.02, "land": -0.001, "unproductive_drill_capacity": -0.1,
        },
    }
    scenario["scenario_hash"] = scenario_hash(scenario)
    validate_scenario(scenario)
    return scenario


def generate_furnace_refining_curriculum(count: int, start_seed: int = 0) -> list[dict]:
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("count must be positive")
    if start_seed < 0 or start_seed + count - 1 > 65535:
        raise ValueError("requested seeds exceed supported range")
    return [generate_furnace_refining_scenario(seed) for seed in range(start_seed, start_seed + count)]
