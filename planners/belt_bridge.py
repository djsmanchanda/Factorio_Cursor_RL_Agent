# Path: planners/belt_bridge.py
# Purpose: Deterministic belt+inserter connections between two existing chests (real-base stage-to-stage links).

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Sequence

from planners.infrastructure_geometry import choose_clear_l_route
from planners.transport_occupancy import RouteOccupancy

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
    include_endpoint: bool = True,
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
        forced = _add(source_belt, exit_vector)
        # Keep the existing source tile facing with the mine. The following
        # tile owns the turn toward the destination.
        turn = (
            (forced[0], approach[1])
            if exit_direction in {"east", "west"}
            else (approach[0], forced[1])
        )
        route = []
        for point in (source_belt, forced, turn, approach):
            if not route or point != route[-1]:
                route.append(point)
    if include_endpoint and route[-1] != belt_end:
        route.append(belt_end)
    return route


# Going AROUND beats demanding the way be cleared. choose_clear_l_route only
# scores a fixed set of L and Z shapes, so a cross-base run through a built-up
# area kept returning a route whose obstacles could not be tunnelled -- "blocked
# tiles sit on a corner", or a span past the tier's reach -- and the run died
# rather than stepping aside. A turn costs more than a tile so routes stay
# belt-shaped instead of becoming staircases.
_ROUTE_TURN_COST = 6
_ROUTE_SEARCH_MARGIN = 48.0
_ROUTE_SEARCH_LIMIT = 400_000

# The typed planner prices a crossover above ordinary belt travel while still
# keeping a legal short tunnel cheaper than a multi-turn surface detour.
_TYPED_ROUTE_TURN_COST = 6.0
_TYPED_UNDERGROUND_COST = 4.0
_TYPED_DEFAULT_EXTRA_TILES = 96

Tile = tuple[int, int]


@dataclass(frozen=True, slots=True)
class RoutePlan:
    """A deterministic, preflighted belt route ready for plan submission."""

    actions: tuple[dict, ...]
    route_tiles: tuple[Tile, ...]
    reused_tiles: frozenset[Tile]
    underground_spans: tuple[tuple[Tile, Tile], ...]
    underground_reach: int
    turns: int
    total_cost: float


@dataclass(frozen=True, slots=True)
class RouteFailure:
    """A fail-closed routing result. Failures never carry executable actions."""

    reason: str
    detail: str
    actions: tuple[dict, ...] = ()


@dataclass(frozen=True, slots=True)
class _TypedRouteLabel:
    tile: Tile
    incoming: str | None
    route_count: int
    action_count: int
    cost: float
    turns: int
    parent_id: int | None
    segment_actions: tuple[dict, ...]
    segment_tiles: tuple[Tile, ...]
    segment_reused: tuple[tuple[Tile, str, str | None], ...]
    underground_span: tuple[Tile, Tile] | None = None


def _tile_center(tile: Tile) -> Point:
    return tile[0] + 0.5, tile[1] + 0.5


def _surface_action(tile: Tile, belt_type: str, direction: str) -> dict:
    point = _tile_center(tile)
    return {
        "action_type": "place_ghost",
        "entity": belt_type,
        "position": {"x": point[0], "y": point[1]},
        "direction": direction,
    }


def _underground_action(
    tile: Tile, belt_type: str, direction: str, underground_type: str,
) -> dict:
    point = _tile_center(tile)
    return {
        "action_type": "place_ghost",
        "entity": belt_type.replace("transport-belt", "underground-belt"),
        "position": {"x": point[0], "y": point[1]},
        "direction": direction,
        "underground_type": underground_type,
    }


def _advance(tile: Tile, direction: str, distance: int = 1) -> Tile:
    vector = _FACING_TO_VECTOR[direction]
    return (
        tile[0] + int(vector[0]) * distance,
        tile[1] + int(vector[1]) * distance,
    )


def _typed_direction_order(incoming: str | None) -> tuple[str, ...]:
    fixed = ("east", "south", "west", "north")
    if incoming is None:
        return fixed
    return (incoming,) + tuple(
        direction
        for direction in fixed
        if direction != incoming and direction != _OPPOSITE[incoming]
    )


