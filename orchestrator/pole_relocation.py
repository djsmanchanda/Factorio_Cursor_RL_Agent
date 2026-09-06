# Path: orchestrator/pole_relocation.py
# Purpose: Decide whether an electric pole blocking a belt route can be nudged aside without dropping anything off the power network, and emit the move.

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from planners.infrastructure import POLE_SPECS

Point = tuple[float, float]

# How far a pole may be nudged. A pole is not free to wander: it exists to
# stand in one supply area and reach its neighbours' wires, so the useful moves
# are all a tile or two. Searching wider mostly finds positions that satisfy
# the geometry while quietly changing which network the pole belongs to.
MAX_NUDGE_TILES = 3.0

# Wire slack kept in hand. A pole placed at exactly the wire limit reconnects,
# but any later pole moved by the same rule can then break the chain, so moves
# are held inside a margin rather than at the edge.
WIRE_MARGIN = 1.0


@dataclass(frozen=True)
class PoleMove:
    """One pole stepping aside so a belt can pass through where it stood."""

    pole: str
    old: Point
    new: Point

    @property
    def tiles(self) -> float:
        return abs(self.new[0] - self.old[0]) + abs(self.new[1] - self.old[1])


def _distance(a: Point, b: Point) -> float:
    return math.dist(a, b)


def candidate_positions(old: Point, *, radius: float = MAX_NUDGE_TILES) -> list[Point]:
    """Tiles a pole might step to, nearest first.

    Nearest-first matters: the smallest nudge is the one least likely to change
    what the pole covers, and the first acceptable candidate is taken.
    """
    step = int(radius)
    offsets = [
        (dx, dy)
        for dx in range(-step, step + 1)
        for dy in range(-step, step + 1)
        if (dx, dy) != (0, 0) and abs(dx) + abs(dy) <= radius
    ]
    offsets.sort(key=lambda o: (abs(o[0]) + abs(o[1]), o))
    return [(old[0] + dx, old[1] + dy) for dx, dy in offsets]


def keeps_supply(new: Point, supplied: Sequence[Point], supply_radius: float) -> bool:
    """Whether everything the pole was powering is still inside its supply area.

    Factorio's supply area is a square around the pole, not a circle, so this
    compares each axis rather than a straight-line distance -- treating it as a
    radius rejects legal corner positions and accepts illegal edge ones.
    """
    return all(
        abs(consumer[0] - new[0]) <= supply_radius
        and abs(consumer[1] - new[1]) <= supply_radius
        for consumer in supplied
    )


def keeps_wire(new: Point, neighbours: Sequence[Point], wire_reach: float) -> bool:
    """Whether the pole can still reach every neighbour it was wired to.

    Every one, not merely the nearest: a pole that links two halves of a network
    keeps them linked only if BOTH ends stay in reach. Dropping to the closest
    neighbour is how a nudge silently cuts a base in two.
    """
    if not neighbours:
        return True
    limit = max(0.0, wire_reach - WIRE_MARGIN)
    return all(_distance(new, neighbour) <= limit for neighbour in neighbours)


def choose_pole_move(
    pole: str,
    old: Point,
    *,
    supplied: Sequence[Point],
    neighbours: Sequence[Point],
    blocked: set[tuple[int, int]],
    keep_clear: set[tuple[int, int]],
) -> PoleMove | None:
    """The smallest nudge that frees `old` without unpowering anything.

    `keep_clear` is the belt route itself: stepping out of the way and onto the
    same route solves nothing. Returns None when no position works, which is
    the caller's signal to route around rather than relocate.
    """
    spec = POLE_SPECS.get(pole)
    if spec is None or spec["size"] != 1:
        # A substation or big pole occupies 2x2 and anchors far more than it
        # supplies; moving one is a network decision, not a routing one.
        return None
    for candidate in candidate_positions(old):
        tile = (math.floor(candidate[0]), math.floor(candidate[1]))
        if tile in blocked or tile in keep_clear:
            continue
        if not keeps_supply(candidate, supplied, spec["supply"]):
            continue
        if not keeps_wire(candidate, neighbours, spec["wire"]):
            continue
        return PoleMove(pole=pole, old=old, new=candidate)
    return None


def relocation_actions(move: PoleMove) -> list[dict]:
    """Place the replacement BEFORE removing the original.

    Order matters on a live base: removing first drops every consumer in the
    old supply area for as long as the pair takes to apply, and a machine that
    loses power mid-build reports as blocked and drags a stage into diagnosis.
    """
    return [
        {
            "action_type": "place_entity", "entity": move.pole,
            "position": {"x": move.new[0], "y": move.new[1]},
        },
        {
            "action_type": "remove_entity", "entity": move.pole,
            "position": {"x": move.old[0], "y": move.old[1]},
        },
    ]


def relocation_plan(moves: Sequence[PoleMove]) -> dict:
    """One phase carrying every pole nudge a route needs."""
    if not moves:
        raise ValueError("A pole relocation plan needs at least one move")
    actions: list[dict] = []
    for move in moves:
        actions.extend(relocation_actions(move))
    return {"phases": [{"name": "relocate_blocking_poles", "actions": actions}]}


def staged_relocation_plans(
    moves: Sequence[PoleMove],
) -> tuple[dict, dict]:
    """Separate bot-built replacements from old-pole removals.

    A build plan executes every action immediately, while a replacement pole
    is now a construction ghost. Keeping placement and removal in one plan
    would therefore cut the live network before bots revived the replacement.
    The submit boundary waits for the first plan's formerly-direct pole action;
    only a later submission may remove the original.
    """
    combined = relocation_plan(moves)
    actions = combined["phases"][0]["actions"]
    placements = [
        action for action in actions
        if action["action_type"] == "place_entity"
    ]
    removals = [
        action for action in actions
        if action["action_type"] == "remove_entity"
    ]
    return (
        {"phases": [{"name": "place_relocated_poles", "actions": placements}]},
        {"phases": [{"name": "retire_relocated_poles", "actions": removals}]},
    )


def corridor_tiles(start: Point, end: Point) -> set[tuple[int, int]]:
    """Tiles either L-shaped route between two points could use.

    Both elbows are kept because the router picks whichever is clearer, and a
    pole nudged off one L onto the other has not moved out of the way at all.
    """
    x0, y0 = math.floor(start[0]), math.floor(start[1])
    x1, y1 = math.floor(end[0]), math.floor(end[1])
    xs = range(min(x0, x1), max(x0, x1) + 1)
    ys = range(min(y0, y1), max(y0, y1) + 1)
    return (
        {(x, y0) for x in xs} | {(x1, y) for y in ys}      # horizontal first
        | {(x0, y) for y in ys} | {(x, y1) for x in xs}    # vertical first
    )
