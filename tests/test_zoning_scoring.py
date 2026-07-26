# Path: tests/test_zoning_scoring.py
# Purpose: Pin deterministic block scoring, ore legality, occupancy, and tie-breaking.


from planners.land_value import DevelopmentMetrics, LATE_GAME_SCIENCE_RATE_PER_TICK
from planners.zoning import (
    BlockGrid,
    CompletedMigration,
    LandClass,
    MigrationPhase,
    OrePatch,
    Rect,
    RezoneVerdict,
    StageKind,
    allocate_block,
    evaluate_rezoning,
    issue_cell_release_permit,
    link_cost,
)

GRID = BlockGrid(anchor=(0.0, 0.0))
MATURE = DevelopmentMetrics(
    required_sciences=("automation",),
    science_rates_per_tick={"automation": LATE_GAME_SCIENCE_RATE_PER_TICK},
    measurement_window_ticks=36_000,
)


def _patch(bounds=Rect(4, 4, 24, 24)):
    return OrePatch("iron-ore", bounds, 1_000_000.0, 1_000_000.0)


def _permit(cell):
    decision = evaluate_rezoning(
        cell,
        OrePatch("iron-ore", Rect(4, 4, 24, 24), 1_000_000.0, 0.0),
        free_cells=20,
        development=MATURE,
    )
    assert decision.verdict is RezoneVerdict.RETIRE_MINING_OUTWARD
    migration = CompletedMigration(
        migration_id="migration",
        grid=GRID,
        source_block_id="mine",
        target_block_id="shadow",
        source_cell=cell,
        source_box=GRID.block_box(cell),
        shadow_stable_ticks=3_600,
        rail_connected=True,
        traffic_diverted=True,
        old_drained=True,
        old_decommissioned=True,
        phase=MigrationPhase.CELL_RELEASED,
    )
    return issue_cell_release_permit(decision, migration, released_tick=42_000)


def test_link_cost_sums_manhattan_interface_distance():
    assert link_cost(Rect(0, 0, 4, 4), [(2.0, 12.0), (-8.0, 2.0)]) == 20.0


def test_mining_requires_and_selects_ore_land():
    allocated = allocate_block(GRID, StageKind.MINING, [_patch()], near=(0.0, 0.0))
    assert allocated is not None and allocated.cell == (0, 0)
    assert allocated.land is LandClass.ORE
    assert allocate_block(GRID, StageKind.MINING, [], near=(0.0, 0.0)) is None


def test_production_reserves_ore_and_uses_free_land():
    allocated = allocate_block(GRID, StageKind.CONVERSION, [_patch()], near=(0.0, 0.0))
    assert allocated is not None and allocated.cell != (0, 0)
    assert allocated.land is LandClass.FREE


def test_occupied_immutable_cell_is_never_reallocated():
    first = allocate_block(GRID, StageKind.CONVERSION, [], near=(0.0, 0.0))
    second = allocate_block(
        GRID,
        StageKind.CONVERSION,
        [],
        near=(0.0, 0.0),
        occupied=[first.cell],
    )
    assert first is not None and second is not None and first.cell != second.cell


def test_connections_pull_allocation_toward_their_interfaces():
    allocated = allocate_block(
        GRID,
        StageKind.CONVERSION,
        [_patch()],
        near=(0.0, 0.0),
        connects_to=[(600.0, 32.0)],
    )
    assert allocated is not None and allocated.cell[0] > 0


def test_apron_is_a_penalty_not_an_ore_release():
    spilling = _patch(Rect(4, 4, 76, 24))
    occupied = [(0, 1), (1, 1), (-1, 1), (0, -1), (1, -1), (-1, -1), (-1, 0)]
    allocated = allocate_block(
        GRID,
        StageKind.CONVERSION,
        [spilling],
        near=(0.0, 0.0),
        rings=1,
        occupied=occupied,
    )
    assert allocated is not None and allocated.cell == (1, 0)
    assert allocated.land is LandClass.ORE_BUFFER


def test_release_permit_is_exactly_cell_scoped():
    occupied = [(1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
    assert (
        allocate_block(
            GRID,
            StageKind.CONVERSION,
            [_patch()],
            near=(0.0, 0.0),
            rings=1,
            occupied=occupied,
        )
        is None
    )
    allocated = allocate_block(
        GRID,
        StageKind.CONVERSION,
        [_patch()],
        near=(0.0, 0.0),
        rings=1,
        occupied=occupied,
        release_permits={_permit((0, 0))},
    )
    assert allocated is not None and allocated.cell == (0, 0)


def test_allocation_is_independent_of_occupied_input_order():
    taken = [(1, 0), (0, 1)]
    first = allocate_block(
        GRID, StageKind.CONVERSION, [_patch()], near=(0.0, 0.0), occupied=taken
    )
    again = allocate_block(
        GRID,
        StageKind.CONVERSION,
        [_patch()],
        near=(0.0, 0.0),
        occupied=reversed(taken),
    )
    assert first == again


def test_equal_scores_break_ties_by_cell_coordinate():
    allocated = allocate_block(
        GRID,
        StageKind.CONVERSION,
        [_patch()],
        near=(0.0, 0.0),
        connects_to=[(0.0, 0.0)],
        rings=1,
    )
    assert allocated is not None and allocated.cell == (-1, 0)


def test_allocator_gives_up_at_bounded_search_limit():
    assert (
        allocate_block(
            GRID,
            StageKind.CONVERSION,
            [_patch()],
            near=(0.0, 0.0),
            rings=0,
        )
        is None
    )


def test_focus_falls_back_to_connection_then_anchor():
    connected = allocate_block(
        GRID,
        StageKind.CONVERSION,
        [_patch()],
        connects_to=[(600.0, 600.0)],
        rings=1,
    )
    anchored = allocate_block(GRID, StageKind.CONVERSION, [_patch()], rings=1)
    assert connected is not None and connected.cell[0] >= 7
    assert anchored is not None and anchored.cell in GRID.cells_within((0, 0), 1)
