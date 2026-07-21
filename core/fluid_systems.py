# Path: core/fluid_systems.py
# Purpose: Deterministic data tables + validation helpers for Factorio fluid
# systems (pipes, pumps, tanks, chemical-plant/oil-refinery fluid boxes).
# Data verified against live Factorio 2.0.77 prototypes (RCON) and
# wiki.factorio.com/Fluid_system. Mirrors the house style of
# core/quality_modules.py: data tables + small pure validators, stdlib-only.
# core/ MUST NOT import from planners/ (docs/20 layering invariant).

from __future__ import annotations

# --- Entity footprints, fluid boxes, connection counts ----------------------
# tile_width/tile_height: footprint. boxes: fluid box count. connections:
# pipe-connection count. Extra keys document mechanic-specific behavior.
FLUID_ENTITIES: dict[str, dict] = {
    "pipe": {
        "tile_width": 1, "tile_height": 1, "boxes": 1, "connections": 4,
    },
    "pipe-to-ground": {
        "tile_width": 1, "tile_height": 1, "boxes": 1, "connections": 2,
        "max_underground_distance": 10,  # one "normal" end, one "underground" end
    },
    "pump": {
        "tile_width": 1, "tile_height": 2, "boxes": 1, "connections": 2,
        "directional": True,  # input one end, output the other; blocks backflow
    },
    "storage-tank": {
        "tile_width": 3, "tile_height": 3, "boxes": 1, "connections": 4,
        "capacity": 25000,
    },
    "offshore-pump": {
        "tile_width": 1, "tile_height": 1, "boxes": 1, "connections": 1,
        "fluid_source": True,  # must be placed on water
    },
    "chemical-plant": {
        "tile_width": 3, "tile_height": 3, "boxes": 4, "connections": 4,
    },
    "oil-refinery": {
        "tile_width": 5, "tile_height": 5, "boxes": 5, "connections": 5,
    },
}

# Mechanism retained for future fluid entities whose connection offsets have
# not been verified against live prototypes yet (mirrors
# quality_modules.UNVERIFIED_SLOT_MACHINES). connection_points() refuses to
# place any entity listed here rather than guess.
UNVERIFIED_FLUID_ENTITIES: frozenset[str] = frozenset()

# --- Connection offsets, relative to entity center, unrotated north frame --
# chemical-plant verified live (RCON, 2026-07-22): 2 inputs on the north
# side, 2 outputs on the south side.
# oil-refinery verified live (RCON, 2026-07-22): 2 inputs on the south side,
# 3 outputs on the north side.
# pump verified live (RCON, 2026-07-22): 1x2 footprint, half-tile offsets
# along its long axis -- output at the north end, input at the south end.
# Offsets are kept as floats (not rounded) where the live data is half-tile.
CONNECTION_OFFSETS: dict[str, list[dict] | None] = {
    "chemical-plant": [
        {"role": "input", "offset": (-1, -1)},
        {"role": "input", "offset": (1, -1)},
        {"role": "output", "offset": (-1, 1)},
        {"role": "output", "offset": (1, 1)},
    ],
    "oil-refinery": [
        {"role": "input", "offset": (-1, 2)},
        {"role": "input", "offset": (1, 2)},
        {"role": "output", "offset": (-2, -2)},
        {"role": "output", "offset": (0, -2)},
        {"role": "output", "offset": (2, -2)},
    ],
    "pump": [
        {"role": "output", "offset": (0.0, -0.5)},
        {"role": "input", "offset": (0.0, 0.5)},
    ],
}

# --- Throughput, span, and capacity constants -------------------------------
# fluid/s. Theoretical = 100/tick * 60 ticks/s; practical accounts for
# real-world segment overhead. Both are per single pipe connection.
PIPE_THROUGHPUT: dict[str, float] = {
    "theoretical": 6000.0,
    "practical": 4200.0,
}
PIPE_CAPACITY = 100  # fluid held per pipe segment
TANK_CAPACITY = 25000  # fluid held per storage-tank
MAX_UNDERGROUND_SPAN = 10  # max_underground_distance, in tiles
MAX_PIPELINE_SPAN = 320  # tiles (10x10 chunks); beyond this without a pump, flow stops entirely

_DIRECTIONS = ("north", "east", "south", "west")
_AXES = ("horizontal", "vertical")


def rotate_offset(offset: tuple, direction: str) -> tuple:
    """Rotate a north-frame (x, y) offset to face `direction`. north is
    identity; east/south/west are successive 90-degree rotations. Verified:
    a connection at (0, -1) (north of center) rotates east to (1, 0)."""
    if direction not in _DIRECTIONS:
        raise ValueError(f"Unknown direction: {direction!r} (expected one of {_DIRECTIONS})")
    x, y = offset
    if direction == "north":
        return (x, y)
    if direction == "east":
        return (-y, x)
    if direction == "south":
        return (-x, -y)
    return (y, -x)  # west


def flip_offset(offset: tuple, axis: str) -> tuple:
    """Mirror a north-frame (x, y) offset across `axis` ("horizontal" negates
    x, "vertical" negates y)."""
    if axis not in _AXES:
        raise ValueError(f"Unknown flip axis: {axis!r} (expected one of {_AXES})")
    x, y = offset
    return (-x, y) if axis == "horizontal" else (x, -y)


