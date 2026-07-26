# Path: tests/test_zoning_geometry.py
# Purpose: Pin immutable grid geometry, corridor reservation, and ore-land classification.

from __future__ import annotations

import pytest

from planners.zoning import (
    BLOCK_PITCH,
    BLOCK_SIZE,
    CORRIDOR_WIDTH,
    BlockGrid,
    LandClass,
    OrePatch,
    Rect,
    classify_cell,
    classify_land,
    ore_coverage,
)

GRID = BlockGrid(anchor=(0.0, 0.0))
_PATCH_BOUNDS = Rect(4, 4, 24, 24)


def _patch(
    bounds: Rect = _PATCH_BOUNDS,
    *,
    amount: float = 1_000_000.0,
    initial: float = 1_000_000.0,
    tiles=None,
) -> OrePatch:
    return OrePatch(
        resource="iron-ore",
        bounds=bounds,
        initial_amount=initial,
        amount=amount,
        ore_tiles=tiles,
    )


def test_rect_tiles_and_tile_area_agree_on_fractional_boxes():
    box = Rect(0.5, 0.5, 3.5, 3.5)

    tiles = list(box.tiles())

    assert len(tiles) == box.tile_area == 16
    assert tiles[0] == (0, 0) and tiles[-1] == (3, 3)
    assert all(box.contains_tile(*tile) for tile in tiles)


def test_rect_rejects_a_degenerate_box():
    with pytest.raises(ValueError, match="positive extent"):
        Rect(0, 0, 0, 5)


def test_ore_patch_rejects_a_survey_with_no_starting_amount():
    with pytest.raises(ValueError, match="initial_amount"):
        OrePatch(
            resource="iron-ore", bounds=Rect(0, 0, 1, 1), initial_amount=0.0, amount=0.0
        )


def test_remaining_fraction_is_clamped_so_a_regrown_patch_never_exceeds_one():
    assert _patch(amount=2_000_000.0, initial=1_000_000.0).remaining_fraction == 1.0
    assert _patch(amount=250_000.0, initial=1_000_000.0).remaining_fraction == 0.25


# --------------------------------------------------------------------------
# 1. The lattice: fixed spacing, corridors reserved even while empty
# --------------------------------------------------------------------------


def test_the_pitch_nests_into_the_documented_block_standard():
    """docs/13: micro blocks are 64, macro districts 256 or 512. Starting at
    the micro size means a district is a whole number of cells later, so the
    grid grows into the standard without ever being re-tiled."""
    assert BLOCK_SIZE == 64.0
    assert 256 % BLOCK_SIZE == 0 and 512 % BLOCK_SIZE == 0
    assert BLOCK_PITCH == BLOCK_SIZE + CORRIDOR_WIDTH


def test_the_corridor_is_wide_enough_for_the_four_lane_rail_standard():
    """docs/13 section 5.1 reserves four lanes. A rail is 2 tiles wide and
    needs room for signals and poles, so 4 tiles a lane is the floor -- rail
    must be layable later WITHOUT moving a deployed block."""
    assert CORRIDOR_WIDTH >= 4 * 4
    assert BLOCK_SIZE == BLOCK_PITCH - CORRIDOR_WIDTH


def test_blocks_never_touch_and_the_gap_is_exactly_the_reserved_corridor():
    here, east, south = (
        GRID.block_box((0, 0)),
        GRID.block_box((1, 0)),
        GRID.block_box((0, 1)),
    )

    assert not here.overlaps(east) and not here.overlaps(south)
    assert east.min_x - here.max_x == CORRIDOR_WIDTH
    assert south.min_y - here.max_y == CORRIDOR_WIDTH


def test_the_block_footprint_excludes_the_corridor_that_the_cell_reserves():
    """The corridor is reserved from day one even though nothing uses it yet;
    that reservation is the expansion room."""
    block, cell = GRID.block_box((2, -1)), GRID.cell_box((2, -1))

    assert block.max_x - block.min_x == BLOCK_SIZE
    assert cell.max_x - cell.min_x == BLOCK_PITCH
    assert cell.tile_area - block.tile_area > 0


def test_the_lattice_is_global_so_any_landmark_inside_a_cell_yields_one_grid():
    """Two callers naming different landmarks must not get grids offset from
    each other, which would drop blocks into each other's corridors."""
    from_roboport = BlockGrid.anchored_at((3.0, -1.0))
    from_a_chest = BlockGrid.anchored_at((60.5, -63.0))

    assert from_roboport == from_a_chest
    assert from_roboport.anchor == (0.0, -80.0)


