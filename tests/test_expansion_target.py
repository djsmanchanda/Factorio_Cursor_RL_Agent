# Path: tests/test_expansion_target.py
# Purpose: Prove expansion follows the bottleneck down the recipe chain instead of giving up when no direct ingredient is mineable.

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator.autonomous_builder import _mineable, expansion_target  # noqa: E402
from planners.recipe_data import LINE_RECIPES  # noqa: E402


@pytest.fixture
def catalog_belt(monkeypatch):
    """fast-transport-belt reaches LINE_RECIPES from the live recipe catalog,
    so a test has to install it the way install_catalog_line_recipes does."""
    monkeypatch.setitem(LINE_RECIPES, "fast-transport-belt", {
        "machine": "assembling-machine-2",
        "ingredients": ["transport-belt", "iron-gear-wheel"],
        "amounts": [1, 5], "product_amount": 1, "craft_time": 0.5,
    })


def test_traces_through_two_levels_to_the_real_constraint(catalog_belt) -> None:
    """The live stall: fast-transport-belt sat at 11/25 while its constraint was
    iron ore, two levels down. Neither direct ingredient is mineable, so the old
    one-level check returned None and deferred forever."""
    stock = {"transport-belt": 39, "iron-gear-wheel": 2, "iron-plate": 208}

    assert not any(_mineable(i) for i in LINE_RECIPES["fast-transport-belt"]["ingredients"])
    assert expansion_target("fast-transport-belt", stock) == "iron-plate"


def test_follows_whichever_input_the_base_is_short_of(catalog_belt) -> None:
    """'Solve one bottleneck after the next' means tracking the live shortage,
    not walking a fixed branch."""
    gears_short = expansion_target(
        "fast-transport-belt", {"transport-belt": 500, "iron-gear-wheel": 1},
    )
    belts_short = expansion_target(
        "fast-transport-belt", {"transport-belt": 0, "iron-gear-wheel": 500},
    )

    assert gears_short == "iron-plate"   # via iron-gear-wheel
    assert belts_short == "iron-plate"   # via transport-belt


def test_shortage_is_measured_against_what_a_craft_consumes(catalog_belt) -> None:
    """5 gears per craft vs 1 belt: 20 gears is scarcer than 10 belts here."""
    spec = LINE_RECIPES["fast-transport-belt"]

    assert dict(zip(spec["ingredients"], spec["amounts"]))["iron-gear-wheel"] == 5
    assert expansion_target("fast-transport-belt", {"transport-belt": 10, "iron-gear-wheel": 20})


def test_a_mineable_stage_is_its_own_target() -> None:
    assert _mineable("iron-plate")
    assert expansion_target("iron-plate", {}) == "iron-plate"


def test_stone_brick_is_a_mineable_furnace_stage() -> None:
    assert _mineable("stone-brick")
    assert expansion_target("stone-brick", {}) == "stone-brick"


def test_raw_ingredient_does_not_turn_an_assembler_recipe_into_a_refinery(
    monkeypatch,
) -> None:
    monkeypatch.setitem(LINE_RECIPES, "landfill", {
        "machine": "assembling-machine-2", "ingredients": ["stone"],
        "amounts": [50], "product_amount": 1, "craft_time": 0.5,
    })

    assert not _mineable("landfill")
    assert expansion_target("landfill", {}) is None


@pytest.mark.parametrize("item", ["automation-science-pack", "electronic-circuit", "inserter"])
def test_every_real_goal_resolves_to_an_extraction_stage(item: str) -> None:
    target = expansion_target(item, {})

    assert target is not None, f"{item} would defer forever"
    assert _mineable(target)


def test_a_recipe_cycle_terminates(monkeypatch) -> None:
    """Guard against the class of hang already seen once: inserters need iron
    plate, and the iron-plate stage needed inserters."""
    monkeypatch.setitem(LINE_RECIPES, "ouroboros-a", {
        "machine": "assembling-machine-2", "ingredients": ["ouroboros-b"],
        "amounts": [1], "product_amount": 1, "craft_time": 1.0,
    })
    monkeypatch.setitem(LINE_RECIPES, "ouroboros-b", {
        "machine": "assembling-machine-2", "ingredients": ["ouroboros-a"],
        "amounts": [1], "product_amount": 1, "craft_time": 1.0,
    })

    assert expansion_target("ouroboros-a", {}) is None


def test_returns_none_when_no_branch_reaches_extraction(monkeypatch) -> None:
    """An honest None still defers -- but only when nothing is expandable."""
    monkeypatch.setitem(LINE_RECIPES, "imported-widget", {
        "machine": "assembling-machine-2", "ingredients": ["off-world-part"],
        "amounts": [1], "product_amount": 1, "craft_time": 1.0,
    })

    assert expansion_target("imported-widget", {}) is None
