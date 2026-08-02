# Path: tests/test_smelter_block.py
# Purpose: Prove a growing mine widens a compact smelting block instead of extending one enormous furnace line.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.extraction_capacity import EXTRACTION_DRILL_PHASES  # noqa: E402
from orchestrator.stage_extraction import smelter_count_for_drills  # noqa: E402
from planners.smelter_block import (  # noqa: E402
    BLOCK_ROWS_LATE,
    BLOCK_ROWS_START,
    FURNACE_PITCH,
    ROW_FURNACES_LATE,
    ROW_FURNACES_START,
    block_capacity,
    block_is_full,
    block_shape,
    feed_belt_length,
    row_capacity,
)


def test_the_opening_row_holds_what_was_asked_for() -> None:
    """'atleast 15 furnaces in the line (15 in starting phase, upto 50 in late
    game) and 4 rows (in the starting phase upto 30 in late game)'."""
    assert ROW_FURNACES_START == 15
    assert ROW_FURNACES_LATE == 50
    assert BLOCK_ROWS_START == 4
    assert BLOCK_ROWS_LATE == 30


def test_a_row_fills_before_a_second_is_started() -> None:
    """A small mine gets one short row, not a wide sparse block, so the feed
    belt runs the length of a row it can actually saturate."""
    assert block_shape(7).rows == 1
    assert block_shape(7).per_row == 7


def test_a_row_never_exceeds_its_cap() -> None:
    for furnaces in range(1, 200):
        assert block_shape(furnaces).per_row <= row_capacity()
        assert block_shape(furnaces, late_game=True).per_row <= row_capacity(True)


def test_rows_are_the_growth_axis_beyond_one_row() -> None:
    assert block_shape(ROW_FURNACES_START + 1).rows == 2
    assert block_shape(ROW_FURNACES_START * 4).rows == 4


def test_every_furnace_asked_for_has_a_place() -> None:
    for furnaces in range(1, 200):
        assert block_shape(furnaces).capacity >= furnaces


def test_a_block_beats_a_line_on_belt_at_every_drill_phase() -> None:
    """The point of the change: the same furnace count behind a much shorter
    run of belt. One row of 104 furnaces is a 312-tile line, and a belt long
    enough to serve it has to cross whatever the base already built."""
    for drills in EXTRACTION_DRILL_PHASES:
        furnaces = smelter_count_for_drills("iron-plate", drills, 0.30)
        one_row = furnaces * FURNACE_PITCH
        block = feed_belt_length(block_shape(furnaces))

        assert block <= one_row, f"{drills} drills: block {block} vs line {one_row}"


def test_the_top_drill_phase_is_a_third_of_the_belt() -> None:
    furnaces = smelter_count_for_drills("iron-plate", EXTRACTION_DRILL_PHASES[-1], 0.30)

    assert feed_belt_length(block_shape(furnaces)) < furnaces * FURNACE_PITCH / 2


def test_a_block_past_its_rows_says_so() -> None:
    """At that size the ore feeding it is more than one mine can supply, so the
    answer is a second block at another patch, not a deeper one here."""
    assert not block_is_full(ROW_FURNACES_START * BLOCK_ROWS_START)
    assert block_is_full(ROW_FURNACES_START * BLOCK_ROWS_START + 1)


def test_late_game_holds_far_more_before_it_is_full() -> None:
    at_start_limit = ROW_FURNACES_START * BLOCK_ROWS_START + 1

    assert block_is_full(at_start_limit)
    assert not block_is_full(at_start_limit, late_game=True)
    assert block_capacity(True) > block_capacity()


def test_the_late_game_block_covers_the_final_drill_phase() -> None:
    """100 drills is the last rung of the extraction ladder; the block for it
    must not already be full."""
    furnaces = smelter_count_for_drills("iron-plate", EXTRACTION_DRILL_PHASES[-1], 0.30)

    assert not block_is_full(furnaces, late_game=True)


def test_an_empty_block_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one furnace"):
        block_shape(0)


def test_the_shape_is_deterministic() -> None:
    assert block_shape(37) == block_shape(37)
