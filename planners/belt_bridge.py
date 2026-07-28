# Path: planners/belt_bridge.py
# Purpose: Deterministic belt+inserter connections between two existing chests (real-base stage-to-stage links).

from __future__ import annotations

import math
from typing import Sequence

from planners.infrastructure_geometry import choose_clear_l_route

Point = tuple[float, float]

DIRECTION_VECTORS = {
    "north": (0.0, -1.0), "south": (0.0, 1.0), "east": (1.0, 0.0), "west": (-1.0, 0.0),
}
_FACING_TO_VECTOR = DIRECTION_VECTORS
_VECTOR_TO_FACING = {vector: name for name, vector in _FACING_TO_VECTOR.items()}
_OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}

# Max distance between an underground belt's entry and exit, per tier. Queried
# live against Factorio 2.0.77:
#   /sc prototypes.entity[name].max_underground_distance
# A span of N covers N-1 obstructed tiles, so yellow (5) tunnels under 4.
UNDERGROUND_REACH = {
    "transport-belt": 5,
    "fast-transport-belt": 7,
    "express-transport-belt": 9,
    "turbo-transport-belt": 11,
}


# Tiles per second an item travels on each belt tier. Queried live against
# Factorio 2.0.77 (prototypes.entity[name].belt_speed * 60). Used to work out
# how long the FIRST item takes to cross a bridge, which is dead time before a
# fed stage can possibly run.
BELT_SPEEDS = {
    "transport-belt": 1.875,
    "fast-transport-belt": 3.75,
    "express-transport-belt": 5.625,
    "turbo-transport-belt": 7.5,
}


def transit_seconds(belt_type: str, tiles: float) -> float:
    """How long the first item needs to traverse `tiles` of `belt_type`."""
    return tiles / BELT_SPEEDS[belt_type]


def _tile(point: Point) -> tuple[int, int]:
    return (math.floor(point[0]), math.floor(point[1]))


def opposite(direction: str) -> str:
    """The facing 180 degrees from `direction`. Public because callers that
    place a bridge also have to reason about its two ends (see
    orchestrator.autonomous_builder), and one canonical map beats two."""
    try:
        return _OPPOSITE[direction]
    except KeyError:
        raise ValueError(f"Unknown direction: {direction!r}") from None


def _scaled(vector: tuple[float, float], scale: float) -> Point:
    return (vector[0] * scale, vector[1] * scale)


def _add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def _leg_direction(start: Point, end: Point) -> str:
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == 0 and dy == 0:
        raise ValueError("belt leg needs distinct start and end points")
    if dx != 0 and dy != 0:
        raise ValueError(f"belt leg must be axis-aligned: {start} -> {end}")
    axis = (1.0 if dx > 0 else -1.0, 0.0) if dx != 0 else (0.0, 1.0 if dy > 0 else -1.0)
    return _VECTOR_TO_FACING[axis]


def _aligned_final_route(
    source_belt: Point, belt_end: Point, entry_direction: str,
    blocked_tiles: set[tuple[int, int]] | None,
    exit_direction: str | None = None,
) -> list[Point]:
    """Route to a belt endpoint through a straight, aligned final leg.

    Underground belts must be paired on one axis with one facing.  Asking the
    L-route chooser to end at a corner lets the last tunnel inherit a turn or
    line up with a nearby unrelated endpoint.  One extra clear approach tile
    makes the final leg deterministic for both belt-to-belt and belt-to-chest
    bridges.
    """
    entry_vector = _FACING_TO_VECTOR[entry_direction]
    approach = _add(belt_end, entry_vector)
    if exit_direction is None:
        route = choose_clear_l_route(
            source_belt, approach, 1.0, 1.0, blocked_tiles,
        )
    else:
        if exit_direction not in _FACING_TO_VECTOR:
            raise ValueError(f"Unknown belt exit direction: {exit_direction!r}")
        exit_vector = _FACING_TO_VECTOR[exit_direction]
        forced = _add(source_belt, _scaled(exit_vector, 2))
        # Keep the existing source and side-tap tiles facing with the mine.
        # The following tile owns the turn toward the destination.
        turn = (
            (forced[0], approach[1])
            if exit_direction in {"east", "west"}
            else (approach[0], forced[1])
        )
        route = []
        for point in (source_belt, forced, turn, approach):
            if not route or point != route[-1]:
                route.append(point)
    if route[-1] != belt_end:
        route.append(belt_end)
    return route


