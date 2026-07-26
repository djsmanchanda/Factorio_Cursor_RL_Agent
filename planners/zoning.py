# Path: planners/zoning.py
# Purpose: Deterministic, decision-only allocation and rezoning policy for rail-separated city blocks.

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from planners.land_value import (
    BLOCK_CATEGORIES,
    DevelopmentMetrics,
    LandValuePolicy,
    StageKind,
)
from planners.zoning_geometry import (
    BLOCK_PITCH,
    BLOCK_SIZE,
    CORRIDOR_WIDTH,
    MINING_APRON_TILES,
    BlockGrid,
    Cell,
    LandClass,
    OrePatch,
    Point,
    Rect,
    classify_cell,
    classify_land,
    ore_coverage,
)

__all__ = [
    "BLOCK_PITCH",
    "BLOCK_SIZE",
    "CORRIDOR_WIDTH",
    "DEPLETION_REZONE_RATIO",
    "LAND_SCARCITY_RATIO",
    "BlockGrid",
    "CellReleasePermit",
    "CompletedMigration",
    "DevelopmentMetrics",
    "LandClass",
    "LandValuePolicy",
    "MigrationPhase",
    "OrePatch",
    "Rect",
    "RezoneVerdict",
    "StageKind",
    "allocate_block",
    "block_contract",
    "classify_cell",
    "classify_land",
    "evaluate_rezoning",
    "issue_cell_release_permit",
    "link_cost",
    "ore_coverage",
    "plan_extraction",
]

DEFAULT_SEARCH_RINGS = 6
ORE_APRON_PENALTY_TILES = 60.0
ORE_SQUAT_PENALTY_TILES = 120.0
ORE_COVERAGE_WEIGHT_TILES = 40.0
DEPLETION_REZONE_RATIO = 0.25
LAND_SCARCITY_RATIO = 2.0


class RezoneVerdict(str, Enum):
    KEEP_MINING = "keep_mining"
    REZONE_TO_PRODUCTION = "rezone_to_production"
    RETIRE_MINING_OUTWARD = "retire_mining_outward"


class MigrationPhase(str, Enum):
    PROPOSED = "proposed"
    SHADOW_RESERVED = "shadow_reserved"
    SHADOW_BUILT = "shadow_built"
    SHADOW_STABLE = "shadow_stable"
    TRAFFIC_DIVERTED = "traffic_diverted"
    OLD_DRAINED = "old_drained"
    OLD_DECOMMISSIONED = "old_decommissioned"
    CELL_RELEASED = "cell_released"


@dataclass(frozen=True)
class CompletedMigration:
    migration_id: str
    grid: BlockGrid
    source_block_id: str
    target_block_id: str
    source_cell: Cell
    source_box: Rect
    shadow_stable_ticks: int
    rail_connected: bool
    traffic_diverted: bool
    old_drained: bool
    old_decommissioned: bool
    phase: MigrationPhase = MigrationPhase.CELL_RELEASED

    def __post_init__(self) -> None:
        if (
            not self.migration_id
            or not self.source_block_id
            or not self.target_block_id
        ):
            raise ValueError("migration and block IDs are required")
        if self.source_block_id == self.target_block_id:
            raise ValueError(
                "shadow migration requires distinct source and target blocks"
            )
        if self.phase is not MigrationPhase.CELL_RELEASED:
            raise ValueError("completed migration must be at CELL_RELEASED")
        if isinstance(self.shadow_stable_ticks, bool) or not isinstance(
            self.shadow_stable_ticks, int
        ):
            raise ValueError("shadow_stable_ticks must be an integer")
        if self.shadow_stable_ticks <= 0:
            raise ValueError("shadow_stable_ticks must be positive")
        if not all(
            (
                self.rail_connected,
                self.traffic_diverted,
                self.old_drained,
                self.old_decommissioned,
            )
        ):
            raise ValueError("migration evidence is incomplete")
        if self.source_box != self.grid.block_box(self.source_cell):
            raise ValueError("source_box does not match the migration grid and cell")


@dataclass(frozen=True)
class CellReleasePermit:
    migration_id: str
    surface: str
    grid_anchor: Point
    grid_pitch: float
    corridor_width: float
    source_block_id: str
    cell: Cell
    box: Rect
    released_tick: int

    def matches(self, grid: BlockGrid) -> bool:
        return (
            self.surface == grid.surface
            and self.grid_anchor == grid.anchor
            and self.grid_pitch == grid.pitch
            and self.corridor_width == grid.corridor_width
            and self.box == grid.block_box(self.cell)
        )


@dataclass(frozen=True)
class BlockAllocation:
    """A cell awarded to a stage, with every score component exposed."""

    cell: Cell
    box: Rect
    land: LandClass
    category: str
    score: float
    link_tiles: float
    proximity_tiles: float
    zoning_penalty: float
    land_value_penalty: float
    reason: str


@dataclass(frozen=True)
class MinePlacement:
    """Separate mine and smelter allocations; no transport is executed here."""

    mine: BlockAllocation
    smelter: BlockAllocation
    transport_tiles: float


