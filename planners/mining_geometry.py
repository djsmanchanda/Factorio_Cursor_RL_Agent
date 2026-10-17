# Path: planners/mining_geometry.py | Purpose: Share pure drill-row geometry across deterministic and training planners.

from __future__ import annotations

Point = tuple[float, float]


def mining_drill_positions(origin: Point, machine_count: int) -> list[Point]:
    """Centres for a south-facing drill row whose output is a local belt."""
    ox, oy = origin
    return [(ox + 1.5 + (3 * index), oy - 1.5) for index in range(machine_count)]


def paired_mining_drill_positions(origin: Point, machine_count: int) -> list[Point]:
    """Three-above/belt/three-below geometry for a complete mine unit."""
    upper = mining_drill_positions(origin, machine_count)
    belt_y = origin[1] + 0.5
    return upper + [(x, belt_y + 2) for x, _ in upper]
