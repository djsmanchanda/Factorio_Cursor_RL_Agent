# Path: orchestrator/stage_services.py
# Purpose: Execution primitives shared by every stage build -- submitting plans, waiting on bots, and extending power and roboport coverage so a stage is reachable and served.

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from typing import Callable

from orchestrator import live_base
from orchestrator.game_bridge import GameBridge, load_json
from planners.infrastructure import POLE_SPECS
from planners.infrastructure_geometry import step_points
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
    placed = roboport_chain(nearest, target_position, radius)
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
    for position in placed:
        if live_base.entity_status_name(client, surface, position) == "no_power":
            emit(f"  bridged roboport at {position} has no power -- connecting it")
            if not extend_power(client, bridge, surface, force, position, emit):
                raise StuckError(
                    f"roboport at {position} cannot be powered; it would provide no "
                    f"{purpose} coverage"
                )
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
