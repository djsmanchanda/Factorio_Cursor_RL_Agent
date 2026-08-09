# Path: training/scenarios/mining_delivery.py
# Purpose: Generate seeded ore-mining and delivery tasks for the first curriculum.

from __future__ import annotations

import math
import random

from training.canonical import scenario_hash
from training.contracts import validate_scenario

_VERSION = "1.1.0"
_WORLD_BOUNDS = {
    "x_min": -64, "y_min": -64,
    "x_max_exclusive": 64, "y_max_exclusive": 64,
}
_RESOURCES = ("iron-ore", "copper-ore", "coal", "stone")
_TARGET_RATES_PER_TICK = tuple(rate / 60.0 for rate in (0.5, 1.0, 2.0, 3.0))
_PATCH_SIZES = (12, 15, 18)
_DRILL_RATE_PER_TICK = 0.5 / 60.0
_POLE_WIRE_STEP = 8.0


def _scenario_id(seed: int) -> str:
    return f"mining-delivery-{seed:08x}"


def _opposed_sites(rng: random.Random) -> tuple[tuple[int, int], tuple[float, float]]:
    """Place the patch and sink in opposing quadrants with bounded variation."""
    axis = rng.choice(("horizontal", "vertical"))
    sign = rng.choice((-1, 1))
    along = rng.randint(28, 42) * sign
    lateral = rng.randint(-20, 20)
    if axis == "horizontal":
        return (along, lateral), (-along + 0.5, -lateral + 0.5)
    return (lateral, along), (-lateral + 0.5, -along + 0.5)


def _patch_bounds(center: tuple[int, int], rng: random.Random) -> dict[str, int]:
    width = rng.choice(_PATCH_SIZES)
    height = rng.choice(_PATCH_SIZES)
    x1 = center[0] - width // 2
    y1 = center[1] - height // 2
    return {"x1": x1, "y1": y1, "x2": x1 + width - 1, "y2": y1 + height - 1}


def _route_span(patch: dict[str, int], destination: tuple[float, float]) -> int:
    patch_center = ((patch["x1"] + patch["x2"]) / 2, (patch["y1"] + patch["y2"]) / 2)
    return math.ceil(
        abs(patch_center[0] - destination[0])
        + abs(patch_center[1] - destination[1])
    )


def _construction_budget(target_rate_per_tick: float, route_span: int) -> dict[str, int]:
    drills = math.ceil(target_rate_per_tick / _DRILL_RATE_PER_TICK) + 1
    poles = math.ceil(route_span / _POLE_WIRE_STEP) + 4
    return {
        "electric-mining-drill": drills,
        "fast-inserter": 4,
        "medium-electric-pole": poles,
        "splitter": 2,
        "transport-belt": route_span + 32,
        "underground-belt": max(4, route_span // 16 * 2),
    }


def generate_mining_delivery_scenario(seed: int) -> dict:
    """Return one deterministic, schema-validated mining training episode."""
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 2_147_483_647:
        raise ValueError("seed must be an integer from 0 through 2147483647")
    rng = random.Random(seed)
    identifier = _scenario_id(seed)
    resource = rng.choice(_RESOURCES)
    target_rate_per_tick = rng.choice(_TARGET_RATES_PER_TICK)
    patch_center, destination = _opposed_sites(rng)
    patch = _patch_bounds(patch_center, rng)
    route_span = _route_span(patch, destination)
    budget = _construction_budget(target_rate_per_tick, route_span)
    scenario = {
        "version": _VERSION,
        "scenario_id": identifier,
        "family": "mining_delivery",
        "seed": seed,
        "curriculum": {
            "level": _TARGET_RATES_PER_TICK.index(target_rate_per_tick) + 1,
            "tags": ["direct-delivery", "electric-only", "mining", resource],
        },
        "environment": {
            "surface_name": f"training/{identifier}",
            "force_name": f"training-{identifier}",
            "isolated_force": True,
            "autoplace_enabled": False,
            "bounds": dict(_WORLD_BOUNDS),
        },
        "resource_patch": {
            "resource": resource,
            "bounds": patch,
            "amount_per_tile": 100_000,
        },
        "fixtures": [
            {
                "id": "power-source", "kind": "power_source",
                "entity": "electric-energy-interface", "position": [0, 0],
                "protected": True,
            },
            {
                "id": "delivery-sink", "kind": "item_sink",
                "entity": "infinity-chest", "position": list(destination),
                "protected": True,
            },
        ],
        "construction_budget": budget,
        "objective": {
            "kind": "deliver_item_rate",
            "item": resource,
            "target_rate_per_tick": target_rate_per_tick,
            "sustain_ticks": 1_800,
            "destination_fixture_id": "delivery-sink",
        },
        "constraints": {
            "max_episode_ticks": 21_600,
            "allow_fixture_deconstruction": False,
            "allowed_build_area": dict(_WORLD_BOUNDS),
            "allowed_entities": sorted(budget),
        },
        "reward_weights": {
            "completion": 10.0,
            "delivered_item": 0.01,
            "elapsed_tick": -0.0001,
            "material_item": -0.01,
            "failed_placement": -1.0,
            "extra_pole": -0.1,
        },
    }
    scenario["scenario_hash"] = scenario_hash(scenario)
    validate_scenario(scenario)
    return scenario


def generate_mining_delivery_curriculum(count: int, start_seed: int = 0) -> list[dict]:
    """Return `count` sequential, unique scenarios for batched training."""
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("count must be a positive integer")
    final_seed = start_seed + count - 1
    if start_seed < 0 or final_seed > 2_147_483_647:
        raise ValueError("requested curriculum seeds exceed the supported range")
    return [generate_mining_delivery_scenario(seed) for seed in range(start_seed, final_seed + 1)]