def _label_is_dominated(
    labels: Sequence[_TypedRouteLabel], candidate: _TypedRouteLabel,
) -> bool:
    return any(
        existing.cost <= candidate.cost
        and existing.route_count <= candidate.route_count
        and existing.action_count <= candidate.action_count
        for existing in labels
    )


def _reconstruct_typed_route(
    labels: dict[int, _TypedRouteLabel], goal_id: int,
    *,
    destination: Tile,
    destination_action: dict | None,
    destination_reuse: tuple[Tile, str, str | None] | None,
    total_cost: float,
) -> tuple[
    tuple[dict, ...], tuple[Tile, ...],
    tuple[tuple[Tile, str, str | None], ...],
    tuple[tuple[Tile, Tile], ...], int,
]:
    lineage: list[_TypedRouteLabel] = []
    label_id: int | None = goal_id
    while label_id is not None:
        label = labels[label_id]
        lineage.append(label)
        label_id = label.parent_id
    lineage.reverse()

    actions: list[dict] = []
    route_tiles: list[Tile] = []
    reused: list[tuple[Tile, str, str | None]] = []
    spans: list[tuple[Tile, Tile]] = []
    for label in lineage[1:]:
        actions.extend(label.segment_actions)
        route_tiles.extend(label.segment_tiles)
        reused.extend(label.segment_reused)
        if label.underground_span is not None:
            spans.append(label.underground_span)
    route_tiles.append(destination)
    if destination_action is not None:
        actions.append(destination_action)
    if destination_reuse is not None:
        reused.append(destination_reuse)
    # `total_cost` is deliberately accepted here to keep reconstruction's
    # inputs explicit when route metrics grow; turns come from the goal label.
    _ = total_cost
    return (
        tuple(actions), tuple(route_tiles), tuple(reused), tuple(spans),
        labels[goal_id].turns,
    )


def _fresh_typed_route_is_valid(
    occupancy: RouteOccupancy,
    *,
    actions: Sequence[dict],
    reused: Sequence[tuple[Tile, str, str | None]],
    district_id: str,
    belt_type: str,
) -> tuple[bool, str]:
    for action in actions:
        position = action["position"]
        tile = (math.floor(position["x"]), math.floor(position["y"]))
        if action.get("underground_type"):
            if not occupancy.underground_endpoint_is_clear(
                tile, district_id=district_id, belt_type=belt_type,
            ):
                return False, f"underground endpoint {tile} is no longer clear"
            continue
        interface = None
        disposition = occupancy.surface_disposition(
            tile,
            district_id=district_id,
            belt_type=belt_type,
            direction=action["direction"],
            interface=interface,
        )
        if disposition != "place":
            return False, f"surface action tile {tile} is no longer clear"
    for tile, direction, interface in reused:
        if interface in {"underground_input", "underground_output"}:
            endpoint_type = "input" if interface == "underground_input" else "output"
            if not occupancy.owned_underground_endpoint(
                tile,
                district_id=district_id,
                belt_type=belt_type,
                direction=direction,
                underground_type=endpoint_type,
            ):
                return False, f"reused underground {endpoint_type} at {tile} no longer matches"
            continue
        disposition = occupancy.surface_disposition(
            tile,
            district_id=district_id,
            belt_type=belt_type,
            direction=direction,
            interface=interface,
        )
        if disposition != "reuse":
            return False, f"reused belt at {tile} no longer matches"
    return True, ""


