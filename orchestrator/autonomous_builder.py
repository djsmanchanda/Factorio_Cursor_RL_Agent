# Path: orchestrator/autonomous_builder.py
# Purpose: Goal-driven autonomous factory expansion on a real base -- given a target item, recursively ensures every ingredient in its recipe chain has a real, working production stage, deciding placement, connections, and troubleshooting itself.

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from orchestrator import extraction_state, live_base, stage_extraction
from orchestrator.build_decisions import (
    _heaviest_source,
    _mineable,
    _stage_inserter_type,
    expansion_target,
    may_consume_stocked_inputs,
)
from orchestrator.build_diagnostics import _diagnose_blockage, _side_sample_plate_output
from orchestrator.bootstrap_district import (
    BootstrapDistrictLedger, BootstrapDistrictState, BootstrapLifecycleError,
    REQUIRED_RESERVATION_ROLES,
)
from orchestrator.construction_stock import FALLBACK_STACK_SIZE, MallReserve, mall_reserve
from orchestrator.baseline_production import (
    BASELINE_MACHINES, BASELINE_PLATES, BOOTSTRAP_FURNACE_CAPS,
    PLATE_FOUNDATION_BUILD_ORDER, PLATE_FOUNDATION_FURNACES,
    baseline_build_order, baseline_drill_phase, baseline_plate_draw,
    demand_adjusted_plate_draw, drill_phase_for_draw,
    smelter_count_for_draw, STEEL_BASELINE_FURNACES,
    STEEL_IRON_CAPACITY_FLOOR,
)
from orchestrator.game_bridge import GameBridge, load_json
from orchestrator.controller_budget import (
    begin_run_budget, consume_diagnosis, consume_remediation, consume_wait,
    end_run_budget,
)
from orchestrator.mine_retirement import retire_depleted_mines
from orchestrator.mine_output_tap import legacy_output_tap_plan
from orchestrator.mall_builder import (
    build_compact_mall_stage, compact_mall_project_bill,
    locate_mall_cell,
    mall_cell_needs_rebuild,
    refresh_paired_mall_requests,
    rebuild_incomplete_mall_cell,
)
from orchestrator.mall_bootstrap import (
    MallBootstrapLoan,
    active_bootstrap_loans,
    bootstrap_loan_plan,
    next_bootstrap_step,
    restore_bootstrap_loan_plan,
)
from orchestrator.material_reservations import (
    MaterialReservationLedger, set_active_material_ledger,
)
from orchestrator.parts_mall import (
    MaterialShortage, add_demands, mission_mall_targets, wait_for_stock,
)
from orchestrator.intermediate_scaling import (
    backlog_seconds,
    live_intermediate_demand, promoted_line_belt_type, promoted_line_machine_count,
)
from orchestrator.priority_list import PriorityList
from orchestrator.power_district import ensure_power_capacity
from orchestrator.extraction_transport import (
    planned_entity_count, planned_footprint_tiles, preflight_ingredient_transport,
)
from orchestrator.refinery_state import (
    ManagedRefineryState, assert_refinery_removals_owned,
    live_refinery_placements, recover_managed_refinery,
)
from orchestrator.stage_chemical import (
    ensure_battery_cell, ensure_coal_mine, ensure_oil_cell,
)
from orchestrator.stage_extraction import (
    LOCAL_MODE_MAX_LINK_TILES, existing_mine_service_geometry,
    candidate_mining_origins as _candidate_mining_origins,  # noqa: F401 - compatibility export
    choose_mining_origin as _choose_mining_origin,  # noqa: F401 - compatibility export
    mining_drill_positions as _mining_drill_positions,  # noqa: F401 - compatibility export
    plan_local_extraction,
    planned_smelter_count_for_drills,
    smelter_count_for_drills,
)
from planners.resource_layouts import mine_substation_positions
from orchestrator.stage_recovery import repair_existing_ingredient_transport
from orchestrator.stage_services import (
    StuckError,
    _BLOCKAGE_INTERVAL,
    _BLOCKAGE_ROUNDS,
    _DEFAULT_BELT,
    _DEFAULT_INSERTER,
    _LOGISTIC_CHEST_ENTITIES,
    _ROBOPORT_LINK_DISTANCE,
    _ROBOPORT_SERVICE_AREAS,
    _STAGE_CHEST_REACH,
    _diagnose_machines,
    _logistic_chest_positions,
    _submit,
    _wait_for_ghosts,
    assert_affordable,
    construction_supply_chain_is_scheduled,
    ensure_logistic_coverage,
    extend_power,
    extend_roboport_coverage,
    service_distance,
    validate_builder_target,
)
from orchestrator.stage_transport import (
    _BELT_TIERS_CHEAPEST_FIRST,
    _publish_output_chest,
    _direct_single_belt_feed,
    _swap_infinity_chests,
    _transport_mode,
    ensure_ingredient_transport,
    relocate_blocking_poles,
    transport_grace_seconds,
)
from planners.bootstrap_smelting import (
    direct_smelter_positions,
    generate_direct_smelter,
    logistic_smelter_origin,
    retire_direct_smelter_plan,
    retire_logistic_smelter_plan,
)
from planners.infrastructure import strip_local_power
from planners.infrastructure_geometry import footprint_tile_indices
from planners.local_layout_planner import LocalLayoutPlanner
from planners.mall_layout import (
    generate_compact_mall_request_update, generate_mall_provider_limit_update,
    generate_mall_stock_gate_update, generate_promoted_mall_retirement_plan,
    request_multiplier as standard_mall_request_multiplier,
)
from planners.plan_validation import ENTITY_FOOTPRINTS, actions as plan_actions
from planners.recipe_data import (
    BELT_TIERS,
    LINE_RECIPES,
    MACHINE_SPEEDS,
    ITEM_STACK_SIZES,
    install_catalog_line_recipes,
    install_catalog_machines,
    install_catalog_stack_sizes,
    machine_ingredient_rates,
)
from planners.smelter_block import (
    FURNACES_PER_MODULE, generate_managed_refinery_extension_plan,
    generate_managed_refinery_plan, refinery_interfaces, scheduled_refinery_target,
)
from tools.rcon_client import RconClient

Point = tuple[float, float]
_DEFAULT_MACHINE_COUNT = 2
_FAST_BELT_IRON_CAPACITY = 24


class ProductionPrerequisiteDeferred(RuntimeError):
    """A construction item must wait for a cheaper upstream capacity phase."""

# A livelock re-selects the same task and gets the same result forever.
# max_iterations never bounded it: `iteration` only advances on goal work,
# so every mall/prep `continue` skipped it and a stuck run spun for hours.
# This counts consecutive passes that chose the same task at the same
# completion -- real progress moves one of them.
_MAX_UNCHANGED_PASSES = 12
_PENDING_FOUNDATION_POLL_SECONDS = 30.0


# How long one logistic-coverage remedy may wait for a just-connected
# roboport to charge before the round is honestly reported as a no-op. A fresh
# port lands at ~50% of 100 MJ and draws megawatts while topping up; without a
# real wait every round re-diagnosed the same orphaned chest within seconds,
# burned all six rounds on nothing, and killed the run (live, 2026-08-22).
_LOGISTIC_CHARGE_WAIT_SECONDS = 90.0

# A construction-material provider may be needed while an earmarked mine's
# own logistic inventory is empty. Keep that temporary chest outside the mine
# service envelope so it cannot occupy a reserved drill column on a later
# expansion. The cache makes every remediation round reuse the same chest.
_STAGE_DELIVERY_PROVIDERS: dict[tuple[str, str, str, Point], Point] = {}
_REFINERY_SITE_RESERVATIONS: dict[
    tuple[str, str, str], tuple[Point, Point],
] = {}
_BOOTSTRAP_DISTRICT_LEDGER: BootstrapDistrictLedger | None = None
_MATERIAL_RESERVATION_LEDGER: MaterialReservationLedger | None = None


def _bootstrap_lifecycle_stuck(
    recipe: str, error: BootstrapLifecycleError,
) -> StuckError:
    return StuckError(
        f"{recipe} bootstrap district lifecycle is contradictory: {error}",
        code="bootstrap_lifecycle_conflict",
        classification="bug",
        state="failed",
        details={"recipe": recipe, "reason": str(error)},
    )


def _placement_actions(plan: dict) -> tuple[dict, ...]:
    return tuple(
        action for action in plan_actions(plan)
        if action.get("action_type") in {"place_entity", "place_ghost"}
    )


def _area_tiles(area: tuple[Point, Point]) -> frozenset[tuple[int, int]]:
    minimum, maximum = area
    return frozenset(
        (x, y)
        for x in range(math.floor(minimum[0]), math.ceil(maximum[0]))
        for y in range(math.floor(minimum[1]), math.ceil(maximum[1]))
    )


def _bootstrap_state(recipe: str) -> BootstrapDistrictState | None:
    if _BOOTSTRAP_DISTRICT_LEDGER is None:
        return None
    try:
        return _BOOTSTRAP_DISTRICT_LEDGER.load(recipe)
    except BootstrapLifecycleError as error:
        raise _bootstrap_lifecycle_stuck(recipe, error) from error


def _bootstrap_owned_actions(
    recipe: str, furnace_count: int,
) -> tuple[dict, ...]:
    state = _bootstrap_state(recipe)
    if state is None or state.replacement_furnaces != furnace_count:
        return ()
    return state.replacement_actions


