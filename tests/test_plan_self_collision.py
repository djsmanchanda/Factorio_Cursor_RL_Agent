# Path: tests/test_plan_self_collision.py
# Purpose: Prove a plan is rejected when it overlaps ITSELF, and that a line's power scaffolding stays clear of feed columns that grow toward it.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planners.infrastructure_geometry import boxes_overlap  # noqa: E402
from planners.local_layout_planner import LocalLayoutPlanner  # noqa: E402
from planners.plan_validation import (  # noqa: E402
    ENTITY_FOOTPRINTS,
    actions,
    entity_footprint_tiles,
    validate_build_plan,
)

_SCAFFOLDING = {"substation", "electric-energy-interface"}
_SIZES = (1, 2, 3, 6, 12, 21, 50)


def _line(machines: int, origin=(0, 0)) -> dict:
    plan = LocalLayoutPlanner().generate_line_layout(
        "transport-belt", machines, origin[0], origin[1],
        belt_type="transport-belt", inserter_type="fast-inserter",
        feed_style="chest", terminal_collector=True, flow_direction="east",
    )
    plan["surface"], plan["force"] = "nauvis", "player"
    return plan


def _placed(plan: dict) -> list[dict]:
    return [action for action in actions(plan) if action.get("entity")]


def test_a_plan_that_overlaps_itself_is_rejected() -> None:
    """validate_no_collisions existed but was only ever called with SEPARATE
    plans, so a generator colliding with its own output passed unchecked."""
    plan = _line(6)
    plan["phases"][0]["actions"].append({
        "action_type": "place_entity", "entity": "substation",
        "position": {"x": 0.5, "y": 1.5},
    })

    with pytest.raises(ValueError, match="Plan collision"):
        validate_build_plan(plan)


def test_a_two_by_two_over_a_one_by_one_counts_as_a_collision() -> None:
    """The shape the live executor can never see: it matches entities whose
    CENTRE is identical, and a 2x2 over a 1x1 never has the same centre."""
    assert boxes_overlap((49.0, 47.0), 2, (49.5, 47.5), 1)
    assert (49.0, 47.0) != (49.5, 47.5)


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("north", frozenset({(9, 10), (10, 10)})),
        ("south", frozenset({(9, 10), (10, 10)})),
        ("east", frozenset({(10, 9), (10, 10)})),
        ("west", frozenset({(10, 9), (10, 10)})),
    ],
)
def test_splitter_footprint_rotates_with_belt_flow(
    direction: str, expected: frozenset[tuple[int, int]],
) -> None:
    action = {
        "action_type": "place_ghost",
        "entity": "splitter",
        "position": {"x": 10.0 if direction in {"north", "south"} else 10.5,
                     "y": 10.5 if direction in {"north", "south"} else 10.0},
        "direction": direction,
    }

    assert entity_footprint_tiles(action) == expected


def test_splitter_second_tile_participates_in_self_collision_validation() -> None:
    plan = {
        "phases": [{
            "name": "splitter_collision",
            "actions": [
                {
                    "action_type": "place_ghost",
                    "entity": "splitter",
                    "position": {"x": 10.5, "y": 10.0},
                    "direction": "east",
                },
                {
                    "action_type": "place_ghost",
                    "entity": "transport-belt",
                    "position": {"x": 10.5, "y": 9.5},
                    "direction": "east",
                },
            ],
        }],
    }

    with pytest.raises(ValueError, match="Plan collision"):
        validate_build_plan(plan)


@pytest.mark.parametrize("machines", _SIZES)
def test_a_line_never_collides_with_itself(machines: int) -> None:
    validate_build_plan(_line(machines))


@pytest.mark.parametrize("machines", _SIZES)
def test_power_scaffolding_clears_the_feed_columns(machines: int) -> None:
    """The feed columns grow westward with machine count -- x=-2.5 at three
    machines, -10.5 at twenty-one -- while the scaffolding was pinned at -7.5
    and -4.0. Past about nine machines a line grew into its own power.

    The scaffolding now sits SOUTH of those columns, which occupy only
    y=-1.5..2.5 west of the machines, so it stays clear at every size without
    having to outrun the line westward -- which it could not do and stay wired.
    """
    placed = _placed(_line(machines))
    columns = [
        action for action in placed
        if action["entity"] not in _SCAFFOLDING and action["position"]["x"] < 0.5
    ]

    for action in placed:
        if action["entity"] not in _SCAFFOLDING:
            continue
        for column in columns:
            assert not boxes_overlap(
                (action["position"]["x"], action["position"]["y"]),
                ENTITY_FOOTPRINTS.get(action["entity"], 1),
                (column["position"]["x"], column["position"]["y"]),
                ENTITY_FOOTPRINTS.get(column["entity"], 1),
            ), f"{action['entity']} over {column['entity']}"


@pytest.mark.parametrize("machines", _SIZES)
def test_the_substation_touches_nothing(machines: int) -> None:
    """Reported live: a substation sharing ground with a fast inserter and a
    steel chest."""
    placed = _placed(_line(machines))
    substation = next(a for a in placed if a["entity"] == "substation")
    box = (substation["position"]["x"], substation["position"]["y"])

    for other in placed:
        if other is substation:
            continue
        assert not boxes_overlap(
            box, ENTITY_FOOTPRINTS["substation"],
            (other["position"]["x"], other["position"]["y"]),
            ENTITY_FOOTPRINTS.get(other["entity"], 1),
        ), f"{machines} machines: substation over {other['entity']}"


@pytest.mark.parametrize("machines", _SIZES)
def test_the_substation_still_reaches_the_line(machines: int) -> None:
    """Moving it west must not put it out of wire reach of the line's poles --
    a substation just out of range reads as no_power on every machine."""
    placed = _placed(_line(machines))
    substation = next(a for a in placed if a["entity"] == "substation")
    poles = [a for a in placed if a["entity"] == "medium-electric-pole"]
    nearest = min(
        abs(pole["position"]["x"] - substation["position"]["x"])
        + abs(pole["position"]["y"] - substation["position"]["y"])
        for pole in poles
    )

    assert nearest <= 18, f"{machines} machines: nearest pole {nearest} tiles away"


def test_a_fluid_source_may_still_share_ground_with_its_own_pipe() -> None:
    """An offshore pump hands its output to a pipe standing ON its connector,
    which is an attachment rather than a fault."""
    from planners.plan_validation import is_verified_pumpjack_attachment

    assert is_verified_pumpjack_attachment(
        {"entity": "offshore-pump", "position": {"x": 5.5, "y": 30.5},
         "direction": "east"},
        {"entity": "pipe", "position": {"x": 6.5, "y": 30.5}},
    )


def test_two_unrelated_entities_are_never_excused() -> None:
    from planners.plan_validation import is_verified_pumpjack_attachment

    assert not is_verified_pumpjack_attachment(
        {"entity": "substation", "position": {"x": 5.5, "y": 30.5}},
        {"entity": "steel-chest", "position": {"x": 6.5, "y": 30.5}},
    )
