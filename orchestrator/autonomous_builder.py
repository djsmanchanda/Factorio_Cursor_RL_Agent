# Path: orchestrator/autonomous_builder.py
# Purpose: Goal-driven autonomous factory expansion on a real base -- given a target item, recursively ensures every ingredient in its recipe chain has a real, working production stage, deciding placement, connections, and troubleshooting itself.

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Callable

from orchestrator import live_base
from orchestrator.game_bridge import GameBridge
from orchestrator.stage_services import (
    StuckError,
    _BLOCKAGE_INTERVAL,
    _BLOCKAGE_ROUNDS,
    _BOT_THROUGHPUT_LIMIT,
    _BRIDGE_SURVEY_MARGIN,
    _DEFAULT_BELT,
    _DEFAULT_INSERTER,
    _LOGISTIC_CHEST_ENTITIES,
    _ROBOPORT_CONSTRUCTION_RADIUS,
    _ROBOPORT_LOGISTIC_RADIUS,
    _STAGE_CHEST_REACH,
    _STUCK_GRACE_SECONDS,
    _TRANSIT_SAFETY,
    _diagnose_machines,
    _logistic_chest_positions,
    _submit,
    _wait_for_ghosts,
    ensure_logistic_coverage,
    extend_power,
    extend_roboport_coverage,
)
from orchestrator.stage_transport import (
    _clear_side,
    _ingredient_demand,
    _publish_output_chest,
    _swap_infinity_chests,
    _toward,
    _transport_mode,
    choose_belt_tier,
)
from planners.belt_bridge import bridge_chest_to_chest, opposite, transit_seconds
from planners.infrastructure import strip_local_power
from planners.local_layout_planner import LocalLayoutPlanner
from planners.recipe_data import LINE_RECIPES
from tools.rcon_client import RconClient

Point = tuple[float, float]
_DEFAULT_MACHINE_COUNT = 2


def _mineable(recipe: str) -> bool:
    """True iff this recipe's sole ingredient is a raw resource (mined/pumped),
    not another LINE_RECIPES product -- i.e. it needs generate_mining_feed,
    not a chest-fed conversion stage."""
    ingredients = LINE_RECIPES[recipe]["ingredients"]
    return len(ingredients) == 1 and ingredients[0] not in LINE_RECIPES


def _diagnose_blockage(
    client: RconClient, surface: str, force: str, origin: Point,
    substation_position: Point, machine_positions: list[Point],
    logistic_chest_positions: Sequence[Point] = (),
) -> tuple[str, str] | None:
    """Why is this stage not finishing? Returns (issue, remedy) or None.

    Ordered by what actually blocks construction first: bots cannot build
    outside coverage, and a roboport with no power provides no coverage at all.
    Logistic coverage is checked next because its remedy places AND powers a
    roboport, which can incidentally close a power gap near the stage -- the
    reverse is never true, so diagnosing it before machine power lets one round
    fix both. Machine power is last; it is also the only check here that has to
    poll every machine's live status.
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
    if logistic_chest_positions:
        # Only chests that are actually BUILT are judged here; ones still
        # waiting on a bot are absent from the reply and are the ghost count's
        # business, not a coverage fault.
        served = live_base.logistic_network_ids(client, surface, logistic_chest_positions)
        orphaned = [p for p, network in served.items() if network is None]
        if orphaned:
            return (
                f"{len(orphaned)} logistic chest(s) belong to no logistic network "
                f"(first at {tuple(orphaned[0])}); a chest outside every roboport's "
                f"{_ROBOPORT_LOGISTIC_RADIUS:.0f}-tile supply area can neither supply "
                "nor be supplied",
                "logistic_coverage",
            )
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
    logistic_chest_positions: Sequence[Point] = (),
) -> None:
    """Work a stage until it is physically alive, like an open ticket.

    Each round: give the bots a bounded window, and if the build has not
    finished, diagnose WHY and apply the matching remedy, then re-check. The
    previous design waited out one long timeout, attempted a single fix and
    gave up -- so an issue needing two fixes (extend coverage, THEN power the
    roboport that extended it) could never resolve itself.
    """
    # Both coverages are knowable from geometry alone, so fix them before
    # waiting on bots -- and logistic coverage BEFORE the chests are even built,
    # so they join a network the moment they exist rather than after a stage has
    # visibly starved.
    extend_roboport_coverage(client, bridge, surface, force, origin, emit)
    ensure_logistic_coverage(client, bridge, surface, force, logistic_chest_positions, emit)
    for attempt in range(1, rounds + 1):
        remaining = _wait_for_ghosts(
            client, surface, force, area, timeout_seconds=interval,
        )
        issue = _diagnose_blockage(
            client, surface, force, origin, substation_position, machine_positions,
            logistic_chest_positions,
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
        elif remedy == "logistic_coverage":
            ensure_logistic_coverage(
                client, bridge, surface, force, logistic_chest_positions, emit,
            )
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
                    (ox, oy), area, substation_position, machine_positions, emit,
                    logistic_chest_positions=_logistic_chest_positions(plan))
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
    # The UPSTREAM provider chest of every bot-served ingredient needs supply-area
    # coverage just as much as this stage's own requesters do: a provider outside
    # every network hands out nothing, and the only symptom downstream is
    # item_ingredient_shortage on a stage that looks correctly built. Live case:
    # the iron-gear-wheel output chest at (-9.5,30.5) had no network at all.
    logistic_chests = _logistic_chest_positions(plan) + [
        ingredient_sources[ingredient]
        for ingredient, mode in sorted(modes.items())
        if mode == "logistic" and ingredient in ingredient_sources
    ]
    bring_stage_up(client, bridge, surface, force, f"conversion stage for {recipe}",
                    (ox, oy), stage_area, substation_position, machine_positions, emit,
                    logistic_chest_positions=logistic_chests)

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


def _existing_stage_chests(
    client: RconClient, surface: str, force: str, last_machine: Point,
) -> list[Point]:
    """The logistic chest of an ALREADY-BUILT stage, when it has one nearby.

    A stage found by survey has no BuildPlan to read chest positions out of,
    and it is exactly the case where a stranded provider chest has been sitting
    unnoticed for a whole run. One nearest-container probe is enough: a stage's
    own collection chest sits at the end of its machine row.
    """
    chest = live_base.nearest_container(
        client, surface, force, last_machine, names=tuple(sorted(_LOGISTIC_CHEST_ENTITIES)),
    )
    if chest is None or math.dist(chest, last_machine) > _STAGE_CHEST_REACH:
        return []
    return [chest]


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
            logistic_chest_positions=_existing_stage_chests(
                client, surface, force, existing.machine_positions[-1],
            ),
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
