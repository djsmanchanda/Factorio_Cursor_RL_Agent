# Path: tests/test_baseline_production.py
# Purpose: Prove the standing prep set is internally balanced and that its plate draw sizes extraction on the same phase ladder the rest of the system uses.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.baseline_production import (  # noqa: E402
    BASELINE_MACHINES,
    BASELINE_PLATES,
    PLATE_FOUNDATION_BUILD_ORDER,
    PLATE_FOUNDATION_FURNACES,
    baseline_build_order,
    baseline_drill_phase,
    baseline_plate_draw,
    baseline_smelter_count,
    demand_adjusted_plate_draw,
    mall_plate_draw,
    STEEL_BASELINE_FURNACES,
    STEEL_IRON_CAPACITY_FLOOR,
)
from orchestrator.extraction_capacity import EXTRACTION_DRILL_PHASES  # noqa: E402
from planners.recipe_data import LINE_RECIPES, MACHINE_SPEEDS  # noqa: E402


def _output_rate(recipe: str, machines: int) -> float:
    spec = LINE_RECIPES[recipe]
    return (
        machines * MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]
        * spec.get("product_amount", 1)
    )


def test_the_prep_set_is_the_agreed_one() -> None:
    assert BASELINE_MACHINES == {
        "iron-gear-wheel": 2,
        "copper-cable": 2,
        "electronic-circuit": 1,
    }


def test_direct_plate_foundation_precedes_capacity_expansion() -> None:
    assert PLATE_FOUNDATION_BUILD_ORDER == (
        "iron-plate", "copper-plate",
    )
    assert PLATE_FOUNDATION_FURNACES == {
        "iron-plate": 6,
        "copper-plate": 6,
        "stone-brick": 6,
    }


def test_nothing_smelted_is_prepped_as_a_mall_cell() -> None:
    """A furnace takes its recipe from what is inserted, so an idle one reports
    none and find_line can never count it. Prep saw zero however many it had
    built and placed another cell every pass -- twelve steel-plate furnaces
    across six mall cells in one run."""
    for recipe in BASELINE_MACHINES:
        assert LINE_RECIPES[recipe].get("set_recipe", True), recipe


def test_cable_capacity_covers_the_circuit_machine_it_feeds() -> None:
    """A prep set that starves itself is not prep. Three cable per circuit."""
    made = _output_rate("copper-cable", BASELINE_MACHINES["copper-cable"])
    needed = (
        BASELINE_MACHINES["electronic-circuit"]
        * MACHINE_SPEEDS["assembling-machine-2"]
        / LINE_RECIPES["electronic-circuit"]["craft_time"]
        * 3
    )

    assert made >= needed


def test_plate_draw_counts_every_direct_consumer() -> None:
    """Cable machines draw copper whoever ends up using the cable."""
    draw = baseline_plate_draw()

    assert set(draw) == set(BASELINE_PLATES)
    assert draw["iron-plate"] == pytest.approx(7.5)
    assert draw["copper-plate"] == pytest.approx(3.0)


def test_mall_burst_draw_expands_nested_plate_requirements() -> None:
    draw = mall_plate_draw({"transport-belt": 200})

    assert draw["iron-plate"] == pytest.approx(300.0)
    assert draw["copper-plate"] == pytest.approx(0.0)


def test_mall_burst_subtracts_stocked_intermediates_recursively() -> None:
    draw = mall_plate_draw(
        {"transport-belt": 200},
        {"transport-belt": 100, "iron-gear-wheel": 50},
    )

    assert draw["iron-plate"] == pytest.approx(50.0)

def test_mall_burst_has_a_bounded_influence_on_standing_draw() -> None:
    adjusted = demand_adjusted_plate_draw({"transport-belt": 200})
    baseline = baseline_plate_draw()

    assert adjusted["iron-plate"] > baseline["iron-plate"]
    assert adjusted["copper-plate"] == baseline["copper-plate"]

def test_iron_needs_the_next_phase_up_from_a_starting_row() -> None:
    """8.75 plate/s is well past what six drills carry -- the reason prep has
    to raise extraction rather than inherit the opening row."""
    assert baseline_drill_phase("iron-plate") == 24
    assert EXTRACTION_DRILL_PHASES[0] == 6


def test_copper_is_already_covered_by_the_opening_row() -> None:
    assert baseline_drill_phase("copper-plate") == EXTRACTION_DRILL_PHASES[0]


def test_steel_baseline_preserves_shared_iron_capacity() -> None:
    assert STEEL_BASELINE_FURNACES == 6
    assert STEEL_IRON_CAPACITY_FLOOR == 12


@pytest.mark.parametrize("plate", BASELINE_PLATES)
def test_drill_phases_land_on_the_shared_ladder(plate: str) -> None:
    assert baseline_drill_phase(plate) in EXTRACTION_DRILL_PHASES


@pytest.mark.parametrize("plate", BASELINE_PLATES)
def test_smelters_cover_the_draw_they_are_sized_for(plate: str) -> None:
    furnaces = baseline_smelter_count(plate)

    assert _output_rate(plate, furnaces) >= baseline_plate_draw()[plate]
    assert _output_rate(plate, furnaces - 1) < baseline_plate_draw()[plate], "no slack furnace"


def test_build_order_puts_a_feeder_before_what_it_feeds() -> None:
    order = baseline_build_order()

    assert set(order) == set(BASELINE_MACHINES)
    assert order.index("copper-cable") < order.index("electronic-circuit")


def test_build_order_is_deterministic() -> None:
    assert baseline_build_order() == baseline_build_order()


def test_a_circular_prep_set_is_rejected(monkeypatch) -> None:
    """A definition error should fail loudly here, not deadlock a live run."""
    monkeypatch.setitem(LINE_RECIPES, "loop-a", {
        "machine": "assembling-machine-2", "ingredients": ["loop-b"],
        "amounts": [1], "product_amount": 1, "craft_time": 1.0,
    })
    monkeypatch.setitem(LINE_RECIPES, "loop-b", {
        "machine": "assembling-machine-2", "ingredients": ["loop-a"],
        "amounts": [1], "product_amount": 1, "craft_time": 1.0,
    })
    monkeypatch.setattr(
        "orchestrator.baseline_production.BASELINE_MACHINES",
        {"loop-a": 1, "loop-b": 1},
    )

    with pytest.raises(ValueError, match="circular dependency"):
        baseline_build_order()


def test_every_prep_recipe_is_actually_buildable() -> None:
    for recipe in BASELINE_MACHINES:
        spec = LINE_RECIPES[recipe]
        assert spec["machine"] in MACHINE_SPEEDS, f"{recipe} has no measured machine speed"
