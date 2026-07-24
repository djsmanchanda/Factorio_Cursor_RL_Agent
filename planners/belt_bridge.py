# Path: planners/belt_bridge.py
# Purpose: Deterministic belt+inserter connections between two existing chests (real-base stage-to-stage links).

from __future__ import annotations

from typing import Sequence

from planners.infrastructure_geometry import choose_clear_l_route

Point = tuple[float, float]

_FACING_TO_VECTOR = {
    "north": (0.0, -1.0), "south": (0.0, 1.0), "east": (1.0, 0.0), "west": (-1.0, 0.0),
}
_VECTOR_TO_FACING = {vector: name for name, vector in _FACING_TO_VECTOR.items()}
_OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}


def _scaled(vector: tuple[float, float], scale: float) -> Point:
    return (vector[0] * scale, vector[1] * scale)


def _add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def _leg_direction(start: Point, end: Point) -> str:
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == 0 and dy == 0:
        raise ValueError("belt leg needs distinct start and end points")
    if dx != 0 and dy != 0:
        raise ValueError(f"belt leg must be axis-aligned: {start} -> {end}")
    axis = (1.0 if dx > 0 else -1.0, 0.0) if dx != 0 else (0.0, 1.0 if dy > 0 else -1.0)
    return _VECTOR_TO_FACING[axis]


def bridge_chest_to_chest(
    source_position: Point,
    dest_position: Point,
    *,
    exit_direction: str,
    entry_direction: str,
    belt_type: str = "fast-transport-belt",
    inserter_type: str = "fast-inserter",
    blocked_tiles: set[tuple[int, int]] | None = None,
    belt_spacing: float = 1.0,
) -> list[dict]:
    """Real entities moving one item from `source_position` chest to `dest_position` chest.

    `exit_direction` is the side of the source chest the belt departs from (an
    inserter sits one tile that way and unloads the chest onto the belt);
    `entry_direction` is the side of the dest chest the belt arrives from (an
    inserter one tile that way loads the belt onto the chest). Both are one of
    "north"/"south"/"east"/"west". Verified live this session: an inserter's
    pickup tile is on its facing side, its drop tile on the opposite side --
    so the loading inserter faces the source chest and the unloading inserter
    faces away from the dest chest, each one tile off it.

    Route between the two belt endpoints uses the SAME obstacle-aware L-route
    chooser the power spine uses (`choose_clear_l_route`), so a stage-to-stage
    connection dodges anything already built between them instead of assuming
    open ground.
    """
    if exit_direction not in _FACING_TO_VECTOR or entry_direction not in _FACING_TO_VECTOR:
        raise ValueError(f"Unknown direction: {exit_direction!r} / {entry_direction!r}")

    # An inserter's pickup tile is on its facing side, its drop on the opposite
    # side (live-verified). Both endpoints here place the belt on the named
    # side of their chest (`exit_direction`/`entry_direction` mean the same
    # thing: which side of that chest the belt sits on) -- so the source
    # inserter must face the CHEST (opposite of exit_direction, so pickup
    # lands on the chest and drop lands on the belt), and the dest inserter
    # must face the BELT side, i.e. entry_direction itself (so pickup lands on
    # the belt and drop lands on the chest). Cross-checked live against an
    # existing chest-draining inserter this session: chest north of it,
    # facing south, pickup=chest/drop=belt -- matches this formula exactly.
    exit_vector = _FACING_TO_VECTOR[exit_direction]
    entry_vector = _FACING_TO_VECTOR[entry_direction]
    source_inserter = _add(source_position, _scaled(exit_vector, 1))
    belt_start = _add(source_position, _scaled(exit_vector, 2))
    dest_inserter = _add(dest_position, _scaled(entry_vector, 1))
    belt_end = _add(dest_position, _scaled(entry_vector, 2))

    actions: list[dict] = [
        {"action_type": "place_entity", "entity": inserter_type,
         "position": {"x": source_inserter[0], "y": source_inserter[1]}, "direction": _OPPOSITE[exit_direction]},
        {"action_type": "place_entity", "entity": inserter_type,
         "position": {"x": dest_inserter[0], "y": dest_inserter[1]}, "direction": entry_direction},
    ]

    route = choose_clear_l_route(belt_start, belt_end, belt_spacing, 1.0, blocked_tiles)
    for leg_index, (leg_start, leg_end) in enumerate(zip(route, route[1:])):
        direction = _leg_direction(leg_start, leg_end)
        length = int(round(abs(leg_end[0] - leg_start[0]) + abs(leg_end[1] - leg_start[1])))
        vector = _FACING_TO_VECTOR[direction]
        # Exclusive of the leg's start (already emitted by the previous leg, or
        # is belt_start itself) so a shared corner tile is placed exactly once,
        # carrying the direction of the leg it exits on -- matching how a real
        # player-built belt corner is a single tile, not two.
        first_step = 1 if leg_index > 0 else 0
        for step in range(first_step, length + 1):
            point = _add(leg_start, _scaled(vector, step))
            actions.append({
                "action_type": "place_ghost", "entity": belt_type,
                "position": {"x": point[0], "y": point[1]}, "direction": direction,
            })

    return actions


def bridge_blocked_tiles(actions: Sequence[dict]) -> set[tuple[int, int]]:
    """Tile indices a set of existing actions occupy, for `blocked_tiles` input."""
    import math

    return {
        (math.floor(action["position"]["x"]), math.floor(action["position"]["y"]))
        for action in actions
        if "position" in action
    }