def plan_belt_route(
    source: Point,
    destination: Point,
    *,
    belt_type: str,
    occupancy: RouteOccupancy,
    district_id: str,
    source_heading: str,
    destination_heading: str,
    max_route_tiles: int | None = None,
    max_actions: int | None = None,
    max_search_nodes: int = _ROUTE_SEARCH_LIMIT,
    underground_reach: int | float | None = None,
    fresh_occupancy: RouteOccupancy | None = None,
) -> RoutePlan | RouteFailure:
    """Plan one legal belt route with surface and underground graph edges.

    The search never overlays tunnels onto a chosen route. Underground pairs
    are bounded, straight crossover edges whose endpoints are checked before
    entering the queue. A second occupancy snapshot can invalidate the entire
    result before any action is returned to the caller.
    """
    if belt_type not in UNDERGROUND_REACH:
        return RouteFailure(
            "invalid_belt_type", f"unknown underground reach for {belt_type!r}",
        )
    if underground_reach is None:
        underground_reach = UNDERGROUND_REACH[belt_type]
    if (
        isinstance(underground_reach, bool)
        or not isinstance(underground_reach, (int, float))
        or not math.isfinite(float(underground_reach))
        or float(underground_reach) <= 0
        or not float(underground_reach).is_integer()
    ):
        return RouteFailure(
            "invalid_underground_reach",
            f"underground reach must be a finite positive integer, got {underground_reach!r}",
        )
    live_reach = int(underground_reach)
    if source_heading not in _FACING_TO_VECTOR or destination_heading not in _FACING_TO_VECTOR:
        return RouteFailure(
            "invalid_heading",
            f"unknown source/destination heading: {source_heading!r}/{destination_heading!r}",
        )
    if not district_id:
        return RouteFailure("invalid_district", "district_id must be non-empty")
    if not isinstance(occupancy, RouteOccupancy):
        return RouteFailure("invalid_occupancy", "occupancy must be RouteOccupancy")
    if max_search_nodes <= 0:
        return RouteFailure("invalid_search_limit", "max_search_nodes must be positive")

    source_tile, destination_tile = _tile(source), _tile(destination)
    direct_tiles = (
        abs(destination_tile[0] - source_tile[0])
        + abs(destination_tile[1] - source_tile[1]) + 1
    )
    route_limit_was_explicit = max_route_tiles is not None
    if max_route_tiles is None:
        max_route_tiles = direct_tiles + _TYPED_DEFAULT_EXTRA_TILES
    if max_route_tiles <= 0:
        return RouteFailure("invalid_route_limit", "max_route_tiles must be positive")
    if max_actions is not None and max_actions < 0:
        return RouteFailure("invalid_action_limit", "max_actions cannot be negative")

    source_disposition = occupancy.surface_disposition(
        source_tile,
        district_id=district_id,
        belt_type=belt_type,
        direction=source_heading,
        interface="source",
    )
    if source_disposition == "blocked":
        return RouteFailure(
            "blocked_source", f"source tile {source_tile} is not a legal district interface",
        )
    destination_disposition = occupancy.surface_disposition(
        destination_tile,
        district_id=district_id,
        belt_type=belt_type,
        direction=destination_heading,
        interface="destination",
    )
    if destination_disposition == "blocked":
        return RouteFailure(
            "blocked_destination",
            f"destination tile {destination_tile} is not a legal district interface",
        )
    if direct_tiles > max_route_tiles:
        return RouteFailure(
            "route_limit",
            f"direct route needs {direct_tiles} tiles, beyond limit {max_route_tiles}",
        )

    labels: dict[int, _TypedRouteLabel] = {
        0: _TypedRouteLabel(
            tile=source_tile,
            incoming=None,
            route_count=1,
            action_count=0,
            cost=0.0,
            turns=0,
            parent_id=None,
            segment_actions=(),
            segment_tiles=(),
            segment_reused=(),
        ),
    }
    frontier: dict[tuple[Tile, str | None], list[_TypedRouteLabel]] = {
        (source_tile, None): [labels[0]],
    }
    queue: list[tuple[float, float, int]] = [
        (float(direct_tiles - 1), 0.0, 0),
    ]
    next_id = 1
    expanded = 0
    route_pruned = False
    action_pruned = False
    goal_id: int | None = None

    while queue:
        _priority, _cost_tie, label_id = heapq.heappop(queue)
        label = labels[label_id]
        if label not in frontier.get((label.tile, label.incoming), ()):
            continue
        expanded += 1
        if expanded > max_search_nodes:
            return RouteFailure(
                "search_limit", f"route search exceeded {max_search_nodes} expanded labels",
            )

        if label.tile == destination_tile:
            if label.incoming == destination_heading or (
                label.incoming is None and source_heading == destination_heading
            ):
                final_action_count = label.action_count + (
                    0 if destination_disposition == "reuse" else 1
                )
                if max_actions is not None and final_action_count > max_actions:
                    action_pruned = True
                else:
                    goal_id = label_id
                    break
            # The destination is an interface, never a transit tile.
            continue

        directions = (
            (source_heading,) if label.incoming is None
            else _typed_direction_order(label.incoming)
        )
        for direction in directions:
            if label.incoming is not None and direction == _OPPOSITE[label.incoming]:
                continue
            interface = "source" if label.tile == source_tile else None
            disposition = occupancy.surface_disposition(
                label.tile,
                district_id=district_id,
                belt_type=belt_type,
                direction=direction,
                interface=interface,
            )
            if disposition == "blocked":
                continue
            next_tile = _advance(label.tile, direction)
            next_interface = "destination" if next_tile == destination_tile else None
            next_required_direction = (
                destination_heading if next_tile == destination_tile else None
            )
            if not occupancy.may_enter_surface(
                next_tile,
                district_id=district_id,
                belt_type=belt_type,
                interface=next_interface,
                required_direction=next_required_direction,
            ):
                continue
            route_count = label.route_count + 1
            if route_count > max_route_tiles:
                route_pruned = True
                continue
            action_count = label.action_count + (0 if disposition == "reuse" else 1)
            if max_actions is not None and action_count > max_actions:
                action_pruned = True
                continue
            turned = label.incoming is not None and direction != label.incoming
            cost = label.cost + 1.0 + (_TYPED_ROUTE_TURN_COST if turned else 0.0)
            action = () if disposition == "reuse" else (
                _surface_action(label.tile, belt_type, direction),
            )
            reused = () if disposition != "reuse" else (
                (label.tile, direction, interface),
            )
            candidate = _TypedRouteLabel(
                tile=next_tile,
                incoming=direction,
                route_count=route_count,
                action_count=action_count,
                cost=cost,
                turns=label.turns + int(turned),
                parent_id=label_id,
                segment_actions=action,
                segment_tiles=(label.tile,),
                segment_reused=reused,
            )
            state = (candidate.tile, candidate.incoming)
            state_labels = frontier.setdefault(state, [])
            if _label_is_dominated(state_labels, candidate):
                continue
            frontier[state] = [
                existing for existing in state_labels
                if not (
                    candidate.cost <= existing.cost
                    and candidate.route_count <= existing.route_count
                    and candidate.action_count <= existing.action_count
                )
            ]
            frontier[state].append(candidate)
            labels[next_id] = candidate
            heuristic = abs(next_tile[0] - destination_tile[0]) + abs(
                next_tile[1] - destination_tile[1]
            )
            heapq.heappush(queue, (cost + heuristic, cost, next_id))
            next_id += 1

        # Exact owned underground pairs are graph edges too. Reuse requires
        # both endpoint identities, directions, endpoint types, and live reach.
        tunnel_direction = source_heading if label.incoming is None else label.incoming
        label_interface = "source" if label.tile == source_tile else None
        label_disposition = occupancy.surface_disposition(
            label.tile,
            district_id=district_id,
            belt_type=belt_type,
            direction=tunnel_direction,
            interface=label_interface,
        )
        input_tile = _advance(label.tile, tunnel_direction)
        if label_disposition != "blocked" and occupancy.owned_underground_endpoint(
            input_tile,
            district_id=district_id,
            belt_type=belt_type,
            direction=tunnel_direction,
            underground_type="input",
        ):
            for span in range(1, live_reach + 1):
                output_tile = _advance(input_tile, tunnel_direction, span)
                if not occupancy.owned_underground_endpoint(
                    output_tile,
                    district_id=district_id,
                    belt_type=belt_type,
                    direction=tunnel_direction,
                    underground_type="output",
                ):
                    continue
                next_tile = _advance(output_tile, tunnel_direction)
                next_interface = (
                    "destination" if next_tile == destination_tile else None
                )
                next_required_direction = (
                    destination_heading if next_tile == destination_tile else None
                )
                if not occupancy.may_enter_surface(
                    next_tile,
                    district_id=district_id,
                    belt_type=belt_type,
                    interface=next_interface,
                    required_direction=next_required_direction,
                ):
                    continue
                route_count = label.route_count + span + 2
                if route_count > max_route_tiles:
                    route_pruned = True
                    continue
                surface_action_count = 0 if label_disposition == "reuse" else 1
                action_count = label.action_count + surface_action_count
                if max_actions is not None and action_count > max_actions:
                    action_pruned = True
                    continue
                cost = label.cost + span + 2
                candidate = _TypedRouteLabel(
                    tile=next_tile,
                    incoming=tunnel_direction,
                    route_count=route_count,
                    action_count=action_count,
                    cost=cost,
                    turns=label.turns,
                    parent_id=label_id,
                    segment_actions=(
                        () if label_disposition == "reuse" else (
                            _surface_action(label.tile, belt_type, tunnel_direction),
                        )
                    ),
                    segment_tiles=tuple(
                        _advance(label.tile, tunnel_direction, distance)
                        for distance in range(span + 2)
                    ),
                    segment_reused=(
                        *((
                            (label.tile, tunnel_direction, label_interface),
                        ) if label_disposition == "reuse" else ()),
                        (input_tile, tunnel_direction, "underground_input"),
                        (output_tile, tunnel_direction, "underground_output"),
                    ),
                    underground_span=(input_tile, output_tile),
                )
                state_key = (candidate.tile, candidate.incoming)
                state_labels = frontier.setdefault(state_key, [])
                if _label_is_dominated(state_labels, candidate):
                    continue
                frontier[state_key] = [
                    existing for existing in state_labels
                    if not (
                        candidate.cost <= existing.cost
                        and candidate.route_count <= existing.route_count
                        and candidate.action_count <= existing.action_count
                    )
                ]
                frontier[state_key].append(candidate)
                labels[next_id] = candidate
                heuristic = abs(next_tile[0] - destination_tile[0]) + abs(
                    next_tile[1] - destination_tile[1]
                )
                heapq.heappush(queue, (cost + heuristic, cost, next_id))
                next_id += 1
                break

        # A new tunnel is a graph edge only when the tile immediately ahead cannot
        # carry a surface belt. This covers the useful crossover case while
        # avoiding arbitrary underground runs through clear ground.
        if not occupancy.underground_endpoint_is_clear(
            label.tile, district_id=district_id, belt_type=belt_type,
        ):
            continue
        immediately_ahead = _advance(label.tile, tunnel_direction)
        if occupancy.may_enter_surface(
            immediately_ahead,
            district_id=district_id,
            belt_type=belt_type,
        ):
            continue
        for span in range(2, live_reach + 1):
            exit_tile = _advance(label.tile, tunnel_direction, span)
            if not occupancy.underground_endpoint_is_clear(
                exit_tile, district_id=district_id, belt_type=belt_type,
            ):
                continue
            next_tile = _advance(exit_tile, tunnel_direction)
            next_interface = "destination" if next_tile == destination_tile else None
            next_required_direction = (
                destination_heading if next_tile == destination_tile else None
            )
            if not occupancy.may_enter_surface(
                next_tile,
                district_id=district_id,
                belt_type=belt_type,
                interface=next_interface,
                required_direction=next_required_direction,
            ):
                continue
            route_count = label.route_count + span + 1
            if route_count > max_route_tiles:
                route_pruned = True
                continue
            action_count = label.action_count + 2
            if max_actions is not None and action_count > max_actions:
                action_pruned = True
                continue
            cost = label.cost + span + 1 + _TYPED_UNDERGROUND_COST
            segment_tiles = tuple(
                _advance(label.tile, tunnel_direction, distance)
                for distance in range(span + 1)
            )
            candidate = _TypedRouteLabel(
                tile=next_tile,
                incoming=tunnel_direction,
                route_count=route_count,
                action_count=action_count,
                cost=cost,
                turns=label.turns,
                parent_id=label_id,
                segment_actions=(
                    _underground_action(
                        label.tile, belt_type, tunnel_direction, "input",
                    ),
                    _underground_action(
                        exit_tile, belt_type, tunnel_direction, "output",
                    ),
                ),
                segment_tiles=segment_tiles,
                segment_reused=(),
                underground_span=(label.tile, exit_tile),
            )
            state = (candidate.tile, candidate.incoming)
            state_labels = frontier.setdefault(state, [])
            if _label_is_dominated(state_labels, candidate):
                continue
            frontier[state] = [
                existing for existing in state_labels
                if not (
                    candidate.cost <= existing.cost
                    and candidate.route_count <= existing.route_count
                    and candidate.action_count <= existing.action_count
                )
            ]
            frontier[state].append(candidate)
            labels[next_id] = candidate
            heuristic = abs(next_tile[0] - destination_tile[0]) + abs(
                next_tile[1] - destination_tile[1]
            )
            heapq.heappush(queue, (cost + heuristic, cost, next_id))
            next_id += 1

    if goal_id is None:
        if action_pruned:
            reason = "action_limit"
            detail = f"no route fits the {max_actions}-action limit"
        elif route_pruned and route_limit_was_explicit:
            reason = "route_limit"
            detail = f"no legal route fits the {max_route_tiles}-tile limit"
        else:
            reason = "no_legal_route"
            detail = "no legal surface/underground route was found"
        return RouteFailure(reason, detail)

    goal = labels[goal_id]
    destination_action = (
        None if destination_disposition == "reuse"
        else _surface_action(destination_tile, belt_type, destination_heading)
    )
    destination_reuse = (
        (destination_tile, destination_heading, "destination")
        if destination_disposition == "reuse" else None
    )
    actions, route_tiles, reused, spans, turns = _reconstruct_typed_route(
        labels,
        goal_id,
        destination=destination_tile,
        destination_action=destination_action,
        destination_reuse=destination_reuse,
        total_cost=goal.cost,
    )
    if fresh_occupancy is not None:
        if not isinstance(fresh_occupancy, RouteOccupancy):
            return RouteFailure(
                "invalid_fresh_occupancy", "fresh_occupancy must be RouteOccupancy",
            )
        valid, detail = _fresh_typed_route_is_valid(
            fresh_occupancy,
            actions=actions,
            reused=reused,
            district_id=district_id,
            belt_type=belt_type,
        )
        if not valid:
            return RouteFailure("fresh_occupancy_conflict", detail)
    return RoutePlan(
        actions=actions,
        route_tiles=route_tiles,
        reused_tiles=frozenset(tile for tile, _direction, _interface in reused),
        underground_spans=spans,
        underground_reach=live_reach,
        turns=turns,
        total_cost=goal.cost,
    )


