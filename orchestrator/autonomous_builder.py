# Path: orchestrator/autonomous_builder.py
# Purpose: Goal-driven autonomous factory expansion on a real base -- given a target item, recursively ensures every ingredient in its recipe chain has a real, working production stage, deciding placement, connections, and troubleshooting itself.

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Callable

from orchestrator import live_base
from orchestrator.game_bridge import GameBridge, load_json
from planners.belt_bridge import (
    DIRECTION_VECTORS, UNDERGROUND_REACH, bridge_chest_to_chest, opposite,
    transit_seconds,
)
from planners.infrastructure import POLE_SPECS, strip_local_power
from planners.infrastructure_geometry import step_points
from planners.local_layout_planner import LocalLayoutPlanner
from planners.recipe_data import LINE_RECIPES, MACHINE_SPEEDS
from planners.sandbox_infrastructure import build_layout_authorization
from tools.rcon_client import RconClient

Point = tuple[float, float]

_DEFAULT_MACHINE_COUNT = 2
_DEFAULT_BELT = "fast-transport-belt"
_DEFAULT_INSERTER = "fast-inserter"
_HEALTHY_STATUSES = {"working"}
_STUCK_GRACE_SECONDS = 20.0
# Live-verified this session: roboport construction radius 55, link (chain)
# radius conservatively 46 (hard game limit ~50).
_ROBOPORT_CONSTRUCTION_RADIUS = 55.0
_ROBOPORT_LINK_DISTANCE = 46.0
# How far outside the source->destination box to survey obstacles, so a route
# has room to detour around something sitting right on the straight path.
_BRIDGE_SURVEY_MARGIN = 24.0
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


def _toward(source: Point, dest: Point) -> str:
    dx, dy = dest[0] - source[0], dest[1] - source[1]
    if abs(dx) >= abs(dy):
        return "east" if dx > 0 else "west"
    return "south" if dy > 0 else "north"


def _clear_side(chest: Point, preferred: str, blocked: set[tuple[int, int]]) -> str:
    """Pick the side of `chest` a bridge can actually attach to.

    bridge_chest_to_chest puts an inserter one tile out and the belt's first
    tile two tiles out, so a side is usable only when BOTH are free. Facing the
    destination is merely the preference: a production stage sits on one side
    of its own output chest, so the direct side is frequently its own machine
    row -- observed live, where every attempt drove the belt back through the
    furnaces it had just built. Falls back to the preferred side when nothing
    is clear, so the caller still gets a plan and a real placement error
    rather than a silent no-op.
    """
    ordered = [preferred, *(d for d in DIRECTION_VECTORS if d != preferred)]
    for direction in ordered:
        vx, vy = DIRECTION_VECTORS[direction]
        tiles = {
            (math.floor(chest[0] + vx * step), math.floor(chest[1] + vy * step))
            for step in (1, 2)
        }
        if not (tiles & blocked):
            return direction
    return preferred


def _ingredient_demand(recipe: str, ingredient: str, machine_count: int) -> float:
    """Items/second of `ingredient` a `machine_count`-machine stage consumes."""
    spec = LINE_RECIPES[recipe]
    crafts_per_second = machine_count * MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]
    amount = spec["amounts"][spec["ingredients"].index(ingredient)]
    return amount * crafts_per_second


def _transport_mode(recipe: str, ingredient: str, machine_count: int) -> str:
    """"logistic" (requester chest, bots deliver) or "belt" (physical corridor).

    Small demand does not justify a belt run across the base; large demand
    cannot be served by bots at all. Chosen per ingredient, because one stage
    can easily need a trickle of one input and a torrent of another.
    """
    demand = _ingredient_demand(recipe, ingredient, machine_count)
    return "belt" if demand > _BOT_THROUGHPUT_LIMIT else "logistic"


def choose_belt_tier(stock: dict[str, int], needed: int) -> str:
    """Cheapest belt tier the base actually holds enough of.

    _DEFAULT_BELT is only a preference. Picking a tier that is out of stock
    places ghosts nothing can build, so availability decides; ties break toward
    the slowest adequate tier, leaving faster belts for links that need them.
    """
    for tier in ("transport-belt", "fast-transport-belt", "express-transport-belt",
                  "turbo-transport-belt"):
        if stock.get(tier, 0) >= needed:
            return tier
    raise StuckError(
        f"No belt tier has {needed} in stock (have: "
        + ", ".join(f"{t}={stock.get(t, 0)}" for t in UNDERGROUND_REACH) + ")"
    )


