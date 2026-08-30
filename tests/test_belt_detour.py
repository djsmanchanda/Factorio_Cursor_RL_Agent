# Path: tests/test_belt_detour.py
# Purpose: Prove a belt route steps around an obstruction it cannot tunnel under, instead of ending the run.

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from planners.belt_bridge import (  # noqa: E402
    UNDERGROUND_REACH,
    _route_points,
    bridge_belt_to_belt,
    search_clear_route,
)


def _path(route):
    return [point for point, _direction, _leg in _route_points(route)]


def test_it_goes_around_an_obstruction_no_tier_can_tunnel() -> None:
    """The live run-killer: 'Belt route needs a 25-tile tunnel ... beyond
    turbo-underground-belt's 11-tile reach'."""
    obstacle = {(x, 0) for x in range(6, 31)}
    assert len(obstacle) > max(UNDERGROUND_REACH.values())

    route = search_clear_route((0.5, 0.5), (40.5, 0.5), obstacle)

    assert route is not None
    assert not ({(int(x // 1), int(y // 1)) for x, y in _path(route)} & obstacle)


def test_the_route_actually_connects_its_endpoints() -> None:
    route = search_clear_route((0.5, 0.5), (20.5, 8.5), {(x, 4) for x in range(-5, 15)})

    assert route[0] == (0.5, 0.5)
    assert route[-1] == (20.5, 8.5)
    steps = _path(route)
    for previous, point in zip(steps, steps[1:]):
        assert abs(point[0] - previous[0]) + abs(point[1] - previous[1]) == 1, "contiguous"


def test_a_clear_run_stays_straight() -> None:
    """A turn costs more than a tile, so an unobstructed route is one leg."""
    route = search_clear_route((0.5, 0.5), (20.5, 0.5), set())

    assert route == [(0.5, 0.5), (20.5, 0.5)]


def test_it_prefers_few_turns_over_the_shortest_wiggle() -> None:
    route = search_clear_route((0.5, 0.5), (20.5, 6.5), set())

    assert len(route) <= 3, f"one corner expected, got {route}"


def test_the_final_approach_direction_can_be_required() -> None:
    """A belt has to enter its destination facing the right way."""
    route = search_clear_route((0.5, 0.5), (20.5, 6.5), set(), final_direction="south")
    steps = _path(route)

    assert steps[-1][1] - steps[-2][1] == 1, "arrives heading south"


def test_a_sealed_destination_is_reported_rather_than_guessed() -> None:
    wall = {(x, y) for x in range(6, 31) for y in range(-60, 61)}

    assert search_clear_route((0.5, 0.5), (40.5, 0.5), wall) is None


def test_a_blocked_endpoint_is_not_a_route() -> None:
    assert search_clear_route((0.5, 0.5), (10.5, 0.5), {(10, 0)}) is None
    assert search_clear_route((0.5, 0.5), (10.5, 0.5), {(0, 0)}) is None


def test_the_same_inputs_always_give_the_same_belt() -> None:
    """Planning is deterministic; a tie must not depend on heap ordering."""
    obstacle = {(x, 0) for x in range(6, 31)}
    routes = [search_clear_route((0.5, 0.5), (40.5, 0.5), obstacle) for _ in range(5)]

    assert all(route == routes[0] for route in routes)


def test_the_bridge_falls_back_to_a_detour_and_reports_when_it_cannot() -> None:
    obstacle = {(x, 0) for x in range(6, 31)}
    actions = bridge_belt_to_belt(
        (0.5, 0.5), (40.5, 0.5), entry_direction="west",
        belt_type="turbo-transport-belt", blocked_tiles=obstacle,
    )
    assert actions

    wall = {(x, y) for x in range(6, 31) for y in range(-60, 61)}
    with pytest.raises(ValueError, match="no belt route is available"):
        bridge_belt_to_belt(
            (0.5, 0.5), (40.5, 0.5), entry_direction="west",
            belt_type="turbo-transport-belt", blocked_tiles=wall,
        )
def test_belt_detour_preserves_the_declared_source_exit_direction() -> None:
    actions = bridge_belt_to_belt(
        (0.5, 0.5), (40.5, 0.5), entry_direction="west",
        exit_direction="west", belt_type="transport-belt",
        blocked_tiles={(x, 0) for x in range(1, 31)},
    )
    first = next(
        action for action in actions
        if action["position"] == {"x": 0.5, "y": 0.5}
    )
    assert first["direction"] == "west"