def search_clear_route(
    start: Point, end: Point, blocked: set[tuple[int, int]],
    *, initial_direction: str | None = None,
    final_direction: str | None = None,
) -> list[Point] | None:
    """A rectilinear route from `start` to `end` over free tiles, or None.

    Deterministic A*: ties break on the ordered direction list, so the same
    inputs always give the same belt. Returns corner points in the shape
    _route_points expects -- the caller's existing machinery is unchanged.
    """
    start_tile, end_tile = _tile(start), _tile(end)
    if start_tile in blocked or end_tile in blocked:
        return None
    low_x = min(start[0], end[0]) - _ROUTE_SEARCH_MARGIN
    high_x = max(start[0], end[0]) + _ROUTE_SEARCH_MARGIN
    low_y = min(start[1], end[1]) - _ROUTE_SEARCH_MARGIN
    high_y = max(start[1], end[1]) + _ROUTE_SEARCH_MARGIN
    order = ("east", "west", "south", "north")

    def heuristic(point: Point) -> float:
        return abs(point[0] - end[0]) + abs(point[1] - end[1])

    origin = (start, None)
    best: dict[tuple[Point, str | None], float] = {origin: 0.0}
    came: dict[tuple[Point, str | None], tuple[Point, str | None]] = {}
    queue: list[tuple[float, int, Point, str | None]] = [(heuristic(start), 0, start, None)]
    counter = 0
    expanded = 0
    while queue:
        _priority, _tiebreak, point, heading = heapq.heappop(queue)
        state = (point, heading)
        if point == end and (final_direction is None or heading == final_direction):
            return _corners(_unwind(came, state))
        expanded += 1
        if expanded > _ROUTE_SEARCH_LIMIT:
            return None
        for direction in order:
            if heading is None and initial_direction is not None:
                if direction != initial_direction:
                    continue
            if heading is not None and direction == _OPPOSITE[heading]:
                continue
            vector = _FACING_TO_VECTOR[direction]
            nxt = (point[0] + vector[0], point[1] + vector[1])
            if not (low_x <= nxt[0] <= high_x and low_y <= nxt[1] <= high_y):
                continue
            if _tile(nxt) in blocked:
                continue
            cost = (
                best[state] + 1.0
                + (_ROUTE_TURN_COST if heading is not None and direction != heading else 0.0)
            )
            successor = (nxt, direction)
            if cost >= best.get(successor, math.inf):
                continue
            best[successor] = cost
            came[successor] = state
            counter += 1
            heapq.heappush(queue, (cost + heuristic(nxt), counter, nxt, direction))
    return None


