# Path: planners/zoning_geometry.py
# Purpose: Immutable geometry and ore-land classification for deterministic block zoning.

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import Enum

Point = tuple[float, float]
Cell = tuple[int, int]

# A documented 64-tile micro/support slot with a four-lane corridor reserved
# inside its pitch. These cells stay immutable; macro districts coordinate
# multiple cells without deleting the corridors between them.
BLOCK_SIZE = 64.0
CORRIDOR_WIDTH = 16.0
BLOCK_PITCH = BLOCK_SIZE + CORRIDOR_WIDTH
MINING_APRON_TILES = 5.0


class LandClass(str, Enum):
    """The permitted primary use of a piece of ground."""

    ORE = "ore"
    ORE_BUFFER = "ore_buffer"
    FREE = "free"


@dataclass(frozen=True)
class Rect:
    """Axis-aligned world box, with every intersected integer tile included."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self) -> None:
        if self.max_x <= self.min_x or self.max_y <= self.min_y:
            raise ValueError(f"Rect must have positive extent, got {self}")

    @property
    def centre(self) -> Point:
        return ((self.min_x + self.max_x) / 2.0, (self.min_y + self.max_y) / 2.0)

    @property
    def tile_area(self) -> int:
        return len(range(math.floor(self.min_x), math.ceil(self.max_x))) * len(
            range(math.floor(self.min_y), math.ceil(self.max_y))
        )

    def tiles(self) -> Iterator[tuple[int, int]]:
        for x in range(math.floor(self.min_x), math.ceil(self.max_x)):
            for y in range(math.floor(self.min_y), math.ceil(self.max_y)):
                yield (x, y)

    def contains_tile(self, x: int, y: int) -> bool:
        """Whether the box intersects the integer tile identified by ``x, y``."""
        return math.floor(self.min_x) <= x < math.ceil(self.max_x) and math.floor(
            self.min_y
        ) <= y < math.ceil(self.max_y)

    def expanded(self, margin: float) -> "Rect":
        return Rect(
            self.min_x - margin,
            self.min_y - margin,
            self.max_x + margin,
            self.max_y + margin,
        )

    def overlaps(self, other: "Rect") -> bool:
        return (
            self.min_x < other.max_x
            and other.min_x < self.max_x
            and self.min_y < other.max_y
            and other.min_y < self.max_y
        )


@dataclass(frozen=True)
class OrePatch:
    """A surveyed patch with a persistent starting amount and current amount."""

    resource: str
    bounds: Rect
    initial_amount: float
    amount: float
    ore_tiles: frozenset[tuple[int, int]] | None = None

    def __post_init__(self) -> None:
        if self.initial_amount <= 0:
            raise ValueError(f"{self.resource}: initial_amount must be positive")
        if self.amount < 0:
            raise ValueError(f"{self.resource}: amount cannot be negative")
        if self.ore_tiles is not None:
            outside = [
                tile for tile in self.ore_tiles if not self.bounds.contains_tile(*tile)
            ]
            if outside:
                raise ValueError(
                    f"{self.resource}: ore_tiles outside bounds: {outside[:3]}"
                )

    @property
    def remaining_fraction(self) -> float:
        return min(1.0, self.amount / self.initial_amount)

    def has_ore_tile(self, x: int, y: int) -> bool:
        if self.ore_tiles is not None:
            return (x, y) in self.ore_tiles
        return self.bounds.contains_tile(x, y)


@dataclass(frozen=True)
class BlockGrid:
    """A fixed lattice whose cells reserve a buildable block and rail corridor."""

    anchor: Point
    surface: str = "nauvis"
    pitch: float = BLOCK_PITCH
    corridor_width: float = CORRIDOR_WIDTH

    def __post_init__(self) -> None:
        if not self.surface:
            raise ValueError("surface is required")
        if self.pitch <= 0 or not 0 < self.corridor_width < self.pitch:
            raise ValueError(
                "corridor must be narrower than the pitch and both positive"
            )

    @classmethod
    def anchored_at(
        cls,
        origin: Point,
        *,
        surface: str = "nauvis",
        pitch: float = BLOCK_PITCH,
        corridor_width: float = CORRIDOR_WIDTH,
    ) -> "BlockGrid":
        return cls(
            anchor=(
                math.floor(origin[0] / pitch) * pitch,
                math.floor(origin[1] / pitch) * pitch,
            ),
            surface=surface,
            pitch=pitch,
            corridor_width=corridor_width,
        )

    @property
    def block_size(self) -> float:
        return self.pitch - self.corridor_width

    def _min_corner(self, cell: Cell) -> Point:
        return (
            self.anchor[0] + cell[0] * self.pitch,
            self.anchor[1] + cell[1] * self.pitch,
        )

    def block_box(self, cell: Cell) -> Rect:
        min_x, min_y = self._min_corner(cell)
        return Rect(min_x, min_y, min_x + self.block_size, min_y + self.block_size)

    def cell_box(self, cell: Cell) -> Rect:
        min_x, min_y = self._min_corner(cell)
        return Rect(min_x, min_y, min_x + self.pitch, min_y + self.pitch)

    def cell_at(self, point: Point) -> Cell:
        return (
            math.floor((point[0] - self.anchor[0]) / self.pitch),
            math.floor((point[1] - self.anchor[1]) / self.pitch),
        )

    def cells_within(self, focus: Cell, rings: int) -> list[Cell]:
        if rings < 0:
            raise ValueError("rings cannot be negative")
        return [
            (focus[0] + dx, focus[1] + dy)
            for dx in range(-rings, rings + 1)
            for dy in range(-rings, rings + 1)
        ]


def ore_coverage(box: Rect, patches: Sequence[OrePatch]) -> float:
    """Fraction of the box's intersected tiles that contain surveyed ore."""
    hits: set[tuple[int, int]] = set()
    for patch in patches:
        if not patch.bounds.overlaps(box):
            continue
        if patch.ore_tiles is None:
            hits.update(tile for tile in box.tiles() if patch.has_ore_tile(*tile))
        else:
            hits.update(tile for tile in patch.ore_tiles if box.contains_tile(*tile))
    return len(hits) / box.tile_area


def _touches_ore(box: Rect, patches: Sequence[OrePatch]) -> bool:
    for patch in patches:
        if not patch.bounds.overlaps(box):
            continue
        if patch.ore_tiles is None or any(
            box.contains_tile(*tile) for tile in patch.ore_tiles
        ):
            return True
    return False


def classify_land(
    box: Rect,
    patches: Sequence[OrePatch],
    *,
    apron: float = MINING_APRON_TILES,
) -> LandClass:
    if _touches_ore(box, patches):
        return LandClass.ORE
    if any(patch.bounds.expanded(apron).overlaps(box) for patch in patches):
        return LandClass.ORE_BUFFER
    return LandClass.FREE


def classify_cell(
    grid: BlockGrid,
    cell: Cell,
    patches: Sequence[OrePatch],
    *,
    apron: float = MINING_APRON_TILES,
) -> LandClass:
    return classify_land(grid.block_box(cell), patches, apron=apron)
