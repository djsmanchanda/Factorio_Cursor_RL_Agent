# Path: tests/test_bootstrap_smelting.py
# Purpose: Verify the plate bootstrap has no circular belt dependency.

import pytest

from planners.bootstrap_smelting import (
    direct_smelter_positions,
    generate_direct_smelter,
    logistic_smelter_origin,
    retire_direct_smelter_plan,
    retire_logistic_smelter_plan,
)
from planners.plan_validation import validate_no_collisions


def test_direct_starter_matches_the_live_reference_stack() -> None:
    positions = direct_smelter_positions((61.5, 24.5), "north")

    assert positions == {
        "drill": (61.5, 24.5),
        "furnace": (61.5, 21.5),
        "inserter": (61.5, 19.5),
        "provider": (61.5, 18.5),
        "power": (59.5, 22.5),
    }
    plan = generate_direct_smelter(
        "copper-plate", "copper-ore", (61.5, 24.5), "north",
    )
    actions = plan["phases"][0]["actions"]
    assert [action["entity"] for action in actions] == [
        "medium-electric-pole",
        "electric-mining-drill",
        "electric-furnace",
        "fast-inserter",
        "passive-provider-chest",
    ]
    assert not any("transport-belt" in action["entity"] for action in actions)
    assert not any("requester" in action["entity"] for action in actions)


def test_stone_starter_uses_two_drills_feeding_one_furnace() -> None:
    positions = direct_smelter_positions(
        (54.5, -64.5), "north", drill_count=2,
    )
    assert positions["secondary_drill"] == (57.5, -67.5)
    plan = generate_direct_smelter(
        "stone-brick", "stone", (54.5, -64.5), "north",
    )
    actions = plan["phases"][0]["actions"]

    drills = [
        action for action in actions
        if action["entity"] == "electric-mining-drill"
    ]
    assert [(action["position"], action["direction"]) for action in drills] == [
        ({"x": 57.5, "y": -67.5}, "west"),
        ({"x": 54.5, "y": -64.5}, "north"),
    ]
    assert sum(action["entity"] == "electric-furnace" for action in actions) == 1
    assert sum(action["entity"] == "medium-electric-pole" for action in actions) == 2
    assert not any("transport-belt" in action["entity"] for action in actions)
    assert not any("requester" in action["entity"] for action in actions)


def test_iron_starter_uses_two_direct_lanes_with_one_provider() -> None:
    positions = direct_smelter_positions(
        (61.5, 24.5), "north", drill_count=2, furnace_count=2,
    )
    plan = generate_direct_smelter(
        "iron-plate", "iron-ore", (61.5, 24.5), "north",
    )
    actions = plan["phases"][0]["actions"]

    assert positions["secondary_drill"] == (67.5, 18.5)
    assert positions["secondary_furnace"] == (64.5, 18.5)
    assert positions["secondary_inserter"] == (62.5, 18.5)
    assert sum(action["entity"] == "electric-mining-drill" for action in actions) == 2
    assert sum(action["entity"] == "electric-furnace" for action in actions) == 2
    assert sum(action["entity"] == "passive-provider-chest" for action in actions) == 1


@pytest.mark.parametrize("direction", ["north", "east", "south", "west"])
@pytest.mark.parametrize("pole_side", [-1, 1])
def test_two_lane_iron_starter_is_collision_free_in_every_orientation(
    direction: str, pole_side: int,
) -> None:
    plan = generate_direct_smelter(
        "iron-plate", "iron-ore", (61.5, 24.5), direction,
        pole_side=pole_side,
    )

    validate_no_collisions([("iron-starter", plan)])


@pytest.mark.parametrize("direction", ["north", "east", "south", "west"])
@pytest.mark.parametrize("pole_side", [-1, 1])
def test_two_drill_starter_is_collision_free_in_every_orientation(
    direction: str, pole_side: int,
) -> None:
    plan = generate_direct_smelter(
        "stone-brick", "stone", (54.5, -64.5), direction,
        pole_side=pole_side,
    )

    validate_no_collisions([("stone-brick-starter", plan)])


def test_stone_starter_retirement_removes_both_drills() -> None:
    retirement = retire_direct_smelter_plan(
        "stone-brick", "stone", (54.5, -64.5), "north",
    )
    actions = retirement["phases"][0]["actions"]

    assert sum(action["entity"] == "electric-mining-drill" for action in actions) == 2
    assert not any(action["entity"] == "medium-electric-pole" for action in actions)


def test_direct_starter_retirement_keeps_only_its_shared_power_pole() -> None:
    retirement = retire_direct_smelter_plan(
        "copper-plate", "copper-ore", (61.5, 24.5), "north",
    )
    actions = retirement["phases"][0]["actions"]

    assert {action["entity"] for action in actions} == {
        "electric-mining-drill",
        "electric-furnace",
        "fast-inserter",
        "passive-provider-chest",
    }
    assert all(action["action_type"] == "remove_entity" for action in actions)


def test_legacy_logistic_cell_remains_recognizable_for_retirement_only() -> None:
    origin = logistic_smelter_origin(((-29.5, -25.5), (-29.5, -19.5)))
    retirement = retire_logistic_smelter_plan("iron-plate", "iron-ore", origin)
    assert origin == (-31, -26)
    actions = retirement["phases"][0]["actions"]
    assert sum(a["entity"] == "requester-chest" for a in actions) == 1
    assert all(
        action["action_type"] == "remove_entity"
        for action in actions
    )