def _unwind(
    came: dict[tuple[Point, str | None], tuple[Point, str | None]],
    state: tuple[Point, str | None],
) -> list[Point]:
    path = [state[0]]
    while state in came:
        state = came[state]
        path.append(state[0])
    path.reverse()
    return path


def _corners(path: Sequence[Point]) -> list[Point]:
    """Collapse a tile-by-tile path to the corner points a route is made of."""
    if len(path) < 2:
        return list(path)
    corners = [path[0]]
    for previous, point, following in zip(path, path[1:], path[2:]):
        before = (point[0] - previous[0], point[1] - previous[1])
        after = (following[0] - point[0], following[1] - point[1])
        if before != after:
            corners.append(point)
    corners.append(path[-1])
    return corners


def _route_or_detour(
    route: Sequence[Point], belt_type: str, blocked: set[tuple[int, int]],
    *, initial_direction: str | None = None,
    final_direction: str | None = None,
) -> tuple[list[dict], list[Point]]:
    """Belt the chosen route, or search a clear one when it cannot be belted."""
    try:
        return _belt_run(route, belt_type, blocked), list(route)
    except ValueError as blocked_route:
        detour = search_clear_route(
            route[0], route[-1], blocked,
            initial_direction=initial_direction, final_direction=final_direction,
        )
        if detour is None:
            raise ValueError(
                f"no belt route is available for this bridge: {blocked_route}"
            ) from blocked_route
        return _belt_run(detour, belt_type, blocked), detour


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
    actions.extend(
        _route_or_detour(
            route, belt_type, blocked_tiles or set(),
            final_direction=entry_direction,
        )[0]
    )
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
    add_turn_buffer: bool = False,
    destination_direction: str | None = None,
) -> list[dict]:
    """Continue one belt into another without inserting a chest hop."""
    if destination_direction is not None:
        if destination_direction not in _FACING_TO_VECTOR:
            raise ValueError(
                f"Unknown destination belt direction: {destination_direction!r}"
            )
        dest_belt = _add(dest_belt, _FACING_TO_VECTOR[destination_direction])
    entry_vector = _FACING_TO_VECTOR[entry_direction]
    belt_end = _add(dest_belt, entry_vector)
    route = _aligned_final_route(
        source_belt, belt_end, entry_direction, blocked_tiles,
        exit_direction=exit_direction, include_endpoint=False,
    )
    desired_direction = opposite(entry_direction)
    if exit_direction and _leg_direction(route[-2], route[-1]) != desired_direction:
        detour = search_clear_route(
            source_belt, route[-1], blocked_tiles or set(),
            initial_direction=exit_direction, final_direction=desired_direction,
        )
        if detour is None:
            raise ValueError("no route satisfies the source and destination belt directions")
        route = detour
    inline_destination = (
        destination_direction is not None
        and entry_direction == opposite(destination_direction)
    )
    if not inline_destination and route[-1] != belt_end:
        route.append(belt_end)
    route_tiles = len(_route_points(route))
    if max_route_tiles is not None and route_tiles > max_route_tiles:
        raise ValueError(
            f"Generated belt route needs {route_tiles} tiles, beyond the "
            f"{max_route_tiles}-tile local-mode limit"
        )
    blocked = blocked_tiles or set()
    actions, built = _route_or_detour(
        route, belt_type, blocked,
        initial_direction=exit_direction,
        final_direction=opposite(entry_direction),
    )
    if add_turn_buffer:
        actions.extend(_turn_buffer_actions(built, blocked, actions))
    return actions