def _mineable(recipe: str) -> bool:
    """True iff this recipe's sole ingredient is a raw resource (mined/pumped),
    not another LINE_RECIPES product -- i.e. it needs generate_mining_feed,
    not a chest-fed conversion stage."""
    ingredients = LINE_RECIPES[recipe]["ingredients"]
    return len(ingredients) == 1 and ingredients[0] not in LINE_RECIPES


def _swap_infinity_chests(
    plan: dict, modes: dict[str, str], *, request_count: int = _LOGISTIC_REQUEST,
) -> dict[str, Point]:
    """Turn every infinity-chest feeder into a real REQUESTER chest asking for
    its ingredient, so logistic bots deliver it.

    The sandbox pipeline used infinity chests (an infinite cheat source); on a
    real base the alternative is a physical belt from the upstream stage. A
    stage-to-stage belt costs ~50 belts and has to route around everything
    already built, while a requester costs one chest and no corridor at all --
    which matters because belts are a consumable the base has to produce.
    Returns {ingredient: chest_position}.
    """
    positions: dict[str, Point] = {}
    for phase in plan["phases"]:
        for action in phase["actions"]:
            if action.get("entity") == "infinity-chest":
                ingredient = action.pop("infinity_filter")
                if modes.get(ingredient, "logistic") == "logistic":
                    action["entity"] = "requester-chest"
                    action["logistic_request"] = {"name": ingredient, "count": request_count}
                else:
                    # Belt-fed: a plain chest the incoming belt unloads into.
                    action["entity"] = "steel-chest"
                positions[ingredient] = (action["position"]["x"], action["position"]["y"])
    return positions


def _publish_output_chest(plan: dict) -> None:
    """Make a stage's collection chest a passive provider, so its product is
    visible to the logistic network and can be requested by downstream stages
    (and by construction bots for building material)."""
    for phase in plan["phases"]:
        for action in phase["actions"]:
            if action.get("entity") == "steel-chest":
                action["entity"] = "passive-provider-chest"


def _ghost_materials(plan: dict) -> dict[str, int]:
    """Items construction bots must consume to revive this plan's ghosts.
    place_entity actions are created directly and cost nothing."""
    required: dict[str, int] = {}
    for phase in plan["phases"]:
        for action in phase["actions"]:
            if action.get("action_type") == "place_ghost":
                required[action["entity"]] = required.get(action["entity"], 0) + 1
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
        detail = ", ".join(f"{item}: need {required[item]}, short {missing}"
                            for item, missing in sorted(short.items()))
        raise StuckError(
            f"{name} needs material the base does not have -- {detail}. "
            "Produce it (or free some up) before this stage can be built."
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
                      stall_seconds: float = 8.0) -> int:
    """Ghosts remaining, returning as soon as progress STOPS rather than when a
    long timeout expires.

    Bots build continuously while they can; once the count stops falling, more
    waiting changes nothing and the real answer is a diagnosis (no coverage, no
    power, no material). Burning the full timeout first is what made the
    builder feel like it took minutes to notice an obvious problem.
    """
    min_point, max_point = area
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "rcon.print(s.count_entities_filtered{name='entity-ghost',force='" + force + "',"
        "area={{" + str(min_point[0]) + "," + str(min_point[1]) + "},"
        "{" + str(max_point[0]) + "," + str(max_point[1]) + "}}})"
    )
    deadline = time.monotonic() + timeout_seconds
    remaining = int(client.command("/sc " + lua).strip())
    last_change = time.monotonic()
    while remaining and time.monotonic() < deadline:
        time.sleep(poll_seconds)
        current = int(client.command("/sc " + lua).strip())
        if current != remaining:
            remaining, last_change = current, time.monotonic()
        elif time.monotonic() - last_change >= stall_seconds:
            return remaining
    return remaining


