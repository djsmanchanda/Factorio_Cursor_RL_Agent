# Path: orchestrator/stage_services.py
# Purpose: Execution primitives shared by every stage build -- submitting plans, waiting on bots, and extending power and roboport coverage so a stage is reachable and served.

from __future__ import annotations

import heapq
import math
import time
from collections.abc import Mapping, Sequence
from typing import Callable

from core.science_recipe_graph import validate_current_builder_target
from orchestrator import live_base
from orchestrator.game_bridge import GameBridge, load_json
from orchestrator.parts_mall import MaterialShortage
from orchestrator.placement_clutter import clear_plan_clutter
from orchestrator.roboport_placement import clear_chain_positions
from planners.belt_bridge import _ROUTE_SEARCH_MARGIN
from planners.infrastructure import POLE_SPECS
from planners.infrastructure_geometry import distance, l_route, step_points
from planners.plan_validation import ENTITY_FOOTPRINTS
from planners.sandbox_infrastructure import build_layout_authorization
from tools.rcon_client import RconClient

Point = tuple[float, float]

_DEFAULT_MACHINE_COUNT = 2
_DEFAULT_BELT = "transport-belt"
_DEFAULT_INSERTER = "fast-inserter"
# Statuses that mean the machine itself is fine and is only waiting on supply.
# These are indistinguishable BY STATUS from a genuinely broken feed, so they
# are never judged on status alone -- see _diagnose_machines, which decides on
# observed progress instead.
_HEALTHY_STATUSES = {"working"}
_SUPPLY_WAIT_STATUSES = {
    "no_ingredients", "item_ingredient_shortage", "fluid_ingredient_shortage",
    "waiting_for_source_items", "waiting_for_space_in_destination",
    "no_minable_resources",
}
_STUCK_GRACE_SECONDS = 20.0
# How long a bot-built entity gets to actually exist before it is called missing.
_GHOST_BUILD_SECONDS = 60.0
# Live-verified this session: roboport construction radius 55, link (chain)
# radius conservatively 46 (hard game limit ~50).
_ROBOPORT_CONSTRUCTION_RADIUS = 55.0
_ROBOPORT_LINK_DISTANCE = 46.0
# Live-verified on 2.0.77 (prototypes.entity['roboport'].logistic_radius): a
# roboport serves LOGISTIC chests only within 25 tiles -- under half its
# construction reach. Coverage checked against construction radius alone
# therefore passes while the chests it just built sit in no network at all: a
# passive provider supplies nothing, a requester never fills, and the only
# symptom is a downstream stage stuck at item_ingredient_shortage.
_ROBOPORT_LOGISTIC_RADIUS = 25.0
# (radius, service area is a square) per coverage purpose. The logistic supply
# area is genuinely square -- live-probed with
# `roboport.logistic_cell.is_in_logistic_range`: for the roboport at (3,-1),
# (28,-1) is in range and (28.5,-1) is not, while the far corner (27.9,23.9)
# is still in. So Chebyshev distance is the exact test. Construction coverage
# keeps the Euclidean measure it was live-verified with; Euclidean >=
# Chebyshev, so it can only ever add a roboport that was not strictly needed,
# never skip one that was.
_ROBOPORT_SERVICE_AREAS = {
    "construction": (_ROBOPORT_CONSTRUCTION_RADIUS, False),
    "logistic": (_ROBOPORT_LOGISTIC_RADIUS, True),
}
# Slack subtracted from a chain's final hop so tile rounding can never land it
# a fraction outside the service radius it was placed to satisfy.
_COVERAGE_MARGIN = 2.0
# Avoid laying the same emergency power bridge repeatedly while a newly
# connected roboport is still charging and reports low_power.
_REPAIRED_ROBOPORT_POWER: set[Point] = set()
# Every chest type that is inert unless it is inside a logistic supply area.
_LOGISTIC_CHEST_ENTITIES = frozenset({
    "active-provider-chest", "buffer-chest", "passive-provider-chest",
    "requester-chest", "storage-chest",
})
# How far from a stage's machines its own collection chest can be. A stage's
# chest sits at the end of its machine row; a container farther away than a
# whole stage footprint belongs to something else and is not ours to cover.
_STAGE_CHEST_REACH = 30.0
# How far outside the source->destination box to survey obstacles, so a route
# has room to detour around something sitting right on the straight path.
#
# This must cover at least as much ground as the detour router is allowed to
# SEARCH. At half the search margin, a detour could leave the surveyed box and
# emit belt onto tiles never checked for occupancy -- an iron-ore bridge did
# exactly that, laying transport-belt over the mine's own fast-transport-belt
# at x=15.5..17.5, and the ore never reached the furnaces.
_BRIDGE_SURVEY_MARGIN = max(24.0, _ROUTE_SEARCH_MARGIN)
# Blockage remediation: bounded rounds of (wait -> diagnose -> fix -> recheck).
# One round is long enough for bots to make visible progress, and the round
# count bounds how long a genuinely unfixable stage can spin.
_BLOCKAGE_INTERVAL = 30.0
_BLOCKAGE_ROUNDS = 6
# Multiplier on computed belt transit time before judging a fed stage
# unhealthy: the first item has to cross, then the inserter has to load it.
_TRANSIT_SAFETY = 1.5
# How many of an ingredient a stage's requester chest asks for. Two full
# assembler input stacks' worth: enough to ride out bot round-trip latency
# without hoarding a scarce item in one chest.
_LOGISTIC_REQUEST = 100
# Ingredient demand (items/s) above which a link must be a belt rather than
# logistic bots. Bots deliver roughly cargo-size per round trip, so their
# throughput falls off with distance and is capped by the bot population; a
# belt is constant-throughput at any length (yellow alone is 15 items/s).
# Below this a requester chest is far cheaper -- one chest instead of a ~50
# belt corridor -- which matters while belts are a scarce consumable. This is
# a deliberate policy threshold, not a measured constant; raise it if bots are
# plentiful, lower it once belt production is established.
_BOT_THROUGHPUT_LIMIT = 3.0


