# Path: orchestrator/roboport_placement.py
# Purpose: Relocate coverage-chain roboports away from existing entities and stage ghosts.

from __future__ import annotations

import math

from orchestrator import live_base
from planners.infrastructure_geometry import footprint_tile_indices
from tools.rcon_client import RconClient

Point = tuple[float, float]

_ALTERNATE_SEARCH_RADII = (20, 32, 48)
_ALTERNATE_BEAM = 256


def _service_distance(source: Point, target: Point, square: bool) -> float:
    if square:
        return max(abs(source[0] - target[0]), abs(source[1] - target[1]))
    return math.dist(source, target)


def _local_chain_positions(
    client: RconClient,
    surface: str,
    source: Point,
    target: Point,
    ideals: list[Point],
    *,
    service_radius: float,
    service_square: bool,
    link_distance: float,
    search_radius: int = 10,
    reserved_tiles: set[tuple[int, int]] | None = None,
) -> list[Point]:
    """Move chain centres off live and already-planned infrastructure."""
    reserved = reserved_tiles or set()
    placed: list[Point] = []
    previous = source
    for index, ideal in enumerate(ideals):
        following = ideals[index + 1] if index + 1 < len(ideals) else None
        candidates = sorted(
            {
                (ideal[0] + dx, ideal[1] + dy)
                for dx in range(-search_radius, search_radius + 1)
                for dy in range(-search_radius, search_radius + 1)
            },
            key=lambda point: (math.dist(point, ideal), point[0], point[1]),
        )
        chosen = next((
            point for point in candidates
            if math.dist(previous, point) <= link_distance
            and (following is None or math.dist(point, following) <= link_distance)
            and (
                following is not None
                or _service_distance(point, target, service_square) <= service_radius
            )
            and not (footprint_tile_indices(point, 4) & reserved)
            and live_base.area_clear(
                client, surface,
                (point[0] - 2, point[1] - 2),
                (point[0] + 2, point[1] + 2),
                avoid_resources=True,
            )
        ), None)
        if chosen is None:
            raise ValueError(
                f"no clear 4x4 roboport site within {search_radius} tiles of {ideal}"
            )
        placed.append(chosen)
        previous = chosen
    return placed


def _alternate_chain_positions(
    client: RconClient,
    surface: str,
    source: Point,
    target: Point,
    ideals: list[Point],
    *,
    service_radius: float,
    service_square: bool,
    link_distance: float,
    reserved_tiles: set[tuple[int, int]],
) -> list[Point]:
    """Search a bounded connected corridor after a local ideal is blocked.

    The local placer is intentionally cheap, but a single large factory or
    coastline can make ten tiles around an ideal unusable. Surveying the
    complete bounded corridor once lets this fallback bend several consecutive
    hops together without treating existing infrastructure as removable.
    """
    max_radius = max(_ALTERNATE_SEARCH_RADII)
    min_x = min([source[0], target[0], *[point[0] for point in ideals]]) - max_radius - 3
    max_x = max([source[0], target[0], *[point[0] for point in ideals]]) + max_radius + 3
    min_y = min([source[1], target[1], *[point[1] for point in ideals]]) - max_radius - 3
    max_y = max([source[1], target[1], *[point[1] for point in ideals]]) + max_radius + 3
    blocked = live_base.occupied_tiles(
        client, surface, (min_x, min_y), (max_x, max_y),
        include_clutter=True, include_resources=True,
    )

    def candidates(ideal: Point, radius: int) -> list[Point]:
        points = {
            (float(round(ideal[0] + dx)), float(round(ideal[1] + dy)))
            for dx in range(-radius, radius + 1)
            for dy in range(-radius, radius + 1)
        }
        points = {
            point for point in points
            if point != target
            and not (footprint_tile_indices(point, 4) & blocked)
            and not (footprint_tile_indices(point, 4) & reserved_tiles)
        }
        return sorted(points, key=lambda point: (math.dist(point, ideal), point))

    for radius in _ALTERNATE_SEARCH_RADII:
        states: list[tuple[float, Point, list[Point]]] = [(0.0, source, [])]
        for index, ideal in enumerate(ideals):
            next_states: dict[Point, tuple[float, Point, list[Point]]] = {}
            for candidate in candidates(ideal, radius):
                for score, previous, path in states:
                    if math.dist(previous, candidate) > link_distance:
                        continue
                    if index == len(ideals) - 1 and (
                        _service_distance(candidate, target, service_square)
                        > service_radius
                    ):
                        continue
                    candidate_score = score + math.dist(candidate, ideal)
                    existing = next_states.get(candidate)
                    if existing is None or candidate_score < existing[0]:
                        next_states[candidate] = (
                            candidate_score, candidate, [*path, candidate],
                        )
            if not next_states:
                states = []
                break
            states = sorted(
                next_states.values(), key=lambda item: (item[0], item[1]),
            )[:_ALTERNATE_BEAM]
        if states:
            return min(states, key=lambda item: (item[0], item[1]))[2]
    raise ValueError(
        "no connected roboport corridor within "
        f"{max_radius} tiles of the planned route"
    )


def clear_chain_positions(
    client: RconClient,
    surface: str,
    source: Point,
    target: Point,
    ideals: list[Point],
    *,
    service_radius: float,
    service_square: bool,
    link_distance: float,
    search_radius: int = 10,
    reserved_tiles: set[tuple[int, int]] | None = None,
) -> list[Point]:
    """Place a chain locally, then search alternate connected corridors.

    A blocked local ideal is recoverable when another corridor can connect the
    source to the target. Only after the bounded fallback is exhausted does the
    caller receive ``ValueError`` and decide whether the stage should defer.
    """
    reserved = reserved_tiles or set()
    try:
        return _local_chain_positions(
            client, surface, source, target, ideals,
            service_radius=service_radius, service_square=service_square,
            link_distance=link_distance, search_radius=search_radius,
            reserved_tiles=reserved,
        )
    except ValueError as local_error:
        try:
            return _alternate_chain_positions(
                client, surface, source, target, ideals,
                service_radius=service_radius, service_square=service_square,
                link_distance=link_distance, reserved_tiles=reserved,
            )
        except ValueError as alternate_error:
            raise ValueError(
                f"{local_error}; alternate corridor failed: {alternate_error}"
            ) from alternate_error