def _diagnose_machines(
    client: RconClient, surface: str, positions: list[Point], emit: Callable[[str], None],
    *, grace_seconds: float = _STUCK_GRACE_SECONDS, poll_seconds: float = 2.0,
) -> list[tuple[Point, str]]:
    """Poll each machine's real status until every one reaches a healthy state
    or `grace_seconds` elapses (bots dispatch slowly; a machine that just went
    up needs a moment to receive its first ingredients). Returns the ones
    still stuck, each with its actual reason -- never a guess."""
    deadline = time.monotonic() + grace_seconds
    stuck: list[tuple[Point, str]] = []
    while True:
        statuses = live_base.entity_statuses(client, surface, positions)
        stuck = [
            (position, statuses.get(tuple(position), "missing"))
            for position in positions
            if statuses.get(tuple(position)) not in _HEALTHY_STATUSES
        ]
        if not stuck or time.monotonic() >= deadline:
            break
        time.sleep(poll_seconds)
    for position, status in stuck:
        emit(f"  STUCK: machine at {position} is {status}, not working")
    return stuck


def extend_power(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    near_position: Point, emit: Callable[[str], None],
) -> bool:
    """If the pole network at `near_position` is isolated from the rest of
    the base, build a real medium-pole chain bridging it to the nearest other
    network. Returns True if a bridge was built (caller should re-check
    status afterward), False if there was nothing to bridge (no local pole
    here, or only one network exists at all)."""
    own_network = live_base.pole_network_id(client, surface, near_position)
    if own_network is None:
        return False
    target = live_base.nearest_pole_on_other_network(client, surface, force, near_position, own_network)
    if target is None:
        return False
    target_position, target_name = target
    emit(f"  power gap found: network {own_network} at {near_position} is isolated from "
         f"{target_name} at {target_position} -- bridging with a pole chain")
    # Conservative spacing (< medium-electric-pole's own 9-tile reach) so
    # every hop connects regardless of what pole type sits at either end.
    spacing = min(POLE_SPECS["medium-electric-pole"]["wire"] - 1, 8.0)
    hops = step_points(target_position, near_position, spacing)[:-1]  # drop the dest, already a real pole
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


def extend_roboport_coverage(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    target_position: Point, emit: Callable[[str], None],
) -> bool:
    """If `target_position` is beyond every existing roboport's construction
    radius, chain new roboports out to it (each within link distance of the
    previous one, ending within construction radius of the target). Returns
    True if roboports were added (caller should re-check ghost completion),
    False if coverage was already fine."""
    nearest = live_base.nearest_roboport(client, surface, force, target_position)
    if nearest is None:
        return False
    if math.dist(nearest, target_position) <= _ROBOPORT_CONSTRUCTION_RADIUS:
        return False
    emit(f"  roboport coverage gap: nearest roboport {nearest} is "
         f"{math.dist(nearest, target_position):.0f} tiles from {target_position} "
         f"(construction radius is {_ROBOPORT_CONSTRUCTION_RADIUS:.0f}) -- chaining roboports out")
    step = _ROBOPORT_LINK_DISTANCE - 4  # slack so a rounded tile never lands on the link cliff edge
    hops = step_points(nearest, target_position, step)
    placed: list[Point] = []
    for hop in hops:
        placed.append(hop)
        if math.dist(hop, target_position) <= _ROBOPORT_CONSTRUCTION_RADIUS:
            break
    actions = [
        {"action_type": "place_entity", "entity": "roboport", "position": {"x": x, "y": y}}
        for x, y in placed
    ]
    plan = {"phases": [{"name": "roboport_bridge", "actions": actions}], "surface": surface, "force": force}
    _submit(client, bridge, surface, plan, "roboport_bridge", emit)
    # A roboport with no power provides NO construction coverage, so chaining
    # one out without connecting it just moves the stall. Observed live: a
    # bridged roboport sat at no_power and its ghosts never built.
    for position in placed:
        if live_base.entity_status_name(client, surface, position) == "no_power":
            emit(f"  bridged roboport at {position} has no power -- connecting it")
            if not extend_power(client, bridge, surface, force, position, emit):
                raise StuckError(
                    f"roboport at {position} cannot be powered; it would provide no "
                    "construction coverage"
                )
    return True


def _diagnose_blockage(
    client: RconClient, surface: str, force: str, origin: Point,
    substation_position: Point, machine_positions: list[Point],
) -> tuple[str, str] | None:
    """Why is this stage not finishing? Returns (issue, remedy) or None.

    Ordered by what actually blocks construction first: bots cannot build
    outside coverage, a roboport with no power provides no coverage, and a
    machine with no power never runs even once built.
    """
    nearest = live_base.nearest_roboport(client, surface, force, origin)
    if nearest is None:
        return ("no roboport on this surface", "none")
    if math.dist(nearest, origin) > _ROBOPORT_CONSTRUCTION_RADIUS:
        return (
            f"site is {math.dist(nearest, origin):.0f} tiles from the nearest roboport "
            f"(construction radius {_ROBOPORT_CONSTRUCTION_RADIUS:.0f})",
            "coverage",
        )
    if live_base.entity_status_name(client, surface, nearest) == "no_power":
        return (f"covering roboport at {nearest} has no power", "roboport_power")
    statuses = live_base.entity_statuses(client, surface, machine_positions)
    unpowered = [p for p in machine_positions if statuses.get(tuple(p)) == "no_power"]
    if unpowered:
        return (f"{len(unpowered)} machine(s) unpowered", "stage_power")
    return None


