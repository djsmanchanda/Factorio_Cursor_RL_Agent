# Path: planners/fluid_layout_search.py
# Purpose: Generate and rank rotated/mirrored fluid-row layouts from reusable geometry.

from __future__ import annotations

import copy
import heapq
import math
from dataclasses import dataclass
from typing import Iterable, Mapping

from core.fluid_systems import flip_offset, rotate_offset, validate_network_purity
from planners.fluid_layouts import (
    FLUID_RECIPES,
    fluid_network_segments,
    generate_fluid_machine_row,
    header_attachment,
)
from planners.infrastructure import strip_local_power
from planners.plan_validation import (
    actions,
    occupied_tile_indices,
    validate_build_plan,
)

Point = tuple[float, float]
Tile = tuple[int, int]

CARDINALS = ("north", "east", "south", "west")
MIRRORS = (None, "horizontal")
_DIRECTION_VECTOR = {
    "north": (0, -1), "east": (1, 0),
    "south": (0, 1), "west": (-1, 0),
}
_VECTOR_DIRECTION = {value: key for key, value in _DIRECTION_VECTOR.items()}


@dataclass(frozen=True)
class FluidLayoutCandidate:
    shape: str
    plan: dict
    origin: Point
    direction: str
    mirror: str | None
    attachments: dict[str, Tile]
    pipe_tiles: int
    pole_count: int
    occupied_tiles: int
    expansion_seam_tiles: int
    score: tuple[int, ...]


@dataclass(frozen=True)
class PairedFluidBlock:
    plan: dict
    segments: tuple[dict, ...]
    attachments: dict[str, Tile]
    machine_count: int


def _transform_vector(
    vector: tuple[float, float], direction: str, mirror: str | None,
) -> tuple[float, float]:
    transformed = flip_offset(vector, mirror) if mirror is not None else vector
    return rotate_offset(transformed, direction)


def transform_point(
    point: Point, *, origin: Point = (0.0, 0.0),
    direction: str = "north", mirror: str | None = None,
) -> Point:
    """Apply one of the eight square symmetries, then translate to ``origin``."""
    if direction not in CARDINALS:
        raise ValueError(f"Unknown fluid-layout direction: {direction!r}")
    if mirror not in MIRRORS:
        raise ValueError(f"Unknown fluid-layout mirror: {mirror!r}")
    x, y = _transform_vector(point, direction, mirror)
    return origin[0] + x, origin[1] + y


def transform_tile(
    tile: Tile, *, origin: Point = (0.0, 0.0),
    direction: str = "north", mirror: str | None = None,
) -> Tile:
    """Transform an occupied tile through its centre without half-tile drift."""
    x, y = transform_point(
        (tile[0] + 0.5, tile[1] + 0.5), origin=origin,
        direction=direction, mirror=mirror,
    )
    return math.floor(x), math.floor(y)


def transform_direction(
    facing: str, *, direction: str = "north", mirror: str | None = None,
) -> str:
    if facing not in CARDINALS:
        raise ValueError(f"Unknown entity direction: {facing!r}")
    vector = _transform_vector(_DIRECTION_VECTOR[facing], direction, mirror)
    return _VECTOR_DIRECTION[(int(vector[0]), int(vector[1]))]


def _oriented_plan(
    source: dict, origin: Point, direction: str, mirror: str | None,
    directional_machines: set[str],
) -> dict:
    plan = copy.deepcopy(source)
    for action in actions(plan):
        position = action.get("position")
        if position is not None:
            x, y = transform_point(
                (float(position["x"]), float(position["y"])), origin=origin,
                direction=direction, mirror=mirror,
            )
            position["x"], position["y"] = x, y
        facing = action.get("direction")
        if facing in CARDINALS:
            action["direction"] = transform_direction(
                facing, direction=direction, mirror=mirror,
            )
        elif action.get("entity") in directional_machines:
            action["direction"] = transform_direction(
                "north", direction=direction, mirror=mirror,
            )
    validate_build_plan(plan)
    return plan


