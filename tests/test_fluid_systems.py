# Path: tests/test_fluid_systems.py
# Purpose: Deterministic tests for core/fluid_systems.py -- offset rotation
# and flip, world-coordinate connection points (chemical-plant, oil-refinery,
# pump), underground span validation, the anti-mixing network purity guard,
# pipeline span validation, and the throughput approximation.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.fluid_systems import (
    CONNECTION_OFFSETS,
    MAX_PIPELINE_SPAN,
    MAX_UNDERGROUND_SPAN,
    PIPE_THROUGHPUT,
    TANK_CAPACITY,
    connection_points,
    estimate_throughput,
    flip_offset,
    rotate_offset,
    validate_network_purity,
    validate_pipeline_span,
    validate_underground_span,
)


# --- rotate_offset -----------------------------------------------------------

def test_rotate_offset_north_is_identity():
    assert rotate_offset((-1, -1), "north") == (-1, -1)


def test_rotate_offset_east_matches_verified_case():
    # Verified: a connection at (0, -1) (north of center) rotates east to (1, 0).
    assert rotate_offset((0, -1), "east") == (1, 0)


def test_rotate_offset_unknown_direction_raises():
    with pytest.raises(ValueError):
        rotate_offset((0, -1), "northeast")


def test_chemical_plant_inputs_stay_opposite_outputs_through_all_directions():
    offsets = CONNECTION_OFFSETS["chemical-plant"]
    inputs = [o["offset"] for o in offsets if o["role"] == "input"]
    outputs = [o["offset"] for o in offsets if o["role"] == "output"]
    for direction in ("north", "east", "south", "west"):
        rotated_inputs = {rotate_offset(o, direction) for o in inputs}
        rotated_outputs = {rotate_offset(o, direction) for o in outputs}
        # Opposite means negating each rotated input offset lands exactly on
        # the set of rotated output offsets (chemical-plant's inputs/outputs
        # are point-symmetric through the center in every orientation).
        negated_inputs = {(-x, -y) for x, y in rotated_inputs}
        assert negated_inputs == rotated_outputs


# --- flip_offset -------------------------------------------------------------

def test_flip_horizontal_negates_x():
    assert flip_offset((1, -1), "horizontal") == (-1, -1)


def test_flip_vertical_negates_y():
    assert flip_offset((1, -1), "vertical") == (1, 1)


def test_flip_unknown_axis_raises():
    with pytest.raises(ValueError):
        flip_offset((1, -1), "diagonal")


# --- connection_points: chemical-plant ---------------------------------------

def test_connection_points_chemical_plant_world_coordinates_north():
    points = connection_points("chemical-plant", center=(10, 10), direction="north")
    positions_by_role = {}
    for p in points:
        positions_by_role.setdefault(p["role"], set()).add(p["position"])
    assert positions_by_role["input"] == {(9, 9), (11, 9)}
    assert positions_by_role["output"] == {(9, 11), (11, 11)}


def test_connection_points_chemical_plant_rotated_east():
    points = connection_points("chemical-plant", center=(0, 0), direction="east")
    positions_by_role = {}
    for p in points:
        positions_by_role.setdefault(p["role"], set()).add(p["position"])
    # north offsets (-1,-1),(1,-1) rotate east via (x,y)->(-y,x)
    assert positions_by_role["input"] == {(1, -1), (1, 1)}
    assert positions_by_role["output"] == {(-1, -1), (-1, 1)}


def test_connection_points_chemical_plant_with_flip():
    plain = connection_points("chemical-plant", center=(0, 0), direction="north")
    flipped = connection_points(
        "chemical-plant", center=(0, 0), direction="north", flip="horizontal"
    )
    plain_positions = {p["position"] for p in plain}
    flipped_positions = {p["position"] for p in flipped}
    assert flipped_positions == {(-x, y) for x, y in plain_positions}


# --- connection_points: oil-refinery (now verified) --------------------------

def test_connection_points_oil_refinery_verified_north():
    points = connection_points("oil-refinery", center=(0, 0), direction="north")
    positions_by_role = {}
    for p in points:
        positions_by_role.setdefault(p["role"], set()).add(p["position"])
    assert positions_by_role["input"] == {(-1, 2), (1, 2)}
    assert positions_by_role["output"] == {(-2, -2), (0, -2), (2, -2)}
    assert len(points) == 5


# --- connection_points: pump (directional, half-tile offsets) ---------------