def bring_stage_up(
    client: RconClient, bridge: GameBridge, surface: str, force: str, name: str,
    origin: Point, area: tuple[Point, Point], substation_position: Point,
    machine_positions: list[Point], emit: Callable[[str], None],
    *, rounds: int = _BLOCKAGE_ROUNDS, interval: float = _BLOCKAGE_INTERVAL,
) -> None:
    """Work a stage until it is physically alive, like an open ticket.

    Each round: give the bots a bounded window, and if the build has not
    finished, diagnose WHY and apply the matching remedy, then re-check. The
    previous design waited out one long timeout, attempted a single fix and
    gave up -- so an issue needing two fixes (extend coverage, THEN power the
    roboport that extended it) could never resolve itself.
    """
    # Coverage is knowable from geometry alone, so fix it before waiting on bots.
    extend_roboport_coverage(client, bridge, surface, force, origin, emit)
    for attempt in range(1, rounds + 1):
        remaining = _wait_for_ghosts(
            client, surface, force, area, timeout_seconds=interval,
        )
        issue = _diagnose_blockage(
            client, surface, force, origin, substation_position, machine_positions,
        )
        if remaining == 0 and issue is None:
            if attempt > 1:
                emit(f"  [{name}] RESOLVED after {attempt} round(s)")
            return
        if issue is None:
            emit(f"  [{name} #{attempt}/{rounds}] {remaining} ghost(s) left, no blockage "
                 "found -- bots still working")
            continue
        description, remedy = issue
        emit(f"  [{name} #{attempt}/{rounds}] OPEN: {description} -> remedy: {remedy}")
        if remedy == "coverage":
            extend_roboport_coverage(client, bridge, surface, force, origin, emit)
        elif remedy == "roboport_power":
            nearest = live_base.nearest_roboport(client, surface, force, origin)
            if nearest is not None:
                extend_power(client, bridge, surface, force, nearest, emit)
        elif remedy == "stage_power":
            extend_power(client, bridge, surface, force, substation_position, emit)
        else:
            raise StuckError(f"{name}: {description} -- no automatic remedy")
    raise StuckError(
        f"{name} still blocked after {rounds} rounds "
        f"({rounds * interval:.0f}s of remediation attempts)"
    )


def _mining_drill_positions(origin: Point, machine_count: int) -> list[Point]:
    """Centres emitted by LocalLayoutPlanner.generate_mining_feed()."""
    ox, oy = origin
    return [(ox + 1.5 + (3 * index), oy - 1.5) for index in range(machine_count)]


def _candidate_mining_origins(
    preferred: Point, patch_min: Point, patch_max: Point, machine_count: int,
) -> list[Point]:
    """Integer line origins whose drill footprints can overlap the patch bbox."""
    min_x = math.floor(patch_min[0]) - (3 * machine_count) + 1
    max_x = math.floor(patch_max[0])
    min_y = math.floor(patch_min[1]) + 1
    max_y = math.floor(patch_max[1]) + 3
    origins = [(float(x), float(y)) for x in range(min_x, max_x + 1) for y in range(min_y, max_y + 1)]
    return sorted(origins, key=lambda point: (math.dist(point, preferred), point[1], point[0]))


def _choose_mining_origin(
    preferred: Point, patch_min: Point, patch_max: Point, machine_count: int,
    area_is_clear: Callable[[Point, Point], bool], footprint_has_resource: Callable[[list[Point]], bool],
) -> tuple[Point, int] | None:
    """Find the nearest clear layout whose every drill can mine the target ore.

    Reducing to one drill is an explicit capacity reduction only when the real
    patch cannot support the default pair; it is safer than placing a dead drill.
    """
    for count in range(machine_count, 0, -1):
        for origin in _candidate_mining_origins(preferred, patch_min, patch_max, count):
            ox, oy = origin
            if not area_is_clear((ox, oy - 5), (ox + (count * 3) + 12, oy + 7)):
                continue
            if footprint_has_resource(_mining_drill_positions(origin, count)):
                return origin, count
    return None