def oriented_fluid_machine_row(
    recipe: str, machine_count: int, origin: Point = (0.0, 0.0), *,
    direction: str = "north", mirror: str | None = None,
    belt_type: str = "transport-belt", inserter_type: str = "fast-inserter",
) -> dict:
    """Rotate/mirror the existing verified row as one rigid layout candidate."""
    base = generate_fluid_machine_row(
        recipe, machine_count, 0, 0, belt_type, inserter_type,
    )
    machine = FLUID_RECIPES[recipe]["machine"]
    plan = _oriented_plan(base, origin, direction, mirror, {machine})
    validate_network_purity(
        oriented_fluid_network_segments(
            recipe, machine_count, origin,
            direction=direction, mirror=mirror,
        )
    )
    return plan


def oriented_fluid_network_segments(
    recipe: str, machine_count: int, origin: Point = (0.0, 0.0), *,
    direction: str = "north", mirror: str | None = None,
) -> list[dict]:
    return _transform_segments(
        fluid_network_segments(recipe, machine_count), origin, direction, mirror,
    )


def _transform_segments(
    segments: Iterable[dict], origin: Point, direction: str, mirror: str | None,
) -> list[dict]:
    result = []
    for segment in segments:
        transformed = {**segment}
        transformed["tiles"] = [
            transform_tile(
                tuple(tile), origin=origin, direction=direction, mirror=mirror,
            )
            for tile in segment.get("tiles", ())
        ]
        transformed["tunnel_endpoints"] = [
            (
                transform_tile(tuple(start), origin=origin, direction=direction, mirror=mirror),
                transform_tile(tuple(end), origin=origin, direction=direction, mirror=mirror),
            )
            for start, end in segment.get("tunnel_endpoints", ())
        ]
        result.append(transformed)
    return result


def oriented_header_attachment(
    recipe: str, fluid: str, machine_count: int, origin: Point = (0.0, 0.0), *,
    direction: str = "north", mirror: str | None = None,
) -> dict:
    base = header_attachment(recipe, fluid, machine_count)
    outward = _DIRECTION_VECTOR[base["side"]]
    side = transform_direction(base["side"], direction=direction, mirror=mirror)
    world_west = transform_tile(
        tuple(base["west"]), origin=origin, direction=direction, mirror=mirror,
    )
    return {
        **base,
        "side": side,
        "row": world_west[1] if side in {"north", "south"} else None,
        "column": world_west[0] if side in {"east", "west"} else None,
        "west": world_west,
        "attach": transform_tile(
            tuple(base["attach"]), origin=origin, direction=direction, mirror=mirror,
        ),
        "outward": _transform_vector(outward, direction, mirror),
    }


def _plan_bounds(plan: dict) -> tuple[int, int, int, int]:
    tiles = occupied_tile_indices([("fluid-layout", plan)])
    if not tiles:
        raise ValueError("Fluid layout has no occupied tiles")
    return (
        min(x for x, _ in tiles), min(y for _, y in tiles),
        max(x for x, _ in tiles), max(y for _, y in tiles),
    )


def _centred_origin(plan: dict, center: Point) -> Point:
    min_x, min_y, max_x, max_y = _plan_bounds(plan)
    return (
        float(round(center[0] - (min_x + max_x + 1) / 2)),
        float(round(center[1] - (min_y + max_y + 1) / 2)),
    )


def _shortest_distance(start: Tile, goal: Tile, blocked: set[Tile]) -> int | None:
    """Bounded orthogonal A* used only to compare candidate attachment costs."""
    blocked = set(blocked) - {start, goal}
    margin = 24
    lo_x, hi_x = min(start[0], goal[0]) - margin, max(start[0], goal[0]) + margin
    lo_y, hi_y = min(start[1], goal[1]) - margin, max(start[1], goal[1]) + margin
    queue = [(abs(goal[0] - start[0]) + abs(goal[1] - start[1]), 0, start)]
    best = {start: 0}
    while queue:
        _estimate, cost, point = heapq.heappop(queue)
        if point == goal:
            return cost
        if cost != best.get(point):
            continue
        x, y = point
        for nxt in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if not (lo_x <= nxt[0] <= hi_x and lo_y <= nxt[1] <= hi_y):
                continue
            if nxt in blocked:
                continue
            new_cost = cost + 1
            if new_cost >= best.get(nxt, 10**9):
                continue
            best[nxt] = new_cost
            estimate = new_cost + abs(goal[0] - nxt[0]) + abs(goal[1] - nxt[1])
            heapq.heappush(queue, (estimate, new_cost, nxt))
    return None


