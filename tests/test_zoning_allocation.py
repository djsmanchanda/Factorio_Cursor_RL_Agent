# Path: tests/test_zoning_allocation.py
# Purpose: Pin deterministic block allocation, cell-scoped ore release, and separate mine/smelter placement.

from __future__ import annotations

from pathlib import Path


from planners.land_value import (
    DevelopmentMetrics,
    LATE_GAME_SCIENCE_RATE_PER_TICK,
)
from planners.zoning import (
    BLOCK_PITCH,
    BLOCK_SIZE,
    BlockGrid,
    CellReleasePermit,
    CompletedMigration,
    LandClass,
    OrePatch,
    Rect,
    evaluate_rezoning,
    issue_cell_release_permit,
    plan_extraction,
)

ROOT = Path(__file__).resolve().parents[1]

# Cell (0, 0) is the 64x64 block Rect(0, 0, 64, 64),
# and cell (1, 0) starts after its 16-tile corridor at x=80.
GRID = BlockGrid(anchor=(0.0, 0.0))
MATURE = DevelopmentMetrics(
    required_sciences=("automation", "logistic"),
    science_rates_per_tick={
        "automation": LATE_GAME_SCIENCE_RATE_PER_TICK,
        "logistic": LATE_GAME_SCIENCE_RATE_PER_TICK,
    },
    measurement_window_ticks=36_000,
)

# A patch small enough to sit entirely inside cell (0, 0)'s block.
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


def _released(cell: tuple[int, int]) -> CellReleasePermit:
    decision = evaluate_rezoning(
        cell,
        _patch(amount=0.0),
        free_cells=20,
        development=MATURE,
    )
    migration = CompletedMigration(
        migration_id=f"migration-{cell}",
        grid=GRID,
        source_block_id=f"mine-{cell}",
        target_block_id=f"shadow-{cell}",
        source_cell=cell,
        source_box=GRID.block_box(cell),
        shadow_stable_ticks=3_600,
        rail_connected=True,
        traffic_diverted=True,
        old_drained=True,
        old_decommissioned=True,
    )
    return issue_cell_release_permit(decision, migration, released_tick=42_000)


# --------------------------------------------------------------------------
# 4. The mine and its smelter are two blocks, not one line
# --------------------------------------------------------------------------


def test_plan_extraction_puts_drills_on_ore_and_furnaces_in_another_block():
    placement = plan_extraction(GRID, [_patch()], near=(0.0, 0.0))

    assert placement is not None
    assert placement.mine.cell == (0, 0) and placement.mine.land is LandClass.ORE
    assert placement.smelter.cell != placement.mine.cell
    assert placement.smelter.land is LandClass.FREE
    assert placement.smelter.category == "smelting"


def test_the_two_blocks_are_separate_cells_that_never_share_ground():
    placement = plan_extraction(GRID, [_patch()], near=(0.0, 0.0))

    assert placement is not None
    assert not placement.mine.box.overlaps(placement.smelter.box)
    assert placement.transport_tiles >= BLOCK_PITCH - BLOCK_SIZE


def test_the_smelter_is_pulled_toward_its_mine_but_never_onto_the_ore():
    placement = plan_extraction(GRID, [_patch()], near=(0.0, 0.0))
    again = plan_extraction(GRID, [_patch()], near=(0.0, 0.0))

    assert placement is not None
    assert placement.smelter.cell in GRID.cells_within(placement.mine.cell, 1)
    assert placement == again


def test_a_downstream_consumer_pulls_the_smelter_away_from_the_mine():
    """The smelter is scored against the mine AND whatever it feeds, so it
    settles between the two rather than always hugging the patch."""
    alone = plan_extraction(GRID, [_patch()], near=(0.0, 0.0), rings=3)
    feeding_east = plan_extraction(
        GRID, [_patch()], near=(0.0, 0.0), rings=3, smelter_connects_to=[(600.0, 24.0)]
    )

    assert alone is not None and feeding_east is not None
    assert feeding_east.smelter.cell[0] > alone.smelter.cell[0]


def test_plan_extraction_gives_up_rather_than_merging_the_two_stages():
    """No legal cell for either half returns None; it never falls back to
    stacking the furnaces onto the drill row."""
    assert plan_extraction(GRID, [], near=(0.0, 0.0)) is None
    assert plan_extraction(GRID, [_patch()], near=(0.0, 0.0), rings=0) is None


def test_a_rezoned_patch_lets_the_smelter_take_an_ore_cell_as_a_last_resort():
    everything_in_reach_is_ore = _patch(bounds=Rect(-100, -100, 120, 120))

    placement = plan_extraction(
        GRID,
        [everything_in_reach_is_ore],
        near=(0.0, 0.0),
        rings=1,
        release_permits={_released((-1, 0))},
    )

    assert placement is not None and placement.smelter.land is LandClass.ORE