def _measured_bootstrap_replacement_output(
    client: RconClient, surface: str, recipe: str,
    state: BootstrapDistrictState,
) -> int:
    machine = LINE_RECIPES[recipe]["machine"]
    positions = tuple(
        (float(action["position"]["x"]), float(action["position"]["y"]))
        for action in state.replacement_actions
        if action.get("entity") == machine
    )
    counters = live_base.progress_counters(client, surface, positions)
    return sum(int(value // 1000) for value in counters.values())


def _record_bootstrap_pioneer(recipe: str, ore: str, plan: dict) -> None:
    if _BOOTSTRAP_DISTRICT_LEDGER is None:
        return
    try:
        _BOOTSTRAP_DISTRICT_LEDGER.record_pioneer(
            recipe, ore, _placement_actions(plan),
        )
    except BootstrapLifecycleError as error:
        raise _bootstrap_lifecycle_stuck(recipe, error) from error


def _record_bootstrap_provisioning(
    recipe: str, extraction, replacement_plan: dict,
    system_plan: dict, route_actions: Sequence[dict], provider: Point,
) -> None:
    """Reserve the complete future district before construction can spend it."""
    if _BOOTSTRAP_DISTRICT_LEDGER is None:
        return
    state = _bootstrap_state(recipe)
    if state is None or state.lifecycle_state == "released":
        return
    if state.lifecycle_state not in {"pioneer", "provisioning"}:
        expected = extraction.smelter_origin
        if state.replacement_origin != expected:
            error = BootstrapLifecycleError(
                f"{recipe} replacement moved from {state.replacement_origin} to {expected}"
            )
            raise _bootstrap_lifecycle_stuck(recipe, error) from error
        return
    build_plan = getattr(extraction, "build_plan", None)
    mine_growth = frozenset(
        tuple(tile) for tile in (build_plan or {}).get("reserved_tiles", ())
    )
    if not mine_growth and build_plan is not None:
        mine_growth = frozenset(planned_footprint_tiles(build_plan))
    if not mine_growth:
        mine_origin = getattr(extraction, "mine_origin", (0.0, 0.0))
        mine_growth = frozenset({(math.floor(mine_origin[0]), math.floor(mine_origin[1]))})
    refinery_area = getattr(extraction, "smelter_reserved_area", None)
    refinery_growth = (
        _area_tiles(refinery_area)
        if refinery_area is not None
        else frozenset(planned_footprint_tiles(replacement_plan))
    )
    route_plan = {"phases": [{"name": "route", "actions": list(route_actions)}]}
    transport_service = frozenset(planned_footprint_tiles(route_plan))
    if not transport_service:
        transport_service = frozenset(planned_footprint_tiles(system_plan))
    district_envelope = mine_growth | refinery_growth | transport_service
    reservations = {
        "mine_growth": mine_growth,
        "refinery_growth": refinery_growth,
        "transport_service": transport_service,
        # Power and roboport placement can move within the district as terrain
        # and network reach change. Reserve their service corridor, not one
        # guessed pole/port coordinate.
        "power_service": district_envelope,
        "roboport_service": district_envelope,
    }
    if set(reservations) != REQUIRED_RESERVATION_ROLES:
        raise AssertionError("bootstrap reservation roles drifted")
    target = max(FURNACES_PER_MODULE, extraction.furnace_count)
    try:
        _BOOTSTRAP_DISTRICT_LEDGER.provision(
            recipe,
            reservations=reservations,
            replacement_origin=extraction.smelter_origin,
            replacement_provider=provider,
            replacement_furnaces=target,
            replacement_actions=_placement_actions(replacement_plan),
        )
    except BootstrapLifecycleError as error:
        raise _bootstrap_lifecycle_stuck(recipe, error) from error


def _record_bootstrap_replacement(
    recipe: str, plan: dict, provider: Point, furnace_count: int,
) -> None:
    if _BOOTSTRAP_DISTRICT_LEDGER is None:
        return
    try:
        _BOOTSTRAP_DISTRICT_LEDGER.update_replacement(
            recipe,
            replacement_provider=provider,
            replacement_furnaces=furnace_count,
            replacement_actions=_placement_actions(plan),
        )
    except BootstrapLifecycleError as error:
        raise _bootstrap_lifecycle_stuck(recipe, error) from error


def _restore_bootstrap_reservations() -> None:
    if _BOOTSTRAP_DISTRICT_LEDGER is None:
        return
    try:
        states = _BOOTSTRAP_DISTRICT_LEDGER.states()
    except (OSError, BootstrapLifecycleError) as error:
        wrapped = (
            error if isinstance(error, BootstrapLifecycleError)
            else BootstrapLifecycleError(str(error))
        )
        raise _bootstrap_lifecycle_stuck("persisted", wrapped) from error
    for state in states:
        tiles = state.reservations.get("refinery_growth", frozenset())
        if not tiles:
            continue
        minimum = (float(min(x for x, _ in tiles)), float(min(y for _, y in tiles)))
        maximum = (
            float(max(x for x, _ in tiles) + 1),
            float(max(y for _, y in tiles) + 1),
        )
        _REFINERY_SITE_RESERVATIONS[(state.surface, state.force, state.recipe)] = (
            minimum, maximum,
        )


def _stage_delivery_anchor(
    name: str, origin: Point, area: tuple[Point, Point] | None,
) -> Point:
    if area is None or "mine" not in name:
        return origin
    minimum, _maximum = area
    return (minimum[0] - 3.0, minimum[1] - 3.0)


def _wait_for_logistic_service(
    client: RconClient, surface: str, force: str,
    chests: Sequence[Point], emit: Callable[[str], None],
) -> bool:
    """Poll until every chest joins a logistic network, bounded.

    Returns True once served (the wait was the remedy), False when the bound
    expired -- which the next diagnosis round will classify with evidence
    (a no_power roboport asks for roboport_power, not more waiting)."""
    deadline = time.monotonic() + _LOGISTIC_CHARGE_WAIT_SECONDS
    while time.monotonic() < deadline:
        served = live_base.logistic_network_ids(client, surface, list(chests))
        if served and all(network is not None for network in served.values()):
            return True
        time.sleep(3.0)
    emit(
        "    covering roboport still has not charged after "
        f"{_LOGISTIC_CHARGE_WAIT_SECONDS:.0f}s -- handing the fault back to "
        "diagnosis"
    )
    return False


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
        # The stage origin is only an anchor. A long output belt can extend
        # beyond it while the anchor itself remains covered, which used to
        # make every coverage retry a no-op. Follow the exact stranded ghost
        # reported by the diagnosis when one is available.
        coverage_target = origin
        if area is not None:
            for ghost in live_base.ghost_blockages(client, surface, force, area):
                if ghost.get("reason") == "out_of_construction_range":
                    coverage_target = tuple(ghost["position"])
                    break
        acted = extend_roboport_coverage(
            client, bridge, surface, force, coverage_target, emit,
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
            emit(
                "    coverage is already geometrically sufficient -- waiting for "
                "the covering roboport to finish powering up"
            )
            # Waiting IS the remedy here, but only if it is a real wait: the
            # charge takes tens of seconds, and rounds that re-check instantly
            # are six ways of doing nothing.
            acted = _wait_for_logistic_service(
                client, surface, force, list(logistic_chest_positions), emit,
            )
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
        acted = extend_power(
            client, bridge, surface, force, substation_position, emit,
        )
        if not acted:
            # A scaffold can already be on the source network while one or
            # more machines sit outside its supply area (for example, the
            # second row of a two-row mine). In that shape the substation has
            # no *other* powered network to bridge from, but the stranded
            # machines still need individual hookup poles.
            statuses = live_base.entity_statuses(
                client, surface, machine_positions,
            )
            stranded = [
                position for position in machine_positions
                if statuses.get(tuple(position)) == "no_power"
            ]
            for position in stranded:
                acted |= extend_power(
                    client, bridge, surface, force, position, emit,
                )
        if not acted:
            raise StuckError(
                f"{name}: {description}, but no power bridge can be built from "
                f"{substation_position} -- either no pole stands there (check the "
                "planned vs. built substation position) or the whole surface is "
                "already one network, so retrying cannot change anything"
            )
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
    elif remedy.startswith("materials:"):
        _, item, required_text = remedy.split(":", 2)
        required = int(required_text)
        stock = live_base.available_items(client, surface, force)
        if stock.get(item, 0) < required:
            raise MaterialShortage(name, {item: required}, stock)
        # Distinguish two zero-network situations. Reservations by other
        # ghosts clear on their own; a DISCONNECTED network never receives
        # anything no matter how long we wait -- logistic bots cannot cross a
        # network gap (live run 17 waited out 28 belts forever). Relocate the
        # stock into this stage's own provider chest.
        try:
            local_count = live_base.network_item_count(
                client, surface, force, origin, item,
            )
        except Exception:  # survey hiccup: reservations may still clear alone
            local_count = None
        if local_count is not None and local_count < required:
            key = (surface, force, name, origin)
            delivery_anchor = _stage_delivery_anchor(name, origin, area)
            delivery = _STAGE_DELIVERY_PROVIDERS.get(key)
            if delivery is None:
                delivery = live_base.nearest_container(
                    client, surface, force, delivery_anchor,
                    names=("passive-provider-chest",), max_distance=8.0,
                )
            if delivery is None:
                spots = live_base.chained_clear_spots(
                    client, surface, [("passive-provider-chest", 1)],
                    delivery_anchor,
                )
                chest_spot = next(
                    (spot for spot in spots if spot[0] == "passive-provider-chest"),
                    None,
                )
                if chest_spot is None:
                    return False
                delivery = (chest_spot[1], chest_spot[2])
                plan = {"phases": [{
                    "name": f"deliver_{item}",
                    "actions": [
                        {"action_type": "place_entity",
                         "entity": "passive-provider-chest",
                         "position": {"x": delivery[0], "y": delivery[1]}},
                    ],
                }]}
                plan["surface"], plan["force"] = surface, force
                _submit(client, bridge, surface, plan,
                        f"deliver_{item}", emit)
            _STAGE_DELIVERY_PROVIDERS[key] = delivery
            # The provider must be in the stage's actual logistic network.
            # Construction coverage alone only lets bots place the chest; it
            # does not let them take items from it. Reusing this one provider
            # prevents each diagnostic pass from building another chest.
            ensure_logistic_coverage(
                client, bridge, surface, force, [delivery], emit,
            )
            moved = live_base.transfer_stock(
                client, surface, item,
                max(required * 8, 16), delivery,
            )
            emit(
                f"  MATERIAL DELIVERY: moved {moved} {item} from base stock "
                f"into the existing stage provider at {delivery}"
            )
            return True
        # ``available_items`` sees the whole force, while the ghost probe sees
        # only the target logistic network. A zero network count with stock in
        # the base is often temporary: other construction ghosts have reserved
        # the items and the count returns as those jobs finish. Treating that
        # moment as a broken provider stopped the science stage even though the
        # belt reserve was healthy and the network was already connected.
        emit(
            f"    {item} exists in base stock ({stock[item]}) but is temporarily "
            "unavailable to this construction network; waiting for reservations "
            "or provider delivery to clear"
        )
        return True
    else:
        raise StuckError(f"{name}: {description} -- no automatic remedy")
    return bool(acted)


def _rebuild_stale_ghost(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    area: tuple[Point, Point], emit: Callable[[str], None],
) -> bool:
    """Remove and re-submit the one ghost diagnosis cannot explain.

    Rare but real (live run 13): a single pending ghost with a healthy
    network, robots, materials and clear ground sat unbuilt for 40+ minutes.
    Removing OUR OWN ghost and re-placing it through the executor re-runs
    placement and its explicit wiring pass -- cheaper than a stage timeout
    and it either builds or produces a fresh, diagnosable failure."""
    ghosts = live_base.ghost_blockages(client, surface, force, area)
    if not ghosts:
        return False
    actions: list[dict] = []
    removed = 0
    for ghost in ghosts:
        position = tuple(ghost["position"])  # type: ignore[arg-type]
        entity = str(ghost.get("entity", ""))
        if not live_base.remove_entity_at(client, surface, position):
            continue
        removed += 1
        actions.append(
            {"action_type": "place_ghost", "entity": entity,
             "position": {"x": position[0], "y": position[1]}},
        )
    if not actions:
        return False
    plan = {"phases": [{"name": "rebuild_stale_ghost", "actions": actions}]}
    plan["surface"], plan["force"] = surface, force
    _submit(client, bridge, surface, plan, "rebuild_stale_ghost", emit)
    emit(
        f"  STALE GHOST: rebuilt {removed} unbuilt ghost(s) despite healthy "
        "network evidence"
    )
    return True


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
    ensure_logistic_coverage(
        client, bridge, surface, force, logistic_chest_positions, emit,
    )
    description, remedy, acted_ever = "no blockage recorded", "none", False
    extensions = 0
    rebuilt_stale = False
    total_rounds = rounds
    attempt = 0
    local_baseline: int | None = None
    while True:
        if attempt >= total_rounds:
            # A blueprint the bots are VISIBLY filling does not deserve a
            # timeout: production falls while a stage is half-built, so
            # patience here is progress elsewhere too. Progress is judged on
            # THIS STAGE'S pending ghosts, not the whole surface: live run 30
            # (2026-08-22) built an oil pipeline whose pipes sat ~250 tiles
            # away, so every placement flew a cross-base round trip and the
            # area count fell 15 -> 10 across two extensions -- real, slow,
            # converging work that a single-extension limit killed anyway.
            # Extensions repeat while the local count keeps falling, capped so
            # a genuinely stalled stage still surfaces.
            if (
                local_baseline is not None and remaining is not None
                and remaining < local_baseline and extensions < 4
            ):
                extensions += 1
                emit(
                    f"  [{name}] bots are visibly winning ({local_baseline} -> "
                    f"{remaining} pending ghost(s)) -- extending remediation "
                    f"({extensions}/4)"
                )
                local_baseline = remaining
                total_rounds += rounds
                consume_remediation(f"{name}#extension{extensions}")
                continue
            # A LONE unresolved ghost with no named cause is the stale-ghost
            # signature: rebuild it once before giving up on the whole stage.
            if (
                not rebuilt_stale
                and remaining == 1
                and issue is None
            ):
                rebuilt_stale = True
                if _rebuild_stale_ghost(
                    client, bridge, surface, force, area, emit,
                ):
                    total_rounds += rounds
                    consume_remediation(f"{name}#stale_ghost")
                    continue
            break
        attempt += 1
        consume_remediation(f"{name}#round{attempt}")
        remaining = _wait_for_ghosts(
            client, surface, force, area, timeout_seconds=interval,
        )
        if local_baseline is None:
            local_baseline = remaining
        issue = _diagnose_blockage(
            client, surface, force, origin, substation_position, machine_positions,
            logistic_chest_positions, area,
        )
        consume_diagnosis(f"{name}#round{attempt}")
        if remaining == 0 and issue is None:
            if attempt > 1:
                emit(f"  [{name}] RESOLVED after {attempt} round(s)")
            return
        if issue is None:
            emit(f"  [{name} #{attempt}/{total_rounds}] {remaining} ghost(s) left, no blockage "
                 "found -- bots still working")
            continue
        description, remedy = issue
        emit(f"  [{name} #{attempt}/{total_rounds}] OPEN: {description} -> remedy: {remedy}")
        acted = _apply_remedy(
            client, bridge, surface, force, name, remedy, description, origin,
            substation_position, machine_positions, logistic_chest_positions,
            area, emit,
        )
        acted_ever |= bool(acted)
    progress = (
        f", area ghosts {local_baseline} -> {remaining}"
        if local_baseline is not None else ""
    )
    if not acted_ever:
        raise StuckError(
            f"{name}: {description}{progress}, and no remediation round could act "
            f"across {total_rounds} rounds ({total_rounds * interval:.0f}s) -- "
            f"the remedy '{remedy}' cannot address this fault"
        )
    raise StuckError(
        f"{name} still blocked after {total_rounds} rounds "
        f"({total_rounds * interval:.0f}s of remediation attempts); "
        f"last issue: {description}{progress}"
    )


def _place_new_mine(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    extraction, emit: Callable[[str], None], *,
    allow_unfunded_ghosts: bool = False,
) -> None:
    """Submit a freshly planned mine and work it up to running."""
    if extraction.mine_origin is None:
        raise StuckError("new extraction plan has no mine origin")
    ox, oy = extraction.mine_origin
    plan = strip_local_power(extraction.build_plan, remove_substations=False)
    _publish_output_chest(plan)
    if hasattr(client, "command"):
        _relocate_roboports_blocking_mine_plan(
            client, bridge, surface, force, plan, emit,
        )
        belt_rows: dict[float, list[float]] = {}
        for phase in plan["phases"]:
            for action in phase["actions"]:
                if action.get("entity", "").endswith("transport-belt"):
                    position = action["position"]
                    belt_rows.setdefault(position["y"], []).append(position["x"])
        for belt_y, belt_xs in sorted(belt_rows.items()):
            relocate_blocking_poles(
                client, bridge, surface, force,
                (min(belt_xs), belt_y), (max(belt_xs), belt_y), emit,
            )
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
    _submit(
        client, bridge, surface, plan, f"mining_{extraction.ore}", emit,
        stage_coverage=lambda: _ensure_plan_construction_coverage(
            client, bridge, surface, force, plan, emit,
        ),
        allow_unfunded_ghosts=allow_unfunded_ghosts,
    )
    if allow_unfunded_ghosts:
        # The substation is intentionally placed as a real service anchor even
        # when the rest of the mine is ghosts. Do not defer its connection:
        # otherwise the drills finish later on an isolated grid and the first
        # recovery pass has to rebuild their construction supply around them.
        if live_base.entity_status_name(
            client, surface, substation_position,
        ) in {"no_power", "low_power"}:
            extend_power(
                client, bridge, surface, force, substation_position, emit,
                reserved_tiles=planned_footprint_tiles(plan),
            )
        emit(
            f"  BLUEPRINT EARMARK: {extraction.ore} mine is placed as ghosts; "
            "construction continues while the mall fills the bill"
        )
        return
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
    substation_position = mine_substation_positions(
        sorted({x for x, _y in machines}), extraction.shared_belt_y,
    )[0]
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
    direct_belt = False
    if hasattr(client, "command"):
        existing_output = live_base.entity_at(
            client, surface, extraction.ore_output,
        )
        direct_belt = existing_output and (
            existing_output.get("type") == "transport-belt"
            or existing_output.get("ghost_name", "").endswith("transport-belt")
        )
    if extraction.shared_belt_y == extraction.ore_output[1] and not direct_belt:
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
        first_column_x=getattr(extraction, "first_column_x", None),
    )
    bring_stage_up(
        client, bridge, surface, force, f"existing mine for {extraction.ore}",
        origin, area, substation_position, machines, emit,
        # A direct belt is the mine's transport endpoint, not a logistic
        # chest. Passing its tile to the logistic-network probe makes the
        # belt's naturally absent `logistic_network` look like a stranded
        # chest and blocks the refinery behind a false coverage fault.
        logistic_chest_positions=[] if direct_belt else [ore_output],
    )


def _submit_mining_plan(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    extraction, ore_output: Point, emit: Callable[[str], None], *,
    allow_unfunded_ghosts: bool = False,
) -> None:
    """Place a freshly planned mine and work it up, or service an existing one."""
    if extraction.build_plan is not None:
        _place_new_mine(
            client, bridge, surface, force, extraction, emit,
            allow_unfunded_ghosts=allow_unfunded_ghosts,
        )
    elif extraction.expansion_positions:
        _reuse_expansion_row(client, bridge, surface, force, extraction, emit)
    else:
        _service_legacy_mine(
            client, bridge, surface, force, extraction, ore_output, emit,
        )


def _repair_unpowered_existing_mine(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    extraction, ore_output: Point, recipe: str,
    emit: Callable[[str], None],
) -> bool:
    """Repair a standing mine before treating low furnace feed as capacity demand."""
    if (
        getattr(extraction, "build_plan", None) is not None
        or getattr(extraction, "expansion_positions", ())
    ):
        return False
    _origin, _area, _power, machines = existing_mine_service_geometry(
        extraction.ore_output,
        getattr(extraction, "row_drill_count", 0) or extraction.drill_count,
        getattr(extraction, "expansion_step", -1),
        shared_belt_y=getattr(
            extraction, "shared_belt_y", extraction.ore_output[1],
        ),
        first_column_x=getattr(extraction, "first_column_x", None),
    )
    statuses = live_base.entity_statuses(client, surface, machines)
    unpowered = [
        position for position in machines
        if statuses.get(tuple(position)) == "no_power"
    ]
    if not unpowered:
        return False
    emit(
        f"  MINE POWER REPAIR: {len(unpowered)} {extraction.ore} drill(s) "
        f"are unpowered; repairing the existing mine before expanding {recipe}"
    )
    _submit_mining_plan(
        client, bridge, surface, force, extraction, ore_output, emit,
    )
    return True




def _tile_bounds(tiles: set[tuple[int, int]]) -> tuple[Point, Point]:
    """Return the smallest RCON survey box that contains the given tiles."""
    xs, ys = zip(*tiles)
    return (float(min(xs)), float(min(ys))), (float(max(xs) + 1), float(max(ys) + 1))


def _planned_removal_tiles(plan: dict) -> set[tuple[int, int]]:
    """Tiles the same authorized delta retires before replacement placement."""
    removed: set[tuple[int, int]] = set()
    for phase in plan["phases"]:
        for action in phase["actions"]:
            if action.get("action_type") != "remove_entity":
                continue
            removed.update(footprint_tile_indices(
                (action["position"]["x"], action["position"]["y"]),
                ENTITY_FOOTPRINTS.get(action["entity"], 1),
            ))
    return removed


def _planned_entity_positions(*plans: dict) -> set[tuple[str, float, float]]:
    """(entity, centre) identity of every entity a plan places.

    Collision surveys report live bounding boxes, which can cover tiles the
    declared footprint constants never claim -- so ownership is decided by
    matching the occupant's exact planned centre, never by tile arithmetic."""
    return {
        (action["entity"], action["position"]["x"], action["position"]["y"])
        for plan in plans
        for phase in plan.get("phases", [])
        for action in phase.get("actions", [])
        if "entity" in action and "position" in action
    }


_BELT_TIER_PAIRS = {
    "transport-belt": "fast-transport-belt",
    "underground-belt": "fast-underground-belt",
}
_BELT_TIER_UPGRADES = _BELT_TIER_PAIRS


def _prefer_stocked_belt_tiers(plan: dict, available: Mapping[str, int]) -> int:
    """Rebalance a plan's belt tiers toward whatever the base actually stocks.

    Belts are drop-in compatible across tiers (same footprint, same geometry),
    so the plan should follow INVENTORY, not the other way round. Two failure
    directions observed live: run 6 built on regular while fast sat unused;
    run 11 demanded fast it could not produce while regular belts were
    plentiful. For each tier pair, swaps actions from the short side to the
    covered side and returns the swap count."""
    planned: dict[str, list[dict]] = {}
    for phase in plan.get("phases", []):
        for action in phase["actions"]:
            entity = action.get("entity")
            if entity in _BELT_TIER_PAIRS or entity in _BELT_TIER_PAIRS.values():
                planned.setdefault(entity, []).append(action)
    if not planned:
        return 0
    swapped = 0
    # A tier may substitute the other AFTER covering its own plan needs:
    # surplus = stocked minus what this plan directly requires of it.
    for base, fast in _BELT_TIER_PAIRS.items():
        base_actions = planned.get(base, [])
        fast_actions = planned.get(fast, [])
        spare_base = max(
            0, available.get(base, 0) - len(base_actions),
        )
        spare_fast = max(
            0, available.get(fast, 0) - len(fast_actions),
        )

        def swap(actions: list[dict], from_tier: str, to_tier: str,
                 count: int) -> int:
            moved = 0
            for action in actions:
                if moved >= count:
                    break
                if action.get("entity") == from_tier:
                    action["entity"] = to_tier
                    moved += 1
            return moved

        deficit_base = max(0, len(base_actions) - available.get(base, 0))
        deficit_fast = max(0, len(fast_actions) - available.get(fast, 0))
        if deficit_base and spare_fast:
            swapped += swap(
                base_actions, base, fast, min(deficit_base, spare_fast),
            )
        if deficit_fast and spare_base:
            swapped += swap(
                fast_actions, fast, base, min(deficit_fast, spare_base),
            )
    return swapped


def _own_service_infrastructure(
    client: RconClient, surface: str, force: str,
    owner: tuple[str, float, float],
    *, owned_entities: set[tuple[str, float, float]] | None = None,
) -> bool:
    """Accept service infrastructure only by an exact persisted placement ID."""
    del client, surface, force
    if owner[1] != owner[1] or owner[2] != owner[2]:  # NaN guard
        return False
    return owner in (owned_entities or set())


def _plate_expansion_foundation(
    client: RconClient, surface: str, force: str, recipe: str,
    smelter_delta: dict,
    *, allowed_tiles: set[tuple[int, int]] | None = None,
    own_action_positions: set[tuple[str, float, float]] | None = None,
    emit: Callable[[str], None] = lambda _message: None,
) -> dict | None:
    """Survey a refinery extension before mining and stage needed landfill."""
    if not hasattr(client, "command"):
        return None
    footprint = planned_footprint_tiles(smelter_delta)
    if not footprint:
        return None
    minimum, maximum = _tile_bounds(footprint)
    owners = live_base.occupied_tile_owners(client, surface, minimum, maximum)
    replacement_tiles = _planned_removal_tiles(smelter_delta)
    excused = replacement_tiles | (allowed_tiles or set())
    # Entities this system itself planned -- the sibling mine submitted seconds
    # earlier, or a partial application of this same bill -- occupy ground via
    # their live bounding boxes long before their declared footprints do.
    # They are construction in flight, not a conflict.
    own = own_action_positions or set()
    suspects = sorted(
        (tile, owners[tile])
        for tile in (footprint & set(owners)) - excused
        if owners[tile] not in own
    )
    excusable: dict[tuple[str, float, float], bool] = {}
    collision: list[tuple[int, int]] = []
    relocate_roboports: set[Point] = set()
    for tile, owner in suspects:
        is_ours = excusable.get(owner)
        if is_ours is None:
            is_ours = _own_service_infrastructure(
                client, surface, force, owner, owned_entities=own,
            )
            excusable[owner] = is_ours
            if is_ours:
                emit(
                    f"  {owner[0]} at ({owner[1]}, {owner[2]}) is this base's "
                    "own power/coverage scaffolding -- rewiring it beats "
                    "refusing the extension"
                )
        if owner[0] == "roboport":
            relocate_roboports.add((owner[1], owner[2]))
        elif not is_ours:
            collision.append(tile)
    if collision:
        raise StuckError(
            f"{recipe} refinery extension intersects real infrastructure at "
            f"{collision[:3]}; refusing to expand its mine ahead of that conflict"
        )
    water = sorted(footprint & live_base.water_tiles(client, surface, minimum, maximum))
    if not water and not relocate_roboports:
        return None
    phases = [{
        "name": f"{recipe}_smelter_landfill_foundation",
        "actions": [
            {"action_type": "place_tile_ghost", "tile": "landfill",
             "position": {"x": x, "y": y}}
            for x, y in water
        ],
    }] if water else []
    return {
        "phases": phases,
        "relocate_roboports": sorted(relocate_roboports),
        "reserved_tiles": sorted(footprint),
    }


def _relocate_roboport_for_expansion(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    old: Point, reserved_tiles: set[tuple[int, int]], emit: Callable[[str], None],
) -> None:
    """Move one obstructing service port while preserving all of its links."""
    ports = [position for position in live_base.roboport_positions(
        client, surface, force,
    ) if position != old]
    neighbors = [
        position for position in ports
        if math.dist(position, old) <= _ROBOPORT_LINK_DISTANCE
    ]
    if not neighbors:
        raise StuckError(
            f"roboport at {old} blocks refinery growth but has no alternate "
            "network neighbor; refusing to strand its logistic network"
        )
    candidates = sorted(
        {
            (old[0] + dx, old[1] + dy)
            for dx in range(-20, 21) for dy in range(-20, 21)
            if dx or dy
        },
        key=lambda point: (math.dist(point, old), point),
    )
    replacement = next((
        point for point in candidates
        if all(math.dist(point, neighbor) <= _ROBOPORT_LINK_DISTANCE
               for neighbor in neighbors)
        and not (footprint_tile_indices(point, 4) & reserved_tiles)
        and live_base.area_clear(
            client, surface,
            (point[0] - 2, point[1] - 2), (point[0] + 2, point[1] + 2),
        )
    ), None)
    if replacement is None:
        raise StuckError(
            f"roboport at {old} blocks refinery growth and no connected clear "
            "replacement site exists within 20 tiles"
        )
    emit(
        f"SMELTER SERVICE RELOCATION: moving roboport at {old} to {replacement} "
        "before expanding the furnace line"
    )
    place = {"surface": surface, "force": force, "phases": [{
        "name": "relocate_smelter_roboport",
        "actions": [{"action_type": "place_entity", "entity": "roboport",
                     "position": {"x": replacement[0], "y": replacement[1]}}],
    }]}
    _submit(client, bridge, surface, place, "relocate_smelter_roboport", emit)
    if not extend_power(client, bridge, surface, force, replacement, emit):
        status = live_base.entity_status_name(client, surface, replacement)
        if status in {"no_power", "low_power"}:
            raise StuckError(f"replacement roboport at {replacement} cannot be powered")
    remove = {"surface": surface, "force": force, "phases": [{
        "name": "retire_obstructing_roboport",
        "actions": [{"action_type": "remove_entity", "entity": "roboport",
                     "position": {"x": old[0], "y": old[1]}}],
    }]}
    _submit(client, bridge, surface, remove, "retire_obstructing_roboport", emit)


def _relocate_roboports_blocking_mine_plan(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    plan: dict, emit: Callable[[str], None],
) -> None:
    """Move owned service ports out of the *current* mine expansion footprint.

    The future corridor remains reserved for later power placement, but a port
    moves only when the next six-drill module actually needs its tile.
    """
    active_tiles = planned_footprint_tiles(plan)
    if not active_tiles:
        return
    reserved = active_tiles | {
        tuple(tile) for tile in plan.get("reserved_tiles", [])
        if isinstance(tile, (list, tuple)) and len(tile) == 2
    }
    minimum, maximum = _tile_bounds(active_tiles)
    owners = live_base.occupied_tile_owners(client, surface, minimum, maximum)
    ports = sorted({
        (owner[1], owner[2]) for tile, owner in owners.items()
        if tile in active_tiles and owner[0] == "roboport"
    })
    for port in ports:
        _relocate_roboport_for_expansion(
            client, bridge, surface, force, port, reserved, emit,
        )


def _place_plate_expansion_foundation(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, foundation: dict, emit: Callable[[str], None],
) -> None:
    """Build solid ground before a plate-refinery ghost is submitted there."""
    reserved = {tuple(tile) for tile in foundation.get("reserved_tiles", [])}
    for position in foundation.get("relocate_roboports", []):
        _relocate_roboport_for_expansion(
            client, bridge, surface, force, tuple(position), reserved, emit,
        )
    if not foundation.get("phases"):
        return
    foundation["surface"], foundation["force"] = surface, force
    tiles = [
        action["position"] for phase in foundation["phases"]
        for action in phase["actions"]
    ]
    emit(f"SMELTER FOUNDATION: placing landfill on {len(tiles)} water tile(s) before {recipe}")
    _submit(
        client, bridge, surface, foundation, f"{recipe}_smelter_foundation", emit,
        stage_coverage=lambda: _ensure_plan_construction_coverage(
            client, bridge, surface, force, foundation, emit,
        ),
    )
    positions = {(int(tile["x"]), int(tile["y"])) for tile in tiles}
    remaining = _wait_for_ghosts(
        client, surface, force, _tile_bounds(positions),
        include_entity_ghosts=False,
    )
    if remaining:
        raise StuckError(
            f"{recipe} smelter landfill foundation has {remaining} tile ghost(s) remaining; "
            "refusing to place furnaces on water before the landfill is built"
        )


def _plan_area(plan: dict, padding: float = 15.0) -> tuple[Point, Point]:
    """Bounds containing every action position with remediation space around it."""
    positions = [
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if "position" in action
    ]
    xs, ys = zip(*positions)
    return (
        (min(xs) - padding, min(ys) - padding),
        (max(xs) + padding, max(ys) + padding),
    )


def _modular_machine_positions(plan: dict, recipe: str) -> list[Point]:
    machine = LINE_RECIPES[recipe]["machine"]
    return [
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("action_type") in {"place_entity", "place_ghost"}
        and action.get("entity") == machine
    ]


def _bring_modular_refinery_up(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, plan: dict, furnace_count: int, origin: Point,
    emit: Callable[[str], None], *, feed_grace_seconds: float = 0.0,
    variant: str = "standard",
) -> None:
    """Power, cover, and diagnose the complete modular refinery footprint."""
    interface = refinery_interfaces(
        furnace_count, origin_x=origin[0], origin_y=origin[1], variant=variant,
    )
    machines = _modular_machine_positions(plan, recipe)
    reserved = planned_footprint_tiles(plan)
    support_positions = {
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity", "").endswith("inserter")
    }
    for position in sorted(support_positions):
        if live_base.entity_status_name(client, surface, position) == "no_power":
            emit(f"  support inserter at {position} has no power -- connecting it")
            if not extend_power(
                client, bridge, surface, force, position, emit,
                reserved_tiles=reserved,
            ):
                raise StuckError(f"support inserter at {position} cannot reach generated power")
    bring_stage_up(
        client, bridge, surface, force, f"modular refinery for {recipe}",
        origin, _plan_area(plan), interface.power_anchor, machines, emit,
        logistic_chest_positions=[interface.provider],
    )
    stuck = _diagnose_machines(
        client, surface, machines, emit, grace_seconds=feed_grace_seconds,
        bridge=bridge, force=force,
    )
    if stuck:
        raise StuckError(f"modular refinery for {recipe} built but not healthy: {stuck}")


def _extend_plate_smelter(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, state: ManagedRefineryState, target_machines: int,
    ore_output: Point, emit: Callable[[str], None], *,
    allow_unfunded_ghosts: bool = False,
) -> Point:
    """Expand one recovered block by migrating its planner-owned End/output cap."""
    del ore_output
    origin = state.origin
    # Keep the cheaper bootstrap geometry while it grows. Migrating a basic
    # starter block to the standard two-row interface adds belts on its old
    # footprint and can collide with valid infrastructure before capacity grows.
    target_variant = state.variant
    full = generate_managed_refinery_plan(
        recipe, target_machines, origin_x=origin[0], origin_y=origin[1],
        variant=target_variant,
    )
    interface = refinery_interfaces(
        target_machines, origin_x=origin[0], origin_y=origin[1],
        variant=target_variant,
    )
    if target_machines <= state.furnace_count:
        emit(
            f"SMELTER COHESION: existing modular {recipe} block already has "
            f"{state.furnace_count}/{target_machines} furnace(s)"
        )
        _bring_modular_refinery_up(
            client, bridge, surface, force, recipe, full,
            target_machines, origin, emit, variant=target_variant,
        )
        return interface.provider
    delta = generate_managed_refinery_extension_plan(
        recipe, state.furnace_count, target_machines,
        origin_x=origin[0], origin_y=origin[1],
        current_variant=state.variant, target_variant=target_variant,
    )
    try:
        assert_refinery_removals_owned(client, surface, force, state, delta)
    except ValueError as error:
        raise StuckError(
            str(error), code="refinery_ownership_mismatch",
            classification="bug", state="failed",
            details={
                "recipe": recipe,
                "current_furnaces": state.furnace_count,
                "target_furnaces": target_machines,
            },
        ) from error
    delta["surface"], delta["force"] = surface, force
    emit(
        f"SMELTER COHESION: expanding {recipe} at {origin} from "
        f"{state.furnace_count} to {target_machines}; retire End, add Repeat, finish End"
    )
    _prepare_replacement_services(
        client, bridge, surface, force, full, delta, emit,
    )
    _record_bootstrap_replacement(
        recipe, full, interface.provider, target_machines,
    )
    _submit(
        client, bridge, surface, delta, f"extend_{recipe}_refinery", emit,
        allow_unfunded_ghosts=allow_unfunded_ghosts,
    )
    _bring_modular_refinery_up(
        client, bridge, surface, force, recipe, full,
        target_machines, origin, emit, variant=target_variant,
    )
    return interface.provider


def _assert_atomic_plate_expansion_affordable(
    client: RconClient, surface: str, force: str, recipe: str, extraction,
    state: ManagedRefineryState, target_machines: int, emit: Callable[[str], None], *,
    allow_unfunded_ghosts: bool = False,
) -> dict | None:
    """Preflight the mine and exact modular refinery delta before either grows."""
    if target_machines <= state.furnace_count:
        return None
    # Expansion must not implicitly migrate a basic starter refinery. The
    # basic Start/Repeat/End geometry is the low-budget growth path; migration
    # is a separate, explicitly planned operation.
    target_variant = state.variant
    smelter_delta = generate_managed_refinery_extension_plan(
        recipe, state.furnace_count, target_machines,
        origin_x=state.origin[0], origin_y=state.origin[1],
        current_variant=state.variant, target_variant=target_variant,
    )
    try:
        assert_refinery_removals_owned(
            client, surface, force, state, smelter_delta,
        )
    except ValueError as error:
        raise StuckError(
            str(error), code="refinery_ownership_mismatch",
            classification="bug", state="failed",
            details={
                "recipe": recipe,
                "current_furnaces": state.furnace_count,
                "target_furnaces": target_machines,
            },
        ) from error
    foundation = _plate_expansion_foundation(
        client, surface, force, recipe, smelter_delta,
        own_action_positions=_planned_entity_positions(
            smelter_delta,
            *( [extraction.build_plan] if extraction.build_plan is not None else [] ),
        ) | live_refinery_placements(client, surface, force, state),
    )
    plans = [smelter_delta]
    if foundation is not None:
        plans.insert(0, foundation)
    if extraction.build_plan is not None:
        plans.insert(0, extraction.build_plan)
    try:
        belt_stock = live_base.available_items(client, surface, force)
        swapped = sum(
            _prefer_stocked_belt_tiers(staged, belt_stock) for staged in plans
        )
        if swapped:
            emit(
                f"  BELT ECONOMY: upgraded {swapped} regular belt action(s) to "
                "stocked fast tiers"
            )
    except Exception as error:  # economy upgrade is opportunistic
        emit(f"  BELT ECONOMY skipped: {error}")
    combined = {
        "force": force,
        "phases": [phase for plan in plans for phase in plan["phases"]],
    }
    try:
        assert_affordable(
            client, surface, force, combined, f"expand_{recipe}_system", emit,
        )
    except MaterialShortage:
        if not allow_unfunded_ghosts:
            raise
        emit(
            f"  BLUEPRINT EARMARK: coherent {recipe} mine/refinery expansion "
            "passed ownership and layout preflight; material shortfalls are queued"
        )
    return foundation


def _refinery_machine_positions(
    client: RconClient, surface: str, force: str, recipe: str,
    line, near: Point, emit: Callable[[str], None],
) -> tuple[Point, ...]:
    """Merge recipe-visible and starved furnaces before structural recovery."""
    visible = set(line.machine_positions) if line is not None else set()
    anchor = next(iter(visible), near)
    idle = live_base.find_idle_machine_row(
        client, surface, force, recipe, LINE_RECIPES[recipe]["machine"],
        anchor, radius=150.0,
    )
    idle_positions = set(idle.machine_positions) if idle is not None else set()
    positions = tuple(sorted(visible | idle_positions))
    if idle_positions - visible:
        emit(
            f"SMELTER RECOVERY: merged {len(idle_positions - visible)} starved "
            f"{recipe} furnace(s) into the managed block survey"
        )
    return positions


def _complete_six_furnace_candidates(
    positions: tuple[Point, ...],
) -> tuple[tuple[Point, ...], ...]:
    """Exact six-furnace template windows contained in an incomplete survey."""
    available = set(positions)
    candidates: list[tuple[Point, ...]] = []
    for x in sorted({position[0] for position in available}):
        for y in sorted({position[1] for position in available}):
            candidate = tuple(sorted({
                (x, y), (x + 6, y),
                (x, y + 3), (x + 6, y + 3),
                (x, y + 6), (x + 6, y + 6),
            }))
            if set(candidate) <= available:
                candidates.append(candidate)
    return tuple(dict.fromkeys(candidates))


def _cohesive_smelter_target(
    client: RconClient, surface: str, force: str, recipe: str,
    extraction, expand: bool, emit: Callable[[str], None],
) -> tuple[ManagedRefineryState | None, int | None]:
    """Recover one modular refinery and round its rate target to whole modules."""
    if not expand:
        return None, None
    line = live_base.find_line(
        client, surface, force, recipe, LINE_RECIPES[recipe]["machine"],
    )
    positions = _refinery_machine_positions(
        client, surface, force, recipe, line, extraction.smelter_origin, emit,
    )
    if not positions:
        return None, None
    try:
        owned_actions = _bootstrap_owned_actions(recipe, len(positions))
        existing = recover_managed_refinery(
            client, surface, force, recipe, positions,
            **({"owned_actions": owned_actions} if owned_actions else {}),
        )
    except ValueError as error:
        # A direct line can have expansion furnaces already placed (or ghosts
        # still waiting for their recipe) beside it.  The old recovery path
        # merged every nearby recipe-less furnace first, so a valid six-furnace
        # block plus three or four in-progress machines became an invalid
        # 9/10-furnace "module" and expansion deferred forever.  Recover the
        # recipe-visible managed block on its own; the next extension plan can
        # claim the pending slots once the complete target is affordable.
        visible = tuple(sorted(set(line.machine_positions))) if line is not None else ()
        candidates = [visible] if visible and set(visible) != set(positions) else []
        candidates.extend(_complete_six_furnace_candidates(positions))
        existing = None
        for candidate in candidates:
            try:
                owned_actions = _bootstrap_owned_actions(recipe, len(candidate))
                existing = recover_managed_refinery(
                    client, surface, force, recipe, candidate,
                    **({"owned_actions": owned_actions} if owned_actions else {}),
                )
                break
            except ValueError:
                continue
        if existing is None:
            emit(
                f"SMELTER RECOVERY: {len(positions)} observed {recipe} furnace(s) "
                "are incomplete construction, not a managed refinery; waiting for "
                "the existing blueprint instead of expanding its mine"
            )
            raise ProductionPrerequisiteDeferred(
                f"{recipe} refinery has incomplete furnace modules"
            ) from error
        ignored = len(set(positions) - set(existing.machine_positions))
        if ignored:
            emit(
                f"SMELTER RECOVERY: ignored {ignored} incomplete {recipe} furnace(s) "
                "outside the verified managed module"
            )
    total_drills = extraction.system_drill_count_before + extraction.drill_count
    rate_required = smelter_count_for_drills(
        recipe, total_drills, extraction.mining_productivity_bonus,
    )
    supported = planned_smelter_count_for_drills(
        recipe, total_drills, extraction.mining_productivity_bonus,
    )
    target = scheduled_refinery_target(existing.furnace_count, supported)
    if target is None:
        raise StuckError(
            f"{recipe} refinery reached its generation-1 cap at "
            f"{existing.furnace_count} furnace(s); refusing to overbuild this "
            "footprint before a new refinery site is planned"
        )
    emit(
        f"SMELTER SYSTEM TARGET: {total_drills} total {extraction.ore} "
        f"drill(s) calculate {rate_required} furnace(s), but the current mine "
        f"supports {supported}; scheduled target is {target}"
    )
    return existing, target


def _prepare_initial_refinery(
    client: RconClient, surface: str, force: str, recipe: str,
    extraction, ore_output: Point, emit: Callable[[str], None], *,
    preflight_only: bool,
    allow_unfunded_ghosts: bool = False,
) -> tuple[dict, dict | None, tuple[float, float], str, int]:
    """Preflight the refinery, route, landfill, bill, and planned mine together."""
    origin = extraction.smelter_origin
    # A new site can be opened for a later drill phase. Keep the first site at
    # six furnaces, but size an unmanaged replacement for the phase that caused
    # the expansion request so it immediately relieves the bottleneck.
    target = max(FURNACES_PER_MODULE, extraction.furnace_count)
    variant = "basic"
    plan = generate_managed_refinery_plan(
        recipe, target, origin_x=origin[0], origin_y=origin[1], variant=variant,
    )
    replacement_plan = json.loads(json.dumps(plan))
    interface = refinery_interfaces(
        target, origin_x=origin[0], origin_y=origin[1], variant=variant,
    )
    emit(
        f"modular refinery for {recipe}: building basic Start + End at {origin} "
        f"with {target} furnace(s)"
    )
    planned_blocked = planned_footprint_tiles(plan)
    build_plan = getattr(extraction, "build_plan", None)
    reserved_transport_belts = planned_entity_count(plan, "transport-belt")
    if build_plan is not None:
        planned_blocked |= planned_footprint_tiles(build_plan)
        reserved_transport_belts += planned_entity_count(build_plan, "transport-belt")
        planned_blocked -= {
            (math.floor(ore_output[0]), math.floor(ore_output[1])),
            (math.floor(ore_output[0] + 1), math.floor(ore_output[1])),
        }
    route = preflight_ingredient_transport(
        client, surface, force, recipe, extraction.ore,
        ore_output, interface.ore_inputs[0], target,
        max_belt_route_tiles=int(LOCAL_MODE_MAX_LINK_TILES),
        additional_blocked=planned_blocked,
        mode="belt", destination_is_belt=True,
        reserved_transport_belts=reserved_transport_belts,
        # The mine is part of this same transaction on both the survey and
        # build pass; otherwise the build pass mistakes our fresh terminal for
        # an existing one and preserves an unusable exit direction.
        planned_belt_source=(
            (ore_output[0] + 1, ore_output[1])
            if build_plan is not None else None
        ),
        # Managed collectors flow east by design (direct_mine_plan
        # output_side="east"); the position heuristic cannot know that while
        # the row is still ghosts and reads the head as a west terminal.
        through_flow_direction="east",
        destination_belt_direction="east",
        # Opening metal foundations use the belt tier the cold-start mall can
        # actually produce. Route geometry is priced now; the combined bill
        # below decides when the whole mine/refinery blueprint is affordable.
        required_belt_type=_DEFAULT_BELT,
        defer_required_tier_affordability=True,
    )
    if route is None:
        raise StuckError(f"{recipe} direct ore route unexpectedly selected logistics")
    route_actions, belt_type = route
    plan["phases"].append({
        "name": f"bridge_{extraction.ore}_to_{recipe}",
        "actions": route_actions,
    })
    _record_bootstrap_provisioning(
        recipe, extraction, replacement_plan, plan, route_actions,
        interface.provider,
    )
    ore_interface_tiles = {
        (math.floor(ore_output[0]), math.floor(ore_output[1])),
        (math.floor(ore_output[0] + 1), math.floor(ore_output[1])),
    }
    foundation = _plate_expansion_foundation(
        client, surface, force, recipe, plan, allowed_tiles=ore_interface_tiles,
        # The mine is submitted as part of THIS system seconds before the
        # refinery lands; its power scaffold occupies ground by live bounding
        # box and must read as construction in flight, not as a conflict.
        own_action_positions=_planned_entity_positions(
            plan,
            *( [build_plan] if build_plan is not None else [] ),
        ),
    )
    bill_route = route_actions
    build_plan = getattr(extraction, "build_plan", None)
    if preflight_only and build_plan is not None:
        existing = {
            json.dumps(action, sort_keys=True)
            for phase in build_plan["phases"] for action in phase["actions"]
        }
        bill_route = [
            action for action in route_actions
            if json.dumps(action, sort_keys=True) not in existing
        ]
    bill_plan = dict(plan)
    bill_plan["phases"] = [
        *plan["phases"][:-1],
        {**plan["phases"][-1], "actions": bill_route},
    ]
    combined_plans = (
        [build_plan] if preflight_only and build_plan is not None else []
    ) + ([foundation] if foundation is not None else []) + [bill_plan]
    combined = {
        "force": force,
        "phases": [phase for staged in combined_plans for phase in staged["phases"]],
    }
    try:
        assert_affordable(
            client, surface, force, combined, f"initial_{recipe}_system", emit,
        )
    except MaterialShortage as shortage:
        if not allow_unfunded_ghosts or not all(
            construction_supply_chain_is_scheduled(
                client, surface, force, item,
            )
            for item in shortage.required
        ):
            raise
        emit(
            f"  BLUEPRINT EARMARK: coherent {recipe} mine/refinery foundation "
            "has scheduled construction supply chains; placing ghosts now"
        )
    belt_tiles = sum(
        1 for action in route_actions
        if "transport-belt" in action.get("entity", "")
    )
    return plan, foundation, interface.provider, belt_type, belt_tiles


def _retire_standing_bootstrap_cells(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, ore: str, ore_output: Point, emit: Callable[[str], None],
) -> int:
    """Retire recognized temporary plate producers after replacement is healthy.

    The caller invokes this only after the direct refinery is healthy. Keeping
    the temporary path until then preserves the only working plate source when
    construction or bring-up fails. Legacy requester cells are also recognized
    so an old save can migrate without opening another temporary path.
    """
    removed = 0
    ledger = _BOOTSTRAP_DISTRICT_LEDGER
    lifecycle = _bootstrap_state(recipe)
    try:
        starter = live_base.direct_plate_starter(
            client, surface, force, recipe, ore, ore_output,
        )
    except Exception:  # survey unavailable (dry harness): legacy path still runs
        starter = None
    if lifecycle is not None:
        assert ledger is not None
        if starter is None and lifecycle.lifecycle_state == "retiring":
            try:
                lifecycle = ledger.mark_released(recipe)
            except BootstrapLifecycleError as error:
                raise _bootstrap_lifecycle_stuck(recipe, error) from error
            emit(
                f"BOOTSTRAP DISTRICT: {recipe} pioneer absence verified; "
                "lifecycle released"
            )
        elif starter is not None:
            if lifecycle.lifecycle_state == "released":
                error = BootstrapLifecycleError(
                    f"released {recipe} district still contains its pioneer"
                )
                raise _bootstrap_lifecycle_stuck(recipe, error) from error
            measured = _measured_bootstrap_replacement_output(
                client, surface, recipe, lifecycle,
            )
            if measured <= 0:
                emit(
                    f"BOOTSTRAP DISTRICT: {recipe} replacement has no measured "
                    "output; keeping its pioneer"
                )
                consume_wait(f"measured_{recipe}_replacement_output")
                return 0
            try:
                if lifecycle.lifecycle_state == "pioneer":
                    raise BootstrapLifecycleError(
                        f"{recipe} pioneer cannot retire before replacement provisioning"
                    )
                if lifecycle.lifecycle_state == "provisioning":
                    lifecycle = ledger.mark_validating(
                        recipe, measured,
                    )
                if lifecycle.lifecycle_state == "validating":
                    lifecycle = ledger.mark_retiring(recipe)
            except BootstrapLifecycleError as error:
                raise _bootstrap_lifecycle_stuck(recipe, error) from error
    if starter is not None:
        plan = retire_direct_smelter_plan(
            recipe, ore, starter.drill_position, starter.output_direction,
            pole_side=starter.pole_side,
        )
        plan["surface"], plan["force"] = surface, force
        _submit(
            client, bridge, surface, plan,
            f"retire_direct_{recipe}_starter", emit,
        )
        removed += 1
        emit(
            f"BOOTSTRAP SWAP: full {recipe} system is healthy; retiring the "
            f"direct starter at {starter.drill_position}"
        )
        if recipe in {"iron-plate", "copper-plate"}:
            _release_metal_starter_limits_if_complete(client, surface, force)
        if lifecycle is not None:
            try:
                remaining = live_base.direct_plate_starter(
                    client, surface, force, recipe, ore, ore_output,
                )
                if remaining is None:
                    lifecycle = ledger.mark_released(recipe)
                    emit(
                        f"BOOTSTRAP DISTRICT: {recipe} pioneer absence verified; "
                        "lifecycle released"
                    )
            except BootstrapLifecycleError as error:
                raise _bootstrap_lifecycle_stuck(recipe, error) from error
    try:
        standing = live_base.bootstrap_cell_origins(client, surface, force, ore)
    except Exception:  # survey unavailable (dry harness): no legacy cell to retire
        standing = []
    legacy_removed = 0
    for spot in standing:
        origin = (round(spot[0] - 1.5), round(spot[1] - 3.5))
        plan = retire_logistic_smelter_plan(recipe, ore, origin)
        plan["surface"], plan["force"] = surface, force
        _submit(client, bridge, surface, plan,
                f"retire_logistic_{recipe}_cell", emit)
        removed += 1
        legacy_removed += 1
    if legacy_removed:
        intake_actions: list[dict] = []
        try:
            surveyed = live_base.intake_candidate_tiles(
                client, surface, ore_output,
            )
            candidates = list(dict.fromkeys([
                ore_output, *(surveyed or ()),
            ]))
        except Exception:
            candidates = [ore_output]
        seen: set[Point] = set()
        directions = ((0.0, -1.0), (0.0, 1.0),
                      (-1.0, 0.0), (1.0, 0.0))
        for anchor in candidates:
            standing = live_base.entity_at(client, surface, anchor)
            if (
                standing is not None
                and standing.get("name") == "passive-provider-chest"
                and anchor not in seen
            ):
                intake_actions.append({
                    "action_type": "remove_entity",
                    "entity": "passive-provider-chest",
                    "position": {"x": anchor[0], "y": anchor[1]},
                })
                seen.add(anchor)
            for dx, dy in directions:
                inserter = (anchor[0] + dx, anchor[1] + dy)
                chest = (anchor[0] + 2 * dx, anchor[1] + 2 * dy)
                standing_inserter = live_base.entity_at(
                    client, surface, inserter,
                )
                standing_chest = live_base.entity_at(client, surface, chest)
                if not (
                    standing_inserter is not None
                    and standing_inserter.get("type") == "inserter"
                    and standing_chest is not None
                    and standing_chest.get("name") == "passive-provider-chest"
                ):
                    continue
                for position, entity in (
                    (inserter, standing_inserter["name"]),
                    (chest, "passive-provider-chest"),
                ):
                    if position in seen:
                        continue
                    intake_actions.append({
                        "action_type": "remove_entity",
                        "entity": entity,
                        "position": {"x": position[0], "y": position[1]},
                    })
                    seen.add(position)
        if intake_actions:
            intake_plan = {
                "surface": surface,
                "force": force,
                "phases": [{
                    "name": f"retire_{ore}_logistic_intake",
                    "actions": intake_actions,
                }],
            }
            _submit(
                client, bridge, surface, intake_plan,
                f"retire_{ore}_logistic_intake", emit,
            )
        emit(
            f"BOOTSTRAP SWAP COMPLETE: direct {recipe} refinery is healthy; "
            f"removed {legacy_removed} legacy requester cell(s) and their recognized "
            "mine-side logistic intake"
        )
    return removed


def _build_initial_plate_smelter(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, extraction, ore_output: Point, reference_point: Point,
    emit: Callable[[str], None], *, preflight_only: bool = False,
    allow_unfunded_ghosts: bool = False,
) -> Point | None:
    """Build the first modular refinery with one continuous mine-to-ore belt."""
    del reference_point
    plan, foundation, provider, belt_type, belt_tiles = _prepare_initial_refinery(
        client, surface, force, recipe, extraction, ore_output, emit,
        preflight_only=preflight_only,
        allow_unfunded_ghosts=allow_unfunded_ghosts,
    )
    if foundation is not None:
        _place_plate_expansion_foundation(
            client, bridge, surface, force, recipe, foundation, emit,
        )
    plan["surface"], plan["force"] = surface, force
    coverage_plan = {
        "surface": surface, "force": force,
        "phases": [
            action_phase for staged in (
                ([extraction.build_plan] if preflight_only and
                 getattr(extraction, "build_plan", None) is not None else [])
                + [plan]
            ) for action_phase in staged["phases"]
        ],
    }
    if preflight_only:
        # A dry run must not mutate the world: coverage roboports are real
        # infrastructure, and staging them for a plan that is never submitted
        # is exactly the wasted-chain failure this ordering exists to prevent.
        return provider
    # An earmarked refinery still needs one real power anchor. The rest of the
    # plan can remain ghosts while belts arrive, but a ghost-only power pole
    # leaves completed furnaces on an isolated network forever.
    if allow_unfunded_ghosts and live_base.available_items(
        client, surface, force,
    ).get("medium-electric-pole", 0) > 0:
        target = max(FURNACES_PER_MODULE, extraction.furnace_count)
        interface = refinery_interfaces(
            target,
            origin_x=extraction.smelter_origin[0],
            origin_y=extraction.smelter_origin[1], variant="basic",
        )
        for phase in plan["phases"]:
            for action in phase["actions"]:
                if (
                    action.get("entity") == "medium-electric-pole"
                    and (action["position"]["x"], action["position"]["y"])
                    == interface.power_anchor
                ):
                    action["action_type"] = "place_entity"
                    break
    _submit(
        client, bridge, surface, plan, f"modular_{recipe}_refinery", emit,
        stage_coverage=lambda: _ensure_plan_construction_coverage(
            client, bridge, surface, force, coverage_plan, emit,
        ),
        allow_unfunded_ghosts=allow_unfunded_ghosts,
    )
    if allow_unfunded_ghosts:
        # The provider chest is a future logistic endpoint, so cover it before
        # the first bot has an item to deliver. A construction-only chain was
        # the reason the furnace block could be built but never supplied.
        ensure_logistic_coverage(
            client, bridge, surface, force, [provider], emit,
        )
        target = max(FURNACES_PER_MODULE, extraction.furnace_count)
        interface = refinery_interfaces(
            target,
            origin_x=extraction.smelter_origin[0],
            origin_y=extraction.smelter_origin[1], variant="basic",
        )
        if live_base.entity_status_name(
            client, surface, interface.power_anchor,
        ) in {"no_power", "low_power"}:
            extend_power(
                client, bridge, surface, force, interface.power_anchor, emit,
                reserved_tiles=planned_footprint_tiles(plan),
            )
        emit(
            f"  BLUEPRINT EARMARK: {recipe} refinery is placed as ghosts; "
            "construction continues while the mall fills the bill"
        )
        return None
    _bring_modular_refinery_up(
        client, bridge, surface, force, recipe, plan,
        FURNACES_PER_MODULE,
        extraction.smelter_origin, emit,
        feed_grace_seconds=transport_grace_seconds(belt_type, belt_tiles),
        variant="basic",
    )
    _retire_standing_bootstrap_cells(
        client, bridge, surface, force, recipe, extraction.ore,
        ore_output, emit,
    )
    return provider

def _log_mining_expansion(extraction, emit: Callable[[str], None]) -> None:
    """Describe the selected drill phase without inflating the orchestration gate."""
    if extraction.build_plan is None:
        return
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


def build_mining_stage(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, reference_point: Point, emit: Callable[[str], None], *,
    expand: bool = False, earmark_unfunded: bool = False,
    require_direct: bool = False,
    excluded_drill_positions: tuple[Point, ...] = (),
) -> Point:
    """Build or expand one cohesive mine-to-smelter system.

    ``require_direct`` is used while replacing temporary startup production.
    A material shortage must then stay visible to the mall instead of selecting
    another starter.
    """
    ore = LINE_RECIPES[recipe]["ingredients"][0]
    # Retirement is deferred until the replacement mine and refinery are
    # built and healthy. Removing the only live source before prerequisite
    # machines are available can strand the entire supply chain.
    try:
        belt_type = _essential_belt_type(client, surface, force)
        extraction = plan_local_extraction(
            client, surface, force, recipe, reference_point, 3,
            belt_type=belt_type, inserter_type=_DEFAULT_INSERTER,
            reuse_existing=not expand,
            belt_stock=live_base.available_items(
                client, surface, force,
            ).get(belt_type, 0),
            excluded_drill_positions=excluded_drill_positions,
            reserved_refinery_areas=tuple(
                area for (reserved_surface, reserved_force, reserved_recipe), area
                in _REFINERY_SITE_RESERVATIONS.items()
                if (reserved_surface, reserved_force) == (surface, force)
                and reserved_recipe != recipe
            ),
        )
    except stage_extraction.PendingSystemDeferred as error:
        # The system serving this demand is still being built -- bots need
        # minutes, not another system. Ending the run here is how the landfill
        # mission died: the second survey under-counted a half-built mine,
        # opened a duplicate system, and its preflight collided with the
        # first system's own ore bridge.
        emit(f"PLATE SYSTEM PENDING: {error}")
        raise ProductionPrerequisiteDeferred(str(error)) from error
    except ValueError as error:
        raise StuckError(str(error)) from error
    reserved_area = getattr(extraction, "smelter_reserved_area", None)
    if reserved_area is not None:
        _REFINERY_SITE_RESERVATIONS[(surface, force, recipe)] = (
            reserved_area
        )
    if not expand:
        starved = False
        line = None
        try:
            line = live_base.find_line(
                client, surface, force, recipe,
                LINE_RECIPES[recipe]["machine"],
            )
            positions: set[Point] = set()
            if line is not None:
                positions |= set(line.machine_positions)
            idle_row = live_base.find_idle_machine_row(
                client, surface, force, recipe,
                LINE_RECIPES[recipe]["machine"],
                extraction.smelter_origin, radius=150.0,
            )
            if idle_row is not None:
                positions |= set(idle_row.machine_positions)
            if len(positions) >= FURNACES_PER_MODULE:
                statuses = live_base.entity_statuses(
                    client, surface, sorted(positions),
                )
                working = sum(
                    1 for state in statuses.values()
                    if state == "working"
                )
                starved = working <= len(positions) // 4
        except Exception:  # survey unavailable (dry harness): guard passes
            starved = False
        if starved:
            if _repair_unpowered_existing_mine(
                client, bridge, surface, force, extraction,
                extraction.ore_output, recipe, emit,
            ):
                raise ProductionPrerequisiteDeferred(
                    f"{recipe} mine power was repaired; waiting for ore delivery"
                )
            if line is None or getattr(line, "produced_count", 1) == 0:
                emit(
                    f"  REFINERY STARTUP PENDING: {recipe} has {working}/"
                    f"{len(positions)} furnace(s) fed but no completed plates; "
                    "holding this district for power/transport repair instead of "
                    "opening another mine phase"
                )
                raise ProductionPrerequisiteDeferred(
                    f"{recipe} direct refinery has not produced yet"
                )
            # A standing refinery that is mostly UNFED is an ore-supply
            # problem, not a capacity one (live run 15 built 24 stone
            # furnaces fed for three). User standard: keep the proper module
            # and grow ITS OWN mine instead -- one more drill row behind the
            # existing line feeds what the furnaces already draw.
            emit(
                f"  ORE STARVATION: {recipe} refinery runs at "
                f"{working}/{len(positions)} furnace(s) fed -- expanding its "
                "own mine by one phase instead of adding capacity"
            )
            return build_mining_stage(
                client, bridge, surface, force, recipe,
                reference_point, emit, expand=True,
                excluded_drill_positions=excluded_drill_positions,
            )
    bootstrap_cap = BOOTSTRAP_FURNACE_CAPS.get(recipe)
    if (
        bootstrap_cap is not None
        and getattr(extraction, "furnace_count", 0) > bootstrap_cap
        and not _electric_furnace_producer_started(client, surface, force)
    ):
        emit(
            f"BOOTSTRAP FURNACE CAP: deferring {recipe} expansion at "
            f"{bootstrap_cap} furnace(s) until electric-furnace production is working "
            f"(planned {extraction.furnace_count})"
        )
        raise ProductionPrerequisiteDeferred(
            f"{recipe} expansion waits for electric-furnace production after "
            f"the {bootstrap_cap}-furnace bootstrap cap"
        )
    existing_smelter, cohesive_target = _cohesive_smelter_target(
        client, surface, force, recipe, extraction, expand, emit,
    )
    if expand and existing_smelter is None:
        raise ProductionPrerequisiteDeferred(
            f"{recipe} expansion has no recoverable managed refinery; refusing to "
            "expand its mine ahead of the refinery"
        )
    foundation = None
    if cohesive_target is not None:
        try:
            foundation = _assert_atomic_plate_expansion_affordable(
                client, surface, force, recipe, extraction, existing_smelter,
                cohesive_target, emit,
                allow_unfunded_ghosts=earmark_unfunded,
            )
        except StuckError as error:
            if "refinery extension intersects real infrastructure" not in str(error):
                raise
            raise ProductionPrerequisiteDeferred(str(error)) from error
    if expand:
        _log_mining_expansion(extraction, emit)
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

    if foundation is not None:
        _place_plate_expansion_foundation(
            client, bridge, surface, force, recipe, foundation, emit,
        )
    if cohesive_target is None:
        try:
            _build_initial_plate_smelter(
                client, bridge, surface, force, recipe, extraction, ore_output,
                reference_point, emit, preflight_only=True,
                allow_unfunded_ghosts=earmark_unfunded,
            )
        except StuckError as error:
            # Genuinely foreign infrastructure at the surveyed site is not a
            # material shortage: queuing its bill as a mall demand livelocked
            # the whole run on transport-belt the base could never afford.
            # Defer instead -- prep retries when demand rises, and the stall
            # report names the exact tiles.
            if "intersects real infrastructure" not in str(error):
                raise
            raise ProductionPrerequisiteDeferred(str(error)) from error
        except MaterialShortage as error:
            # Before the direct starter exists, belts made from this same plate
            # are circular. The startup path normally prevents reaching this
            # fallback; it remains restart-safe for a cold interrupted pass.
            if not _cold_start_belt_shortage(client, surface, force, recipe, error):
                raise
            emit(
                f"  PLATE BOOTSTRAP PENDING: {error.required} short for the "
                "first system; the mine still builds and the one-drill "
                "direct starter follows this pass"
            )
    if cohesive_target is not None:
        provider = _extend_plate_smelter(
            client, bridge, surface, force, recipe, existing_smelter,
            cohesive_target, ore_output, emit,
            allow_unfunded_ghosts=earmark_unfunded,
        )
        _submit_mining_plan(
            client, bridge, surface, force, extraction, ore_output, emit,
            allow_unfunded_ghosts=earmark_unfunded,
        )
    else:
        _submit_mining_plan(
            client, bridge, surface, force, extraction, ore_output, emit,
            allow_unfunded_ghosts=earmark_unfunded,
        )
        try:
            provider = _build_initial_plate_smelter(
                client, bridge, surface, force, recipe, extraction, ore_output,
                reference_point, emit,
                allow_unfunded_ghosts=earmark_unfunded,
            )
        except StuckError as error:
            if "intersects real infrastructure" not in str(error):
                raise
            raise ProductionPrerequisiteDeferred(str(error)) from error
        except MaterialShortage as error:
            if not _cold_start_belt_shortage(client, surface, force, recipe, error):
                raise
            if require_direct:
                raise
            provider = _bootstrap_direct_plate_line(
                client, bridge, surface, force, recipe, reference_point, emit,
            )
    try:
        retire_depleted_mines(
            client, bridge, surface, force, ore, reference_point, emit,
        )
    except RuntimeError as error:
        raise StuckError(str(error)) from error
    # The plate line now exists (or its bootstrap cell does): an earlier pass
    # may have recorded this recipe as an unbacked draw when nothing produced
    # it. A stale entry would keep telling every later stall report that
    # nothing makes it -- observed live on a saturated copper provider.
    UNBACKED_DRAWS.discard(recipe)
    return provider


def _serve_direct_plate_starter(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, ore: str, starter: live_base.DirectPlateStarter,
    emit: Callable[[str], None], *, submit: bool,
) -> Point:
    """Build or service the removable direct drill -> furnace starter."""
    drill_count = 2 if recipe in {"iron-plate", "stone-brick"} else 1
    furnace_count = 2 if recipe == "iron-plate" else 1
    positions = direct_smelter_positions(
        starter.drill_position, starter.output_direction,
        pole_side=starter.pole_side,
        drill_count=drill_count,
        furnace_count=furnace_count,
    )
    plan = generate_direct_smelter(
        recipe, ore, starter.drill_position, starter.output_direction,
        pole_side=starter.pole_side,
    )
    plan["surface"], plan["force"] = surface, force
    if submit:
        _submit(
            client, bridge, surface, plan, f"direct_{recipe}_starter", emit,
            stage_coverage=lambda: _ensure_plan_construction_coverage(
                client, bridge, surface, force, plan, emit,
            ),
        )
    area = _plan_area(plan, padding=10.0)
    machines = [
        positions[key] for key in (
            "drill", "secondary_drill", "furnace", "secondary_furnace",
        ) if key in positions
    ]
    bring_stage_up(
        client, bridge, surface, force, f"direct starter for {recipe}",
        starter.drill_position, area, positions["power"], machines, emit,
        logistic_chest_positions=[positions["provider"]],
    )
    stuck = _diagnose_machines(
        client, surface, machines, emit, bridge=bridge, force=force,
        grace_seconds=60.0,
    )
    if stuck:
        raise StuckError(f"direct starter for {recipe} built but not healthy: {stuck}")
    _record_bootstrap_pioneer(recipe, ore, plan)
    UNBACKED_DRAWS.discard(recipe)
    return positions["provider"]


def _bootstrap_direct_plate_line(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, reference_point: Point, emit: Callable[[str], None],
) -> Point:
    """Ensure one local direct plate or brick starter without bots or belts."""
    global _STARTUP_METAL_STARTERS_OBSERVED
    ore = LINE_RECIPES[recipe]["ingredients"][0]
    standing = live_base.direct_plate_starter(
        client, surface, force, recipe, ore, reference_point,
    )
    if standing is not None:
        if recipe in {"iron-plate", "copper-plate"}:
            _STARTUP_METAL_STARTERS_OBSERVED = True
        emit(
            f"  PLATE STARTER: reusing the direct {recipe} stack at "
            f"{standing.drill_position}"
        )
        return _serve_direct_plate_starter(
            client, bridge, surface, force, recipe, ore, standing, emit,
            submit=False,
        )
    site = live_base.direct_plate_starter_site(
        client, surface, force, ore, reference_point,
    )
    if site is None:
        raise StuckError(
            f"no legal direct {recipe} starter fits on a {ore} patch within "
            "the local search radius"
        )
    drill_count = 2 if recipe in {"iron-plate", "stone-brick"} else 1
    furnace_count = 2 if recipe == "iron-plate" else 1
    emit(
        f"PLATE STARTER: {recipe} begins with {drill_count} drill(s) feeding "
        f"{furnace_count} furnace(s) "
        f"directly at {site.drill_position}; no belts, requesters, or bot haul"
    )
    if recipe in {"iron-plate", "copper-plate"}:
        _STARTUP_METAL_STARTERS_OBSERVED = True
    return _serve_direct_plate_starter(
        client, bridge, surface, force, recipe, ore, site, emit, submit=True,
    )


# A plate refinery's bill is belts AND the inserters that feed its furnaces --
# both are made FROM this very plate. On a cold base either shortfall is
# unaffordable forever even with perfect mall behaviour: the demand for the
# system's own inputs feeding back into itself.
_BOOTSTRAP_CIRCULAR_ENTITIES = frozenset({
    "transport-belt", "underground-belt",
    "fast-transport-belt", "fast-underground-belt",
    "express-transport-belt", "express-underground-belt",
    "inserter", "fast-inserter", "bulk-inserter", "stack-inserter",
})


def _cold_start_belt_shortage(
    client: RconClient, surface: str, force: str, recipe: str,
    error: Exception,
) -> bool:
    """Whether the FIRST plate system predates direct starter production.

    Only then may the one-drill direct starter absorb a circular belt/inserter
    shortage. Once one furnace exists, the complete system's bill must remain
    visible. A legacy requester cell counts as cold solely so an old save can
    migrate through the direct starter instead of creating another cell."""
    if recipe not in ("iron-plate", "copper-plate"):
        return False
    required = getattr(error, "required", None)
    if not required or not set(required).issubset(_BOOTSTRAP_CIRCULAR_ENTITIES):
        return False
    line = live_base.find_line(
        client, surface, force, recipe, LINE_RECIPES[recipe]["machine"],
    )
    if line is None:
        return True
    positions = getattr(line, "machine_positions", None)
    return positions is not None and (
        logistic_smelter_origin(positions) is not None
    )


def _essential_belt_type(
    client: RconClient, surface: str, force: str,
) -> str:
    """Belt tier for the FIRST mine/refinery systems.

    The cold-start mall produces regular belts. Selecting fast belts from a
    partial starter stock made the copper foundation demand 126 fast belts
    while the fast-belt producer was intentionally gated, even after regular
    belt production was healthy. Tier upgrades belong after both foundations.
    """
    del client, surface, force
    return _DEFAULT_BELT


_GENERATION_CHECK_INTERVAL_TICKS = 1800  # 30s of game time between grid checks


def _top_up_solar_generation(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    near: Point, emit: Callable[[str], None], *,
    ensure_main_connection: bool = True,
) -> bool:
    """Join the primary grid, then build one validated power unit if needed."""
    capacity_near = near
    if ensure_main_connection:
        outcome = extend_power(
            client, bridge, surface, force, near, emit, detailed=True,
        )
        # Compatibility with narrow harnesses that still stub the legacy bool.
        if getattr(outcome, "changed", bool(outcome)):
            emit("POWER DISTRICT: submitted a primary-grid bridge; waiting for it to connect")
            return True
        if getattr(outcome, "ready", bool(outcome)):
            emit("POWER DISTRICT: already covered by the selected primary grid; sizing capacity")
            primary = live_base.nearest_powered_pole(
                client, surface, force, near,
            )
            if primary is not None:
                capacity_near = primary[0]
    script_output = getattr(bridge, "script_output", Path(""))
    return ensure_power_capacity(
        client=client, bridge=bridge, surface=surface, force=force,
        near=capacity_near, script_output=script_output, emit=emit, submit=_submit,
    )


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
            "belt" if recipe == "steel-plate"
            else "logistic" if allow_logistic_inputs
            else _transport_mode(recipe, ingredient, machine_count)
        )
        for ingredient in LINE_RECIPES[recipe]["ingredients"]
    }
    direct_belt_input = (
        recipe in {"iron-plate", "copper-plate", "steel-plate"}
        and len(modes) == 1
        and next(iter(modes.values())) == "belt"
    )
    if direct_belt_input:
        modes[next(iter(modes))] = "belt"
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


