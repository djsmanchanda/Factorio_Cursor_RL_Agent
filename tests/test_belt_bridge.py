# Path: tests/test_belt_bridge.py
# Purpose: Chest-to-chest belt bridges must tunnel under obstacles instead of demolishing them, within the real per-tier underground reach.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from planners.belt_bridge import (
    UNDERGROUND_REACH,
    bridge_belt_to_belt,
    bridge_chest_to_chest,
    opposite,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas" / "build_plan.schema.json").read_text(encoding="utf-8"))


def _tiles(actions, entity_substring: str) -> set[tuple[int, int]]:
    return {
        (int(a["position"]["x"] // 1), int(a["position"]["y"] // 1))
        for a in actions
        if entity_substring in a["entity"]
    }


def _bridge(**kwargs):
    defaults = dict(
        source_position=(0.5, 0.5), dest_position=(16.5, 0.5),
        exit_direction="east", entry_direction="west",
        belt_type="fast-transport-belt", inserter_type="fast-inserter",
    )
    defaults.update(kwargs)
    source = defaults.pop("source_position")
    dest = defaults.pop("dest_position")
    return bridge_chest_to_chest(source, dest, **defaults)


def test_underground_reach_matches_the_live_prototypes() -> None:
    """Queried live against Factorio 2.0.77 via
    prototypes.entity[name].max_underground_distance. A wrong value here emits
    a tunnel the game silently refuses to connect."""
    assert UNDERGROUND_REACH == {
        "transport-belt": 5,
        "fast-transport-belt": 7,
        "express-transport-belt": 9,
        "turbo-transport-belt": 11,
    }


def test_clear_route_uses_no_underground_belt() -> None:
    actions = _bridge()
    assert not _tiles(actions, "underground-belt")
    assert _tiles(actions, "transport-belt")


def test_obstacles_are_tunnelled_under_not_built_over() -> None:
    """The live failure this fixes: the route ran a surface belt straight
    through an electric furnace it had just built."""
    blocked = {(5, 0), (6, 0), (7, 0)}
    actions = _bridge(blocked_tiles=blocked)

    assert not (_tiles(actions, "transport-belt") - _tiles(actions, "underground-belt")) & blocked
    tunnel = [a for a in actions if "underground-belt" in a["entity"]]
    assert [a["underground_type"] for a in tunnel] == ["input", "output"]
    entry, exit_ = tunnel[0]["position"], tunnel[1]["position"]
    span = abs(exit_["x"] - entry["x"]) + abs(exit_["y"] - entry["y"])
    assert span <= UNDERGROUND_REACH["fast-transport-belt"]
    # entry/exit straddle the obstacle run
    assert entry["x"] < 5 and exit_["x"] > 7
    assert tunnel[0]["direction"] == tunnel[1]["direction"] == "east"


def test_a_run_longer_than_the_tier_reach_is_reported_not_emitted() -> None:
    with pytest.raises(ValueError, match="beyond .*underground-belt's 7-tile reach"):
        _bridge(blocked_tiles={(x, 0) for x in range(4, 13)})


def test_a_higher_tier_spans_what_a_lower_tier_cannot() -> None:
    """Reach is per tier, so the same obstacle run is only a hard failure
    relative to the belt being used."""
    blocked = {(x, 0) for x in range(4, 12)}
    with pytest.raises(ValueError):
        _bridge(belt_type="fast-transport-belt", blocked_tiles=blocked)
    actions = _bridge(belt_type="turbo-transport-belt", blocked_tiles=blocked)
    assert _tiles(actions, "underground-belt")


def test_a_blocked_endpoint_is_reported_because_a_tunnel_needs_both_sides() -> None:
    with pytest.raises(ValueError, match="needs a free tile on both sides"):
        _bridge(blocked_tiles={(13, 0), (14, 0)})


def test_emitted_actions_validate_against_the_build_plan_schema() -> None:
    """Underground belts require underground_type in the schema; a bridge that
    omits it is rejected at execution rather than at plan time."""
    plan = {"phases": [{"name": "bridge", "actions": _bridge(blocked_tiles={(5, 0), (6, 0)})}]}
    assert not list(Draft7Validator(SCHEMA).iter_errors(plan))


def test_a_corner_faces_the_direction_items_leave_it_in() -> None:
    """Found live: the corner tile carried the INCOMING leg's direction, so a
    belt arriving eastbound stayed eastbound at the turn and ran items off the
    end instead of sending them down the next leg."""
    from planners.belt_bridge import _route_points

    points = _route_points([(0.5, 0.5), (3.5, 0.5), (3.5, 2.5)])
    facings = {tile: direction for tile, direction, _leg in points}
    assert facings[(3.5, 0.5)] == "south", "corner must face the outgoing leg"
    assert facings[(2.5, 0.5)] == "east", "the tile before the corner still runs east"
    assert facings[(3.5, 2.5)] == "south"
    # every tile appears exactly once -- a doubled corner would be two belts
    assert len(points) == len({tile for tile, _d, _l in points})


def test_every_belt_tile_feeds_into_the_next_one() -> None:
    """End-to-end orientation check: following each belt's facing must land on
    the next belt in the run, which is what makes items actually flow."""
    from planners.belt_bridge import DIRECTION_VECTORS, _route_points

    points = _route_points([(0.5, 0.5), (6.5, 0.5), (6.5, 4.5), (9.5, 4.5)])
    for (tile, direction, _leg), (next_tile, _nd, _nl) in zip(points, points[1:]):
        vx, vy = DIRECTION_VECTORS[direction]
        assert (round(tile[0] + vx, 1), round(tile[1] + vy, 1)) == next_tile, (
            f"belt at {tile} facing {direction} does not feed {next_tile}"
        )


def test_mine_side_tap_stays_westbound_before_the_bridge_turns_south() -> None:
    actions = bridge_belt_to_belt(
        (78.5, -39.5), (77.5, -18.5),
        entry_direction="north", exit_direction="west",
        belt_type="transport-belt", blocked_tiles=set(),
    )
    facings = {
        (action["position"]["x"], action["position"]["y"]): action["direction"]
        for action in actions
        if action.get("entity") == "transport-belt"
    }
    assert facings[(78.5, -39.5)] == "west"
    assert facings[(77.5, -39.5)] == "west"
    assert facings[(76.5, -39.5)] == "south"


def test_opposite_rejects_an_unknown_facing() -> None:
    assert opposite("north") == "south"
    with pytest.raises(ValueError):
        opposite("sideways")


def test_generated_detour_cannot_cross_the_local_mode_route_limit(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "planners.belt_bridge.choose_clear_l_route",
        lambda *_args: [
            (2.5, 0.5), (2.5, 20.5), (294.5, 20.5), (294.5, 0.5),
        ],
    )

    with pytest.raises(ValueError, match="CityPlanner rail handoff"):
        _bridge(dest_position=(296.5, 0.5), max_route_tiles=300)


def test_belt_turn_gets_an_offline_reserve_chest() -> None:
    actions = bridge_belt_to_belt(
        (20.5, 0.5),
        (0.5, 20.5),
        entry_direction="north",
        belt_type="fast-transport-belt",
        blocked_tiles=set(),
    )

    assert {
        "action_type": "place_entity", "entity": "steel-chest",
        "position": {"x": 2.5, "y": 2.5},
    } in actions
    assert {
        "action_type": "place_entity", "entity": "inserter",
        "position": {"x": 2.5, "y": 1.5}, "direction": "north",
    } in actions
    assert {
        "action_type": "place_entity", "entity": "inserter",
        "position": {"x": 1.5, "y": 2.5}, "direction": "east",
    } in actions
    surface_belts = {
        (action["position"]["x"], action["position"]["y"])
        for action in actions if action.get("entity") == "fast-transport-belt"
    }
    assert (2.5, 0.5) in surface_belts  # buffer input pickup
    assert (0.5, 2.5) in surface_belts  # buffer output drop

def test_turn_buffer_is_skipped_when_its_output_would_hit_a_tunnel() -> None:
    actions = bridge_belt_to_belt(
        (20.5, 0.5),
        (0.5, 20.5),
        entry_direction="north",
        belt_type="fast-transport-belt",
        blocked_tiles={(0, 2)},
    )

    assert not any(action["entity"] == "steel-chest" for action in actions)