@dataclass(frozen=True)
class RezoneDecision:
    """A migration proposal; only a later release permit authorizes reuse."""

    cell: Cell
    verdict: RezoneVerdict
    trigger: str
    remaining_fraction: float
    land_headroom: float
    reason: str

    @property
    def requires_shadow_migration(self) -> bool:
        return self.verdict is not RezoneVerdict.KEEP_MINING


def issue_cell_release_permit(
    decision: RezoneDecision,
    migration: CompletedMigration,
    *,
    released_tick: int,
) -> CellReleasePermit:
    """Bind one physical ore cell to a fully evidenced shadow migration."""
    if decision.verdict is RezoneVerdict.KEEP_MINING:
        raise ValueError("a keep-mining decision cannot release land")
    if migration.source_cell != decision.cell:
        raise ValueError("migration source cell does not match the decision")
    if isinstance(released_tick, bool) or not isinstance(released_tick, int):
        raise ValueError("released_tick must be an integer")
    if released_tick < 0:
        raise ValueError("released_tick cannot be negative")
    return CellReleasePermit(
        migration_id=migration.migration_id,
        surface=migration.grid.surface,
        grid_anchor=migration.grid.anchor,
        grid_pitch=migration.grid.pitch,
        corridor_width=migration.grid.corridor_width,
        source_block_id=migration.source_block_id,
        cell=migration.source_cell,
        box=migration.source_box,
        released_tick=released_tick,
    )


def link_cost(box: Rect, connects_to: Sequence[Point]) -> float:
    """Summed Manhattan transport distance from a block to its interfaces."""
    centre_x, centre_y = box.centre
    return sum(abs(centre_x - x) + abs(centre_y - y) for x, y in connects_to)


def _zoning_penalty(
    kind: StageKind,
    cell: Cell,
    land: LandClass,
    box: Rect,
    patches: Sequence[OrePatch],
    released_cells: Collection[Cell],
) -> float | None:
    if kind is StageKind.MINING:
        if land is not LandClass.ORE:
            return None
        return ORE_COVERAGE_WEIGHT_TILES * (1.0 - ore_coverage(box, patches))
    if land is LandClass.FREE:
        return 0.0
    if land is LandClass.ORE_BUFFER:
        return ORE_APRON_PENALTY_TILES
    return ORE_SQUAT_PENALTY_TILES if cell in released_cells else None


def allocate_block(
    grid: BlockGrid,
    kind: StageKind,
    patches: Sequence[OrePatch],
    *,
    occupied: Collection[Cell] = (),
    connects_to: Sequence[Point] = (),
    near: Point | None = None,
    rings: int = DEFAULT_SEARCH_RINGS,
    apron: float = MINING_APRON_TILES,
    release_permits: Collection[CellReleasePermit] = (),
    land_value_policy: LandValuePolicy | None = None,
    machine_count: int = 0,
) -> BlockAllocation | None:
    """Award one immutable lattice cell to a stage.

    ``near`` contributes an explicit proximity cost instead of merely choosing
    a search window. Ore permission is cell-scoped and requires a release permit
    from a completed shadow migration; a proposal alone authorizes nothing. Intrinsic land
    value is optional because its base centre must be supplied from measured
    city state, never guessed by this planner.

    This function emits no layout or transport. A caller may realize separate
    blocks only after rail station and corridor templates are available; belts
    and bots may not cross these block boundaries.
    """
    if machine_count < 0:
        raise ValueError("machine_count cannot be negative")
    if near is not None:
        focus = grid.cell_at(near)
    elif connects_to:
        focus = grid.cell_at(connects_to[0])
    else:
        focus = grid.cell_at(grid.anchor)
    taken = set(occupied)
    released_cells = {permit.cell for permit in release_permits if permit.matches(grid)}
    best: tuple[tuple[float, int, int], BlockAllocation] | None = None
    for cell in grid.cells_within(focus, rings):
        if cell in taken:
            continue
        box = grid.block_box(cell)
        land = classify_land(box, patches, apron=apron)
        zoning = _zoning_penalty(kind, cell, land, box, patches, released_cells)
        if zoning is None:
            continue
        links = link_cost(box, connects_to)
        proximity = link_cost(box, [near]) if near is not None else 0.0
        land_value = (
            land_value_policy.placement_penalty(kind, box.centre, machine_count)
            if land_value_policy is not None
            else 0.0
        )
        score = zoning + links + proximity + land_value
        key = (score, cell[0], cell[1])
        if best is not None and key >= best[0]:
            continue
        best = (
            key,
            BlockAllocation(
                cell=cell,
                box=box,
                land=land,
                category=BLOCK_CATEGORIES[kind],
                score=score,
                link_tiles=links,
                proximity_tiles=proximity,
                zoning_penalty=zoning,
                land_value_penalty=land_value,
                reason=(
                    f"cell {cell} on {land.value}: {links:.0f} link + "
                    f"{proximity:.0f} proximity + {zoning:.0f} zoning + "
                    f"{land_value:.0f} land value = {score:.0f}"
                ),
            ),
        )
    return None if best is None else best[1]