class StuckError(RuntimeError):
    """The builder cannot proceed and needs a human decision -- never guessed silently."""


def validate_builder_target(
    target: str, surface: str, recipes: Mapping[str, Mapping],
) -> None:
    """Convert symbolic target rejection into the builder's fail-closed error."""
    try:
        validate_current_builder_target(target, surface, recipes)
    except ValueError as error:
        raise StuckError(str(error)) from error


def _ghost_materials(plan: dict) -> dict[str, int]:
    """Items construction bots must consume to revive this plan's ghosts.
    place_entity actions are created directly and cost nothing."""
    required: dict[str, int] = {}
    for phase in plan["phases"]:
        for action in phase["actions"]:
            if action.get("action_type") == "place_ghost":
                required[action["entity"]] = required.get(action["entity"], 0) + 1
            elif action.get("action_type") == "place_tile_ghost":
                required[action["tile"]] = required.get(action["tile"], 0) + 1
    return required


def assert_affordable(
    client: RconClient, surface: str, force: str, plan: dict, name: str,
    emit: Callable[[str], None],
) -> None:
    """Refuse to place ghosts the base cannot pay for.

    Without this the plan places fine and then stalls invisibly: the ghosts
    exist, the bots have nothing to build them with, and the only symptom is a
    stage that never comes up. Naming the exact shortfall turns that into an
    actionable message -- and, once the builder can produce its own belts, into
    a decision about what to make next.
    """
    required = _ghost_materials(plan)
    if not required:
        return
    stock = live_base.available_items(client, surface, force)
    short = {
        item: count - stock.get(item, 0)
        for item, count in required.items()
        if stock.get(item, 0) < count
    }
    if short:
        raise MaterialShortage(
            name,
            {item: required[item] for item in short},
            stock,
        )
    emit(f"  {name}: material check ok ({sum(required.values())} ghost items in stock)")