def _ensure_plan_construction_coverage(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    plan: dict, emit: Callable[[str], None], *,
    reserved_tiles: set[tuple[int, int]] | None = None,
) -> None:
    """Extend construction coverage to every action position the plan places.

    Coverage is demanded for POSITIONS, not for the four corners of their
    bounding box: a pipe route spanning 350 tiles put two of its box corners
    on empty map with no action anywhere near them, and eleven roboports plus
    their power chains were strung out to serve nothing. Positions already
    inside an existing port's service area are skipped locally; only genuinely
    uncovered positions chain new ports."""
    if not hasattr(client, "command"):
        return
    # Keep chained 4x4 roboports off every footprint the plan is about to
    # reserve; a roboport centre can be clear while still covering a pending
    # pole or machine ghost, and the executor's centre-only check would accept
    # the overlap and leave an unbuildable ghost behind.
    if reserved_tiles is None:
        reserved_tiles = planned_footprint_tiles(plan)
    positions = sorted({
        (action["position"]["x"], action["position"]["y"])
        for phase in plan.get("phases", [])
        for action in phase.get("actions", [])
        if "position" in action
    })
    if not positions:
        return
    radius, square = _ROBOPORT_SERVICE_AREAS["construction"]
    ports = live_base.roboport_positions(client, surface, force)

    def uncovered(targets: list[Point]) -> list[Point]:
        return [
            target for target in targets
            if all(
                service_distance(port, target, square=square) > radius
                for port in ports
            )
        ]

    pending = uncovered(positions)
    for _ in range(64):
        if not pending:
            return
        target = pending[0]
        acted = extend_roboport_coverage(
            client, bridge, surface, force, target, emit,
            reserved_tiles=reserved_tiles,
        )
        ports = live_base.roboport_positions(client, surface, force)
        pending = uncovered(pending)
        if pending and pending[0] == target and not acted:
            raise StuckError(
                f"{target} needs construction coverage but the surface has no "
                "roboport to chain from"
            )
    raise StuckError(
        "construction coverage did not converge after 64 chain attempts; "
        "investigate the coverage survey"
    )


