# Path: orchestrator/autonomous_builder.py
# Purpose: Goal-driven autonomous factory expansion on a real base -- given a target item, recursively ensures every ingredient in its recipe chain has a real, working production stage, deciding placement, connections, and troubleshooting itself.

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Callable

from core.science_recipe_graph import NAUVIS_DIRECT_RESOURCE_INPUTS
from orchestrator import live_base
from orchestrator.game_bridge import GameBridge, load_json
from orchestrator.mine_retirement import retire_depleted_mines
from orchestrator.mine_output_tap import legacy_output_tap_plan
from orchestrator.mall_builder import build_compact_mall_stage
from orchestrator.parts_mall import (
    MaterialShortage, add_demands, mission_mall_targets, wait_for_stock,
)
from orchestrator.intermediate_scaling import (
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
    _ROBOPORT_CONSTRUCTION_RADIUS,
    _ROBOPORT_LOGISTIC_RADIUS,
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
    FEED_HEADROOM,
    LINE_RECIPES,
    install_catalog_line_recipes,
    inserter_tiers_covering,
    machine_handled_rates,
)
from tools.rcon_client import RconClient

Point = tuple[float, float]
_DEFAULT_MACHINE_COUNT = 2


def _mineable(recipe: str) -> bool:
    """Whether this recipe is a supported direct resource-extraction stage."""
    ingredients = LINE_RECIPES[recipe]["ingredients"]
    return (
        len(ingredients) == 1
        and ingredients[0] in NAUVIS_DIRECT_RESOURCE_INPUTS
    )


def expansion_target(item: str, stock: Mapping[str, int]) -> str | None:
    """The deepest extraction stage that limits `item`, or None if none does.

    Bottlenecks are recursive. A is short because B is short because C is
    short, all the way down to ore, so raising A means raising whatever is
    actually starved beneath it. Checking only DIRECT ingredients for a
    mineable one gives up far too early: fast-transport-belt needs
    transport-belt and iron-gear-wheel, neither of which is mineable, so it
    deferred forever while its real constraint -- iron ore -- sat two levels
    down.

    At each level it follows the input the base is SHORTEST of, measured
    against what one craft consumes, so the walk tracks the live constraint
    rather than an arbitrary branch. `seen` guards against recipe cycles.
    """
    seen: set[str] = set()
    current = item
    while current in LINE_RECIPES and current not in seen:
        seen.add(current)
        if _mineable(current):
            return current
        spec = LINE_RECIPES[current]
        candidates = [
            ingredient for ingredient in spec["ingredients"]
            if ingredient in LINE_RECIPES and ingredient not in seen
        ]
        if not candidates:
            return None
        current = min(
            candidates,
            key=lambda ingredient: stock.get(ingredient, 0) / max(
                1, spec["amounts"][spec["ingredients"].index(ingredient)]
            ),
        )
    return None


def _side_sample_plate_output(
    plan: dict, origin: Point, machine_count: int,
    belt_type: str = _DEFAULT_BELT, flow_direction: str = "east",
    tap_inserter_type: str = "inserter",
) -> Point:
    """Keep one powered side tap while the output belt stays continuous."""
    if flow_direction not in {"east", "west"}:
        raise ValueError("Plate output flow must be east or west")
    ox, oy = origin
    terminal_chests = [
        action for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == "steel-chest"
        and action["position"]["y"] == oy + 6.5
    ]
    if len(terminal_chests) != 1:
        raise StuckError("plate layout has no unique terminal collector")
    terminal_chest = terminal_chests[0]
    chest_x = terminal_chest["position"]["x"]
    step = 1 if flow_direction == "east" else -1
    inserter_x = chest_x - step
    terminal_inserter = next((
        action for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity", "").endswith("inserter")
        and action["position"] == {"x": inserter_x, "y": oy + 6.5}
    ), None)
    if terminal_inserter is None:
        raise StuckError("plate layout terminal collector has no inserter")

    # Generic high-throughput lines add drain chests below the output belt.
    # Plates keep one low-priority sample only; the continuous belt is primary.
    for phase in plan["phases"]:
        phase["actions"] = [
            action for action in phase["actions"]
            if action is terminal_chest or action is terminal_inserter or not (
                action.get("entity") == "steel-chest"
                and action["position"]["y"] == oy + 8.5
            ) and not (
                action.get("entity", "").endswith("inserter")
                and action["position"]["y"] == oy + 7.5
            )
        ]

    provider = (chest_x, oy + 8.5)
    terminal_chest["position"] = {"x": provider[0], "y": provider[1]}
    terminal_inserter.update({
        "entity": tap_inserter_type,
        "position": {"x": provider[0], "y": provider[1] - 1},
        "direction": "north",
    })
    collector_phase = next(
        phase for phase in plan["phases"] if terminal_chest in phase["actions"]
    )
    collector_phase["actions"].extend([
        {
            "action_type": "place_ghost", "entity": belt_type,
            "position": {"x": inserter_x, "y": oy + 6.5},
            "direction": flow_direction,
        },
        {
            "action_type": "place_ghost", "entity": belt_type,
            "position": {"x": chest_x, "y": oy + 6.5},
            "direction": flow_direction,
        },
        {
            "action_type": "place_ghost", "entity": belt_type,
            "position": {"x": chest_x + step, "y": oy + 6.5},
            "direction": flow_direction,
        },
        {
            "action_type": "place_ghost", "entity": "medium-electric-pole",
            "position": {"x": provider[0], "y": provider[1] + 2},
        },
    ])
    return provider

