# Path: tests/test_bootstrap_smelting.py
# Purpose: Verify the plate bootstrap has no circular belt dependency.

from planners.bootstrap_smelting import (
    direct_smelter_positions,
    generate_direct_smelter,
    logistic_smelter_origin,
    retire_direct_smelter_plan,
    retire_logistic_smelter_plan,
)


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
