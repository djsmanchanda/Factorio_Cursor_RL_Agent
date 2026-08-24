# Path: tests/test_transport_occupancy.py
# Purpose: Define typed belt-route occupancy, exact reuse, and fail-closed route planning.

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planners.belt_bridge import (  # noqa: E402
    UNDERGROUND_REACH,
    RouteFailure,
    RoutePlan,
    plan_belt_route,
)
from planners.transport_occupancy import (  # noqa: E402
    Occupant,
    OccupantIdentity,
    RouteOccupancy,
)

Tile = tuple[int, int]

_DISTRICT = "iron-district"
_SOURCE = (0.5, 0.5)
_DESTINATION = (10.5, 0.5)


def _identity(entity_id: str, district_id: str = _DISTRICT) -> OccupantIdentity:
    return OccupantIdentity(district_id=district_id, entity_id=entity_id)


def _occupant(
    tile: Tile,
    category: str,
    *,
    name: str | None = None,
    direction: str | None = None,
    underground_type: str | None = None,
    identity: OccupantIdentity | None = None,
    allowed_entities: frozenset[str] = frozenset(),
) -> Occupant:
    return Occupant(
        category=category,
        tiles=frozenset({tile}),
        name=name,
        direction=direction,
        underground_type=underground_type,
        identity=identity,
        allowed_entities=allowed_entities,
    )


def _occupancy(*occupants: Occupant) -> RouteOccupancy:
    return RouteOccupancy(occupants=tuple(occupants))


def _plan(
    occupancy: RouteOccupancy | None = None,
    **overrides: object,
) -> RoutePlan | RouteFailure:
    arguments: dict[str, object] = {
        "source": _SOURCE,
        "destination": _DESTINATION,
        "belt_type": "transport-belt",
        "occupancy": occupancy or _occupancy(),
        "district_id": _DISTRICT,
        "source_heading": "east",
        "destination_heading": "east",
    }
    arguments.update(overrides)
    return plan_belt_route(**arguments)


def _action_tile(action: dict) -> Tile:
    position = action["position"]
    return math.floor(position["x"]), math.floor(position["y"])


def _surface_action_tiles(plan: RoutePlan) -> set[Tile]:
    return {
        _action_tile(action)
        for action in plan.actions
        if "position" in action and not action.get("underground_type")
    }


@pytest.mark.parametrize(
    ("category", "name"),
    [
        ("live_entity", "solar-panel"),
        ("live_entity", "medium-electric-pole"),
        ("entity_ghost", "transport-belt"),
        ("tile_ghost", "landfill"),
        ("terrain", "water"),
        ("deconstruction_order", "stone-furnace"),
        ("pending_plan", "transport-belt"),
        ("district_reservation", None),
    ],
)
def test_typed_occupants_block_surface_belt_placement(
    category: str, name: str | None,
) -> None:
    blocked = (4, 0)
    result = _plan(_occupancy(_occupant(blocked, category, name=name)))

    assert isinstance(result, RoutePlan)
    assert blocked not in _surface_action_tiles(result)


def test_source_and_destination_interfaces_are_legal_for_their_district() -> None:
    result = _plan(_occupancy(
        _occupant(
            (0, 0), "source_interface", name="transport-belt",
            direction="east", identity=_identity("source-interface"),
        ),
        _occupant(
            (10, 0), "destination_interface", name="transport-belt",
            direction="east", identity=_identity("destination-interface"),
        ),
    ))

    assert isinstance(result, RoutePlan)


def test_owner_may_build_inside_its_reservation_but_foreign_district_may_not() -> None:
    reserved = _occupant(
        (4, 0), "district_reservation",
        identity=_identity("mine-egress-reservation"),
        allowed_entities=frozenset({"transport-belt", "underground-belt"}),
    )

    result = _plan(_occupancy(reserved))

    assert isinstance(result, RoutePlan)
    assert (4, 0) in _surface_action_tiles(result)