def _submit(
    client: RconClient, bridge: GameBridge, surface: str, plan: dict, name: str,
    emit: Callable[[str], None], *, max_retries: int = 2,
) -> dict:
    """Submit a plan; if a tile is blocked, clear it ONLY when it's obviously
    safe map clutter (a tree, a rock -- never anything a force built) and
    retry. A collision with anything else means this exact placement is
    genuinely occupied -- raise so the caller picks a different spot instead
    of bulldozing real infrastructure."""
    clear_plan_clutter(client, surface, plan, emit)
    assert_affordable(client, surface, plan.get("force", "player"), plan, name, emit)
    authorization = build_layout_authorization([(name, plan)])
    for attempt in range(max_retries + 1):
        report = load_json(bridge.build_layout(authorization, plan))
        if report.get("ok"):
            emit(f"{name}: placed {report['succeeded_placements']} actions "
                 f"({report['placed_ghosts']} ghosts, {report['placed_entities']} entities)")
            return report
        cleared_any = False
        blocking = []
        for failure in report.get("placement_failures", []):
            if failure.get("reason") != "exact_position_occupied_by_different_entity":
                blocking.append(failure)
                continue
            position = (failure["position"]["x"], failure["position"]["y"])
            occupant = live_base.entity_at(client, surface, position)
            if occupant and live_base.is_safe_to_clear(occupant):
                emit(f"  clearing {occupant['name']} at {position} (map clutter) to make room")
                live_base.remove_entity_at(client, surface, position)
                cleared_any = True
            else:
                blocking.append({**failure, "occupant": occupant})
        if blocking:
            raise StuckError(
                f"{name}: blocked by real infrastructure, not map clutter -- needs a "
                f"different placement (go around, not through): {blocking[:3]}"
            )
        if not cleared_any:
            raise StuckError(f"{name}: {report['failed_placements']} placement(s) failed: "
                              f"{report['placement_failures']}")
        emit(f"  retrying {name} after clearing obstruction(s) ({attempt + 1}/{max_retries})")
    raise StuckError(f"{name}: still blocked after {max_retries} clutter-clearing retries")


def _wait_for_ghosts(client: RconClient, surface: str, force: str, area: tuple[Point, Point],
                      *, timeout_seconds: float = 180.0, poll_seconds: float = 1.0,
                      stall_seconds: float = 8.0,
                      minimum_wait_seconds: float = 0.0,
                      include_entity_ghosts: bool = True,
                      include_tile_ghosts: bool = True) -> int:
    """Entity and tile ghosts remaining, returning as soon as progress STOPS
    rather than when a long timeout expires.

    Bots build continuously while they can; once the count stops falling, more
    waiting changes nothing and the real answer is a diagnosis (no coverage, no
    power, no material). Burning the full timeout first is what made the
    builder feel like it took minutes to notice an obvious problem.
    """
    min_point, max_point = area
    entity_count = (
        "s.count_entities_filtered{name='entity-ghost',force='" + force + "',area=area}"
        if include_entity_ghosts else "0"
    )
    tile_count = (
        "s.count_entities_filtered{name='tile-ghost',force='" + force + "',area=area}"
        if include_tile_ghosts else "0"
    )
    lua = (
        "local s=game.surfaces['" + surface + "'];local area={{"
        + str(min_point[0]) + "," + str(min_point[1]) + "},{"
        + str(max_point[0]) + "," + str(max_point[1]) + "}};rcon.print("
        + entity_count + "+" + tile_count + ")"
    )
    deadline = time.monotonic() + timeout_seconds
    remaining = int(client.command("/sc " + lua).strip())
    started = time.monotonic()
    last_change = time.monotonic()
    while remaining and time.monotonic() < deadline:
        time.sleep(poll_seconds)
        current = int(client.command("/sc " + lua).strip())
        if current != remaining:
            remaining, last_change = current, time.monotonic()
        elif (
            time.monotonic() - started >= minimum_wait_seconds
            and time.monotonic() - last_change >= stall_seconds
        ):
            return remaining
    return remaining


