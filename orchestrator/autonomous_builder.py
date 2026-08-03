# Path: orchestrator/autonomous_builder.py
# Purpose: Goal-driven autonomous factory expansion on a real base -- given a target item, recursively ensures every ingredient in its recipe chain has a real, working production stage, deciding placement, connections, and troubleshooting itself.

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from orchestrator import live_base
from orchestrator.build_decisions import (
    _heaviest_source,
    _mineable,
    _stage_inserter_type,
    expansion_target,
    may_consume_stocked_inputs,
)
from orchestrator.build_diagnostics import _diagnose_blockage, _side_sample_plate_output
from orchestrator.construction_stock import BULK_CONSTRUCTION_ITEMS, standing_target
from orchestrator.baseline_production import (
    BASELINE_MACHINES, BASELINE_PLATES, baseline_build_order,
    baseline_drill_phase, baseline_plate_draw, baseline_smelter_count,
)
from orchestrator.game_bridge import GameBridge, load_json
from orchestrator.mine_retirement import retire_depleted_mines
from orchestrator.mine_output_tap import legacy_output_tap_plan
from orchestrator.mall_builder import build_compact_mall_stage
from orchestrator.parts_mall import (
    MaterialShortage, add_demands, mission_mall_targets, wait_for_stock,
)
from orchestrator.intermediate_scaling import (
    backlog_seconds,
    live_intermediate_demand, promoted_line_belt_type, promoted_line_machine_count,
)
from orchestrator.priority_list import PriorityList
from orchestrator.extraction_transport import (
    planned_footprint_tiles, preflight_ingredient_transport,
)
from orchestrator.stage_chemical import ensure_coal_mine, ensure_oil_cell
from orchestrator.stage_extraction import (
    LOCAL_MODE_MAX_LINK_TILES, existing_mine_service_geometry,
    candidate_mining_origins as _candidate_mining_origins,  # noqa: F401 - compatibility export
    choose_mining_origin as _choose_mining_origin,  # noqa: F401 - compatibility export
    mining_drill_positions as _mining_drill_positions,  # noqa: F401 - compatibility export
    plan_local_extraction,
)
from orchestrator.stage_recovery import repair_existing_ingredient_transport
from orchestrator.stage_services import (
    StuckError,
    _BLOCKAGE_INTERVAL,
    _BLOCKAGE_ROUNDS,
    _DEFAULT_BELT,
    _DEFAULT_INSERTER,
    _LOGISTIC_CHEST_ENTITIES,
    _STAGE_CHEST_REACH,
    _diagnose_machines,
    _logistic_chest_positions,
    _submit,
    _wait_for_ghosts,
    ensure_logistic_coverage,
    extend_power,
    extend_roboport_coverage,
    validate_builder_target,
)
from orchestrator.stage_transport import (
    _BELT_TIERS_CHEAPEST_FIRST,
    _publish_output_chest,
    _direct_single_belt_feed,
    _swap_infinity_chests,
    _transport_mode,
    ensure_ingredient_transport,
    transport_grace_seconds,
)
from planners.bootstrap_smelting import (
    generate_logistic_smelter,
    logistic_smelter_origin,
    retire_logistic_smelter_plan,
)
from planners.infrastructure import strip_local_power
from planners.local_layout_planner import LocalLayoutPlanner
from planners.mall_layout import (
    generate_compact_mall_request_update, generate_mall_provider_limit_update,
    generate_promoted_mall_retirement_plan,
)
from planners.recipe_data import (
    LINE_RECIPES,
    MACHINE_SPEEDS,
    ITEM_STACK_SIZES,
    install_catalog_line_recipes,
    install_catalog_machines,
    install_catalog_stack_sizes,
)
from tools.rcon_client import RconClient

Point = tuple[float, float]
_DEFAULT_MACHINE_COUNT = 2

# A livelock re-selects the same task and gets the same result forever.
# max_iterations never bounded it: `iteration` only advances on goal work,
# so every mall/prep `continue` skipped it and a stuck run spun for hours.
# This counts consecutive passes that chose the same task at the same
# completion -- real progress moves one of them.
_MAX_UNCHANGED_PASSES = 12