def connection_points(
    entity: str, center: tuple, direction: str = "north", flip: str | None = None
) -> list[dict]:
    """Return [{"role": "input"|"output", "position": (x, y)}] in world
    coordinates for `entity` placed at `center`, applying `flip` (if any)
    then `direction` rotation to each verified offset. Raises ValueError for
    entities with no offset data, or entities explicitly flagged
    UNVERIFIED_FLUID_ENTITIES (refuse to place rather than guess)."""
    if entity in UNVERIFIED_FLUID_ENTITIES:
        raise ValueError(
            f"Connection offsets for '{entity}' are unverified (NEEDS_VERIFICATION); "
            "refusing to place until confirmed"
        )
    offsets = CONNECTION_OFFSETS.get(entity)
    if offsets is None:
        raise ValueError(f"No verified connection offset data for entity '{entity}'")

    cx, cy = center
    points = []
    for item in offsets:
        off = item["offset"]
        if flip is not None:
            off = flip_offset(off, flip)
        off = rotate_offset(off, direction)
        points.append({"role": item["role"], "position": (cx + off[0], cy + off[1])})
    return points


def validate_underground_span(a: tuple, b: tuple) -> None:
    """Raise ValueError unless underground-pipe ends `a` and `b` share a row
    or column AND their separation is <= MAX_UNDERGROUND_SPAN tiles."""
    ax, ay = a
    bx, by = b
    if ax != bx and ay != by:
        raise ValueError(
            f"Underground pipe ends {a} and {b} are not on the same row or column"
        )
    distance = abs(ax - bx) if ay == by else abs(ay - by)
    if distance > MAX_UNDERGROUND_SPAN:
        raise ValueError(
            f"Underground pipe span {distance} exceeds max {MAX_UNDERGROUND_SPAN} tiles "
            f"({a} -> {b})"
        )


def _orthogonally_adjacent(a: tuple, b: tuple) -> bool:
    ax, ay = a
    bx, by = b
    return (ax == bx and abs(ay - by) == 1) or (ay == by and abs(ax - bx) == 1)


def validate_network_purity(segments: list[dict]) -> None:
    """Raise ValueError naming the offending tiles when two tiles of
    DIFFERENT, non-None fluid from different segments are orthogonally
    adjacent and neither segment is separated_by_pump. Each segment is
    {"fluid": str|None, "tiles": [(x, y)...], "separated_by_pump": bool}.
    This is the anti-mixing guard: a fluid network can hold exactly one
    fluid type, the fluid analogue of the quality jam rule."""
    for i in range(len(segments)):
        seg_a = segments[i]
        fluid_a = seg_a.get("fluid")
        for j in range(i + 1, len(segments)):
            seg_b = segments[j]
            fluid_b = seg_b.get("fluid")
            if fluid_a is None or fluid_b is None or fluid_a == fluid_b:
                continue
            if seg_a.get("separated_by_pump") or seg_b.get("separated_by_pump"):
                continue
            for tile_a in seg_a["tiles"]:
                for tile_b in seg_b["tiles"]:
                    if _orthogonally_adjacent(tile_a, tile_b):
                        raise ValueError(
                            f"Fluid mixing: tile {tile_a} ('{fluid_a}') is adjacent to "
                            f"tile {tile_b} ('{fluid_b}') without a separating pump"
                        )


def validate_pipeline_span(tiles: list[tuple], pumps: list[tuple] | None = None) -> None:
    """Raise ValueError if the bounding box of `tiles` exceeds
    MAX_PIPELINE_SPAN on either axis. A non-empty `pumps` list means the run
    is broken into pump-separated segments, so the span check does not
    apply."""
    if pumps:
        return
    xs = [t[0] for t in tiles]
    ys = [t[1] for t in tiles]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    if width > MAX_PIPELINE_SPAN or height > MAX_PIPELINE_SPAN:
        raise ValueError(
            f"Pipeline bounding box {width}x{height} exceeds MAX_PIPELINE_SPAN "
            f"({MAX_PIPELINE_SPAN}) without a pump"
        )


_SHORT_RUN_TILES = 20  # empirical "full rate" cutoff, pending live measurement


def estimate_throughput(pipe_length: int, practical: bool = True) -> float:
    """Approximate model, NOT the real Factorio fluid-flow formula: full
    rated throughput for pipe_length <= _SHORT_RUN_TILES, linearly degrading
    to 0 at MAX_PIPELINE_SPAN tiles (where the real game stops flow entirely
    without a pump). This is a planner-usable approximation pending live
    throughput measurement against long pipelines in-game."""
    if pipe_length < 0:
        raise ValueError("pipe_length must be non-negative")
    base = PIPE_THROUGHPUT["practical"] if practical else PIPE_THROUGHPUT["theoretical"]
    if pipe_length >= MAX_PIPELINE_SPAN:
        return 0.0
    if pipe_length <= _SHORT_RUN_TILES:
        return base
    remaining_fraction = 1.0 - (pipe_length - _SHORT_RUN_TILES) / (MAX_PIPELINE_SPAN - _SHORT_RUN_TILES)
    return base * remaining_fraction