def test_pump_north_facing_input_is_south_of_output():
    points = connection_points("pump", center=(0, 0), direction="north")
    by_role = {p["role"]: p["position"] for p in points}
    # South = larger y in this offset convention (matches chemical-plant's
    # inputs-at-y=-1/outputs-at-y=+1 north/south convention).
    assert by_role["input"][1] > by_role["output"][1]
    assert by_role["output"] == (0.0, -0.5)
    assert by_role["input"] == (0.0, 0.5)


def test_pump_rotated_east_puts_output_on_east_side():
    points = connection_points("pump", center=(0, 0), direction="east")
    by_role = {p["role"]: p["position"] for p in points}
    assert by_role["output"][0] > 0  # east = positive x
    assert by_role["input"][0] < 0


# --- connection_points: unverified / unknown entities ------------------------

def test_connection_points_unknown_entity_raises():
    with pytest.raises(ValueError):
        connection_points("pipe", center=(0, 0))


# --- validate_underground_span -----------------------------------------------

def test_underground_span_exactly_max_ok():
    validate_underground_span((0, 0), (10, 0))


def test_underground_span_one_over_max_raises():
    with pytest.raises(ValueError):
        validate_underground_span((0, 0), (11, 0))


def test_underground_span_diagonal_raises():
    with pytest.raises(ValueError):
        validate_underground_span((0, 0), (5, 5))


def test_underground_span_same_column_ok():
    validate_underground_span((3, 0), (3, MAX_UNDERGROUND_SPAN))


# --- validate_network_purity ---------------------------------------------------

def test_network_purity_same_fluid_adjacent_ok():
    segments = [
        {"fluid": "water", "tiles": [(0, 0), (1, 0)], "separated_by_pump": False},
        {"fluid": "water", "tiles": [(2, 0)], "separated_by_pump": False},
    ]
    validate_network_purity(segments)  # no raise


def test_network_purity_pump_separated_different_fluids_ok():
    segments = [
        {"fluid": "water", "tiles": [(0, 0)], "separated_by_pump": True},
        {"fluid": "crude-oil", "tiles": [(1, 0)], "separated_by_pump": False},
    ]
    validate_network_purity(segments)  # no raise: pump sanctions the adjacency


def test_network_purity_adjacent_different_fluids_raises_naming_tiles():
    segments = [
        {"fluid": "water", "tiles": [(0, 0)], "separated_by_pump": False},
        {"fluid": "crude-oil", "tiles": [(1, 0)], "separated_by_pump": False},
    ]
    with pytest.raises(ValueError) as exc_info:
        validate_network_purity(segments)
    message = str(exc_info.value)
    assert "(0, 0)" in message
    assert "(1, 0)" in message


def test_network_purity_non_adjacent_different_fluids_ok():
    segments = [
        {"fluid": "water", "tiles": [(0, 0)], "separated_by_pump": False},
        {"fluid": "crude-oil", "tiles": [(5, 5)], "separated_by_pump": False},
    ]
    validate_network_purity(segments)  # no raise: not touching


# --- validate_pipeline_span ---------------------------------------------------

def test_pipeline_span_within_limit_ok():
    validate_pipeline_span([(0, 0), (MAX_PIPELINE_SPAN, 0)])


def test_pipeline_span_over_limit_raises():
    with pytest.raises(ValueError):
        validate_pipeline_span([(0, 0), (MAX_PIPELINE_SPAN + 1, 0)])


def test_pipeline_span_over_limit_ok_with_pump():
    validate_pipeline_span([(0, 0), (MAX_PIPELINE_SPAN + 100, 0)], pumps=[(160, 0)])


# --- estimate_throughput -------------------------------------------------------

def test_estimate_throughput_short_run_full_rate():
    assert estimate_throughput(5, practical=True) == pytest.approx(PIPE_THROUGHPUT["practical"])


def test_estimate_throughput_theoretical_short_run():
    assert estimate_throughput(5, practical=False) == pytest.approx(PIPE_THROUGHPUT["theoretical"])


def test_estimate_throughput_degrades_with_length():
    short = estimate_throughput(50, practical=True)
    long = estimate_throughput(300, practical=True)
    assert long < short


def test_estimate_throughput_zero_at_or_beyond_max_span():
    assert estimate_throughput(MAX_PIPELINE_SPAN, practical=True) == 0.0
    assert estimate_throughput(MAX_PIPELINE_SPAN + 50, practical=True) == 0.0


def test_estimate_throughput_negative_length_raises():
    with pytest.raises(ValueError):
        estimate_throughput(-1)


# --- sanity: capacity constant matches brief ----------------------------------

def test_tank_capacity_constant():
    assert TANK_CAPACITY == 25000