def _diagnose_machines(
    client: RconClient, surface: str, positions: list[Point], emit: Callable[[str], None],
    *, grace_seconds: float = _STUCK_GRACE_SECONDS, poll_seconds: float = 2.0,
    bridge: GameBridge | None = None, force: str = "player",
) -> list[tuple[Point, str]]:
    """Poll each machine until every one is demonstrably alive or `grace_seconds`
    elapses. Returns the ones still stuck, each with its actual reason.

    A machine counts as alive when its status is healthy OR its progress counter
    has moved since the first sample. The second test is what makes this
    independent of how long items take to arrive: a furnace fed down a long
    yellow belt spends most of its life flickering between `working` and
    `no_ingredients`, and sampling status at one arbitrary instant used to call
    that stuck even after it had produced dozens of plates. Waiting a fixed 20s
    plus belt transit only ever approximated the answer -- and could not
    approximate it at all once inserter swings, craft time and multi-ingredient
    recipes stacked up behind the belt. Observed progress answers it directly.

    There is exactly ONE failure condition: the line produces nothing. Anything
    less than that is a rate observation, not a fault.

    A line is routinely built with more machines than its upstream can saturate
    -- three furnaces on two drills runs at ~70% -- so some machine is idle at
    any instant, permanently and by design. Under-utilisation is a signal about
    where capacity should grow, not a reason to stop: no grace period can
    outlast a supply deficit, so failing on it is wrong at every timeout. Broken
    machines are still named in the log while the line runs, because they are
    real work to do, but they do not halt a mission that is producing.

    Given a `bridge`, an unpowered machine is REPAIRED rather than reported: a
    machine with no pole covering it gets a pole chain run out to it, which is
    the whole of that fault's fix. Repairing beats reporting because a defect
    left in place keeps costing throughput for the rest of the run.
    """
    deadline = time.monotonic() + grace_seconds
    baseline = live_base.progress_counters(client, surface, positions)
    progressed: set[Point] = set()
    stuck: list[tuple[Point, str]] = []
    repaired: set[Point] = set()
    producing = False
    while True:
        statuses, counters = live_base.machine_health(client, surface, positions)
        if bridge is not None:
            for position in positions:
                key = tuple(position)
                if statuses.get(key) != "no_power" or key in repaired:
                    continue
                repaired.add(key)
                emit(f"  REPAIR: machine at {position} has no power -- running a pole to it")
                if not extend_power(client, bridge, surface, force, position, emit):
                    emit(f"  REPAIR FAILED: {position} cannot be reached by a pole chain "
                         "from any generating network")
        for key, value in counters.items():
            if key in baseline and value != baseline[key]:
                progressed.add(key)
        producing = bool(progressed) or any(
            statuses.get(tuple(position)) in _HEALTHY_STATUSES
            for position in positions
        )
        stuck = [
            (position, statuses.get(tuple(position), "missing"))
            for position in positions
            if statuses.get(tuple(position)) not in _HEALTHY_STATUSES
            and tuple(position) not in progressed
        ]
        if producing or not stuck or time.monotonic() >= deadline:
            break
        time.sleep(poll_seconds)

    if producing:
        idle = [p for p, s in stuck if s in _SUPPLY_WAIT_STATUSES]
        broken = [(p, s) for p, s in stuck if s not in _SUPPLY_WAIT_STATUSES]
        if idle:
            emit(f"  CAPACITY: {len(idle)}/{len(positions)} machine(s) idle waiting on supply "
                 "while the line produces -- raise upstream output or intake throughput to use "
                 "them; the line is healthy either way")
        for position, status in broken:
            emit(f"  DEFECT: machine at {position} is {status} while the line produces -- "
                 "worth repairing, not worth stopping for")
        return []

    for position, status in stuck:
        if status in _SUPPLY_WAIT_STATUSES:
            emit(f"  STUCK: machine at {position} is {status} and the line produced nothing "
                 f"at all in {grace_seconds:.0f}s -- its feed never delivered")
        else:
            emit(f"  STUCK: machine at {position} is {status}, not working")
    return stuck


