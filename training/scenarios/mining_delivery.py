# Path: training/scenarios/mining_delivery.py
# Purpose: Generate seeded ore-mining and delivery tasks for the first curriculum.

from __future__ import annotations

import math
import random

from training.canonical import scenario_hash
from training.contracts import validate_scenario

_VERSION = "1.2.0"
_REWARD_PROFILE = "mining-throughput-cost-v1"
_WORLD_BOUNDS = {
    "x_min": -64, "y_min": -64,
    "x_max_exclusive": 64, "y_max_exclusive": 64,
}
_RESOURCES = ("iron-ore", "copper-ore", "coal", "stone")
_DEFAULT_TARGET_RATES_PER_SECOND = (0.5, 1.0, 2.0, 3.0)
_MAX_TARGET_RATES_PER_SECOND = (0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 30.0, 60.0)
_TARGET_RATES_PER_TICK = tuple(rate / 60.0 for rate in _DEFAULT_TARGET_RATES_PER_SECOND)
_PATCH_SIZES = (12, 15, 18)
_DRILL_RATE_PER_TICK = 0.5 / 60.0
_POLE_WIRE_STEP = 8.0
_MAX_SCENARIO_SEED = 0xFFFF


def _scenario_id(seed: int) -> str:
    return f"mining-delivery-{seed:04x}"


def _opposed_sites(rng: random.Random) -> tuple[tuple[int, int], tuple[float, float]]:
    """Place the patch and sink in opposing quadrants with bounded variation."""
    axis = rng.choice(("horizontal", "vertical"))
    sign = rng.choice((-1, 1))
    along = rng.randint(28, 42) * sign
    lateral = rng.randint(-20, 20)
    if axis == "horizontal":
        return (along, lateral), (-along + 0.5, -lateral + 0.5)
    return (lateral, along), (-lateral + 0.5, -along + 0.5)