def bridge_belt_to_chest(
    source_belt: Point,
    dest_position: Point,
    *,
    entry_direction: str,
    belt_type: str = "fast-transport-belt",
    inserter_type: str = "fast-inserter",
    blocked_tiles: set[tuple[int, int]] | None = None,
    max_route_tiles: int | None = None,
) -> list[dict]:
    """Continue an existing output belt into a destination chest.

    Unlike chest-to-chest transport, this has no source inserter: production
    stays on the belt while a side chest samples it for logistic consumers.
    """
    if entry_direction not in _FACING_TO_VECTOR:
        raise ValueError(f"Unknown direction: {entry_direction!r}")
    entry_vector = _FACING_TO_VECTOR[entry_direction]
    dest_inserter = _add(dest_position, _scaled(entry_vector, 1))
    belt_end = _add(dest_position, _scaled(entry_vector, 2))
    route = _aligned_final_route(
        source_belt, belt_end, entry_direction, blocked_tiles,
    )
    route_tiles = len(_route_points(route))
    if max_route_tiles is not None and route_tiles > max_route_tiles:
        raise ValueError(
            f"Generated belt route needs {route_tiles} tiles, beyond the "
            f"{max_route_tiles}-tile local-mode limit; CityPlanner rail handoff "
            "is required"
        )
    actions = [{
        "action_type": "place_entity", "entity": inserter_type,
        "position": {"x": dest_inserter[0], "y": dest_inserter[1]},
        "direction": entry_direction,
    }]
    actions.extend(_belt_run(route, belt_type, blocked_tiles or set()))
    return actions

def bridge_belt_to_belt(
    source_belt: Point,
    dest_belt: Point,
    *,
    entry_direction: str,
    belt_type: str = "fast-transport-belt",
    blocked_tiles: set[tuple[int, int]] | None = None,
    max_route_tiles: int | None = None,
    exit_direction: str | None = None,
) -> list[dict]:
    """Continue one belt into the free tile beside another belt."""
    entry_vector = _FACING_TO_VECTOR[entry_direction]
    belt_end = _add(dest_belt, entry_vector)
    route = _aligned_final_route(
        source_belt, belt_end, entry_direction, blocked_tiles,
        exit_direction=exit_direction,
    )
    route_tiles = len(_route_points(route))
    if max_route_tiles is not None and route_tiles > max_route_tiles:
        raise ValueError(
            f"Generated belt route needs {route_tiles} tiles, beyond the "
            f"{max_route_tiles}-tile local-mode limit"
        )
    blocked = blocked_tiles or set()
    actions = _belt_run(route, belt_type, blocked)
    actions.extend(_turn_buffer_actions(route, blocked, actions))
    return actions


def _turn_buffer_actions(
    route: Sequence[Point],
    blocked_tiles: set[tuple[int, int]],
    belt_actions: Sequence[dict],
) -> list[dict]:
    """Add a side reserve only where both inserters touch surface belts."""
    points: list[Point] = []
    for point in route:
        if not points or point != points[-1]:
            points.append(point)
    route_tiles = {_tile(position) for position, _, _ in _route_points(points)}
    for previous, corner, following in zip(points, points[1:], points[2:]):
        incoming = _leg_direction(previous, corner)
        outgoing = _leg_direction(corner, following)
        if incoming == outgoing:
            continue
        incoming_vector = _FACING_TO_VECTOR[incoming]
        outgoing_vector = _FACING_TO_VECTOR[outgoing]
        chest = _add(_add(corner, _scaled(incoming_vector, -2)), _scaled(outgoing_vector, 2))
        input_inserter = _add(_add(corner, _scaled(incoming_vector, -2)), outgoing_vector)
        output_inserter = _add(_add(corner, _scaled(incoming_vector, -1)), _scaled(outgoing_vector, 2))
        reserve_tiles = {_tile(chest), _tile(input_inserter), _tile(output_inserter)}
        if reserve_tiles & (blocked_tiles | route_tiles):
            continue
        surface_belts = {
            _tile((action["position"]["x"], action["position"]["y"]))
            for action in belt_actions if action.get("entity") in UNDERGROUND_REACH
        }
        input_pickup = _add(corner, _scaled(incoming_vector, -2))
        output_drop = _add(corner, _scaled(outgoing_vector, 2))
        if {_tile(input_pickup), _tile(output_drop)} - surface_belts:
            continue
        return [
            {"action_type": "place_entity", "entity": "steel-chest",
             "position": {"x": chest[0], "y": chest[1]}},
            {"action_type": "place_entity", "entity": "inserter",
             "position": {"x": input_inserter[0], "y": input_inserter[1]},
             "direction": opposite(outgoing)},
            {"action_type": "place_entity", "entity": "inserter",
             "position": {"x": output_inserter[0], "y": output_inserter[1]},
             "direction": opposite(incoming)},
        ]
    return []