def _prepare_replacement_services(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    replacement: dict, delta: dict, emit: Callable[[str], None],
) -> None:
    """Make a replacement footprint safe before its owned removals run.

    Factorio's super-force placement is a player interaction, not an executor
    API. The planner therefore uses the safe equivalent: only a delta that has
    already passed its ownership check may replace infrastructure, and all
    coverage/power needed by the replacement is staged first. A roboport is a
    service dependency rather than a disposable obstruction; removing one
    without an alternate chain would strand construction bots, so that case is
    rejected until a caller supplies an explicit relocation plan.
    """
    if not hasattr(client, "command"):
        return
    removals = [
        action for phase in delta.get("phases", [])
        for action in phase.get("actions", [])
        if action.get("action_type") == "remove_entity"
    ]
    if not removals:
        _ensure_plan_construction_coverage(
            client, bridge, surface, force, replacement, emit,
        )
        return
    if any(action.get("entity") == "roboport" for action in removals):
        raise StuckError(
            "replacement would remove a roboport before an alternate coverage "
            "chain exists; route around it or stage the replacement chain first"
        )

    # Use the complete future footprint, not just the delta. This matters when
    # the old End is removed first: a chain derived from the partial delta can
    # leave the newly-added Repeat rows outside construction range.
    reserved = planned_footprint_tiles(replacement)
    _ensure_plan_construction_coverage(
        client, bridge, surface, force, replacement, emit,
        reserved_tiles=reserved,
    )
    power_targets = sorted({
        (action["position"]["x"], action["position"]["y"])
        for phase in replacement.get("phases", [])
        for action in phase.get("actions", [])
        if action.get("entity") in {
            "substation", "medium-electric-pole", "big-electric-pole",
        }
    })
    for target in power_targets:
        # False means the target is already on a generating network (or no
        # generator exists yet); it is not a reason to tear down the old path.
        extend_power(
            client, bridge, surface, force, target, emit,
            reserved_tiles=reserved,
        )


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
    destination_belt_direction: str,
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
                destination_belt_direction=destination_belt_direction,
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


