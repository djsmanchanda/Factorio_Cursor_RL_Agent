# Path: orchestrator/roboport_placement.py
# Purpose: Relocate coverage-chain roboports away from existing entities and stage ghosts (pure planners plus live drivers).

from __future__ import annotations

import math
from collections.abc import Generator

from orchestrator.reach_survey import ReachSurvey, drive_reach_planner, read_reach_survey
from planners.infrastructure_geometry import footprint_tile_indices
from tools.rcon_client import RconClient

Point = tuple[float, float]

# A factory corridor can be wider than one local 10-tile detour, especially
# after mine transport and power routes have been built.  Keep the cheap local
# search first, then widen the connected fallback far enough to route around a
# complete district without baking in map coordinates.
_ALTERNATE_SEARCH_RADII = (20, 32, 48, 64, 80)
_ALTERNATE_BEAM = 512


def _service_distance(source: Point, target: Point, square: bool) -> float:
    if square:
        return max(abs(source[0] - target[0]), abs(source[1] - target[1]))
    return math.dist(source, target)


def _local_chain_planner(
    source: Point,
    target: Point,
    ideals: list[Point],
    *,
    service_radius: float,
    service_square: bool,
    link_distance: float,
    search_radius: int = 10,
    reserved_tiles: set[tuple[int, int]] | None = None,
) -> Generator[ReachSurvey, object, list[Point]]:
    """Move chain centres off live and already-planned infrastructure.

    Pure: each candidate that passes the geometric checks is confirmed by one
    yielded 4x4 ``area_clear`` survey, in the order the live read ran.
    """
    reserved = reserved_tiles or set()
    placed: list[Point] = []
    previous = source
    for index, ideal in enumerate(ideals):
        following = ideals[index + 1] if index + 1 < len(ideals) else None
        candidates = sorted(
            {
                (ideal[0] + dx, ideal[1] + dy)
                for dx in range(-search_radius, search_radius + 1)
                for dy in range(-search_radius, search_radius + 1)
            },
            key=lambda point: (math.dist(point, ideal), point[0], point[1]),
        )
        chosen = None
        for point in candidates:
            if not (
                math.dist(previous, point) <= link_distance
                and (following is None or math.dist(point, following) <= link_distance)
                and (
                    following is not None
                    or _service_distance(point, target, service_square) <= service_radius
                )
                and not (footprint_tile_indices(point, 4) & reserved)
            ):
                continue
            if (yield ReachSurvey(
                "area_clear",
                min_point=(point[0] - 2, point[1] - 2),
                max_point=(point[0] + 2, point[1] + 2),
                flags=(("avoid_resources", True),),
            )):
                chosen = point
                break
        if chosen is None:
            raise ValueError(
                f"no clear 4x4 roboport site within {search_radius} tiles of {ideal}"
            )
        placed.append(chosen)
        previous = chosen
    return placed


def _local_chain_positions(
    client: RconClient,
    surface: str,
    source: Point,
    target: Point,
    ideals: list[Point],
    **options,
) -> list[Point]:
    """Live driver for `_local_chain_planner`."""
    return drive_reach_planner(
        _local_chain_planner(source, target, ideals, **options),
        lambda survey: read_reach_survey(client, surface, None, survey),
    )