def test_foreign_belt_is_an_obstacle_not_a_reusable_prototype_family() -> None:
    foreign = _occupant(
        (3, 0), "live_entity", name="transport-belt", direction="east",
        identity=_identity("foreign-belt", district_id="copper-district"),
    )

    result = _plan(_occupancy(foreign))

    assert isinstance(result, RoutePlan)
    assert result.reused_tiles == frozenset()
    assert (3, 0) not in _surface_action_tiles(result)


def test_only_exact_owned_direction_and_tier_compatible_belts_are_reused() -> None:
    owned = (
        _occupant(
            (1, 0), "live_entity", name="transport-belt", direction="east",
            identity=_identity("belt-1"),
        ),
        _occupant(
            (2, 0), "live_entity", name="transport-belt", direction="east",
            identity=_identity("belt-2"),
        ),
    )

    result = _plan(_occupancy(*owned))

    assert isinstance(result, RoutePlan)
    assert result.reused_tiles == frozenset({(1, 0), (2, 0)})
    assert not result.reused_tiles & _surface_action_tiles(result)


def test_exact_owned_underground_pair_is_reused_as_one_bounded_graph_edge() -> None:
    occupancy = _occupancy(
        _occupant(
            (3, 0), "live_entity", name="underground-belt", direction="east",
            underground_type="input", identity=_identity("tunnel-input"),
        ),
        _occupant(
            (7, 0), "live_entity", name="underground-belt", direction="east",
            underground_type="output", identity=_identity("tunnel-output"),
        ),
    )

    result = _plan(occupancy, fresh_occupancy=occupancy)

    assert isinstance(result, RoutePlan)
    assert {(3, 0), (7, 0)} <= result.reused_tiles
    assert ((3, 0), (7, 0)) in result.underground_spans
    assert not [
        action for action in result.actions if action.get("underground_type")
    ]


def test_foreign_or_mismatched_underground_endpoint_is_never_reused() -> None:
    occupancy = _occupancy(
        _occupant(
            (3, 0), "live_entity", name="underground-belt", direction="east",
            underground_type="input", identity=_identity("foreign-input", "copper"),
        ),
        _occupant(
            (7, 0), "live_entity", name="underground-belt", direction="west",
            underground_type="output", identity=_identity("wrong-output"),
        ),
    )

    result = _plan(occupancy)

    assert isinstance(result, RoutePlan)
    assert not ({(3, 0), (7, 0)} <= result.reused_tiles)


@pytest.mark.parametrize(
    ("name", "direction"),
    [("fast-transport-belt", "east"), ("transport-belt", "west")],
)
def test_owned_belt_with_incompatible_tier_or_direction_is_not_reused(
    name: str, direction: str,
) -> None:
    incompatible = _occupant(
        (3, 0), "live_entity", name=name, direction=direction,
        identity=_identity("incompatible-belt"),
    )

    result = _plan(_occupancy(incompatible))

    assert isinstance(result, RoutePlan)
    assert result.reused_tiles == frozenset()
    assert (3, 0) not in _surface_action_tiles(result)


def test_within_reach_obstacle_is_crossed_by_one_legal_underground_pair() -> None:
    occupancy = _occupancy(*(
        _occupant((x, 0), "live_entity", name="pipe") for x in (3, 4)
    ))

    result = _plan(occupancy)

    assert isinstance(result, RoutePlan)
    underground = [
        action for action in result.actions if action.get("underground_type")
    ]
    assert [action["underground_type"] for action in underground] == ["input", "output"]
    assert underground[0]["direction"] == underground[1]["direction"] == "east"
    span = abs(_action_tile(underground[1])[0] - _action_tile(underground[0])[0])
    assert span <= UNDERGROUND_REACH["transport-belt"]
    assert not {(3, 0), (4, 0)} & {_action_tile(action) for action in result.actions}