def oriented_fluid_row_candidates(
    recipe: str, machine_count: int, center: Point,
    terminals: Mapping[str, Iterable[Point]], *,
    blocked_tiles: set[Tile] = frozenset(),
    directions: Iterable[str] = CARDINALS,
    mirrors: Iterable[str | None] = MIRRORS,
) -> list[FluidLayoutCandidate]:
    """Return legal D4 row variants ordered by routed construction cost.

    Hard legality (self-collision, live occupancy, route existence and fluid
    purity) filters candidates.  Pipe/pole/land/expansion preferences stay in
    the explicit score so training can replace or tune them later.
    """
    candidates: list[FluidLayoutCandidate] = []
    seen: set[tuple] = set()
    for direction_index, direction in enumerate(directions):
        for mirror_index, mirror in enumerate(mirrors):
            local = oriented_fluid_machine_row(
                recipe, machine_count, direction=direction, mirror=mirror,
            )
            origin = _centred_origin(local, center)
            plan = oriented_fluid_machine_row(
                recipe, machine_count, origin,
                direction=direction, mirror=mirror,
            )
            claimed = occupied_tile_indices([("fluid-layout", plan)])
            if claimed & blocked_tiles:
                continue
            fingerprint = tuple(sorted(
                (action.get("entity"), action["position"]["x"], action["position"]["y"],
                 action.get("direction"), action.get("recipe"))
                for action in actions(plan) if "position" in action
            ))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            attachments = {
                fluid: oriented_header_attachment(
                    recipe, fluid, machine_count, origin,
                    direction=direction, mirror=mirror,
                )["attach"]
                for fluid in terminals
            }
            non_pipe = {
                tile
                for action in actions(plan)
                if action.get("entity") not in {"pipe", "pipe-to-ground"}
                for tile in occupied_tile_indices([
                    ("action", {"phases": [{"actions": [action]}]}),
                ])
            }
            route_tiles = 0
            legal = True
            for fluid, endpoints in terminals.items():
                for endpoint in endpoints:
                    goal = (math.floor(endpoint[0]), math.floor(endpoint[1]))
                    distance = _shortest_distance(
                        attachments[fluid], goal, set(blocked_tiles) | non_pipe,
                    )
                    if distance is None:
                        legal = False
                        break
                    route_tiles += distance
                if not legal:
                    break
            if not legal:
                continue
            counts: dict[str, int] = {}
            for action in actions(plan):
                entity = action.get("entity", "")
                counts[entity] = counts.get(entity, 0) + 1
            min_x, min_y, max_x, max_y = _plan_bounds(plan)
            # Short side is the cheapest place to reserve a parallel mirrored
            # row; retaining it distinguishes equally routed candidates.
            seam = min(max_x - min_x + 1, max_y - min_y + 1)
            pipe_tiles = route_tiles + counts.get("pipe", 0) + counts.get("pipe-to-ground", 0)
            pole_count = counts.get("medium-electric-pole", 0) + counts.get("substation", 0)
            land = len(claimed)
            score = (
                pipe_tiles, pole_count, land, seam,
                1, direction_index, mirror_index,
            )
            candidates.append(FluidLayoutCandidate(
                shape="row", plan=plan, origin=origin,
                direction=direction, mirror=mirror,
                attachments=attachments, pipe_tiles=pipe_tiles,
                pole_count=pole_count, occupied_tiles=land,
                expansion_seam_tiles=seam, score=score,
            ))
    return sorted(candidates, key=lambda candidate: candidate.score)