def bridge_chest_to_chest(
    source_position: Point,
    dest_position: Point,
    *,
    exit_direction: str,
    entry_direction: str,
    belt_type: str = "fast-transport-belt",
    inserter_type: str = "fast-inserter",
    blocked_tiles: set[tuple[int, int]] | None = None,
    belt_spacing: float = 1.0,
    max_route_tiles: int | None = None,
) -> list[dict]:
    """Real entities moving one item from `source_position` chest to `dest_position` chest.

    `exit_direction` is the side of the source chest the belt departs from (an
    inserter sits one tile that way and unloads the chest onto the belt);
    `entry_direction` is the side of the dest chest the belt arrives from (an
    inserter one tile that way loads the belt onto the chest). Both are one of
    "north"/"south"/"east"/"west". Verified live this session: an inserter's
    pickup tile is on its facing side, its drop tile on the opposite side --
    so the loading inserter faces the source chest and the unloading inserter
    faces away from the dest chest, each one tile off it.

    Route between the two belt endpoints uses the SAME obstacle-aware L-route
    chooser the power spine uses (`choose_clear_l_route`), so a stage-to-stage
    connection dodges anything already built between them instead of assuming
    open ground.
    """
    if exit_direction not in _FACING_TO_VECTOR or entry_direction not in _FACING_TO_VECTOR:
        raise ValueError(f"Unknown direction: {exit_direction!r} / {entry_direction!r}")

    # An inserter's pickup tile is on its facing side, its drop on the opposite
    # side (live-verified). Both endpoints here place the belt on the named
    # side of their chest (`exit_direction`/`entry_direction` mean the same
    # thing: which side of that chest the belt sits on) -- so the source
    # inserter must face the CHEST (opposite of exit_direction, so pickup
    # lands on the chest and drop lands on the belt), and the dest inserter
    # must face the BELT side, i.e. entry_direction itself (so pickup lands on
    # the belt and drop lands on the chest). Cross-checked live against an
    # existing chest-draining inserter this session: chest north of it,
    # facing south, pickup=chest/drop=belt -- matches this formula exactly.
    exit_vector = _FACING_TO_VECTOR[exit_direction]
    entry_vector = _FACING_TO_VECTOR[entry_direction]
    source_inserter = _add(source_position, _scaled(exit_vector, 1))
    belt_start = _add(source_position, _scaled(exit_vector, 2))
    dest_inserter = _add(dest_position, _scaled(entry_vector, 1))
    belt_end = _add(dest_position, _scaled(entry_vector, 2))

    actions: list[dict] = [
        {"action_type": "place_entity", "entity": inserter_type,
         "position": {"x": source_inserter[0], "y": source_inserter[1]}, "direction": _OPPOSITE[exit_direction]},
        {"action_type": "place_entity", "entity": inserter_type,
         "position": {"x": dest_inserter[0], "y": dest_inserter[1]}, "direction": entry_direction},
    ]

    route = choose_clear_l_route(belt_start, belt_end, belt_spacing, 1.0, blocked_tiles)
    route_tiles = len(_route_points(route))
    if max_route_tiles is not None and route_tiles > max_route_tiles:
        raise ValueError(
            f"Generated belt route needs {route_tiles} tiles, beyond the "
            f"{max_route_tiles}-tile local-mode limit; CityPlanner rail handoff "
            "is required"
        )
    actions.extend(_belt_run(route, belt_type, blocked_tiles or set()))
    return actions


