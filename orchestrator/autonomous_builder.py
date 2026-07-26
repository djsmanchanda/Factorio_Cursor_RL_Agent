# Path: orchestrator/autonomous_builder.py
# Purpose: Goal-driven autonomous factory expansion on a real base -- given a target item, recursively ensures every ingredient in its recipe chain has a real, working production stage, deciding placement, connections, and troubleshooting itself.

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Callable

from orchestrator import live_base
from orchestrator.game_bridge import GameBridge, load_json
from planners.belt_bridge import DIRECTION_VECTORS, bridge_chest_to_chest, opposite
from planners.infrastructure import POLE_SPECS, strip_local_power
from planners.infrastructure_geometry import step_points
from planners.local_layout_planner import LocalLayoutPlanner
from planners.recipe_data import LINE_RECIPES
from planners.sandbox_infrastructure import build_layout_authorization
from tools.rcon_client import RconClient

Point = tuple[float, float]

_DEFAULT_MACHINE_COUNT = 2
_DEFAULT_BELT = "fast-transport-belt"
_DEFAULT_INSERTER = "fast-inserter"
_HEALTHY_STATUSES = {"working"}
_STUCK_GRACE_SECONDS = 30.0
# Live-verified this session: roboport construction radius 55, link (chain)
# radius conservatively 46 (hard game limit ~50).
_ROBOPORT_CONSTRUCTION_RADIUS = 55.0
_ROBOPORT_LINK_DISTANCE = 46.0
# How far outside the source->destination box to survey obstacles, so a route
# has room to detour around something sitting right on the straight path.
_BRIDGE_SURVEY_MARGIN = 24.0


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


def _mineable(recipe: str) -> bool:
    """True iff this recipe's sole ingredient is a raw resource (mined/pumped),
    not another LINE_RECIPES product -- i.e. it needs generate_mining_feed,
    not a chest-fed conversion stage."""
    ingredients = LINE_RECIPES[recipe]["ingredients"]
    return len(ingredients) == 1 and ingredients[0] not in LINE_RECIPES


def _swap_infinity_chests(plan: dict) -> dict[str, Point]:
    """Replace every infinity-chest feeder with a real steel-chest in place,
    returning {ingredient_name: chest_position} for the caller to bridge."""
    positions: dict[str, Point] = {}
    for phase in plan["phases"]:
        for action in phase["actions"]:
            if action.get("entity") == "infinity-chest":
                ingredient = action.pop("infinity_filter")
                action["entity"] = "steel-chest"
                positions[ingredient] = (action["position"]["x"], action["position"]["y"])
    return positions


def _submit(
    client: RconClient, bridge: GameBridge, surface: str, plan: dict, name: str,
    emit: Callable[[str], None], *, max_retries: int = 2,
) -> dict:
    """Submit a plan; if a tile is blocked, clear it ONLY when it's obviously
    safe map clutter (a tree, a rock -- never anything a force built) and
    retry. A collision with anything else means this exact placement is
    genuinely occupied -- raise so the caller picks a different spot instead
    of bulldozing real infrastructure."""
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
                      *, timeout_seconds: float = 180.0, poll_seconds: float = 3.0) -> int:
    min_point, max_point = area
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "rcon.print(s.count_entities_filtered{name='entity-ghost',force='" + force + "',"
        "area={{" + str(min_point[0]) + "," + str(min_point[1]) + "},"
        "{" + str(max_point[0]) + "," + str(max_point[1]) + "}}})"
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        remaining = int(client.command("/sc " + lua).strip())
        if remaining == 0:
            return 0
        time.sleep(poll_seconds)
    return remaining


def _diagnose_machines(
    client: RconClient, surface: str, positions: list[Point], emit: Callable[[str], None],
    *, grace_seconds: float = _STUCK_GRACE_SECONDS, poll_seconds: float = 5.0,
) -> list[tuple[Point, str]]:
    """Poll each machine's real status until every one reaches a healthy state
    or `grace_seconds` elapses (bots dispatch slowly; a machine that just went
    up needs a moment to receive its first ingredients). Returns the ones
    still stuck, each with its actual reason -- never a guess."""
    deadline = time.monotonic() + grace_seconds
    stuck: list[tuple[Point, str]] = []
    while True:
        stuck = []
        for position in positions:
            status = live_base.entity_status_name(client, surface, position)
            if status not in _HEALTHY_STATUSES:
                stuck.append((position, status or "missing"))
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


def _repair_and_rediagnose(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    substation_position: Point, machine_positions: list[Point], stuck: list[tuple[Point, str]],
    emit: Callable[[str], None],
) -> list[tuple[Point, str]]:
    """If the stuck reason is a real, fixable gap this builder knows how to
    close (no_power so far), fix it and re-check; otherwise return `stuck`
    as-is so the caller reports it plainly."""
    if any(reason == "no_power" for _position, reason in stuck):
        if extend_power(client, bridge, surface, force, substation_position, emit):
            return _diagnose_machines(client, surface, machine_positions, emit)
    return stuck


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
    return True


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
    remaining = _wait_for_ghosts(client, surface, force, area)
    if remaining and extend_roboport_coverage(client, bridge, surface, force, (ox, oy), emit):
        remaining = _wait_for_ghosts(client, surface, force, area)
    if remaining:
        raise StuckError(f"mining stage for {recipe} still has {remaining} unbuilt ghosts after settling")
    stuck = _diagnose_machines(client, surface, machine_positions, emit)
    if stuck:
        stuck = _repair_and_rediagnose(
            client, bridge, surface, force, substation_position, machine_positions, stuck, emit,
        )
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
    feed_positions = _swap_infinity_chests(plan)
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

    for ingredient, feed_position in feed_positions.items():
        source_position = ingredient_sources[ingredient]
        direction = _toward(source_position, feed_position)
        # Survey what is really in the corridor first. Without this the route
        # is a naive L that happily runs through the upstream stage's own
        # furnaces and any belt already crossing the base -- observed live.
        # The two endpoints are excluded so the bridge can still attach to the
        # chests it is supposed to connect.
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
        exit_direction = _clear_side(source_position, direction, blocked)
        entry_direction = _clear_side(feed_position, opposite(direction), blocked)
        bridge_actions = bridge_chest_to_chest(
            source_position, feed_position,
            exit_direction=exit_direction, entry_direction=entry_direction,
            belt_type=_DEFAULT_BELT, inserter_type=_DEFAULT_INSERTER,
            blocked_tiles=blocked,
        )
        bridge_plan = {
            "phases": [{"name": f"bridge_{ingredient}_to_{recipe}", "actions": bridge_actions}],
            "surface": surface, "force": force,
        }
        _submit(client, bridge, surface, bridge_plan, f"bridge_{ingredient}_to_{recipe}", emit)

    length = machine_count * 3
    xs = [ox, ox + length, *(p[0] for p in ingredient_sources.values())]
    area = ((min(xs) - 5, oy - 15), (max(xs) + 5, oy + 15))
    remaining = _wait_for_ghosts(client, surface, force, area)
    if remaining and extend_roboport_coverage(client, bridge, surface, force, (ox, oy), emit):
        remaining = _wait_for_ghosts(client, surface, force, area)
    if remaining:
        raise StuckError(f"conversion stage for {recipe} still has {remaining} unbuilt ghosts after settling")
    stuck = _diagnose_machines(client, surface, machine_positions, emit)
    if stuck:
        stuck = _repair_and_rediagnose(
            client, bridge, surface, force, substation_position, machine_positions, stuck, emit,
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
        return existing.output_position

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
