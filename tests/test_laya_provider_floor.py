# Path: tests/test_laya_provider_floor.py | Purpose: Protect the one-cell passive-provider floor.

from orchestrator.parts_mall import PROVIDER_CHEST_FLOOR, mission_mall_targets

RECIPES = {
    "automation-science-pack": {"machine": "assembling-machine-1", "ingredients": []},
    "passive-provider-chest": {"machine": "assembling-machine-1", "ingredients": []},
}


def test_a_one_cell_provider_floor_is_kept_funded():
    targets = mission_mall_targets(("automation-science-pack",), RECIPES)
    assert targets["passive-provider-chest"] == PROVIDER_CHEST_FLOOR == 16


def test_the_floor_is_dropped_when_the_recipe_is_not_enabled():
    targets = mission_mall_targets(("automation-science-pack",), {
        "automation-science-pack": {"machine": "assembling-machine-1", "ingredients": []},
    })
    assert "passive-provider-chest" not in targets
