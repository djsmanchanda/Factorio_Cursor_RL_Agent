# Path: planners/pumpjack_siting.py
# Purpose: Choose crude-oil sites while reserving machine bodies and connector stubs.

from __future__ import annotations

import math

from planners.infrastructure_geometry import footprint_tile_indices
from planners.plan_validation import ENTITY_FOOTPRINTS
from planners.resource_layouts import verified_pumpjack_output_tile

Point = tuple[float, float]
REFINERY_CRUDE_DRAW_PER_SECOND = 20.0
ESTIMATED_CRUDE_PER_PUMPJACK = 8.0
MAX_OPENING_PUMPJACKS = 4
_DIRECTIONS = ("north", "east", "south", "west")
_VECTORS = ((0, -1), (1, 0), (0, 1), (-1, 0))


def _footprint(position: Point) -> set[tuple[int, int]]:
    return footprint_tile_indices(position, ENTITY_FOOTPRINTS["pumpjack"])


def _orientations(position: Point) -> list[dict]:
    sites = []
    for direction in _DIRECTIONS:
        site = {"position": position, "resource": "crude-oil", "direction": direction}
        site["output"] = verified_pumpjack_output_tile(site)
        sites.append(site)
    return sites


def _target_cost(site: dict, target: Point) -> float:
    return abs(site["output"][0] - target[0]) + abs(site["output"][1] - target[1])


def pumpjack_site_nearest(
    position: Point, target: Point, *,
    blocked_tiles: set[tuple[int, int]] = frozenset(),
) -> dict | None:
    """Prefer facing the cell, then try other legal external connectors."""
    if _footprint(position) & blocked_tiles:
        return None
    sites = [site for site in _orientations(position) if site["output"] not in blocked_tiles]
    return min(sites, key=lambda site: (
        -sum(v * (t - p) for v, t, p in zip(
            _VECTORS[_DIRECTIONS.index(site["direction"])], target, position,
        )),
        _target_cost(site, target), _DIRECTIONS.index(site["direction"]),
    ), default=None)


def _reserve(site: dict, blocked: set[tuple[int, int]]) -> None:
    # Both directions matter: a later body must avoid prior stubs, and its
    # stub must avoid prior bodies. Shared stubs are unnecessary at siting.
    blocked.update(_footprint(site["position"]))
    blocked.add(site["output"])


def _wanted(draw: float, per_jack: float, limit: int) -> int:
    return max(1, min(limit, math.ceil(draw / per_jack)))


def extra_pumpjack_spots(
    tiles: list[Point], existing: Point | None, target: Point, *,
    draw_per_second: float = REFINERY_CRUDE_DRAW_PER_SECOND,
    est_per_jack: float = ESTIMATED_CRUDE_PER_PUMPJACK,
    max_jacks: int = MAX_OPENING_PUMPJACKS,
    blocked_tiles: set[tuple[int, int]] = frozenset(),
) -> list[dict]:
    """Choose additional wells nearest the existing well or refinery."""
    blocked = set(blocked_tiles)
    if existing is not None:
        # This legacy interface lacks the live direction. Protect all possible
        # outlets rather than infer or rotate an existing machine.
        for site in _orientations(existing):
            _reserve(site, blocked)
    remaining = _wanted(draw_per_second, est_per_jack, max_jacks) - int(existing is not None)
    origin = existing if existing is not None else target
    spots = []
    for candidate in sorted(dict.fromkeys(tuple(map(float, t)) for t in tiles), key=lambda t: (math.dist(t, origin), t)):
        if len(spots) >= remaining:
            break
        site = pumpjack_site_nearest(candidate, target, blocked_tiles=blocked)
        if site is not None:
            spots.append(site)
            _reserve(site, blocked)
    return spots


def pumpjack_sites_for_patch(
    tiles: list[Point], primary: Point, target: Point, *,
    blocked_tiles: set[tuple[int, int]] = frozenset(),
    draw_per_second: float = REFINERY_CRUDE_DRAW_PER_SECOND,
    est_per_jack: float = ESTIMATED_CRUDE_PER_PUMPJACK,
    max_jacks: int = MAX_OPENING_PUMPJACKS,
) -> list[dict]:
    """Rank the first outlet toward the refinery, then join the local network."""
    candidates = list(dict.fromkeys(tuple(map(float, p)) for p in [primary, *tiles]))
    blocked = set(blocked_tiles)
    chosen = []
    wanted = _wanted(draw_per_second, est_per_jack, max_jacks)
    while candidates and len(chosen) < wanted:
        # Re-evaluate orientations after every reservation. A well whose
        # preferred outlet became blocked may still have a legal rotation.
        legal = [site for candidate in candidates if (
            site := pumpjack_site_nearest(candidate, target, blocked_tiles=blocked)
        ) is not None]
        if not legal:
            break

        def cost(site: dict) -> tuple:
            branch = min((_target_cost(site, prior["output"]) for prior in chosen), default=0)
            return branch, _target_cost(site, target), site["position"], site["direction"]

        selected = min(legal, key=cost)
        chosen.append(selected)
        _reserve(selected, blocked)
        candidates.remove(selected["position"])
    return chosen