def recover_oriented_fluid_row(
    recipe: str, machine_positions: Iterable[Point],
    observed_pipe_tiles: set[Tile],
) -> tuple[Point, str, str | None]:
    """Recover a row's transform from live machines and its local pipe tiles.

    Machine centres identify rotation and translation.  A horizontal mirror
    can leave a symmetric machine set unchanged, so the expected verified
    fluid network breaks that tie.  This makes rotated/mirrored plans safe to
    resume after a controller restart without storing process-local state.
    """
    observed = {
        (round(float(x), 3), round(float(y), 3)) for x, y in machine_positions
    }
    if not observed:
        raise ValueError("Cannot recover a fluid row without machine positions")
    machine = FLUID_RECIPES[recipe]["machine"]
    hypotheses: list[tuple[tuple, Point, str, str | None]] = []
    seen: set[tuple] = set()
    for direction_index, direction in enumerate(CARDINALS):
        for mirror_index, mirror in enumerate(MIRRORS):
            local = oriented_fluid_machine_row(
                recipe, len(observed), direction=direction, mirror=mirror,
            )
            local_machines = [
                (float(action["position"]["x"]), float(action["position"]["y"]))
                for action in actions(local) if action.get("entity") == machine
            ]
            for live_x, live_y in observed:
                for local_x, local_y in local_machines:
                    origin = (live_x - local_x, live_y - local_y)
                    key = (origin, direction, mirror)
                    if key in seen:
                        continue
                    seen.add(key)
                    expected = {
                        (round(local_position[0] + origin[0], 3),
                         round(local_position[1] + origin[1], 3))
                        for local_position in local_machines
                    }
                    if expected != observed:
                        continue
                    networks = oriented_fluid_network_segments(
                        recipe, len(observed), origin,
                        direction=direction, mirror=mirror,
                    )
                    expected_pipes = {
                        tuple(tile) for segment in networks
                        for tile in segment.get("tiles", ())
                    }
                    overlap = len(expected_pipes & observed_pipe_tiles)
                    missing = len(expected_pipes - observed_pipe_tiles)
                    score = (-overlap, missing, direction_index, mirror_index, origin)
                    hypotheses.append((score, origin, direction, mirror))
    if not hypotheses:
        raise ValueError("Live machines do not match any oriented fluid row")
    _score, origin, direction, mirror = min(hypotheses)
    return origin, direction, mirror


