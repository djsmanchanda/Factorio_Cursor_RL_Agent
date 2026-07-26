# Path: tests/test_zoning.py
# Purpose: Pin block contracts, late-game rezoning gates, and release-permit lifecycle safety.

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from planners.land_value import (
    DevelopmentMetrics,
    LATE_GAME_SCIENCE_RATE_PER_TICK,
    LandValuePolicy,
)
from planners.zoning import (
    BLOCK_SIZE,
    DEPLETION_REZONE_RATIO,
    LAND_SCARCITY_RATIO,
    BlockGrid,
    CellReleasePermit,
    CompletedMigration,
    MigrationPhase,
    OrePatch,
    Rect,
    RezoneVerdict,
    StageKind,
    allocate_block,
    block_contract,
    evaluate_rezoning,
    issue_cell_release_permit,
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
# 5. The allocation is a block contract, not just a rectangle
# --------------------------------------------------------------------------


def test_block_contract_validates_against_the_canonical_block_schema():
    schema = json.loads(
        (ROOT / "schemas" / "block.schema.json").read_text(encoding="utf-8")
    )
    allocated = allocate_block(GRID, StageKind.SMELTING, [_patch()], near=(0.0, 0.0))
    assert allocated is not None

    contract = block_contract(
        allocated,
        "IRON_SMELTING_01",
        inputs={"iron-ore": 30.0},
        outputs={"iron-plate": 24.0},
    )

    jsonschema.validate(contract, schema)
    assert contract["category"] == "smelting"
    assert contract["size"] == {"w": int(BLOCK_SIZE), "h": int(BLOCK_SIZE)}
    assert contract["inputs"] == [{"item": "iron-ore", "rate": 30.0}]


def test_a_block_contract_with_no_declared_flows_is_still_schema_valid():
    schema = json.loads(
        (ROOT / "schemas" / "block.schema.json").read_text(encoding="utf-8")
    )
    allocated = allocate_block(GRID, StageKind.MINING, [_patch()], near=(0.0, 0.0))
    assert allocated is not None

    jsonschema.validate(block_contract(allocated, "IRON_MINE_01"), schema)


# --------------------------------------------------------------------------
# 6. Rezoning an ore cell
# --------------------------------------------------------------------------


def test_a_healthy_patch_with_room_to_spare_keeps_its_cell_for_mining():
    decision = evaluate_rezoning((0, 0), _patch(), free_cells=20)

    assert decision.verdict is RezoneVerdict.KEEP_MINING
    assert decision.trigger == "none"
    assert decision.requires_shadow_migration is False
    assert decision.cell == (0, 0)


def test_a_substantially_depleted_patch_retires_its_mining_outward():
    spent = _patch(amount=200_000.0, initial=1_000_000.0)  # 20% left

    decision = evaluate_rezoning((0, 0), spent, free_cells=20, development=MATURE)

    assert decision.verdict is RezoneVerdict.RETIRE_MINING_OUTWARD
    assert decision.trigger == "depletion"
    assert decision.requires_shadow_migration is True
    assert decision.remaining_fraction == pytest.approx(0.2)


def test_the_depletion_threshold_is_inclusive_and_configurable():
    at_threshold = _patch(amount=DEPLETION_REZONE_RATIO * 1_000_000.0)
    just_above = _patch(amount=DEPLETION_REZONE_RATIO * 1_000_000.0 + 1.0)

    assert (
        evaluate_rezoning(
            (0, 0), at_threshold, free_cells=20, development=MATURE
        ).verdict
        is RezoneVerdict.RETIRE_MINING_OUTWARD
    )
    assert (
        evaluate_rezoning((0, 0), just_above, free_cells=20).verdict
        is RezoneVerdict.KEEP_MINING
    )
    assert (
        evaluate_rezoning(
            (0, 0),
            just_above,
            free_cells=20,
            depletion_threshold=0.9,
            development=MATURE,
        ).verdict
        is RezoneVerdict.RETIRE_MINING_OUTWARD
    )


def test_a_land_starved_base_rezones_a_still_rich_cell_to_production():
    """Fewer than LAND_SCARCITY_RATIO x the requested cells are free, so a
    central cell is worth more as production space than as a mine."""
    decision = evaluate_rezoning(
        (0, 0), _patch(), free_cells=1, requested_cells=1, development=MATURE
    )

    assert decision.verdict is RezoneVerdict.REZONE_TO_PRODUCTION
    assert decision.trigger == "land_pressure"
    assert decision.land_headroom == pytest.approx(1.0)
    assert decision.requires_shadow_migration is True


def test_the_land_scarcity_threshold_is_exclusive_and_configurable():
    exactly_enough = evaluate_rezoning(
        (0, 0), _patch(), free_cells=4, requested_cells=2
    )
    a_shade_short = evaluate_rezoning(
        (0, 0), _patch(), free_cells=3, requested_cells=2, development=MATURE
    )

    assert exactly_enough.land_headroom == LAND_SCARCITY_RATIO
    assert exactly_enough.verdict is RezoneVerdict.KEEP_MINING
    assert a_shade_short.verdict is RezoneVerdict.REZONE_TO_PRODUCTION
    assert (
        evaluate_rezoning(
            (0, 0),
            _patch(),
            free_cells=20,
            scarcity_threshold=100.0,
            development=MATURE,
        ).verdict
        is RezoneVerdict.REZONE_TO_PRODUCTION
    )


def test_depletion_outranks_land_pressure_when_both_conditions_hold():
    """A spent patch gives up nothing, so retire the mine rather than merely
    sharing the cell with production."""
    spent = _patch(amount=100_000.0, initial=1_000_000.0)

    decision = evaluate_rezoning((2, -3), spent, free_cells=0, development=MATURE)

    assert decision.verdict is RezoneVerdict.RETIRE_MINING_OUTWARD
    assert decision.trigger == "depletion"


def test_every_decision_names_the_cell_and_the_numbers_that_drove_it():
    kept = evaluate_rezoning((0, 0), _patch(), free_cells=20)
    pressured = evaluate_rezoning((1, 2), _patch(), free_cells=1)
    spent = evaluate_rezoning(
        (0, 0), _patch(amount=1.0), free_cells=20, development=MATURE
    )

    assert "keep it for mining" in kept.reason
    assert "cell (1, 2)" in pressured.reason and "land pressure" in pressured.reason
    assert "iron-ore" in spent.reason and "0%" in spent.reason


def test_rezoning_rejects_a_zero_sized_request():
    with pytest.raises(ValueError, match="requested_cells"):
        evaluate_rezoning((0, 0), _patch(), free_cells=20, requested_cells=0)


def test_rezoning_is_pure_and_repeatable():
    patch = _patch(amount=300_000.0)

    first = evaluate_rezoning((0, 0), patch, free_cells=3, requested_cells=2)
    again = evaluate_rezoning((0, 0), patch, free_cells=3, requested_cells=2)

    assert first == again


def test_near_is_a_real_score_not_only_a_search_window():
    allocated = allocate_block(
        GRID,
        StageKind.CONVERSION,
        [],
        near=(0.0, 0.0),
        rings=6,
    )

    assert allocated is not None
    assert allocated.cell == (0, 0)
    assert allocated.proximity_tiles == pytest.approx(64.0)


def test_intrinsic_land_value_places_research_inside_bulk_smelting():
    policy = LandValuePolicy(base_center=(32.0, 32.0), block_pitch=GRID.pitch)

    research = allocate_block(
        GRID,
        StageKind.RESEARCH,
        [],
        rings=4,
        land_value_policy=policy,
    )
    smelting = allocate_block(
        GRID,
        StageKind.SMELTING,
        [],
        rings=4,
        land_value_policy=policy,
        machine_count=30,
    )

    assert research is not None and smelting is not None
    assert policy.intrinsic_value(research.box.centre) == pytest.approx(1.0)
    assert policy.intrinsic_value(smelting.box.centre) == pytest.approx(0.0)


def test_land_pressure_cannot_displace_rich_ore_before_late_game():
    decision = evaluate_rezoning(
        (0, 0),
        _patch(),
        free_cells=0,
        requested_cells=1,
    )

    assert decision.verdict is RezoneVerdict.KEEP_MINING
    assert decision.trigger == "late_game_gate"
    assert decision.requires_shadow_migration is False


def test_release_permit_requires_completed_bound_migration():
    with pytest.raises(ValueError, match="CELL_RELEASED"):
        CompletedMigration(
            migration_id="migration-1",
            grid=GRID,
            source_block_id="mine-1",
            target_block_id="shadow-1",
            source_cell=(2, 3),
            source_box=GRID.block_box((2, 3)),
            shadow_stable_ticks=3_600,
            rail_connected=True,
            traffic_diverted=True,
            old_drained=True,
            old_decommissioned=True,
            phase=MigrationPhase.SHADOW_STABLE,
        )

    permit = _released((2, 3))
    assert permit.matches(GRID)
    assert not permit.matches(BlockGrid(anchor=(80.0, 0.0)))
    assert permit.box == GRID.block_box((2, 3))
    assert permit.released_tick == 42_000


@pytest.mark.parametrize("bad_tick", [True, 1.5])
def test_release_tick_must_be_an_integer(bad_tick):
    decision = evaluate_rezoning(
        (2, 3),
        _patch(amount=0.0),
        free_cells=20,
        development=MATURE,
    )
    migration = CompletedMigration(
        migration_id="migration-1",
        grid=GRID,
        source_block_id="mine-1",
        target_block_id="shadow-1",
        source_cell=(2, 3),
        source_box=GRID.block_box((2, 3)),
        shadow_stable_ticks=3_600,
        rail_connected=True,
        traffic_diverted=True,
        old_drained=True,
        old_decommissioned=True,
    )
    with pytest.raises(ValueError, match="integer"):
        issue_cell_release_permit(decision, migration, released_tick=bad_tick)


def test_depleted_ore_cannot_relocate_before_late_game_gate():
    decision = evaluate_rezoning(
        (0, 0),
        _patch(amount=0.0),
        free_cells=20,
    )
    assert decision.verdict is RezoneVerdict.KEEP_MINING
    assert decision.trigger == "late_game_gate"


def test_completed_migration_rejects_missing_rail_evidence():
    with pytest.raises(ValueError, match="evidence is incomplete"):
        CompletedMigration(
            migration_id="migration-1",
            grid=GRID,
            source_block_id="mine-1",
            target_block_id="shadow-1",
            source_cell=(0, 0),
            source_box=GRID.block_box((0, 0)),
            shadow_stable_ticks=3_600,
            rail_connected=False,
            traffic_diverted=True,
            old_drained=True,
            old_decommissioned=True,
        )