def _diagnose_blockage(
    client: RconClient, surface: str, force: str, origin: Point,
    substation_position: Point, machine_positions: list[Point],
    logistic_chest_positions: Sequence[Point] = (),
    area: tuple[Point, Point] | None = None,
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
    # Machines are not the whole stage. The inserter that moves the product is
    # what makes a mining row actually deliver, and it sits on the output row --
    # outside the supply area of poles positioned for the machine row. Checking
    # machines alone declared such a stage healthy while its belt backed up and
    # its provider chest stayed empty forever.
    if area is not None:
        stranded = live_base.unpowered_entities(client, surface, area)
        if stranded:
            return (
                f"{len(stranded)} support entity(ies) unpowered "
                f"(first at {stranded[0]}); the stage's machines have power but "
                "something that moves its product does not",
                "entity_power",
            )
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

    if extraction.build_plan is not None:
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
    elif extraction.expansion_positions:
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
    else:
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
    conversion_args = dict(
        placement_origin=extraction.smelter_origin,
        machine_count=extraction.furnace_count,
        max_belt_route_tiles=int(LOCAL_MODE_MAX_LINK_TILES),
        flow_direction=extraction.smelter_flow_direction,
    )
    try:
        return build_conversion_stage(
            client, bridge, surface, force, recipe,
            {extraction.ore: ore_output}, reference_point, emit,
            **conversion_args,
        )
    except MaterialShortage as shortage:
        if _DEFAULT_BELT not in shortage.required:
            raise
        emit(
            f"  BOOTSTRAP: {recipe} cannot wait for {_DEFAULT_BELT}; "
            "building a beltless logistic smelter"
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


def _stage_inserter_type(
    client: RconClient, surface: str, force: str, recipe: str, machine_count: int,
    belt_type: str, flow_direction: str, emit: Callable[[str], None],
) -> str:
    """The cheapest inserter tier that both CARRIES this line and can be BUILT.

    Rate alone is not enough. Several tiers are usually adequate, and demanding
    the cheapest one deadlocks whenever the base cannot make it yet: a smelter
    row needs inserters, inserters need iron plate, and iron plate needs the
    smelter row. Observed live -- the runner asked for 14 plain inserters,
    held 9, and spun on that cycle indefinitely.

    So among the tiers that carry the load, prefer one the base is already
    holding enough of. Substituting UP is always safe (more throughput than
    required, only more expensive), and an oversized inserter that exists beats
    a right-sized one that cannot be produced -- stability over optimality.
    """
    peak_rate = max(machine_handled_rates(recipe))
    covering = inserter_tiers_covering(peak_rate * FEED_HEADROOM)
    preferred = covering[0]
    needed = _line_inserter_count(recipe, machine_count, belt_type, preferred, flow_direction)
    stock = live_base.available_items(client, surface, force)
    if stock.get(preferred, 0) >= needed:
        emit(f"  {recipe} moves {peak_rate:.2f} item/s per machine -- using {preferred}")
        return preferred
    for tier in covering[1:]:
        if stock.get(tier, 0) >= needed:
            emit(f"  {recipe} moves {peak_rate:.2f} item/s per machine -- {preferred} "
                 f"fits but only {stock.get(preferred, 0)}/{needed} are in stock; "
                 f"using {tier} instead ({stock[tier]} available)")
            return tier
    # Nothing adequate is in stock: keep the right-sized choice so the parts
    # mall is asked for the cheapest part rather than an oversized one.
    emit(f"  {recipe} moves {peak_rate:.2f} item/s per machine -- using {preferred} "
         f"(only {stock.get(preferred, 0)}/{needed} in stock; no adequate tier is stocked)")
    return preferred


def _line_inserter_count(
    recipe: str, machine_count: int, belt_type: str, inserter_type: str,
    flow_direction: str,
) -> int:
    """How many of `inserter_type` this line's layout actually places."""
    plan = LocalLayoutPlanner().generate_line_layout(
        recipe, machine_count, 0, 0, belt_type=belt_type,
        inserter_type=inserter_type, feed_style="chest",
        terminal_collector=True, flow_direction=flow_direction,
    )
    return sum(
        1
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == inserter_type
    )


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


def ensure_produced(
    client: RconClient, bridge: GameBridge, surface: str, force: str, item: str,
    reference_point: Point, emit: Callable[[str], None], *,
    upgrade_bootstrap: bool = True, stock_target: int = 1,
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
    promoted_count = promoted_line_machine_count(
        item, demand, existing.machine_count if existing else 0, saturated=saturated,
    )
    promote_to_line = promoted_count is not None and (
        existing is None or existing.machine_count < promoted_count
    )
    if promote_to_line:
        why = (
            f"all {existing.machine_count} machine(s) running flat out"
            if saturated and existing else f"demand is {demand:.2f}/s"
        )
        emit(
            f"  INTERMEDIATE PROMOTION: {item} -- {why}; "
            f"building a shared {promoted_count}-machine line instead of another mall cell"
        )
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

    if existing and existing.working_count > 0 and not promote_to_line:
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
    if existing and not promote_to_line:
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

    if not _mineable(item):
        sources: dict[str, Point] = {}
        # A readiness mall is allowed to consume a complete stocked input
        # buffer. Requiring a live upstream line first creates a deadlock for
        # bootstrap items such as inserters: their iron plates/gears/circuits
        # are already available, but the iron-plate line itself needs the same
        # inserters as construction ghosts.
        stocked = (
            live_base.available_items(client, surface, force)
            if not upgrade_bootstrap else {}
        )
        crafts_needed = math.ceil(
            mall_stock_target / max(1, spec.get("product_amount", 1)),
        )
        for ingredient, amount in zip(spec["ingredients"], spec["amounts"], strict=True):
            required = math.ceil(amount * crafts_needed)
            if not upgrade_bootstrap and stocked.get(ingredient, 0) >= required:
                emit(
                    f"  MALL BOOTSTRAP: using stocked {ingredient} "
                    f"({stocked[ingredient]}/{required}) for {item}"
                )
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
        if promote_to_line:
            if mall_provider is not None and existing and len(existing.machine_positions) == 1:
                retirement = generate_promoted_mall_retirement_plan(
                    item, spec["machine"], existing.machine_positions[0], mall_provider,
                )
                retirement["surface"], retirement["force"] = surface, force
                emit(
                    f"  MALL RETIRE: removing the old {item} cell and clearing its "
                    "request group before the dedicated line takes over"
                )
                _submit(
                    client, bridge, surface, retirement,
                    f"retire_promoted_mall_{item}", emit,
                )
            build_conversion_stage(
                client, bridge, surface, force, item, sources, reference_point, emit,
                machine_count=promoted_count,
                belt_type=promoted_line_belt_type(
                    item, promoted_count, live_base.available_items(client, surface, force),
                ),
                inserter_type="fast-inserter",
                allow_logistic_inputs=False,
                side_tap_output=True,
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

    build_mining_stage(client, bridge, surface, force, item, reference_point, emit)
    return None


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
        catalog = load_json(bridge.export_recipe_catalog(force=force))
        learned = install_catalog_line_recipes(catalog)
        emit(
            f"RECIPE CATALOG: loaded {len(catalog.get('recipes', []))} force recipes; "
            f"{len(learned)} additional solid recipes are executable"
        )
        validate_builder_target(goal_item, surface, LINE_RECIPES)
        mall_targets = mission_mall_targets(
            mission_items or (goal_item,), LINE_RECIPES,
        )
        emit(
            "CONSTRUCTION READINESS: phase 0 targets -- "
            + ", ".join(f"{item}={target}" for item, target in mall_targets.items())
        )
        priority_path = Path(script_output).parent / "logs" / "autonomous-priorities.json"
        priorities = PriorityList(priority_path, live_base.game_tick(client))
        iteration = 0
        while iteration < max_iterations:
            stock = live_base.available_items(client, surface, force)
            tick = live_base.game_tick(client)
            priorities.sync(mall_targets, stock, tick)
            for stocked_item, stocked_target in list(mall_targets.items()):
                if stock.get(stocked_item, 0) >= stocked_target:
                    priorities.complete(stocked_item, tick)
                    mall_targets.pop(stocked_item)
            task = priorities.next(mall_targets, tick)
            if task is not None:
                item, target = task.item, task.target
                emit(priorities.describe(task, tick))
                if item not in LINE_RECIPES:
                    raise StuckError(
                        f"Parts mall needs {target} {item}, but no executable recipe "
                        "knowledge exists for that construction item"
                    )
                emit(f"--- parts mall: ensuring {item} production for stock target {target} ---")
                try:
                    output = ensure_produced(
                        client, bridge, surface, force, item, reference_point, emit,
                        upgrade_bootstrap=False, stock_target=target,
                    )
                except MaterialShortage as shortage:
                    add_demands(mall_targets, shortage)
                    continue
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
                        continue
                    if not ready:
                        continue
                    priorities.complete(item, live_base.game_tick(client))
                    mall_targets.pop(item, None)
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
            try:
                position = ensure_produced(
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
                continue
            iteration += 1
            if position is not None:
                emit(f"GOAL MET: {goal_item} is producing at {position}")
                return {"ok": True, "iterations": iteration, "output_position": position}
        raise StuckError(f"Did not reach a working {goal_item} line within {max_iterations} iterations")
    finally:
        client.close()
        bridge.close()