def build_mining_stage(
    client: RconClient, bridge: GameBridge, surface: str, force: str, recipe: str,
    reference_point: Point, emit: Callable[[str], None],
) -> Point:
    """Mine + smelt/pump `recipe` (e.g. iron-plate, copper-plate) from real,
    surveyed resource near `reference_point`. Returns the real output chest position."""
    ore = LINE_RECIPES[recipe]["ingredients"][0]
    found = live_base.nearest_resource(client, surface, ore, reference_point)
    if found is None:
        raise StuckError(f"No {ore} found within survey radius of {reference_point} -- "
                          "cannot mine what isn't on the map")
    nearest_tile, patch_min, patch_max = found
    preferred_box = live_base.find_clear_area(
        client, surface, (nearest_tile[0] - 5, nearest_tile[1] - 5),
        (_DEFAULT_MACHINE_COUNT * 3) + 12, 12,
    )
    if preferred_box is None:
        raise StuckError(f"No clear staging area found near the {ore} patch at {nearest_tile}")
    preferred = (round(preferred_box[0]), round(preferred_box[1] + 5))
    selected = _choose_mining_origin(
        preferred, patch_min, patch_max, _DEFAULT_MACHINE_COUNT,
        lambda lower, upper: live_base.area_clear(client, surface, lower, upper),
        lambda centres: live_base.drill_footprints_have_resource(client, surface, ore, centres),
    )
    if selected is None:
        raise StuckError(f"No clear position near the {ore} patch at {nearest_tile} puts every drill on {ore}")
    (ox, oy), machine_count = selected
    ox, oy = int(ox), int(oy)
    emit(f"mining stage for {recipe}: ore at {nearest_tile}, building at ({ox},{oy})")

    planner = LocalLayoutPlanner()
    plan = planner.generate_line_layout(
        recipe, machine_count, ox, oy, mining_feed=True,
        belt_type=_DEFAULT_BELT, inserter_type=_DEFAULT_INSERTER,
        feed_style="chest", terminal_collector=True,
    )
    plan = strip_local_power(plan, remove_substations=False)
    _publish_output_chest(plan)
    machine = LINE_RECIPES[recipe]["machine"]
    machine_positions = [
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action["entity"] == machine
    ]
    substation_position = next(
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action["entity"] == "substation"
    )
    plan["surface"], plan["force"] = surface, force
    _submit(client, bridge, surface, plan, f"mining_{recipe}", emit)

    length = machine_count * 3
    area = ((ox - 15, oy - 15), (ox + length + 15, oy + 15))
    bring_stage_up(client, bridge, surface, force, f"mining stage for {recipe}",
                    (ox, oy), area, substation_position, machine_positions, emit)
    stuck = _diagnose_machines(client, surface, machine_positions, emit)
    if stuck:
        raise StuckError(f"mining stage for {recipe} built but not healthy: {stuck}")
    return (ox + length + 1.5, oy + 6.5)