def plan_extraction(
    grid: BlockGrid,
    patches: Sequence[OrePatch],
    *,
    occupied: Collection[Cell] = (),
    near: Point | None = None,
    smelter_connects_to: Sequence[Point] = (),
    rings: int = DEFAULT_SEARCH_RINGS,
    apron: float = MINING_APRON_TILES,
    release_permits: Collection[CellReleasePermit] = (),
    land_value_policy: LandValuePolicy | None = None,
    drill_count: int = 0,
    furnace_count: int = 0,
) -> MinePlacement | None:
    """Allocate mining and smelting independently, sized by their own rates."""
    mine = allocate_block(
        grid,
        StageKind.MINING,
        patches,
        occupied=occupied,
        near=near,
        rings=rings,
        apron=apron,
        land_value_policy=land_value_policy,
        machine_count=drill_count,
    )
    if mine is None:
        return None
    smelter = allocate_block(
        grid,
        StageKind.SMELTING,
        patches,
        occupied=[*occupied, mine.cell],
        connects_to=[mine.box.centre, *smelter_connects_to],
        near=near,
        rings=rings,
        apron=apron,
        release_permits=release_permits,
        land_value_policy=land_value_policy,
        machine_count=furnace_count,
    )
    if smelter is None:
        return None
    return MinePlacement(
        mine=mine,
        smelter=smelter,
        transport_tiles=link_cost(smelter.box, [mine.box.centre]),
    )


def block_contract(
    allocation: BlockAllocation,
    block_id: str,
    *,
    inputs: Mapping[str, float] | None = None,
    outputs: Mapping[str, float] | None = None,
) -> dict:
    """Represent an allocation as the canonical block throughput contract."""
    return {
        "block_id": block_id,
        "category": allocation.category,
        "size": {
            "w": int(allocation.box.max_x - allocation.box.min_x),
            "h": int(allocation.box.max_y - allocation.box.min_y),
        },
        "inputs": [
            {"item": item, "rate": float(rate)}
            for item, rate in sorted((inputs or {}).items())
        ],
        "outputs": [
            {"item": item, "rate": float(rate)}
            for item, rate in sorted((outputs or {}).items())
        ],
    }


def evaluate_rezoning(
    cell: Cell,
    patch: OrePatch,
    *,
    free_cells: int,
    requested_cells: int = 1,
    depletion_threshold: float = DEPLETION_REZONE_RATIO,
    scarcity_threshold: float = LAND_SCARCITY_RATIO,
    development: DevelopmentMetrics | None = None,
) -> RezoneDecision:
    """Decide whether one ore cell may be retired and reused.

    Physical relocation, whether depletion- or pressure-driven, stays forbidden until
    every explicitly required science sustains at least 100 items/second for
    36,000 ticks. The decision grants no build permission; migration must build,
    validate, divert, drain, decommission, and release the exact cell first.
    """
    if requested_cells <= 0:
        raise ValueError("requested_cells must be positive")
    if free_cells < 0:
        raise ValueError("free_cells cannot be negative")
    remaining = patch.remaining_fraction
    headroom = free_cells / requested_cells
    relocation_requested = (
        remaining <= depletion_threshold or headroom < scarcity_threshold
    )
    if relocation_requested and (
        development is None or not development.relocation_ready
    ):
        signal = "depletion" if remaining <= depletion_threshold else "land pressure"
        return RezoneDecision(
            cell=cell,
            verdict=RezoneVerdict.KEEP_MINING,
            trigger="late_game_gate",
            remaining_fraction=remaining,
            land_headroom=headroom,
            reason=(
                f"cell {cell}: {signal} requests relocation, but the all-science "
                "100/s gate is not yet satisfied"
            ),
        )
    if remaining <= depletion_threshold:
        return RezoneDecision(
            cell=cell,
            verdict=RezoneVerdict.RETIRE_MINING_OUTWARD,
            trigger="depletion",
            remaining_fraction=remaining,
            land_headroom=headroom,
            reason=(
                f"cell {cell}: {patch.resource} is at {remaining:.0%} of its starting "
                "amount; build and validate outward extraction before retiring this mine"
            ),
        )
    if headroom < scarcity_threshold:
        return RezoneDecision(
            cell=cell,
            verdict=RezoneVerdict.REZONE_TO_PRODUCTION,
            trigger="land_pressure",
            remaining_fraction=remaining,
            land_headroom=headroom,
            reason=(
                f"cell {cell}: only {free_cells} free cell(s) remain and the late-game "
                "science gate is satisfied; shadow-migrate mining outward before reuse"
            ),
        )
    return RezoneDecision(
        cell=cell,
        verdict=RezoneVerdict.KEEP_MINING,
        trigger="none",
        remaining_fraction=remaining,
        land_headroom=headroom,
        reason=(
            f"cell {cell}: {patch.resource} still holds {remaining:.0%} of its ore "
            f"and {headroom:.1f}x the requested cells are free; keep it for mining"
        ),
    )
