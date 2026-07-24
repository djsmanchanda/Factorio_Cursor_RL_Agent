# Path: planners/fluid_routing.py
# Purpose: Deterministic purity-safe routing between generated fluid stage headers.

from __future__ import annotations

import json
from heapq import heappop, heappush
from pathlib import Path
from typing import List

from jsonschema import Draft7Validator

from core.fluid_systems import validate_network_purity, validate_underground_span
from planners.local_layout_planner import _reject_fuel_entities

LINK_TUNNEL_CLEARANCE = 2
ROUTE_CLEARANCE = 1
ROUTE_SEARCH_MARGIN = 24
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
) -> list[tuple[int, int]]:
    """Deterministic bounded A* path from any existing network tile to ``goal``."""
    min_x, max_x, min_y, max_y = bounds
    frontier: list[tuple[int, int, int, int]] = []
    previous: dict[tuple[int, int], tuple[int, int] | None] = {}
    costs: dict[tuple[int, int], int] = {}
    for start in sorted(starts):
        costs[start] = 0
        previous[start] = None
        heuristic = abs(start[0] - goal[0]) + abs(start[1] - goal[1])
        heappush(frontier, (heuristic, 0, *start))
    while frontier:
        _, cost, x, y = heappop(frontier)
        current = (x, y)
        if cost != costs.get(current):
            continue
        if current == goal:
            path = [current]
            while previous[path[-1]] is not None:
                path.append(previous[path[-1]])
            return list(reversed(path))
        for dx, dy in ((0, -1), (-1, 0), (1, 0), (0, 1)):
            nxt = x + dx, y + dy
            if not (min_x <= nxt[0] <= max_x and min_y <= nxt[1] <= max_y):
                continue
            if nxt in blocked:
                continue
            next_cost = cost + 1
            if next_cost >= costs.get(nxt, float("inf")):
                continue
            costs[nxt] = next_cost
            previous[nxt] = current
            heuristic = abs(nxt[0] - goal[0]) + abs(nxt[1] - goal[1])
            heappush(frontier, (next_cost + heuristic, next_cost, *nxt))
    raise ValueError(f"No clear fluid route to {goal} inside bounded search area")


def shortest_fluid_chain_tiles(
    from_point: tuple,
    to_points: List[tuple],
    hard_tiles=(),
    tunnelable_tiles=(),
    clearance: int = ROUTE_CLEARANCE,
    search_margin: int = ROUTE_SEARCH_MARGIN,
) -> list[tuple[int, int]]:
    """Return a deterministic, compact pipe tree with a fixed expansion margin.

    ``hard_tiles`` are entities or terrain that a pipe may never occupy.
    ``tunnelable_tiles`` are occupied routes that may support an underground
    crossing in a later emission phase.  This conservative first phase avoids
    both classes on the surface, rather than silently placing a crossing whose
    endpoints have not been generated.  Every target joins the existing tree
    with bounded A*, so the result is computed from inputs, not a fixed trunk.
    """
    source = _route_tile(from_point)
    targets = sorted({_route_tile(point) for point in to_points})
    if not targets:
        raise ValueError("A chain link needs at least one consumer attachment")
    if search_margin < 0:
        raise ValueError("search_margin must be non-negative")
    hard = {_route_tile(point) for point in hard_tiles}
    tunnelable = {_route_tile(point) for point in tunnelable_tiles}
    # A fixed belt may sit on an infrastructure footprint in the abstract
    # planner model. Hard terrain/entities take precedence there.
    tunnelable -= hard
    network = {source}
    # Structures and terrain reserve expansion clearance. Belts reserve their
    # actual tiles only: widening every belt into a wall can close a valid
    # factory corridor before an underground crossing policy is available.
    blocked = (_route_clearance_tiles(hard, clearance) | tunnelable) - {source, *targets}
    points = [source, *targets]
    bounds = (
        min(x for x, _ in points) - search_margin,
        max(x for x, _ in points) + search_margin,
        min(y for _, y in points) - search_margin,
        max(y for _, y in points) + search_margin,
    )
    remaining = set(targets)
    while remaining:
        candidates = [
            (_bounded_shortest_path(network, target, blocked - network, bounds), target)
            for target in remaining
        ]
        path, target = min(candidates, key=lambda candidate: (len(candidate[0]), candidate[1]))
        network.update(path)
        remaining.remove(target)
    return sorted(network)


def shortest_fluid_chain_segments(
    from_point: tuple,
    to_points: List[tuple],
    fluid: str,
    foreign: List[dict] = (),
    hard_tiles=(),
    tunnelable_tiles=(),
    clearance: int = ROUTE_CLEARANCE,
    search_margin: int = ROUTE_SEARCH_MARGIN,
) -> List[dict]:
    """Return one purity segment generated by ``shortest_fluid_chain_tiles``."""
    foreign = list(foreign)
    return [{
        "fluid": fluid,
        "separated_by_pump": False,
        "tiles": shortest_fluid_chain_tiles(
            from_point,
            to_points,
            set(hard_tiles) | _obstacles(foreign, fluid),
            tunnelable_tiles,
            clearance,
            search_margin,
        ),
    }]


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
) -> dict:
    """Return a schema-validated plan for the automatic fluid-chain router."""
    foreign = list(foreign)
    segments = shortest_fluid_chain_segments(
        from_point, to_points, fluid, foreign, hard_tiles, tunnelable_tiles,
        clearance, search_margin,
    )
    existing = {_route_tile(point) for point in existing_tiles}
    actions = [
        {"action_type": "place_ghost", "entity": "pipe",
         "position": {"x": x + 0.5, "y": y + 0.5}}
        for x, y in segments[0]["tiles"] if (x, y) not in existing
    ]
    plan = {"phases": [{"name": f"fluid_link_{fluid}", "actions": actions}]}
    _validate(plan)
    validate_network_purity(segments + foreign)
    return plan