def build_conversion_stage(
    client: RconClient, bridge: GameBridge, surface: str, force: str, recipe: str,
    ingredient_sources: dict[str, Point], reference_point: Point, emit: Callable[[str], None],
) -> Point:
    """Assemble `recipe` from its (already-producing) ingredients. Bridges each
    ingredient's real upstream output chest to this stage's real feed chest
    with a belt+inserter pair, using whichever side faces the source."""
    machine_count = _DEFAULT_MACHINE_COUNT
    width = machine_count * 3
    origin = live_base.find_clear_area(client, surface, reference_point, width + 12, 20)
    if origin is None:
        raise StuckError(f"No clear space found near {reference_point} for the {recipe} stage")
    ox, oy = round(origin[0]), round(origin[1] + 5)
    emit(f"conversion stage for {recipe}: building at ({ox},{oy})")

    planner = LocalLayoutPlanner()
    plan = planner.generate_line_layout(
        recipe, machine_count, ox, oy,
        belt_type=_DEFAULT_BELT, inserter_type=_DEFAULT_INSERTER,
        feed_style="chest", terminal_collector=True,
    )
    plan = strip_local_power(plan, remove_substations=False)
    _publish_output_chest(plan)
    modes = {
        ingredient: _transport_mode(recipe, ingredient, machine_count)
        for ingredient in LINE_RECIPES[recipe]["ingredients"]
    }
    feed_positions = _swap_infinity_chests(plan, modes)
    machine = LINE_RECIPES[recipe]["machine"]
    machine_positions = [
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action["entity"] == machine
    ]
    substation_position = next(
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action["entity"] == "substation"
    )
    plan["surface"], plan["force"] = surface, force
    _submit(client, bridge, surface, plan, f"conversion_{recipe}", emit)

    length = machine_count * 3
    stage_area = ((ox - 15, oy - 15), (ox + length + 15, oy + 15))
    bring_stage_up(client, bridge, surface, force, f"conversion stage for {recipe}",
                    (ox, oy), stage_area, substation_position, machine_positions, emit)

    feed_delay = 0.0
    unresolved = sorted(set(feed_positions) - set(ingredient_sources))
    if unresolved:
        raise StuckError(
            f"{recipe} feeds on {unresolved}, which have no producing stage to supply them"
        )
    for ingredient in sorted(feed_positions):
        feed_position = feed_positions[ingredient]
        source_position = ingredient_sources[ingredient]
        if modes[ingredient] == "logistic":
            # Bots carry it: the upstream chest is a passive provider and this
            # one is a requester, so no corridor is built at all.
            emit(f"  {recipe}: {ingredient} by logistic bots "
                 f"({_ingredient_demand(recipe, ingredient, machine_count):.2f}/s, "
                 f"provider at {source_position})")
            continue
        emit(f"  {recipe}: {ingredient} needs a belt "
             f"({_ingredient_demand(recipe, ingredient, machine_count):.2f}/s exceeds "
             f"the {_BOT_THROUGHPUT_LIMIT}/s bot limit)")
        blocked = live_base.occupied_tiles(
            client, surface,
            (min(source_position[0], feed_position[0]) - _BRIDGE_SURVEY_MARGIN,
             min(source_position[1], feed_position[1]) - _BRIDGE_SURVEY_MARGIN),
            (max(source_position[0], feed_position[0]) + _BRIDGE_SURVEY_MARGIN,
             max(source_position[1], feed_position[1]) + _BRIDGE_SURVEY_MARGIN),
        )
        blocked -= {
            (math.floor(source_position[0]), math.floor(source_position[1])),
            (math.floor(feed_position[0]), math.floor(feed_position[1])),
        }
        direction = _toward(source_position, feed_position)
        span = int(abs(source_position[0] - feed_position[0])
                   + abs(source_position[1] - feed_position[1])) + 4
        belt_type = choose_belt_tier(live_base.available_items(client, surface, force), span)
        bridge_actions = bridge_chest_to_chest(
            source_position, feed_position,
            exit_direction=_clear_side(source_position, direction, blocked),
            entry_direction=_clear_side(feed_position, opposite(direction), blocked),
            belt_type=belt_type, inserter_type=_DEFAULT_INSERTER,
            blocked_tiles=blocked,
        )
        bridge_plan = {
            "phases": [{"name": f"bridge_{ingredient}_to_{recipe}", "actions": bridge_actions}],
            "surface": surface, "force": force,
        }
        _submit(client, bridge, surface, bridge_plan, f"bridge_{ingredient}_to_{recipe}", emit)
        belt_tiles = sum(
            1 for action in bridge_actions if "transport-belt" in action["entity"]
        )
        feed_delay = max(feed_delay, transit_seconds(belt_type, belt_tiles))
        emit(f"    {ingredient}: {belt_tiles} belt tiles, first item arrives in "
             f"~{transit_seconds(belt_type, belt_tiles):.0f}s")

    xs = [ox, ox + length, *(p[0] for p in ingredient_sources.values())]
    transport_area = ((min(xs) - 5, oy - 15), (max(xs) + 5, oy + 15))
    remaining = _wait_for_ghosts(client, surface, force, transport_area)
    if remaining:
        raise StuckError(f"{recipe} transport still has {remaining} unbuilt ghosts after settling")
    # A stage cannot possibly run before its first ingredient physically
    # arrives. Judging it healthy-or-not sooner than that reports a false
    # failure on a bridge that is working -- observed live, where a ~60 tile
    # yellow belt needed ~32s and the check gave up at 20s.
    stuck = _diagnose_machines(
        client, surface, machine_positions, emit,
        grace_seconds=_STUCK_GRACE_SECONDS + feed_delay * _TRANSIT_SAFETY,
    )
    if stuck:
        # Bridges are the most likely remaining culprit for "built but not
        # fed": confirm each feed chest is actually receiving the ingredient
        # before blaming something deeper.
        for ingredient, feed_position in feed_positions.items():
            contents = live_base.chest_contents(client, surface, feed_position)
            if contents.get(ingredient, 0) == 0:
                emit(f"  DIAGNOSIS: {feed_position} feed chest for {ingredient} is empty -- "
                     "the bridge from its source isn't delivering (check the bridge inserters/belt)")
        raise StuckError(f"conversion stage for {recipe} built but not healthy: {stuck}")
    return (ox + length + 1.5, oy + 6.5)