def _alternate_chain_planner(
    source: Point,
    target: Point,
    ideals: list[Point],
    *,
    service_radius: float,
    service_square: bool,
    link_distance: float,
    reserved_tiles: set[tuple[int, int]],
) -> Generator[ReachSurvey, object, list[Point]]:
    """Search a bounded connected corridor after a local ideal is blocked.

    The local placer is intentionally cheap, but a single large factory or
    coastline can make ten tiles around an ideal unusable. Surveying the
    complete bounded corridor once lets this fallback bend several consecutive
    hops together without treating existing infrastructure as removable.
    """
    max_radius = max(_ALTERNATE_SEARCH_RADII)
    min_x = min([source[0], target[0], *[point[0] for point in ideals]]) - max_radius - 3
    max_x = max([source[0], target[0], *[point[0] for point in ideals]]) + max_radius + 3
    min_y = min([source[1], target[1], *[point[1] for point in ideals]]) - max_radius - 3
    max_y = max([source[1], target[1], *[point[1] for point in ideals]]) + max_radius + 3
    blocked = yield ReachSurvey(
        "occupied_tiles", min_point=(min_x, min_y), max_point=(max_x, max_y),
        flags=(("include_clutter", True), ("include_resources", True)),
    )

    def candidates(ideal: Point, radius: int) -> list[Point]:
        points = {
            (float(round(ideal[0] + dx)), float(round(ideal[1] + dy)))
            for dx in range(-radius, radius + 1)
            for dy in range(-radius, radius + 1)
        }
        points = {
            point for point in points
            if point != target
            and not (footprint_tile_indices(point, 4) & blocked)
            and not (footprint_tile_indices(point, 4) & reserved_tiles)
        }
        return sorted(points, key=lambda point: (math.dist(point, ideal), point))

    for radius in _ALTERNATE_SEARCH_RADII:
        states: list[tuple[float, Point, list[Point]]] = [(0.0, source, [])]
        for index, ideal in enumerate(ideals):
            next_states: dict[Point, tuple[float, Point, list[Point]]] = {}
            for candidate in candidates(ideal, radius):
                for score, previous, path in states:
                    if math.dist(previous, candidate) > link_distance:
                        continue
                    if index == len(ideals) - 1 and (
                        _service_distance(candidate, target, service_square)
                        > service_radius
                    ):
                        continue
                    candidate_score = score + math.dist(candidate, ideal)
                    existing = next_states.get(candidate)
                    if existing is None or candidate_score < existing[0]:
                        next_states[candidate] = (
                            candidate_score, candidate, [*path, candidate],
                        )
            if not next_states:
                states = []
                break
            states = sorted(
                next_states.values(), key=lambda item: (item[0], item[1]),
            )[:_ALTERNATE_BEAM]
        if states:
            return min(states, key=lambda item: (item[0], item[1]))[2]
    raise ValueError(
        "no connected roboport corridor within "
        f"{max_radius} tiles of the planned route"
    )


def _alternate_chain_positions(
    client: RconClient,
    surface: str,
    source: Point,
    target: Point,
    ideals: list[Point],
    **options,
) -> list[Point]:
    """Live driver for `_alternate_chain_planner`."""
    return drive_reach_planner(
        _alternate_chain_planner(source, target, ideals, **options),
        lambda survey: read_reach_survey(client, surface, None, survey),
    )


def clear_chain_positions(
    client: RconClient,
    surface: str,
    source: Point,
    target: Point,
    ideals: list[Point],
    *,
    service_radius: float,
    service_square: bool,
    link_distance: float,
    search_radius: int = 10,
    reserved_tiles: set[tuple[int, int]] | None = None,
) -> list[Point]:
    """Place a chain locally, then search alternate connected corridors.

    A blocked local ideal is recoverable when another corridor can connect the
    source to the target. Only after the bounded fallback is exhausted does the
    caller receive ``ValueError`` and decide whether the stage should defer.
    """
    reserved = reserved_tiles or set()
    try:
        return _local_chain_positions(
            client, surface, source, target, ideals,
            service_radius=service_radius, service_square=service_square,
            link_distance=link_distance, search_radius=search_radius,
            reserved_tiles=reserved,
        )
    except ValueError as local_error:
        try:
            return _alternate_chain_positions(
                client, surface, source, target, ideals,
                service_radius=service_radius, service_square=service_square,
                link_distance=link_distance, reserved_tiles=reserved,
            )
        except ValueError as alternate_error:
            raise ValueError(
                f"{local_error}; alternate corridor failed: {alternate_error}"
            ) from alternate_error


def clear_chain_planner(
    source: Point,
    target: Point,
    ideals: list[Point],
    *,
    service_radius: float,
    service_square: bool,
    link_distance: float,
    search_radius: int = 10,
    reserved_tiles: set[tuple[int, int]] | None = None,
) -> Generator[ReachSurvey, object, list[Point]]:
    """Pure `clear_chain_positions`: same local-then-alternate fallback."""
    reserved = reserved_tiles or set()
    try:
        return (yield from _local_chain_planner(
            source, target, ideals,
            service_radius=service_radius, service_square=service_square,
            link_distance=link_distance, search_radius=search_radius,
            reserved_tiles=reserved,
        ))
    except ValueError as local_error:
        try:
            return (yield from _alternate_chain_planner(
                source, target, ideals,
                service_radius=service_radius, service_square=service_square,
                link_distance=link_distance, reserved_tiles=reserved,
            ))
        except ValueError as alternate_error:
            raise ValueError(
                f"{local_error}; alternate corridor failed: {alternate_error}"
            ) from alternate_error
