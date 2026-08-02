# Path: tests/test_construction_stock.py
# Purpose: Prove a construction item is held to a small opening figure only while the base cannot make it, and fills the chest once it can.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.construction_stock import (  # noqa: E402
    BULK_CONSTRUCTION_ITEMS,
    FALLBACK_STACK_SIZE,
    PROVIDER_CHEST_SLOTS,
    chest_full_target,
    standing_target,
    standing_targets,
)

_STACKS = {"transport-belt": 100, "splitter": 50, "underground-belt": 50}


def test_a_scarce_item_holds_its_opening_figure() -> None:
    """Nothing on the base makes it, so every one comes out of the starter kit."""
    assert standing_target(
        "transport-belt", 50, self_sufficient=False, stack_sizes=_STACKS,
    ) == 50


def test_a_self_made_item_fills_the_chest() -> None:
    """'after the game is out of the starter phase, just let it build and fill
    up the chest'."""
    assert standing_target(
        "transport-belt", 50, self_sufficient=True, stack_sizes=_STACKS,
    ) == PROVIDER_CHEST_SLOTS * 100


def test_the_cap_that_was_too_low_is_gone() -> None:
    """The reported limit: blueprints wanting more than a thousand belts."""
    target = standing_target(
        "transport-belt", 50, self_sufficient=True, stack_sizes=_STACKS,
    )

    assert target > 1000


@pytest.mark.parametrize("item", ["splitter", "underground-belt"])
def test_the_other_belt_family_items_scale_too(item: str) -> None:
    """'hundreds of splitters and underground belts'."""
    assert item in BULK_CONSTRUCTION_ITEMS
    assert standing_target(
        item, 20, self_sufficient=True, stack_sizes=_STACKS,
    ) >= 200


@pytest.mark.parametrize("item", ["oil-refinery", "pumpjack", "assembling-machine-2"])
def test_a_machine_never_fills_a_chest(item: str) -> None:
    """A base needs a handful of refineries however large it grows, and a chest
    full of them would eat the plates the belts joining them are made of."""
    assert item not in BULK_CONSTRUCTION_ITEMS
    assert standing_target(item, 2, self_sufficient=True, stack_sizes=_STACKS) == 2


def test_an_unknown_stack_size_falls_back_low_rather_than_high() -> None:
    """Understating fills less of the chest and costs a top-up. Overstating asks
    for stock the chest cannot hold, and the cell never reads as done."""
    assert chest_full_target("mystery-item", {}) == (
        PROVIDER_CHEST_SLOTS * FALLBACK_STACK_SIZE
    )


def test_the_target_never_drops_below_what_the_mission_asked_for() -> None:
    """A mission needing 5000 of something must not be cut to a chest."""
    assert standing_target(
        "transport-belt", 9000, self_sufficient=True, stack_sizes=_STACKS,
    ) == 9000


def test_a_whole_table_is_decided_per_item() -> None:
    grown = standing_targets(
        {"transport-belt": 50, "oil-refinery": 2, "inserter": 20},
        {"transport-belt": True, "oil-refinery": True, "inserter": False},
        _STACKS,
    )

    assert grown["transport-belt"] == PROVIDER_CHEST_SLOTS * 100
    assert grown["oil-refinery"] == 2
    assert grown["inserter"] == 20, "still scarce, so still capped"


def test_an_item_absent_from_the_capability_map_is_treated_as_scarce() -> None:
    """Not knowing is not the same as knowing it is fine."""
    assert standing_targets({"transport-belt": 50}, {}, _STACKS) == {
        "transport-belt": 50,
    }


def test_the_run_loop_lifts_caps_before_it_picks_a_task() -> None:
    """Otherwise a pass completes a target that was about to be raised, and the
    item is dropped from the mall for a whole cycle."""
    import inspect

    from orchestrator import autonomous_builder as builder

    source = inspect.getsource(builder._survey_pass)

    assert source.index("_lift_targets_the_base_can_supply(") < source.index(
        "priorities.sync("
    )


def test_only_bulk_items_are_ever_lifted() -> None:
    import inspect

    from orchestrator import autonomous_builder as builder

    source = inspect.getsource(builder._lift_targets_the_base_can_supply)

    assert "BULK_CONSTRUCTION_ITEMS" in source
    assert "_has_producer(" in source, "self-sufficiency is the phase boundary"


def test_a_lift_never_lowers_a_target() -> None:
    import inspect

    from orchestrator import autonomous_builder as builder

    source = inspect.getsource(builder._lift_targets_the_base_can_supply)

    assert "if lifted > target:" in source