def _route_points(route: Sequence[Point]) -> list[tuple[Point, str, int]]:
    """(tile, direction, leg index) along the route, corners appearing once.

    A belt tile faces the direction items LEAVE it in, so the corner tile must
    carry the OUTGOING leg's direction: a tile facing east at the end of an
    eastward leg runs items off the end instead of turning them south. Each leg
    therefore emits start-inclusive, end-exclusive -- the corner belongs to the
    leg that departs from it -- and the route's final tile is appended with the
    last leg's direction.
    """
    points: list[tuple[Point, str, int]] = []
    for leg_index, (leg_start, leg_end) in enumerate(zip(route, route[1:])):
        direction = _leg_direction(leg_start, leg_end)
        vector = _FACING_TO_VECTOR[direction]
        length = int(round(abs(leg_end[0] - leg_start[0]) + abs(leg_end[1] - leg_start[1])))
        for step in range(length):
            points.append((_add(leg_start, _scaled(vector, step)), direction, leg_index))
    last_start, last_end = route[-2], route[-1]
    points.append((tuple(last_end), _leg_direction(last_start, last_end), len(route) - 2))
    return points


def _belt_run(
    route: Sequence[Point], belt_type: str, blocked: set[tuple[int, int]],
) -> list[dict]:
    """Surface belt over free ground, tunnelling under anything in the way.

    `choose_clear_l_route` returns the route with the FEWEST collisions, not a
    guaranteed-clear one, so a long cross-base run will still meet obstacles.
    Rather than demand they be removed, each unbroken run of blocked tiles
    becomes an underground-belt pair: entry on the last free tile before it,
    exit on the first free tile after. The pair's span must fit the tier's
    reach (live-verified UNDERGROUND_REACH), and a tunnel cannot turn, so a
    blocked corner or a run too long to span is reported instead of silently
    emitting a belt that cannot be built.
    """
    underground = belt_type.replace("transport-belt", "underground-belt")
    reach = UNDERGROUND_REACH[belt_type]
    points = _route_points(route)
    actions: list[dict] = []
    index = 0
    while index < len(points):
        point, direction, leg = points[index]
        if _tile(point) not in blocked:
            actions.append({
                "action_type": "place_ghost", "entity": belt_type,
                "position": {"x": point[0], "y": point[1]}, "direction": direction,
            })
            index += 1
            continue

        span_end = index
        while span_end < len(points) and _tile(points[span_end][0]) in blocked:
            span_end += 1
        if index == 0 or span_end >= len(points):
            raise ValueError(
                f"Belt route is blocked at its {'start' if index == 0 else 'end'} "
                f"({point}); a tunnel needs a free tile on both sides"
            )
        entry_point, entry_direction, entry_leg = points[index - 1]
        exit_point, _, exit_leg = points[span_end]
        if not (entry_leg == leg == exit_leg):
            raise ValueError(
                f"Blocked tiles at {point} sit on a corner of the belt route; an "
                "underground belt cannot turn"
            )
        distance = int(round(abs(exit_point[0] - entry_point[0]) + abs(exit_point[1] - entry_point[1])))
        if distance > reach:
            raise ValueError(
                f"Belt route needs a {distance}-tile tunnel at {entry_point}, beyond "
                f"{underground}'s {reach}-tile reach; route around or use a higher tier"
            )
        # Replace the surface belt already emitted for the entry tile.
        actions.pop()
        actions.append({
            "action_type": "place_ghost", "entity": underground,
            "position": {"x": entry_point[0], "y": entry_point[1]},
            "direction": entry_direction, "underground_type": "input",
        })
        actions.append({
            "action_type": "place_ghost", "entity": underground,
            "position": {"x": exit_point[0], "y": exit_point[1]},
            "direction": entry_direction, "underground_type": "output",
        })
        index = span_end + 1
    return actions


def bridge_blocked_tiles(actions: Sequence[dict]) -> set[tuple[int, int]]:
    """Tile indices a set of existing actions occupy, for `blocked_tiles` input."""
    import math

    return {
        (math.floor(action["position"]["x"]), math.floor(action["position"]["y"]))
        for action in actions
        if "position" in action
    }
