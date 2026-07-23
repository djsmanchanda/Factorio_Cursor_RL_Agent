# Path: tools/spidertron_geometry.py
# Purpose: Pure deterministic geometry shared by the spidertron self-test and
#          composed-production radial execution.

from __future__ import annotations

import math
from typing import Sequence

Point = tuple[float, float]


def _axis_coords(lo: float, hi: float, step: float) -> list[float]:
    """Return evenly spaced coordinates covering [lo, hi] with spacing <= step."""
    span = hi - lo
    if span <= step:
        return [(lo + hi) / 2.0]
    intervals = math.ceil(span / step)
    return [lo + index * span / intervals for index in range(intervals + 1)]


def lawnmower_waypoints(
    min_x: float, min_y: float, max_x: float, max_y: float, step: float
) -> list[Point]:
    """Deterministically cover a box in a serpentine tour with bounded hops."""
    if step <= 0:
        raise ValueError("step must be positive")
    if max_x < min_x or max_y < min_y:
        raise ValueError("bounding box max must be >= min on both axes")
    xs = _axis_coords(min_x, max_x, step)
    ys = _axis_coords(min_y, max_y, step)
    waypoints: list[Point] = []
    for row, y in enumerate(ys):
        row_xs = xs if row % 2 == 0 else list(reversed(xs))
        waypoints.extend((float(x), float(y)) for x in row_xs)
    return waypoints


def concentric_rings(
    positions: Sequence[Point], center: Point, ring_width: float
) -> list[list[Point]]:
    """Partition points into sorted Chebyshev-distance shells, innermost first."""
    if ring_width <= 0:
        raise ValueError("ring_width must be positive")
    center_x, center_y = center
    buckets: dict[int, list[Point]] = {}
    for x, y in positions:
        distance = max(abs(x - center_x), abs(y - center_y))
        buckets.setdefault(int(distance // ring_width), []).append((float(x), float(y)))
    return [sorted(buckets[index]) for index in sorted(buckets)]


def square_grid(count: int, spacing: float, center: Point) -> list[Point]:
    """Return a deterministic square test grid centred on ``center``."""
    if count <= 0:
        raise ValueError("count must be positive")
    side = math.ceil(math.sqrt(count))
    center_x, center_y = center
    half = (side - 1) / 2.0
    points: list[Point] = []
    for row in range(side):
        for column in range(side):
            if len(points) >= count:
                return points
            points.append((center_x + (column - half) * spacing, center_y + (row - half) * spacing))
    return points


def ring_bbox(ring: Sequence[Point]) -> tuple[float, float, float, float]:
    """Return the bounding box for a non-empty ring."""
    xs = [point[0] for point in ring]
    ys = [point[1] for point in ring]
    return min(xs), min(ys), max(xs), max(ys)