def test_a_point_in_a_corridor_still_belongs_to_the_cell_before_it():
    assert GRID.cell_at((10.0, 10.0)) == (0, 0)
    assert GRID.cell_at((50.0, 10.0)) == (0, 0)  # inside the reserved corridor
    assert GRID.cell_at((80.0, 10.0)) == (1, 0)
    assert GRID.cell_at((-1.0, -1.0)) == (-1, -1)


def test_a_grid_rejects_a_corridor_it_cannot_reserve():
    with pytest.raises(ValueError, match="corridor"):
        BlockGrid(anchor=(0.0, 0.0), pitch=64.0, corridor_width=64.0)


# --------------------------------------------------------------------------
# 2. Land classification
# --------------------------------------------------------------------------


def test_a_box_over_the_patch_is_ore_land():
    assert classify_land(Rect(5, 5, 9, 9), [_patch()]) is LandClass.ORE


def test_a_box_just_off_the_patch_is_reserved_buffer_not_free_land():
    """The apron is the mine's egress -- inserters, output belt, chest, pole --
    so it stays reserved. It is THIN, because the furnaces are not part of the
    mine."""
    assert classify_land(Rect(26, 5, 30, 9), [_patch()]) is LandClass.ORE_BUFFER


def test_a_box_beyond_the_apron_is_free_land():
    assert classify_land(Rect(40, 5, 44, 9), [_patch()]) is LandClass.FREE


def test_the_apron_edge_is_exact_at_five_tiles():
    """Patch bbox ends at x=24, so the 5-tile apron ends at x=29."""
    patches = [_patch()]

    assert classify_land(Rect(28.5, 0, 32.5, 4), patches) is LandClass.ORE_BUFFER
    assert classify_land(Rect(29.0, 0, 33.0, 4), patches) is LandClass.FREE


def test_a_custom_apron_widens_the_reservation():
    box = Rect(40, 5, 44, 9)

    assert classify_land(box, [_patch()], apron=5.0) is LandClass.FREE
    assert classify_land(box, [_patch()], apron=25.0) is LandClass.ORE_BUFFER


def test_a_bay_inside_an_irregular_patch_bbox_is_buffer_not_ore():
    """With the real tile set known, a notch in a ragged patch must not be
    mistaken for drillable ground -- but it stays reserved, being inside the
    envelope the mine needs."""
    ragged = _patch(
        bounds=Rect(0, 0, 10, 10),
        tiles=frozenset((x, y) for x in range(5) for y in range(10)),
    )

    assert classify_land(Rect(6, 2, 8, 4), [ragged]) is LandClass.ORE_BUFFER
    assert classify_land(Rect(2, 2, 4, 4), [ragged]) is LandClass.ORE


def test_a_box_clipping_the_patch_corner_still_counts_as_ore_land():
    """Any overlap at all costs the base drilling surface."""
    assert classify_land(Rect(23, 23, 27, 27), [_patch()]) is LandClass.ORE


def test_land_with_no_surveyed_patches_is_free():
    assert classify_land(Rect(0, 0, 4, 4), []) is LandClass.FREE
    assert ore_coverage(Rect(0, 0, 4, 4), []) == 0.0


def test_ore_coverage_is_the_fraction_of_box_tiles_over_ore():
    coverage = ore_coverage(Rect(2, 2, 6, 6), [_patch(bounds=Rect(0, 0, 4, 4))])

    assert coverage == pytest.approx(0.25)  # 4 of 16 tiles


def test_overlapping_patches_are_not_double_counted():
    twins = [_patch(bounds=Rect(0, 0, 4, 4)), _patch(bounds=Rect(0, 0, 4, 4))]

    assert ore_coverage(Rect(0, 0, 4, 4), twins) == 1.0


def test_classify_cell_judges_the_block_footprint_not_the_corridor():
    """A patch lying only in cell (0,0)'s corridor must not make the block ore
    land -- nothing is built in a corridor."""
    in_the_corridor = _patch(bounds=Rect(66, 50, 75, 60))

    assert classify_cell(GRID, (0, 0), [_patch()]) is LandClass.ORE
    assert classify_cell(GRID, (0, 0), [in_the_corridor], apron=0.5) is LandClass.FREE
