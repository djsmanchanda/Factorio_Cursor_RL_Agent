# Path: planners/fluid_routing.py
# Purpose: Deterministic purity-safe routing between generated fluid stage headers.

from __future__ import annotations

import json
from heapq import heappop, heappush
from pathlib import Path
from typing import List

from jsonschema import Draft7Validator

from core.fluid_systems import (
    MAX_UNDERGROUND_SPAN, validate_network_purity, validate_underground_span,
)
from planners.local_layout_planner import _reject_fuel_entities

LINK_TUNNEL_CLEARANCE = 2
ROUTE_CLEARANCE = 1
ROUTE_SEARCH_MARGIN = 24
# A pipe-to-ground pair can bridge nine water tiles between endpoints. Longer
# straight crossings add landfill under the next pair of pipe endpoints.
MAX_TERRAIN_TUNNEL_TILES = MAX_UNDERGROUND_SPAN - 1
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _validate(plan: dict) -> None:
    schema_path = _REPO_ROOT / "schemas" / "build_plan.schema.json"
    with schema_path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    errors = list(Draft7Validator(schema).iter_errors(plan))
    if errors:
        joined = "\n".join(
            f"- {'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ValueError("BuildPlan validation FAILED:\n" + joined)
    _reject_fuel_entities(plan)

def _link_legs(from_point: tuple, to_points: List[tuple], trunk_x: int) -> tuple:
    """(horizontal legs as (row, x_a, x_b), trunk tiles) for a Z-shaped route."""
    xs = [from_point[0]] + [p[0] for p in to_points]
    if trunk_x >= min(xs):
        raise ValueError(
            f"trunk_x {trunk_x} must lie strictly WEST of every attachment "
            f"column (westmost is {min(xs)})"
        )
    rows = [from_point[1]] + [p[1] for p in to_points]
    legs = [(from_point[1], from_point[0], trunk_x)]
    legs += [(y, trunk_x, x) for x, y in to_points]
    trunk = [(trunk_x, y) for y in range(min(rows), max(rows) + 1)]
    return legs, trunk


def fluid_chain_link_trunk(from_point: tuple, to_points: List[tuple],
                           trunk_x: int) -> List[tuple]:
    """The link's vertical trunk tiles: what a SIBLING link has to tunnel under.

    At a crossing the trunk stays on the surface and the crossing leg dives, so
    the obstacle a sibling routes around is the trunk, not the whole footprint.
    Feeding siblings the whole footprint would be circular -- two links whose
    leg crosses the other's trunk would each demand the other tunnel, and
    neither could. Trunk tiles never move, so this is order-independent."""
    return _link_legs(from_point, to_points, trunk_x)[1]


def _obstacles(foreign: List[dict], fluid: str) -> set:
    """Tiles of every FOREIGN fluid: what the route has to tunnel under."""
    return {tuple(t) for seg in foreign
            if seg.get("fluid") is not None and seg["fluid"] != fluid
            for t in seg["tiles"]}


def _mixing_keepout(foreign: List[dict], fluid: str, endpoints: set) -> set:
    """Foreign-fluid tiles AND the ring of tiles orthogonally touching them.

    Two pipes that merely touch are connected in Factorio, so avoiding only the
    tiles a foreign fluid occupies is not enough to stay pure -- a route that
    settles one tile alongside crude oil has already mixed with it. Reserving
    the ring makes validate_network_purity hold by construction instead of
    catching the violation after the route is committed and failing the run.
    Observed live: a water route laid at (46,47) beside crude oil at (46,46).

    The ring is reserved ONLY around segments that validate_network_purity
    actually polices. A pump-separated segment is explicitly exempt there, so
    ringing it would forbid layouts the rules permit -- and does: it closes the
    one-tile corridors the designed electronics blocks route through. Occupancy
    is still blocked for every foreign fluid; only the adjacency margin is
    conditional, which keeps this constraint identical to the validator's.

    `endpoints` stay routable: a link has to reach its own source and consumers,
    and an endpoint genuinely adjacent to another fluid is a pump's job to
    separate, not something the router can dodge.
    """
    mixing = {
        tuple(t) for seg in foreign
        if seg.get("fluid") is not None and seg["fluid"] != fluid
        and not seg.get("separated_by_pump")
        for t in seg["tiles"]
    }
    ring = {
        neighbour
        for x, y in mixing
        for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
    }
    return (_obstacles(foreign, fluid) | ring) - endpoints


def _link_route(from_point: tuple, to_points: List[tuple], trunk_x: int,
                obstacles: set) -> tuple:
    """(plain pipe tiles, [(pipe-to-ground tile, direction)]) for the route."""
    legs, trunk = _link_legs(from_point, to_points, trunk_x)
    fouled = [t for t in trunk if t in obstacles]
    if fouled:
        raise ValueError(
            f"Trunk column {trunk_x} is not clear -- foreign fluid at {fouled[:3]}. "
            "A trunk cannot tunnel along itself; choose another column."
        )

    clear = LINK_TUNNEL_CLEARANCE
    pipes, undergrounds = set(trunk), []
    for row, a, b in legs:
        lo, hi = min(a, b), max(a, b)
        crossed = [x for x in range(lo, hi + 1) if (x, row) in obstacles]
        crossing_runs = []
        for col in crossed:
            if not crossing_runs or col - crossing_runs[-1][-1] > 2 * clear:
                crossing_runs.append([col])
            else:
                crossing_runs[-1].append(col)
        for run in crossing_runs:
            first, last = run[0], run[-1]
            if first - clear <= lo or last + clear >= hi:
                raise ValueError(
                    f"No room to tunnel under {(first, row)}..{(last, row)}: leg spans {lo}..{hi} and a "
                    f"crossing needs {clear} clear tiles plus a pipe on each side"
                )
            validate_underground_span((first - clear, row), (last + clear, row))
            # A pipe-to-ground's `direction` is where its NORMAL end points, so
            # the two ends of a west-east tunnel face away from each other.
            undergrounds.append(((first - clear, row), "west"))
            undergrounds.append(((last + clear, row), "east"))
        buried = {
            x for run in crossing_runs
            for x in range(run[0] - clear + 1, run[-1] + clear)
        }
        pipes.update((x, row) for x in range(lo, hi + 1) if x not in buried)
    pipes -= {tile for tile, _ in undergrounds}
    # validate_network_purity only compares ADJACENT tiles, so it cannot see two
    # fluids claiming the very same tile. Close that hole here.
    overlap = sorted((pipes | {tile for tile, _ in undergrounds}) & obstacles)
    if overlap:
        raise ValueError(f"Chain link would place pipe on foreign fluid tiles {overlap[:3]}")
    return sorted(pipes), undergrounds


def fluid_chain_link_segments(
    from_point: tuple, to_points: List[tuple], fluid: str, trunk_x: int,
    foreign: List[dict] = (), obstacle_tiles=(),
) -> List[dict]:
    """The link's single purity segment, in world tiles. Buried tiles are left
    out for the same reason generate_fluid_machine_row leaves its underground
    spans out: a tile a pipe merely passes UNDER is not part of the network."""
    pipes, undergrounds = _link_route(from_point, to_points, trunk_x,
                                      _obstacles(list(foreign), fluid) | set(obstacle_tiles))
    return [{"fluid": fluid, "separated_by_pump": False,
             "tiles": pipes + [tile for tile, _ in undergrounds]}]


def generate_fluid_chain_link(
    from_point: tuple, to_points: List[tuple], fluid: str, trunk_x: int,
    foreign: List[dict] = (), obstacle_tiles=(),
) -> dict:
    """Pipe route carrying `fluid` from one producer/source to N consumers.

    The rows built by generate_fluid_machine_row are islands: each carries its
    own headers but nothing joins a producer's output header to the input header
    of the row that consumes it, which is exactly why every refinery and
    chemical plant sat at fluid_ingredient_shortage with empty fluid boxes. This
    is the fluid analogue of LocalLayoutPlanner.generate_chain_link.

    THE ROUTE (deterministic, Z-shaped, one branch per consumer):

        from_point  o------------------+            trunk_x
                                       |
                       consumer 1  o---+
                                       |
                       consumer 2  o---+

      * one horizontal leg west from `from_point` to `trunk_x`,
      * one vertical trunk down `trunk_x` spanning every row involved,
      * one horizontal leg east from the trunk to each consumer.

    `trunk_x` must lie WEST of every attachment: the rows' own scaffolding
    (substation at origin_x-2, energy interface at origin_x-5) and the shared
    roboport anchors sit in the south-west, so the clear water is further west
    still, and one fluid per column keeps two trunks from ever being collinear.

    `foreign` is the purity segments of everything else already on the ground.
    Wherever a leg would run over a foreign fluid the route dives under it with
    a pipe-to-ground pair placed LINK_TUNNEL_CLEARANCE tiles either side --
    live-proven to keep the two segments disjoint -- and the whole emitted set
    is re-checked with validate_network_purity, so a link cannot create a mixing
    hazard even if its columns were chosen badly.
    """
    to_points = [tuple(p) for p in to_points]
    if not to_points:
        raise ValueError("A chain link needs at least one consumer attachment")
    foreign = list(foreign)

    pipes, undergrounds = _link_route(tuple(from_point), to_points, trunk_x,
                                      _obstacles(foreign, fluid) | set(obstacle_tiles))
    actions = [{"action_type": "place_ghost", "entity": "pipe",
                "position": {"x": x + 0.5, "y": y + 0.5}} for x, y in pipes]
    actions += [{"action_type": "place_ghost", "entity": "pipe-to-ground",
                 "position": {"x": x + 0.5, "y": y + 0.5}, "direction": facing}
                for (x, y), facing in undergrounds]

    plan = {"phases": [{"name": f"fluid_link_{fluid}", "actions": actions}]}
    _validate(plan)
    validate_network_purity(
        fluid_chain_link_segments(
            from_point, to_points, fluid, trunk_x, foreign, obstacle_tiles
        ) + foreign
    )
    return plan

def _route_tile(point: tuple) -> tuple[int, int]:
    """Normalize one public grid coordinate without accepting fractional tiles."""
    x, y = point
    if int(x) != x or int(y) != y:
        raise ValueError(f"Fluid routes require integer tile coordinates, got {point!r}")
    return int(x), int(y)


def _route_clearance_tiles(
    occupied: set[tuple[int, int]], clearance: int,
) -> set[tuple[int, int]]:
    """Reserve a fixed square margin around known hard or tunnelable tiles."""
    if clearance < 0:
        raise ValueError("clearance must be non-negative")
    return {
        (x + dx, y + dy)
        for x, y in occupied
        for dx in range(-clearance, clearance + 1)
        for dy in range(-clearance, clearance + 1)
    }


def _bounded_shortest_path(
    starts: set[tuple[int, int]],
    goal: tuple[int, int],
    blocked: set[tuple[int, int]],
    bounds: tuple[int, int, int, int],
    tunnelable: set[tuple[int, int]] = frozenset(),
) -> list[tuple[int, int]]:
    """Deterministic bounded A* path with straight terrain crossings."""
    min_x, max_x, min_y, max_y = bounds
    frontier: list[tuple[int, int, int, int, str, int]] = []
    state_type = tuple[tuple[int, int], str | None, int]
    previous: dict[state_type, state_type | None] = {}
    costs: dict[state_type, int] = {}
    vectors = {"north": (0, -1), "west": (-1, 0),
               "east": (1, 0), "south": (0, 1)}
    headings = {vector: heading for heading, vector in vectors.items()}
    for start in sorted(starts):
        state = (start, None, 0)
        costs[state] = 0
        previous[state] = None
        heuristic = abs(start[0] - goal[0]) + abs(start[1] - goal[1])
        heappush(frontier, (heuristic, 0, *start, "", 0))
    while frontier:
        _, cost, x, y, heading_value, terrain_run = heappop(frontier)
        current = (x, y)
        heading = heading_value or None
        state = (current, heading, terrain_run)
        if cost != costs.get(state):
            continue
        if current == goal:
            path = [current]
            cursor = state
            while previous[cursor] is not None:
                cursor = previous[cursor]
                path.append(cursor[0])
            return list(reversed(path))
        directions = tuple(vectors.values())
        if current in tunnelable and heading is not None:
            directions = (vectors[heading],)
        for dx, dy in directions:
            nxt = x + dx, y + dy
            if not (min_x <= nxt[0] <= max_x and min_y <= nxt[1] <= max_y):
                continue
            if nxt in blocked:
                continue
            if current in tunnelable and (dx, dy) != vectors[heading]:
                continue
            next_heading = heading
            next_run = terrain_run
            if nxt in tunnelable:
                next_heading = next_heading or headings[(dx, dy)]
                next_run += 1
            else:
                next_heading, next_run = None, 0
            next_cost = cost + 1
            successor = (nxt, next_heading, next_run)
            if next_cost >= costs.get(successor, float("inf")):
                continue
            costs[successor] = next_cost
            previous[successor] = state
            heuristic = abs(nxt[0] - goal[0]) + abs(nxt[1] - goal[1])
            heappush(frontier, (
                next_cost + heuristic, next_cost, *nxt,
                next_heading or "", next_run,
            ))
    raise ValueError(f"No clear fluid route to {goal} inside bounded search area")


def _terrain_run_tunnels(
    path: list[tuple[int, int]], start: int, end: int,
) -> tuple[list[tuple[tuple[int, int], tuple[int, int]]], set[tuple[int, int]]]:
    """Bridge one straight water run with legal underground spans and landfill."""
    if start == 0 or end == len(path):
        raise ValueError("A terrain tunnel needs clear land on both sides")
    tunnels: list[tuple[tuple[int, int], tuple[int, int]]] = []
    landfill: set[tuple[int, int]] = set()
    cursor, exit_index = start - 1, end
    while True:
        if exit_index - cursor <= MAX_UNDERGROUND_SPAN:
            next_index = exit_index
        else:
            next_index = cursor + MAX_UNDERGROUND_SPAN
        entry, exit = path[cursor], path[next_index]
        validate_underground_span(entry, exit)
        if entry[0] != exit[0] and entry[1] != exit[1]:
            raise ValueError("A terrain tunnel cannot turn underground")
        tunnels.append((entry, exit))
        if start <= cursor < end:
            landfill.add(entry)
        if start <= next_index < end:
            landfill.add(exit)
        if next_index == exit_index or next_index + 1 == exit_index:
            return tunnels, landfill
        cursor = next_index + 1


def _surface_path_and_tunnels(
    path: list[tuple[int, int]], tunnelable: set[tuple[int, int]],
) -> tuple[
    list[tuple[int, int]],
    list[tuple[tuple[int, int], tuple[int, int]]],
    set[tuple[int, int]],
]:
    """Replace terrain runs with pipe-to-ground pairs and landfill endpoints."""
    surface = {point for point in path if point not in tunnelable}
    tunnels: list[tuple[tuple[int, int], tuple[int, int]]] = []
    landfill: set[tuple[int, int]] = set()
    index = 0
    while index < len(path):
        if path[index] not in tunnelable:
            index += 1
            continue
        start = index
        while index < len(path) and path[index] in tunnelable:
            index += 1
        run_tunnels, run_landfill = _terrain_run_tunnels(path, start, index)
        tunnels.extend(run_tunnels)
        landfill.update(run_landfill)
    surface.update(landfill)
    endpoints = [point for pair in tunnels for point in pair]
    duplicates = sorted({point for point in endpoints if endpoints.count(point) > 1})
    if duplicates:
        raise ValueError(
            "Terrain crossings need two clear surface tiles between spans; "
            f"shared pipe-to-ground endpoint at {duplicates[0]}"
        )
    return sorted(surface), tunnels, landfill

def _tunnel_direction(
    entry: tuple[int, int], exit: tuple[int, int],
) -> tuple[str, str]:
    """Return normal-end facings away from one axis-aligned underground span."""
    if entry[0] < exit[0]:
        return "west", "east"
    if entry[0] > exit[0]:
        return "east", "west"
    if entry[1] < exit[1]:
        return "north", "south"
    if entry[1] > exit[1]:
        return "south", "north"
    raise ValueError("A terrain tunnel needs distinct endpoints")

def _chain_network(
    from_point: tuple,
    to_points: List[tuple],
    hard_tiles,
    tunnelable_tiles,
    clearance: int,
    search_margin: int,
    *,
    allow_tunnels: bool,
) -> tuple[list[tuple[int, int]], list[tuple[tuple[int, int], tuple[int, int]]]]:
    """Join targets to one deterministic network, optionally crossing terrain."""
    source = _route_tile(from_point)
    targets = sorted({_route_tile(point) for point in to_points})
    if not targets:
        raise ValueError("A chain link needs at least one consumer attachment")
    if search_margin < 0:
        raise ValueError("search_margin must be non-negative")
    hard = {_route_tile(point) for point in hard_tiles}
    tunnelable = {_route_tile(point) for point in tunnelable_tiles} - hard
    if source in tunnelable or any(target in tunnelable for target in targets):
        raise ValueError("Fluid endpoints must be on land, not tunnelable terrain")
    network = {source}
    blocked = _route_clearance_tiles(hard, clearance)
    if not allow_tunnels:
        blocked |= tunnelable
    points = [source, *targets]
    bounds = (
        min(x for x, _ in points) - search_margin,
        max(x for x, _ in points) + search_margin,
        min(y for _, y in points) - search_margin,
        max(y for _, y in points) + search_margin,
    )
    remaining = set(targets)
    tunnels: list[tuple[tuple[int, int], tuple[int, int]]] = []
    landfill: set[tuple[int, int]] = set()
    while remaining:
        used_tunnel_ends = {point for pair in tunnels for point in pair}
        candidates = []
        for target in sorted(remaining):
            path = _bounded_shortest_path(
                network, target, blocked - network, bounds,
                tunnelable if allow_tunnels else set(),
            )
            surface, path_tunnels, path_landfill = _surface_path_and_tunnels(
                path, tunnelable if allow_tunnels else set(),
            )
            if used_tunnel_ends & {point for pair in path_tunnels for point in pair}:
                continue
            candidates.append((len(path), target, surface, path_tunnels, path_landfill))
        if not candidates:
            raise ValueError("No non-overlapping fluid tunnel route inside bounded search area")
        _, target, surface, path_tunnels, path_landfill = min(
            candidates, key=lambda candidate: (candidate[0], candidate[1]),
        )
        network.update(surface)
        tunnels.extend(path_tunnels)
        landfill.update(path_landfill)
        remaining.remove(target)
    return sorted(network), sorted(set(tunnels)), sorted(landfill)


def shortest_fluid_chain_tiles(
    from_point: tuple,
    to_points: List[tuple],
    hard_tiles=(),
    tunnelable_tiles=(),
    clearance: int = ROUTE_CLEARANCE,
    search_margin: int = ROUTE_SEARCH_MARGIN,
) -> list[tuple[int, int]]:
    """Return a deterministic surface-only pipe tree.

    ``tunnelable_tiles`` remains conservative here for compatibility: callers
    that need terrain crossings use ``generate_shortest_fluid_chain_link`` or
    ``shortest_fluid_chain_segments``, which emit the verified underground
    endpoints explicitly.
    """
    network, _, _ = _chain_network(
        from_point, to_points, hard_tiles, tunnelable_tiles,
        clearance, search_margin, allow_tunnels=False,
    )
    return network


def _foreign_tunnel_endpoints(
    foreign: List[dict], fluid: str,
) -> set[tuple[int, int]]:
    """Reserve tunnel endpoints already owned by a same-fluid link."""
    return {
        tuple(point) for segment in foreign
        if segment.get("fluid") == fluid
        for pair in segment.get("tunnel_endpoints", ())
        for point in pair
    }


def shortest_fluid_chain_segments(
    from_point: tuple,
    to_points: List[tuple],
    fluid: str,
    foreign: List[dict] = (),
    hard_tiles=(),
    tunnelable_tiles=(),
    clearance: int = ROUTE_CLEARANCE,
    search_margin: int = ROUTE_SEARCH_MARGIN,
    mixing_margin: bool = False,
    allow_terrain_tunnels: bool = False,
) -> List[dict]:
    """Return one purity segment, with narrow terrain tunnels represented."""
    foreign = list(foreign)
    endpoints = {_route_tile(from_point)} | {_route_tile(point) for point in to_points}
    blocked = (
        _mixing_keepout(foreign, fluid, endpoints) if mixing_margin
        else _obstacles(foreign, fluid)
    )
    hard = set(hard_tiles) | blocked | _foreign_tunnel_endpoints(foreign, fluid)
    network, tunnels, _ = _chain_network(
        from_point, to_points, hard, tunnelable_tiles,
        clearance, search_margin, allow_tunnels=allow_terrain_tunnels,
    )
    return [{
        "fluid": fluid,
        "separated_by_pump": False,
        "tiles": network,
        "tunnel_endpoints": tunnels,
    }]


def _route_segment_with_tunnels(
    from_point: tuple,
    to_points: List[tuple],
    fluid: str,
    foreign: List[dict],
    hard_tiles,
    tunnelable_tiles,
    clearance: int,
    search_margin: int,
    mixing_margin: bool,
    allow_terrain_tunnels: bool,
) -> tuple[dict, list[tuple[tuple[int, int], tuple[int, int]]]]:
    """Build the segment and retain the tunnel endpoint pairs for emission."""
    endpoints = {_route_tile(from_point)} | {_route_tile(point) for point in to_points}
    blocked = (
        _mixing_keepout(foreign, fluid, endpoints) if mixing_margin
        else _obstacles(foreign, fluid)
    )
    hard = set(hard_tiles) | blocked | _foreign_tunnel_endpoints(foreign, fluid)
    network, tunnels, landfill = _chain_network(
        from_point, to_points, hard, tunnelable_tiles,
        clearance, search_margin, allow_tunnels=allow_terrain_tunnels,
    )
    return {
        "fluid": fluid, "separated_by_pump": False, "tiles": network,
        "tunnel_endpoints": tunnels,
    }, tunnels, landfill


def generate_shortest_fluid_chain_link(
    from_point: tuple,
    to_points: List[tuple],
    fluid: str,
    foreign: List[dict] = (),
    hard_tiles=(),
    tunnelable_tiles=(),
    clearance: int = ROUTE_CLEARANCE,
    search_margin: int = ROUTE_SEARCH_MARGIN,
    existing_tiles=(),
    mixing_margin: bool = False,
    allow_terrain_tunnels: bool = False,
) -> dict:
    """Return a schema-validated fluid link, tunnelling only narrow terrain."""
    foreign = list(foreign)
    segment, tunnels, landfill = _route_segment_with_tunnels(
        from_point, to_points, fluid, foreign, hard_tiles, tunnelable_tiles,
        clearance, search_margin, mixing_margin, allow_terrain_tunnels,
    )
    existing = {_route_tile(point) for point in existing_tiles}
    # A same-fluid link may already own part of this network. Reusing those
    # tiles is valid, but submitting duplicate ghosts would collide in the
    # non-transactional executor (especially at shared tunnel endpoints).
    existing |= {
        tuple(tile) for segment in foreign
        if segment.get("fluid") == fluid
        for tile in segment.get("tiles", ())
    }
    tunnel_ends = {point for pair in tunnels for point in pair}
    actions = [
        {"action_type": "place_tile_ghost", "tile": "landfill",
         "position": {"x": x, "y": y}}
        for x, y in landfill
    ] + [
        {"action_type": "place_ghost", "entity": "pipe",
         "position": {"x": x + 0.5, "y": y + 0.5}}
        for x, y in segment["tiles"]
        if (x, y) not in existing and (x, y) not in tunnel_ends
    ]
    emitted_tunnel_ends: set[tuple[int, int]] = set()
    for entry, exit in tunnels:
        entry_direction, exit_direction = _tunnel_direction(entry, exit)
        for point, direction in ((entry, entry_direction), (exit, exit_direction)):
            if point in existing or point in emitted_tunnel_ends:
                continue
            emitted_tunnel_ends.add(point)
            actions.append({
                "action_type": "place_ghost", "entity": "pipe-to-ground",
                "position": {"x": point[0] + 0.5, "y": point[1] + 0.5},
                "direction": direction,
            })
    plan = {"phases": [{"name": f"fluid_link_{fluid}", "actions": actions}]}
    _validate(plan)
    validate_network_purity([segment] + foreign)
    return plan