@pytest.mark.parametrize("reach", [0, -1, float("inf"), float("-inf"), float("nan")])
def test_unusable_injected_underground_reach_fails_closed(reach: float) -> None:
    result = _plan(underground_reach=reach)

    assert isinstance(result, RouteFailure)
    assert result.reason == "invalid_underground_reach"
    assert result.actions == ()


def test_injected_live_reach_bounds_every_underground_edge() -> None:
    occupancy = _occupancy(*(
        _occupant((x, 0), "live_entity", name="pipe") for x in (3, 4)
    ))

    result = _plan(occupancy, underground_reach=2)

    assert isinstance(result, RoutePlan)
    assert all(
        abs(exit_[0] - entrance[0]) + abs(exit_[1] - entrance[1]) <= 2
        for entrance, exit_ in result.underground_spans
    )


def test_obstacle_beyond_underground_reach_is_routed_around() -> None:
    obstacle = frozenset((x, 0) for x in range(3, 9))
    occupancy = _occupancy(*(
        _occupant(tile, "live_entity", name="pipe") for tile in obstacle
    ))

    result = _plan(
        occupancy, destination=(12.5, 0.5), max_route_tiles=30,
    )

    assert isinstance(result, RoutePlan)
    assert any(_action_tile(action)[1] != 0 for action in result.actions)
    assert not obstacle & _surface_action_tiles(result)
    underground = [
        action for action in result.actions if action.get("underground_type")
    ]
    for entrance, exit_ in zip(underground[::2], underground[1::2]):
        distance = sum(
            abs(a - b) for a, b in zip(_action_tile(entrance), _action_tile(exit_))
        )
        assert distance <= UNDERGROUND_REACH["transport-belt"]


def test_overlong_crossover_refuses_when_no_detour_fits_the_route_bound() -> None:
    wall = frozenset(
        (x, y) for x in range(3, 9) for y in range(-1, 2)
    )
    occupancy = _occupancy(*(
        _occupant(tile, "live_entity", name="pipe") for tile in wall
    ))

    result = _plan(
        occupancy, destination=(12.5, 0.5), max_route_tiles=14,
    )

    assert isinstance(result, RouteFailure)
    assert result.reason in {"no_legal_route", "route_limit"}
    assert result.actions == ()


@pytest.mark.parametrize(
    ("position", "reason"),
    [((0, 0), "blocked_source"), ((10, 0), "blocked_destination")],
)
def test_blocked_endpoint_returns_a_structured_zero_action_failure(
    position: Tile, reason: str,
) -> None:
    result = _plan(_occupancy(
        _occupant(position, "live_entity", name="substation"),
    ))

    assert isinstance(result, RouteFailure)
    assert result.reason == reason
    assert result.actions == ()


def test_required_source_exit_and_destination_entry_headings_are_honored() -> None:
    result = _plan(
        source=(0.5, 0.5), destination=(8.5, 4.5),
        source_heading="east", destination_heading="south",
    )

    assert isinstance(result, RoutePlan)
    actions = {_action_tile(action): action for action in result.actions}
    assert actions[(0, 0)]["direction"] == "east"
    assert actions[(8, 4)]["direction"] == "south"


def test_identical_inputs_produce_identical_route_plans() -> None:
    occupancy = _occupancy(*(
        _occupant((x, 0), "live_entity", name="pipe") for x in range(3, 9)
    ))

    results = [_plan(occupancy, destination=(12.5, 0.5)) for _ in range(5)]

    assert all(isinstance(result, RoutePlan) for result in results)
    assert all(result == results[0] for result in results[1:])


def test_fresh_occupancy_conflict_invalidates_the_plan_before_actions_escape() -> None:
    fresh = _occupancy(_occupant(
        (5, 0), "district_reservation",
        identity=_identity("late-reservation", district_id="copper-district"),
    ))

    result = _plan(fresh_occupancy=fresh)

    assert isinstance(result, RouteFailure)
    assert result.reason == "fresh_occupancy_conflict"
    assert result.actions == ()
