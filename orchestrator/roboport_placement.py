# Path: orchestrator/roboport_placement.py
# Purpose: Relocate coverage-chain roboports away from existing entities and stage ghosts.

from __future__ import annotations

import math

from orchestrator import live_base
from planners.infrastructure_geometry import footprint_tile_indices
from tools.rcon_client import RconClient

Point = tuple[float, float]


def _service_distance(source: Point, target: Point, square: bool) -> float:
    if square:
        return max(abs(source[0] - target[0]), abs(source[1] - target[1]))
    return math.dist(source, target)


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
            )
        ), None)
        if chosen is None:
            raise ValueError(
                f"no clear 4x4 roboport site within {search_radius} tiles of {ideal}"
            )
        placed.append(chosen)
        previous = chosen
    return placed
