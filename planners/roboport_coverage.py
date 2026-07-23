# Path: planners/roboport_coverage.py
# Purpose: Derive the EXTRA roboports a composed block needs so that construction
# coverage follows the geometry actually emitted, not the anchor list.
#
# WHY THIS EXISTS
#   plan_roboport_network() chains roboports over the block's *named anchors*.
#   That is the right skeleton, but item and fluid routes are solved afterwards
#   and deliberately wander around obstacles, so their `place_ghost` tiles can
#   sit far outside the anchor tree. Bots only build a ghost inside a roboport's
#   55-tile construction square, so those tiles are simply never built -- a
#   silent, expensive live failure. This module takes the FINISHED placements,
#   finds the ghosts nothing covers, and walks new roboports out towards them in
#   hops of at most ROBOPORT_LINK_DISTANCE so the result stays ONE logistic
#   network. Distances are Chebyshev: roboport areas are squares.

from __future__ import annotations

from typing import Iterable, List, Sequence, Set, Tuple

from planners.infrastructure import (
    ROBOPORT_CONSTRUCTION_RADIUS, ROBOPORT_ENTITY, ROBOPORT_LINK_DISTANCE,
    ROBOPORT_SIZE, ROBOPORT_SPACING,
)
from planners.infrastructure_geometry import chebyshev_distance
from planners.plan_validation import actions

Point = Tuple[float, float]
Tile = Tuple[int, int]

# Hop length when extending the chain. ROBOPORT_SPACING is already the validated
# chain pitch and is comfortably inside ROBOPORT_LINK_DISTANCE.
CHAIN_STEP = float(ROBOPORT_SPACING)

# Substation offsets sandbox_infrastructure.roboport_power_sites() will try; a
# new roboport is only usable if at least one of them is free, otherwise the
# roboport itself ends up unpowered.
POWER_SITE_OFFSETS = ((-7, 0), (7, 0), (0, -7), (0, 7), (-7, -7), (7, -7), (-7, 7), (7, 7))
SUBSTATION_SIZE = 2

# Search ring for nudging a candidate off an obstacle, ordered by ring then by
# (dx, dy) so the outcome is deterministic.
_NUDGE_OFFSETS: List[Tuple[int, int]] = [(0, 0)] + [
    (dx, dy)
    for radius in range(1, 13)
    for dx in range(-radius, radius + 1)
    for dy in range(-radius, radius + 1)
    if max(abs(dx), abs(dy)) == radius
]


def footprint_tiles(centre: Point, size: int) -> Set[Tile]:
    """Tile indices a square footprint occupies -- same convention as
    plan_validation.occupied_tile_indices()."""
    left, top = int(centre[0] - size / 2), int(centre[1] - size / 2)
    return {(left + i, top + j) for i in range(size) for j in range(size)}


def roboport_ghost_targets(named_plans: Sequence[Tuple[str, dict]]) -> List[Point]:
    """Every tile bots must build: the `place_ghost` positions of these plans.

    `place_entity` items are placed directly by the executor and need no bot,
    which is the same distinction planners.preflight draws.
    """
    targets = {
        (action["position"]["x"], action["position"]["y"])
        for _, plan in named_plans
        for action in actions(plan)
        if action.get("action_type") == "place_ghost"
        and action.get("entity") != ROBOPORT_ENTITY
    }
    return sorted(targets)


def uncovered_targets(targets: Iterable[Point], roboports: Sequence[Point]) -> List[Point]:
    return [
        target for target in targets
        if not any(chebyshev_distance(target, port) <= ROBOPORT_CONSTRUCTION_RADIUS
                   for port in roboports)
    ]


def _step_towards(anchor: Point, target: Point) -> Point:
    span = chebyshev_distance(anchor, target)
    scale = min(1.0, CHAIN_STEP / span) if span else 0.0
    return (round(anchor[0] + (target[0] - anchor[0]) * scale),
            round(anchor[1] + (target[1] - anchor[1]) * scale))


def _is_free(position: Point, placed: Sequence[Point], obstacles: Set[Tile]) -> bool:
    if any(chebyshev_distance(position, other) < ROBOPORT_SIZE for other in placed):
        return False
    if footprint_tiles(position, ROBOPORT_SIZE) & obstacles:
        return False
    # Needs a spot for its substation, or the roboport has no power.
    return any(
        not (footprint_tiles((position[0] + dx, position[1] + dy), SUBSTATION_SIZE) & obstacles)
        and not any(chebyshev_distance((position[0] + dx, position[1] + dy), other)
                    < (SUBSTATION_SIZE + ROBOPORT_SIZE) / 2 for other in placed)
        for dx, dy in POWER_SITE_OFFSETS
    )


def _settle(candidate: Point, anchor: Point, placed: Sequence[Point], obstacles: Set[Tile]) -> Point:
    for dx, dy in _NUDGE_OFFSETS:
        position = (candidate[0] + dx, candidate[1] + dy)
        if chebyshev_distance(position, anchor) > ROBOPORT_LINK_DISTANCE:
            continue
        if _is_free(position, placed, obstacles):
            return position
    raise ValueError(
        f"No buildable roboport position near {candidate} that stays within "
        f"{ROBOPORT_LINK_DISTANCE:.0f} tiles of {anchor}"
    )


def plan_coverage_roboports(
    existing: Sequence[Point],
    targets: Sequence[Point],
    obstacles: Set[Tile] | None = None,
    limit: int = 128,
) -> List[Point]:
    """Extra roboport positions that bring every `targets` tile into construction
    range while every new roboport stays chained to the existing network.

    `existing` are the roboports already planned (assumed one connected network),
    `obstacles` the tile indices already claimed by emitted geometry. Returns []
    when the existing network already covers everything.
    """
    placed = [tuple(port) for port in existing]
    if not placed:
        raise ValueError("plan_coverage_roboports needs at least one existing roboport")
    obstacles = set(obstacles or ())

    extra: List[Point] = []
    remaining = uncovered_targets(sorted(targets), placed)
    while remaining:
        if len(extra) >= limit:
            raise ValueError(
                f"Roboport coverage did not converge after {limit} extra roboports; "
                f"{len(remaining)} tile(s) still uncovered, first {remaining[0]}"
            )
        target = remaining[0]
        anchor = min(placed, key=lambda port: (chebyshev_distance(port, target), port))
        position = _settle(_step_towards(anchor, target), anchor, placed, obstacles)
        placed.append(position)
        extra.append(position)
        obstacles |= footprint_tiles(position, ROBOPORT_SIZE)
        remaining = uncovered_targets(remaining, [position])
    return extra
