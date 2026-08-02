# Path: tests/test_pole_relocation.py
# Purpose: Prove a pole blocking a belt route is nudged aside only when the move keeps everything it powers powered and everything it wires connected.

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import stage_transport  # noqa: E402
from orchestrator.pole_relocation import (  # noqa: E402
    MAX_NUDGE_TILES,
    WIRE_MARGIN,
    PoleMove,
    candidate_positions,
    choose_pole_move,
    corridor_tiles,
    keeps_supply,
    keeps_wire,
    relocation_actions,
    relocation_plan,
)
from planners.infrastructure import POLE_SPECS  # noqa: E402
from planners.plan_validation import validate_build_plan  # noqa: E402

_MEDIUM = "medium-electric-pole"
_POLE = (10.5, 10.5)


def _move(**kwargs):
    defaults = dict(supplied=(), neighbours=(), blocked=set(), keep_clear=set())
    return choose_pole_move(_MEDIUM, _POLE, **{**defaults, **kwargs})


def test_a_free_pole_steps_one_tile_aside() -> None:
    move = _move(keep_clear={(10, 10)})

    assert move is not None
    assert move.tiles == 1


def test_the_smallest_nudge_wins() -> None:
    """The least movement is the least likely to change what the pole covers."""
    offsets = [
        abs(p[0] - _POLE[0]) + abs(p[1] - _POLE[1]) for p in candidate_positions(_POLE)
    ]

    assert offsets == sorted(offsets)
    assert max(offsets) <= MAX_NUDGE_TILES


def test_a_pole_never_steps_onto_the_route_it_is_clearing() -> None:
    """Moving out of the way and onto the same corridor solves nothing."""
    corridor = {(x, 10) for x in range(0, 20)}

    move = _move(keep_clear=corridor)

    assert move is not None
    assert (int(move.new[0]), int(move.new[1])) not in corridor


def test_a_pole_never_steps_onto_an_occupied_tile() -> None:
    blocked = {(x, y) for x in range(8, 14) for y in range(8, 14)} - {(12, 10)}

    move = _move(blocked=blocked, keep_clear={(10, 10)})

    assert move is not None
    assert (int(move.new[0]), int(move.new[1])) == (12, 10)


def test_a_move_that_would_unpower_a_consumer_is_refused() -> None:
    """Machines at opposite edges of the supply area pin the pole in place."""
    supply = POLE_SPECS[_MEDIUM]["supply"]
    corner = (_POLE[0] - supply, _POLE[1] - supply)
    opposite = (_POLE[0] + supply, _POLE[1] + supply)

    assert _move(supplied=(corner, opposite), keep_clear={(10, 10)}) is None


def test_a_move_that_keeps_every_consumer_covered_is_allowed() -> None:
    move = _move(supplied=((10.5, 10.5),), keep_clear={(10, 10)})

    assert move is not None
    assert keeps_supply(move.new, ((10.5, 10.5),), POLE_SPECS[_MEDIUM]["supply"])


def test_supply_area_is_treated_as_a_square_not_a_circle() -> None:
    """The supply area is a square, so a radius check would reject legal corner
    positions and accept illegal edge ones."""
    corner = (3.4, 3.4)  # inside the square, outside a radius-3.5 circle

    assert keeps_supply((0.0, 0.0), (corner,), 3.5)


def test_a_move_out_of_wire_reach_of_a_neighbour_is_refused() -> None:
    """A pole linking two halves of a network keeps them linked only if BOTH
    ends stay in reach -- checking the nearest alone cuts the base in two."""
    wire = POLE_SPECS[_MEDIUM]["wire"]
    limit = wire - WIRE_MARGIN
    # Neighbours at the margin on opposite sides: the pole is pinned, because
    # any step at all pulls one of them out of reach.
    east = (_POLE[0] + limit, _POLE[1])
    west = (_POLE[0] - limit, _POLE[1])

    assert keeps_wire(_POLE, (east, west), wire), "it reaches both where it stands"
    assert not keeps_wire((_POLE[0] + 1, _POLE[1]), (east, west), wire)
    assert not keeps_wire((_POLE[0], _POLE[1] + 1), (east, west), wire)
    assert _move(neighbours=(east, west), keep_clear={(10, 10)}) is None


