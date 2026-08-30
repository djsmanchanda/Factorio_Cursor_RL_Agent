# Path: tests/test_promotable.py
# Purpose: Prove promotion eligibility is derived from what a belt-fed line can physically supply, rather than from a hand-maintained list of recipe names.

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator.intermediate_scaling import (  # noqa: E402
    STAGE_OWNED_MACHINES,
    is_promotable,
    promoted_line_machine_count,
)
from planners.recipe_data import LINE_RECIPES, MACHINE_SPEEDS  # noqa: E402


def test_a_belt_fed_intermediate_is_promotable() -> None:
    assert is_promotable("iron-gear-wheel")
    assert is_promotable("copper-cable")
    assert is_promotable("electronic-circuit")


def test_a_fluid_recipe_is_not_promotable() -> None:
    """A promoted line is belt-fed with no pipe run, so six assemblers waiting
    on sulfuric acid would sit empty. The old list named this one anyway."""
    assert LINE_RECIPES["processing-unit"]["fluid_ingredients"]
    assert not is_promotable("processing-unit")


def test_the_items_the_mall_mass_produces_are_promotable() -> None:
    """Both were absent from the old list, and 'it should have started regular
    transport belt production' was the reported symptom."""
    assert is_promotable("transport-belt")
    assert is_promotable("inserter")


@pytest.mark.parametrize("plate", ["iron-plate", "copper-plate", "steel-plate"])
def test_smelting_stays_with_the_mining_phase_that_sizes_it(plate: str) -> None:
    assert LINE_RECIPES[plate]["machine"] in STAGE_OWNED_MACHINES
    assert not is_promotable(plate)


@pytest.mark.parametrize("recipe", ["plastic-bar", "sulfur"])
def test_chemical_recipes_stay_with_the_stage_that_pipes_them(recipe: str) -> None:
    assert not is_promotable(recipe)


def test_an_unknown_item_is_not_promotable() -> None:
    assert not is_promotable("iron-ore")
    assert not is_promotable("nonexistent-widget")


def test_every_promotable_recipe_has_what_a_line_needs_to_be_built() -> None:
    """The predicate is the only gate, so anything passing it must be sizeable
    and belt-feedable without a further check downstream."""
    for item in LINE_RECIPES:
        if not is_promotable(item):
            continue
        spec = LINE_RECIPES[item]
        assert spec["machine"] in MACHINE_SPEEDS
        assert spec["craft_time"] > 0
        assert spec["ingredients"], f"{item} has no belt-deliverable input"
        assert not spec.get("fluid_ingredients")


def test_promotion_still_declines_everything_it_should() -> None:
    assert promoted_line_machine_count("processing-unit", 99.0, saturated=True) is None
    assert promoted_line_machine_count("iron-plate", 99.0, saturated=True) is None
    assert promoted_line_machine_count("iron-gear-wheel", 99.0) is not None
