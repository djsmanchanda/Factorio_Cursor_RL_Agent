# Path: planners/water_lakes.py
# Purpose: Derive the deterministic water-tile obstacles seeded behind surveyed offshore pumps.

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

WATER_LAKE_HALF_WIDTH = 6
WATER_LAKE_DEPTH = 13


def water_lake_bounds(
    position: tuple[float, float], direction: str,
) -> tuple[int, int, int, int]:
    """Return the canonical bounded lake behind one offshore-pump site.

    The entity direction points into the lake; its pipe connector is on the
    opposite landward side. This function therefore extends water in the
    stated direction.
    """
    x, y = map(math.floor, position)
    cross_min = -WATER_LAKE_HALF_WIDTH
    cross_max = WATER_LAKE_HALF_WIDTH + 1
    depth = WATER_LAKE_DEPTH - 1
    if direction == "north":
        return x + cross_min, y - depth, x + cross_max, y
    if direction == "south":
        return x + cross_min, y + 1, x + cross_max, y + WATER_LAKE_DEPTH
    if direction == "east":
        return x + 1, y + cross_min, x + WATER_LAKE_DEPTH, y + cross_max
    if direction == "west":
        return x - depth, y + cross_min, x, y + cross_max
    raise ValueError(f"Unknown offshore-pump direction: {direction}")


def water_lake_tile_indices(sites: Iterable[Mapping]) -> set[tuple[int, int]]:
    """Return every terrain tile that execution will convert to water."""
    tiles: set[tuple[int, int]] = set()
    for site in sites:
        x1, y1, x2, y2 = water_lake_bounds(
            tuple(site["position"]), str(site.get("direction", "north")),
        )
        tiles.update(
            (x, y) for x in range(x1, x2 + 1) for y in range(y1, y2 + 1)
        )
    return tiles