def _patch_bounds(
    center: tuple[int, int], rng: random.Random, *, required_drills: int,
) -> dict[str, int]:
    """Size a patch for both its sampled geometry and advertised drill demand."""
    # Each collection column holds a north/south pair of 3x3 drills. Reserve
    # one additional drill so the two policy alternatives are both feasible.
    columns = math.ceil((required_drills + 1) / 2)
    max_width = _WORLD_BOUNDS["x_max_exclusive"] - _WORLD_BOUNDS["x_min"] - 2
    width = min(max_width, max(rng.choice(_PATCH_SIZES), columns * 3))
    height = rng.choice(_PATCH_SIZES)
    x1 = min(
        _WORLD_BOUNDS["x_max_exclusive"] - width - 1,
        max(_WORLD_BOUNDS["x_min"] + 1, center[0] - width // 2),
    )
    y1 = center[1] - height // 2
    return {"x1": x1, "y1": y1, "x2": x1 + width - 1, "y2": y1 + height - 1}


def _route_span(patch: dict[str, int], destination: tuple[float, float]) -> int:
    patch_center = ((patch["x1"] + patch["x2"]) / 2, (patch["y1"] + patch["y2"]) / 2)
    return math.ceil(
        abs(patch_center[0] - destination[0])
        + abs(patch_center[1] - destination[1])
    )


def _power_source_position(
    rng: random.Random, patch: dict[str, int], destination: tuple[float, float],
) -> tuple[int, int]:
    """Sample a clear, in-bounds generator site near the mining patch."""
    center_x = (patch["x1"] + patch["x2"]) // 2
    center_y = (patch["y1"] + patch["y2"]) // 2
    candidates: list[tuple[int, int]] = []
    for gap in (7, 10, 13):
        for lateral in (-6, 0, 6):
            candidates.extend((
                (patch["x1"] - gap, center_y + lateral),
                (patch["x2"] + gap, center_y + lateral),
                (center_x + lateral, patch["y1"] - gap),
                (center_x + lateral, patch["y2"] + gap),
            ))
    rng.shuffle(candidates)
    for x, y in candidates:
        source_inside = (
            _WORLD_BOUNDS["x_min"] + 1 <= x < _WORLD_BOUNDS["x_max_exclusive"] - 1
            and _WORLD_BOUNDS["y_min"] + 1 <= y < _WORLD_BOUNDS["y_max_exclusive"] - 1
        )
        clear_of_patch = (
            x + 1 < patch["x1"] or x - 1 > patch["x2"]
            or y + 1 < patch["y1"] or y - 1 > patch["y2"]
        )
        if source_inside and clear_of_patch and math.dist((x, y), destination) >= 4:
            return x, y
    raise ValueError("could not place an in-bounds power source away from the mining patch")


def _construction_budget(
    target_rate_per_tick: float, route_span: int, source: tuple[int, int], patch: dict[str, int],
) -> dict[str, int]:
    drills = math.ceil(target_rate_per_tick / _DRILL_RATE_PER_TICK) + 1
    patch_center = ((patch["x1"] + patch["x2"]) / 2, (patch["y1"] + patch["y2"]) / 2)
    source_span = math.dist(source, patch_center)
    # The source and remote delivery inserter both need continuous pole coverage.
    patch_width = patch["x2"] - patch["x1"] + 1
    # The compiled network branches along the full collection row as well as
    # crossing the source-to-sink span. Reserve that branch explicitly rather
    # than advertising a scenario whose legal candidate cannot be funded.
    poles = (
        math.ceil((source_span + route_span + patch_width) / _POLE_WIRE_STEP)
        + math.ceil(patch_width / 6.0) + 15
    )
    budget = {
        "electric-mining-drill": drills,
        "fast-inserter": 4,
        "medium-electric-pole": poles,
        "splitter": 2,
        # Reserve a bounded detour allowance in addition to the collection row
        # and centre-to-sink span; protected fixtures can displace the bridge.
        "transport-belt": route_span + patch_width + 64,
        "underground-belt": max(4, route_span // 16 * 2),
    }
    # One yellow belt cannot honestly predict a 30/s target. These items are
    # provided only in the explicitly high-throughput profile; 60/s remains a
    # two-sink staged objective because a single express delivery lane is 45/s.
    if target_rate_per_tick > 15.0 / 60.0:
        budget.update({
            "express-transport-belt": route_span + patch_width + 64,
            "express-underground-belt": max(4, route_span // 10 * 2),
            "express-loader": 1,
        })
    return budget


def generate_mining_delivery_scenario(
    seed: int, target_rate_per_second: float | None = None,
) -> dict:
    """Return one deterministic, schema-validated mining training episode."""
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= _MAX_SCENARIO_SEED:
        raise ValueError("seed must be an integer from 0 through 65535")
    rng = random.Random(seed)
    identifier = _scenario_id(seed)
    resource = rng.choice(_RESOURCES)
    if target_rate_per_second is None:
        target_rate_per_second = rng.choice(_DEFAULT_TARGET_RATES_PER_SECOND)
    if target_rate_per_second not in _MAX_TARGET_RATES_PER_SECOND:
        raise ValueError(f"target rate must be one of {_MAX_TARGET_RATES_PER_SECOND}")
    target_rate_per_tick = target_rate_per_second / 60.0
    patch_center, destination = _opposed_sites(rng)
    required_drills = math.ceil(target_rate_per_tick / _DRILL_RATE_PER_TICK)
    patch = _patch_bounds(patch_center, rng, required_drills=required_drills)
    power_source = _power_source_position(rng, patch, destination)
    route_span = _route_span(patch, destination)
    budget = _construction_budget(target_rate_per_tick, route_span, power_source, patch)
    scenario = {
        "version": _VERSION,
        "scenario_id": identifier,
        "family": "mining_delivery",
        "seed": seed,
        "curriculum": {
            "level": _MAX_TARGET_RATES_PER_SECOND.index(target_rate_per_second) + 1,
            "tags": ["direct-delivery", "distributed-power-source", "electric-only", "mining", resource, f"demand-{target_rate_per_second:g}-per-second"],
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
                "entity": "electric-energy-interface", "position": list(power_source),
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
        "reward_profile": _REWARD_PROFILE,
        "reward_weights": {
            "completion": 20.0,
            "throughput": 100.0,
            "elapsed_tick": -8.0,
            "material_item": -8.0,
            "failed_placement": -1.0,
            "pole": -0.02,
            "route_excess": -0.02,
            "land": -0.0002,
            "unproductive_drill_capacity": -3.0,
        },
    }
    scenario["scenario_hash"] = scenario_hash(scenario)
    validate_scenario(scenario)
    return scenario


def generate_staged_mining_delivery_scenario(
    seed: int,
    target_rates_per_second: tuple[float, ...] = (10.0, 30.0, 60.0),
    *,
    sustain_ticks: int = 1_800,
) -> dict:
    """Generate one persistent factory episode with an ordered demand ladder.

    The training lab keeps this factory alive while it advances
    ``objective.stages`` after each sustained target. The final stage names two
    sinks, so a policy must scale and route an existing line instead of getting
    a fresh layout for every demand.
    """
    if not isinstance(target_rates_per_second, tuple) or not target_rates_per_second:
        raise ValueError("target_rates_per_second must be a non-empty tuple")
    if any(rate <= 0 or rate not in _MAX_TARGET_RATES_PER_SECOND for rate in target_rates_per_second):
        raise ValueError(f"staged rates must be selected from {_MAX_TARGET_RATES_PER_SECOND}")
    if tuple(sorted(target_rates_per_second)) != target_rates_per_second:
        raise ValueError("staged rates must be ordered from lower to higher demand")
    if sustain_ticks < 60:
        raise ValueError("sustain_ticks must be at least 60")
    scenario = generate_mining_delivery_scenario(seed, max(target_rates_per_second))
    scenario = {**scenario, "scenario_id": f"mining-staged-{seed:04x}"}
    scenario["environment"] = {
        **scenario["environment"],
        "surface_name": f"training/{scenario['scenario_id']}",
        "force_name": f"training-{scenario['scenario_id']}",
    }
    # Give staged episodes a larger disposable arena so 10/s and 30/s can be
    # attempted with one coherent two-row collection corridor.
    staged_bounds = {**scenario["environment"]["bounds"], "x_min": -128, "x_max_exclusive": 128}
    scenario["environment"] = {**scenario["environment"], "bounds": staged_bounds}
    scenario["constraints"] = {**scenario["constraints"], "allowed_build_area": staged_bounds}
    # Reserve two stable mining corridors so a final 60/s stage can feed two
    # sinks without replacing the established line to sink A. Lower final
    # targets deliberately use one sink: splitting 30/s into two 15/s targets
    # would change the task the policy is meant to learn.
    patch = dict(scenario["resource_patch"]["bounds"])
    center_x = (patch["x1"] + patch["x2"]) // 2
    center_y = (patch["y1"] + patch["y2"]) // 2
    patch["x1"], patch["x2"] = center_x - 45, center_x + 44
    patch["y1"], patch["y2"] = center_y - 12, center_y + 11
    scenario["resource_patch"] = {**scenario["resource_patch"], "bounds": patch}
    scenario["construction_budget"] = {
        **scenario["construction_budget"],
        "express-transport-belt": scenario["construction_budget"]["transport-belt"] + 300,
        "express-underground-belt": scenario["construction_budget"]["underground-belt"] + 20,
        "express-loader": 2,
        "medium-electric-pole": scenario["construction_budget"]["medium-electric-pole"] + 200,
    }
    scenario["constraints"] = {
        **scenario["constraints"],
        "allowed_entities": sorted(scenario["construction_budget"]),
    }
    destination = next(fixture for fixture in scenario["fixtures"] if fixture["kind"] == "item_sink")
    original_sink_x = float(destination["position"][0])
    left_sink_x = patch["x1"] - 20.5
    right_sink_x = patch["x2"] + 20.5
    if original_sink_x < center_x:
        first_sink_x, second_sink_x = left_sink_x, right_sink_x
    else:
        first_sink_x, second_sink_x = right_sink_x, left_sink_x
    first_sink_position = [first_sink_x, math.floor(patch["y1"] + 14) + 0.5]
    second_sink_position = [second_sink_x, math.floor(patch["y1"] + 18) + 0.5]
    source_position = [center_x, patch["y1"] - 5]
    dual_sink_final = target_rates_per_second[-1] >= 60.0
    scenario["fixtures"] = [
        {**fixture, "position": source_position}
        if fixture["kind"] == "power_source" else fixture
        for fixture in scenario["fixtures"]
        if fixture["id"] != "delivery-sink"
    ] + [{**destination, "id": "delivery-sink-a", "position": first_sink_position}]
    if dual_sink_final:
        scenario["fixtures"].append(
            {**destination, "id": "delivery-sink-b", "position": second_sink_position},
        )
    # A wide protected wall field blocks both shortest L-shaped approaches.
    # Express underground belts cannot span its ten-tile thickness, so the
    # policy must choose a legal multi-turn route around it.
    if first_sink_x > center_x:
        obstacle_x1, obstacle_x2 = patch["x2"] + 5, patch["x2"] + 14
    else:
        obstacle_x1, obstacle_x2 = patch["x1"] - 14, patch["x1"] - 5
    first_belt_y = patch["y1"] + 6
    scenario["obstacles"] = [{
        "id": "delivery-wall-a",
        "entity": "stone-wall",
        "bounds": {
            "x1": obstacle_x1, "y1": first_belt_y - 2,
            "x2": obstacle_x2, "y2": math.floor(first_sink_position[1]) + 2,
        },
        "protected": True,
    }]
    stages = []
    for index, rate in enumerate(target_rates_per_second):
        destinations = ["delivery-sink-a"]
        if dual_sink_final and index == len(target_rates_per_second) - 1:
            destinations.append("delivery-sink-b")
        stages.append({
            "id": f"demand-{int(rate):02d}",
            "target_rate_per_tick": rate / 60.0,
            "sustain_ticks": sustain_ticks,
            "destination_fixture_ids": destinations,
        })
    first = stages[0]
    scenario["objective"] = {
        **scenario["objective"],
        "target_rate_per_tick": first["target_rate_per_tick"],
        "sustain_ticks": first["sustain_ticks"],
        "destination_fixture_id": "delivery-sink-a",
        "stages": stages,
    }
    scenario["curriculum"] = {
        **scenario["curriculum"],
        "level": min(10, 7 + len(stages)),
        "tags": sorted(set(scenario["curriculum"]["tags"]) | {
            "staged-demand", "in-place-upgrade", "obstacle-detour",
            *[f"demand-{rate:g}-per-second" for rate in target_rates_per_second],
        }),
    }
    if dual_sink_final:
        scenario["curriculum"]["tags"].append("dual-sink-final")
    scenario["constraints"] = {
        **scenario["constraints"],
        "max_episode_ticks": max(int(scenario["constraints"]["max_episode_ticks"]), sustain_ticks * len(stages) + 7_200),
    }
    scenario["scenario_hash"] = scenario_hash(scenario)
    validate_scenario(scenario)
    return scenario

def generate_mining_delivery_curriculum(
    count: int, start_seed: int = 0,
    target_rates_per_second: tuple[float, ...] | None = None,
) -> list[dict]:
    """Return `count` sequential, unique scenarios for batched training."""
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("count must be a positive integer")
    final_seed = start_seed + count - 1
    if start_seed < 0 or final_seed > _MAX_SCENARIO_SEED:
        raise ValueError("requested curriculum seeds exceed the supported range")
    rates = target_rates_per_second or ()
    if rates and any(rate not in _MAX_TARGET_RATES_PER_SECOND for rate in rates):
        raise ValueError(f"target rates must be selected from {_MAX_TARGET_RATES_PER_SECOND}")
    seeds = list(range(start_seed, final_seed + 1))
    if not rates:
        return [generate_mining_delivery_scenario(seed) for seed in seeds]
    # A supplied rate list is a curriculum progression, not a round-robin
    # sampler: finish the lower-demand phase before exposing the next one.
    phase_for_index = lambda index: min(len(rates) - 1, index * len(rates) // len(seeds))
    return [
        generate_mining_delivery_scenario(seed, rates[phase_for_index(index)])
        for index, seed in enumerate(seeds)
    ]
