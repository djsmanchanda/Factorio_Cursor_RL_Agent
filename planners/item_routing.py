# Path: planners/item_routing.py
# Purpose: Compose declared item endpoints into deterministic collision-guarded belt routes.

from __future__ import annotations

from dataclasses import dataclass

from planners.plan_validation import validate_build_plan
from planners.recipe_data import BELT_TIERS

_DIRECTIONS = {(1, 0): "east", (-1, 0): "west", (0, 1): "south", (0, -1): "north"}
UNDERGROUND_MAX_ENDPOINT_DISTANCE = {
    "transport-belt": 5,
    "fast-transport-belt": 7,
    "express-transport-belt": 9,
    "turbo-transport-belt": 11,
}


@dataclass(frozen=True)
class ItemEndpoint:
    name: str
    item: str
    role: str
    position: tuple[int, int]
    direction: str

    def __post_init__(self) -> None:
        if self.role not in {"producer", "consumer"}:
            raise ValueError("Item endpoint role must be producer or consumer")
        if self.direction not in set(_DIRECTIONS.values()):
            raise ValueError(f"Unknown endpoint direction: {self.direction}")


@dataclass(frozen=True)
class ItemRoute:
    name: str
    item: str
    producer: str
    consumer: str
    waypoints: tuple[tuple[int, int], ...] = ()
    # Every contiguous run is the empty span between one entrance and one exit.
    tunnel_crossings: tuple[tuple[int, int], ...] = ()


def _segment(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    if start[0] != end[0] and start[1] != end[1]:
        raise ValueError(f"Item route segment must be axis-aligned: {start} -> {end}")
    dx = 0 if start[0] == end[0] else (1 if end[0] > start[0] else -1)
    dy = 0 if start[1] == end[1] else (1 if end[1] > start[1] else -1)
    distance = max(abs(end[0] - start[0]), abs(end[1] - start[1]))
    return [(start[0] + dx * step, start[1] + dy * step) for step in range(distance + 1)]


def _tunnel_geometry(
    route: ItemRoute,
    points: list[tuple[int, int]],
    belt_type: str,
) -> tuple[set[tuple[int, int]], dict[tuple[int, int], tuple[str, str]]]:
    """Turn each contiguous empty run into exactly one underground-belt pair."""
    declared = route.tunnel_crossings
    tunnels = set(declared)
    if len(tunnels) != len(declared):
        raise ValueError(f"Item route {route.name} repeats a tunnel crossing")
    indices = sorted(points.index(point) if point in points else -1 for point in tunnels)
    if any(index <= 0 or index >= len(points) - 1 for index in indices):
        raise ValueError(f"Item route {route.name} tunnel crossings must be interior route tiles")

    runs: list[tuple[int, int]] = []
    for index in indices:
        if not runs or index != runs[-1][1] + 1:
            runs.append((index, index))
        else:
            runs[-1] = (runs[-1][0], index)

    endpoints: dict[tuple[int, int], tuple[str, str]] = {}
    maximum = UNDERGROUND_MAX_ENDPOINT_DISTANCE[belt_type]
    for first, last in runs:
        entrance = points[first - 1]
        exit_ = points[last + 1]
        step = (points[first][0] - entrance[0], points[first][1] - entrance[1])
        if step not in _DIRECTIONS or any(
            (points[index][0] - points[index - 1][0],
             points[index][1] - points[index - 1][1]) != step
            for index in range(first + 1, last + 2)
        ):
            raise ValueError(
                f"Item route {route.name} tunnel run {points[first]}..{points[last]} "
                "must be straight"
            )
        endpoint_distance = max(abs(exit_[0] - entrance[0]), abs(exit_[1] - entrance[1]))
        if endpoint_distance > maximum:
            raise ValueError(
                f"Item route {route.name} tunnel span {endpoint_distance} exceeds "
                f"{belt_type} maximum {maximum}"
            )
        if entrance in endpoints or exit_ in endpoints:
            raise ValueError(f"Item route {route.name} tunnel runs cannot share an endpoint")
        endpoints[entrance] = (_DIRECTIONS[step], "input")
        endpoints[exit_] = (_DIRECTIONS[step], "output")
    if len(endpoints) != 2 * len(runs):
        raise ValueError(f"Item route {route.name} must emit exactly two endpoints per tunnel run")
    return tunnels, endpoints


def route_declared_items(
    endpoints: list[ItemEndpoint],
    routes: list[ItemRoute],
    *,
    occupied_tiles: set[tuple[int, int]] | None = None,
    belt_type: str = "express-transport-belt",
) -> list[tuple[str, dict]]:
    if belt_type not in BELT_TIERS:
        raise ValueError(f"Unknown belt tier: {belt_type}")
    by_name = {endpoint.name: endpoint for endpoint in endpoints}
    if len(by_name) != len(endpoints):
        raise ValueError("Item endpoint names must be unique")
    occupied = set(occupied_tiles or ())
    claimed: set[tuple[int, int]] = set()
    plans = []
    for route in routes:
        producer = by_name.get(route.producer)
        consumer = by_name.get(route.consumer)
        if not producer or producer.role != "producer":
            raise ValueError(f"Route {route.name} has no declared producer endpoint")
        if not consumer or consumer.role != "consumer":
            raise ValueError(f"Route {route.name} has no declared consumer endpoint")
        if route.item != producer.item or route.item != consumer.item:
            raise ValueError(f"Route {route.name} item does not match both endpoints")
        anchors = [producer.position, *route.waypoints, consumer.position]
        points: list[tuple[int, int]] = []
        for start, end in zip(anchors, anchors[1:]):
            segment = _segment(start, end)
            points.extend(segment if not points else segment[1:])
        tunnels, tunnel_ends = _tunnel_geometry(route, points, belt_type)
        physical_points = set(points) - tunnels
        collisions = sorted(physical_points & (occupied | claimed))
        if collisions:
            raise ValueError(f"Item route {route.name} collides at {collisions[0]}")

        underground = belt_type.replace("transport-belt", "underground-belt")
        route_actions = []
        for index, point in enumerate(points):
            if point in tunnels:
                continue
            if index + 1 < len(points):
                next_index = index + 1
                while points[next_index] in tunnels:
                    next_index += 1
                nxt = points[next_index]
                delta = (
                    0 if nxt[0] == point[0] else (1 if nxt[0] > point[0] else -1),
                    0 if nxt[1] == point[1] else (1 if nxt[1] > point[1] else -1),
                )
                direction = _DIRECTIONS[delta]
            else:
                direction = consumer.direction
            tunnel_end = tunnel_ends.get(point)
            action = {
                "action_type": "place_ghost",
                "entity": underground if tunnel_end else belt_type,
                "position": {"x": point[0] + 0.5, "y": point[1] + 0.5},
                "direction": tunnel_end[0] if tunnel_end else direction,
            }
            if tunnel_end:
                action["underground_type"] = tunnel_end[1]
            route_actions.append(action)
        plan = {"phases": [{"name": f"item_route_{route.name}", "actions": route_actions}]}
        validate_build_plan(plan)
        plans.append((route.name, plan))
        claimed.update(physical_points)
    return plans