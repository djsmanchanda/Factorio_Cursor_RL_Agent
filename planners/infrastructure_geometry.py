# Path: planners/infrastructure_geometry.py
# Purpose: Deterministic geometry primitives for factory infrastructure planning.

from __future__ import annotations

from typing import List, Sequence, Tuple

Point = Tuple[float, float]


def distance(a: Point, b: Point) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def chebyshev_distance(a: Point, b: Point) -> float:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def boxes_overlap(centre_a: Point, size_a: float, centre_b: Point, size_b: float) -> bool:
    """Return whether two axis-aligned square footprints share any area."""
    half_a, half_b = size_a / 2.0, size_b / 2.0
    return (
        abs(centre_a[0] - centre_b[0]) < half_a + half_b
        and abs(centre_a[1] - centre_b[1]) < half_a + half_b
    )


def l_route(start: Point, end: Point) -> List[Point]:
    """Return corners for a horizontal-then-vertical reserved corridor."""
    corners = [tuple(start)]
    if end[0] != start[0]:
        corners.append((end[0], start[1]))
    if end != corners[-1]:
        corners.append(tuple(end))
    return corners


def step_points(start: Point, end: Point, spacing: float) -> List[Point]:
    """Return rounded points from exclusive start to inclusive end."""
    span = distance(start, end)
    if span == 0:
        return []
    ux, uy = (end[0] - start[0]) / span, (end[1] - start[1]) / span
    points: List[Point] = []
    for step in range(1, int(span // spacing) + 1):
        candidate = (
            round(start[0] + ux * spacing * step),
            round(start[1] + uy * spacing * step),
        )
        if distance(candidate, end) > 1e-9:
            points.append(candidate)
    points.append((round(end[0]), round(end[1])))
    return points


class FootprintPlacer:
    """Ordered, overlap-free accumulator of same-size square footprints."""

    def __init__(self, size: float):
        self.size = size
        self.points: List[Point] = []

    def add(self, point: Point) -> bool:
        point = (round(point[0]), round(point[1]))
        if any(chebyshev_distance(existing, point) < self.size for existing in self.points):
            return False
        self.points.append(point)
        return True


def minimum_spanning_tree_edges(nodes: Sequence[Point]) -> List[Tuple[int, int]]:
    """Return Prim MST edges with deterministic index-based tie-breaking."""
    if len(nodes) < 2:
        return []
    inside = {0}
    edges: List[Tuple[int, int]] = []
    while len(inside) < len(nodes):
        best = None
        for left in sorted(inside):
            for right in range(len(nodes)):
                if right in inside:
                    continue
                cost = distance(nodes[left], nodes[right])
                if best is None or cost < best[0] - 1e-9:
                    best = (cost, left, right)
        assert best is not None
        edges.append((best[1], best[2]))
        inside.add(best[2])
    return edges