def _recover_partial_conversion_power(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, substation_position: Point, emit: Callable[[str], None],
) -> None:
    """Bridge a scaffold left behind when a later conversion action failed."""
    if live_base.pole_network_id(client, surface, substation_position) is None:
        return
    emit(
        f"CONVERSION RECOVERY: {recipe} submit failed after scaffolding; "
        f"connecting its substation at {substation_position}"
    )
    try:
        extend_power(client, bridge, surface, force, substation_position, emit)
    except StuckError as error:
        emit(f"  CONVERSION RECOVERY: power bridge unavailable: {error}")


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
    try:
        _submit(
            client, bridge, surface, plan, f"conversion_{recipe}", emit,
            stage_coverage=lambda: _ensure_plan_construction_coverage(
                client, bridge, surface, force, plan, emit,
            ),
        )
    except StuckError:
        _recover_partial_conversion_power(
            client, bridge, surface, force, recipe, substation_position, emit,
        )
        raise
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
        destination_belt_direction=flow_direction,
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
    production_target: int
    mall_storage_limit: int
    fill_provider: bool
    mall_request_multiplier: int | None
    demand: float
    saturated: bool
    promoted_count: int | None
    promote_to_line: bool
    at_size: bool


def _plan_line(
    client: RconClient, surface: str, force: str, item: str,
    emit: Callable[[str], None], *, upgrade_bootstrap: bool, stock_target: int,
    minimum_machines: int, allow_promotion: bool,
    storage_limit: int | None = None, fill_provider: bool = False,
) -> _LinePlan:
    """Survey the item's current line and decide whether it should be promoted."""
    spec = LINE_RECIPES[item]
    startup_cap = _startup_mall_item_cap(client, surface, force, item)
    mall_storage_limit = (
        startup_cap
        if not upgrade_bootstrap and startup_cap is not None
        else max(stock_target, storage_limit or stock_target)
    )
    if not upgrade_bootstrap and storage_limit is None and startup_cap is None:
        mall_storage_limit += live_base.logistic_request_total(
            client, surface, force, item,
        )
    existing = live_base.find_line(client, surface, force, item, spec["machine"])
    mall_request_multiplier = _mall_request_multiplier(
        client, surface, force, item, spec,
    )
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
        existing=existing, spec=spec, production_target=stock_target,
        mall_storage_limit=mall_storage_limit, fill_provider=fill_provider,
        mall_request_multiplier=mall_request_multiplier,
        demand=demand, saturated=saturated, promoted_count=promoted_count,
        promote_to_line=promote_to_line,
        at_size=existing is None or existing.machine_count >= minimum_machines,
    )


def _refresh_mall_cell(
    client: RconClient, bridge: GameBridge, surface: str, force: str, item: str,
    plan: _LinePlan, emit: Callable[[str], None], *, upgrade_bootstrap: bool,
    reference_point: Point = (3.0, -1.0),
    stock_gate_target: int | None = None,
) -> Point | None:
    """Re-apply a live mall cell's request group and provider limit.

    Returns the paired provider position, which the caller needs both to serve
    the cell's output and to retire it once a shared line replaces it.
    """
    existing, spec = plan.existing, plan.spec
    mall_storage_limit = plan.mall_storage_limit
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
                stock_target=plan.production_target,
                product_amount=spec.get("product_amount", 1),
            )
            request_plan["surface"], request_plan["force"] = surface, force
            _submit(
                client, bridge, surface, request_plan,
                f"compact_mall_requests_{item}", emit,
            )

    if existing and not _mineable(item):
        if getattr(plan, "mall_request_multiplier", None) is not None and not upgrade_bootstrap:
            refresh_paired_mall_requests(
                client, bridge, surface, force, item,
                list(existing.machine_positions), reference_point, emit,
                request_multiplier_override=plan.mall_request_multiplier,
            )
        mall_provider = _paired_mall_provider(
            client, surface, existing.machine_positions,
        )
        if mall_provider is not None and not upgrade_bootstrap:
            capacity = (
                "the full chest"
                if plan.fill_provider
                else str(mall_storage_limit)
            )
            emit(f"  MALL RESERVE: {item} provider at {mall_provider} holds {capacity}")
            limit_plan = generate_mall_provider_limit_update(
                item, mall_provider, mall_storage_limit,
                fill_chest=plan.fill_provider,
            )
            limit_plan["surface"], limit_plan["force"] = surface, force
            _submit(
                client, bridge, surface, limit_plan,
                f"mall_provider_limit_{item}", emit,
            )
            gate_plan = generate_mall_stock_gate_update(
                item, spec["machine"], list(existing.machine_positions),
                stock_gate_target,
            )
            gate_plan["surface"], gate_plan["force"] = surface, force
            _submit(
                client, bridge, surface, gate_plan,
                f"mall_stock_gate_{item}", emit,
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
        emit(f"  BOOTSTRAP UPGRADE: replacing requester-fed {item} with belt transport")
        build_mining_stage(
            client, bridge, surface, force, item, reference_point, emit,
            require_direct=True,
        )
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
    mall_provider: Point | None, upgrade_bootstrap: bool,
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
    pre_statuses = live_base.entity_statuses(
        client, surface, existing.machine_positions,
    )
    if pre_statuses and all(
        status in {"working", "full_output", "disabled_by_control_behavior"}
        for status in pre_statuses.values()
    ):
        kinds = sorted(set(pre_statuses.values()))
        emit(
            f"  REPAIR CLASSIFICATION: {item} is not structurally broken "
            f"({', '.join(kinds)}); waiting for supply/output demand"
        )
        return (
            mall_provider
            or live_base.nearest_container(
                client, surface, force, existing.machine_positions[-1],
                names=("passive-provider-chest",),
            )
            or existing.output_position
        )
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
        # A declared feed chest that is missing or has lost its request group
        # can never recover by waiting: regenerate the cell's own plan first.
        if mall_cell_needs_rebuild(
            client, surface, item,
            existing.machine_positions[0], reference_point,
        ) and rebuild_incomplete_mall_cell(
            client, bridge, surface, force, item,
            existing.machine_positions[0], reference_point, emit,
        ):
            return None
        # Paired mall cells intentionally use a six-tile machine spacing and
        # requester/provider side-taps. They are valid compact topology, not
        # the three-tile deterministic belt line reconstructed by
        # ``repair_existing_ingredient_transport``. Treat the cell as a
        # supply wait here; attempting line repair is what turned a healthy
        # mall cell into a terminal geometry error in live runs.
        if mall_provider is not None:
            emit(
                f"  MALL WAIT: existing {item} paired cell is supply-starved; "
                "keeping its compact requester transport while inputs recover"
            )
            return mall_provider
        if not upgrade_bootstrap:
            chest = mall_provider or live_base.nearest_container(
                client, surface, force, existing.machine_positions[-1],
                names=("passive-provider-chest",),
            )
            emit(
                f"  MALL WAIT: existing {item} cell is supply-starved; "
                "keeping its current transport while bootstrap production catches up"
            )
            return chest or existing.output_position
        try:
            repaired = repair_existing_ingredient_transport(
                client, bridge, surface, force, item, existing.machine_positions,
                lambda ingredient: ensure_produced(
                    client, bridge, surface, force, ingredient, reference_point, emit,
                    upgrade_bootstrap=upgrade_bootstrap,
                ),
                emit,
            )
        except StuckError as error:
            # A paired mall cell whose feed infrastructure was never built is
            # incomplete CONSTRUCTION, not unrepairable geometry: live run 32
            # died because its advanced-circuit assembler had no requester
            # chest at all. Regenerate that cell's own declared plan in place
            # (idempotent) and let the next pass re-survey; anything outside a
            # declared cell half still fails exactly as before.
            if rebuild_incomplete_mall_cell(
                client, bridge, surface, force, item,
                existing.machine_positions[0], reference_point, emit,
            ):
                return None
            raise
        if repaired:
            return None
    idle = {
        position: status
        for position, status in (statuses or {}).items()
        if status != "working"
    }
    if idle:
        # Live run 2026-08-22 22:02: two science assemblers sat at low_power
        # while this function returned silently, burning 116 passes in ~50s.
        # Name the residual cause so the next pass is a decision, not a mystery.
        sample = "; ".join(
            f"{status}@({position[0]:.0f},{position[1]:.0f})"
            for position, status in sorted(idle.items())[:3]
        )
        emit(
            f"  REPAIR DIAGNOSIS: {item} still has {len(idle)} non-working "
            f"machine(s) after remediation: {sample}"
        )
        consume_wait(f"{item}#repair_settle")
        time.sleep(5)
    return None


# Ingredients a mall cell drew from stock while NOTHING was producing them.
# Stock is a buffer, not a supply: a draw with no line behind it is the player's
# starter chest being eaten, and the run stalls the moment it runs out. Recorded
# rather than refused -- refusing deadlocks, because the mall must be able to
# build the very assemblers the producing lines are made of.
UNBACKED_DRAWS: set[str] = set()


# These are intermediate goods, not one-off mall stock. When a construction
# recipe consumes the starter reserve for them, start a real producer first so
# the reserve is a bootstrap input rather than the only supply behind the mall.
# Steel is a small logistic-fed furnace line because an unset furnace recipe
# cannot be rediscovered by ``find_line`` later.
# Advanced-circuit and steel-chest sit on the electric-furnace unlock chain
# (GATE WORK): drawing them from starter stock without a producer held that
# gate at 83% for twelve passes (live run 15).
PERSISTENT_INTERMEDIATES = frozenset({
    "iron-stick", "steel-plate", "advanced-circuit", "steel-chest",
})
MANAGED_INTERMEDIATE_SOURCES: dict[str, Point] = {}

# The direct iron/copper stacks are deliberately temporary. Their tiny mall
# ceilings protect the first plates from being converted into construction
# components before the belt-fed metal systems take over.
_STARTUP_METAL_STARTERS_OBSERVED = False
_STARTUP_MALL_LIMITS_RELEASED = False
_STARTUP_MALL_LIMITS_FALLBACK_PROBED = False
_STARTUP_MALL_ITEM_CAPS = {
    "electronic-circuit": 5,
    "splitter": 2,
    "underground-belt": 5,
}
_POST_STARTER_ONE_STACK_ITEMS = frozenset({"splitter", "underground-belt"})
_STARTUP_MALL_REQUESTER_ITEMS = frozenset({"splitter", "underground-belt"})


def _mall_request_multiplier(
    client: RconClient, surface: str, force: str, item: str, spec: dict,
) -> int | None:
    """Keep belt-component requester buffers small while starter metal is scarce."""
    if item == "transport-belt":
        return 30
    if item not in _STARTUP_MALL_REQUESTER_ITEMS:
        return None
    if not _metal_starter_transition_complete(client, surface, force):
        return 2
    return standard_mall_request_multiplier(spec["machine"], spec["craft_time"])


def _startup_mall_item_cap(
    client: RconClient, surface: str, force: str, item: str,
) -> int | None:
    """The active provider ceiling for parts that would drain starter metal."""
    if item not in _STARTUP_MALL_ITEM_CAPS:
        return None
    if _metal_starter_transition_complete(client, surface, force):
        return None
    return _STARTUP_MALL_ITEM_CAPS[item]


def _effective_mall_stock_gate(
    client: RconClient, surface: str, force: str, item: str, *,
    upgrade_bootstrap: bool, requested: int | None,
) -> int | None:
    """Apply scarce-metal caps to the assembler, not only its output chest."""
    if upgrade_bootstrap:
        return requested
    startup_cap = _startup_mall_item_cap(client, surface, force, item)
    if startup_cap is None:
        return requested
    return startup_cap if requested is None else min(startup_cap, requested)

def _has_producer(
    client: RconClient, surface: str, force: str, ingredient: str,
) -> bool:
    """Whether anything on the base is actually making `ingredient`."""
    if ingredient in MANAGED_INTERMEDIATE_SOURCES:
        return True
    if ingredient not in LINE_RECIPES:
        return True  # mined or externally supplied; not ours to produce
    line = live_base.find_line(
        client, surface, force, ingredient, LINE_RECIPES[ingredient]["machine"],
    )
    return line is not None and line.machine_count > 0


def _material_project_id(item: str) -> str:
    return f"paired_mall_{item}"


def _material_sources_and_rates(
    client: RconClient, surface: str, force: str,
    bill: Mapping[str, int], stock: Mapping[str, int],
) -> tuple[dict[str, str | None], dict[str, float | None]]:
    """Describe how each reserved item can arrive, without creating work."""
    sources: dict[str, str | None] = {}
    rates: dict[str, float | None] = {}
    for item, required in bill.items():
        if stock.get(item, 0) >= required:
            sources[item], rates[item] = "stock", None
            continue
        spec = LINE_RECIPES.get(item)
        if spec is None:
            sources[item], rates[item] = None, None
            continue
        line = live_base.find_line(
            client, surface, force, item, str(spec["machine"]),
        )
        if line is None:
            sources[item], rates[item] = None, None
            continue
        speed = MACHINE_SPEEDS.get(str(spec["machine"]), 1.0)
        rate = (
            line.working_count * speed
            * float(spec.get("product_amount", 1))
            / max(float(spec["craft_time"]), 1e-9)
        )
        sources[item] = f"producer:{item}"
        rates[item] = rate if rate > 0 else None
    return sources, rates


_BOOTSTRAP_LOAN_CANDIDATES = ("copper-cable", "iron-gear-wheel")


def _bootstrap_loan_stock(
    client: RconClient, surface: str, force: str, target_item: str,
) -> tuple[dict[str, int], dict[str, int]]:
    """Physical stock plus the share not reserved away from this producer."""
    actual = live_base.available_items(client, surface, force)
    ledger = _MATERIAL_RESERVATION_LEDGER
    usable = (
        ledger.allocatable_stock(actual, claimant=_material_project_id(target_item))
        if ledger is not None else dict(actual)
    )
    return actual, usable


def _submit_bootstrap_loan(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    loan: MallBootstrapLoan, emit: Callable[[str], None],
) -> str:
    actual, usable = _bootstrap_loan_stock(
        client, surface, force, loan.target_item,
    )
    step = next_bootstrap_step(
        loan.target_item, loan.target_count, usable, actual,
    )
    if step is None:
        plan = restore_bootstrap_loan_plan(loan)
        plan["surface"], plan["force"] = surface, force
        _submit(
            client, bridge, surface, plan,
            f"restore_bootstrap_loan_{loan.target_item}", emit,
        )
        emit(
            f"  MALL BOOTSTRAP LOAN RESTORED: {loan.original_recipe} at "
            f"{loan.machine_position}; {loan.target_count} {loan.target_item} "
            "seed item(s) are now stocked"
        )
        return f"restored borrowed {loan.original_recipe} producer after seed completion"

    if loan.current_recipe != step.recipe:
        plan = bootstrap_loan_plan(loan, step)
        plan["surface"], plan["force"] = surface, force
        _submit(
            client, bridge, surface, plan,
            f"bootstrap_loan_{loan.target_item}", emit,
        )
        emit(
            f"  MALL BOOTSTRAP LOAN: borrowed {loan.original_recipe} at "
            f"{loan.machine_position} to make {step.recipe} through stock "
            f"{step.target_count}; requester now asks for exactly "
            f"{step.crafts} craft(s) of ingredients"
        )
    else:
        emit(
            f"  MALL BOOTSTRAP LOAN WAIT: {step.recipe} at {loan.machine_position} "
            f"is making temporary stock through {step.target_count}"
        )
        _deliver_cell_ingredients(
            client, bridge, surface, force, step.recipe,
            loan.machine_position, emit,
        )
    return (
        f"borrowed {loan.original_recipe} cell is producing temporary "
        f"{step.recipe} for the {loan.target_item} seed"
    )


def _service_bootstrap_loan(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    target_item: str, emit: Callable[[str], None],
) -> str | None:
    loans = active_bootstrap_loans(client, surface, force)
    if len(loans) > 1:
        raise StuckError(
            f"Found {len(loans)} simultaneous mall bootstrap loans; only one "
            "planner-owned cell may be borrowed at a time",
            code="multiple_bootstrap_mall_loans",
            classification="bug",
            details={"loans": [loan.group for loan in loans]},
        )
    if not loans or loans[0].target_item != target_item:
        return None
    return _submit_bootstrap_loan(
        client, bridge, surface, force, loans[0], emit,
    )


def _start_bootstrap_loan(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    target_item: str, target_count: int, reference_point: Point,
    emit: Callable[[str], None],
) -> str | None:
    existing = active_bootstrap_loans(client, surface, force)
    if existing:
        if len(existing) == 1 and existing[0].target_item == target_item:
            return _submit_bootstrap_loan(
                client, bridge, surface, force, existing[0], emit,
            )
        return None
    for original_recipe in _BOOTSTRAP_LOAN_CANDIDATES:
        spec = LINE_RECIPES.get(original_recipe)
        if spec is None:
            continue
        line = live_base.find_line(
            client, surface, force, original_recipe, str(spec["machine"]),
        )
        if line is None or line.machine_count < 2:
            continue
        for machine_position in reversed(line.machine_positions):
            located = locate_mall_cell(machine_position, reference_point)
            if located is None:
                continue
            origin, side = located
            requester_position = (origin[0] + 4.5, origin[1] + 1.5)
            machine = live_base.entity_at(client, surface, machine_position)
            requester = live_base.entity_at(client, surface, requester_position)
            if (
                not machine or machine["name"] != "assembling-machine-2"
                or not requester or requester["name"] != "requester-chest"
            ):
                continue
            loan = MallBootstrapLoan(
                original_recipe=original_recipe,
                target_item=target_item,
                target_count=target_count,
                side=side,
                requester_position=requester_position,
                current_recipe=original_recipe,
            )
            return _submit_bootstrap_loan(
                client, bridge, surface, force, loan, emit,
            )
    return None


def _reserve_compact_mall_project(
    client: RconClient, surface: str, force: str, item: str,
    plan: _LinePlan, stock_gate_target: int | None,
    emit: Callable[[str], None], *, bridge: GameBridge | None = None,
    reference_point: Point | None = None,
) -> None:
    """Protect the complete cell and its first craft before recursion starts."""
    ledger = _MATERIAL_RESERVATION_LEDGER
    if ledger is None:
        return
    project_id = _material_project_id(item)
    bill = compact_mall_project_bill(
        item, stock_target=plan.mall_storage_limit,
        stock_gate_target=stock_gate_target,
        fill_chest=plan.fill_provider,
        request_multiplier_override=plan.mall_request_multiplier,
    )
    stock = live_base.available_items(client, surface, force)
    sources, rates = _material_sources_and_rates(
        client, surface, force, bill, stock,
    )
    project = ledger.declare(
        project_id, bill, stock, target_item=item,
        source_producers=sources, expected_rates=rates,
        priority=100, hold_until_producing=True,
    )
    shortage = ledger.shortage_targets(project_id, stock)
    emit(
        f"  MATERIAL PROJECT: {project_id} state={project.state} "
        f"bill=" + ",".join(f"{name}:{count}" for name, count in bill.items())
    )
    if not shortage:
        emit(f"  MATERIAL PROJECT READY: {project_id} has its complete startup bill")
        return
    emit(
        f"  MATERIAL PROJECT WAIT: {project_id} needs total stock "
        + ", ".join(f"{name}={count}" for name, count in shortage.items())
    )
    if item in shortage and bridge is not None and reference_point is not None:
        remedy = _start_bootstrap_loan(
            client, bridge, surface, force, item, shortage[item],
            reference_point, emit,
        )
        if remedy is not None:
            raise ProductionPrerequisiteDeferred(remedy)
    if item in shortage and not _has_producer(client, surface, force, item):
        raise StuckError(
            f"{project_id} needs {shortage[item]} {item} seed item(s) before "
            "its own producer can be constructed",
            code="producer_bootstrap_seed_shortage",
            classification="intended_difficulty",
            state="supply_wait",
            details={
                "project_id": project_id,
                "item": item,
                "required_stock": shortage[item],
                "available_stock": stock.get(item, 0),
                "bill": bill,
            },
        )
    raise MaterialShortage(project_id, shortage, stock)


def _complete_material_producer(item: str) -> None:
    if _MATERIAL_RESERVATION_LEDGER is not None:
        _MATERIAL_RESERVATION_LEDGER.complete(_material_project_id(item))


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
        plan.production_target / max(1, spec.get("product_amount", 1)),
    )
    # A compact mall cell is a producer, not a request to consume the entire
    # future stock target up front. Requiring all target inputs before placing
    # its first machine deadlocks bootstrap items: transport-belt wanted 58
    # plates before the iron line could exist. One craft seeds the requester;
    # the running producer then draws the remainder through the network. A
    # promoted line still needs its full declared production bill.
    setup_crafts = crafts_needed if promote_to_line else 1
    for ingredient, amount in zip(spec["ingredients"], spec["amounts"], strict=True):
        required = math.ceil(amount * setup_crafts)
        managed_source = MANAGED_INTERMEDIATE_SOURCES.get(ingredient)
        if managed_source is not None:
            sources[ingredient] = managed_source
            continue
        if not upgrade_bootstrap and stocked.get(ingredient, 0) >= required:
            backed = _has_producer(client, surface, force, ingredient)
            emit(
                f"  MALL BOOTSTRAP: using stocked {ingredient} "
                f"({stocked[ingredient]}/{required}) for {item}"
                + ("" if backed else " -- NOTHING IS PRODUCING IT")
            )
            if not backed and ingredient in PERSISTENT_INTERMEDIATES:
                emit(
                    f"  MALL BOOTSTRAP: scheduling a persistent {ingredient} "
                    "producer before consuming the reserve"
                )
                position = ensure_produced(
                    client, bridge, surface, force, ingredient, reference_point,
                    emit, upgrade_bootstrap=False, stock_target=max(1, required),
                )
                if position is None:
                    return None
                MANAGED_INTERMEDIATE_SOURCES[ingredient] = position
                sources[ingredient] = position
                continue
            if item == "steel-plate" and ingredient == "iron-plate":
                # Steel is a persistent line: never point it at the nearest
                # starter/storage chest. Re-survey or repair the real iron
                # producer and use its recorded provider output instead.
                source = ensure_produced(
                    client, bridge, surface, force, ingredient, reference_point,
                    emit, upgrade_bootstrap=True,
                )
                if source is None:
                    return None
                sources[ingredient] = source
                continue
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


