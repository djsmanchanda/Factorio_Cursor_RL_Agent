# Path: planners/smelter_block.py
# Purpose: Decide the shape of a smelting block -- how many furnaces stand in a row and how many rows deep it goes -- so a growing mine widens a compact block instead of extending one enormous line.

from __future__ import annotations

import math
from dataclasses import dataclass

# User standard, 2026-08-03. A row holds this many furnaces in the opening
# phase and grows to the late-game figure; the block holds this many rows.
#
# The old rule put every furnace in ONE row: 104 furnaces at the top drill
# phase is a 394-tile line, which is why the connecting belt had to travel so
# far and turn so often. A block 15 wide and 4 deep covers the same output in
# roughly a quarter of the span, and the feed belt meets it at one end.
ROW_FURNACES_START = 15
ROW_FURNACES_LATE = 50
BLOCK_ROWS_START = 4
BLOCK_ROWS_LATE = 30

# Tiles per furnace along a row, and per row across the block. An electric
# furnace is 3x3; a row adds its feed belt, inserters, and output belt.
FURNACE_PITCH = 3
ROW_PITCH = 8


@dataclass(frozen=True)
class BlockShape:
    """A smelting block as rows of furnaces, rather than one long line."""

    furnaces: int
    per_row: int
    rows: int

    @property
    def width(self) -> int:
        return self.per_row * FURNACE_PITCH

    @property
    def depth(self) -> int:
        return self.rows * ROW_PITCH

    @property
    def capacity(self) -> int:
        return self.per_row * self.rows


def row_capacity(late_game: bool = False) -> int:
    """Furnaces per row for the phase the base is in."""
    return ROW_FURNACES_LATE if late_game else ROW_FURNACES_START


def block_capacity(late_game: bool = False) -> int:
    """Rows the block may grow to before it must be split."""
    return BLOCK_ROWS_LATE if late_game else BLOCK_ROWS_START


def block_shape(furnaces: int, *, late_game: bool = False) -> BlockShape:
    """Fold `furnaces` into rows, widening before deepening.

    A row is filled to its cap before a second is started, so a small mine gets
    one short row rather than a wide sparse block, and the feed belt runs the
    length of a row it can actually saturate.

    Rows are the growth axis after that. Extending a single row instead is what
    produced a 394-tile furnace line, and a belt long enough to serve one has to
    cross whatever the base has already built.
    """
    if furnaces < 1:
        raise ValueError(f"A smelting block needs at least one furnace, got {furnaces}")
    per_row = min(furnaces, row_capacity(late_game))
    rows = math.ceil(furnaces / per_row)
    return BlockShape(furnaces=furnaces, per_row=per_row, rows=rows)


def block_is_full(furnaces: int, *, late_game: bool = False) -> bool:
    """Whether this many furnaces no longer fit the phase's block.

    A block past its row count is a signal to open a SECOND block at another
    patch, not to keep deepening this one -- at that size the ore feeding it is
    more than one mine can supply anyway.
    """
    return block_shape(furnaces, late_game=late_game).rows > block_capacity(late_game)


def feed_belt_length(shape: BlockShape) -> int:
    """Tiles of belt the block's own feed spine costs.

    One spine runs the length of a row and serves every furnace on it, so the
    cost scales with WIDTH, plus a cross-run that reaches the other rows. This
    is what a block buys over a line: the same furnace count behind a much
    shorter run of belt.

    A single row needs no cross-run -- it IS the line -- so the depth term only
    applies once there is a second row to reach.
    """
    if shape.rows <= 1:
        return shape.width
    return shape.width + shape.depth