def test_a_pole_may_move_toward_a_distant_neighbour() -> None:
    """Refusing every move near a far neighbour would be too conservative --
    stepping toward it is fine, and is often the only clear direction."""
    wire = POLE_SPECS[_MEDIUM]["wire"]
    far_west = (_POLE[0] - wire + 2, _POLE[1])

    move = _move(neighbours=(far_west,), keep_clear={(10, 10)})

    assert move is not None
    assert keeps_wire(move.new, (far_west,), wire)


def test_wire_reach_is_held_inside_a_margin() -> None:
    wire = POLE_SPECS[_MEDIUM]["wire"]

    assert keeps_wire((0.0, 0.0), ((wire - WIRE_MARGIN, 0.0),), wire)
    assert not keeps_wire((0.0, 0.0), ((wire, 0.0),), wire)


def test_a_pole_with_no_neighbours_is_unconstrained_by_wire() -> None:
    assert keeps_wire((99.0, 99.0), (), POLE_SPECS[_MEDIUM]["wire"])


@pytest.mark.parametrize("pole", ["substation", "big-electric-pole"])
def test_a_two_by_two_pole_is_never_relocated_for_a_belt(pole: str) -> None:
    """Moving a substation is a network decision, not a routing one."""
    assert POLE_SPECS[pole]["size"] == 2
    assert choose_pole_move(
        pole, _POLE, supplied=(), neighbours=(), blocked=set(), keep_clear=set(),
    ) is None


def test_an_unknown_pole_is_left_alone() -> None:
    assert choose_pole_move(
        "mystery-pole", _POLE, supplied=(), neighbours=(),
        blocked=set(), keep_clear=set(),
    ) is None


def test_the_replacement_is_placed_before_the_original_is_removed() -> None:
    """Removing first drops every consumer in the old supply area, and a machine
    that loses power mid-build reports as blocked and drags a stage into
    diagnosis."""
    actions = relocation_actions(PoleMove(_MEDIUM, _POLE, (11.5, 10.5)))

    assert [a["action_type"] for a in actions] == ["place_entity", "remove_entity"]
    assert actions[0]["position"] == {"x": 11.5, "y": 10.5}
    assert actions[1]["position"] == {"x": 10.5, "y": 10.5}


def test_a_relocation_plan_is_a_valid_build_plan() -> None:
    plan = relocation_plan([
        PoleMove(_MEDIUM, _POLE, (11.5, 10.5)),
        PoleMove(_MEDIUM, (20.5, 5.5), (20.5, 6.5)),
    ])
    plan["surface"], plan["force"] = "nauvis", "player"

    validate_build_plan(plan)
    assert len(plan["phases"][0]["actions"]) == 4


def test_an_empty_relocation_is_refused_rather_than_submitted() -> None:
    with pytest.raises(ValueError, match="at least one move"):
        relocation_plan([])


def test_the_corridor_covers_both_elbows() -> None:
    """The router takes whichever L is clearer, so a pole nudged off one and
    onto the other has not moved out of the way at all."""
    corridor = corridor_tiles((0.5, 0.5), (4.5, 3.5))

    assert (4, 0) in corridor and (0, 3) in corridor   # both elbows
    assert (2, 0) in corridor and (2, 3) in corridor   # both horizontals
    assert (2, 2) not in corridor                      # not the whole box


def test_relocation_is_only_attempted_after_a_route_fails() -> None:
    """Moving live infrastructure is a last resort, not a first move."""
    source = inspect.getsource(stage_transport.ensure_ingredient_transport)
    body = source[source.index("except StuckError as blocked_route:"):]

    assert "relocate_blocking_poles(" in body
    assert "raise" in body, "an unmovable blockage must still fail"


def test_a_route_that_succeeds_never_touches_a_pole() -> None:
    source = inspect.getsource(stage_transport.ensure_ingredient_transport)
    before = source[:source.index("except StuckError as blocked_route:")]

    assert "relocate_blocking_poles(" not in before
