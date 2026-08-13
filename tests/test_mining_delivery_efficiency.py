# Path: tests/test_mining_delivery_efficiency.py
# Purpose: Protect mining route, infrastructure, land, and cost feature accounting.

from __future__ import annotations

from copy import deepcopy

import pytest

from planners.plan_validation import actions, validate_build_plan
from training.candidates.mining_delivery import (
    MATERIAL_COST_BY_ENTITY,
    mining_delivery_candidates,
    orthogonal_route_lower_bound,
)
from training.canonical import scenario_hash
from training.scenarios.mining_delivery import (
    generate_mining_delivery_scenario,
    generate_staged_mining_delivery_scenario,
)


def _entity_counts(plan: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in actions(plan):
        entity = action["entity"]
        counts[entity] = counts.get(entity, 0) + 1
    return counts


def test_orthogonal_route_lower_bound_is_manhattan_distance() -> None:
    assert orthogonal_route_lower_bound((0, 0), (20, 50)) == 70
    assert orthogonal_route_lower_bound((20, 50), (0, 0)) == 70


def test_candidate_efficiency_features_come_from_the_generated_plan() -> None:
    scenario = generate_mining_delivery_scenario(42)

    for candidate in mining_delivery_candidates(scenario):
        plan = candidate["plan"]
        features = candidate["features"]
        counts = _entity_counts(plan)
        validate_build_plan(plan)

        assert features["route_tiles"] == counts["transport-belt"]
        assert features["route_tiles"] == (
            features["collection_belt_tiles"]
            + features["actual_delivery_route_tiles"]
        )
        assert features["collection_belt_tiles"] > 0
        assert features["actual_delivery_route_tiles"] > 0
        assert features["shortest_delivery_route_tiles"] >= 0
        assert features["route_excess_tiles"] == max(
            0,
            features["actual_delivery_route_tiles"]
            - features["shortest_delivery_route_tiles"],
        )
        assert 0.0 <= features["route_efficiency"] <= 1.0
        assert features["pole_count"] == counts["medium-electric-pole"]
        assert features["drill_count"] == counts["electric-mining-drill"]
        assert features["occupied_land_tiles"] >= sum(counts.values())
        assert features["material_cost"] == sum(
            MATERIAL_COST_BY_ENTITY[entity] * count
            for entity, count in counts.items()
        )


def test_candidate_efficiency_catalog_is_stable_and_budget_bounded() -> None:
    scenario = generate_mining_delivery_scenario(99)
    assert mining_delivery_candidates(scenario) == mining_delivery_candidates(scenario)

    constrained = deepcopy(scenario)
    constrained["construction_budget"]["transport-belt"] = 0
    constrained["scenario_hash"] = scenario_hash(constrained)
    with pytest.raises(ValueError, match="exceeds construction budget"):
        mining_delivery_candidates(constrained)


def test_staged_final_candidates_supply_two_independent_thirty_per_second_sinks() -> None:
    scenario = generate_staged_mining_delivery_scenario(321, sustain_ticks=600)
    sink_ids = ("delivery-sink-a", "delivery-sink-b")

    candidates = mining_delivery_candidates(
        scenario,
        target_rate_per_tick=60 / 60,
        sink_fixture_ids=sink_ids,
    )

    sink_positions = {
        fixture["id"]: tuple(fixture["position"])
        for fixture in scenario["fixtures"]
        if fixture["id"] in sink_ids
    }
    for candidate in candidates:
        plan_actions = actions(candidate["plan"])
        counts = _entity_counts(candidate["plan"])
        transfer_positions = [
            tuple(action["position"].values())
            for action in plan_actions
            if action["entity"] == "express-loader"
        ]
        assert candidate["features"]["sink_count"] == 2
        assert candidate["features"]["drill_count"] == 120
        assert counts["electric-mining-drill"] == 120
        assert counts["express-loader"] == 2
        loaders = [action for action in plan_actions if action["entity"] == "express-loader"]
        for loader in loaders:
            lx, ly = loader["position"].values()
            sx, sy = min(sink_positions.values(), key=lambda sink: abs(lx - sink[0]) + abs(ly - sink[1]))
            expected_direction = "east" if sx > lx else "west" if sx < lx else "south" if sy > ly else "north"
            assert loader["direction"] == expected_direction
            assert loader["underground_type"] == "input"
        assert counts["express-transport-belt"] > 0
        assert all(
            any(abs(ix - sx) + abs(iy - sy) == 1.5 for ix, iy in transfer_positions)
            for sx, sy in sink_positions.values()
        )