def _promotion_upstream_shortfall(
    client: RconClient, surface: str, force: str, item: str, machine_count: int,
) -> tuple[str, float, float] | None:
    """Return the raw stage that cannot feed a proposed promoted line.

    A backlog only says the existing mall cell is busy.  It does not prove the
    base has material for a larger line.  In particular, a six-machine pipe
    line consumes 9/s iron plate while six working electric furnaces produce
    far less; building it merely turns an iron shortage into six more waiting
    assemblers and a route from a sealed provider chest.
    """
    stock = live_base.available_items(client, surface, force)
    for ingredient, required_rate in zip(
        LINE_RECIPES[item]["ingredients"],
        machine_ingredient_rates(item, machine_count),
        strict=True,
    ):
        extraction = expansion_target(ingredient, stock)
        if extraction is None or not _mineable(extraction):
            continue
        source = live_base.find_line(
            client, surface, force, extraction,
            LINE_RECIPES[extraction]["machine"],
        )
        produced_per_machine = (
            LINE_RECIPES[extraction].get("product_amount", 1)
            * MACHINE_SPEEDS[LINE_RECIPES[extraction]["machine"]]
            / LINE_RECIPES[extraction]["craft_time"]
        )
        available_rate = (
            source.working_count * produced_per_machine if source is not None else 0.0
        )
        if available_rate + 1e-9 < required_rate:
            return extraction, available_rate, required_rate
    return None


def _build_assembled_stage(
    client: RconClient, bridge: GameBridge, surface: str, force: str, item: str,
    reference_point: Point, emit: Callable[[str], None], plan: _LinePlan,
    mall_provider: Point | None, *, upgrade_bootstrap: bool,
    stock_gate_target: int | None = None,
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
    mall_storage_limit = plan.mall_storage_limit
    if item == "steel-plate":
        iron_furnaces, iron_drills = _iron_capacity_for_fast_belts(
            client, surface, force,
        )
        if min(iron_furnaces, iron_drills) < STEEL_IRON_CAPACITY_FLOOR:
            emit(
                "  STEEL CAPACITY GATE: six steel furnaces need the shared "
                f"iron line at {STEEL_IRON_CAPACITY_FLOOR} furnaces and drills "
                f"(have {iron_furnaces}/{iron_drills}); expanding iron first"
            )
            build_mining_stage(
                client, bridge, surface, force, "iron-plate", reference_point,
                emit, expand=iron_furnaces > 0,
            )
            raise ProductionPrerequisiteDeferred(
                "steel-plate waits for the 12-furnace/12-drill iron checkpoint"
            )
        existing_count = existing.machine_count if existing is not None else 0
        missing = max(0, STEEL_BASELINE_FURNACES - existing_count)
        if missing == 0:
            return
        line_reference = sources["iron-plate"]
        output = build_conversion_stage(
            client, bridge, surface, force, item, sources, line_reference, emit,
            machine_count=missing, allow_logistic_inputs=False,
            side_tap_output=True,
        )
        MANAGED_INTERMEDIATE_SOURCES[item] = output
        emit(
            f"  PERSISTENT INTERMEDIATE: steel-plate now has its "
            f"{STEEL_BASELINE_FURNACES}-furnace baseline beside the iron source"
        )
        return
    if promote_to_line:
        upstream = _promotion_upstream_shortfall(
            client, surface, force, item, promoted_count,
        )
        if upstream is not None:
            extraction, available_rate, required_rate = upstream
            emit(
                f"  PROMOTION DEFERRED: {item} would draw {required_rate:.2f}/s "
                f"of {extraction}, but its live line supplies only "
                f"{available_rate:.2f}/s; expanding {extraction} before adding "
                "downstream machines"
            )
            try:
                build_mining_stage(
                    client, bridge, surface, force, extraction,
                    reference_point, emit, expand=True,
                )
            except stage_extraction.PendingSystemDeferred as error:
                raise ProductionPrerequisiteDeferred(str(error)) from error
            return
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
        output = build_compact_mall_stage(
            client, bridge, surface, force, item, sources, reference_point,
            bring_stage_up, emit, stock_target=mall_storage_limit,
            stock_gate_target=stock_gate_target,
            fill_chest=plan.fill_provider,
            request_multiplier_override=plan.mall_request_multiplier,
        )
        if item in PERSISTENT_INTERMEDIATES:
            MANAGED_INTERMEDIATE_SOURCES[item] = output
    else:
        build_conversion_stage(
            client, bridge, surface, force, item, sources, reference_point, emit,
            allow_logistic_inputs=not upgrade_bootstrap,
        )
    return None


def _iron_capacity_for_fast_belts(
    client: RconClient, surface: str, force: str,
) -> tuple[int, int]:
    """Return built iron furnaces and drills used by the fast-belt gate."""
    line = live_base.find_line(
        client, surface, force, "iron-plate", LINE_RECIPES["iron-plate"]["machine"],
    )
    furnaces = line.machine_count if line is not None else 0
    drills = extraction_state.resource_drill_count(
        client, surface, force, "iron-ore",
    )
    return furnaces, drills

def ensure_produced(
    client: RconClient, bridge: GameBridge, surface: str, force: str, item: str,
    reference_point: Point, emit: Callable[[str], None], *,
    upgrade_bootstrap: bool = True, stock_target: int = 1,
    minimum_machines: int = 1, allow_promotion: bool = True,
    stock_gate_target: int | None = None, storage_limit: int | None = None,
    fill_provider: bool = False,
) -> Point | None:
    """Returns the item's real output chest position if it's already producing;
    otherwise builds exactly ONE missing stage (the deepest unmet ingredient
    first) and returns None so the caller re-surveys and calls again."""
    if item in MANAGED_INTERMEDIATE_SOURCES:
        _complete_material_producer(item)
        return MANAGED_INTERMEDIATE_SOURCES[item]
    if item == "steel-plate":
        minimum_machines = max(minimum_machines, STEEL_BASELINE_FURNACES)
    if item == "fast-transport-belt":
        if not _electric_furnace_producer_started(client, surface, force):
            emit(
                "  FAST BELT GATE: postponing fast belts until the "
                "electric-furnace producer is working; use regular belts or "
                "remaining fast-belt stock for bootstrap routes"
            )
            raise ProductionPrerequisiteDeferred(
                "fast-transport-belt waits for electric-furnace production"
            )
        iron_furnaces, iron_drills = _iron_capacity_for_fast_belts(
            client, surface, force,
        )
        if min(iron_furnaces, iron_drills) < _FAST_BELT_IRON_CAPACITY:
            emit(
                f"  FAST BELT GATE: waiting for iron capacity {iron_furnaces}/"
                f"{_FAST_BELT_IRON_CAPACITY} furnaces and {iron_drills}/"
                f"{_FAST_BELT_IRON_CAPACITY} drills before producing fast belts"
            )
            build_mining_stage(
                client, bridge, surface, force, "iron-plate", reference_point,
                emit, expand=True,
            )
            raise ProductionPrerequisiteDeferred(
                "fast-transport-belt is gated until iron reaches 24 furnaces and drills"
            )
    if item == "coal":
        return ensure_coal_mine(
            client, bridge, surface, force, reference_point, bring_stage_up, emit,
        )
    if item in {"plastic-bar", "sulfur"}:
        outputs = ensure_oil_cell(
            client, bridge, surface, force, reference_point,
            bring_stage_up, emit,
        )
        return outputs[item] if outputs else None
    if item == "battery":
        return ensure_battery_cell(
            client, bridge, surface, force, reference_point,
            bring_stage_up, emit,
        )
    if item not in LINE_RECIPES:
        raise StuckError(f"No recipe knowledge for {item!r} -- add it to planners/recipe_data.py "
                          "before asking the builder to produce it")
    if not upgrade_bootstrap and _MATERIAL_RESERVATION_LEDGER is not None:
        loan_wait = _service_bootstrap_loan(
            client, bridge, surface, force, item, emit,
        )
        if loan_wait is not None:
            raise ProductionPrerequisiteDeferred(loan_wait)
    if (
        item == "automation-science-pack"
        and not _metal_starter_transition_complete(client, surface, force)
        and live_base.find_line(
            client, surface, force, item, LINE_RECIPES[item]["machine"],
        ) is None
    ):
        emit(
            "  AUTOMATION SCIENCE GATE: waiting for direct iron/copper "
            "starters to retire before placing the science assembler"
        )
        raise ProductionPrerequisiteDeferred(
            "automation-science-pack waits for direct metal starter retirement"
        )
    stock_gate_target = _effective_mall_stock_gate(
        client, surface, force, item,
        upgrade_bootstrap=upgrade_bootstrap,
        requested=stock_gate_target,
    )
    plan = _plan_line(
        client, surface, force, item, emit,
        upgrade_bootstrap=upgrade_bootstrap, stock_target=stock_target,
        minimum_machines=minimum_machines, allow_promotion=allow_promotion,
        storage_limit=storage_limit, fill_provider=fill_provider,
    )
    mall_provider = _refresh_mall_cell(
        client, bridge, surface, force, item, plan, emit,
        upgrade_bootstrap=upgrade_bootstrap,
        stock_gate_target=stock_gate_target,
        reference_point=reference_point,
    )
    existing = plan.existing
    if existing and (
        existing.working_count > 0 or getattr(existing, "produced_count", 0) > 0
    ):
        _complete_material_producer(item)
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
            mall_provider=mall_provider, upgrade_bootstrap=upgrade_bootstrap,
        )
    if not _mineable(item):
        if (
            not upgrade_bootstrap
            and not getattr(plan, "promote_to_line", False)
            and getattr(plan, "spec", LINE_RECIPES[item]).get("set_recipe", True)
        ):
            _reserve_compact_mall_project(
                client, surface, force, item, plan, stock_gate_target, emit,
                bridge=bridge, reference_point=reference_point,
            )
        _build_assembled_stage(
            client, bridge, surface, force, item, reference_point, emit, plan,
            mall_provider, upgrade_bootstrap=upgrade_bootstrap,
            stock_gate_target=stock_gate_target,
        )
        return None
    build_mining_stage(client, bridge, surface, force, item, reference_point, emit)
    return None


def _ensure_mall_item(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    item: str, target: int, mall_targets: dict[str, int],
    reference_point: Point, emit: Callable[[str], None], *, background: bool,
) -> tuple[bool, Point | None]:
    """Start or repair one mall producer, optionally without a stock wait."""
    if item not in LINE_RECIPES:
        raise StuckError(
            f"Parts mall needs {target} {item}, but no executable recipe "
            "knowledge exists for that construction item"
        )
    mode = "background reserve" if background else "stock target"
    emit(f"--- parts mall: ensuring {item} production for {mode} {target} ---")
    try:
        reserve = mall_reserve_for(client, surface, force, item, target)
        production_target = (
            target if reserve.fill_chest or reserve.storage_count >= target
            else reserve.storage_count
        )
        if not background and target > production_target:
            emit(
                f"  SCHEDULED BILL OVERRIDE: {item} idle cap "
                f"{production_target} -> blocking project demand {target}"
            )
            production_target = target
            reserve = MallReserve(
                max(reserve.gate_target or 0, target),
                max(reserve.storage_count, target),
                reserve.storage_stacks,
                reserve.fill_chest,
            )
        ledger_target = (
            _MATERIAL_RESERVATION_LEDGER.required_stock(item)
            if _MATERIAL_RESERVATION_LEDGER is not None else 0
        )
        if ledger_target > production_target:
            emit(
                f"  MATERIAL RESERVATION OVERRIDE: {item} idle cap "
                f"{production_target} -> scheduled bill {ledger_target}"
            )
            production_target = ledger_target
            reserve = MallReserve(
                max(reserve.gate_target or 0, ledger_target),
                max(reserve.storage_count, ledger_target),
                reserve.storage_stacks,
                reserve.fill_chest,
            )
        if reserve.fill_chest:
            emit(
                f"  MALL RESERVE: {item} is self-sufficient; removing its "
                "provider bar and stock gate so it can fill the chest"
            )
        else:
            emit(
                f"  MALL RESERVE: {item} will maintain {reserve.storage_count} "
                f"({reserve.storage_stacks} stack(s)); this job needs {target}"
            )
            if production_target < target:
                emit(
                    f"  MALL STARTER CAP: limiting {item} to "
                    f"{production_target} until direct iron/copper starters retire"
                )
        output = ensure_produced(
            client, bridge, surface, force, item, reference_point, emit,
            upgrade_bootstrap=False, stock_target=production_target,
            stock_gate_target=reserve.gate_target,
            storage_limit=reserve.storage_count,
            fill_provider=reserve.fill_chest,
        )
    except ProductionPrerequisiteDeferred as deferred:
        emit(f"  MALL DEFERRED: {deferred}")
        return False, None
    except MaterialShortage as shortage:
        add_demands(mall_targets, shortage)
        emit(
            f"  MALL DEMAND: {shortage.stage} needs "
            + ", ".join(
                f"{name}={count}" for name, count in sorted(shortage.required.items())
            )
            + " -- queued; this stage resumes once the mall has them"
        )
        return False, None
    return True, output


