# Path: tests/test_blocked_feed_endpoint.py
# Purpose: Prove a bridge that cannot reach its feed endpoint says so, instead of attaching to a blocked side and laying belt over whatever stands there.

from __future__ import annotations

import inspect
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import extraction_transport, stage_transport  # noqa: E402
from orchestrator.stage_transport import _clear_side  # noqa: E402
from planners.belt_bridge import DIRECTION_VECTORS  # noqa: E402

# The reported case: the copper-cable line's feed chest, flanked by the line's
# own feed inserters. A transport-belt ghost went to (117.5,-18.5), on top of a
# fast-inserter the same system had placed twenty-five seconds earlier.
_CHEST = (117.5, -17.5)


def _surround(chest, *, leave: str | None = None) -> set[tuple[int, int]]:
    blocked: set[tuple[int, int]] = set()
    for name, (vx, vy) in DIRECTION_VECTORS.items():
        if name == leave:
            continue
        for step in (1, 2):
            # floor, not int: they differ on negative coordinates, and the
            # reported case sits at y=-17.5.
            blocked.add((
                math.floor(chest[0] + vx * step), math.floor(chest[1] + vy * step),
            ))
    return blocked


def test_a_reachable_side_is_still_chosen() -> None:
    assert _clear_side(_CHEST, "west", set()) == "west"


def test_the_preferred_side_is_taken_when_it_is_free() -> None:
    assert _clear_side(_CHEST, "north", set()) == "north"


def test_a_blocked_preference_falls_through_to_a_free_side() -> None:
    """A stage sits on one side of its own chest, so the direct approach is
    frequently its own machine row."""
    chosen = _clear_side(_CHEST, "west", _surround(_CHEST, leave="east"))

    assert chosen == "east"


def test_a_surrounded_chest_reports_no_side_at_all() -> None:
    """It used to return the preferred side regardless, deliberately -- which
    is what laid belt on top of an existing inserter."""
    assert _clear_side(_CHEST, "west", _surround(_CHEST)) is None


def test_both_tiles_of_a_side_must_be_free() -> None:
    """The inserter goes one tile out and the belt's first tile two out, so a
    side with either occupied is unusable."""
    one_tile_out = {(math.floor(_CHEST[0] - 1), math.floor(_CHEST[1]))}

    assert _clear_side(_CHEST, "west", one_tile_out) != "west"


def test_the_build_path_refuses_rather_than_colliding() -> None:
    source = inspect.getsource(stage_transport._survey_belt_route)
    guard = source[source.index("entry_direction = _clear_side"):]

    assert "if entry_direction is None:" in guard
    assert "raise StuckError(" in guard
    assert "every side of it is already occupied" in guard


def test_the_source_side_is_guarded_too() -> None:
    source = inspect.getsource(stage_transport._survey_belt_route)

    assert "if exit_direction is None:" in source


def test_the_preflight_refuses_on_the_same_rule() -> None:
    """The preflight exists to find this before anything is placed, so it must
    not be the one path that still emits the colliding plan."""
    source = inspect.getsource(extraction_transport.preflight_ingredient_transport)
    owner = inspect.getsource(stage_transport._survey_belt_route)

    assert "_plan_belt_transport(" in source
    assert "entry_direction is None" in owner


def test_the_failure_names_the_endpoint_that_cannot_be_reached() -> None:
    """'blocked by real infrastructure' said nothing about which chest or why."""
    source = inspect.getsource(stage_transport._survey_belt_route)

    assert "{feed_position}" in source


def test_every_caller_checks_for_a_missing_side() -> None:
    """A None leaking into bridge_* would become an invalid direction rather
    than a stated failure."""
    for module in (stage_transport, extraction_transport):
        source = inspect.getsource(module)
        uses = source.count("_clear_side(")
        if "def _clear_side(" in source:
            uses -= 1
        assert source.count("is None") >= uses, module.__name__
