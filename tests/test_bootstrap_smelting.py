# Path: tests/test_bootstrap_smelting.py
# Purpose: Verify the plate bootstrap has no circular belt dependency.

from planners.bootstrap_smelting import (
    generate_logistic_smelter,
    logistic_smelter_origin,
    retire_logistic_smelter_plan,
)


def test_logistic_smelter_shares_one_requester_without_belts() -> None:
    plan = generate_logistic_smelter("iron-plate", "iron-ore", (-31, -26))
    actions = plan["phases"][0]["actions"]

    assert sum(a["entity"] == "electric-furnace" for a in actions) == 2
    assert sum(a["entity"] == "requester-chest" for a in actions) == 1
    assert sum(a["entity"] == "passive-provider-chest" for a in actions) == 2
    assert not any("transport-belt" in a["entity"] for a in actions)

    origin = logistic_smelter_origin(((-29.5, -25.5), (-29.5, -19.5)))
    retirement = retire_logistic_smelter_plan("iron-plate", "iron-ore", origin)
    assert origin == (-31, -26)
    assert all(
        action["action_type"] == "remove_entity"
        for action in retirement["phases"][0]["actions"]
    )