def _serve_background_mall_task(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    background_targets: dict[str, int], mall_targets: dict[str, int],
    reference_point: Point, emit: Callable[[str], None],
) -> bool:
    """Bring up one reserve producer without waiting for its chest to fill."""
    if not background_targets:
        return False
    item = next(iter(background_targets))
    target = background_targets[item]
    ready, _ = _ensure_mall_item(
        client, bridge, surface, force, item, target, mall_targets,
        reference_point, emit, background=True,
    )
    if ready:
        background_targets.pop(item)
        emit(
            f"  MALL BACKGROUND: {item} producer is available; reserve continues "
            "filling without blocking the mission"
        )
    return True

# Cells whose recipes CONSUME belts wait behind the blueprint reserve once the
# belt line is producing. A stalled starter is different: holding its only
# belts hostage prevents the splitter/other first consumer that unlocks the
# iron blueprint from ever being made. In that case one craft may draw from
# stock; the normal reserve resumes with live belt output.
_BELT_RESERVE_FLOORS = {
    "transport-belt": 50,
    "fast-transport-belt": 50,
}


def _belt_starved_consumer(
    client: RconClient, surface: str, force: str, item: str,
) -> str | None:
    """Why this mall item must wait for the blueprint belt reserve, or None."""
    spec = LINE_RECIPES.get(item)
    if spec is None:
        return None
    stock = live_base.available_items(client, surface, force)
    for ingredient, amount in zip(
        spec["ingredients"], spec["amounts"], strict=True,
    ):
        floor = _BELT_RESERVE_FLOORS.get(ingredient)
        if floor is None:
            continue
        held = stock.get(ingredient, 0)
        ingredient_recipe = LINE_RECIPES.get(ingredient)
        belt_line = None
        if ingredient_recipe is not None:
            try:
                belt_line = live_base.find_line(
                    client, surface, force, ingredient,
                    str(ingredient_recipe["machine"]),
                )
            except Exception:  # dry harnesses may not provide line telemetry
                belt_line = None
        if (
            ingredient == "transport-belt"
            and belt_line is not None
            and belt_line.working_count <= 0
        ):
            continue
        if held < floor + amount:
            return (
                f"its recipe consumes {ingredient} and only {held} remain -- "
                f"active blueprints hold the reserve until stock recovers "
                f"past {floor}"
            )
    return None


def _deliver_cell_ingredients(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    item: str, near: Point, emit: Callable[[str], None],
) -> bool:
    """Ship a stalled mall cell's ingredients from base stock to its network.

    A cell whose requester holds gears but no plates crafts nothing while
    plates sit in a distant provider across a logistic-network gap (live run
    18: inserters frozen 9/12 with 16 idle gears). For each ingredient the
    cell requests, place a small local provider and relocate stock into it --
    conservation-honest movement of OUR inventory, no new items."""
    spec = LINE_RECIPES.get(item)
    if spec is None:
        return False
    moved_total = 0
    try:
        for ingredient, amount in zip(
            spec["ingredients"], spec["amounts"], strict=True,
        ):
            chest = live_base.requester_requesting(
                client, surface, ingredient, near,
            )
            if chest is None:
                continue
            local = live_base.network_item_count(
                client, surface, force, chest, ingredient,
            )
            if local is not None and local >= amount * 2:
                continue
            provider = live_base.nearest_container(
                client, surface, force, chest,
                names=("passive-provider-chest",),
            )
            if provider is not None and math.dist(provider, chest) <= 10.0:
                destination = provider
            else:
                spots = live_base.chained_clear_spots(
                    client, surface,
                    [("passive-provider-chest", 1), ("medium-electric-pole", 1)],
                    chest,
                )
                spot = next(
                    (s for s in spots if s[0] == "passive-provider-chest"), None,
                )
                if spot is None:
                    continue
                destination = (spot[1], spot[2])
                plan = {"phases": [{
                    "name": f"deliver_{ingredient}",
                    "actions": [
                        {"action_type": "place_entity",
                         "entity": "passive-provider-chest",
                         "position": {"x": destination[0], "y": destination[1]}},
                        {"action_type": "place_entity",
                         "entity": "medium-electric-pole",
                         "position": {"x": destination[0] + 2.0, "y": destination[1]}},
                    ],
                }]}
                plan["surface"], plan["force"] = surface, force
                _submit(client, bridge, surface, plan,
                        f"deliver_{ingredient}", emit)
            moved = live_base.transfer_stock(
                client, surface, ingredient,
                max(amount * 4, 20), destination,
            )
            moved_total += moved
            emit(
                f"  CELL DELIVERY: moved {moved} {ingredient} beside the "
                f"{item} cell's requester at {chest}"
            )
    except Exception as error:  # delivery is opportunistic
        emit(f"  CELL DELIVERY skipped: {error}")
    return moved_total > 0


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
    starved_on = _belt_starved_consumer(client, surface, force, item)
    if starved_on is not None:
        tick_now = live_base.game_tick(client)
        priorities.defer(item, tick_now, starved_on)
        emit(f"  PRIORITY DEFERRED: {item}; {starved_on}")
        return
    emit(priorities.describe(task, tick))
    ready, output = _ensure_mall_item(
        client, bridge, surface, force, item, target, mall_targets,
        reference_point, emit, background=False,
    )
    if not ready:
        stock = live_base.available_items(client, surface, force)
        other_pending = {
            other for other, target in mall_targets.items()
            if other != item and stock.get(other, 0) < target
        }
        if other_pending:
            # This item depends on prerequisites still queued beside it.
            # Serving it again first would re-queue the same numbers every
            # pass while they starve behind it in the ranking (live runs
            # 11/16: landfill outranked the belts and chests it demanded).
            # Yield the rank until they are satisfied.
            tick_now = live_base.game_tick(client)
            for prerequisite in sorted(other_pending):
                priorities.promote(
                    prerequisite, mall_targets[prerequisite], tick_now,
                )
            priorities.defer(
                item, tick_now,
                "waiting on its own queued prerequisite",
            )
            emit(
                f"  PRIORITY DEFERRED: {item} behind reserved prerequisite(s) "
                + ", ".join(sorted(other_pending))
            )
            return
        if _deliver_cell_ingredients(
            client, bridge, surface, force, item, reference_point, emit,
        ):
            return
        return
    if output is not None:
        if construction_supply_chain_is_scheduled(
            client, surface, force, item,
        ):
            line = live_base.find_line(
                client, surface, force, item,
                str(LINE_RECIPES[item]["machine"]),
            )
            if line is not None and (
                line.working_count > 0 or line.produced_count > 0
            ):
                emit(
                    f"  MALL PRODUCING: {item} and every prerequisite have "
                    "live production; construction can draw while stock builds"
                )
                priorities.complete(item, live_base.game_tick(client))
                mall_targets.pop(item, None)
                return
            if _deliver_cell_ingredients(
                client, bridge, surface, force, item, reference_point, emit,
            ):
                return
            reason = "producer exists but has not produced yet"
            priorities.defer(item, live_base.game_tick(client), reason)
            emit(f"  MALL WAIT: {item} {reason}; retaining its demand")
            return

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
            except ProductionPrerequisiteDeferred as deferred:
                current_tick = live_base.game_tick(client)
                priorities.defer(item, current_tick, str(deferred))
                emit(f"  PRIORITY DEFERRED: {item}; {deferred}")
                # The bootstrap caps name their unlock condition; queue it as
                # concrete mall work instead of letting the gate idle the
                # whole expansion ladder (live run of 2026-08-22 03:01 crashed
                # here because this deferral escaped the StuckError handler).
                if "electric-furnace" in str(deferred):
                    _queue_electric_furnace_unlock(
                        client, surface, force,
                        mall_targets, str(deferred), emit,
                    )
                    priorities.promote(
                        "electric-furnace",
                        mall_targets["electric-furnace"],
                        current_tick,
                    )
                else:
                    _queue_electric_furnace_unlock(
                        client, surface, force,
                        mall_targets, str(deferred), emit,
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
            emit(
                f"  MALL EXPANSION DEMAND: {shortage.stage} needs "
                + ", ".join(
                    f"{name}={count}"
                    for name, count in sorted(shortage.required.items())
                )
                + " -- queued; expansion resumes once the mall has them"
            )
            return
        if not ready:
            return
        priorities.complete(item, live_base.game_tick(client))
        mall_targets.pop(item, None)
    return


def _electric_furnace_producer_started(
    client: RconClient, surface: str, force: str,
) -> bool:
    """Whether the mall producer for electric furnaces has demonstrably run.

    Smelting furnaces are not this producer: the item itself must be made by
    an assembling-machine line. Demand is intermittent, so requiring the
    machine to be working at the exact survey tick made a proven producer look
    unstarted; monotonic ``products_finished`` is the durable evidence.
    """
    line = live_base.find_line(
        client, surface, force, "electric-furnace", "assembling-machine-2",
    )
    return bool(
        line is not None
        and (line.working_count > 0 or line.produced_count > 0)
    )


def _prep_plate_extraction(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    short_plate: str, prepped: set[str], deferred_targets: dict[str, int],
    mall_targets: dict[str, int],
    reference_point: Point, emit: Callable[[str], None],
    demand_targets: Mapping[str, int] | None = None,
    pending_materials: dict[str, dict[str, int]] | None = None,
    furnace_target: int | None = None,
    excluded_drill_positions: tuple[Point, ...] = (),
) -> bool:
    """Grow one plate line to the furnace count its own prep draw implies.

    Returns whether it spent the pass. A material shortage hands the pass back
    so the mall can build the drills it is short of -- prep runs before the mall
    now, so holding on would re-hit the identical shortage forever.
    """
    available = live_base.available_items(client, surface, force)
    pending = (pending_materials or {}).get(short_plate)
    missing_pending = {
        item: count for item, count in (pending or {}).items()
        if available.get(item, 0) < count
    }
    pipeline_ready = False
    if missing_pending:
        if not all(
            construction_supply_chain_is_scheduled(
                client, surface, force, item,
            )
            for item in missing_pending
        ):
            return False
        pipeline_ready = True
        emit(
            f"  PLATE FOUNDATION PIPELINE READY: {short_plate} construction "
            "items are being produced; releasing its coherent blueprint"
        )
    if pending_materials is not None:
        pending_materials.pop(short_plate, None)
    plate_line = live_base.find_line(
        client, surface, force, short_plate,
        LINE_RECIPES[short_plate]["machine"],
    )
    bootstrap_line = bool(
        plate_line is not None
        and logistic_smelter_origin(tuple(
            getattr(plate_line, "machine_positions", ()) or (),
        )) is not None
    )
    targets = dict(demand_targets or {})
    for item, target in mall_targets.items():
        targets[item] = max(targets.get(item, 0), target)
    adjusted_draw = demand_adjusted_plate_draw(targets, available)
    declared_draw = adjusted_draw.get(short_plate)
    if declared_draw is None:
        if furnace_target is None:
            raise ValueError(
                f"{short_plate} has no demand-adjusted extraction rate"
            )
        spec = LINE_RECIPES[short_plate]
        declared_draw = (
            furnace_target
            * MACHINE_SPEEDS[spec["machine"]]
            * spec.get("product_amount", 1)
            / spec["craft_time"]
        )
    wanted_furnaces = (
        furnace_target if furnace_target is not None
        else smelter_count_for_draw(short_plate, declared_draw)
    )
    if (
        short_plate in BOOTSTRAP_FURNACE_CAPS
        and not _electric_furnace_producer_started(client, surface, force)
    ):
        cap = BOOTSTRAP_FURNACE_CAPS[short_plate]
        if wanted_furnaces > cap:
            emit(
                f"  BOOTSTRAP FURNACE CAP: holding {short_plate} at {cap} "
                f"furnaces until electric-furnace production is working "
                f"(demand calculated {wanted_furnaces})"
            )
            wanted_furnaces = cap
    deferred_target = deferred_targets.get(short_plate)
    if deferred_target is not None and wanted_furnaces <= deferred_target:
        return False
    deferred_targets.pop(short_plate, None)
    have = plate_line.machine_count if plate_line else 0
    if have >= wanted_furnaces:
        newly_prepped = short_plate not in prepped
        prepped.add(short_plate)
        if newly_prepped:
            emit(
                f"  PREP READY: {short_plate} has {have}/{wanted_furnaces} furnace(s)"
            )
        return newly_prepped
    emit(
        f"--- production prep: {short_plate} extraction to "
        f"{wanted_furnaces} furnace(s) for "
        f"{declared_draw:.2f}/s "
        f"(have {have}, drill phase {drill_phase_for_draw(declared_draw)}) ---"
    )
    try:
        output_source = build_mining_stage(
            client, bridge, surface, force, short_plate,
            reference_point, emit,
            # Temporary starter production is not the first managed refinery.
            # A fixed foundation request establishes or repairs its opening
            # six-furnace district; recipe-visible startup furnaces must never
            # turn foundation work into the 6 -> 12 growth policy.
            expand=(
                furnace_target is None
                and plate_line is not None
                and not bootstrap_line
            ),
            earmark_unfunded=pipeline_ready,
            excluded_drill_positions=excluded_drill_positions,
        )
        if output_source is not None:
            MANAGED_INTERMEDIATE_SOURCES[short_plate] = output_source
            emit(
                f"  PLATE SOURCE: recorded {short_plate} provider at "
                f"{output_source} for downstream logistic consumers"
            )
    except ProductionPrerequisiteDeferred as deferred:
        if isinstance(deferred.__cause__, stage_extraction.PendingSystemDeferred):
            emit(
                f"  PREP CONSTRUCTING: {short_plate} foundation ghosts are "
                "still being built; holding startup instead of advancing the goal"
            )
            consume_wait(f"pending_{short_plate}_foundation")
            time.sleep(_PENDING_FOUNDATION_POLL_SECONDS)
            return True
        if (
            "mine power was repaired" in str(deferred)
            or "direct refinery has not produced yet" in str(deferred)
        ):
            emit(
                f"  PREP CONSTRUCTING: {short_plate} foundation is recovering "
                "power or first output; holding startup instead of advancing the goal"
            )
            consume_wait(f"recovering_{short_plate}_foundation")
            time.sleep(_PENDING_FOUNDATION_POLL_SECONDS)
            return True
        deferred_targets[short_plate] = wanted_furnaces
        emit(f"  PREP DEFERRED: {short_plate} extraction -- {deferred}")
        _queue_electric_furnace_unlock(
            client, surface, force, mall_targets, str(deferred), emit,
        )
        return False
    except MaterialShortage as shortage:
        # Raising a drill phase needs drills, and drills come from
        # the mall. Push the shortfall back as a mall target instead
        # of dying on it: prep is the first thing that ever asks for
        # 14 drills at once, so it is also the first thing to find
        # the base holding 8. Every other path in this loop already
        # does this -- omitting it here ended a run outright.
        add_demands(mall_targets, shortage)
        if pending_materials is not None:
            pending_materials[short_plate] = dict(shortage.required)
        emit(
            f"  PREP DEMAND: {short_plate} extraction needs "
            + ", ".join(
                f"{item}={target}"
                for item, target in sorted(shortage.required.items())
            )
            + " -- queued for the mall"
        )
        if furnace_target is None and plate_line is not None and not bootstrap_line:
            try:
                output_source = build_mining_stage(
                    client, bridge, surface, force, short_plate,
                    reference_point, emit, expand=True, earmark_unfunded=True,
                    excluded_drill_positions=excluded_drill_positions,
                )
                if output_source is not None:
                    MANAGED_INTERMEDIATE_SOURCES[short_plate] = output_source
                emit(
                    f"  BLUEPRINT EARMARK: {short_plate} mine/refinery expansion "
                    "is staged; "
                    "construction may finish asynchronously"
                )
                return True
            except (ProductionPrerequisiteDeferred, MaterialShortage, StuckError) as error:
                # A coherent ghost plan may now exist even though its machines
                # are not healthy yet. The next prep pass re-surveys pending
                # ghosts and will not submit a duplicate.
                emit(f"  BLUEPRINT EARMARK pending: {error}")
        return False
    except (StuckError, ValueError) as error:
        deferred_targets[short_plate] = wanted_furnaces
        prepped.add(short_plate)
        emit(
            f"  PREP DEFERRED: {short_plate} extraction stays at {have} "
            f"furnace(s) -- {error}"
        )
    return True


def _direct_plate_foundation_ready(
    client: RconClient, surface: str, force: str, recipe: str,
) -> bool:
    """Whether one real, belt-fed opening module exists for `recipe`.

    Furnaces infer their recipe from inserted material. During startup a real
    six-furnace refinery can therefore appear as four recipe-visible machines
    plus two unset machines. Count an exact planner-shaped module after
    excluding requester bootstrap geometry; a recipe-only count retriggered
    iron growth before copper existed in the 2026-08-25 19:12 run.
    """
    line = live_base.find_line(
        client, surface, force, recipe, LINE_RECIPES[recipe]["machine"],
    )
    if line is None:
        return False
    visible = tuple(getattr(line, "machine_positions", ()) or ())
    if logistic_smelter_origin(visible) is not None:
        return False
    positions = set(visible)
    anchor = getattr(line, "output_position", None) or (
        visible[0] if visible else None
    )
    if anchor is not None:
        idle = live_base.find_idle_machine_row(
            client, surface, force, recipe, LINE_RECIPES[recipe]["machine"],
            anchor, radius=40.0,
        )
        if idle is not None:
            positions.update(idle.machine_positions)
    required = PLATE_FOUNDATION_FURNACES[recipe]
    if len(positions) < required:
        return False
    return bool(_complete_six_furnace_candidates(tuple(sorted(positions))))


def _prep_plate_foundation(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    prepped: set[str], deferred_targets: dict[str, int],
    mall_targets: dict[str, int], reference_point: Point,
    emit: Callable[[str], None],
    background_targets: Mapping[str, int],
    pending_materials: dict[str, dict[str, int]],
) -> bool:
    """Start both metals and brick cheaply, then build opening direct modules.

    The direct starter breaks the belt construction circle without a
    requester network. Once iron, copper, and stone-brick flow, the ordinary
    six-furnace systems are attempted in the same order and their complete
    material bills remain visible to the mall.
    """
    standing_starters: dict[str, live_base.DirectPlateStarter] = {}
    for plate in PLATE_FOUNDATION_BUILD_ORDER:
        ore = LINE_RECIPES[plate]["ingredients"][0]
        starter = live_base.direct_plate_starter(
            client, surface, force, plate, ore, reference_point,
        )
        if _direct_plate_foundation_ready(client, surface, force, plate):
            if starter is None:
                continue
            line = live_base.find_line(
                client, surface, force, plate, LINE_RECIPES[plate]["machine"],
            )
            healthy = bool(
                line is not None
                and line.produced_count > 0
            )
            if not healthy:
                emit(
                    f"  PLATE FOUNDATION CONSTRUCTING: {plate} is structurally "
                    "complete but has not produced yet; keeping its starter"
                )
                consume_wait(f"healthy_{plate}_foundation")
                time.sleep(_PENDING_FOUNDATION_POLL_SECONDS)
                return True
            _retire_standing_bootstrap_cells(
                client, bridge, surface, force, plate, ore,
                reference_point, emit,
            )
            return True
        if starter is not None:
            standing_starters[plate] = starter
            continue
        try:
            _bootstrap_direct_plate_line(
                client, bridge, surface, force, plate, reference_point, emit,
            )
        except MaterialShortage as error:
            add_demands(mall_targets, error)
            emit(
                f"  PLATE STARTER PENDING: {plate} needs {error.required}; "
                "queued its construction items"
            )
        return True
    for plate in PLATE_FOUNDATION_BUILD_ORDER:
        if _direct_plate_foundation_ready(client, surface, force, plate):
            continue
        emit(
            f"PLATE FOUNDATION: establishing {plate} "
            f"({PLATE_FOUNDATION_FURNACES[plate]} furnaces) before expansion"
        )
        starter = standing_starters.get(plate)
        excluded_positions = (
            (
                starter.drill_position,
                *starter.additional_drill_positions,
            )
            if starter is not None else ()
        )
        return _prep_plate_extraction(
            client, bridge, surface, force, plate, prepped,
            deferred_targets, mall_targets, reference_point, emit,
            background_targets, pending_materials,
            furnace_target=PLATE_FOUNDATION_FURNACES[plate],
            excluded_drill_positions=excluded_positions,
        )
    return False


def _queue_electric_furnace_unlock(
    client: RconClient, surface: str, force: str,
    mall_targets: dict[str, int], deferred_text: str,
    emit: Callable[[str], None],
) -> None:
    """Turn the bootstrap cap's named unlock into concrete mall work.

    The cap holds plate growth until an electric-furnace PRODUCER works; a
    deferral that only waits never builds one, so the whole expansion ladder
    idled at 12 furnaces while every downstream cell starved on plates (live
    run of 2026-08-22 03:54). Queueing the producer lets the normal mall
    recursion chain its prerequisites (steel, stone-brick, circuits) into real
    passes.

    The demand is one MORE furnace than the force already owns: a flat target
    of 1 was retired by the survey the moment it saw idle starter stock, so
    the gate requeued and was re-retired every pass while the producer line
    never ran (live run of 2026-08-24 02:38 livelocked to STUCK this way).
    Requiring new production is what actually lifts the cap.
    """
    if "electric-furnace" not in deferred_text:
        return
    owned = int(
        live_base.available_items(client, surface, force)
        .get("electric-furnace", 0)
    )
    add_demands(mall_targets, MaterialShortage(
        "unlock_plate_expansion", {"electric-furnace": owned + 1}, {},
    ))
    emit(
        "  GATE WORK: queued an electric-furnace producer -- "
        "the cap lifts once it is working"
    )


def _prep_the_belt_cell(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    prepped: set[str], mall_targets: dict[str, int],
    reference_point: Point, emit: Callable[[str], None],
) -> bool:
    """Stand up the transport-belt producer BEFORE any mine spends stock.

    The user standard, 2026-08-22: the mall's belt assembler belongs in place
    first -- mines then draw from a replenishing producer instead of draining
    a finite starter reserve they can never refill while plates are still
    bootstrapping. The compact cell itself costs no belts (requester-fed), so
    this cannot recurse into the shortage it prevents.

    Returns whether the pass was spent."""
    emit(
        "--- production prep: transport-belt cell before any mine spends "
        "the belt reserve ---"
    )
    try:
        ensure_produced(
            client, bridge, surface, force, "transport-belt", reference_point,
            emit, upgrade_bootstrap=False, stock_target=1,
            minimum_machines=1, allow_promotion=False,
        )
    except MaterialShortage as shortage:
        add_demands(mall_targets, shortage)
        emit(
            "  PREP BLOCKED: transport-belt cell needs "
            + ", ".join(
                f"{item}={count}"
                for item, count in sorted(shortage.required.items())
            )
            + " -- handing the pass to the mall"
        )
        return False
    except ProductionPrerequisiteDeferred as deferred:
        emit(f"  BELT CELL DEFERRED: {deferred}")
        return False
    prepped.add(_BELT_CELL_PREP_KEY)
    emit("  PREP READY: transport-belt cell is producing")
    return True


_BELT_CELL_PREP_KEY = "_belt_cell"


def _production_started(
    client: RconClient, surface: str, force: str, item: str,
) -> bool:
    """Whether an upstream line is working now or has produced before."""
    spec = LINE_RECIPES.get(item)
    if spec is None:
        return True
    line = live_base.find_line(
        client, surface, force, item, spec["machine"],
    )
    return line is not None and (
        line.working_count > 0 or getattr(line, "produced_count", 0) > 0
    )


def _baseline_recipe_ready(
    client: RconClient, surface: str, force: str, recipe: str,
) -> bool:
    """Whether baseline prep can build `recipe` without recursive bootstrap."""
    return all(
        _production_started(client, surface, force, ingredient)
        for ingredient in LINE_RECIPES[recipe]["ingredients"]
    )


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
    ready = [
        recipe for recipe in pending
        if _baseline_recipe_ready(client, surface, force, recipe)
    ]
    if ready:
        recipe = ready[0]
        wanted = BASELINE_MACHINES[recipe]
        line = live_base.find_line(
            client, surface, force, recipe, LINE_RECIPES[recipe]["machine"],
        )
        if line is not None and line.machine_count >= wanted:
            ensure_produced(
                client, bridge, surface, force, recipe, reference_point, emit,
                upgrade_bootstrap=False, stock_target=wanted,
                minimum_machines=wanted, allow_promotion=False,
            )
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
        except ProductionPrerequisiteDeferred as deferred:
            # An ingredient recursion can hit a bootstrap gate (e.g. fast-belt
            # or furnace caps); like a shortage it is the mall's turn until
            # the named prerequisite exists -- not a reason to crash the run.
            emit(f"  PREP BLOCKED: {recipe} -- {deferred}")
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
    except ProductionPrerequisiteDeferred as deferred:
        emit(f"  MALL DEFERRED: {deferred}")
        return _SHORTAGE
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


def _metal_starter_transition_complete(
    client: RconClient, surface: str, force: str,
) -> bool:
    """Whether direct iron/copper stacks have yielded to full refineries."""
    global _STARTUP_MALL_LIMITS_RELEASED, _STARTUP_MALL_LIMITS_FALLBACK_PROBED
    if _STARTUP_MALL_LIMITS_RELEASED:
        return True
    if _STARTUP_METAL_STARTERS_OBSERVED:
        return False
    if _STARTUP_MALL_LIMITS_FALLBACK_PROBED:
        return False
    _STARTUP_MALL_LIMITS_FALLBACK_PROBED = True
    try:
        completed = all(
            _direct_plate_foundation_ready(client, surface, force, recipe)
            and live_base.direct_plate_starter(
                client, surface, force, recipe,
                LINE_RECIPES[recipe]["ingredients"][0], (3.0, -1.0),
            ) is None
            for recipe in ("iron-plate", "copper-plate")
        )
    except Exception:
        return False
    _STARTUP_MALL_LIMITS_RELEASED = completed
    return completed


def _release_metal_starter_limits_if_complete(
    client: RconClient, surface: str, force: str,
) -> None:
    """Lift startup caps immediately after the second direct stack retires."""
    global _STARTUP_MALL_LIMITS_RELEASED
    if not _STARTUP_METAL_STARTERS_OBSERVED or _STARTUP_MALL_LIMITS_RELEASED:
        return
    try:
        if all(
            live_base.direct_plate_starter(
                client, surface, force, recipe,
                LINE_RECIPES[recipe]["ingredients"][0], (3.0, -1.0),
            ) is None
            for recipe in ("iron-plate", "copper-plate")
        ):
            _STARTUP_MALL_LIMITS_RELEASED = True
    except Exception:
        return


def mall_reserve_for(
    client: RconClient, surface: str, force: str, item: str, target: int,
) -> MallReserve:
    """Reserve ahead while scarce; fill the chest once AM3 is self-produced."""
    transitioned = _metal_starter_transition_complete(client, surface, force)
    startup_cap = _startup_mall_item_cap(client, surface, force, item)
    if startup_cap is not None:
        return MallReserve(startup_cap, startup_cap, None)
    if transitioned and item in _POST_STARTER_ONE_STACK_ITEMS:
        stack_size = ITEM_STACK_SIZES.get(item, FALLBACK_STACK_SIZE)
        return MallReserve(stack_size, stack_size, 1)
    return mall_reserve(
        item,
        target,
        stack_sizes=ITEM_STACK_SIZES,
        mature=_has_producer(
            client, surface, force, "assembling-machine-3",
        ),
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
    background_targets: dict[str, int] | None = None,
    deferred_plate_targets: Mapping[str, int] | None = None,
    pending_plate_materials: Mapping[str, Mapping[str, int]] | None = None,
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
        tuple(sorted(background_targets or {})),
        tuple(sorted((deferred_plate_targets or {}).items())),
        tuple(sorted(
            (plate, tuple(sorted(required.items())))
            for plate, required in (pending_plate_materials or {}).items()
        )),
        tuple(sorted(prepped)),
    )


def _livelock_step(
    signature_changed: bool, construction_progressed: bool,
    unchanged_passes: int,
) -> int:
    """Next no-progress pass count for the livelock guard.

    A falling pending-ghost count means bots built something since the last
    pass -- that is patience, not a livelock. Only a repeated decision
    signature with NO ground progress may accumulate toward the bound; the
    research-queue run of 2026-08-21 died while copper was mid-construction
    because only the decision layer was ever consulted."""
    if construction_progressed:
        return 0
    return 0 if signature_changed else unchanged_passes + 1


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
        f"finding it -- see the repeated reason above.{unbacked}",
        code="no_progress",
        classification="bug",
        state="failed",
        details={
            "unchanged_passes": unchanged_passes,
            "goal_item": goal_item,
            "selected_task": signature[0],
            "progress_percent": signature[1],
        },
    )