def compact_paired_fluid_block(
    recipe: str, machine_count: int, origin: Point = (0.0, 0.0), *,
    belt_type: str = "transport-belt", inserter_type: str = "fast-inserter",
) -> PairedFluidBlock:
    """Build two opposing half-rows around one shared single-fluid seam.

    This is a structural edit operator, not a stored blueprint.  It derives
    both rows from the verified row generator, mirrors the second row, removes
    their redundant inward headers and outer pole rows, then synthesizes the
    shortest straight shared seam through the coincident machine connectors.
    Recipes exposing multiple different fluids on the inward face are refused:
    one seam could mix them, so those need a wider searched topology.
    """
    if machine_count < 2 or machine_count % 2:
        raise ValueError("Compact paired fluid blocks require an even machine count")
    half = machine_count // 2
    inward_fluids = [
        fluid
        for fluid in (
            *FLUID_RECIPES[recipe]["fluid_ingredients"],
            *FLUID_RECIPES[recipe]["fluid_products"],
        )
        if header_attachment(recipe, fluid, half)["side"] == "north"
    ]
    inward_fluids = list(dict.fromkeys(inward_fluids))
    if len(inward_fluids) != 1:
        raise ValueError(
            f"Compact paired {recipe} needs exactly one shared-facing fluid; "
            f"found {inward_fluids}"
        )
    shared_fluid = inward_fluids[0]
    bottom = strip_local_power(
        oriented_fluid_machine_row(
            recipe, half, origin, belt_type=belt_type,
            inserter_type=inserter_type,
        ),
        remove_substations=True,
    )
    # direction=south + horizontal reflection is a vertical mirror: machine
    # columns stay aligned while the second row faces the first across one tile.
    top_origin = (origin[0], origin[1] + 3.0)
    top = strip_local_power(
        oriented_fluid_machine_row(
            recipe, half, top_origin, direction="south", mirror="horizontal",
            belt_type=belt_type, inserter_type=inserter_type,
        ),
        remove_substations=True,
    )
    bottom_segments = oriented_fluid_network_segments(recipe, half, origin)
    top_segments = oriented_fluid_network_segments(
        recipe, half, top_origin, direction="south", mirror="horizontal",
    )
    bottom_shared = next(s for s in bottom_segments if s["fluid"] == shared_fluid)
    top_shared = next(s for s in top_segments if s["fluid"] == shared_fluid)
    shared_stubs = {
        tuple(start) for start, _end in bottom_shared.get("tunnel_endpoints", ())
    } & {
        tuple(start) for start, _end in top_shared.get("tunnel_endpoints", ())
    }
    if len(shared_stubs) != half or len({y for _x, y in shared_stubs}) != 1:
        raise ValueError(f"Mirrored {recipe} rows do not expose one aligned shared seam")
    shared_tiles = {
        tuple(tile) for tile in bottom_shared["tiles"]
    } | {tuple(tile) for tile in top_shared["tiles"]}

    combined_actions = []
    pole_counts: dict[tuple[float, float], int] = {}
    for plan in (bottom, top):
        for action in actions(plan):
            if action.get("entity") == "medium-electric-pole":
                key = (
                    float(action["position"]["x"]),
                    float(action["position"]["y"]),
                )
                pole_counts[key] = pole_counts.get(key, 0) + 1
                continue
            if action.get("entity") in {"pipe", "pipe-to-ground"}:
                tile = (
                    math.floor(action["position"]["x"]),
                    math.floor(action["position"]["y"]),
                )
                if tile in shared_tiles:
                    continue
            combined_actions.append(copy.deepcopy(action))
    # Duplicate poles proposed by both rows occupy the fluid seam.  Keep the
    # two outer rows instead: one pole per machine remains half the long-row
    # generator's two-pole-per-machine bill and leaves the shared header clear.
    combined_actions.extend({
        "action_type": "place_ghost", "entity": "medium-electric-pole",
        "position": {"x": x, "y": y},
    } for (x, y), count in sorted(pole_counts.items()) if count == 1)
    seam_y = next(iter(shared_stubs))[1]
    seam_min = min(x for x, _y in shared_stubs)
    seam_max = max(x for x, _y in shared_stubs)
    # The outer pole rows are twelve tiles apart, beyond a medium pole's wire
    # reach. One pole immediately west of the shared seam joins both rows while
    # remaining outside every 5x5 body and off the fluid header.
    combined_actions.append({
        "action_type": "place_ghost", "entity": "medium-electric-pole",
        "position": {"x": seam_min - 1.5, "y": seam_y + 0.5},
    })
    seam = [(x, seam_y) for x in range(seam_min, seam_max + 1)]
    combined_actions.extend({
        "action_type": "place_ghost", "entity": "pipe",
        "position": {"x": x + 0.5, "y": seam_y + 0.5},
    } for x, _y in seam)
    plan = {"phases": [{"name": f"compact_paired_{recipe}", "actions": combined_actions}]}
    validate_build_plan(plan)

    segments = [
        segment for segment in (*bottom_segments, *top_segments)
        if segment["fluid"] != shared_fluid
    ] + [{
        "fluid": shared_fluid,
        "separated_by_pump": False,
        "tiles": seam,
        "tunnel_endpoints": [],
    }]
    validate_network_purity(segments)
    attachments = {}
    for fluid in {
        *FLUID_RECIPES[recipe]["fluid_ingredients"],
        *FLUID_RECIPES[recipe]["fluid_products"],
    }:
        fluid_segments = [segment for segment in segments if segment["fluid"] == fluid]
        tiles = {tuple(tile) for segment in fluid_segments for tile in segment["tiles"]}
        # Expose the lexicographically stable perimeter tile nearest the block's
        # west edge.  The external router still chooses its obstacle-aware path.
        attachments[fluid] = min(tiles, key=lambda tile: (tile[0], tile[1]))
    return PairedFluidBlock(
        plan=plan, segments=tuple(segments), attachments=attachments,
        machine_count=machine_count,
    )


