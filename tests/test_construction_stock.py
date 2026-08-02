# Path: tests/test_construction_stock.py
# Purpose: Prove a construction buffer is earned from what the base can already make, so scarce resources go to capacity rather than into a stockpile.

from __future__ import annotations

import inspect
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator.construction_stock import (  # noqa: E402
    BUFFER_SECONDS,
    BULK_CONSTRUCTION_ITEMS,
    FALLBACK_STACK_SIZE,
    MAX_BUFFER_STACKS,
    buffer_ceiling,
    standing_target,
    standing_targets,
)
from orchestrator.parts_mall import MaterialShortage, add_demands  # noqa: E402
from orchestrator.priority_list import PriorityItem, PriorityList  # noqa: E402

_STACKS = {"transport-belt": 100, "splitter": 50, "underground-belt": 50}
_OPENING = 200


def _belt(rate: float) -> int:
    return standing_target(
        "transport-belt", _OPENING, production_rate=rate, stack_sizes=_STACKS,
    )


# --- scarce: spend on capacity, not on stock -------------------------------

def test_an_item_nothing_produces_gets_only_what_the_mission_asked() -> None:
    """Every one comes out of the player's starter kit, so stockpiling spends a
    resource the base cannot replace."""
    assert _belt(0.0) == _OPENING


def test_a_barely_producing_base_does_not_stockpile() -> None:
    """One machine at three a second has better uses for its iron -- 'the
    resources can be used to build more important things faster'."""
    assert _belt(3.0) == _OPENING


# --- productive: the buffer is earned --------------------------------------

def test_the_buffer_grows_with_what_the_base_can_make() -> None:
    assert _belt(6.0) > _belt(3.0)
    assert _belt(18.0) > _belt(6.0)


def test_the_buffer_is_that_many_seconds_of_its_own_output() -> None:
    """Affordable by construction: the base is already making them that fast."""
    assert _belt(6.0) == int(6.0 * BUFFER_SECONDS)


def test_a_six_machine_line_earns_about_a_thousand_belts() -> None:
    """The figure the user set by hand, now a consequence rather than a rule."""
    assert 900 <= _belt(18.0) <= 1100


# --- the ceiling -----------------------------------------------------------

def test_the_buffer_stops_at_the_ceiling() -> None:
    """Past this it is iron sitting in a chest instead of iron doing something."""
    assert _belt(1000.0) == MAX_BUFFER_STACKS * _STACKS["transport-belt"]


def test_the_ceiling_is_the_ten_stacks_that_was_asked_for() -> None:
    assert MAX_BUFFER_STACKS == 10
    assert buffer_ceiling("transport-belt", _STACKS) == 1000


def test_the_old_full_chest_figure_is_unreachable() -> None:
    """4800 was a milestone a run climbed toward for forty minutes, expanding
    iron every sixty seconds to reach it."""
    assert _belt(10_000.0) < 4800


def test_an_unknown_stack_size_falls_back_low_rather_than_high() -> None:
    assert buffer_ceiling("mystery-item", {}) == MAX_BUFFER_STACKS * FALLBACK_STACK_SIZE


# --- what never gets a buffer ----------------------------------------------

@pytest.mark.parametrize("item", ["oil-refinery", "pumpjack", "assembling-machine-2"])
def test_a_machine_never_earns_a_buffer(item: str) -> None:
    """A base needs a handful of refineries however large it grows."""
    assert item not in BULK_CONSTRUCTION_ITEMS
    assert standing_target(item, 2, production_rate=99.0, stack_sizes=_STACKS) == 2


def test_the_belt_family_does_earn_one() -> None:
    for item in ("transport-belt", "splitter", "underground-belt"):
        assert item in BULK_CONSTRUCTION_ITEMS


# --- invariants ------------------------------------------------------------

def test_the_buffer_never_drops_below_the_mission_requirement() -> None:
    """A mission needing 9000 must not be cut to a buffer."""
    assert standing_target(
        "transport-belt", 9000, production_rate=18.0, stack_sizes=_STACKS,
    ) == 9000


def test_the_buffer_is_monotonic_in_production_rate() -> None:
    previous = 0
    for rate in (0.0, 1.0, 3.0, 6.0, 12.0, 18.0, 60.0, 600.0):
        current = _belt(rate)
        assert current >= previous
        previous = current


def test_a_whole_table_is_decided_per_item() -> None:
    decided = standing_targets(
        {"transport-belt": 200, "oil-refinery": 2, "inserter": 20},
        {"transport-belt": 18.0, "oil-refinery": 99.0},
        _STACKS,
    )

    assert 900 <= decided["transport-belt"] <= 1100
    assert decided["oil-refinery"] == 2
    assert decided["inserter"] == 20, "no production, so no buffer"


# --- the run must not wait on a buffer -------------------------------------

def test_a_buffer_never_becomes_something_the_run_waits_for() -> None:
    """Raising the mall TARGET to a buffer turned a satisfied 200-belt
    requirement into a gate: the loop sat in wait_for_stock polling every five
    seconds and expanding iron every sixty, while the research it was launched
    for never started."""
    survey = inspect.getsource(builder._survey_pass)

    assert "standing_target" not in survey
    assert "mall_targets[item] =" not in survey


def test_the_buffer_is_offered_to_the_cell_not_to_the_priority_list() -> None:
    served = inspect.getsource(builder._serve_mall_task)

    assert "stock_buffer_for(" in served
    assert "stock_target=target" in served, "the run still waits for the mission figure"
    assert "stock_buffer=buffer" in served


def test_the_buffer_is_measured_from_the_live_line() -> None:
    """Not from stock, which a starter kit inflates, and not from a constant."""
    source = inspect.getsource(builder.stock_buffer_for)

    assert "_live_output_rate(" in source
    assert "BULK_CONSTRUCTION_ITEMS" in source


def test_an_item_with_no_line_reports_no_output() -> None:
    source = inspect.getsource(builder._live_output_rate)

    assert "return 0.0" in source
    assert "machine_count <= 0" in source


# --- the persisted-target trap ---------------------------------------------

def test_a_persisted_target_never_outlives_the_mission_that_set_it() -> None:
    """max() meant a target could only rise, and it is saved to disk -- so one
    run that raised transport-belt to 4800 left every LATER run waiting for
    4800, with nothing in the log saying where the number came from."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "priorities.json"
        stale = PriorityList(path, 0)
        stale.items["transport-belt"] = PriorityItem(
            item="transport-belt", target=4800, base_rating=100, created_tick=0,
        )
        stale._save()

        fresh = PriorityList(path, 0)
        fresh.sync({"transport-belt": 200}, {"transport-belt": 0}, 10)

        assert fresh.items["transport-belt"].target == 200


def test_a_shortage_can_still_raise_a_target_within_a_run() -> None:
    with tempfile.TemporaryDirectory() as directory:
        priorities = PriorityList(Path(directory) / "p.json", 0)
        targets = {"electric-mining-drill": 6}
        priorities.sync(targets, {}, 10)
        add_demands(targets, MaterialShortage("mine", {"electric-mining-drill": 14}, {}))
        priorities.sync(targets, {}, 20)

        assert priorities.items["electric-mining-drill"].target == 14
