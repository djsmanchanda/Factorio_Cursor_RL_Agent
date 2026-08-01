# Path: tests/test_inserter_sizing.py
# Purpose: Prove a line's inserter tier follows the rate one inserter actually carries, so slow smelter rows stop paying for fast inserters they cannot use.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planners.recipe_data import (  # noqa: E402
    FEED_HEADROOM,
    feeder_rate,
    INSERTER_TIERS,
    LINE_RECIPES,
    inserter_for_demand,
    machine_handled_rates,
    machine_ingredient_rates,
)


def _chosen(recipe: str) -> str:
    return inserter_for_demand(max(machine_handled_rates(recipe)) * FEED_HEADROOM)


@pytest.mark.parametrize("recipe", ["iron-plate", "copper-plate", "steel-plate"])
def test_electric_furnace_rows_use_a_plain_inserter(recipe: str) -> None:
    """A furnace smelting one plate per 1.6s moves 0.625 item/s; a fast inserter
    there is five times the throughput and a whole extra production chain."""
    assert LINE_RECIPES[recipe]["machine"] == "electric-furnace"
    assert _chosen(recipe) == "inserter"


def test_a_busy_line_still_escalates() -> None:
    """Cheaper by default must not mean under-provisioned."""
    assert _chosen("electronic-circuit") == "bulk-inserter"
    assert _chosen("iron-gear-wheel") == "fast-inserter"


@pytest.mark.parametrize("recipe", ["copper-cable", "iron-stick", "transport-belt"])
def test_two_product_recipes_are_measured_on_their_output(recipe: str) -> None:
    """A line layout puts the same tier on both faces, so the busier face has to
    be the one measured. These emit two products per craft."""
    assert max(machine_handled_rates(recipe)) == 2 * max(machine_ingredient_rates(recipe))
    assert feeder_rate(_chosen(recipe)) >= max(machine_handled_rates(recipe)) * FEED_HEADROOM


def test_output_rate_can_decide_the_tier_on_its_own() -> None:
    """sulfur draws no items at all and emits two a second, so ingredient-only
    sizing had nothing to work from and would have starved its collector."""
    ingredients_only = inserter_for_demand(
        max(machine_ingredient_rates("sulfur"), default=0.0) * FEED_HEADROOM
    )
    assert ingredients_only == "inserter"
    assert _chosen("sulfur") == "fast-inserter"


def test_no_recipe_is_sized_below_what_it_actually_moves() -> None:
    """The property the sulfur and copper-cable cases are instances of. Stated
    outright so it keeps holding when a rate change stops separating them."""
    top = feeder_rate("bulk-inserter")
    for recipe in LINE_RECIPES:
        demand = max(machine_handled_rates(recipe), default=0.0) * FEED_HEADROOM
        assert feeder_rate(_chosen(recipe)) >= min(demand, top)


def test_a_fluid_only_recipe_is_sized_by_what_it_emits() -> None:
    """sulfur has no item ingredients at all -- ingredient-only sizing had no
    value to work from."""
    assert LINE_RECIPES["sulfur"]["ingredients"] == []
    assert machine_ingredient_rates("sulfur") == []
    assert _chosen("sulfur") == "fast-inserter"


def test_every_line_recipe_can_size_itself() -> None:
    for recipe in LINE_RECIPES:
        assert _chosen(recipe) in INSERTER_TIERS


def test_stack_inserters_are_never_selected_automatically() -> None:
    """docs/21 records stack inserters as Gleba-only production, so demand alone
    must not conjure one onto Nauvis."""
    assert inserter_for_demand(10_000) == "bulk-inserter"
    assert all(_chosen(recipe) != "stack-inserter" for recipe in LINE_RECIPES)


def test_selection_is_the_cheapest_tier_that_covers_demand() -> None:
    for tier in ("inserter", "fast-inserter", "bulk-inserter"):
        rate = feeder_rate(tier)
        assert inserter_for_demand(rate) == tier, "a tier must cover its own rated load"
    # Just past a tier's rating escalates -- except at the top, which saturates
    # rather than reaching for a Gleba-only stack inserter.
    assert inserter_for_demand(feeder_rate("inserter") + 0.01) == "fast-inserter"
    assert inserter_for_demand(feeder_rate("fast-inserter") + 0.01) == "bulk-inserter"
    assert inserter_for_demand(feeder_rate("bulk-inserter") + 0.01) == "bulk-inserter"


def test_demand_must_not_be_negative() -> None:
    with pytest.raises(ValueError, match="negative"):
        inserter_for_demand(-1)


def test_plain_inserter_is_a_legal_tier_for_line_layouts() -> None:
    """generate_line_layout validates against INSERTER_TIERS, which excluded the
    basic inserter entirely before rate-driven selection existed."""
    from planners.local_layout_planner import LocalLayoutPlanner

    plan = LocalLayoutPlanner().generate_line_layout(
        "iron-plate", 2, 0, 0, belt_type="transport-belt", inserter_type="inserter",
        feed_style="chest", terminal_collector=True,
    )
    entities = {
        action["entity"]
        for phase in plan["phases"] for action in phase["actions"]
    }
    assert "inserter" in entities
    assert "fast-inserter" not in entities