def oriented_compact_paired_fluid_block(
    recipe: str, machine_count: int, origin: Point = (0.0, 0.0), *,
    direction: str = "north", mirror: str | None = None,
    belt_type: str = "transport-belt", inserter_type: str = "fast-inserter",
) -> PairedFluidBlock:
    """Apply a rigid rotation/reflection to a generated compact paired block."""
    base = compact_paired_fluid_block(
        recipe, machine_count, belt_type=belt_type, inserter_type=inserter_type,
    )
    machine = FLUID_RECIPES[recipe]["machine"]
    return PairedFluidBlock(
        plan=_oriented_plan(base.plan, origin, direction, mirror, {machine}),
        segments=tuple(_transform_segments(base.segments, origin, direction, mirror)),
        attachments={
            fluid: transform_tile(
                tile, origin=origin, direction=direction, mirror=mirror,
            )
            for fluid, tile in base.attachments.items()
        },
        machine_count=machine_count,
    )


def fluid_block_candidates(
    recipe: str, machine_count: int, center: Point,
    terminals: Mapping[str, Iterable[Point]], *,
    blocked_tiles: set[Tile] = frozenset(),
) -> list[FluidLayoutCandidate]:
    """Rank long-row and compact paired-row shapes across all D4 transforms."""
    candidates = oriented_fluid_row_candidates(
        recipe, machine_count, center, terminals,
        blocked_tiles=blocked_tiles,
    )
    try:
        compact_paired_fluid_block(recipe, machine_count)
    except ValueError:
        return candidates
    for direction_index, direction in enumerate(CARDINALS):
        for mirror_index, mirror in enumerate(MIRRORS):
            local_block = oriented_compact_paired_fluid_block(
                recipe, machine_count, direction=direction, mirror=mirror,
            )
            origin = _centred_origin(local_block.plan, center)
            block = oriented_compact_paired_fluid_block(
                recipe, machine_count, origin,
                direction=direction, mirror=mirror,
            )
            plan = block.plan
            claimed = occupied_tile_indices([("paired-fluid-layout", plan)])
            if claimed & blocked_tiles:
                continue
            attachments = {
                fluid: tile for fluid, tile in block.attachments.items()
                if fluid in terminals
            }
            non_pipe = {
                tile
                for action in actions(plan)
                if action.get("entity") not in {"pipe", "pipe-to-ground"}
                for tile in occupied_tile_indices([
                    ("action", {"phases": [{"actions": [action]}]}),
                ])
            }
            route_tiles = 0
            legal = True
            for fluid, endpoints in terminals.items():
                if fluid not in attachments:
                    legal = False
                    break
                for endpoint in endpoints:
                    goal = (math.floor(endpoint[0]), math.floor(endpoint[1]))
                    distance = _shortest_distance(
                        attachments[fluid], goal, set(blocked_tiles) | non_pipe,
                    )
                    if distance is None:
                        legal = False
                        break
                    route_tiles += distance
                if not legal:
                    break
            if not legal:
                continue
            counts: dict[str, int] = {}
            for action in actions(plan):
                entity = action.get("entity", "")
                counts[entity] = counts.get(entity, 0) + 1
            min_x, min_y, max_x, max_y = _plan_bounds(plan)
            seam = min(max_x - min_x + 1, max_y - min_y + 1)
            pipe_tiles = route_tiles + counts.get("pipe", 0) + counts.get("pipe-to-ground", 0)
            pole_count = counts.get("medium-electric-pole", 0) + counts.get("substation", 0)
            land = len(claimed)
            score = (
                pipe_tiles, pole_count, land, seam,
                0, direction_index, mirror_index,
            )
            candidates.append(FluidLayoutCandidate(
                shape="paired", plan=plan, origin=origin,
                direction=direction, mirror=mirror,
                attachments=attachments, pipe_tiles=pipe_tiles,
                pole_count=pole_count, occupied_tiles=land,
                expansion_seam_tiles=seam, score=score,
            ))
    return sorted(candidates, key=lambda candidate: candidate.score)