def bridge_chest_to_belt(
    source_position: Point,
    dest_belt: Point,
    *,
    exit_direction: str,
    entry_direction: str,
    belt_type: str = "fast-transport-belt",
    inserter_type: str = "fast-inserter",
    blocked_tiles: set[tuple[int, int]] | None = None,
    max_route_tiles: int | None = None,
    destination_direction: str | None = None,
) -> list[dict]:
    """Drain a provider chest onto a belt that joins an inline destination bus."""
    if exit_direction not in _FACING_TO_VECTOR:
        raise ValueError(f"Unknown exit direction: {exit_direction!r}")
    if entry_direction not in _FACING_TO_VECTOR:
        raise ValueError(f"Unknown entry direction: {entry_direction!r}")
    exit_vector = _FACING_TO_VECTOR[exit_direction]
    source_inserter = _add(source_position, _scaled(exit_vector, 1))
    belt_start = _add(source_position, _scaled(exit_vector, 2))
    actions = [{
        "action_type": "place_entity",
        "entity": inserter_type,
        "position": {"x": source_inserter[0], "y": source_inserter[1]},
        "direction": opposite(exit_direction),
    }]
    actions.extend(
        bridge_belt_to_belt(
            belt_start,
            dest_belt,
            entry_direction=entry_direction,
            belt_type=belt_type,
            blocked_tiles=blocked_tiles,
            max_route_tiles=max_route_tiles,
            exit_direction=exit_direction,
            destination_direction=destination_direction,
        )
    )
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
    actions.extend(
        _route_or_detour(
            route, belt_type, blocked_tiles or set(),
            final_direction=entry_direction,
        )[0]
    )
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


