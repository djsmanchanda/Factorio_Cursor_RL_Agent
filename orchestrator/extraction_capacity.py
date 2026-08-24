# Path: orchestrator/extraction_capacity.py
# Purpose: Deterministic system-wide mining phases and expandable-corridor batch sizing.

from __future__ import annotations

from orchestrator.extraction_state import ResourceMine

EXTRACTION_DRILL_PHASES = (6, 20, 50, 100)


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


def next_drill_phase(current_drills: int) -> int | None:
    """Return the next whole-system drill target, or None at final capacity."""
    if current_drills < 0:
        raise ValueError("current_drills cannot be negative")
    return next((phase for phase in EXTRACTION_DRILL_PHASES if phase > current_drills), None)


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
