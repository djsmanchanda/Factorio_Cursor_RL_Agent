# Path: tests/test_spidertron_tour.py
# Purpose: Deterministic tour + ring geometry. The lawnmower tour must cover a box
#          within the given step; concentric_rings must partition a field center-out
#          so the build expands outward, ring by ring.

from __future__ import annotations

import math

import pytest

from tools.spidertron_build import (
    concentric_rings,
    lawnmower_waypoints,
    square_grid,
)


def _nearest_distance(point, waypoints):
    return min(math.hypot(point[0] - wx, point[1] - wy) for wx, wy in waypoints)


def test_covers_every_tile_within_step():
    step = 36.0
    bbox = (0.0, 0.0, 108.0, 90.0)
    waypoints = lawnmower_waypoints(*bbox, step)
    min_x, min_y, max_x, max_y = bbox
    x = min_x
    while x <= max_x:
        y = min_y
        while y <= max_y:
            assert _nearest_distance((x, y), waypoints) <= step + 1e-6
            y += 3
        x += 3


def test_consecutive_hops_never_exceed_step():
    step = 40.0
    waypoints = lawnmower_waypoints(-50.0, -50.0, 70.0, 33.0, step)
    for (ax, ay), (bx, by) in zip(waypoints, waypoints[1:]):
        assert math.hypot(bx - ax, by - ay) <= step + 1e-6


def test_endpoints_are_included():
    waypoints = lawnmower_waypoints(0.0, 0.0, 100.0, 50.0, 30.0)
    xs = {round(x, 6) for x, _ in waypoints}
    ys = {round(y, 6) for _, y in waypoints}
    assert 0.0 in xs and 100.0 in xs
    assert 0.0 in ys and 50.0 in ys


def test_serpentine_alternates_direction():
    waypoints = lawnmower_waypoints(0.0, 0.0, 100.0, 100.0, 50.0)
    # xs per axis: 0,50,100. First row left->right, second row right->left.
    row0 = [x for x, y in waypoints if y == 0.0]
    row1 = [x for x, y in waypoints if y == 50.0]
    assert row0 == [0.0, 50.0, 100.0]
    assert row1 == [100.0, 50.0, 0.0]


def test_is_deterministic():
    args = (-10.0, -20.0, 55.0, 44.0, 25.0)
    assert lawnmower_waypoints(*args) == lawnmower_waypoints(*args)


def test_single_point_box_yields_one_waypoint():
    assert lawnmower_waypoints(7.0, 9.0, 7.0, 9.0, 30.0) == [(7.0, 9.0)]


def test_box_smaller_than_step_is_served_from_one_central_stop():
    # A box that fits within one step needs no touring: one stop at its center covers
    # it (avoids teleports that would interrupt in-flight bots).
    waypoints = lawnmower_waypoints(0.0, 0.0, 10.0, 10.0, 40.0)
    assert waypoints == [(5.0, 5.0)]


def test_invalid_step_rejected():
    with pytest.raises(ValueError):
        lawnmower_waypoints(0.0, 0.0, 10.0, 10.0, 0.0)


def test_inverted_box_rejected():
    with pytest.raises(ValueError):
        lawnmower_waypoints(10.0, 0.0, 0.0, 10.0, 5.0)


# --- concentric rings -------------------------------------------------------

def test_rings_are_ordered_center_out():
    positions = square_grid(49, 7.0, (0.0, 0.0))  # 7x7 grid, extent +-21
    rings = concentric_rings(positions, (0.0, 0.0), 14.0)
    assert len(rings) >= 2
    # Every cell in ring k is strictly closer (by Chebyshev) than every cell in
    # ring k+1 -- the build front only moves outward.
    def max_cheb(ring):
        return max(max(abs(x), abs(y)) for x, y in ring)

    def min_cheb(ring):
        return min(max(abs(x), abs(y)) for x, y in ring)

    for inner, outer in zip(rings, rings[1:]):
        assert max_cheb(inner) < min_cheb(outer) + 1e-9


def test_rings_partition_every_position_exactly_once():
    positions = square_grid(64, 6.0, (10.0, -5.0))
    rings = concentric_rings(positions, (10.0, -5.0), 12.0)
    flat = [p for ring in rings for p in ring]
    assert sorted(flat) == sorted(positions)
    assert len(flat) == len(positions)


def test_ring_zero_contains_the_center():
    positions = square_grid(25, 7.0, (0.0, 0.0))
    rings = concentric_rings(positions, (0.0, 0.0), 14.0)
    assert (0.0, 0.0) in rings[0]


def test_rings_are_deterministic():
    positions = square_grid(36, 5.0, (3.0, 3.0))
    assert concentric_rings(positions, (3.0, 3.0), 10.0) == concentric_rings(
        positions, (3.0, 3.0), 10.0
    )


def test_invalid_ring_width_rejected():
    with pytest.raises(ValueError):
        concentric_rings([(0.0, 0.0)], (0.0, 0.0), 0.0)


def test_square_grid_is_centered_and_sized():
    points = square_grid(9, 10.0, (0.0, 0.0))
    assert len(points) == 9
    assert (0.0, 0.0) in points  # center cell present for an odd perfect square
    xs = [x for x, _ in points]
    assert min(xs) == -10.0 and max(xs) == 10.0