def _tunnelled_points(
    points, blocked: set[tuple[int, int]],
) -> list[bool]:
    """Which route tiles must run underground: the blocked ones, and any free
    tile trapped between two of them.

    A single free tile between two blocked runs cannot carry the first tunnel's
    EXIT and the second tunnel's ENTRY -- that is two entities on one tile. The
    tile is therefore swallowed and the two runs become one tunnel, whose longer
    span is then checked against the tier's reach like any other.

    Left unhandled this severed the belt silently: the second entry overwrote
    the first exit, so items went underground and never came back up. Seen live
    on the iron-plate haul from the mine at (12.5,-3.5), which arrived as three
    disconnected belts with the break at (-9.5,-25.5).
    """
    underground = [_tile(point) in blocked for point, _direction, _leg in points]
    swallowed = True
    while swallowed:
        swallowed = False
        for index in range(1, len(underground) - 1):
            if not underground[index] and underground[index - 1] and underground[index + 1]:
                underground[index] = True
                swallowed = True
    return underground


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
    tunnelled = _tunnelled_points(points, blocked)
    actions: list[dict] = []
    index = 0
    while index < len(points):
        point, direction, leg = points[index]
        if not tunnelled[index]:
            actions.append({
                "action_type": "place_ghost", "entity": belt_type,
                "position": {"x": point[0], "y": point[1]}, "direction": direction,
            })
            index += 1
            continue

        span_end = index
        while span_end < len(points) and tunnelled[span_end]:
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