def ensure_produced(
    client: RconClient, bridge: GameBridge, surface: str, force: str, item: str,
    reference_point: Point, emit: Callable[[str], None],
) -> Point | None:
    """Returns the item's real output chest position if it's already producing;
    otherwise builds exactly ONE missing stage (the deepest unmet ingredient
    first) and returns None so the caller re-surveys and calls again."""
    if item not in LINE_RECIPES:
        raise StuckError(f"No recipe knowledge for {item!r} -- add it to planners/recipe_data.py "
                          "before asking the builder to produce it")
    spec = LINE_RECIPES[item]
    existing = live_base.find_line(client, surface, force, item, spec["machine"])
    if existing and existing.working_count > 0:
        chest = live_base.nearest_container(
            client, surface, force, existing.machine_positions[-1]
        )
        return chest or existing.output_position
    if existing:
        # A stage that exists but is not running is a REPAIR job, not a reason
        # to build a second one. Duplicating instead of repairing is what left
        # three half-built copper stages littering one ore patch across runs,
        # eventually leaving no clear ground to place a fourth.
        emit(f"{item}: found {existing.machine_count} existing machine(s), "
             f"{existing.working_count} working -- repairing rather than duplicating")
        xs = [p[0] for p in existing.machine_positions]
        ys = [p[1] for p in existing.machine_positions]
        area = ((min(xs) - 15, min(ys) - 15), (max(xs) + 15, max(ys) + 15))
        substation = live_base.nearest_pole_on_other_network(
            client, surface, force, existing.machine_positions[0], -1,
        )
        bring_stage_up(
            client, bridge, surface, force, f"existing {item} stage",
            existing.machine_positions[0], area,
            substation[0] if substation else existing.machine_positions[0],
            list(existing.machine_positions), emit,
        )
        return None

    if not _mineable(item):
        sources: dict[str, Point] = {}
        for ingredient in spec["ingredients"]:
            if ingredient not in LINE_RECIPES:
                raise StuckError(f"{item} needs {ingredient!r}, which has no recipe and isn't mineable")
            position = ensure_produced(client, bridge, surface, force, ingredient, reference_point, emit)
            if position is None:
                return None  # built something upstream this round; re-survey next loop
            sources[ingredient] = position
        build_conversion_stage(client, bridge, surface, force, item, sources, reference_point, emit)
        return None

    build_mining_stage(client, bridge, surface, force, item, reference_point, emit)
    return None


def run(
    goal_item: str, *, surface: str = "nauvis", force: str = "player",
    rcon_host: str = "127.0.0.1", rcon_port: int = 27017, rcon_password: str = "",
    script_output: Path | str = "", reference_point: Point = (0.0, 0.0),
    max_iterations: int = 20, emit: Callable[[str], None] = print,
) -> dict:
    """Loop: survey -> decide the single deepest missing stage -> build it ->
    repeat, until `goal_item` has a real, working line or the builder is
    genuinely stuck (raises StuckError rather than guessing)."""
    client = RconClient(rcon_host, rcon_port, rcon_password)
    bridge = GameBridge(script_output=Path(script_output), host=rcon_host, port=rcon_port,
                         password=rcon_password)
    try:
        for iteration in range(max_iterations):
            emit(f"--- iteration {iteration}: checking {goal_item} ---")
            position = ensure_produced(client, bridge, surface, force, goal_item, reference_point, emit)
            if position is not None:
                emit(f"GOAL MET: {goal_item} is producing at {position}")
                return {"ok": True, "iterations": iteration + 1, "output_position": position}
        raise StuckError(f"Did not reach a working {goal_item} line within {max_iterations} iterations")
    finally:
        client.close()
        bridge.close()
