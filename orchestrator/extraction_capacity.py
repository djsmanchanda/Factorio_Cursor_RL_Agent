# Path: orchestrator/extraction_capacity.py
# Purpose: Deterministic system-wide mining phases and expandable-corridor batch sizing.

from __future__ import annotations

from orchestrator.extraction_state import ResourceMine

# Early plate demand is iron-heavy; complete paired checkpoints make the
# first expansions useful while preserving complete six-drill doubling sets.
EXTRACTION_DRILL_PHASES = (6, 12, 24, 48, 96)
PARALLEL_BAND_PITCH = 8.0


def affordable_prebuilt_columns(belt_stock: int, maximum: int) -> int:
    """How many reserved mine columns can be paved before they are needed."""
    if belt_stock < 0 or maximum < 0:
        raise ValueError("belt_stock and maximum cannot be negative")
    if belt_stock < 200:
        return 0
    if belt_stock < 500:
        return min(2, maximum)
    if belt_stock < 1000:
        return min(5, maximum)
    return maximum


def next_drill_phase(
    current_drills: int, *, unbounded: bool = False,
) -> int | None:
    """Return the next whole-system drill target.

    Bootstrap keeps the finite opening ladder. Once a caller has an
    independently supplied post-plastic factory, ``unbounded`` continues the
    same doubling policy instead of turning 96 drills into an arbitrary cap.
    """
    if current_drills < 0:
        raise ValueError("current_drills cannot be negative")
    fixed = next(
        (phase for phase in EXTRACTION_DRILL_PHASES if phase > current_drills),
        None,
    )
    if fixed is not None or not unbounded:
        return fixed
    phase = EXTRACTION_DRILL_PHASES[-1]
    while phase <= current_drills:
        phase *= 2
    return phase


def expandable_mine(mines: list[ResourceMine]) -> ResourceMine | None:
    """Choose the first observed corridor with declared unused paired slots."""
    return next(
        (
            mine for mine in mines
            if mine.row_capacity > mine.drill_count
        ),
        None,
    )


def phase_batch_positions(
    mine: ResourceMine, requested_drills: int,
) -> tuple[tuple[float, float], ...]:
    """Fill as much of one corridor as the current system phase requires."""
    if requested_drills <= 0 or requested_drills % 2:
        raise ValueError("A mining phase must request a positive even drill count")
    free_pairs = max(0, mine.row_capacity - mine.drill_count)
    pair_count = min(requested_drills // 2, free_pairs)
    positions = []
    for offset in range(pair_count):
        if mine.first_column_x is not None:
            # The refinery haul is fixed at the east head.  New columns must
            # grow away from it, while their belts still flow east.
            x = mine.first_column_x + mine.growth_direction * (3 * (offset + 1))
        else:
            index = mine.drill_count + offset
            x = mine.output[0] + mine.expansion_step * (4 + 3 * index)
        positions.extend(((x, mine.shared_belt_y - 2), (x, mine.shared_belt_y + 2)))
    return tuple(positions)


def parallel_phase_batch_positions(
    mine: ResourceMine, requested_drills: int, *,
    band_order: int = 1, band_pitch: float = PARALLEL_BAND_PITCH,
) -> tuple[tuple[float, float], ...]:
    """Return a paired row on a parallel collector band.

    Longitudinal growth remains the first choice. This helper is the explicit
    fallback for a valid second band: it preserves the same column x's while
    moving both drill rows by a fixed pitch, leaving the merge/splitter
    geometry to ``generate_parallel_mining_row_expansion``.
    """
    if band_order == 0:
        raise ValueError("parallel band_order must be non-zero")
    if band_pitch <= 0:
        raise ValueError("band_pitch must be positive")
    if requested_drills <= 0 or requested_drills % 2:
        raise ValueError("A parallel mining phase must request a positive even drill count")
    columns = requested_drills // 2
    if mine.first_column_x is not None:
        xs = [mine.first_column_x + 3 * index for index in range(columns)]
    else:
        xs = [
            mine.output[0] + mine.expansion_step * (4 + 3 * index)
            for index in range(columns)
        ]
    base = tuple(
        position
        for x in xs
        for position in ((x, mine.shared_belt_y - 2), (x, mine.shared_belt_y + 2))
    )
    offset = band_order * band_pitch
    return tuple((x, y + offset) for x, y in base)