def _apply_remedy(
    client: RconClient, bridge: GameBridge, surface: str, force: str, name: str,
    remedy: str, description: str, origin: Point, substation_position: Point,
    machine_positions: list[Point], logistic_chest_positions: Sequence[Point],
    area: tuple[Point, Point] | None, emit: Callable[[str], None],
) -> bool:
    """Apply the one remedy a diagnosis asked for; report whether it acted.

    A no-op is not a failure. A roboport that was only just connected still
    has to charge before it serves a logistic area, so the honest answer is
    'nothing to do yet' -- the caller waits it out rather than giving up.
    """
    if remedy == "coverage":
        acted = extend_roboport_coverage(
            client, bridge, surface, force, origin, emit,
        )
    elif remedy == "logistic_coverage":
        # Unlike a power gap, this one CAN clear without the remedy doing
        # anything: a roboport that was just connected still has to charge
        # its buffer before it serves a logistic area, and until it does the
        # chests inside its range read as belonging to no network. So a
        # no-op here is reported and waited out rather than treated as
        # fatal -- but it is no longer silent, and a run where every single
        # round was a no-op now says so instead of timing out anonymously.
        acted = ensure_logistic_coverage(
            client, bridge, surface, force, logistic_chest_positions, emit,
        )
        if not acted:
            emit("    coverage is already geometrically sufficient -- waiting for the "
                 "covering roboport to finish powering up")
    elif remedy == "roboport_power":
        nearest = live_base.nearest_roboport(client, surface, force, origin)
        if nearest is not None and not extend_power(
            client, bridge, surface, force, nearest, emit,
        ):
            raise StuckError(
                f"{name}: {description}, but no power bridge can be built from the "
                f"roboport at {nearest} -- either no pole stands there or the whole "
                "surface is already one network, so retrying cannot change anything"
            )
        acted = nearest is not None
    elif remedy == "stage_power":
        # A remedy that changes nothing must not be retried. extend_power
        # returns False without emitting when there is no pole at the
        # recorded position or no second network to bridge to; the previous
        # code discarded that, so the loop re-ran an identical no-op for
        # every remaining round and reported only a bare timeout.
        if not extend_power(
            client, bridge, surface, force, substation_position, emit,
        ):
            raise StuckError(
                f"{name}: {description}, but no power bridge can be built from "
                f"{substation_position} -- either no pole stands there (check the "
                "planned vs. built substation position) or the whole surface is "
                "already one network, so retrying cannot change anything"
            )
        acted = True
    elif remedy == "entity_power":
        stranded = live_base.unpowered_entities(client, surface, area)
        if not stranded:
            acted = False
        else:
            # Each stranded entity is wired individually: they are stranded
            # precisely because no single pole position covers them all.
            acted = False
            for position in stranded:
                acted |= extend_power(
                    client, bridge, surface, force, position, emit,
                )
            if not acted:
                raise StuckError(
                    f"{name}: {description}, but none of {stranded} can be reached "
                    "by a pole chain from any generating network"
                )
    else:
        raise StuckError(f"{name}: {description} -- no automatic remedy")
    return bool(acted)


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
    description, remedy, acted_ever = "no blockage recorded", "none", False
    for attempt in range(1, rounds + 1):
        remaining = _wait_for_ghosts(
            client, surface, force, area, timeout_seconds=interval,
        )
        issue = _diagnose_blockage(
            client, surface, force, origin, substation_position, machine_positions,
            logistic_chest_positions, area,
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
        acted = _apply_remedy(
            client, bridge, surface, force, name, remedy, description, origin,
            substation_position, machine_positions, logistic_chest_positions,
            area, emit,
        )
        acted_ever |= bool(acted)
    if not acted_ever:
        raise StuckError(
            f"{name}: {description}, and not one of the {rounds} remediation rounds "
            f"built anything -- the remedy '{remedy}' cannot address this fault, so the "
            f"{rounds * interval:.0f}s were spent re-running a no-op"
        )
    raise StuckError(
        f"{name} still blocked after {rounds} rounds "
        f"({rounds * interval:.0f}s of remediation attempts); last issue: {description}"
    )


def _place_new_mine(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    extraction, emit: Callable[[str], None],
) -> None:
    """Submit a freshly planned mine and work it up to running."""
    if extraction.mine_origin is None:
        raise StuckError("new extraction plan has no mine origin")
    ox, oy = extraction.mine_origin
    plan = strip_local_power(extraction.build_plan, remove_substations=False)
    _publish_output_chest(plan)
    machine_positions = [
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action["entity"] == "electric-mining-drill"
    ]
    power_positions = [
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action["entity"] in {"substation", "medium-electric-pole"}
    ]
    if not power_positions:
        raise StuckError(f"mining plan for {extraction.ore} has no power anchor")
    substation_position = power_positions[0]
    plan["surface"], plan["force"] = surface, force
    _submit(client, bridge, surface, plan, f"mining_{extraction.ore}", emit)
    stage_xs = [x for x, _y in machine_positions] + [extraction.ore_output[0]]
    stage_ys = [y for _x, y in machine_positions] + [extraction.ore_output[1]]
    area = (
        (min(stage_xs) - 15, min(stage_ys) - 15),
        (max(stage_xs) + 15, max(stage_ys) + 15),
    )
    bring_stage_up(
        client, bridge, surface, force, f"mining stage for {extraction.ore}",
        (ox, oy), area, substation_position, machine_positions, emit,
        logistic_chest_positions=_logistic_chest_positions(plan),
    )
    stuck = _diagnose_machines(
        client, surface, machine_positions, emit, bridge=bridge, force=force,
    )
    if stuck:
        raise StuckError(
            f"mining stage for {extraction.ore} built but not healthy: {stuck}"
        )


def _reuse_expansion_row(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    extraction, emit: Callable[[str], None],
) -> None:
    """Work up a reserved row whose drills are already on the ground."""
    machines = list(extraction.expansion_positions)
    first_x = min(x for x, _ in machines)
    row_y = machines[0][1]
    origin = (first_x - 1.5, row_y + 1.5)
    area = ((first_x - 15, row_y - 15), (extraction.ore_output[0] + 15, row_y + 15))
    substation_position = (first_x - 6, row_y)
    emit(f"reusing completed adjacent {extraction.ore} expansion row")
    bring_stage_up(
        client, bridge, surface, force, f"expanded mine for {extraction.ore}",
        origin, area, substation_position, machines, emit,
    )


def _service_legacy_mine(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    extraction, ore_output: Point, emit: Callable[[str], None],
) -> None:
    """Service a mine built before side taps, upgrading its terminal chest."""
    if extraction.shared_belt_y == extraction.ore_output[1]:
        emit(
            f"upgrading legacy {extraction.ore} terminal chest at "
            f"{extraction.ore_output} to a side tap"
        )
        tap_plan, ore_output = legacy_output_tap_plan(
            extraction.ore_output, extraction.expansion_step,
            belt_type=_DEFAULT_BELT, inserter_type="inserter",
        )
        tap_plan["surface"], tap_plan["force"] = surface, force
        _submit(
            client, bridge, surface, tap_plan,
            f"mine_output_tap_{extraction.ore}", emit,
        )
    emit(f"reusing existing {extraction.ore} mine at {ore_output}")
    origin, area, substation_position, machines = existing_mine_service_geometry(
        extraction.ore_output, extraction.row_drill_count or extraction.drill_count,
        extraction.expansion_step, shared_belt_y=extraction.shared_belt_y,
    )
    bring_stage_up(
        client, bridge, surface, force, f"existing mine for {extraction.ore}",
        origin, area, substation_position, machines, emit,
        logistic_chest_positions=[ore_output],
    )


def _submit_mining_plan(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    extraction, ore_output: Point, emit: Callable[[str], None],
) -> None:
    """Place a freshly planned mine and work it up, or service an existing one."""
    if extraction.build_plan is not None:
        _place_new_mine(client, bridge, surface, force, extraction, emit)
    elif extraction.expansion_positions:
        _reuse_expansion_row(client, bridge, surface, force, extraction, emit)
    else:
        _service_legacy_mine(
            client, bridge, surface, force, extraction, ore_output, emit,
        )


def build_mining_stage(
    client: RconClient, bridge: GameBridge, surface: str, force: str, recipe: str,
    reference_point: Point, emit: Callable[[str], None], *, expand: bool = False,
) -> Point:
    """Build ore-only extraction, then an independent off-ore smelting stage."""
    ore = LINE_RECIPES[recipe]["ingredients"][0]
    try:
        retire_depleted_mines(
            client, bridge, surface, force, ore, reference_point, emit,
        )
    except RuntimeError as error:
        raise StuckError(str(error)) from error
    try:
        extraction = plan_local_extraction(
            client, surface, force, recipe, reference_point, 3,
            belt_type=_DEFAULT_BELT, inserter_type=_DEFAULT_INSERTER,
            reuse_existing=not expand,
            belt_stock=live_base.available_items(
                client, surface, force,
            ).get(_DEFAULT_BELT, 0),
        )
    except ValueError as error:
        raise StuckError(str(error)) from error
    if expand and extraction.build_plan is not None:
        emit(
            f"MINING SYSTEM PHASE: {extraction.system_drill_count_before} -> "
            f"{extraction.system_drill_target} total {extraction.ore} drills; "
            f"building {extraction.drill_count} drill(s) in this batch"
        )
        phase_names = {phase["name"] for phase in extraction.build_plan["phases"]}
        if "shared_belt_batch" in phase_names:
            emit("MINING CAPACITY: filling the current reserved corridor in one batch")
        elif "direct_mine_output" in phase_names:
            emit("MINING CAPACITY: opening the next mine at the current system phase")
    emit(
        f"{extraction.drill_count} drill(s) feed {extraction.furnace_count} "
        f"separate {recipe} furnace(s); mining productivity "
        f"is +{extraction.mining_productivity_bonus:.0%}"
    )
    ore_output = extraction.ore_output
    emit(
        f"{recipe} refinery flow is {extraction.smelter_flow_direction}bound: "
        f"input faces mine output {ore_output}; output favors downstream "
        f"reference {reference_point}"
    )

    _submit_mining_plan(
        client, bridge, surface, force, extraction, ore_output, emit,
    )
    conversion_args = dict(
        placement_origin=extraction.smelter_origin,
        machine_count=extraction.furnace_count,
        max_belt_route_tiles=int(LOCAL_MODE_MAX_LINK_TILES),
        flow_direction=extraction.smelter_flow_direction,
    )
    # Try every belt tier before giving up on belts. A bot-fed smelter is a
    # last resort for a BASIC plate -- it is far slower than a belt and the
    # bots are needed elsewhere -- so being short of one tier must never cost
    # the line its belts while a cheaper tier is stocked. One run dropped iron
    # to bot feeding purely because fast-transport-belt was short, with the
    # plain belt sitting on a 200-unit mall target.
    stock = live_base.available_items(client, surface, force)
    tiers = [_DEFAULT_BELT] + [
        tier for tier in _BELT_TIERS_CHEAPEST_FIRST
        if tier != _DEFAULT_BELT and stock.get(tier, 0)
    ]
    shortage: MaterialShortage | None = None
    for tier in tiers:
        try:
            return build_conversion_stage(
                client, bridge, surface, force, recipe,
                {extraction.ore: ore_output}, reference_point, emit,
                belt_type=tier, **conversion_args,
            )
        except MaterialShortage as short_of:
            if not any(belt in short_of.required for belt in _BELT_TIERS_CHEAPEST_FIRST):
                raise
            shortage = short_of
            emit(f"  BELT TIER: {recipe} cannot afford {tier}; trying the next tier")
    if not _mineable(recipe):
        raise shortage
    emit(
        f"  BOOTSTRAP: no belt tier can be afforded for {recipe}; building a "
        "beltless logistic smelter, to be replaced once belts exist"
    )
    return build_logistic_smelter(
        client, bridge, surface, force, recipe, extraction.ore,
        extraction.smelter_origin, ore_output, emit,
    )


def build_logistic_smelter(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, ore: str, origin: Point, ore_output: Point,
    emit: Callable[[str], None],
) -> Point:
    """Build the first plate line without depending on belt production."""
    ox, oy = round(origin[0]), round(origin[1])
    plan = generate_logistic_smelter(recipe, ore, (ox, oy))
    plan["surface"], plan["force"] = surface, force
    machines = [
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action["entity"] == "electric-furnace"
    ]
    providers = [
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action["entity"] == "passive-provider-chest"
    ]
    substation = (ox - 4.0, oy + 3.5)
    _submit(client, bridge, surface, plan, f"logistic_{recipe}_bootstrap", emit)
    area = ((ox - 10, oy - 10), (ox + 10, oy + 12))
    bring_stage_up(
        client, bridge, surface, force, f"logistic bootstrap for {recipe}",
        (ox, oy), area, substation, machines, emit,
        logistic_chest_positions=[ore_output, *providers],
    )
    stuck = _diagnose_machines(
        client, surface, machines, emit, bridge=bridge, force=force,
    )
    if stuck:
        raise StuckError(f"logistic bootstrap for {recipe} built but not healthy: {stuck}")
    return providers[-1]


def _conversion_origin(
    client: RconClient, surface: str, force: str, recipe: str,
    planner: LocalLayoutPlanner, machine_count: int, reference_point: Point,
    placement_origin: Point | None, width: int, belt_type: str,
    inserter_type: str, flow_direction: str, emit: Callable[[str], None],
) -> tuple[float, float]:
    """Find clear ground for this line, or take the origin the caller fixed."""
    if placement_origin is None:
        # Reserve the footprint the layout ACTUALLY occupies, not a box starting
        # at its origin. A line puts its feed chests and input-belt extension at
        # negative offsets, so ~7 tiles of every stage sit WEST of the origin --
        # land that was never checked, because the clear-area search treated the
        # origin as the box's left edge. Observed live: a science stage placed at
        # (-37,44) reached to x=-44.5 and put a belt ghost in a lake, where bots
        # accept it and can never build it.
        probe = planner.generate_line_layout(
            recipe, machine_count, 0, 0,
            belt_type=belt_type, inserter_type=inserter_type,
            feed_style="chest", terminal_collector=True,
            flow_direction=flow_direction,
        )
        spots = [
            (action["position"]["x"], action["position"]["y"])
            for phase in probe["phases"] for action in phase["actions"]
        ]
        west = math.ceil(-min(x for x, _ in spots)) + 1
        east = math.ceil(max(x for x, _ in spots)) + 1
        north = math.ceil(-min(y for _, y in spots)) + 1
        south = math.ceil(max(y for _, y in spots)) + 1
        area = live_base.find_clear_area(
            client, surface, reference_point, west + east, north + south,
            avoid_resources=True, resource_clearance=5,
        )
        if area is None:
            raise StuckError(
                f"No clear space found near {reference_point} for the {recipe} stage"
            )
        ox, oy = round(area[0]) + west, round(area[1]) + north
    else:
        ox, oy = round(placement_origin[0]), round(placement_origin[1])
    return ox, oy


def _conversion_feed_plan(
    client: RconClient, bridge: GameBridge, surface: str, force: str, recipe: str,
    plan: dict, ingredient_sources: dict[str, Point], machine_count: int,
    belt_type: str, flow_direction: str, emit: Callable[[str], None], *,
    allow_logistic_inputs: bool, max_belt_route_tiles: int | None,
) -> tuple[dict, dict, dict, bool]:
    """Decide how each ingredient reaches this line, and preflight the routes.

    Preflighting BEFORE the stage is submitted is the point: a route that
    cannot be built should stop the plan while nothing has been placed,
    rather than after a half-connected line is already on the ground.
    """
    # Belt or bots is a THROUGHPUT question, and _transport_mode already answers
    # it per ingredient. Hardcoding "belt" here overrode that: a science stage
    # drawing 0.30 copper-plate/s -- a tenth of what bots carry -- demanded a
    # 128-tile belt corridor, could not afford any belt tier, and killed the run.
    # allow_logistic_inputs still forces bots for callers that must avoid belts.
    modes = {
        ingredient: (
            "logistic" if allow_logistic_inputs
            else _transport_mode(recipe, ingredient, machine_count)
        )
        for ingredient in LINE_RECIPES[recipe]["ingredients"]
    }
    direct_belt_input = (
        recipe in {"iron-plate", "copper-plate"}
        and len(modes) == 1
        and next(iter(modes.values())) == "belt"
    )
    if direct_belt_input:
        ingredient = next(iter(modes))
        feed_positions = {
            ingredient: _direct_single_belt_feed(
                plan, ingredient, flow_direction,
            )
        }
    else:
        feed_positions = _swap_infinity_chests(plan, modes)
    unresolved = sorted(set(feed_positions) - set(ingredient_sources))
    if unresolved:
        raise StuckError(
            f"{recipe} feeds on {unresolved}, which have no producing stage to supply them"
        )
    preflighted: dict[str, tuple[list[dict], str]] = {}
    if max_belt_route_tiles is not None:
        planned_blocked = planned_footprint_tiles(plan)
        for ingredient, feed_position in sorted(feed_positions.items()):
            route = preflight_ingredient_transport(
                client, surface, force, recipe, ingredient,
                ingredient_sources[ingredient], feed_position, machine_count,
                max_belt_route_tiles=max_belt_route_tiles,
                additional_blocked=planned_blocked,
                mode=modes[ingredient],
                destination_is_belt=direct_belt_input,
                destination_belt_direction=flow_direction,
            )
            if route is not None:
                preflighted[ingredient] = route
                plan["phases"].append({
                    "name": f"bridge_{ingredient}_to_{recipe}",
                    "actions": route[0],
                })
    return modes, feed_positions, preflighted, direct_belt_input


def _power_and_raise_stage(
    client: RconClient, bridge: GameBridge, surface: str, force: str, recipe: str,
    plan: dict, machine_positions: list, substation_position: Point,
    ingredient_sources: dict[str, Point], modes: dict, machine_count: int,
    ox: float, oy: float, emit: Callable[[str], None],
) -> None:
    """Connect any stranded support entity, then work the stage up."""
    length = machine_count * 3
    stage_area = ((ox - 15, oy - 15), (ox + length + 15, oy + 15))
    # Buffer chests themselves are passive; the inserters that load/unload
    # them are not. Check planned support inserters immediately after submit
    # so a buffer cannot silently starve a stage while its machines are powered.
    support_positions = {
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity", "").endswith("inserter")
    }
    for position in sorted(support_positions):
        if live_base.entity_status_name(client, surface, position) == "no_power":
            emit(f"  support inserter at {position} has no power -- connecting it")
            if not extend_power(client, bridge, surface, force, position, emit):
                raise StuckError(
                    f"support inserter at {position} is unpowered and cannot be "
                    "reached by a pole chain from any generating network"
                )
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


def _connect_stage_feeds(
    client: RconClient, bridge: GameBridge, surface: str, force: str, recipe: str,
    feed_positions: dict, ingredient_sources: dict[str, Point], modes: dict,
    preflighted: dict, machine_positions: list, machine_count: int,
    belt_type: str, inserter_type: str, emit: Callable[[str], None], *,
    max_belt_route_tiles: int | None, direct_belt_input: bool,
) -> None:
    """Run each ingredient in, then confirm the machines are actually fed."""
    feed_delay = 0.0
    for ingredient in sorted(feed_positions):
        feed_position = feed_positions[ingredient]
        source_position = ingredient_sources[ingredient]
        if ingredient in preflighted:
            route_actions, belt_type = preflighted[ingredient]
            belt_tiles = sum(
                1 for action in route_actions if "transport-belt" in action["entity"]
            )
            feed_delay = max(
                feed_delay, transport_grace_seconds(belt_type, belt_tiles)
            )
            continue
        feed_delay = max(
            feed_delay,
            ensure_ingredient_transport(
                client, bridge, surface, force, recipe, ingredient,
                source_position, feed_position, machine_count, emit,
                max_belt_route_tiles=max_belt_route_tiles,
                mode=modes[ingredient],
                # The preflight already approved a belt-to-belt join for this
                # feed. Not telling the BUILD meant it laid a chest-shaped
                # bridge instead, with an inserter in the middle of what should
                # be one continuous belt -- and a different bill of materials
                # from the one that was checked.
                destination_is_belt=direct_belt_input,
            ),
        )
    # A stage cannot possibly run before its first ingredient physically
    # arrives. Judging it healthy-or-not sooner than that reports a false
    # failure on a bridge that is working -- observed live, where a ~60 tile
    # yellow belt needed ~32s and the check gave up at 20s.
    stuck = _diagnose_machines(
        client, surface, machine_positions, emit,
        grace_seconds=feed_delay, bridge=bridge, force=force,
    )
    if stuck:
        # Bridges are the most likely remaining culprit for "built but not
        # fed: direct refinery feeds are belts, not chests, so do not run the
        # chest inventory probe against them.
        for ingredient, feed_position in feed_positions.items():
            if direct_belt_input:
                emit(
                    f"  DIAGNOSIS: {feed_position} input belt for {ingredient} "
                    "received nothing -- the mine-to-refinery belt is disconnected"
                )
                continue
            contents = live_base.chest_contents(client, surface, feed_position)
            if contents.get(ingredient, 0) == 0:
                emit(f"  DIAGNOSIS: {feed_position} feed chest for {ingredient} is empty -- "
                     "the bridge from its source isn't delivering (check the bridge inserters/belt)")
        raise StuckError(f"conversion stage for {recipe} built but not healthy: {stuck}")


def build_conversion_stage(
    client: RconClient, bridge: GameBridge, surface: str, force: str, recipe: str,
    ingredient_sources: dict[str, Point], reference_point: Point, emit: Callable[[str], None],
    *, placement_origin: Point | None = None,
    machine_count: int = _DEFAULT_MACHINE_COUNT,
    max_belt_route_tiles: int | None = None,
    belt_type: str = _DEFAULT_BELT,
    inserter_type: str | None = None,
    allow_logistic_inputs: bool = False,
    flow_direction: str = "east",
    side_tap_output: bool = False,
) -> Point:
    """Assemble `recipe` from its (already-producing) ingredients. Bridges each
    ingredient's real upstream output chest to this stage's real feed chest
    with a belt+inserter pair, using whichever side faces the source."""
    width = machine_count * 3
    if inserter_type is None:
        inserter_type = _stage_inserter_type(
            client, surface, force, recipe, machine_count, belt_type,
            flow_direction, emit,
        )
    planner = LocalLayoutPlanner()
    ox, oy = _conversion_origin(
        client, surface, force, recipe, planner, machine_count,
        reference_point, placement_origin, width, belt_type, inserter_type,
        flow_direction, emit,
    )
    emit(f"conversion stage for {recipe}: building at ({ox},{oy})")
    plan = planner.generate_line_layout(
        recipe, machine_count, ox, oy,
        belt_type=belt_type, inserter_type=inserter_type,
        feed_style="chest", terminal_collector=True,
        flow_direction=flow_direction,
    )
    plan = strip_local_power(plan, remove_substations=False)
    output_position = (
        _side_sample_plate_output(
            plan, (ox, oy), machine_count, belt_type, flow_direction,
            tap_inserter_type=inserter_type,
        )
        if recipe in {"iron-plate", "copper-plate"} or side_tap_output
        else next(
            (action["position"]["x"], action["position"]["y"])
            for phase in plan["phases"] for action in phase["actions"]
            if action.get("entity") == "steel-chest"
        )
    )
    _publish_output_chest(plan)
    modes, feed_positions, preflighted, direct_belt_input = _conversion_feed_plan(
        client, bridge, surface, force, recipe, plan, ingredient_sources,
        machine_count, belt_type, flow_direction, emit,
        allow_logistic_inputs=allow_logistic_inputs,
        max_belt_route_tiles=max_belt_route_tiles,
    )
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
    _power_and_raise_stage(
        client, bridge, surface, force, recipe, plan, machine_positions,
        substation_position, ingredient_sources, modes, machine_count, ox, oy,
        emit,
    )
    _connect_stage_feeds(
        client, bridge, surface, force, recipe, feed_positions,
        ingredient_sources, modes, preflighted, machine_positions,
        machine_count, belt_type, inserter_type, emit,
        max_belt_route_tiles=max_belt_route_tiles,
        direct_belt_input=direct_belt_input,
    )
    return output_position


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


def _paired_mall_provider(
    client: RconClient, surface: str, machine_positions: Sequence[Point],
) -> Point | None:
    """Find the provider assigned to one machine in a paired mall cell."""
    for machine_x, machine_y in machine_positions:
        for requester_dx, provider_dy in ((3, -1), (-3, 1)):
            requester = (machine_x + requester_dx, machine_y)
            requester_entity = live_base.entity_at(client, surface, requester)
            if not requester_entity or requester_entity["name"] != "requester-chest":
                continue
            provider = (requester[0], machine_y + provider_dy)
            provider_entity = live_base.entity_at(client, surface, provider)
            if provider_entity and provider_entity["name"] == "passive-provider-chest":
                return provider
    return None


@dataclass(frozen=True)
class _LinePlan:
    """What one survey concluded about an item before anything is built."""

    existing: object | None
    spec: dict
    mall_stock_target: int
    demand: float
    saturated: bool
    promoted_count: int | None
    promote_to_line: bool
    at_size: bool


def _plan_line(
    client: RconClient, surface: str, force: str, item: str,
    emit: Callable[[str], None], *, upgrade_bootstrap: bool, stock_target: int,
    minimum_machines: int, allow_promotion: bool,
) -> _LinePlan:
    """Survey the item's current line and decide whether it should be promoted."""
    spec = LINE_RECIPES[item]
    mall_stock_target = stock_target
    if not upgrade_bootstrap:
        mall_stock_target += live_base.logistic_request_total(
            client, surface, force, item,
        )
    existing = live_base.find_line(client, surface, force, item, spec["machine"])
    demand = live_intermediate_demand(client, surface, force, item)
    # Every machine busy means this cell cannot go faster, whatever measured
    # demand says -- and measured demand is unreliable here precisely because
    # the consumers this item starves are the ones that would report it.
    saturated = bool(
        existing
        and existing.machine_count > 0
        and existing.working_count >= existing.machine_count
    )
    # How long the cells already built need to finish what is still OUTSTANDING.
    # Saturation on its own is far too weak: a single cell runs flat out
    # whenever it has any work, so it fired while filling a one-off chest and
    # promoted transport-belt to six machines -- and six feed requesters -- for
    # a 200 target one cell covers in about a minute.
    outstanding = max(0, stock_target - live_base.available_items(
        client, surface, force,
    ).get(item, 0))
    backlog = backlog_seconds(
        item, outstanding, existing.machine_count if existing else 0,
    )
    promoted_count = promoted_line_machine_count(
        item, demand, existing.machine_count if existing else 0, saturated=saturated,
        backlog=backlog,
    )
    # Production prep asks for a standing number of MALL cells and must be
    # allowed to finish. Promotion outranking it turned "copper-cable to 2
    # machines" into a 6-machine dedicated line on the second pass, which then
    # demanded 9.00/s of plate belted across the base. A dedicated line is for
    # when the planner later judges the mall cells saturated in real service.
    promote_to_line = allow_promotion and promoted_count is not None and (
        existing is None or existing.machine_count < promoted_count
    )
    if promote_to_line:
        why = (
            f"all {existing.machine_count} machine(s) flat out with "
            f"{outstanding} still to make ({backlog:.0f}s of backlog)"
            if saturated and existing else f"demand is {demand:.2f}/s"
        )
        emit(
            f"  INTERMEDIATE PROMOTION: {item} -- {why}; "
            f"building a shared {promoted_count}-machine line instead of another mall cell"
        )
    return _LinePlan(
        existing=existing, spec=spec, mall_stock_target=mall_stock_target,
        demand=demand, saturated=saturated, promoted_count=promoted_count,
        promote_to_line=promote_to_line,
        at_size=existing is None or existing.machine_count >= minimum_machines,
    )


def _refresh_mall_cell(
    client: RconClient, bridge: GameBridge, surface: str, force: str, item: str,
    plan: _LinePlan, emit: Callable[[str], None], *, upgrade_bootstrap: bool,
) -> Point | None:
    """Re-apply a live mall cell's request group and provider limit.

    Returns the paired provider position, which the caller needs both to serve
    the cell's output and to retire it once a shared line replaces it.
    """
    existing, spec = plan.existing, plan.spec
    mall_stock_target = plan.mall_stock_target
    mall_provider: Point | None = None
    if existing and not upgrade_bootstrap and len(existing.machine_positions) == 1:
        machine_position = existing.machine_positions[0]
        requester_position = (machine_position[0] - 3, machine_position[1])
        requester = live_base.entity_at(client, surface, requester_position)
        paired_companion = live_base.entity_at(
            client, surface, (machine_position[0] - 6, machine_position[1]),
        )
        if (
            requester and requester["name"] == "requester-chest"
            and paired_companion is None
        ):
            request_plan = generate_compact_mall_request_update(
                item, spec["ingredients"], spec["amounts"], machine_position,
                stock_target=mall_stock_target,
                product_amount=spec.get("product_amount", 1),
            )
            request_plan["surface"], request_plan["force"] = surface, force
            _submit(
                client, bridge, surface, request_plan,
                f"compact_mall_requests_{item}", emit,
            )

    if existing and not upgrade_bootstrap:
        mall_provider = _paired_mall_provider(
            client, surface, existing.machine_positions,
        )
        if mall_provider is not None:
            emit(
                f"  MALL BUFFER: {item} provider at {mall_provider} "
                f"tracks combined demand {mall_stock_target}"
            )
            limit_plan = generate_mall_provider_limit_update(
                item, mall_provider, mall_stock_target,
            )
            limit_plan["surface"], limit_plan["force"] = surface, force
            _submit(
                client, bridge, surface, limit_plan,
                f"mall_provider_limit_{item}", emit,
            )
    return mall_provider


def _serve_healthy_line(
    client: RconClient, bridge: GameBridge, surface: str, force: str, item: str,
    reference_point: Point, emit: Callable[[str], None], plan: _LinePlan,
    mall_provider: Point | None, *, upgrade_bootstrap: bool,
) -> Point | None:
    """Hand back a working line's output chest, upgrading it off logistics first.

    Returns None when it did structural work instead, so the caller re-surveys.
    """
    existing = plan.existing
    origin = logistic_smelter_origin(existing.machine_positions)
    requester = (
        live_base.entity_at(client, surface, (origin[0] + 1.5, origin[1] + 3.5))
        if origin is not None else None
    )
    if (
        upgrade_bootstrap and item in {"iron-plate", "copper-plate"}
        and requester and requester["name"] == "requester-chest"
    ):
        extraction = plan_local_extraction(
            client, surface, force, item, reference_point, 3,
            belt_type=_DEFAULT_BELT, inserter_type=_DEFAULT_INSERTER,
            reuse_existing=True,
            belt_stock=live_base.available_items(
                client, surface, force,
            ).get(_DEFAULT_BELT, 0),
        )
        emit(f"  BOOTSTRAP UPGRADE: replacing requester-fed {item} with belt transport")
        build_conversion_stage(
            client, bridge, surface, force, item,
            {extraction.ore: extraction.ore_output}, reference_point, emit,
            machine_count=extraction.furnace_count,
            max_belt_route_tiles=int(LOCAL_MODE_MAX_LINK_TILES),
        )
        retirement = retire_logistic_smelter_plan(item, extraction.ore, origin)
        retirement["surface"], retirement["force"] = surface, force
        _submit(client, bridge, surface, retirement, f"retire_logistic_{item}", emit)
        return None
    chest = live_base.nearest_container(
        client, surface, force, existing.machine_positions[-1],
        names=("passive-provider-chest",),
    )
    if mall_provider is not None:
        chest = mall_provider
    if chest is not None:
        output_area = ((chest[0] - 3, chest[1] - 3), (chest[0] + 3, chest[1] + 3))
        for position in live_base.unpowered_entities(client, surface, output_area):
            emit(f"  existing {item} output entity at {position} is unpowered -- connecting it")
            if not extend_power(client, bridge, surface, force, position, emit):
                raise StuckError(
                    f"existing {item} output entity at {position} cannot be powered"
                )
    return chest or existing.output_position


def _repair_stalled_line(
    client: RconClient, bridge: GameBridge, surface: str, force: str, item: str,
    reference_point: Point, emit: Callable[[str], None], plan: _LinePlan, *,
    upgrade_bootstrap: bool,
) -> Point | None:
    """Bring an existing but idle line back up rather than building a second one."""
    existing = plan.existing
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
    statuses = live_base.entity_statuses(client, surface, existing.machine_positions)
    if statuses and all(
        status == "item_ingredient_shortage" for status in statuses.values()
    ) and not _mineable(item):
        if not upgrade_bootstrap:
            chest = live_base.nearest_container(
                client, surface, force, existing.machine_positions[-1],
                names=("passive-provider-chest",),
            )
            emit(
                f"  MALL WAIT: existing {item} line is supply-starved; "
                "keeping its current transport while bootstrap production catches up"
            )
            return chest or existing.output_position
        repaired = repair_existing_ingredient_transport(
            client, bridge, surface, force, item, existing.machine_positions,
            lambda ingredient: ensure_produced(
                client, bridge, surface, force, ingredient, reference_point, emit,
                upgrade_bootstrap=upgrade_bootstrap,
            ),
            emit,
        )
        if repaired:
            return None
    return None


# Ingredients a mall cell drew from stock while NOTHING was producing them.
# Stock is a buffer, not a supply: a draw with no line behind it is the player's
# starter chest being eaten, and the run stalls the moment it runs out. Recorded
# rather than refused -- refusing deadlocks, because the mall must be able to
# build the very assemblers the producing lines are made of.
UNBACKED_DRAWS: set[str] = set()


def _has_producer(
    client: RconClient, surface: str, force: str, ingredient: str,
) -> bool:
    """Whether anything on the base is actually making `ingredient`."""
    if ingredient not in LINE_RECIPES:
        return True  # mined or externally supplied; not ours to produce
    line = live_base.find_line(
        client, surface, force, ingredient, LINE_RECIPES[ingredient]["machine"],
    )
    return line is not None and line.machine_count > 0


def _ingredient_sources(
    client: RconClient, bridge: GameBridge, surface: str, force: str, item: str,
    reference_point: Point, emit: Callable[[str], None], plan: _LinePlan, *,
    upgrade_bootstrap: bool,
) -> dict[str, Point] | None:
    """Find a real source for every input this stage needs.

    Returns None when it built something upstream instead, so the caller stops
    and re-surveys. A bootstrap MALL CELL may draw an input from stock rather
    than from a position; a promoted line may not, because it is belt-fed.
    """
    spec, promote_to_line = plan.spec, plan.promote_to_line
    sources: dict[str, Point] = {}
    stocked = (
        live_base.available_items(client, surface, force)
        if may_consume_stocked_inputs(
            upgrade_bootstrap=upgrade_bootstrap, promote_to_line=promote_to_line,
        ) else {}
    )
    crafts_needed = math.ceil(
        plan.mall_stock_target / max(1, spec.get("product_amount", 1)),
    )
    for ingredient, amount in zip(spec["ingredients"], spec["amounts"], strict=True):
        required = math.ceil(amount * crafts_needed)
        if not upgrade_bootstrap and stocked.get(ingredient, 0) >= required:
            backed = _has_producer(client, surface, force, ingredient)
            emit(
                f"  MALL BOOTSTRAP: using stocked {ingredient} "
                f"({stocked[ingredient]}/{required}) for {item}"
                + ("" if backed else " -- NOTHING IS PRODUCING IT")
            )
            if not backed:
                UNBACKED_DRAWS.add(ingredient)
            elif ingredient in UNBACKED_DRAWS:
                UNBACKED_DRAWS.discard(ingredient)
            continue
        if ingredient not in LINE_RECIPES:
            raise StuckError(f"{item} needs {ingredient!r}, which has no recipe and isn't mineable")
        position = ensure_produced(
            client, bridge, surface, force, ingredient, reference_point, emit,
            upgrade_bootstrap=upgrade_bootstrap,
        )
        if position is None:
            return None  # built something upstream this round; re-survey next loop
        sources[ingredient] = position
    return sources


def _build_assembled_stage(
    client: RconClient, bridge: GameBridge, surface: str, force: str, item: str,
    reference_point: Point, emit: Callable[[str], None], plan: _LinePlan,
    mall_provider: Point | None, *, upgrade_bootstrap: bool,
    gate_on_stock: bool = False, stock_target: int = 1,
) -> None:
    """Build one stage for an assembled item, once its inputs have sources."""
    sources = _ingredient_sources(
        client, bridge, surface, force, item, reference_point, emit, plan,
        upgrade_bootstrap=upgrade_bootstrap,
    )
    if sources is None:
        return
    existing, spec = plan.existing, plan.spec
    promote_to_line, promoted_count = plan.promote_to_line, plan.promoted_count
    mall_stock_target = plan.mall_stock_target
    if promote_to_line:
        # Site the line beside the input it eats most of, not beside the
        # mall. A 6-machine copper-cable line placed at the mall needed
        # 9.00/s of plate belted ~90 tiles from the mine and died on
        # "Belt route needs a 25-tile tunnel". Building next to the source
        # keeps that run short instead of routing it across the base.
        line_reference = (
            _heaviest_source(item, sources, promoted_count) or reference_point
        )
        if line_reference != reference_point:
            emit(
                f"  LINE SITING: placing the {item} line near its heaviest "
                f"input at {line_reference} rather than the mall"
            )
        build_conversion_stage(
            client, bridge, surface, force, item, sources, line_reference, emit,
            machine_count=promoted_count,
            belt_type=promoted_line_belt_type(
                item, promoted_count, live_base.available_items(client, surface, force),
            ),
            inserter_type="fast-inserter",
            allow_logistic_inputs=False,
            side_tap_output=True,
        )
        # Retire the old cell only now the line that replaces it exists.
        # Retiring first meant a pass that did not finish the line left the
        # recipe with no machine at all, so the next survey rebuilt the very
        # cell just removed -- seen twice in fifteen seconds at cell (35,31).
        if mall_provider is not None and existing and len(existing.machine_positions) == 1:
            retirement = generate_promoted_mall_retirement_plan(
                item, spec["machine"], existing.machine_positions[0], mall_provider,
            )
            retirement["surface"], retirement["force"] = surface, force
            emit(
                f"  MALL RETIRE: the {item} line is up; removing the old cell "
                "and clearing its request group"
            )
            _submit(
                client, bridge, surface, retirement,
                f"retire_promoted_mall_{item}", emit,
            )
    elif not upgrade_bootstrap:
        build_compact_mall_stage(
            client, bridge, surface, force, item, sources, reference_point,
            bring_stage_up, emit, stock_target=mall_stock_target,
        )
    else:
        build_conversion_stage(
            client, bridge, surface, force, item, sources, reference_point, emit,
            allow_logistic_inputs=not upgrade_bootstrap,
        )
    return None


def ensure_produced(
    client: RconClient, bridge: GameBridge, surface: str, force: str, item: str,
    reference_point: Point, emit: Callable[[str], None], *,
    upgrade_bootstrap: bool = True, stock_target: int = 1,
    minimum_machines: int = 1, allow_promotion: bool = True,
    gate_on_stock: bool = False, stock_buffer: int | None = None,
) -> Point | None:
    """Returns the item's real output chest position if it's already producing;
    otherwise builds exactly ONE missing stage (the deepest unmet ingredient
    first) and returns None so the caller re-surveys and calls again."""
    if item == "coal":
        return ensure_coal_mine(
            client, bridge, surface, force, reference_point, bring_stage_up, emit,
        )
    if item in {"plastic-bar", "sulfur"}:
        outputs = ensure_oil_cell(
            client, bridge, surface, force, reference_point,
            lambda ingredient: ensure_produced(
                client, bridge, surface, force, ingredient, reference_point, emit,
                upgrade_bootstrap=upgrade_bootstrap,
            ),
            bring_stage_up, emit,
        )
        return outputs[item] if outputs else None
    if item not in LINE_RECIPES:
        raise StuckError(f"No recipe knowledge for {item!r} -- add it to planners/recipe_data.py "
                          "before asking the builder to produce it")
    plan = _plan_line(
        client, surface, force, item, emit,
        upgrade_bootstrap=upgrade_bootstrap, stock_target=stock_target,
        minimum_machines=minimum_machines, allow_promotion=allow_promotion,
    )
    mall_provider = _refresh_mall_cell(
        client, bridge, surface, force, item, plan, emit,
        upgrade_bootstrap=upgrade_bootstrap,
    )
    existing = plan.existing
    # An UNDER-SIZED line falls through to the build path: production prep asks
    # for a standing number of machines, and a line that exists with fewer than
    # that is not finished being built.
    if existing and plan.at_size and not plan.promote_to_line:
        if existing.working_count > 0:
            return _serve_healthy_line(
                client, bridge, surface, force, item, reference_point, emit,
                plan, mall_provider, upgrade_bootstrap=upgrade_bootstrap,
            )
        return _repair_stalled_line(
            client, bridge, surface, force, item, reference_point, emit, plan,
            upgrade_bootstrap=upgrade_bootstrap,
        )
    if not _mineable(item):
        _build_assembled_stage(
            client, bridge, surface, force, item, reference_point, emit, plan,
            mall_provider, upgrade_bootstrap=upgrade_bootstrap,
            gate_on_stock=gate_on_stock,
            stock_target=stock_buffer if stock_buffer else stock_target,
        )
        return None
    build_mining_stage(client, bridge, surface, force, item, reference_point, emit)
    return None
def _serve_mall_task(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    task, tick: int, mall_targets: dict[str, int], priorities: PriorityList,
    reference_point: Point, emit: Callable[[str], None],
) -> None:
    """Work the highest-rated construction task for one pass.

    Always spends the pass: the caller re-surveys afterwards either way.
    A shortage becomes a mall target rather than an error, which is how one
    bottleneck is pushed back a step onto the thing that actually limits it.
    """
    item, target = task.item, task.target
    emit(priorities.describe(task, tick))
    if item not in LINE_RECIPES:
        raise StuckError(
            f"Parts mall needs {target} {item}, but no executable recipe "
            "knowledge exists for that construction item"
        )
    emit(f"--- parts mall: ensuring {item} production for stock target {target} ---")
    try:
        buffer = stock_buffer_for(client, surface, force, item, target)
        if buffer > target:
            emit(
                f"  STOCK BUFFER: {item} keeps making up to {buffer} once the "
                f"{target} this mission needs is covered"
            )
        output = ensure_produced(
            client, bridge, surface, force, item, reference_point, emit,
            upgrade_bootstrap=False, stock_target=target,
            # A construction target is a real count of finished goods, so the
            # cell may stop itself once the network holds that many. Prep does
            # not pass this: its "target" is a machine count, and gating an
            # intermediate on one stops every line behind it.
            gate_on_stock=True, stock_buffer=buffer,
        )
    except MaterialShortage as shortage:
        # SAY SO. This was silent, so a stage that failed half-built looked
        # identical in the log to one nobody had started -- the iron-plate belt
        # for a transport-belt line simply never appeared, with no line
        # explaining why.
        add_demands(mall_targets, shortage)
        emit(
            f"  MALL DEMAND: {shortage.stage} needs "
            + ", ".join(
                f"{name}={count}" for name, count in sorted(shortage.required.items())
            )
            + " -- queued; this stage resumes once the mall has them"
        )
        return
    if output is not None:
        def expand_upstream() -> bool:
            upstream = expansion_target(
                item, live_base.available_items(client, surface, force),
            )
            if upstream is None:
                reason = f"{item} has no supported extraction input to expand"
                priorities.defer(item, live_base.game_tick(client), reason)
                emit(f"  PRIORITY DEFERRED: {reason}")
                return False
            if upstream != item:
                emit(f"  MALL BOTTLENECK: {item} traces down to {upstream}")
            emit(f"  MALL EXPAND: considering another {upstream} extraction phase")
            try:
                build_mining_stage(
                    client, bridge, surface, force, upstream,
                    reference_point, emit, expand=True,
                )
            except StuckError as error:
                current_tick = live_base.game_tick(client)
                priorities.defer(item, current_tick, str(error))
                emit(
                    f"  PRIORITY DEFERRED: {item}; {error}; "
                    "reserved corridor remains bookmarked, nothing was submitted"
                )
                return False
            return True

        try:
            ready = wait_for_stock(
                client, surface, force, item, target, emit,
                on_stalled=expand_upstream,
            )
        except MaterialShortage as shortage:
            add_demands(mall_targets, shortage)
            return
        if not ready:
            return
        priorities.complete(item, live_base.game_tick(client))
        mall_targets.pop(item, None)
    return


def _prep_plate_extraction(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    short_plate: str, prepped: set[str], mall_targets: dict[str, int],
    reference_point: Point, emit: Callable[[str], None],
) -> bool:
    """Grow one plate line to the furnace count its own prep draw implies.

    Returns whether it spent the pass. A material shortage hands the pass back
    so the mall can build the drills it is short of -- prep runs before the mall
    now, so holding on would re-hit the identical shortage forever.
    """
    wanted_furnaces = baseline_smelter_count(short_plate)
    plate_line = live_base.find_line(
        client, surface, force, short_plate,
        LINE_RECIPES[short_plate]["machine"],
    )
    have = plate_line.machine_count if plate_line else 0
    if have >= wanted_furnaces:
        prepped.add(short_plate)
        emit(
            f"  PREP READY: {short_plate} has {have}/{wanted_furnaces} furnace(s)"
        )
        return True
    emit(
        f"--- production prep: {short_plate} extraction to "
        f"{wanted_furnaces} furnace(s) for "
        f"{baseline_plate_draw()[short_plate]:.2f}/s "
        f"(have {have}, drill phase {baseline_drill_phase(short_plate)}) ---"
    )
    try:
        build_mining_stage(
            client, bridge, surface, force, short_plate,
            reference_point, emit, expand=plate_line is not None,
        )
    except MaterialShortage as shortage:
        # Raising a drill phase needs drills, and drills come from
        # the mall. Push the shortfall back as a mall target instead
        # of dying on it: prep is the first thing that ever asks for
        # 14 drills at once, so it is also the first thing to find
        # the base holding 8. Every other path in this loop already
        # does this -- omitting it here ended a run outright.
        add_demands(mall_targets, shortage)
        emit(
            f"  PREP DEMAND: {short_plate} extraction needs "
            + ", ".join(
                f"{item}={target}"
                for item, target in sorted(shortage.required.items())
            )
            + " -- queued for the mall"
        )
        return False
    except (StuckError, ValueError) as error:
        prepped.add(short_plate)
        emit(
            f"  PREP DEFERRED: {short_plate} extraction stays at {have} "
            f"furnace(s) -- {error}"
        )
    return True


def _prep_intermediate(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    prepped: set[str], mall_targets: dict[str, int], reference_point: Point,
    emit: Callable[[str], None],
) -> bool:
    """Fill the next standing mall cell in the prep set, deepest feeder first.

    Returns whether it spent the pass; False means the prep set is complete
    and the caller should move on to the goal item.
    """
    pending = [r for r in baseline_build_order() if r not in prepped]
    if pending:
        recipe = pending[0]
        wanted = BASELINE_MACHINES[recipe]
        line = live_base.find_line(
            client, surface, force, recipe, LINE_RECIPES[recipe]["machine"],
        )
        if line is not None and line.machine_count >= wanted:
            prepped.add(recipe)
            emit(f"  PREP READY: {recipe} has {line.machine_count}/{wanted} machine(s)")
            return True
        have = line.machine_count if line else 0
        emit(
            f"--- production prep: {recipe} to {wanted} machine(s) "
            f"(have {have}) ---"
        )
        try:
            ensure_produced(
                client, bridge, surface, force, recipe, reference_point, emit,
                upgrade_bootstrap=False, stock_target=wanted,
                minimum_machines=wanted, allow_promotion=False,
            )
        except MaterialShortage as shortage:
            # Yield the pass to the mall rather than keeping it. Prep runs
            # FIRST, so holding on here would re-hit the identical shortage
            # every pass and never reach the mall block that builds the missing
            # part -- a spin the livelock guard would eventually abort.
            add_demands(mall_targets, shortage)
            emit(
                f"  PREP BLOCKED: {recipe} needs "
                + ", ".join(
                    f"{item}={target}"
                    for item, target in sorted(shortage.required.items())
                )
                + " -- handing the pass to the mall"
            )
            return False
        return True  # built one stage (or all of it); re-survey and carry on
    return False


# `None` already means "built one stage, re-survey", so a shortage needs its own
# answer -- it must NOT advance the iteration count, since nothing was tried.
_SHORTAGE = object()


def _advance_the_goal(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    goal_item: str, mall_targets: dict[str, int], reference_point: Point,
    emit: Callable[[str], None],
) -> Point | None | object:
    """One attempt at the goal item; a shortage becomes mall demand instead."""
    try:
        return ensure_produced(
            client, bridge, surface, force, goal_item, reference_point, emit,
        )
    except MaterialShortage as shortage:
        add_demands(mall_targets, shortage)
        emit(
            f"  MALL DEMAND: {shortage.stage} requested "
            + ", ".join(
                f"{item}={target}"
                for item, target in sorted(shortage.required.items())
            )
        )
        return _SHORTAGE


def _live_output_rate(
    client: RconClient, surface: str, force: str, item: str,
) -> float:
    """What the base currently makes of `item`, in items per second.

    Zero when nothing produces it, which is the honest answer and the one that
    keeps a scarce item on its opening figure.
    """
    spec = LINE_RECIPES.get(item)
    if spec is None or spec["machine"] not in MACHINE_SPEEDS:
        return 0.0
    line = live_base.find_line(client, surface, force, item, spec["machine"])
    if line is None or line.machine_count <= 0:
        return 0.0
    return (
        line.machine_count
        * MACHINE_SPEEDS[spec["machine"]]
        / spec["craft_time"]
        * spec.get("product_amount", 1)
    )


def stock_buffer_for(
    client: RconClient, surface: str, force: str, item: str, target: int,
) -> int:
    """How much of `item` a cell may keep making, once the mission is covered.

    A BUFFER, never a requirement -- and one the base has to EARN. It is
    BUFFER_SECONDS of the item's own live output, so a base that cannot make
    something quickly does not stockpile it, and the plates go to whatever
    would make more instead. Raising the mall TARGET to a flat figure did the
    opposite: 4800 belts became a gate, and the loop sat in wait_for_stock
    expanding iron every sixty seconds to reach a number, while the research it
    was launched for never started.
    """
    if item not in BULK_CONSTRUCTION_ITEMS:
        return target
    return standing_target(
        item, target,
        production_rate=_live_output_rate(client, surface, force, item),
        stack_sizes=ITEM_STACK_SIZES,
    )


def _survey_pass(
    client: RconClient, surface: str, force: str, mall_targets: dict[str, int],
    priorities: PriorityList,
) -> tuple[int, object | None]:
    """Read the base, retire targets stock already covers, pick the next task.

    Targets are dropped as soon as stock meets them so the priority list does
    not keep re-selecting work the base already finished while a later pass was
    running.
    """
    stock = live_base.available_items(client, surface, force)
    tick = live_base.game_tick(client)
    priorities.sync(mall_targets, stock, tick)
    for stocked_item, stocked_target in list(mall_targets.items()):
        if stock.get(stocked_item, 0) >= stocked_target:
            priorities.complete(stocked_item, tick)
            mall_targets.pop(stocked_item)
    return tick, priorities.next(mall_targets, tick)


def _pass_signature(
    task, mall_targets: dict[str, int], prepped: set[str],
) -> tuple:
    """What this pass chose to work on, and what work is still outstanding.

    Two identical signatures in a row mean the pass changed nothing, which is
    the only reliable livelock signal here: `iteration` advances on goal work
    alone, so every mall or prep `continue` skipped it and max_iterations never
    bounded anything.
    """
    return (
        task.item if task else None,
        task.progress_percent if task else None,
        tuple(sorted(mall_targets)),
        tuple(sorted(prepped)),
    )


def _refuse_to_spin(unchanged_passes: int, signature: tuple, goal_item: str) -> None:
    """Stop once repeating has stopped telling us anything new."""
    if unchanged_passes < _MAX_UNCHANGED_PASSES:
        return
    unbacked = (
        " Nothing is producing " + ", ".join(sorted(UNBACKED_DRAWS))
        + ", which the mall has been drawing from stock -- those lines have to "
        "exist before the buffer runs out." if UNBACKED_DRAWS else ""
    )
    raise StuckError(
        f"No progress in {unchanged_passes} passes: "
        f"{signature[0] or goal_item} has been at "
        f"{signature[1]}% with the same outstanding work each time. "
        "Something it needs cannot be built, and retrying is not "
        f"finding it -- see the repeated reason above.{unbacked}"
    )


def _open_the_run(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    goal_item: str, mission_items: tuple[str, ...], script_output: Path | str,
    emit: Callable[[str], None],
) -> tuple[dict[str, int], PriorityList]:
    """Learn the force's real recipes, then announce what this run is aiming at.

    The catalog load has to happen before the target is validated a second
    time: the first check only knows the hardcoded recipes, and the point of
    loading is that the live force may know more.
    """
    UNBACKED_DRAWS.clear()   # module state must not leak between runs
    catalog = load_json(bridge.export_recipe_catalog(force=force))
    learned = install_catalog_line_recipes(catalog)
    machines = install_catalog_machines(catalog)
    install_catalog_stack_sizes(catalog)
    emit(
        f"RECIPE CATALOG: loaded {len(catalog.get('recipes', []))} force recipes; "
        f"{len(learned)} additional solid recipes are executable"
    )
    emit(
        f"MACHINE CATALOG: {len(machines)} crafting machine(s) with live ingredient "
        "slot counts" if machines else
        "MACHINE CATALOG: none exported -- assembler tiers stay as declared "
        "(redeploy the mod to enable tier selection)"
    )
    validate_builder_target(goal_item, surface, LINE_RECIPES)
    mall_targets = mission_mall_targets(
        mission_items or (goal_item,), LINE_RECIPES,
    )
    emit(
        "CONSTRUCTION READINESS: phase 0 targets -- "
        + ", ".join(f"{item}={target}" for item, target in mall_targets.items())
    )
    emit(
        "PRODUCTION PREP: standing lines -- "
        + ", ".join(
            f"{recipe}x{BASELINE_MACHINES[recipe]}"
            for recipe in baseline_build_order()
        )
        + "; plate draw "
        + ", ".join(
            f"{plate} {rate:.2f}/s (drill phase {baseline_drill_phase(plate)})"
            for plate, rate in sorted(baseline_plate_draw().items())
        )
    )
    priority_path = Path(script_output).parent / "logs" / "autonomous-priorities.json"
    priorities = PriorityList(priority_path, live_base.game_tick(client))
    return mall_targets, priorities


def run(
    goal_item: str, *, surface: str = "nauvis", force: str = "player",
    rcon_host: str = "127.0.0.1", rcon_port: int = 27017, rcon_password: str = "",
    script_output: Path | str = "", reference_point: Point = (0.0, 0.0),
    max_iterations: int = 20, emit: Callable[[str], None] = print,
    mission_items: tuple[str, ...] = (),
) -> dict:
    """Loop: survey -> decide the single deepest missing stage -> build it ->
    repeat, until `goal_item` has a real, working line or the builder is
    genuinely stuck (raises StuckError rather than guessing)."""
    validate_builder_target(goal_item, surface, LINE_RECIPES)
    client = RconClient(rcon_host, rcon_port, rcon_password)
    bridge = GameBridge(
        script_output=Path(script_output), host=rcon_host, port=rcon_port,
        password=rcon_password, command_timeout=30.0,
    )
    try:
        mall_targets, priorities = _open_the_run(
            client, bridge, surface, force, goal_item, mission_items,
            script_output, emit,
        )
        prepped: set[str] = set()
        last_signature: tuple | None = None
        unchanged_passes = 0
        iteration = 0
        while iteration < max_iterations:
            tick, task = _survey_pass(
                client, surface, force, mall_targets, priorities,
            )
            signature = _pass_signature(task, mall_targets, prepped)
            unchanged_passes = (
                unchanged_passes + 1 if signature == last_signature else 0
            )
            last_signature = signature
            _refuse_to_spin(unchanged_passes, signature, goal_item)
            # PREP BEFORE THE MALL. The standing cells are what everything the
            # mall builds is made OF: no copper-cable cell means no circuits,
            # which means no drills and no assemblers. Ordered after the mall
            # this never ran at all -- mall_targets starts with ten entries and
            # only empties once every one is satisfied, so the mall spent every
            # pass consuming the player's starter stock through MALL BOOTSTRAP
            # while the lines that would refill it were never built. The run
            # stalled with the seed corn eaten. Prep hands the pass back when it
            # cannot afford a machine, so the mall still makes progress.
            if _prep_intermediate(
                client, bridge, surface, force, prepped, mall_targets,
                reference_point, emit,
            ):
                continue
            # Extraction second: it is the expensive half -- 14 drills against
            # the prep set's two assemblers -- and an intermediate built over a
            # starved plate line just starves too.
            # EVERY unprepped plate gets a turn, not just the first. Taking
            # only the head of the list let iron -- which yields the pass every
            # time it is short of drills -- block copper forever: a whole run
            # finished with no copper being produced at all.
            if any(
                _prep_plate_extraction(
                    client, bridge, surface, force, plate, prepped,
                    mall_targets, reference_point, emit,
                )
                for plate in BASELINE_PLATES if plate not in prepped
            ):
                continue
            if task is not None:
                _serve_mall_task(
                    client, bridge, surface, force, task, tick, mall_targets,
                    priorities, reference_point, emit,
                )
                continue
            if mall_targets:
                wait_ticks = priorities.wait_ticks(mall_targets, tick)
                emit(
                    "PRIORITY WAIT: all unfinished construction tasks are deferred; "
                    f"next review in {wait_ticks or 60} ticks"
                )
                time.sleep(5)
                continue
            emit(f"--- iteration {iteration}: checking {goal_item} ---")
            position = _advance_the_goal(
                client, bridge, surface, force, goal_item, mall_targets,
                reference_point, emit,
            )
            if position is _SHORTAGE:
                continue
            iteration += 1
            if position is not None:
                emit(f"GOAL MET: {goal_item} is producing at {position}")
                return {"ok": True, "iterations": iteration, "output_position": position}
        raise StuckError(f"Did not reach a working {goal_item} line within {max_iterations} iterations")
    finally:
        client.close()
        bridge.close()