def _await_built_status(
    client: RconClient, surface: str, position: Point,
    *, timeout_seconds: float = _GHOST_BUILD_SECONDS, poll_seconds: float = 2.0,
) -> str | None:
    """The entity's status once it exists, or None if it never got built.

    A ghost has no status, so polling has to distinguish "not there yet" from
    "not going to be there" by waiting rather than by reading once.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        status = live_base.entity_status_name(client, surface, position)
        if status is not None or time.monotonic() >= deadline:
            return status
        time.sleep(poll_seconds)


def _power_bridge_hops(
    start: Point, end: Point, spacing: float, blocked: set[tuple[int, int]],
) -> list[Point]:
    """Shortest deterministic pole chain whose pole footprints avoid blockers."""
    horizontal = l_route(start, end)
    vertical = [start, (start[0], end[1]), end]
    routes = [horizontal, vertical]
    for offset in (-128, -64, -32, -16, 16, 32, 64, 128):
        routes.extend((
            [start, (start[0], start[1] + offset),
             (end[0], start[1] + offset), end],
            [start, (start[0] + offset, start[1]),
             (start[0] + offset, end[1]), end],
        ))

    candidates = []
    for route in routes:
        route = [point for index, point in enumerate(route)
                 if index == 0 or point != route[index - 1]]
        hops: list[Point] = []
        for leg_start, leg_end in zip(route, route[1:]):
            hops.extend(step_points(leg_start, leg_end, spacing))
        # step_points ROUNDS the endpoint it appends, so a destination on a .5
        # coordinate never compared equal to `end` and was never dropped. The
        # chain then put a pole on the very entity it was routing to, that tile
        # counted as a collision, and every candidate route was rejected with
        # "no unobstructed power route is available" -- while the real route was
        # perfectly clear.
        if hops and hops[-1] == (round(end[0]), round(end[1])):
            hops.pop()
        hops = list(dict.fromkeys(hops))
        collisions = sum((math.floor(x), math.floor(y)) in blocked for x, y in hops)
        length = sum(distance(a, b) for a, b in zip(route, route[1:]))
        candidates.append((collisions, length, tuple(route), hops))
    collisions, _length, _route, hops = min(candidates, key=lambda item: item[:3])
    if collisions:
        # The fixed menu of L-routes and offsets above is fast and deterministic,
        # and it is enough to dodge buildings. It is not enough to dodge
        # TERRAIN: a coastline is not a rectangle, so every candidate can clip
        # water while a perfectly good winding route exists. Fall back to a real
        # search rather than declaring the gap unroutable.
        return _searched_bridge_hops(start, end, spacing, blocked)
    return hops


def _searched_bridge_hops(
    start: Point, end: Point, spacing: float, blocked: set[tuple[int, int]],
) -> list[Point]:
    """A* over free tiles for a pole chain from `start` to within reach of `end`.

    Hop lengths are capped at `spacing` in every direction -- diagonals use a
    shorter offset so their true distance stays inside wire reach rather than
    overshooting it by root two, which would produce a chain that looks
    connected and is not.
    """
    cardinal = max(2, int(spacing) - 1)
    diagonal = max(1, int((spacing - 1) / 1.415))
    steps = [
        (cardinal, 0), (-cardinal, 0), (0, cardinal), (0, -cardinal),
        (diagonal, diagonal), (diagonal, -diagonal),
        (-diagonal, diagonal), (-diagonal, -diagonal),
    ]
    margin = 96
    min_x, max_x = min(start[0], end[0]) - margin, max(start[0], end[0]) + margin
    min_y, max_y = min(start[1], end[1]) - margin, max(start[1], end[1]) + margin

    origin = (round(start[0]), round(start[1]))
    goal = (round(end[0]), round(end[1]))
    frontier: list[tuple[float, tuple[int, int]]] = [(0.0, origin)]
    came: dict[tuple[int, int], tuple[int, int] | None] = {origin: None}
    spent: dict[tuple[int, int], float] = {origin: 0.0}
    while frontier:
        _, node = heapq.heappop(frontier)
        if distance(node, end) <= spacing:
            chain: list[Point] = []
            cursor: tuple[int, int] | None = node
            while cursor is not None:
                chain.append((float(cursor[0]), float(cursor[1])))
                cursor = came[cursor]
            chain.reverse()
            return [hop for hop in chain[1:] if hop != (float(goal[0]), float(goal[1]))]
        for dx, dy in steps:
            nxt = (node[0] + dx, node[1] + dy)
            if not (min_x <= nxt[0] <= max_x and min_y <= nxt[1] <= max_y):
                continue
            if nxt in blocked:
                continue
            moved = spent[node] + distance(node, nxt)
            if nxt not in spent or moved < spent[nxt]:
                spent[nxt] = moved
                came[nxt] = node
                heapq.heappush(frontier, (moved + distance(nxt, end), nxt))
    raise ValueError("no unobstructed power route is available")


def _hookup_pole_position(
    client: RconClient, surface: str, consumer: Point,
    blocked: set[tuple[int, int]], toward: Point,
) -> Point | None:
    """Where to park the pole that will actually SUPPLY an unwired consumer.

    A pole chain that merely arrives near a consumer does not power it: the
    consumer has to sit inside a pole's supply area. Chain hops land up to a
    full spacing apart, so the last one can stop well outside that area -- which
    is how a bridged roboport ends up built, connected and still dead.
    """
    entity = live_base.entity_at(client, surface, consumer)
    footprint = ENTITY_FOOTPRINTS.get(entity["name"], 1) if entity else 1
    reach = POLE_SPECS["medium-electric-pole"]["supply"] + footprint / 2
    span = math.ceil(reach)
    candidates = [
        spot
        for tile_x in range(math.floor(consumer[0] - span), math.ceil(consumer[0] + span))
        for tile_y in range(math.floor(consumer[1] - span), math.ceil(consumer[1] + span))
        for spot in [(tile_x + 0.5, tile_y + 0.5)]
        if max(abs(spot[0] - consumer[0]), abs(spot[1] - consumer[1])) < reach
        and (math.floor(spot[0]), math.floor(spot[1])) not in blocked
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda spot: distance(spot, toward))


def extend_power(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    near_position: Point, emit: Callable[[str], None],
) -> bool:
    """Connect `near_position` to a network that actually generates power.

    Handles both shapes of power fault. If a pole stands at `near_position` its
    network is bridged to a powered one; if NO pole stands there -- a roboport
    or machine that was never wired at all -- a chain is run out and terminated
    on a pole whose supply area covers it. Returns True if anything was built,
    False when there is nothing this can do (no powered network exists, or
    `near_position` is already on one).
    """
    own_network = live_base.pole_network_id(client, surface, near_position)
    target = live_base.nearest_powered_pole(
        client, surface, force, near_position, exclude_network_id=own_network,
    )
    if target is None:
        return False
    target_position, target_name = target
    target_supply = POLE_SPECS.get(
        target_name, POLE_SPECS["medium-electric-pole"],
    )["supply"]
    consumer = live_base.entity_at(client, surface, near_position)
    consumer_size = ENTITY_FOOTPRINTS.get(consumer["name"], 1) if consumer else 1
    if (
        own_network is None
        and max(
            abs(target_position[0] - near_position[0]),
            abs(target_position[1] - near_position[1]),
        ) < target_supply + consumer_size / 2
    ):
        emit(f"  {near_position} is already inside the supply area of the powered "
             f"{target_name} at {target_position}; waiting for it to charge")
        return True
    if own_network is None:
        emit(f"  power gap found: {near_position} is not wired to any pole -- running a "
             f"chain from {target_name} at {target_position} and terminating it in supply range")
    else:
        emit(f"  power gap found: network {own_network} at {near_position} carries no "
             f"generation -- bridging to {target_name} at {target_position}")
    # The first hop is limited by the shorter endpoint reach (small poles
    # reach only 7.5 tiles); later medium-pole hops inherit that safe spacing.
    target_wire = POLE_SPECS.get(target_name, POLE_SPECS["medium-electric-pole"])["wire"]
    spacing = min(POLE_SPECS["medium-electric-pole"]["wire"], target_wire) - 1
    margin = 132.0
    blocked = live_base.occupied_tiles(
        client, surface,
        (min(target_position[0], near_position[0]) - margin,
         min(target_position[1], near_position[1]) - margin),
        (max(target_position[0], near_position[0]) + margin,
         max(target_position[1], near_position[1]) + margin),
    )
    hookup_blocked = set(blocked)
    blocked -= {(math.floor(target_position[0]), math.floor(target_position[1]))}
    if own_network is None:
        # The consumer's own body is an obstacle, not an endpoint: the chain has
        # to stop on a free tile whose supply area covers it.
        endpoint = _hookup_pole_position(
            client, surface, near_position, hookup_blocked, target_position,
        )
        if endpoint is None:
            raise StuckError(
                f"nothing at {near_position} can be powered: every tile within a medium "
                "pole's supply area of it is occupied"
            )
    else:
        endpoint = near_position
        blocked -= {(math.floor(near_position[0]), math.floor(near_position[1]))}
    try:
        hops = _power_bridge_hops(target_position, endpoint, spacing, blocked)
    except ValueError as error:
        raise StuckError(f"power gap cannot be routed safely: {error}") from error
    if own_network is None:
        hops = [*hops, endpoint]
    if not hops:
        raise StuckError(f"power gap between {near_position} and {target_position} but no room "
                          "for a bridging pole -- they may already be in reach; investigate directly")
    actions = [
        {"action_type": "place_entity", "entity": "medium-electric-pole", "position": {"x": x, "y": y}}
        for x, y in hops
    ]
    plan = {"phases": [{"name": "power_bridge", "actions": actions}], "surface": surface, "force": force}
    _submit(client, bridge, surface, plan, "power_bridge", emit)
    return True


def service_distance(roboport: Point, target: Point, *, square: bool) -> float:
    """Distance measured in the metric that matches the service area's SHAPE.

    A roboport's areas are squares centred on it, so Chebyshev is the exact
    test; Euclidean is a strictly conservative approximation of it. See
    _ROBOPORT_SERVICE_AREAS for which purpose uses which and why.
    """
    if square:
        return max(abs(roboport[0] - target[0]), abs(roboport[1] - target[1]))
    return math.dist(roboport, target)


def roboport_chain(source: Point, target: Point, radius: float) -> list[Point]:
    """Roboport positions from `source` (an existing roboport) that end with
    `target` inside `radius`, each hop within link distance of the previous.

    The chain deliberately stops `radius` short of `target` instead of walking
    onto it: a logistic chest IS the target, and the coverage that must reach
    it is served just as well from the near edge of the supply area. Distances
    along the chain are Euclidean, which is >= the Chebyshev distance the
    square service area actually uses -- so a chain that satisfies this
    satisfies the real area too.
    """
    total = math.dist(source, target)
    if total == 0 or total <= radius:
        return []
    reach = total - radius + _COVERAGE_MARGIN
    step = _ROBOPORT_LINK_DISTANCE - 4  # slack so a rounded tile never lands on the link cliff edge
    unit = ((target[0] - source[0]) / total, (target[1] - source[1]) / total)
    return [
        (float(round(source[0] + unit[0] * travel)), float(round(source[1] + unit[1] * travel)))
        for travel in (min(step * hop, reach) for hop in range(1, math.ceil(reach / step) + 1))
    ]


def _repair_existing_roboport_power(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    emit: Callable[[str], None],
) -> None:
    """Repair powerless ports left by an earlier interrupted runner."""
    for position, status in live_base.roboports_needing_power(
        client, surface, force
    ):
        if position in _REPAIRED_ROBOPORT_POWER:
            continue
        emit(f"  existing roboport at {position} is {status} -- connecting it")
        if not extend_power(client, bridge, surface, force, position, emit):
            raise StuckError(
                f"existing roboport at {position} cannot be powered"
            )
        _REPAIRED_ROBOPORT_POWER.add(position)

def extend_roboport_coverage(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    target_position: Point, emit: Callable[[str], None], *,
    purpose: str = "construction",
) -> bool:
    """If `target_position` is beyond every existing roboport's service area
    for `purpose` ("construction" for ghosts, "logistic" for chests), chain new
    roboports out to it (each within link distance of the previous one). Returns
    True if roboports were added (caller should re-check ghost completion),
    False if coverage was already fine."""
    if client is not None:
        _repair_existing_roboport_power(
            client, bridge, surface, force, emit
        )
    radius, square = _ROBOPORT_SERVICE_AREAS[purpose]
    nearest = live_base.nearest_roboport(client, surface, force, target_position)
    if nearest is None:
        return False
    gap = service_distance(nearest, target_position, square=square)
    if gap <= radius:
        return False
    emit(f"  roboport {purpose} coverage gap: nearest roboport {nearest} is "
         f"{gap:.0f} tiles from {target_position} "
         f"({purpose} radius is {radius:.0f}) -- chaining roboports out")
    ideals = roboport_chain(nearest, target_position, radius)
    try:
        placed = clear_chain_positions(
            client, surface, nearest, target_position, ideals,
            service_radius=radius, service_square=square,
            link_distance=_ROBOPORT_LINK_DISTANCE,
        )
    except ValueError as error:
        raise StuckError(
            f"roboport chain cannot avoid existing infrastructure: {error}"
        ) from error
    if not placed:
        raise StuckError(
            f"{target_position} is outside {purpose} coverage of the roboport at "
            f"{nearest} but no chain position could be derived; investigate directly"
        )
    actions = [
        {"action_type": "place_entity", "entity": "roboport", "position": {"x": x, "y": y}}
        for x, y in placed
    ]
    plan = {"phases": [{"name": "roboport_bridge", "actions": actions}], "surface": surface, "force": force}
    _submit(client, bridge, surface, plan, "roboport_bridge", emit)
    # A roboport with no power provides NO coverage of either kind, so chaining
    # one out without connecting it just moves the stall. Observed live: a
    # bridged roboport sat at no_power and its ghosts never built.
    #
    # The status has to be read AFTER the roboport actually exists. Checking
    # straight after submitting the plan read the status of a ghost, which is
    # None rather than "no_power", so the guard passed vacuously and left an
    # unpowered roboport serving nothing.
    for position in placed:
        status = _await_built_status(client, surface, position)
        if status is None:
            raise StuckError(
                f"bridged roboport at {position} was never built; it would provide no "
                f"{purpose} coverage"
            )
        if status in {"no_power", "low_power"}:
            emit(f"  bridged roboport at {position} is {status} -- connecting it")
            if not extend_power(client, bridge, surface, force, position, emit):
                raise StuckError(
                    f"roboport at {position} cannot be powered; it would provide no "
                    f"{purpose} coverage"
                )
            _REPAIRED_ROBOPORT_POWER.add(position)
    return True


def _logistic_chest_positions(plan: dict) -> list[Point]:
    """Every coloured logistic chest a plan places -- its requester feed chests
    and its passive-provider output chest.

    These are known at plan time, so their coverage can be guaranteed before
    the stage is ever expected to work rather than diagnosed after it silently
    fails to.
    """
    return [
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") in _LOGISTIC_CHEST_ENTITIES
    ]


def ensure_logistic_coverage(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    chest_positions: Sequence[Point], emit: Callable[[str], None],
) -> bool:
    """Put every one of `chest_positions` inside a roboport's LOGISTIC supply
    area, chaining roboports out where it isn't. Returns True if anything was
    added.

    Construction coverage is not enough and never was: it reaches 55 tiles
    while the supply area reaches 25, so a chest can be built perfectly and
    still belong to no network. Each chest is handled in turn against a
    re-queried nearest roboport, so a port placed for one chest is credited to
    the next instead of being duplicated.
    """
    added = False
    for position in sorted({tuple(p) for p in chest_positions}):
        added |= extend_roboport_coverage(
            client, bridge, surface, force, position, emit, purpose="logistic",
        )
    return added