def _open_the_run(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    goal_item: str, mission_items: tuple[str, ...], script_output: Path | str,
    emit: Callable[[str], None],
) -> tuple[dict[str, int], dict[str, int], PriorityList]:
    """Learn the force's real recipes, then announce what this run is aiming at.

    The catalog load has to happen before the target is validated a second
    time: the first check only knows the hardcoded recipes, and the point of
    loading is that the live force may know more.
    """
    global _STARTUP_METAL_STARTERS_OBSERVED, _STARTUP_MALL_LIMITS_RELEASED
    global _STARTUP_MALL_LIMITS_FALLBACK_PROBED
    UNBACKED_DRAWS.clear()   # module state must not leak between runs
    MANAGED_INTERMEDIATE_SOURCES.clear()
    _REFINERY_SITE_RESERVATIONS.clear()
    _STARTUP_METAL_STARTERS_OBSERVED = False
    _STARTUP_MALL_LIMITS_RELEASED = False
    _STARTUP_MALL_LIMITS_FALLBACK_PROBED = False
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
    background_targets = mission_mall_targets(
        mission_items or (goal_item,), LINE_RECIPES,
    )
    mall_targets: dict[str, int] = {}
    emit(
        "MALL BACKGROUND RESERVES: producers start without blocking the mission -- "
        + ", ".join(
            f"{item}={target}" for item, target in background_targets.items()
        )
    )
    emit(
        "PRODUCTION PREP: standing lines -- "
        + ", ".join(
            f"{recipe}x{BASELINE_MACHINES[recipe]}"
            for recipe in baseline_build_order()
        )
        + "; bootstrap furnace caps "
        + ", ".join(
            f"{recipe}={cap}"
            for recipe, cap in sorted(BOOTSTRAP_FURNACE_CAPS.items())
        )
        + "; plate draw "
        + ", ".join(
            f"{plate} {rate:.2f}/s (drill phase {baseline_drill_phase(plate)})"
            for plate, rate in sorted(baseline_plate_draw().items())
        )
    )
    priority_path = Path(script_output).parent / "logs" / "autonomous-priorities.json"
    priorities = PriorityList(priority_path, live_base.game_tick(client))
    return mall_targets, background_targets, priorities




def _serve_ready_pass(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    task, tick: int, mall_targets: dict[str, int],
    background_targets: dict[str, int], priorities: PriorityList,
    reference_point: Point, goal_item: str, emit: Callable[[str], None],
) -> Point | object | None:
    """Serve blocking work, advance the goal, then start one reserve producer.

    Background mall requesters are deliberately not allowed to get first claim
    on scarce intermediate stock. A bootstrap run can have a provider chest
    with a few starter gears while several reserve cells are simultaneously
    requesting gears; starting all those cells before the mission makes a
    science requester wait forever even though the source chest is non-empty.
    Trying the goal first preserves the mission's critical path. If it needs a
    construction item, ``_advance_the_goal`` queues the normal mall shortage and
    the blocking task is served on the next pass.
    """
    if task is not None:
        _serve_mall_task(
            client, bridge, surface, force, task, tick, mall_targets,
            priorities, reference_point, emit,
        )
        return _SHORTAGE
    if mall_targets:
        wait_ticks = priorities.wait_ticks(mall_targets, tick)
        emit(
            "PRIORITY WAIT: all unfinished construction tasks are deferred; "
            f"next review in {wait_ticks or 60} ticks"
        )
        consume_wait("priority_defer")
        time.sleep(5)
        return _SHORTAGE
    emit(f"--- checking {goal_item} before background reserves ---")
    goal_result = _advance_the_goal(
        client, bridge, surface, force, goal_item, mall_targets,
        reference_point, emit,
    )
    if goal_result is not None:
        return goal_result
    if _serve_background_mall_task(
        client, bridge, surface, force, background_targets, mall_targets,
        reference_point, emit,
    ):
        return _SHORTAGE
    return None


def run(
    goal_item: str, *, surface: str = "nauvis", force: str = "player",
    rcon_host: str = "127.0.0.1", rcon_port: int = 27017, rcon_password: str = "",
    script_output: Path | str = "", reference_point: Point = (0.0, 0.0),
    max_iterations: int = 20, emit: Callable[[str], None] = print,
    mission_items: tuple[str, ...] = (),
    episode_id: str | None = None,
    bootstrap_profile: str = "reduced-v1",
) -> dict:
    """Loop: survey -> decide the single deepest missing stage -> build it ->
    repeat, until `goal_item` has a real, working line or the builder is
    genuinely stuck (raises StuckError rather than guessing)."""
    global _BOOTSTRAP_DISTRICT_LEDGER, _MATERIAL_RESERVATION_LEDGER
    validate_builder_target(goal_item, surface, LINE_RECIPES)
    _BOOTSTRAP_DISTRICT_LEDGER = (
        BootstrapDistrictLedger(
            script_output, episode_id=episode_id, surface=surface, force=force,
            bootstrap_profile=bootstrap_profile,
        )
        if episode_id else None
    )
    _MATERIAL_RESERVATION_LEDGER = (
        MaterialReservationLedger(
            script_output, episode_id=episode_id, surface=surface, force=force,
        )
        if episode_id else None
    )
    set_active_material_ledger(_MATERIAL_RESERVATION_LEDGER)
    client = RconClient(rcon_host, rcon_port, rcon_password)
    bridge = GameBridge(
        script_output=Path(script_output), host=rcon_host, port=rcon_port,
        password=rcon_password, command_timeout=30.0, episode_id=episode_id,
    )
    budget = begin_run_budget(max_iterations)
    try:
        mall_targets, background_targets, priorities = _open_the_run(
            client, bridge, surface, force, goal_item, mission_items,
            script_output, emit,
        )
        _restore_bootstrap_reservations()
        prepped: set[str] = set()
        deferred_plate_targets: dict[str, int] = {}
        pending_plate_materials: dict[str, dict[str, int]] = {}
        last_signature: tuple | None = None
        last_ghost_count: int | None = None
        last_items_total: int | None = None
        last_generation_check_tick = -_GENERATION_CHECK_INTERVAL_TICKS
        unchanged_passes = 0
        iteration = 0
        while budget.passes < max_iterations:
            budget.begin_pass()
            tick, task = _survey_pass(
                client, surface, force, mall_targets, priorities,
            )
            signature = _pass_signature(
                task, mall_targets, prepped, background_targets,
                deferred_plate_targets, pending_plate_materials,
            )
            ghosts_now = live_base.pending_ghost_count(client, surface, force)
            target_stock_now = live_base.available_items(client, surface, force)
            relevant_mission_items = mission_items or (goal_item,)
            items_now = sum(
                int(target_stock_now.get(item, 0))
                for item in relevant_mission_items
            )
            # Only growth in the target's own stock counts as progress. A
            # growing total inventory can belong to unrelated mall work and
            # must not mask a blocked research target.
            stock_grew = (
                last_items_total is not None and items_now > last_items_total
            )
            unchanged_passes = _livelock_step(
                signature != last_signature,
                (last_ghost_count is not None and ghosts_now < last_ghost_count)
                or stock_grew,
                unchanged_passes,
            )
            last_signature = signature
            last_ghost_count = ghosts_now
            last_items_total = items_now
            _refuse_to_spin(unchanged_passes, signature, goal_item)
            # Generation is mission infrastructure, not a side effect of
            # bridges: live run 36 (2026-08-23) burned its whole iteration
            # budget waiting through brownouts while every top-up trigger was
            # a bridge event that never came. Re-check the grid periodically;
            # _top_up_solar_generation self-gates on threshold and stock.
            if tick - last_generation_check_tick >= _GENERATION_CHECK_INTERVAL_TICKS:
                last_generation_check_tick = tick
                if (
                    _top_up_solar_generation(
                        client, bridge, surface, force, reference_point, emit,
                    )
                ):
                    continue
            # Start only dependency-ready prep. It may consume a live plate
            # foundation, but it cannot recursively decide which raw
            # foundation to open: iron and copper remain explicit below.
            if _prep_intermediate(
                client, bridge, surface, force, prepped, mall_targets,
                reference_point, emit,
            ):
                continue
            # Establish iron, then copper, through the only startup path that
            # may create their extraction systems. Between those steps the
            # readiness gate above may start gears from live iron; after copper
            # starts it may add cable and then circuits.
            if not all(
                _direct_plate_foundation_ready(client, surface, force, plate)
                for plate in PLATE_FOUNDATION_BUILD_ORDER
            ):
                if _prep_plate_foundation(
                    client, bridge, surface, force, prepped,
                    deferred_plate_targets, mall_targets, reference_point, emit,
                    background_targets, pending_plate_materials,
                ):
                    continue
                position = _serve_ready_pass(
                    client, bridge, surface, force, task, tick, mall_targets,
                    background_targets, priorities, reference_point, goal_item, emit,
                )
                if position is _SHORTAGE:
                    continue
                iteration += 1
                if position is not None:
                    emit(f"GOAL MET: {goal_item} is producing at {position}")
                    return {"ok": True, "iterations": iteration, "output_position": position}
                continue
            # Once both metal foundations exist, stand up the belt producer
            # before demand-driven extraction spends the remaining reserve.
            if _BELT_CELL_PREP_KEY not in prepped and _prep_the_belt_cell(
                client, bridge, surface, force, prepped, mall_targets,
                reference_point, emit,
            ):
                continue
            # Extraction second: it is the expensive half -- 14 drills against
            # the prep set's two assemblers -- and an intermediate built over a
            # starved plate line just starves too.
            # Recheck every plate after mall targets change. A later shortage
            # such as a chemical cell's pipe bill can legitimately outgrow the
            # opening iron baseline; a completed baseline must not freeze that
            # capacity. Deferred plate targets are retried only when demand
            # rises, so a failed corridor does not spin every pass.
            # The loop deliberately preserves any(...)'s short-circuit behavior:
            # the first plate that spends the pass wins, while every plate gets
            # reconsidered on the next pass.
            plate_spent = False
            for plate in BASELINE_PLATES:
                spent = _prep_plate_extraction(
                    client, bridge, surface, force, plate, prepped,
                    deferred_plate_targets, mall_targets, reference_point, emit,
                    background_targets, pending_plate_materials,
                )
                if spent:
                    plate_spent = True
                    break
                # A material-shortage pass belongs to the mall. Stop here so
                # another plate cannot consume the finite starter belt reserve;
                # then fall through to _serve_ready_pass to build the queued
                # mall item in this same pass.
                if plate in pending_plate_materials:
                    break
            if plate_spent:
                continue
            # No gate preemption here: a ready surveyed task is useful work,
            # and the promoted gate outranks only DEFERRED tasks (the ranking
            # already excludes those). Preempting ready work spun forever --
            # the gate re-promoted itself each pass while a higher-rated
            # inserter task starved, and the spin guard killed the run
            # (live runs of 2026-08-24 07:25 and 07:47).
            position = _serve_ready_pass(
                client, bridge, surface, force, task, tick, mall_targets,
                background_targets, priorities, reference_point, goal_item, emit,
            )
            if position is _SHORTAGE:
                continue
            iteration += 1
            if position is not None:
                emit(f"GOAL MET: {goal_item} is producing at {position}")
                return {"ok": True, "iterations": iteration, "output_position": position}
        raise StuckError(
            f"Did not reach a working {goal_item} line within {max_iterations} iterations",
            code="controller_iteration_limit",
            classification="bug",
            state="failed",
            details={"goal_item": goal_item, "max_iterations": max_iterations},
        )
    finally:
        _BOOTSTRAP_DISTRICT_LEDGER = None
        _MATERIAL_RESERVATION_LEDGER = None
        set_active_material_ledger(None)
        end_run_budget()
        client.close()
        bridge.close()
