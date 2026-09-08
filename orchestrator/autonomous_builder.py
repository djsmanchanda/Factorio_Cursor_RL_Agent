# Path: orchestrator/autonomous_builder.py
# Purpose: Goal-driven autonomous factory expansion on a real base -- given a target item, recursively ensures every ingredient in its recipe chain has a real, working production stage, deciding placement, connections, and troubleshooting itself.

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from orchestrator import extraction_state, live_base, resource_patches, stage_extraction
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
from orchestrator.bootstrap_profiles import bootstrap_profile
from orchestrator.bootstrap_supply import (
    BootstrapSupplyError, ensure_bootstrap_supply,
)
from orchestrator.construction_stock import FALLBACK_STACK_SIZE, MallReserve, mall_reserve
from orchestrator.baseline_production import (
    BASELINE_MACHINES, BASELINE_PLATES, BOOTSTRAP_FURNACE_CAPS,
    BOOTSTRAP_MALL_SLOT_TARGET,
    CHEMICAL_BOOTSTRAP_LADDER, CORE_MALL_PRODUCERS,
    PLATE_FOUNDATION_BUILD_ORDER, PLATE_FOUNDATION_FURNACES,
    RATIONED_MALL_BATCH_ITEMS,
    baseline_build_order, baseline_drill_phase, baseline_plate_draw,
    coherent_drill_cap, demand_adjusted_plate_draw, drill_phase_for_draw,
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
    mall_entity_positions,
    mall_slot_count,
    mall_slot_uses_shared_provider,
    next_shared_provider_retrofit_plan,
    preview_mall_allocation,
    refresh_paired_mall_requests,
    rebuild_incomplete_mall_cell,
)
from orchestrator.mall_bootstrap import (
    MallBootstrapLoan, MallBootstrapStep,
    _LOAN_REQUEST_HEADROOM_FRACTION,
    active_bootstrap_loans,
    bootstrap_external_shortages,
    bootstrap_loan_plan,
    next_bootstrap_step,
    promote_bootstrap_loan_plan,
    restore_bootstrap_loan_plan,
)
from orchestrator.material_reservations import (
    MaterialReservationLedger, plan_material_bill, set_active_material_ledger,
)
from orchestrator.parts_mall import (
    MaterialShortage, add_demands, mission_mall_targets, wait_for_stock,
)
from orchestrator.intermediate_scaling import (
    backlog_seconds,
    live_intermediate_demand, promoted_companion_machine_count,
    promoted_line_belt_type, promoted_line_machine_count,
)
from orchestrator.priority_list import PriorityList
from orchestrator.power_district import ensure_power_capacity
from orchestrator.recoverable_retirement import (
    RecoverableRetirementError, retire_entities_via_bots,
)
from orchestrator.extraction_transport import (
    planned_entity_count, planned_footprint_tiles, preflight_ingredient_transport,
)
from orchestrator.refinery_state import (
    ManagedRefineryState, assert_refinery_removals_owned,
    infer_refinery_state, live_refinery_placements,
    missing_refinery_placements, recover_managed_refinery,
)
from orchestrator.stage_chemical import (
    ensure_battery_cell, ensure_coal_mine, ensure_oil_cell,
    ensure_sulfuric_acid_cell,
)
from orchestrator.stage_extraction import (
    LOCAL_MODE_MAX_LINK_TILES, existing_mine_service_geometry,
    candidate_mining_origins as _candidate_mining_origins,  # noqa: F401 - compatibility export
    choose_mining_origin as _choose_mining_origin,  # noqa: F401 - compatibility export
    mining_drill_positions as _mining_drill_positions,  # noqa: F401 - compatibility export
    mine_short_of_furnace_appetite,
    plan_local_extraction,
    planned_smelter_count_for_drills,
    smelter_count_for_drills,
)
from planners.resource_layouts import mine_substation_positions
from planners.assembler_tiers import (
    UPGRADE_RESERVE, BaseCapability, entity_upgrade_plan, mall_machine,
)
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
from orchestrator.work_state import WorkStateSignal
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
    recipe_group_name, recipe_group_requests,
    request_multiplier as standard_mall_request_multiplier,
)
from planners.plan_validation import ENTITY_FOOTPRINTS, actions as plan_actions
from planners.recipe_data import (
    BELT_TIERS,
    LINE_RECIPES,
    MACHINE_SPEEDS,
    ITEM_STACK_SIZES,
    SIDELOAD_NORTH_COL,
    SIDELOAD_SOUTH_COL,
    install_catalog_line_recipes,
    install_catalog_machines,
    install_catalog_stack_sizes,
    machine_ingredient_rates,
)
from planners.smelter_block import (
    FURNACES_PER_MODULE, generate_managed_refinery_extension_plan,
    generate_managed_refinery_plan, refinery_interfaces, scheduled_refinery_target,
    split_managed_refinery_extension_plan,
)
from tools.rcon_client import RconClient

Point = tuple[float, float]
_DEFAULT_MACHINE_COUNT = 2
_FAST_BELT_IRON_CAPACITY = 24


class ProductionPrerequisiteDeferred(WorkStateSignal):
    """A construction item must wait for a cheaper upstream capacity phase."""

    def __init__(
        self, message: str, *, code: str = "production_prerequisite_deferred",
        classification: str = "intended_difficulty", state: str = "planned",
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(
            message, code=code, classification=classification,
            state=state, details=details,
        )

# A livelock re-selects the same task and gets the same result forever.
# max_iterations never bounded it: `iteration` only advances on goal work,
# so every mall/prep `continue` skipped it and a stuck run spun for hours.
# This counts consecutive passes that chose the same task at the same
# completion -- real progress moves one of them.
_MAX_UNCHANGED_PASSES = 12
# Construction and charging continue in Factorio while the controller waits.
# Ten seconds preserves the 120-second unchanged-pass safety horizon while
# avoiding up to 30 seconds of dispatch latency at every cleared foundation
# condition.
_PENDING_FOUNDATION_POLL_SECONDS = 10.0
_BOOTSTRAP_LOAN_POLL_SECONDS = 5.0
_GAME_TICKS_PER_SECOND = 60.0
_MAX_PRIORITY_SLEEP_SECONDS = 30.0


# How long one logistic-coverage remedy may wait for a just-connected
# roboport to charge before the round is honestly reported as a no-op. A fresh
# port lands at ~50% of 100 MJ and draws megawatts while topping up; without a
# real wait every round re-diagnosed the same orphaned chest within seconds,
# burned all six rounds on nothing, and killed the run (live, 2026-08-22).
_LOGISTIC_CHARGE_WAIT_SECONDS = 90.0

# A construction-material provider may be needed while an earmarked stage's
# own logistic inventory is empty. Keep that temporary chest outside the full
# stage area so it cannot occupy a reserved mine or refinery expansion tile.
# The cache makes every remediation round reuse the same chest.
_STAGE_DELIVERY_PROVIDERS: dict[tuple[str, str, str, Point], Point] = {}
_REFINERY_SITE_RESERVATIONS: dict[
    tuple[str, str, str], tuple[Point, Point],
] = {}
_BOOTSTRAP_DISTRICT_LEDGER: BootstrapDistrictLedger | None = None
_MATERIAL_RESERVATION_LEDGER: MaterialReservationLedger | None = None
# The first steel furnace is an upstream construction capability: once its
# finite, steel-cyclic pole stock is selected, other projects must carry any
# shared-stock shortfall instead of taking those anchors back.
_STEEL_STARTER_RESERVATION_PRIORITY = 100


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


def _all_plate_pioneers_released() -> bool:
    """Whether every temporary iron, copper, and stone starter is retired."""
    states = tuple(
        _bootstrap_state(recipe) for recipe in PLATE_FOUNDATION_BUILD_ORDER
    )
    return all(
        state is not None and state.lifecycle_state == "released"
        for state in states
    )


def _bootstrap_owned_actions(
    recipe: str, furnace_count: int,
) -> tuple[dict, ...]:
    state = _bootstrap_state(recipe)
    if state is None or state.replacement_furnaces != furnace_count:
        return ()
    return state.replacement_actions


def _bootstrap_owned_transport_tiles(
    recipe: str, replacement_origin: Point,
) -> set[tuple[int, int]]:
    """Exact route reservation available to a matching provisioning retry."""
    state = _bootstrap_state(recipe)
    if (
        state is None
        or state.lifecycle_state != "provisioning"
        or state.replacement_origin != replacement_origin
    ):
        return set()
    return set(state.reservations.get("transport_service", frozenset()))


def _bootstrap_owned_transport_route(
    recipe: str, replacement_origin: Point, source: Point,
) -> tuple[dict, ...] | None:
    """Return the first exact route on a provisioning retry, or fail closed."""
    state = _bootstrap_state(recipe)
    if (
        state is None
        or state.lifecycle_state != "provisioning"
        or state.replacement_origin != replacement_origin
    ):
        return None
    if state.transport_source is None or not state.transport_actions:
        error = BootstrapLifecycleError(
            f"{recipe} provisioning predates exact transport ownership; "
            "refusing to infer and duplicate its route"
        )
        raise _bootstrap_lifecycle_stuck(recipe, error) from error
    if state.transport_source != source:
        error = BootstrapLifecycleError(
            f"{recipe} transport source moved from {state.transport_source} to {source}"
        )
        raise _bootstrap_lifecycle_stuck(recipe, error) from error
    return tuple(json.loads(json.dumps(action)) for action in state.transport_actions)


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
    if state is None:
        # Mall-first opening (2026-09-04): no starter stack precedes the
        # first foundation, so the opening district itself is the pioneer.
        # Without a lifecycle the managed gates (science transition, pipe
        # promotion, pioneer release) could never observe this district.
        _record_bootstrap_pioneer(recipe, extraction.ore, replacement_plan)
        state = _bootstrap_state(recipe)
    if state is None or state.lifecycle_state == "released":
        return
    if state.lifecycle_state != "pioneer":
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
            transport_source=extraction.ore_output,
            transport_actions=route_actions,
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


def _mark_bootstrap_replacement_submitted(recipe: str) -> None:
    """Commit the point after which retries reconcile instead of resubmit."""
    if _BOOTSTRAP_DISTRICT_LEDGER is None:
        return
    try:
        _BOOTSTRAP_DISTRICT_LEDGER.mark_replacement_submitted(recipe)
    except BootstrapLifecycleError as error:
        raise _bootstrap_lifecycle_stuck(recipe, error) from error


def _submitted_bootstrap_plan(state: BootstrapDistrictState) -> dict:
    """Rebuild the exact owned system view used only for live diagnosis."""
    return {
        "surface": state.surface,
        "force": state.force,
        "phases": [
            {
                "name": f"owned_{state.recipe}_replacement",
                "actions": [json.loads(json.dumps(action))
                            for action in state.replacement_actions],
            },
            {
                "name": f"owned_{state.ore}_transport",
                "actions": [json.loads(json.dumps(action))
                            for action in state.transport_actions],
            },
        ],
    }


def _ensure_power_anchor_on_generated_network(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    position: Point, label: str, emit: Callable[[str], None], *,
    reserved_tiles: set[tuple[int, int]] | None = None,
) -> None:
    """Require the exact planned pole to belong to a generating network.

    Entity status is not sufficient evidence: a pole on an isolated network
    can report an ordinary status while every machine it serves is unpowered.
    """
    network_id = live_base.pole_network_id(client, surface, position)
    generation = (
        live_base.network_generation_kw(client, surface, force, position)
        if network_id is not None else None
    )
    if generation is not None and generation > 0:
        return
    emit(
        f"  POWER ANCHOR REPAIR: {label} at {position} is not connected "
        "to a generating network"
    )
    acted = extend_power(
        client, bridge, surface, force, position, emit,
        reserved_tiles=reserved_tiles,
    )
    verified_network = live_base.pole_network_id(client, surface, position)
    verified_generation = (
        live_base.network_generation_kw(client, surface, force, position)
        if verified_network is not None else None
    )
    if (
        not acted or verified_network is None
        or verified_generation is None or verified_generation <= 0
    ):
        raise StuckError(
            f"{label} power anchor at {position} remained outside a "
            "generating network after repair",
            code="stage_power_connection_failed", classification="bug",
            state="power_wait",
            details={
                "label": label,
                "position": [position[0], position[1]],
                "network_id": verified_network,
                "generation_kw": verified_generation,
            },
        )
    emit(
        f"  POWER ANCHOR VERIFIED: {label} joined generated network "
        f"{verified_network}"
    )


def _reconcile_submitted_bootstrap_replacement(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    state: BootstrapDistrictState, emit: Callable[[str], None],
) -> Point:
    """Finish and validate one submitted district without billing it again."""
    if (
        state.replacement_origin is None
        or state.replacement_provider is None
        or state.transport_source is None
    ):
        raise _bootstrap_lifecycle_stuck(
            state.recipe,
            BootstrapLifecycleError(
                f"{state.recipe} submitted replacement lacks persisted geometry"
            ),
        )
    plan = _submitted_bootstrap_plan(state)
    normal_provider = refinery_interfaces(
        state.replacement_furnaces,
        origin_x=state.replacement_origin[0],
        origin_y=state.replacement_origin[1],
        variant="basic",
    ).provider
    mirrored_provider = refinery_interfaces(
        state.replacement_furnaces,
        origin_x=state.replacement_origin[0],
        origin_y=state.replacement_origin[1],
        variant="basic", vertical_mirror=True,
    ).provider
    if state.replacement_provider == normal_provider:
        vertical_mirror = False
    elif state.replacement_provider == mirrored_provider:
        vertical_mirror = True
    else:
        raise _bootstrap_lifecycle_stuck(
            state.recipe,
            BootstrapLifecycleError(
                f"{state.recipe} persisted provider {state.replacement_provider} "
                "matches neither approved refinery orientation"
            ),
        )
    belt_tiles = sum(
        1 for action in state.transport_actions
        if "transport-belt" in action.get("entity", "")
    )
    emit(
        f"BOOTSTRAP DISTRICT: reconciling submitted {state.recipe} replacement; "
        "checking construction, coverage, power, transport, and measured output"
    )
    _bring_modular_refinery_up(
        client, bridge, surface, force, state.recipe, plan,
        state.replacement_furnaces, state.replacement_origin, emit,
        feed_grace_seconds=transport_grace_seconds(_DEFAULT_BELT, belt_tiles),
        variant="basic", vertical_mirror=vertical_mirror,
    )
    _retire_standing_bootstrap_cells(
        client, bridge, surface, force, state.recipe, state.ore,
        state.transport_source, emit,
    )
    return state.replacement_provider


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
    _name: str, origin: Point, area: tuple[Point, Point] | None,
) -> Point:
    if area is None:
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


def _wait_for_construction_network(
    client: RconClient, surface: str, force: str,
    area: tuple[Point, Point] | None, emit: Callable[[str], None],
) -> bool:
    """Wait once for a geometrically covering roboport to become usable."""
    if area is None:
        return False
    initial = live_base.ghost_blockages(client, surface, force, area)
    initial_outside = sum(
        ghost.get("reason") == "out_of_construction_range" for ghost in initial
    )
    deadline = time.monotonic() + _LOGISTIC_CHARGE_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(3.0)
        current = live_base.ghost_blockages(client, surface, force, area)
        outside = sum(
            ghost.get("reason") == "out_of_construction_range" for ghost in current
        )
        if len(current) < len(initial) or outside < initial_outside:
            return True
    emit(
        "    geometrically covering roboport made no construction progress after "
        f"{_LOGISTIC_CHARGE_WAIT_SECONDS:.0f}s -- returning to diagnosis"
    )
    return False


#: Consecutive rounds one stage may wait on transferable stock before the
#: mall remakes the item. Force stock counts committed requester WIP that bots
#: may never release, so a ghost remedy that waits on transferable stock
#: forever starves behind a healthy-looking force total (copper refinery,
#: 2026-09-03: 12 belts in force, 0 transferable, 20 dead rounds). A few
#: rounds of patience cover bots mid-flight; then the wait becomes mall
#: demand through the same MaterialShortage path as a force-wide shortage.
_TRANSFERABLE_WAIT_ROUNDS = 3
_TRANSFERABLE_WAITS: dict[tuple[str, str, str, str], int] = {}

#: Consecutive stage-delivery attempts per stage item. A bare MATERIAL
#: DELIVERY line cannot tell "retried after restock" from "tried once while
#: empty and never again" (oil district, 2026-09-06: one moved-0 pumpjack
#: delivery, mall restocked to 1, no further attempt logged before the
#: 12-pass guard fired). Telemetry state only; reset per run.
_STAGE_DELIVERY_ATTEMPTS: dict[tuple[str, str, str, str], int] = {}

#: Mall items currently blocking placed-ghost construction. Only a submitted
#: replacement names what its waiting ghosts need -- bulk prep queues are
#: deliberately excluded, since marking a whole foundation bill binding flattens
#: every rating to 100 and the alphabetical tie-break then serves a stuck task
#: forever (2026-09-03: splitter starved everything behind it for 12 passes).
#: While named, the item outranks standing reserves at task selection and its
#: loan cell is shielded from preempt by non-blocking batches. Entries clear
#: once their demand leaves the queue; see _survey_pass.
_BLOCKING_MALL_ITEMS: set[str] = set()


def _mark_binding_demands(shortage: MaterialShortage) -> None:
    """Name foundation-blocked items so scheduling favors them."""
    _BLOCKING_MALL_ITEMS.update(shortage.required)


#: Per-item transferable history for drain-aware pops: when a loan keeps
#: advancing an item whose spendable stock never accumulates, a live consumer
#: is eating output as fast as it is made — holding the demand (and the cell)
#: forever is how the 12-pass guard tripped on 138/147 belts. Snapshotted in
#: _survey_pass; read by _drain_aware_pop_due.
_DRAIN_WATCH_LAST_TRANSFERABLE: dict[tuple[str, str, str], int] = {}
_DRAIN_WATCH_PREVIOUS_TRANSFERABLE: dict[tuple[str, str, str], int] = {}


def _emit_stage_delivery_telemetry(
    client: RconClient, surface: str, force: str, name: str, item: str,
    required: int, stock: Mapping[str, int], origin: Point,
    emit: Callable[[str], None],
) -> None:
    """One read-only diagnostic line per stage material-delivery attempt.

    Zero behavior change: every probe is guarded, nothing is submitted, and
    any failure emits a skip marker instead of raising. The attempt ordinal
    is the discriminator the 2026-09-06 oil terminal lacked: attempt 1 with
    net 0 followed by silence means the reconcile never re-ran, while a
    later attempt with net/transferable stocked and moved 0 means the
    transfer itself cannot complete. Net vs transferable vs ghost-network
    counts name where the stock sits, and the wait count shows the
    transferable-escalation state.
    """
    try:
        key = (surface, force, name, item)
        attempt = _STAGE_DELIVERY_ATTEMPTS.get(key, 0) + 1
        _STAGE_DELIVERY_ATTEMPTS[key] = attempt
    except Exception:
        attempt = -1
    try:
        net = int(stock.get(item, 0))
    except Exception:
        net = -1
    try:
        transferable = int(live_base.transferable_item_count(
            client, surface, force, item,
        ))
    except Exception:
        transferable = -1
    try:
        local = live_base.network_item_count(client, surface, force, origin, item)
        local_text = "none" if local is None else str(int(local))
    except Exception:
        local_text = "?"
    try:
        waits = int(_TRANSFERABLE_WAITS.get((surface, force, name, item), 0))
    except Exception:
        waits = -1
    try:
        emit(
            f"  STAGE DELIVERY TELEMETRY: {name} {item} attempt {attempt} "
            f"need {int(required)} "
            f"| net {net} transferable {transferable} local {local_text} "
            f"| waits {waits}"
        )
    except Exception as error:
        try:
            emit(f"  STAGE DELIVERY TELEMETRY skipped: {error}")
        except Exception:
            pass


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
    elif remedy == "coverage_charge_wait":
        emit(
            "    construction coverage is already present geometrically -- "
            "waiting for the covering roboport/network instead of placing another"
        )
        acted = _wait_for_construction_network(
            client, surface, force, area, emit,
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
    elif remedy == "roboport_power" or remedy.startswith("roboport_power_at:"):
        if remedy.startswith("roboport_power_at:"):
            _, x, y = remedy.split(":", 2)
            nearest = (float(x), float(y))
        else:
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
        _emit_stage_delivery_telemetry(
            client, surface, force, name, item, required, stock,
            origin, emit,
        )
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
            transferable = live_base.transferable_item_count(
                client, surface, force, item,
            )
            if transferable < required:
                wait_key = (surface, force, name, item)
                waits = _TRANSFERABLE_WAITS.get(wait_key, 0) + 1
                _TRANSFERABLE_WAITS[wait_key] = waits
                if waits >= _TRANSFERABLE_WAIT_ROUNDS:
                    _TRANSFERABLE_WAITS.pop(wait_key, None)
                    raise MaterialShortage(name, {item: required}, stock)
                emit(
                    f"    {item} exists in force stock ({stock[item]}) but only "
                    f"{transferable} is in transferable provider/storage stock; "
                    "waiting instead of placing an empty stage chest"
                )
                return False
            _TRANSFERABLE_WAITS.pop((surface, force, name, item), None)
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
        removal = live_base.remove_ghost_at(
            client, surface, force, entity, position,
        )
        if not removal.cleared:
            raise StuckError(
                f"exact stale ghost removal did not clear {entity}@{position}",
                code="stale_ghost_removal_failed", classification="bug",
                state="failed",
                details={
                    "entity": entity,
                    "position": list(position),
                    "reason": str(ghost.get("reason", "unknown")),
                    "found": removal.found,
                    "cleared": removal.cleared,
                },
            )
        if not removal.found:
            continue
        removed += 1
        actions.append(
            {"action_type": "place_ghost", "entity": entity,
             "position": {"x": position[0], "y": position[1]}},
        )
    if not actions:
        emit(
            "  STALE GHOST: target resolved before exact removal; no "
            "replacement ghost required"
        )
        return True
    plan = {"phases": [{"name": "rebuild_stale_ghost", "actions": actions}]}
    plan["surface"], plan["force"] = surface, force
    report = _submit(
        client, bridge, surface, plan, "rebuild_stale_ghost", emit,
    )
    placed = int(report.get("succeeded_placements", 0))
    if placed != removed:
        remaining_ghosts = live_base.ghost_blockages(
            client, surface, force, area,
        )
        if not remaining_ghosts:
            emit(
                "  STALE GHOST: target resolved before its replacement was "
                "submitted; no rebuilt ghost claimed"
            )
            return True
        ghost_details = [
            {
                "entity": str(ghost.get("entity", "")),
                "position": list(ghost["position"]),
                "reason": str(ghost.get("reason", "unknown")),
            }
            for ghost in remaining_ghosts
        ]
        raise StuckError(
            f"stale ghost rebuild placed {placed}/{removed} replacement "
            f"ghost(s): {ghost_details}",
            code="stale_ghost_rebuild_failed", classification="bug",
            state="failed",
            details={
                "ghosts": ghost_details,
                "attempted_placements": int(
                    report.get("attempted_placements", 0)
                ),
                "succeeded_placements": placed,
                "already_present_placements": int(
                    report.get("already_present_placements", 0)
                ),
                "failed_placements": int(report.get("failed_placements", 0)),
                "placement_failures": report.get("placement_failures", []),
            },
        )
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
    if rebuilt_stale and remaining:
        pending = live_base.ghost_blockages(client, surface, force, area)
        if pending:
            ghost_details = []
            for ghost in pending:
                detail = {
                    "entity": str(ghost.get("entity", "")),
                    "position": list(ghost["position"]),
                    "reason": str(ghost.get("reason", "unknown")),
                }
                for key in (
                    "network_id", "construction_robots",
                    "available_construction_robots", "item", "required",
                    "network_item_count",
                ):
                    if key in ghost:
                        detail[key] = ghost[key]
                ghost_details.append(detail)
            raise StuckError(
                f"{name}: {remaining} ghost(s) remained after an exact "
                f"remove-and-resubmit cycle: {ghost_details}",
                code="stale_ghost_construction_failed", classification="bug",
                state="constructing",
                details={
                    "ghosts": ghost_details,
                    "remaining": remaining,
                    "rounds": total_rounds,
                    "seconds": total_rounds * interval,
                },
            )
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
        # The substation is an ordinary funded ghost. Do not defer its
        # connection once bots revive it: otherwise the drills finish later on
        # an isolated grid and the first recovery pass has to rebuild their
        # construction supply around them.
        _ensure_power_anchor_on_generated_network(
            client, bridge, surface, force, substation_position,
            f"{extraction.ore} mine", emit,
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
    _ensure_power_anchor_on_generated_network(
        client, bridge, surface, force, substation_position,
        f"{extraction.ore} mine", emit,
        reserved_tiles=planned_footprint_tiles(plan),
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
    submitted_new_capacity = bool(
        extraction.build_plan is not None or extraction.expansion_positions
    )
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
    if submitted_new_capacity:
        resource_patches.invalidate_patch_cache(
            client, surface, extraction.ore,
        )
        stage_extraction.invalidate_new_mine_cache(
            client, surface, extraction.ore,
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
    variant: str = "standard", vertical_mirror: bool = False,
) -> None:
    """Power, cover, and diagnose the complete modular refinery footprint."""
    interface = refinery_interfaces(
        furnace_count, origin_x=origin[0], origin_y=origin[1], variant=variant,
        vertical_mirror=vertical_mirror,
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
    _ensure_power_anchor_on_generated_network(
        client, bridge, surface, force, interface.power_anchor,
        f"modular refinery for {recipe}", emit, reserved_tiles=reserved,
    )
    stuck = _diagnose_machines(
        client, surface, machines, emit, grace_seconds=feed_grace_seconds,
        bridge=bridge, force=force,
    )
    if stuck:
        raise StuckError(f"modular refinery for {recipe} built but not healthy: {stuck}")


def _committed_refinery_state(
    recipe: str, observed: ManagedRefineryState, target_machines: int,
) -> ManagedRefineryState:
    """Recover the pre-cutover owner while growth ghosts become real.

    Once all new furnaces are built, live recovery can see the target lattice
    even though the old output adapter is intentionally still serving plates.
    The district ledger is the durable commit marker for that in-between state.
    """
    lifecycle = _bootstrap_state(recipe)
    if (
        lifecycle is None
        or lifecycle.replacement_origin != observed.origin
        or lifecycle.replacement_furnaces <= 0
        or lifecycle.replacement_furnaces >= target_machines
        or not lifecycle.replacement_actions
    ):
        return observed
    machine = LINE_RECIPES[recipe]["machine"]
    positions = tuple(sorted(
        (float(action["position"]["x"]), float(action["position"]["y"]))
        for action in lifecycle.replacement_actions
        if action.get("entity") == machine
        and action.get("action_type") in {"place_entity", "place_ghost"}
    ))
    if len(positions) != lifecycle.replacement_furnaces:
        raise StuckError(
            f"{recipe} district ledger owns {lifecycle.replacement_furnaces} "
            f"furnaces but records {len(positions)} placements",
            code="refinery_ownership_ledger_mismatch",
            classification="bug", state="failed",
            details={
                "recipe": recipe,
                "replacement_furnaces": lifecycle.replacement_furnaces,
                "recorded_placements": len(positions),
            },
        )
    committed = infer_refinery_state(
        recipe, positions, variant=observed.variant,
        vertical_mirror=observed.vertical_mirror,
    )
    return replace(committed, owned_actions=lifecycle.replacement_actions)


def _extend_plate_smelter(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, state: ManagedRefineryState, target_machines: int,
    ore_output: Point, emit: Callable[[str], None], *,
    allow_unfunded_ghosts: bool = False,
) -> Point:
    """Build refinery growth completely before an atomic output cutover."""
    del ore_output
    observed_furnaces = state.furnace_count
    state = _committed_refinery_state(recipe, state, target_machines)
    origin = state.origin
    # Keep the cheaper bootstrap geometry while it grows. Migrating a basic
    # starter block to the standard two-row interface adds belts on its old
    # footprint and can collide with valid infrastructure before capacity grows.
    target_variant = state.variant
    full = generate_managed_refinery_plan(
        recipe, target_machines, origin_x=origin[0], origin_y=origin[1],
        variant=target_variant, vertical_mirror=state.vertical_mirror,
    )
    interface = refinery_interfaces(
        target_machines, origin_x=origin[0], origin_y=origin[1],
        variant=target_variant, vertical_mirror=state.vertical_mirror,
    )
    if target_machines <= state.furnace_count:
        emit(
            f"SMELTER COHESION: existing modular {recipe} block already has "
            f"{state.furnace_count}/{target_machines} furnace(s)"
        )
        _bring_modular_refinery_up(
            client, bridge, surface, force, recipe, full,
            target_machines, origin, emit, variant=target_variant,
            vertical_mirror=state.vertical_mirror,
        )
        return interface.provider
    delta = generate_managed_refinery_extension_plan(
        recipe, state.furnace_count, target_machines,
        origin_x=origin[0], origin_y=origin[1],
        current_variant=state.variant, target_variant=target_variant,
        vertical_mirror=state.vertical_mirror,
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
        f"{state.furnace_count} to {target_machines}; build growth, then cut over End/provider"
    )
    growth, cutover = split_managed_refinery_extension_plan(delta, recipe)
    _prepare_replacement_services(
        client, bridge, surface, force, full, growth, emit,
    )
    if growth.get("phases"):
        missing = missing_refinery_placements(
            client, surface, force, growth,
        )
        if missing:
            _submit(
                client, bridge, surface, growth,
                f"prepare_{recipe}_refinery_growth", emit,
                allow_unfunded_ghosts=allow_unfunded_ghosts,
            )
        remaining = _wait_for_ghosts(
            client, surface, force, _plan_area(growth, padding=0.0),
        )
        if remaining:
            emit(
                f"SMELTER CUTOVER WAIT: {recipe} has {remaining} growth "
                f"ghost(s); keeping the {state.furnace_count}-furnace provider live"
            )
            raise ProductionPrerequisiteDeferred(
                f"{recipe} refinery growth must finish before its provider moves",
                code="refinery_growth_construction_wait", state="constructing",
                details={
                    "recipe": recipe,
                    "committed_furnaces": state.furnace_count,
                    "observed_furnaces": observed_furnaces,
                    "target_furnaces": target_machines,
                    "remaining_ghosts": remaining,
                },
            )
        absent = missing_refinery_placements(
            client, surface, force, growth,
        )
        if absent:
            first = absent[0]
            raise StuckError(
                f"{recipe} refinery growth lost {len(absent)} placement(s) "
                f"before cutover; first is {first['entity']} at "
                f"{tuple(first['position'].values())}",
                code="refinery_growth_incomplete", classification="bug",
                state="failed",
                details={"recipe": recipe, "missing_placements": len(absent)},
            )
    # This check is deliberately repeated immediately before cutover: growth
    # may take minutes, and no old adapter may be removed if ownership drifted.
    try:
        assert_refinery_removals_owned(client, surface, force, state, cutover)
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
    cutover_name = f"cutover_{recipe}_refinery_output"
    # A producer-backed promise is enough for harmless growth ghosts, but not
    # for the destructive output handoff. Warehouse the exact cutover bill.
    assert_affordable(
        client, surface, force, cutover, cutover_name, emit, True,
    )
    _submit(client, bridge, surface, cutover, cutover_name, emit)
    # A rejected material preflight has not changed the live district. Persist
    # larger ownership only after the executor accepts its delta; recording it
    # first made a released 12-furnace district falsely claim 24 missing units.
    _record_bootstrap_replacement(
        recipe, full, interface.provider, target_machines,
    )
    emit(
        f"BOOTSTRAP OWNERSHIP COMMIT: {recipe} accepted expansion from "
        f"{state.furnace_count} to {target_machines} furnace(s)"
    )
    _bring_modular_refinery_up(
        client, bridge, surface, force, recipe, full,
        target_machines, origin, emit, variant=target_variant,
        vertical_mirror=state.vertical_mirror,
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
        vertical_mirror=state.vertical_mirror,
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
    # Recipe-less furnaces are ambiguous: every starved plate block looks the
    # same.  Merge only idle machines that complete a six-furnace template
    # containing at least one recipe-visible machine.  A structurally perfect
    # but wholly disconnected block may belong to another plate recipe (live
    # copper startup adopted the unfinished iron refinery from 41 tiles away).
    connected_idle: set[Point] = set()
    for candidate in _complete_six_furnace_candidates(
        tuple(sorted(visible | idle_positions)),
    ):
        if visible.intersection(candidate):
            connected_idle.update(set(candidate).intersection(idle_positions))
    positions = tuple(sorted(visible | connected_idle))
    if connected_idle - visible:
        emit(
            f"SMELTER RECOVERY: merged {len(connected_idle - visible)} starved "
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
                f"{recipe} refinery has incomplete furnace modules",
                code="refinery_module_construction",
                state="constructing",
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
        vertical_mirror=getattr(extraction, "smelter_vertical_mirror", False),
    )
    replacement_plan = json.loads(json.dumps(plan))
    interface = refinery_interfaces(
        target, origin_x=origin[0], origin_y=origin[1], variant=variant,
        vertical_mirror=getattr(extraction, "smelter_vertical_mirror", False),
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
    owned_route = _bootstrap_owned_transport_route(recipe, origin, ore_output)
    if owned_route is not None:
        route_actions = list(owned_route)
        belt_type = _DEFAULT_BELT
        emit(
            f"BOOTSTRAP TRANSPORT: reusing {len(route_actions)} exact owned "
            f"action(s) from mine output {ore_output}"
        )
    else:
        owned_transport_tiles = _bootstrap_owned_transport_tiles(recipe, origin)
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
            owned_transport_tiles=owned_transport_tiles,
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
            False, (
                f"mining_{extraction.ore}",
                f"modular_{recipe}_refinery",
            ),
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


def _retire_unused_starter_power_branch(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    starter_poles: Sequence[tuple[str, Point]], recipe: str,
    emit: Callable[[str], None],
) -> int:
    """Peel only the now-empty leaf branch that powered a retired starter.

    A pole is removed only after it supplies no real or ghost consumer and has
    at most one copper-wire neighbour. Re-observing after every bot removal
    lets a two-pole starter and its bridge peel back toward the trunk, then
    stops at the first live consumer or branching junction.
    """
    if not hasattr(client, "command"):
        return 0
    removable_names = {"small-electric-pole", "medium-electric-pole"}
    pending = {
        position: name for name, position in starter_poles
        if name in removable_names
    }
    removed = 0
    while pending:
        progressed = False
        for position, expected_name in tuple(pending.items()):
            context = live_base.pole_context(client, surface, position)
            if context is None or context.get("name") != expected_name:
                pending.pop(position, None)
                continue
            neighbours = tuple(context.get("neighbours", ()))
            if context.get("supplied") or len(neighbours) > 1:
                continue
            plan = {
                "surface": surface,
                "force": force,
                "phases": [{
                    "name": f"retire_unused_{recipe}_starter_power",
                    "actions": [{
                        "action_type": "remove_entity",
                        "entity": expected_name,
                        "position": {"x": position[0], "y": position[1]},
                    }],
                }],
            }
            retire_entities_via_bots(
                client, bridge, surface, force, plan,
                f"unused_{recipe}_starter_power", emit,
            )
            pending.pop(position, None)
            removed += 1
            progressed = True
            for neighbour in neighbours:
                neighbour_context = live_base.pole_context(
                    client, surface, neighbour,
                )
                if (
                    neighbour_context is not None
                    and neighbour_context.get("name") in removable_names
                ):
                    pending.setdefault(
                        neighbour, str(neighbour_context["name"]),
                    )
        if not progressed:
            break
    if removed:
        emit(
            f"BOOTSTRAP POWER RETIRE: recovered {removed} unused pole(s) from "
            f"the dead {recipe} starter branch; stopped at live load or junction"
        )
    return removed


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
            if lifecycle.lifecycle_state == "pioneer":
                error = BootstrapLifecycleError(
                    f"{recipe} pioneer cannot retire before replacement provisioning"
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
                if lifecycle.lifecycle_state == "provisioning":
                    lifecycle = ledger.mark_validating(
                        recipe, measured,
                    )
                if lifecycle.lifecycle_state == "validating":
                    lifecycle = ledger.mark_retiring(recipe)
            except BootstrapLifecycleError as error:
                raise _bootstrap_lifecycle_stuck(recipe, error) from error
    if starter is not None:
        starter_plan = generate_direct_smelter(
            recipe, ore, starter.drill_position, starter.output_direction,
            pole_side=starter.pole_side,
        )
        starter_poles = [
            (
                str(action["entity"]),
                (float(action["position"]["x"]), float(action["position"]["y"])),
            )
            for phase in starter_plan["phases"]
            for action in phase["actions"]
            if action.get("entity") == "medium-electric-pole"
        ]
        plan = retire_direct_smelter_plan(
            recipe, ore, starter.drill_position, starter.output_direction,
            pole_side=starter.pole_side,
        )
        plan["surface"], plan["force"] = surface, force
        try:
            retire_entities_via_bots(
                client, bridge, surface, force, plan,
                f"direct_{recipe}_starter", emit,
            )
        except RecoverableRetirementError as error:
            raise StuckError(
                str(error), code="starter_deconstruction_failed",
                classification="bug", state="retiring",
                details={"recipe": recipe, "starter_kind": "direct"},
            ) from error
        removed += 1
        _retire_unused_starter_power_branch(
            client, bridge, surface, force, starter_poles, recipe, emit,
        )
        emit(
            f"BOOTSTRAP SWAP: full {recipe} system is healthy; construction "
            f"bots recovered the direct starter at {starter.drill_position}"
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
        try:
            retire_entities_via_bots(
                client, bridge, surface, force, plan,
                f"legacy_logistic_{recipe}_starter", emit,
            )
        except RecoverableRetirementError as error:
            raise StuckError(
                str(error), code="starter_deconstruction_failed",
                classification="bug", state="retiring",
                details={"recipe": recipe, "starter_kind": "legacy_logistic"},
            ) from error
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
            try:
                retire_entities_via_bots(
                    client, bridge, surface, force, intake_plan,
                    f"legacy_{ore}_logistic_intake", emit,
                )
            except RecoverableRetirementError as error:
                raise StuckError(
                    str(error), code="starter_deconstruction_failed",
                    classification="bug", state="retiring",
                    details={
                        "recipe": recipe,
                        "starter_kind": "legacy_logistic_intake",
                    },
                ) from error
        emit(
            f"BOOTSTRAP SWAP COMPLETE: direct {recipe} refinery is healthy; "
            f"recovered {legacy_removed} legacy requester cell(s) and their recognized "
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
        # A dry run must not mutate the world: staging coverage ghosts for a
        # plan that is never submitted is exactly the wasted-chain failure this
        # ordering exists to prevent.
        return provider
    _submit(
        client, bridge, surface, plan, f"modular_{recipe}_refinery", emit,
        stage_coverage=lambda: _ensure_plan_construction_coverage(
            client, bridge, surface, force, coverage_plan, emit,
        ),
        allow_unfunded_ghosts=allow_unfunded_ghosts,
    )
    _mark_bootstrap_replacement_submitted(recipe)
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
            vertical_mirror=getattr(extraction, "smelter_vertical_mirror", False),
        )
        _ensure_power_anchor_on_generated_network(
            client, bridge, surface, force, interface.power_anchor,
            f"modular refinery for {recipe}", emit,
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
        vertical_mirror=getattr(extraction, "smelter_vertical_mirror", False),
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
        bootstrap = _bootstrap_state(recipe)
        if not excluded_drill_positions and not expand:
            starter_drills: set[Point] = set()
            pioneer_actions = (
                getattr(bootstrap, "pioneer_actions", ())
                if bootstrap is not None else ()
            )
            if pioneer_actions:
                for a in pioneer_actions:
                    if a.get("entity") == "electric-mining-drill":
                        pos = a.get("position")
                        if pos:
                            starter_drills.add((float(pos["x"]), float(pos["y"])))
            if hasattr(client, "command"):
                try:
                    starter = live_base.direct_plate_starter(
                        client, surface, force, recipe, ore, reference_point,
                    )
                    if starter is not None:
                        starter_drills.add(starter.drill_position)
                        starter_drills.update(starter.additional_drill_positions)
                except Exception:
                    pass
            if starter_drills:
                excluded_drill_positions = tuple(sorted(starter_drills))
        owned_smelter_origin = (
            bootstrap.replacement_origin
            if bootstrap is not None
            and bootstrap.lifecycle_state != "pioneer"
            else None
        )
        owned_smelter_vertical_mirror = False
        if owned_smelter_origin is not None and bootstrap is not None:
            mirrored_provider = refinery_interfaces(
                getattr(bootstrap, "replacement_furnaces", 6),
                origin_x=owned_smelter_origin[0],
                origin_y=owned_smelter_origin[1], variant="basic",
                vertical_mirror=True,
            ).provider
            owned_smelter_vertical_mirror = (
                getattr(bootstrap, "replacement_provider", None)
                == mirrored_provider
            )
        extraction = plan_local_extraction(
            client, surface, force, recipe, reference_point, 3,
            belt_type=belt_type, inserter_type=_DEFAULT_INSERTER,
            reuse_existing=not expand,
            belt_stock=live_base.available_items(
                client, surface, force,
            ).get(belt_type, 0),
            excluded_drill_positions=excluded_drill_positions,
            owned_smelter_origin=owned_smelter_origin,
            owned_smelter_vertical_mirror=owned_smelter_vertical_mirror,
            defer_pending_owned_refinery=expand,
            observe=emit,
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
        raise ProductionPrerequisiteDeferred(
            str(error), code=error.code, classification=error.classification,
            state=error.state, details=error.details,
        ) from error
    except ValueError as error:
        raise StuckError(str(error)) from error
    reserved_area = getattr(extraction, "smelter_reserved_area", None)
    if reserved_area is not None:
        _REFINERY_SITE_RESERVATIONS[(surface, force, recipe)] = (
            reserved_area
        )
    if not expand:
        starved = False
        ore_short = False
        line = None
        try:
            line = live_base.find_line(
                client, surface, force, recipe,
                LINE_RECIPES[recipe]["machine"],
            )
            positions = set(_refinery_machine_positions(
                client, surface, force, recipe, line,
                extraction.smelter_origin, emit,
            ))
            if len(positions) >= FURNACES_PER_MODULE:
                statuses = live_base.entity_statuses(
                    client, surface, sorted(positions),
                )
                working = sum(
                    1 for state in statuses.values()
                    if state == "working"
                )
                starved = working <= len(positions) // 4
                # A half-fed multi-ore module never trips the starvation
                # heuristic above, yet its mine cannot feed it at any drill
                # phase the ladder reaches on machine parity (2026-09-03:
                # stone ran 3/6 fed indefinitely). Ore-rate math names it.
                ore_short = mine_short_of_furnace_appetite(
                    recipe, extraction.drill_count, len(positions),
                    extraction.mining_productivity_bonus,
                )
        except Exception:  # survey unavailable (dry harness): guard passes
            starved = False
            ore_short = False
        if starved or ore_short:
            if _repair_unpowered_existing_mine(
                client, bridge, surface, force, extraction,
                extraction.ore_output, recipe, emit,
            ):
                raise ProductionPrerequisiteDeferred(
                    f"{recipe} mine power was repaired; waiting for ore delivery",
                    code="mine_power_repair_wait",
                    classification="bug",
                    state="power_wait",
                    details={"recipe": recipe, "ore": extraction.ore},
                )
            resuming_provisioning = (
                bootstrap is not None
                and bootstrap.lifecycle_state == "provisioning"
            )
            if (
                line is None or getattr(line, "produced_count", 1) == 0
            ) and not resuming_provisioning:
                emit(
                    f"  REFINERY STARTUP PENDING: {recipe} has {working}/"
                    f"{len(positions)} furnace(s) fed but no completed plates; "
                    "holding this district for power/transport repair instead of "
                    "opening another mine phase"
                )
                raise ProductionPrerequisiteDeferred(
                    f"{recipe} direct refinery has not produced yet",
                    code="refinery_first_output_wait",
                    state="producing",
                    details={"recipe": recipe},
                )
            if resuming_provisioning:
                emit(
                    f"BOOTSTRAP DISTRICT: resuming {recipe} replacement at "
                    f"its reserved origin {bootstrap.replacement_origin}"
                )
            else:
                # Coherence before growth: drills the live refinery cannot
                # eat (at live productivity and the recipe's own ore ratio)
                # are idle steel in the ground. When the mine already covers
                # its furnaces plus one lookahead row, the unfed furnaces
                # want transport/power repair or more furnaces -- not more
                # drills -- so fall through to cohesion below instead of
                # extending the mine (2026-09-04: 12 iron drills behind 6
                # furnaces while the refinery waited on oil-gated furnaces).
                coherent_cap = coherent_drill_cap(
                    recipe, len(positions),
                    extraction.mining_productivity_bonus,
                )
                if extraction.drill_count >= coherent_cap:
                    emit(
                        f"  MINE COHERENCE: {recipe} mine holds "
                        f"{extraction.drill_count} drill(s) for "
                        f"{len(positions)} furnace(s) (cap {coherent_cap}); "
                        "growing the refinery instead of the mine"
                    )
                else:
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
    if (
        not expand
        and bootstrap is not None
        and bootstrap.lifecycle_state == "provisioning"
        and getattr(bootstrap, "replacement_submitted", False)
    ):
        return _reconcile_submitted_bootstrap_replacement(
            client, bridge, surface, force, bootstrap, emit,
        )
    existing_smelter, cohesive_target = _cohesive_smelter_target(
        client, surface, force, recipe, extraction, expand, emit,
    )
    if expand and existing_smelter is None:
        raise ProductionPrerequisiteDeferred(
            f"{recipe} expansion has no recoverable managed refinery; refusing to "
            "expand its mine ahead of the refinery"
        )
    bootstrap_cap = BOOTSTRAP_FURNACE_CAPS.get(recipe)
    planned_furnaces = (
        cohesive_target
        if cohesive_target is not None
        else getattr(extraction, "furnace_count", 0)
    )
    if (
        bootstrap_cap is not None
        and planned_furnaces > bootstrap_cap
        and not _electric_furnace_producer_started(client, surface, force)
    ):
        emit(
            f"BOOTSTRAP FURNACE CAP: deferring {recipe} expansion at "
            f"{bootstrap_cap} furnace(s) until electric-furnace production is working "
            f"(cohesive target {planned_furnaces})"
        )
        raise ProductionPrerequisiteDeferred(
            f"{recipe} expansion waits for electric-furnace production after "
            f"the {bootstrap_cap}-furnace bootstrap cap",
            code="electric_furnace_supply_wait",
            state="supply_wait",
            details={
                "recipe": recipe,
                "bootstrap_cap": bootstrap_cap,
                "planned_furnaces": planned_furnaces,
            },
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
        f"{recipe} refinery flow is {extraction.smelter_flow_direction}bound"
        f"{' with a vertical mirror' if getattr(extraction, 'smelter_vertical_mirror', False) else ''}: "
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
        # The mine and smelter deltas were preflighted together above. Submit
        # the harmless mine growth before waiting on refinery growth ghosts;
        # otherwise `_extend_plate_smelter` raises its construction wait and
        # the old provider stays live while the six new drills are never
        # placed.  Extra ore can safely queue on the old output adapter, while
        # the destructive End/provider cutover still remains behind growth.
        _submit_mining_plan(
            client, bridge, surface, force, extraction, ore_output, emit,
            allow_unfunded_ghosts=earmark_unfunded,
        )
        provider = _extend_plate_smelter(
            client, bridge, surface, force, recipe, existing_smelter,
            cohesive_target, ore_output, emit,
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
        # Stage construction service and connect the local pole island while
        # the complete future footprint is still known and clear. The starter
        # blueprint follows only after those dependencies are usable.
        if hasattr(client, "command"):
            # Claim the starter's own poles before its extra bridge spends
            # construction stock; otherwise staging power first can consume
            # the exact two anchors the stone blueprint still needs.
            starter_footprint = planned_footprint_tiles(plan)
            assert_affordable(
                client, surface, force, plan, f"direct_{recipe}_starter",
                emit, reserve_project=True,
            )
            # A remote starter's bridge is bot-built too. Give every future
            # chain hop construction coverage before extend_power waits for
            # those poles; deferring coverage to blueprint submission leaves
            # out-of-range terminal poles unable to dispatch forever.
            _ensure_plan_construction_coverage(
                client, bridge, surface, force, plan, emit,
                reserved_tiles=starter_footprint,
            )
            if not extend_power(
                client, bridge, surface, force, positions["power"], emit,
                reserved_tiles=starter_footprint,
                avoid_resources=False,
            ):
                raise StuckError(
                    f"direct {recipe} starter cannot stage generated power beside "
                    f"its planned anchor at {positions['power']}"
                )
        _submit(
            client, bridge, surface, plan, f"direct_{recipe}_starter", emit,
            stage_coverage=lambda: _ensure_plan_construction_coverage(
                client, bridge, surface, force, plan, emit,
                reserved_tiles=planned_footprint_tiles(plan),
            ),
        )
    else:
        _ensure_power_anchor_on_generated_network(
            client, bridge, surface, force, positions["power"],
            f"direct {recipe} starter", emit,
            reserved_tiles=planned_footprint_tiles(plan),
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
# both are made FROM this very plate. Splitters ride the same belt network
# and join the circle on the first foundation (2026-09-04: mall-first opening
# died at +6s on 123 belts + 3 splitters with zero belt stock because the
# splitter shortfall was not recognized as circular). On a cold base any of
# these shortfalls is unaffordable forever even with perfect mall behaviour:
# the demand for the system's own inputs feeding back into itself.
_BOOTSTRAP_CIRCULAR_ENTITIES = frozenset({
    "transport-belt", "underground-belt",
    "fast-transport-belt", "fast-underground-belt",
    "express-transport-belt", "express-underground-belt",
    "splitter", "fast-splitter", "express-splitter",
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
    direct_sideload_feeds: Mapping[str, tuple[Point, str]] | None = None,
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
    direct_sideload_feeds = direct_sideload_feeds or {}
    for ingredient in direct_sideload_feeds:
        modes[ingredient] = "belt"
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
    feed_positions.update({
        ingredient: position
        for ingredient, (position, _direction) in direct_sideload_feeds.items()
    })
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
                destination_is_belt=direct_belt_input or ingredient in direct_sideload_feeds,
                destination_belt_direction=direct_sideload_feeds.get(
                    ingredient, (None, flow_direction),
                )[1],
                allow_chest_source_to_belt=(recipe == "steel-plate"),
            )
            if route is not None:
                preflighted[ingredient] = route
                plan["phases"].append({
                    "name": f"bridge_{ingredient}_to_{recipe}",
                    "actions": route[0],
                })
    return modes, feed_positions, preflighted, direct_belt_input


def _direct_sideload_feeds(
    plan: dict, recipe: str, machine_count: int, ox: float, oy: float,
    inserter_type: str, ingredients: frozenset[str],
    full_bus_ingredients: frozenset[str] = frozenset(),
) -> dict[str, tuple[Point, str]]:
    """Expose selected sideload feeder ends as continuous belt inputs.

    ``LocalLayoutPlanner`` emits infinity chests for standalone layouts. A
    promoted intermediate block instead takes high-rate ingredients from real
    upstream belts. Remove only those chest/inserter loaders; the planner's
    feeder columns remain stable belt-to-belt endpoints for each lane.
    """
    if not ingredients:
        return {}
    recipe_ingredients = tuple(LINE_RECIPES[recipe]["ingredients"])
    unknown = ingredients.difference(recipe_ingredients)
    if unknown:
        raise ValueError(f"{recipe} has no direct sideload ingredient(s): {sorted(unknown)}")
    if len(recipe_ingredients) > 2:
        raise ValueError("direct sideload feeds support at most two ingredients")
    if not full_bus_ingredients.issubset(ingredients):
        raise ValueError("full-bus ingredients must be direct sideload ingredients")

    layout_planner = LocalLayoutPlanner()
    feeders_needed = layout_planner._feeders_needed(
        recipe, machine_count, inserter_type,
    )
    endpoints: dict[str, tuple[Point, str]] = {}
    loader_chests: set[tuple[float, float]] = set()
    for index, ingredient in enumerate(recipe_ingredients):
        if ingredient not in ingredients:
            continue
        if ingredient in full_bus_ingredients:
            belt_west = layout_planner._belt_west(
                recipe, machine_count, "sideload", inserter_type, False,
            )
            endpoints[ingredient] = ((ox + belt_west + 0.5, oy + 0.5), "east")
        elif index == 0:
            # The north feeder flows south; enter at its northern tail.
            endpoints[ingredient] = (
                (ox + SIDELOAD_NORTH_COL + 0.5, oy - feeders_needed[index] - 0.5),
                "south",
            )
        else:
            # The south feeder flows north; enter at its southern tail.
            endpoints[ingredient] = (
                (ox + SIDELOAD_SOUTH_COL + 0.5, oy + feeders_needed[index] + 1.5),
                "north",
            )
        loader_chests.update({
            (action["position"]["x"], action["position"]["y"])
            for phase in plan["phases"]
            for action in phase["actions"]
            if action.get("entity") == "infinity-chest"
            and action.get("infinity_filter") == ingredient
        })

    for phase in plan["phases"]:
        phase["actions"] = [
            action for action in phase["actions"]
            if not (
                (action.get("entity") == "infinity-chest"
                 and (action["position"]["x"], action["position"]["y"]) in loader_chests)
                or (
                    action.get("entity", "").endswith("inserter")
                    and (action["position"]["x"] - 1, action["position"]["y"])
                    in loader_chests
                )
            )
        ]
    return endpoints


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
    """Work the stage up, then connect any residual stranded support."""
    length = machine_count * 3
    stage_area = ((ox - 15, oy - 15), (ox + length + 15, oy + 15))
    # Let the stage's own pole ghosts build before judging support power. A
    # direct support inserter exists immediately after submission, while its
    # adjacent planned pole may still be a ghost; repairing in that interval
    # creates a redundant bridge and can abandon an otherwise valid stage on
    # the bridge's material shortage.
    support_positions = {
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity", "").endswith("inserter")
    }
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
    # Buffer chests themselves are passive; the inserters that load/unload
    # them are not. Any inserter still unpowered after local construction has
    # settled needs a real external repair.
    for position in sorted(support_positions):
        if live_base.entity_status_name(client, surface, position) == "no_power":
            emit(f"  support inserter at {position} has no power -- connecting it")
            if not extend_power(client, bridge, surface, force, position, emit):
                raise StuckError(
                    f"support inserter at {position} is unpowered and cannot be "
                    "reached by a pole chain from any generating network"
                )


def _connect_stage_feeds(
    client: RconClient, bridge: GameBridge, surface: str, force: str, recipe: str,
    feed_positions: dict, ingredient_sources: dict[str, Point], modes: dict,
    preflighted: dict, machine_positions: list, machine_count: int,
    belt_type: str, inserter_type: str, emit: Callable[[str], None], *,
    max_belt_route_tiles: int | None, direct_belt_input: bool,
    destination_belt_direction: str,
    direct_sideload_feeds: Mapping[str, tuple[Point, str]] | None = None,
) -> None:
    """Run each ingredient in, then confirm the machines are actually fed."""
    feed_delay = 0.0
    direct_sideload_feeds = direct_sideload_feeds or {}
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
                destination_is_belt=direct_belt_input or ingredient in direct_sideload_feeds,
                destination_belt_direction=direct_sideload_feeds.get(
                    ingredient, (None, destination_belt_direction),
                )[1],
                allow_chest_source_to_belt=(recipe == "steel-plate"),
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
            if direct_belt_input or ingredient in direct_sideload_feeds:
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


def _use_presteel_starter_power(
    plan: dict, stock: Mapping[str, int] | None = None,
) -> tuple[dict, str]:
    """Keep the first steel furnace's power bill outside its own dependency.

    The normal conversion scaffold uses a substation and medium poles.  Both
    require steel, so making them material-funded made the first steel furnace
    wait on the steel it was supposed to create. Already-stocked medium poles
    are not cyclic, however, and avoid the small-pole recipe's unproducible wood
    leaf. Retain them only when stock funds every anchor; otherwise use an
    all-small-pole plan paid for through the material ledger.
    """
    medium_actions = [
        action
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == "medium-electric-pole"
    ]
    if not medium_actions:
        raise StuckError("steel starter layout has no local power anchors")
    if int((stock or {}).get("medium-electric-pole", 0)) >= len(medium_actions):
        return plan, "medium-electric-pole"
    for phase in plan["phases"]:
        for action in phase["actions"]:
            if action.get("entity") == "medium-electric-pole":
                action["entity"] = "small-electric-pole"
    return plan, "small-electric-pole"


def _steel_starter_power_seed_bill() -> dict[str, int]:
    """Derive the finite pole seed from the actual one-furnace layout."""
    plan = LocalLayoutPlanner().generate_line_layout(
        "steel-plate", STEEL_BASELINE_FURNACES, 0, 0,
        belt_type="transport-belt", inserter_type="inserter",
        feed_style="chest", terminal_collector=True,
    )
    plan = strip_local_power(plan, remove_substations=True)
    _side_sample_plate_output(
        plan, (0, 0), STEEL_BASELINE_FURNACES, "transport-belt",
        tap_inserter_type="inserter",
    )
    count = plan_material_bill(plan).get("medium-electric-pole", 0)
    if count <= 0:
        raise StuckError("steel starter layout has no reservable power anchors")
    return {"medium-electric-pole": count}


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
    direct_sideload_ingredients: frozenset[str] = frozenset(),
    full_bus_ingredients: frozenset[str] = frozenset(),
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
    if direct_sideload_ingredients and flow_direction != "east":
        raise ValueError("direct sideload conversion stages currently require east flow")
    if not direct_sideload_ingredients:
        feed_style = "chest"
    else:
        unknown = direct_sideload_ingredients.difference(
            LINE_RECIPES[recipe]["ingredients"],
        )
        if unknown:
            raise ValueError(f"{recipe} has no direct sideload ingredient(s): {sorted(unknown)}")
        feed_style = "sideload"
    if not full_bus_ingredients.issubset(direct_sideload_ingredients):
        raise ValueError("full-bus ingredients must be direct sideload ingredients")
    steel_starter = (
        recipe == "steel-plate"
        and machine_count == STEEL_BASELINE_FURNACES
    )
    plan = planner.generate_line_layout(
        recipe, machine_count, ox, oy,
        belt_type=belt_type, inserter_type=inserter_type,
        feed_style=feed_style, terminal_collector=True,
        direct_bus_ingredients=full_bus_ingredients,
        flow_direction=flow_direction,
    )
    direct_sideload_feeds = _direct_sideload_feeds(
        plan, recipe, machine_count, ox, oy, inserter_type,
        direct_sideload_ingredients, full_bus_ingredients,
    )
    plan = strip_local_power(plan, remove_substations=steel_starter)
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
    power_anchor = "substation"
    if steel_starter:
        plan, power_anchor = _use_presteel_starter_power(
            plan, _transferable_or_available_stock(client, surface, force),
        )
        if power_anchor == "medium-electric-pole":
            emit(
                "  STEEL STARTER POWER: existing medium-pole stock funds all "
                "local anchors; no wood-dependent small-pole batch is needed"
            )
    _publish_output_chest(plan)
    modes, feed_positions, preflighted, direct_belt_input = _conversion_feed_plan(
        client, bridge, surface, force, recipe, plan, ingredient_sources,
        machine_count, belt_type, flow_direction, emit,
        allow_logistic_inputs=allow_logistic_inputs,
        max_belt_route_tiles=max_belt_route_tiles,
        direct_sideload_feeds=direct_sideload_feeds,
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
        if action["entity"] == power_anchor
    )
    plan["surface"], plan["force"] = surface, force
    try:
        _submit(
            client, bridge, surface, plan, f"conversion_{recipe}", emit,
            stage_coverage=lambda: _ensure_plan_construction_coverage(
                client, bridge, surface, force, plan, emit,
            ),
            reservation_priority=(
                _STEEL_STARTER_RESERVATION_PRIORITY if steel_starter else 50
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
        direct_sideload_feeds=direct_sideload_feeds,
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
        for requester_dx, provider_dy in ((3, -1), (-3, 1), (-3, -1)):
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
    shared_provider: bool = False


def _plan_line(
    client: RconClient, surface: str, force: str, item: str,
    emit: Callable[[str], None], *, upgrade_bootstrap: bool, stock_target: int,
    minimum_machines: int, allow_promotion: bool,
    storage_limit: int | None = None, fill_provider: bool = False,
    shared_provider: bool = False,
) -> _LinePlan:
    """Survey the item's current line and decide whether it should be promoted."""
    declared_spec = LINE_RECIPES[item]
    capability = BaseCapability(
        produces_tier2=_production_started(
            client, surface, force, "assembling-machine-2",
        ),
        produces_tier3=_production_started(
            client, surface, force, "assembling-machine-3",
        ),
        stock=live_base.available_items(client, surface, force),
    )
    spec = {**declared_spec, "machine": mall_machine(item, capability)}
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
        client, surface, force, item, spec, mall_storage_limit,
        finite_batch=(
            not upgrade_bootstrap
            and _is_pre_core_temporary_mall_item(
                client, surface, force, item,
            )
        ),
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
    outstanding = max(0, max(stock_target, mall_storage_limit) - live_base.available_items(
        client, surface, force,
    ).get(item, 0))
    backlog = backlog_seconds(
        item, outstanding, existing.machine_count if existing else 0,
        spec["machine"],
    )
    promoted_count = promoted_line_machine_count(
        item, demand, existing.machine_count if existing else 0, saturated=saturated,
        backlog=backlog, machine_name=spec["machine"],
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
            f"capacity escalation candidate is a shared {promoted_count}-machine line"
        )
    return _LinePlan(
        existing=existing, spec=spec, production_target=stock_target,
        mall_storage_limit=mall_storage_limit, fill_provider=fill_provider,
        mall_request_multiplier=mall_request_multiplier,
        demand=demand, saturated=saturated, promoted_count=promoted_count,
        promote_to_line=promote_to_line,
        at_size=existing is None or existing.machine_count >= minimum_machines,
        shared_provider=shared_provider,
    )


def _submit_mall_refresh_once(
    signature: tuple[object, ...], action: Callable[[], object],
) -> bool:
    """Apply unchanged mall maintenance once per run and configuration."""
    if signature in _MALL_REFRESH_SIGNATURES:
        return False
    result = action()
    # A paired-request refresh returns False while its topology is incomplete;
    # leave that case retryable. Plan submission helpers return None on success.
    if result is not False:
        _MALL_REFRESH_SIGNATURES.add(signature)
    return True


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
            _submit_mall_refresh_once(
                (
                    "compact_requests", surface, force, item,
                    machine_position, plan.production_target,
                ),
                lambda: _submit(
                    client, bridge, surface, request_plan,
                    f"compact_mall_requests_{item}", emit,
                ),
            )

    if existing and not _mineable(item):
        if getattr(plan, "mall_request_multiplier", None) is not None and not upgrade_bootstrap:
            refresh_multiplier = plan.mall_request_multiplier
            if not (
                item in _STARTUP_MALL_REQUESTER_ITEMS
                and not _metal_starter_transition_complete(
                    client, surface, force,
                )
            ):
                # A transient small batch must not permanently shrink a live
                # cell's input window below throughput size. The finite-batch
                # cap belongs on loan sections; the base section keeps the
                # standard window so the machine cannot starve between bot
                # deliveries (2026-09-04: a clamped circuit window of 6 drove
                # 700s of crumb-fed production for a 200 reserve).
                declared_spec = LINE_RECIPES.get(item, {})
                refresh_multiplier = max(
                    refresh_multiplier,
                    standard_mall_request_multiplier(
                        spec.get("machine", declared_spec.get("machine")),
                        spec.get("craft_time", declared_spec.get("craft_time")),
                    ),
                )
            machine_positions = tuple(existing.machine_positions)
            _submit_mall_refresh_once(
                (
                    "paired_requests", surface, force, item,
                    machine_positions, reference_point,
                    refresh_multiplier,
                ),
                lambda: refresh_paired_mall_requests(
                    client, bridge, surface, force, item,
                    list(machine_positions), reference_point, emit,
                    request_multiplier_override=refresh_multiplier,
                    machine_name=spec["machine"],
                ),
            )
        mall_provider = _paired_mall_provider(
            client, surface, existing.machine_positions,
        )
        if mall_provider is not None and not upgrade_bootstrap:
            shared_output = any(
                mall_slot_uses_shared_provider(
                    client, surface, position, reference_point,
                )
                for position in existing.machine_positions
            )
            shared_or_fill = plan.fill_provider or shared_output
            provider_key = (surface, force, item, mall_provider)
            if not shared_or_fill:
                mall_storage_limit = max(
                    mall_storage_limit,
                    _MALL_PROVIDER_CAPACITY_FLOORS.get(provider_key, 0),
                )
                _MALL_PROVIDER_CAPACITY_FLOORS[provider_key] = mall_storage_limit
                mall_storage_limit = _canonical_mall_provider_limit(
                    surface, force, item, mall_storage_limit,
                )
            if stock_gate_target is not None:
                gate_key = (surface, force, item)
                stock_gate_target = max(
                    stock_gate_target,
                    _MALL_STOCK_GATE_FLOORS.get(gate_key, 0),
                )
                _MALL_STOCK_GATE_FLOORS[gate_key] = stock_gate_target
            capacity = (
                "the full chest"
                if shared_or_fill
                else str(mall_storage_limit)
            )
            limit_plan = generate_mall_provider_limit_update(
                item, mall_provider, mall_storage_limit,
                fill_chest=shared_or_fill,
            )
            limit_plan["surface"], limit_plan["force"] = surface, force

            def refresh_provider_limit() -> None:
                emit(
                    f"  MALL RESERVE: {item} provider at {mall_provider} "
                    f"holds {capacity}"
                )
                _submit(
                    client, bridge, surface, limit_plan,
                    f"mall_provider_limit_{item}", emit,
                )

            _submit_mall_refresh_once(
                (
                    "provider_limit", surface, force, item, mall_provider,
                    mall_storage_limit, shared_or_fill,
                ),
                refresh_provider_limit,
            )
            gate_plan = generate_mall_stock_gate_update(
                item, spec["machine"], list(existing.machine_positions),
                stock_gate_target,
            )
            gate_plan["surface"], gate_plan["force"] = surface, force
            _submit_mall_refresh_once(
                (
                    "stock_gate", surface, force, item,
                    tuple(existing.machine_positions), stock_gate_target,
                ),
                lambda: _submit(
                    client, bridge, surface, gate_plan,
                    f"mall_stock_gate_{item}", emit,
                ),
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
            machine_name=plan.spec["machine"],
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
                machine_name=plan.spec["machine"],
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
# Advanced circuits sit on the electric-furnace unlock chain (GATE WORK):
# drawing them from starter stock without a producer held that gate at 83% for
# twelve passes (live run 15). Steel chests are deliberately excluded: they
# are a low-volume mall capability seed, not a sustained intermediate line.
# Inserters are required by all mall cells; start a compact producer before
# starter reserves are exhausted.
PERSISTENT_INTERMEDIATES = frozenset({
    "iron-stick", "steel-plate", "advanced-circuit", "inserter",
})
MANAGED_INTERMEDIATE_SOURCES: dict[str, Point] = {}


def _transferable_or_available_stock(
    client: RconClient, surface: str, force: str,
) -> dict[str, int]:
    """Transferable construction stock, falling back to available stock if unmocked in test dummies."""
    try:
        return live_base.transferable_items(client, surface, force)
    except AttributeError:
        return live_base.available_items(client, surface, force)

# Logistic chests are capability endpoints, not bootstrap recipes.  Their
# live recipes consume both a steel chest and an advanced circuit; admitting a
# chest cell before those producers exist merely leaves a borrowed assembler
# requesting an impossible recipe.  Keep the order explicit: establish the
# dedicated steel-plate capability first, then the steel-chest source, then
# advance through the oil/plastic chemical ladder for advanced-circuit.  The
# recipe check in
# ``_core_mall_prerequisites`` keeps dry/catalog-less callers compatible.
_CORE_MALL_PREREQUISITE_ORDER = ("steel-chest", "advanced-circuit")
_CORE_MALL_RECIPE_ITEMS = frozenset({
    "passive-provider-chest", "requester-chest",
})
_CORE_MALL_TEMPORARY_PREREQUISITES = frozenset({"steel-chest"})

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
_POST_STARTER_ONE_STACK_ITEMS = frozenset({
    "electronic-circuit", "splitter", "underground-belt",
})
_POST_METAL_STACK_RESERVES = ("electronic-circuit", "splitter")
# Explicit reserve targets where a full stack is pure overstock. Measured
# 2026-09-04 (22:33 run): the splitter full-stack reserve held the single
# rotating assembler from +1538s to +1950s (~412s, including its 200-belt
# prerequisite ladder) before stone could start, yet the only plate project
# that requests splitters asks for 3 (iron PREP DEMAND) and refinery ghosts
# needed 2 more. 12 covers 3-per-refinery across all four plates.
_POST_METAL_RESERVE_TARGETS = {"splitter": 12}
_STARTUP_MALL_REQUESTER_ITEMS = frozenset({"splitter", "underground-belt"})


def _scarce_metal_startup(client: RconClient, surface: str, force: str) -> bool:
    """Whether starter-metal scarcity still justifies tight batch caps.

    User standard 2026-09-03: stock policy is X standing stacks that grow
    past the bill -- once the direct iron/copper replacements are healthy
    and released, pre-core construction keeps its grown reserve and builds
    past need instead of stopping at need-plus-margin. Only the scarce
    opening (starters still carrying the base) keeps the tight batch.
    Dry harnesses without RCON keep the tight behavior.
    """
    if not hasattr(client, "command"):
        return True
    try:
        return not _metal_starter_transition_complete(client, surface, force)
    except Exception:
        return True


def _mall_request_multiplier(
    client: RconClient, surface: str, force: str, item: str, spec: dict,
    output_target: int, *, finite_batch: bool = False,
) -> int | None:
    """Keep startup requesters from claiming more inputs than their batch.

    Permanent cells keep a throughput-sized ten-second input window. Before
    core-mall readiness, every non-anchor output is finite; its requester may
    therefore ask for at most the crafts needed by that bounded batch. This
    prevents a one-item fast-inserter construction need from warehousing ten
    or more regular inserters needed by an already planned refinery.
    """
    standard = standard_mall_request_multiplier(
        spec["machine"], spec["craft_time"],
    )
    if item == "transport-belt":
        multiplier = 30
    elif (
        item in _STARTUP_MALL_REQUESTER_ITEMS
        and not _metal_starter_transition_complete(client, surface, force)
    ):
        multiplier = 2
    else:
        multiplier = standard
    if finite_batch:
        product_amount = max(1, int(spec.get("product_amount", 1)))
        batch_crafts = max(1, math.ceil(output_target / product_amount))
        # Bounded headroom, not an exact cap: the requester keeps pulling a
        # margin past the batch so the machine never idles on a dry requester
        # while bots catch up (user standard 2026-09-03). The cap itself stays.
        return min(
            multiplier,
            max(batch_crafts, math.ceil(
                batch_crafts * (1 + _RATIONED_MALL_SPARE_FRACTION),
            )),
        )
    if item == "transport-belt" or item in _STARTUP_MALL_REQUESTER_ITEMS:
        return multiplier
    return None


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


_BOOTSTRAP_LOAN_PREFERRED = ("copper-cable", "iron-gear-wheel")
_RATIONED_MALL_EXTRA_SPARES = 2
#: Spares built on top of the exact bill so requester/buffer work-in-progress
#: and a concurrent ingredient draw cannot leave transferable stock short.
#: 2026-09-03: stone needed 148 transferable belts while 148 available
#: (requester-held belts counted) left the foundation 89% complete until 5
#: belts were added by hand. User standard: make at least 20% more than
#: needed, rounded up, so a few belts spent on splitters never stall the
#: blueprint they were made for.
_RATIONED_MALL_SPARE_FRACTION = 0.20
#: The two permanent mall anchors: one gear and one cable assembler that no
#: recipe loan may borrow or reconfigure. Second gear/cable cells, circuits,
#: and belts are rotational -- covered by the same guard as duplicates, never
#: as last producers.
_MALL_RECIPE_ANCHORS = {
    "iron-gear-wheel": 1,
    "copper-cable": 1,
}
_PIPE_PERMANENT_DONORS = frozenset(
    RATIONED_MALL_BATCH_ITEMS.difference(CORE_MALL_PRODUCERS, {"pipe"})
)
_DYNAMIC_BELT_BORROWERS = frozenset({"copper-cable"})
_BOOTSTRAP_LOAN_PROGRESS_REVISION = 0
#: Consecutive gate refreshes per loan step without craft progress. A
#: bill-frozen gate refreshes and continues; only a step that ignores
#: repeated refreshes raises mall_loan_gate_mismatch.
_LOAN_GATE_REFRESH_ATTEMPTS: dict[tuple[str, str, int], int] = {}
_LOAN_GATE_REFRESH_LIMIT = 3
_BOOTSTRAP_SHARED_PROVIDER_ITEMS: set[str] = set()
_MALL_REFRESH_SIGNATURES: set[tuple[object, ...]] = set()
_MALL_PROVIDER_CAPACITY_FLOORS: dict[tuple[str, str, str, Point], int] = {}
_MALL_STOCK_GATE_FLOORS: dict[tuple[str, str, str], int] = {}
#: Canonical provider count per item: every live cell making the same item
#: carries the same limit (user standard 2026-09-04). Twins diverged whenever
#: each refreshed against its own transient demand -- or a loan restore reset
#: one twin to a count of 1 while its sibling held hundreds.
_MALL_ITEM_PROVIDER_LIMITS: dict[tuple[str, str, str], int] = {}


def _canonical_mall_provider_limit(
    surface: str, force: str, item: str, proposed: int,
) -> int:
    """Fold `proposed` into the item's canonical provider limit and return it.

    Monotonic like the capacity/stock-gate floors: a smaller incidental demand
    cannot shrink a reserve already being filled, and every twin converges on
    the same count the next time its cell refreshes.
    """
    key = (surface, force, item)
    canonical = max(int(proposed), _MALL_ITEM_PROVIDER_LIMITS.get(key, 0))
    _MALL_ITEM_PROVIDER_LIMITS[key] = canonical
    return canonical
_PARALLEL_BOOTSTRAP_RESERVE_ITEMS = frozenset({
    "electronic-circuit", "splitter", "transport-belt",
})
_PARALLEL_BOOTSTRAP_BACKLOG_SECONDS = 60.0


def _copper_cable_consumers_active(
    client: RconClient, surface: str, force: str,
) -> bool:
    """Whether any item requiring copper cable is currently being produced or prepped."""
    if not hasattr(client, "command"):
        return False
    for recipe, spec in LINE_RECIPES.items():
        if "copper-cable" not in spec.get("ingredients", ()):
            continue
        try:
            line = live_base.find_line(
                client, surface, force, recipe, str(spec["machine"]),
            )
            if line is not None and line.machine_count > 0:
                return True
        except Exception:
            pass
        try:
            from orchestrator.mall_bootstrap import active_bootstrap_loans
            for loan in active_bootstrap_loans(client, surface, force):
                if loan.target_item == recipe or loan.current_recipe == recipe:
                    return True
        except Exception:
            pass
    return False


def _is_pre_core_temporary_mall_item(
    client: RconClient, surface: str, force: str, item: str,
) -> bool:
    """Whether a compact mall output must remain a bounded batch for now.

    Gear and cable each keep one permanent anchor cell. Every other recipe in
    ``RATIONED_MALL_BATCH_ITEMS`` -- including the second gear/cable cells,
    circuits, and belts -- is rotational until the five core mall producers
    are independently working; persistent intermediates keep their
    dedicated-source path instead of being turned into rotating loans.
    """
    return bool(
        item in RATIONED_MALL_BATCH_ITEMS
        and item not in _MALL_RECIPE_ANCHORS
        and item not in PERSISTENT_INTERMEDIATES
        and not _core_mall_ready(client, surface, force)
    )


def _pipe_is_temporary_batch(
    client: RconClient, surface: str, force: str,
) -> bool:
    """Keep pipe in the rotating mall until both bootstrap gates release."""
    return not _all_plate_pioneers_released() or not _core_mall_ready(
        client, surface, force,
    )


def _recipe_depends_on_fast_transport_belt(item: str) -> bool:
    """Whether a mall recipe would bypass the fast-belt capability gate."""
    pending = [item]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == "fast-transport-belt":
            return True
        if current in visited:
            continue
        visited.add(current)
        spec = LINE_RECIPES.get(current)
        if spec is None or spec.get("fluid_ingredients"):
            continue
        pending.extend(str(ingredient) for ingredient in spec.get("ingredients", ()))
    return False


def _fast_transport_belt_gate_open(
    client: RconClient, surface: str, force: str,
) -> bool:
    """Mirror the fast-belt gate before a recipe loan can configure a cell."""
    if not _electric_furnace_producer_started(client, surface, force):
        return False
    iron_furnaces, iron_drills = _iron_capacity_for_fast_belts(
        client, surface, force,
    )
    return min(iron_furnaces, iron_drills) >= _FAST_BELT_IRON_CAPACITY


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


def _bootstrap_loan_products_finished(
    client: RconClient, surface: str, loan: MallBootstrapLoan,
) -> int | None:
    """Read the borrowed assembler's monotonic craft count."""
    counter = live_base.progress_counters(
        client, surface, [loan.machine_position],
    ).get(loan.machine_position)
    return int(counter // 1000) if counter is not None else None


def _bootstrap_loan_minimum_crafts(
    loan: MallBootstrapLoan, step, actual: Mapping[str, int],
) -> int:
    """Crafts needed for the bill, excluding optional spare production."""
    if step.recipe != loan.target_item:
        return step.crafts
    missing = max(0, loan.target_count - int(actual.get(loan.target_item, 0)))
    product_amount = max(
        1, math.floor(float(LINE_RECIPES[step.recipe].get("product_amount", 1))),
    )
    return math.ceil(missing / product_amount)


def _bootstrap_loan_minimum_fulfilled(
    loan: MallBootstrapLoan, actual: Mapping[str, int],
    products_finished: int | None,
) -> bool:
    if int(actual.get(loan.target_item, 0)) >= loan.target_count:
        return True
    if (
        loan.production_target > loan.target_count
        and loan.step_minimum_crafts == 0
    ):
        # V3 spare-phase metadata persists that the blocking bill completed,
        # even while the borrowed cell is making a prerequisite for extras.
        return True
    return bool(
        loan.step_recipe == loan.target_item
        and loan.step_baseline_finished is not None
        and loan.step_minimum_crafts is not None
        and products_finished is not None
        and (
            products_finished - loan.step_baseline_finished
            >= loan.step_minimum_crafts
        )
    )


def _bootstrap_loan_credited_stock(
    loan: MallBootstrapLoan, actual: Mapping[str, int], usable: Mapping[str, int],
) -> tuple[dict[str, int], dict[str, int]]:
    """Apply durable prerequisite credits without inventing final products."""
    credited_actual = dict(actual)
    credited_usable = dict(usable)
    for recipe, target_count in loan.completed_step_targets:
        if recipe == loan.target_item:
            continue
        credited_actual[recipe] = max(
            int(credited_actual.get(recipe, 0)), target_count,
        )
        credited_usable[recipe] = max(
            int(credited_usable.get(recipe, 0)), target_count,
        )
    return credited_actual, credited_usable


def _bootstrap_loan_persisted_step(
    loan: MallBootstrapLoan, actual: Mapping[str, int],
) -> MallBootstrapStep | None:
    """Keep an unfinished configured step stable across stock churn."""
    if (
        loan.step_recipe is None
        or loan.step_required_crafts is None
    ):
        return None
    product_amount = max(
        1, math.floor(float(
            LINE_RECIPES.get(loan.step_recipe, {}).get("product_amount", 1)
        )),
    )
    target_count = loan.step_target_count
    if target_count is None:
        target_count = (
            int(actual.get(loan.step_recipe, 0))
            + loan.step_required_crafts * product_amount
        )
    return MallBootstrapStep(
        loan.step_recipe, target_count, loan.step_required_crafts,
    )


def _restore_bootstrap_loan(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    loan: MallBootstrapLoan, emit: Callable[[str], None], *, reason: str,
    reference_point: Point | None = None,
) -> None:
    """Restore the borrowed cell before another dependency may claim it."""
    try:
        shared_provider = mall_slot_uses_shared_provider(
            client, surface, loan.machine_position,
            reference_point or (3.0, -1.0),
        )
    except Exception:
        # Unknown geometry must not strand the cell behind a count bar:
        # an open shared chest never blocks, a wrong guess might.
        shared_provider = True
    plan = restore_bootstrap_loan_plan(
        loan,
        provider_stock_target=_MALL_ITEM_PROVIDER_LIMITS.get(
            (surface, force, loan.original_recipe),
        ),
        provider_fill_chest=shared_provider,
    )
    plan["surface"], plan["force"] = surface, force
    _submit(
        client, bridge, surface, plan,
        f"restore_bootstrap_loan_{loan.target_item}", emit,
    )
    _MALL_REFRESH_SIGNATURES.clear()
    emit(
        f"  MALL BOOTSTRAP LOAN RESTORED: {loan.original_recipe} at "
        f"{loan.machine_position}; {reason}"
    )


def _binding_loan_shields_preempt(
    client: RconClient, surface: str, force: str,
    loan: MallBootstrapLoan, preempt_for: str,
) -> bool:
    """Whether the active loan keeps its cell against this preemptor.

    A loan still short of its blocking bill on foundation-binding work is not
    time-sliced away for a non-binding batch (2026-09-03: drills at 2/8 with
    mine ghosts pending yielded to circuits-200 stockpiling). Bill-met loans
    and binding-vs-binding contention preempt as before. Dry harnesses
    default to preemptible.
    """
    if preempt_for in _BLOCKING_MALL_ITEMS:
        return False
    if loan.target_item not in _BLOCKING_MALL_ITEMS:
        return False
    if not hasattr(client, "command"):
        return False
    try:
        have = live_base.transferable_items(client, surface, force).get(
            loan.target_item, 0,
        )
    except Exception:
        return False
    return int(have) < loan.target_count


def _emit_loan_cell_telemetry(
    client: RconClient, surface: str, force: str,
    loan: MallBootstrapLoan, step, actual: Mapping[str, int],
    products_finished: int | None, emit: Callable[[str], None], *,
    reference_point: Point | None = None,
) -> None:
    """One read-only diagnostic line for a blocked loan cell. Zero behavior
    change: every probe is guarded, nothing is submitted, and any failure
    emits a skip marker instead of raising.

    A bare LOAN WAIT cannot distinguish the four stall shapes seen live
    (2026-09-06: which ingredient is missing at the stalled requester,
    whether the pool has a free slot, what the other active loans hold, and
    whether the bill or the spare ceiling drives restore). This names all
    four from explicit live facts on every wait pass.
    """
    try:
        spec = LINE_RECIPES.get(step.recipe, {})
        ingredients = [
            (str(item), float(amount))
            for item, amount in zip(
                spec.get("ingredients", ()), spec.get("amounts", ()),
                strict=True,
            )
        ]
    except Exception:
        ingredients = []
    try:
        contents = live_base.chest_contents(
            client, surface, loan.requester_position,
        )
    except Exception:
        contents = {}
    try:
        committed = (
            mall_slot_count(client, surface, reference_point)
            if reference_point is not None else None
        )
    except Exception:
        committed = None
    try:
        loans = active_bootstrap_loans(client, surface, force)
    except Exception:
        loans = ()
    try:
        missing = "-"
        scarcest = 0.0
        for item, amount in ingredients:
            short = (
                float(amount)
                - float(contents.get(item, 0))
                - float(actual.get(item, 0))
            )
            if short > scarcest:
                scarcest = short
                missing = item
        baseline = loan.step_baseline_finished or 0
        crafts_done = (
            max(0, int(products_finished) - int(baseline))
            if products_finished is not None else None
        )
        need = loan.step_required_crafts
        requester = ",".join(
            f"{item}={contents.get(item, 0)}" for item, _ in ingredients
        ) or "-"
        net = ",".join(
            f"{item}={int(actual.get(item, 0))}" for item, _ in ingredients
        ) or "-"
        pool = (
            f"{max(0, BOOTSTRAP_MALL_SLOT_TARGET - int(committed))}"
            f"/{BOOTSTRAP_MALL_SLOT_TARGET}"
            if committed is not None else "?"
        )
        table = ",".join(
            f"{holder.target_item}:{holder.step_recipe or holder.current_recipe}"
            f"@({holder.machine_position[0]:.1f},{holder.machine_position[1]:.1f})"
            for holder in loans
        ) or "-"
        emit(
            f"  LOAN CELL TELEMETRY: {step.recipe} at {loan.machine_position} "
            f"bill {int(actual.get(loan.target_item, 0))}/{loan.target_count}"
            f"+{loan.production_target} "
            f"crafts {crafts_done if crafts_done is not None else '?'}"
            f"/{need if need is not None else '?'} "
            f"| requester {requester} net {net} missing {missing} "
            f"| pool {pool} free | loans {table}"
        )
    except Exception as error:
        emit(f"  LOAN CELL TELEMETRY skipped: {error}")


def _submit_bootstrap_loan(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    loan: MallBootstrapLoan, emit: Callable[[str], None], *,
    preempt_for: str | None = None, reference_point: Point | None = None,
) -> str:
    global _BOOTSTRAP_LOAN_PROGRESS_REVISION
    live_loan_group = loan.group
    actual, usable = _bootstrap_loan_stock(
        client, surface, force, loan.target_item,
    )
    # Finished goods locked in requester/buffer WIP are already consumed, not
    # stock. Complete the loan's own target against spendable transferable
    # units so a drained batch keeps producing spares on its cell instead of
    # restoring and re-borrowing every pass (2026-09-03 belt churn: 150
    # available hit the 147 spare ceiling while transferable sat at 138, and
    # the 12-pass guard tripped on the reconfigure cycle). Prerequisite
    # ingredients keep their available-based accounting: stock already
    # delivered to this loan's own requester is legitimately in flight.
    # Dry harnesses without RCON keep legacy behavior.
    spendable: dict[str, int] | None = None
    if hasattr(client, "command"):
        try:
            spendable = live_base.transferable_items(client, surface, force)
        except Exception:
            spendable = None
    if spendable is not None:
        # A missing key means zero spendable units: transferable reports omit
        # empty entries, while the available-based count may hold WIP.
        actual = {
            **actual,
            loan.target_item: int(spendable.get(loan.target_item, 0)),
        }
    target_spendable: int | None = (
        int(spendable.get(loan.target_item, 0))
        if spendable is not None else None
    )
    products_finished = (
        _bootstrap_loan_products_finished(client, surface, loan)
        if loan.step_recipe is not None else None
    )
    minimum_fulfilled = _bootstrap_loan_minimum_fulfilled(
        loan, actual, products_finished,
    )
    completed_prerequisite = bool(
        loan.step_recipe is not None
        and loan.step_recipe != loan.target_item
        and (
            (
                loan.step_baseline_finished is not None
                and loan.step_required_crafts is not None
                and products_finished is not None
                and products_finished - loan.step_baseline_finished
                >= loan.step_required_crafts
            )
            or (
                loan.step_target_count is not None
                and int(actual.get(loan.step_recipe, 0)) >= loan.step_target_count
            )
        )
    )
    if completed_prerequisite:
        persisted = _bootstrap_loan_persisted_step(loan, actual)
        assert persisted is not None
        loan = loan.credit_completed_step(
            persisted.recipe, persisted.target_count,
        )
        crafts_done = (
            products_finished - loan.step_baseline_finished
            if products_finished is not None and loan.step_baseline_finished is not None
            else 0
        )
        emit(
            f"  MALL BOOTSTRAP PREREQUISITE FULFILLED: {persisted.recipe} "
            f"target {persisted.target_count} reached ({crafts_done} crafted); "
            "advancing the durable loan"
        )
    planning_actual, planning_usable = _bootstrap_loan_credited_stock(
        loan, actual, usable,
    )
    planning_target = (
        loan.production_target if minimum_fulfilled else loan.target_count
    )
    step = next_bootstrap_step(
        loan.target_item, planning_target, planning_usable, planning_actual,
    )
    if not completed_prerequisite and loan.step_recipe is not None:
        persisted = _bootstrap_loan_persisted_step(loan, actual)
        if (
            persisted is not None
            and step is not None
            and step.recipe != loan.step_recipe
        ):
            step = persisted
    if step is not None and products_finished is None:
        products_finished = _bootstrap_loan_products_finished(
            client, surface, loan,
        )
    preempted = bool(
        preempt_for is not None
        and preempt_for != loan.target_item
        and minimum_fulfilled
        and not _binding_loan_shields_preempt(
            client, surface, force, loan, preempt_for,
        )
    )
    if preempted:
        emit(
            f"  MALL BOOTSTRAP LOAN PREEMPT: {loan.target_item} fulfilled its "
            f"required {loan.target_count}; releasing spare production through "
            f"{loan.production_target} for {preempt_for}"
        )
        step = None
    starting_spare_phase = bool(
        step is not None
        and minimum_fulfilled
        and loan.production_target > loan.target_count
        and loan.step_recipe == step.recipe
        and loan.step_required_crafts == loan.step_minimum_crafts
    )
    if (
        step is not None
        and not starting_spare_phase
        and step.recipe == loan.target_item
        and loan.step_recipe == step.recipe
        and loan.step_baseline_finished is not None
        and loan.step_required_crafts is not None
        and products_finished is not None
        and (
            products_finished - loan.step_baseline_finished
            >= loan.step_required_crafts
        )
        # Releasing the cell on craft evidence alone restores and re-borrows
        # every pass while a live consumer drains the batch (2026-09-03 belt
        # churn). Only release when the blocking bill is spendable; otherwise
        # the recomputed step below keeps producing spares on this same cell
        # with no reconfiguration. Dry harnesses keep legacy behavior.
        and (target_spendable is None or target_spendable >= loan.target_count)
    ):
        # Construction bots may consume the finite batch as quickly as it is
        # made. The monotonic craft count is durable fulfillment evidence even
        # when the requested idle stock can never accumulate simultaneously.
        emit(
            f"  MALL BOOTSTRAP LOAN FULFILLED: {loan.target_item} produced "
            f"{products_finished - loan.step_baseline_finished} craft(s); "
            "construction may already have consumed them"
        )
        step = None
    if step is not None:
        predecessor = _missing_chemical_ladder_predecessor(
            client, surface, force, step.recipe,
        )
        if predecessor is not None:
            _restore_bootstrap_loan(
                client, bridge, surface, force, loan, emit,
                reference_point=reference_point,
                reason=(
                    f"chemical handoff from {step.recipe} to missing "
                    f"{predecessor}"
                ),
            )
            emit(
                f"  CHEMICAL LADDER HANDOFF: restored {loan.original_recipe} "
                f"before establishing {predecessor}; {step.recipe} is not "
                "admitted on zero upstream production"
            )
            # Restoring the borrowed assembler is a live configuration
            # transition. Re-survey it on the next controller pass before
            # borrowing the same cell for the predecessor; otherwise the
            # still-observable old loan recursively services itself.
            raise ProductionPrerequisiteDeferred(
                f"chemical ladder handoff restored {step.recipe} before "
                f"establishing {predecessor}; retrying after re-observation",
                code="chemical_capability_handoff",
                state="supply_wait",
                details={"target": step.recipe, "rung": predecessor},
            )
    if step is None:
        if loan.target_item in CORE_MALL_PRODUCERS:
            # A core producer is allowed to reclaim a completed temporary
            # batch, BUT ONLY IF the host recipe has surplus capacity above
            # its BASELINE_MACHINES quota (e.g. 2 for copper-cable, 2 for
            # iron-gear-wheel). Sacrificing a baseline machine permanently
            # starves downstream lines.
            host_count = 0
            if hasattr(client, "command"):
                try:
                    host_line = live_base.find_line(
                        client, surface, force, loan.original_recipe,
                        LINE_RECIPES.get(loan.original_recipe, {}).get("machine", "assembling-machine-1"),
                    )
                    host_count = host_line.machine_count if host_line is not None else 0
                except Exception:
                    host_count = 99
            else:
                host_count = 99
            baseline_needed = BASELINE_MACHINES.get(loan.original_recipe, 0)
            if (
                loan.original_recipe == "copper-cable"
                and _copper_cable_consumers_active(client, surface, force)
            ):
                baseline_needed = max(baseline_needed, 2)
            if host_count > baseline_needed:
                promotion_target = max(
                    1,
                    loan.production_target,
                    int(actual.get(loan.target_item, 0)) + 1,
                )
                plan = promote_bootstrap_loan_plan(
                    loan,
                    stock_target=_canonical_mall_provider_limit(
                        surface, force, loan.target_item, promotion_target,
                    ),
                    clear_original_groups=True,
                )
                plan["surface"], plan["force"] = surface, force
                _submit(
                    client, bridge, surface, plan,
                    f"promote_bootstrap_loan_{loan.target_item}", emit,
                )
                _MALL_REFRESH_SIGNATURES.clear()
                _BOOTSTRAP_SHARED_PROVIDER_ITEMS.discard(loan.target_item)
                emit(
                    f"  CORE MALL PERMANENT: converted the borrowed "
                    f"{loan.original_recipe} demand slot at {loan.machine_position} "
                    f"into {loan.target_item}; reused its existing assembler "
                    "without funding another compact cell"
                )
                return (
                    f"converted borrowed {loan.original_recipe} producer into the "
                    f"permanent {loan.target_item} mall"
                )
            _restore_bootstrap_loan(
                client, bridge, surface, force, loan, emit,
                reference_point=reference_point,
                reason=(
                    f"{loan.target_item} fulfilled its required {loan.target_count}; "
                    f"preserving {loan.original_recipe} baseline machine quota "
                    f"({host_count}/{baseline_needed})"
                ),
            )
            return (
                f"restored borrowed {loan.original_recipe} producer to protect its "
                f"baseline quota"
            )
        if (
            loan.target_item == "pipe"
            and loan.original_recipe in _PIPE_PERMANENT_DONORS
            and not _pipe_is_temporary_batch(client, surface, force)
        ):
            plan = promote_bootstrap_loan_plan(
                loan,
                stock_target=_canonical_mall_provider_limit(
                    surface, force, loan.target_item,
                    loan.production_target,
                ),
            )
            plan["surface"], plan["force"] = surface, force
            _submit(
                client, bridge, surface, plan,
                "promote_bootstrap_loan_pipe", emit,
            )
            _MALL_REFRESH_SIGNATURES.clear()
            _BOOTSTRAP_SHARED_PROVIDER_ITEMS.add("pipe")
            emit(
                "  PIPE MALL PERMANENT: plate pioneers are released and the "
                "core mall is self-sufficient; "
                f"converted the stocked {loan.original_recipe} demand slot at "
                f"{loan.machine_position} without funding another cell"
            )
            return (
                f"converted borrowed {loan.original_recipe} producer into the "
                "permanent pipe mall"
            )
        _restore_bootstrap_loan(
            client, bridge, surface, force, loan, emit,
            reference_point=reference_point,
            reason=(
                f"{loan.target_item} required {loan.target_count}, "
                f"spare ceiling {loan.production_target}"
            ),
        )
        return (
            f"restored borrowed {loan.original_recipe} producer after "
            + ("spare production was preempted" if preempted else "seed completion")
        )

    # The machine gate freezes at whatever step target was live at configure
    # time, while the loan's ambition advances to the spare ceiling without
    # reconfiguring (2026-09-03 drill stall: gate stayed at the bill of 6
    # while the step grew to 8, so the machine disabled at 6 and the loan
    # died on gate_mismatch). A drifted gate is configuration, not progress:
    # resubmit so the gate follows the step.
    gate_refresh_needed = bool(
        step is not None
        and loan.step_target_count is not None
        and step.target_count != loan.step_target_count
    )
    if (
        loan.current_recipe != step.recipe
        or loan.step_recipe != step.recipe
        or loan.step_baseline_finished is None
        or loan.step_required_crafts is None
        or starting_spare_phase
        or completed_prerequisite
        or gate_refresh_needed
    ):
        if products_finished is None:
            raise StuckError(
                f"bootstrap loan target at {loan.machine_position} has no "
                "craft-progress counter",
                code="mall_loan_machine_missing", classification="bug",
                state="failed",
                details={
                    "target_item": loan.target_item,
                    "machine_position": list(loan.machine_position),
                },
            )
        plan = bootstrap_loan_plan(
            loan, step, baseline_finished=products_finished,
            minimum_crafts=(
                0 if minimum_fulfilled
                else _bootstrap_loan_minimum_crafts(loan, step, actual)
            ),
            previous_group=(
                live_loan_group if live_loan_group != loan.group else None
            ),
        )
        plan["surface"], plan["force"] = surface, force
        _submit(
            client, bridge, surface, plan,
            f"bootstrap_loan_{loan.target_item}", emit,
        )
        _MALL_REFRESH_SIGNATURES.clear()
        _BOOTSTRAP_LOAN_PROGRESS_REVISION += 1
        emit(
            f"  MALL BOOTSTRAP LOAN: borrowed {loan.original_recipe} at "
            f"{loan.machine_position} to make {step.recipe} through stock "
            f"{step.target_count}; requester now asks for "
            f"{max(1, math.ceil(step.crafts * (1 + _LOAN_REQUEST_HEADROOM_FRACTION)))} "
            f"craft(s) of ingredients "
            f"(step {step.crafts} + headroom); bill minimum is "
            f"{loan.target_count} {loan.target_item}"
        )
    else:
        emit(
            f"  MALL BOOTSTRAP LOAN WAIT: {step.recipe} at {loan.machine_position} "
            f"is making temporary stock through {step.target_count}"
        )
        _emit_loan_cell_telemetry(
            client, surface, force, loan, step, actual, products_finished,
            emit, reference_point=reference_point,
        )
        _deliver_cell_ingredients(
            client, bridge, surface, force, step.recipe,
            loan.machine_position, emit,
            loan.requester_position,
        )
        consume_wait(f"bootstrap_loan_{loan.target_item}")
        time.sleep(_BOOTSTRAP_LOAN_POLL_SECONDS)
        after = _bootstrap_loan_products_finished(client, surface, loan)
        if after is not None and products_finished is not None and after > products_finished:
            _BOOTSTRAP_LOAN_PROGRESS_REVISION += 1
            emit(
                f"  MALL BOOTSTRAP LOAN PROGRESS: {step.recipe} craft count "
                f"advanced {products_finished} -> {after}"
            )
        elif live_base.entity_status_name(
            client, surface, loan.machine_position,
        ) == "disabled_by_control_behavior":
            # Re-read after the bounded poll. The machine may have reached its
            # gate during the sleep, and prerequisite steps gate their own
            # recipe rather than the loan's final target item.
            refreshed_actual, _ = _bootstrap_loan_stock(
                client, surface, force, loan.target_item,
            )
            available = refreshed_actual.get(step.recipe, 0)
            if available < step.target_count:
                # Status and stock are sampled seconds apart while bots drain
                # the batch, so a stale disabled reading must not end the run
                # on its own: re-read once, and only fail a still-disabled
                # machine that is still short.
                try:
                    still_disabled = (
                        live_base.entity_status_name(
                            client, surface, loan.machine_position,
                        ) == "disabled_by_control_behavior"
                    )
                except Exception:
                    still_disabled = True
                if not still_disabled:
                    emit(
                        f"  MALL BOOTSTRAP LOAN RESUMED: {step.recipe} at "
                        f"{loan.machine_position} re-enabled after stock "
                        f"moved; continuing toward {step.target_count}"
                    )
                    return (
                        f"borrowed {loan.original_recipe} cell is producing "
                        f"temporary {step.recipe} for the {loan.target_item} seed"
                    )
                # A bill-frozen gate is configuration drift, not a bug: the
                # step target advanced but the machine still carries the old
                # threshold, so it sits disabled below what it should make.
                # Refresh the gate to the current step and continue; only a
                # step that ignores repeated refreshes is genuinely stuck
                # (2026-09-04: a drill loan's circuit step sat disabled at
                # 11/18 and ended the run).
                refresh_key = (
                    live_loan_group, step.recipe, step.target_count,
                )
                attempts = _LOAN_GATE_REFRESH_ATTEMPTS.get(refresh_key, 0) + 1
                _LOAN_GATE_REFRESH_ATTEMPTS[refresh_key] = attempts
                if attempts > _LOAN_GATE_REFRESH_LIMIT:
                    raise StuckError(
                        f"bootstrap loan step {step.recipe} is disabled below "
                        f"its stock target {step.target_count} after "
                        f"{_LOAN_GATE_REFRESH_LIMIT} gate refreshes",
                        code="mall_loan_gate_mismatch", classification="bug",
                        state="supply_wait",
                        details={
                            "target_item": loan.target_item,
                            "step_target_count": step.target_count,
                            "available_stock": available,
                            "machine_position": list(loan.machine_position),
                            "step_recipe": step.recipe,
                        },
                    )
                plan = bootstrap_loan_plan(
                    loan, step,
                    baseline_finished=(
                        after if after is not None
                        else (products_finished or 0)
                    ),
                    minimum_crafts=(
                        0 if minimum_fulfilled
                        else _bootstrap_loan_minimum_crafts(loan, step, actual)
                    ),
                    previous_group=(
                        live_loan_group
                        if live_loan_group != loan.group else None
                    ),
                )
                plan["surface"], plan["force"] = surface, force
                _submit(
                    client, bridge, surface, plan,
                    f"bootstrap_loan_{loan.target_item}", emit,
                )
                _MALL_REFRESH_SIGNATURES.clear()
                _BOOTSTRAP_LOAN_PROGRESS_REVISION += 1
                emit(
                    f"  MALL BOOTSTRAP LOAN GATE REFRESH: {step.recipe} at "
                    f"{loan.machine_position} was disabled below "
                    f"{step.target_count} (attempt {attempts}); re-applied "
                    "the gate and continuing"
                )
                return (
                    f"borrowed {loan.original_recipe} cell is producing "
                    f"temporary {step.recipe} for the {loan.target_item} seed"
                )
    return (
        f"borrowed {loan.original_recipe} cell is producing temporary "
        f"{step.recipe} for the {loan.target_item} seed"
    )


def _service_bootstrap_loan(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    target_item: str, reference_point: Point,
    emit: Callable[[str], None],
) -> str | None:
    loans = active_bootstrap_loans(client, surface, force)
    if not loans:
        return None
    for loan in loans:
        if loan.target_item == target_item:
            return _submit_bootstrap_loan(
                client, bridge, surface, force, loan, emit,
                reference_point=reference_point,
            )
    if len(loans) == 1:
        return _submit_bootstrap_loan(
            client, bridge, surface, force, loans[0], emit,
            preempt_for=target_item, reference_point=reference_point,
        )
    # Several batches run on their own cells; each advances on its own pass.
    return None


def _loan_cell_origin(loan: MallBootstrapLoan) -> tuple[float, float]:
    """Cell origin hosting a loan, inverse of the requester offset."""
    return (
        loan.requester_position[0] - 4.5,
        loan.requester_position[1] - 1.5,
    )


def _borrow_free_mall_cell(
    client: RconClient, surface: str, force: str, target_item: str,
    reference_point: Point, *, allowed_original_recipes: frozenset[str] | None,
    allow_shared_provider: bool, require_stocked_original: bool,
    excluded_origins: set[tuple[float, float]],
) -> tuple[str, Point, tuple[int, int], str, str] | None:
    """Find one borrowable cell outside every active loan's own cell.

    Each planner-owned cell hosts at most one loan: paired halves share one
    passive provider, so a second loan on the same origin would mix another
    product into that chest and make safe restoration ambiguous.
    """
    stock = live_base.available_items(client, surface, force)
    recipes = [
        recipe for recipe, spec in LINE_RECIPES.items()
        if recipe != target_item
        and (
            allowed_original_recipes is None
            or recipe in allowed_original_recipes
        )
        and spec.get("set_recipe", True)
        and spec.get("machine") in live_base.ASSEMBLER_TIERS
        and not spec.get("fluid_ingredients")
    ]
    recipes.sort(key=lambda recipe: (
        recipe not in _BOOTSTRAP_LOAN_PREFERRED,
        -int(stock.get(recipe, 0)),
        recipe,
    ))
    candidates: list[tuple[int, int, int, str, Point, tuple[int, int], str]] = []
    for original_recipe in recipes:
        if original_recipe in CORE_MALL_PRODUCERS:
            # Once a core-mall recipe owns a cell, that cell is reserved for
            # self-sufficiency.  Borrowing it can convert the only permanent
            # assembler-2/fast-inserter/etc. producer back into an unrelated
            # temporary recipe and recreate the bootstrap deadlock.
            continue
        spec = LINE_RECIPES.get(original_recipe)
        if spec is None:
            continue
        line = live_base.find_line(
            client, surface, force, original_recipe, str(spec["machine"]),
            # Anchor protection counts real producers only: an unbuilt ghost
            # is not capacity that remains after borrowing the working cell.
            include_ghosts=False,
        )
        if line is None:
            continue
        if require_stocked_original and stock.get(original_recipe, 0) < 1:
            continue
        anchor_minimum = _MALL_RECIPE_ANCHORS.get(original_recipe, 0)
        if line.machine_count <= anchor_minimum:
            # Gear and cable are the bootstrap mall's feedstock anchors. A
            # recipe loan may use a duplicate, never the last producer.
            # Counts are real machines only: borrowing the sole working cell
            # while an unbuilt ghost poses as its duplicate strands the
            # recipe (2026-09-04 cable collapse).
            continue
        for machine_position in reversed(line.machine_positions):
            located = locate_mall_cell(machine_position, reference_point)
            if located is None:
                continue
            origin, side = located
            if origin in excluded_origins:
                # This cell already hosts a concurrent loan on its shared
                # provider; borrowing its other half would mix products.
                continue
            if not allow_shared_provider and mall_slot_uses_shared_provider(
                client, surface, machine_position, reference_point,
            ):
                # The companion recipe still owns this provider. Borrowing
                # either half mixes a third product into the same chest, so
                # dedicated cells rank first -- but a shared cell still beats
                # no cell at all. The loan tag scopes restoration to its own
                # section, and stock surveys (not chest purity) decide when
                # the batch is complete, so rotation through the paired pool
                # stays possible (2026-09-04: every paired half shares, and
                # skipping all of them stalled a run with 7 live cells).
                shared_rank = 1
            else:
                shared_rank = 0
            requester_position = (origin[0] + 4.5, origin[1] + 1.5)
            machine = live_base.entity_at(client, surface, machine_position)
            requester = live_base.entity_at(client, surface, requester_position)
            if (
                not machine or machine["name"] not in live_base.ASSEMBLER_TIERS
                or not requester or requester["name"] != "requester-chest"
            ):
                continue
            provider_position = (
                origin[0] + 4.5,
                origin[1] + 1.5 + (-1.0 if side == "left" else 1.0),
            )
            provider = live_base.entity_at(client, surface, provider_position)
            if not provider or provider["name"] != "passive-provider-chest":
                # The loan configures its provider chest in place. A half
                # whose provider slot is empty (e.g. the right half of a
                # shared-provider cell) would die later at submit time with
                # configure_target_missing (2026-09-04: ended a run), so
                # only complete halves are borrowable.
                continue
            # Prefer an actually spare duplicate. Non-anchor recipes may still
            # lend a stocked sole producer; anchor recipes were filtered above.
            spare_rank = 0 if line.machine_count >= 2 else 1
            stock_rank = 0 if stock.get(original_recipe, 0) > 0 else 1
            candidates.append((
                shared_rank, spare_rank, stock_rank, original_recipe,
                machine_position, origin, side,
            ))
    if not candidates:
        return None
    (_shared, _spare, _stocked, original_recipe, machine_position, origin,
     side) = min(candidates)
    machine_entity = live_base.entity_at(client, surface, machine_position)
    machine_name = (
        str(machine_entity["name"])
        if machine_entity and machine_entity.get("name") in live_base.ASSEMBLER_TIERS
        else "assembling-machine-1"
    )
    return (original_recipe, machine_position, origin, side, machine_name)


def _loan_blocked_inputs(
    client: RconClient, surface: str, force: str, loan: MallBootstrapLoan,
) -> list[str]:
    """Step inputs the loan cannot receive: no live producer and no
    spendable stock. Mirrors the starved-ingredient scan for one loan."""
    recipe = loan.step_recipe or loan.current_recipe or loan.target_item
    spec = LINE_RECIPES.get(recipe)
    if spec is None:
        return []
    try:
        stock = _transferable_or_available_stock(client, surface, force)
    except Exception:
        return []
    try:
        remaining = max(0, loan.target_count - int(stock.get(loan.target_item, 0)))
        product_amount = max(1, math.floor(float(
            LINE_RECIPES.get(loan.target_item, {}).get("product_amount", 1),
        )))
        crafts = max(1, math.ceil(remaining / product_amount))
    except Exception:
        return []
    blocked = []
    for ingredient, amount in zip(
        spec.get("ingredients", ()), spec.get("amounts", ()), strict=True,
    ):
        ingredient = str(ingredient)
        if ingredient == loan.target_item:
            continue
        try:
            have = int(stock.get(ingredient, 0))
        except Exception:
            continue
        if have >= math.ceil(float(amount) * crafts):
            continue
        if LINE_RECIPES.get(ingredient) is None:
            continue
        try:
            if _production_started(client, surface, force, ingredient):
                continue
        except Exception:
            continue
        blocked.append(ingredient)
    return blocked


def _recipe_inputs_flowing(
    client: RconClient, surface: str, force: str, item: str,
) -> bool:
    """Whether every ingredient of `item` has a live producer or spendable
    stock -- a batch for it could run right now."""
    spec = LINE_RECIPES.get(item)
    if spec is None:
        return False
    try:
        stock = _transferable_or_available_stock(client, surface, force)
    except Exception:
        return False
    for ingredient in spec.get("ingredients", ()):
        ingredient = str(ingredient)
        try:
            if int(stock.get(ingredient, 0)) > 0:
                continue
        except Exception:
            pass
        if LINE_RECIPES.get(ingredient) is None:
            return False
        try:
            if _production_started(client, surface, force, ingredient):
                continue
        except Exception:
            return False
        return False
    return True


def _recipe_ingredient_closure(item: str) -> frozenset[str]:
    """Transitive solid ingredients of `item` (not including itself)."""
    closure: set[str] = set()
    visited: set[str] = {item}
    pending = [item]
    while pending:
        current = pending.pop()
        spec = LINE_RECIPES.get(current)
        if spec is None:
            continue
        for ingredient in spec.get("ingredients", ()):
            ingredient = str(ingredient)
            if ingredient == item:
                continue
            closure.add(ingredient)
            if ingredient not in visited:
                visited.add(ingredient)
                pending.append(ingredient)
    closure.discard(item)
    return frozenset(closure)


def _blocked_loan_to_yield(
    client: RconClient, surface: str, force: str,
    loans: Sequence[MallBootstrapLoan], waiter: str,
) -> MallBootstrapLoan | None:
    """An active loan that should surrender its cell to `waiter`.

    A loan with zero progress on its current step, blocked on inputs with no
    producer and no stock, holds its cell forever while the waiter -- whose
    own inputs flow -- could unblock it. Servicing the blocked loan first
    deadlocked a run for 470s (a pipe rung waited on an AM2 batch whose steel
    had no producer, while steel admission waited on pipe output). Only
    current-step progress shields a loan: completed prerequisite steps are
    stale credit, not present capacity (2026-09-05: splitter/fast-inserter
    loans with credited circuit prerequisites sat at zero current progress
    with no circuit producer anywhere, and the credit blocked every yield
    until the run died). Loans with advancing current steps, binding
    shields, or dry harnesses keep their cells; each yield produces real
    output, so the waiter cannot ping-pong back.
    """
    try:
        waiter_flowing = _recipe_inputs_flowing(client, surface, force, waiter)
    except Exception:
        return None
    if not waiter_flowing:
        return None
    for loan in loans:
        try:
            if _binding_loan_shields_preempt(
                client, surface, force, loan, waiter,
            ):
                continue
            if not _loan_blocked_inputs(client, surface, force, loan):
                continue
            if loan.step_recipe is None:
                continue
            finished = _bootstrap_loan_products_finished(
                client, surface, loan,
            )
            baseline = loan.step_baseline_finished or 0
            if finished is None or finished > baseline:
                continue
            return loan
        except Exception:
            continue
    return None


def _feeder_waiter_preempts(
    client: RconClient, surface: str, force: str,
    loans: Sequence[MallBootstrapLoan], waiter: str,
) -> MallBootstrapLoan | None:
    """An active loan that should surrender its cell to the batch feeding it.

    The mirror of yield-to-unblocker: the holder keeps making progress while
    consuming the waiter's item, but the waiter -- with no producer and no
    spendable stock -- can never start, so the holder asymptotically stalls on
    crumbs (2026-09-04: a splitter loan ate every circuit while the circuit
    batch waited 300s for a cell, then both stalled). Restoring the holder
    and running the feeder first terminates: each cycle banks real output of
    both. Skipped when the holder's bill is already fulfilled (the normal
    spare-preempt path owns that), when the waiter flows elsewhere, or under
    dry harnesses.

    Deliberately deaf to the binding shield: feeding serves the binding bill
    itself, so shielding the holder from its own feeder deadlocks (2026-09-04:
    the splitter loan was foundation-binding yet starved the circuits it
    needed). Completed prerequisite steps likewise do not shield: only an
    unfulfilled bill (below) or live stock keeps the cell, since stale
    prerequisite credit otherwise protects a holder with zero current
    progress (2026-09-05: credited circuit prerequisites hid three paralyzed
    loans until the run died). The shield still guards the yield path, where
    the waiter need not feed the holder -- and the drills case the shield was
    built for cannot recur here anyway, because a stockpiling waiter
    (spendable stock above zero) never qualifies as starved.
    """
    try:
        stock = _transferable_or_available_stock(client, surface, force)
    except Exception:
        return None
    try:
        waiter_started = _production_started(client, surface, force, waiter)
    except Exception:
        return None
    if waiter_started:
        return None
    try:
        if int(stock.get(waiter, 0)) > 0:
            return None
    except Exception:
        pass
    for loan in loans:
        try:
            recipe = (
                loan.step_recipe or loan.current_recipe or loan.target_item
            )
            if waiter not in _recipe_ingredient_closure(recipe):
                continue
            actual, _usable = _bootstrap_loan_stock(
                client, surface, force, loan.target_item,
            )
            try:
                products = _bootstrap_loan_products_finished(
                    client, surface, loan,
                )
            except Exception:
                continue
            if _bootstrap_loan_minimum_fulfilled(loan, actual, products):
                continue
            return loan
        except Exception:
            continue
    return None


def _start_bootstrap_loan(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    target_item: str, target_count: int, reference_point: Point,
    emit: Callable[[str], None], *, spare_target_count: int | None = None,
    allowed_original_recipes: frozenset[str] | None = None,
    allow_shared_provider: bool = False,
    require_stocked_original: bool = False,
) -> str | None:
    existing = active_bootstrap_loans(client, surface, force)
    for loan in existing:
        if loan.target_item == target_item:
            # A durable loan already makes this batch; service it. Loans for
            # other batches keep their own cells and progress on their passes.
            return _submit_bootstrap_loan(
                client, bridge, surface, force, loan, emit,
                reference_point=reference_point,
            )
    borrowed = _borrow_free_mall_cell(
        client, surface, force, target_item, reference_point,
        allowed_original_recipes=allowed_original_recipes,
        allow_shared_provider=allow_shared_provider,
        require_stocked_original=require_stocked_original,
        excluded_origins={_loan_cell_origin(loan) for loan in existing},
    )
    if borrowed is None:
        if not existing:
            return None
        try:
            stalled = _blocked_loan_to_yield(
                client, surface, force, existing, target_item,
            )
        except Exception:
            stalled = None
        if stalled is not None:
            try:
                blocked_inputs = _loan_blocked_inputs(
                    client, surface, force, stalled,
                )
            except Exception:
                blocked_inputs = []
            _restore_bootstrap_loan(
                client, bridge, surface, force, stalled, emit,
                reference_point=reference_point,
                reason=(
                    f"yielding its cell to {target_item}, which unblocks "
                    + (", ".join(blocked_inputs) or "its stalled inputs")
                ),
            )
            return _start_bootstrap_loan(
                client, bridge, surface, force, target_item, target_count,
                reference_point, emit, spare_target_count=spare_target_count,
                allowed_original_recipes=allowed_original_recipes,
                allow_shared_provider=allow_shared_provider,
                require_stocked_original=require_stocked_original,
            )
        try:
            feeder = _feeder_waiter_preempts(
                client, surface, force, existing, target_item,
            )
        except Exception:
            feeder = None
        if feeder is not None:
            _restore_bootstrap_loan(
                client, bridge, surface, force, feeder, emit,
                reference_point=reference_point,
                reason=(
                    f"yielding its cell to {target_item}, which feeds its "
                    f"{feeder.step_recipe or feeder.current_recipe} step"
                ),
            )
            return _start_bootstrap_loan(
                client, bridge, surface, force, target_item, target_count,
                reference_point, emit, spare_target_count=spare_target_count,
                allowed_original_recipes=allowed_original_recipes,
                allow_shared_provider=allow_shared_provider,
                require_stocked_original=require_stocked_original,
            )
        # No free cell: serial handoff. Service the durable loan first; once
        # its finite stock exists this call restores the original recipe and
        # the next pass may borrow the cell for ``target_item``.
        loan = existing[0]
        emit(
            f"  MALL BOOTSTRAP LOAN HANDOFF: {target_item} waits while "
            f"the active {loan.target_item} batch at {loan.machine_position} "
            "is completed and restored"
        )
        return _submit_bootstrap_loan(
            client, bridge, surface, force, loan, emit,
            preempt_for=target_item,
            reference_point=reference_point,
        )
    original_recipe, machine_position, origin, side, machine_name = borrowed
    if existing:
        emit(
            f"  MALL BOOTSTRAP LOAN PARALLEL: {target_item} starts on a free "
            f"{original_recipe} cell at {machine_position} while "
            + ", ".join(
                f"{loan.target_item} at {loan.machine_position}"
                for loan in existing
            )
            + " continue"
        )
    requester_position = (origin[0] + 4.5, origin[1] + 1.5)
    loan = MallBootstrapLoan(
        original_recipe=original_recipe,
        target_item=target_item,
        target_count=target_count,
        spare_target_count=max(target_count, spare_target_count or target_count),
        side=side,
        requester_position=requester_position,
        current_recipe=original_recipe,
        machine_name=machine_name,
    )
    return _submit_bootstrap_loan(
        client, bridge, surface, force, loan, emit,
        reference_point=reference_point,
    )


def _allocate_dynamic_belt_capacity(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    item: str, reference_point: Point, emit: Callable[[str], None],
    plan: _LinePlan, required_target: int,
) -> bool:
    """Borrow one duplicate cable cell before building a six-machine belt line.

    The loan planner recursively makes missing gears first, so the same spare
    machine temporarily reinforces gears and then switches to belts. The two
    baseline gear machines stay in place, and borrowing one of the two cable
    machines leaves the required permanent cable anchor.
    """
    if (
        item != "transport-belt"
        or not plan.promote_to_line
        or plan.existing is None
    ):
        return False
    loans = active_bootstrap_loans(client, surface, force)
    if loans:
        return any(
            loan.target_item == item
            and loan.original_recipe in _DYNAMIC_BELT_BORROWERS
            for loan in loans
        )
    anchor_counts: dict[str, int] = {}
    for recipe in ("iron-gear-wheel", "copper-cable"):
        spec = LINE_RECIPES[recipe]
        line = live_base.find_line(
            client, surface, force, recipe, str(spec["machine"]),
        )
        anchor_counts[recipe] = line.machine_count if line is not None else 0
    def _target_anchor_count(recipe: str) -> int:
        if recipe == "copper-cable" and _copper_cable_consumers_active(client, surface, force):
            return 2
        return BASELINE_MACHINES[recipe]

    missing_anchor = next(
        (
            recipe for recipe, count in anchor_counts.items()
            if count < _target_anchor_count(recipe)
        ),
        None,
    )
    if missing_anchor is not None:
        emit(
            f"  DYNAMIC MALL WAIT: belt backlog needs the baseline "
            f"{missing_anchor} pair before a cell can be borrowed"
        )
        ensure_produced(
            client, bridge, surface, force, missing_anchor, reference_point,
            emit, upgrade_bootstrap=False,
            stock_target=max(1, plan.production_target),
            minimum_machines=_target_anchor_count(missing_anchor),
            allow_promotion=False,
        )
        raise ProductionPrerequisiteDeferred(
            f"dynamic belt allocation is preserving the {missing_anchor} anchor",
            code="dynamic_mall_anchor_wait", state="constructing",
            details={"item": item, "anchor": missing_anchor},
        )
    spare_target = max(plan.production_target, plan.mall_storage_limit)
    remedy = _start_bootstrap_loan(
        client, bridge, surface, force, item, required_target,
        reference_point, emit, spare_target_count=spare_target,
        allowed_original_recipes=_DYNAMIC_BELT_BORROWERS,
    )
    if remedy is None:
        raise StuckError(
            "large transport-belt backlog has two cable producers but no "
            "borrowable paired mall cell",
            code="dynamic_mall_no_borrower", classification="bug",
            state="supply_wait",
            details={"item": item, "anchors": anchor_counts},
        )
    emit(
        "  DYNAMIC MALL ALLOCATION: borrowed one duplicate copper-cable cell "
        f"for belts through {spare_target}; missing gears are produced first, "
        "while two gear assemblers and one cable assembler remain assigned"
    )
    return True


#: A compact mall cell may start building once half its bill is stocked, as
#: long as every missing bill item already has live production behind it.
#: Waiting for the complete bill serialized the whole bootstrap behind its
#: slowest ingredient (live runs waited over a minute for two iron plates
#: while gears, circuits, and inserters sat idle). The ledger reservation
#: stays held, so bots deliver the remainder while ghosts construct.
_MATERIAL_PROJECT_PIPELINE_FRACTION = 0.5


def _material_bill_pipeline_ready(
    bill: Mapping[str, int], stock: Mapping[str, int], *,
    is_scheduled: Callable[[str], bool],
) -> bool:
    """Whether a partially stocked bill may build now and catch up later."""
    total = sum(bill.values())
    if total <= 0:
        return False
    covered = sum(
        min(stock.get(name, 0), required)
        for name, required in bill.items()
    )
    if covered / total < _MATERIAL_PROJECT_PIPELINE_FRACTION:
        return False
    return all(
        is_scheduled(name)
        for name, required in bill.items()
        if stock.get(name, 0) < required
    )


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
    allocation = (
        preview_mall_allocation(client, surface, item, reference_point)
        if reference_point is not None else None
    )
    project_spec = getattr(plan, "spec", None) or LINE_RECIPES.get(item, {})
    bill = compact_mall_project_bill(
        item, stock_target=plan.mall_storage_limit,
        stock_gate_target=stock_gate_target,
        fill_chest=plan.fill_provider,
        request_multiplier_override=plan.mall_request_multiplier,
        side=allocation[1] if allocation is not None else "left",
        shared_provider=getattr(plan, "shared_provider", False),
        machine_name=project_spec.get("machine"),
    )
    stock = live_base.transferable_items(client, surface, force)
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
    if _material_bill_pipeline_ready(
        bill, stock,
        is_scheduled=lambda name: construction_supply_chain_is_scheduled(
            client, surface, force, name,
        ),
    ):
        emit(
            f"  MATERIAL PROJECT PIPELINE READY: {project_id} has half its "
            "bill; missing items are backed by live producers; placing now"
        )
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
            classification="bug",
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
        _transferable_or_available_stock(client, surface, force)
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
    # The bootstrap budget is global.  Previously only optional demand cells
    # consulted it, while core-mall promotion kept allocating permanent cells
    # outside the budget; the latest run therefore reached thirteen mall
    # assemblers before the first logistic chest existed.  A running line is
    # never blocked here -- only a genuinely new compact slot, including an
    # extra half for an under-sized existing line, waits.
    if (
        not upgrade_bootstrap
        and plan.spec.get("machine") in live_base.ASSEMBLER_TIERS
        and not _core_mall_ready(client, surface, force)
        and not plan.promote_to_line
        and (plan.existing is None or not plan.at_size)
    ):
        committed = mall_slot_count(client, surface, reference_point)
        if committed >= BOOTSTRAP_MALL_SLOT_TARGET:
            emit(
                f"  BOOTSTRAP MALL CAP: {committed}/"
                f"{BOOTSTRAP_MALL_SLOT_TARGET} assemblers are committed; "
                f"deferring new {item} cell until a line promotion frees a slot"
            )
            raise ProductionPrerequisiteDeferred(
                f"bootstrap mall is capped at {BOOTSTRAP_MALL_SLOT_TARGET} assemblers",
                code="bootstrap_mall_slot_cap",
                state="supply_wait",
                details={
                    "item": item,
                    "committed_slots": committed,
                    "slot_cap": BOOTSTRAP_MALL_SLOT_TARGET,
                },
            )
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
                "  STEEL CAPACITY GATE: the steel starter needs the shared "
                f"iron line at {STEEL_IRON_CAPACITY_FLOOR} furnaces and drills "
                f"(have {iron_furnaces}/{iron_drills}); expanding iron first"
            )
            build_mining_stage(
                client, bridge, surface, force, "iron-plate", reference_point,
                emit, expand=iron_furnaces > 0,
            )
            raise ProductionPrerequisiteDeferred(
                f"steel-plate waits for the {STEEL_IRON_CAPACITY_FLOOR}-furnace/"
                f"{STEEL_IRON_CAPACITY_FLOOR}-drill iron starter checkpoint"
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
            f"{STEEL_BASELINE_FURNACES}-furnace starter beside the iron provider"
        )
        return
    if promote_to_line:
        # Circuit promotion is a complete local production block, not a third
        # requester-fed cell.  First bring copper cable to the recipe-derived
        # companion size; its own raw-copper check expands the refinery before
        # construction.  The following circuit pass can then route the cable
        # line directly and retire both compact circuit cells.
        companion_count = promoted_companion_machine_count(item, promoted_count)
        if companion_count is not None:
            cable_spec = LINE_RECIPES["copper-cable"]
            cable_existing = live_base.find_line(
                client, surface, force, "copper-cable", cable_spec["machine"],
            )
            if cable_existing is None or cable_existing.machine_count < companion_count:
                cable_plan = _LinePlan(
                    existing=cable_existing,
                    spec=cable_spec,
                    production_target=companion_count,
                    mall_storage_limit=companion_count,
                    fill_provider=False,
                    mall_request_multiplier=None,
                    demand=0.0,
                    saturated=False,
                    promoted_count=companion_count,
                    promote_to_line=True,
                    at_size=False,
                )
                cable_provider = (
                    _paired_mall_provider(
                        client, surface, cable_existing.machine_positions,
                    ) if cable_existing is not None else None
                )
                emit(
                    f"  CIRCUIT BLOCK: {promoted_count} circuit machines need "
                    f"{companion_count} direct copper-cable machines; "
                    "expanding cable before circuits"
                )
                _build_assembled_stage(
                    client, bridge, surface, force, "copper-cable",
                    reference_point, emit, cable_plan, cable_provider,
                    upgrade_bootstrap=False,
                )
                return
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
        output = build_conversion_stage(
            client, bridge, surface, force, item, sources, line_reference, emit,
            machine_count=promoted_count,
            belt_type=promoted_line_belt_type(
                item, promoted_count, live_base.available_items(client, surface, force),
                full_lane_input=item in {"copper-cable", "electronic-circuit"},
            ),
            inserter_type="fast-inserter",
            allow_logistic_inputs=False,
            side_tap_output=True,
            direct_sideload_ingredients=(
                frozenset({"copper-plate"})
                if item == "copper-cable"
                else frozenset({"copper-cable", "iron-plate"})
                if item == "electronic-circuit"
                else frozenset()
            ),
            full_bus_ingredients=(
                frozenset({"copper-plate"})
                if item == "copper-cable"
                else frozenset({"copper-cable"})
                if item == "electronic-circuit"
                else frozenset()
            ),
        )
        MANAGED_INTERMEDIATE_SOURCES[item] = output
        # Retire the old cell only now the line that replaces it exists.
        # Retiring first meant a pass that did not finish the line left the
        # recipe with no machine at all, so the next survey rebuilt the very
        # cell just removed -- seen twice in fifteen seconds at cell (35,31).
        if existing:
            retired = 0
            for position in existing.machine_positions:
                provider = _paired_mall_provider(client, surface, [position])
                if provider is None:
                    continue
                preserve_provider = mall_slot_uses_shared_provider(
                    client, surface, position, reference_point,
                )
                retirement = generate_promoted_mall_retirement_plan(
                    item, spec["machine"], position, provider,
                    preserve_provider=preserve_provider,
                )
                retirement["surface"], retirement["force"] = surface, force
                _submit(
                    client, bridge, surface, retirement,
                    f"retire_promoted_mall_{item}_{position[0]}_{position[1]}", emit,
                )
                retired += 1
            if retired:
                emit(
                    f"  MALL RETIRE: the {item} line is up; released {retired} "
                    "compact mall slot(s) and cleared their request groups"
                )
    elif not upgrade_bootstrap:
        output = build_compact_mall_stage(
            client, bridge, surface, force, item, sources, reference_point,
            bring_stage_up, emit, stock_target=mall_storage_limit,
            stock_gate_target=stock_gate_target,
            fill_chest=plan.fill_provider,
            request_multiplier_override=plan.mall_request_multiplier,
            shared_provider=getattr(plan, "shared_provider", False),
            machine_name=spec["machine"],
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


_CHEMICAL_BATCH_TARGETS = {
    "pipe": ITEM_STACK_SIZES.get("pipe", 100),
    "chemical-plant": 2,
    "oil-refinery": 1,
    "offshore-pump": 1,
    "pumpjack": 1,
}


def _chemical_uses_rotating_batch(
    client: RconClient, surface: str, force: str, item: str,
) -> bool:
    if item == "pipe":
        return _pipe_is_temporary_batch(client, surface, force)
    return item in _CHEMICAL_BATCH_TARGETS and not _core_mall_ready(
        client, surface, force,
    )


def _chemical_capability_started(
    client: RconClient, surface: str, force: str, item: str,
) -> bool:
    if item == "sulfuric-acid":
        line = live_base.find_line(
            client, surface, force, item, "chemical-plant",
        )
        return line is not None and (
            line.working_count > 0 or line.produced_count > 0
        )
    if item in _CHEMICAL_BATCH_TARGETS and _chemical_uses_rotating_batch(
        client, surface, force, item,
    ):
        stock = live_base.available_items(client, surface, force)
        return stock.get(item, 0) >= _CHEMICAL_BATCH_TARGETS[item]
    return _production_started(client, surface, force, item)


def _chemical_ladder_predecessors(item: str) -> tuple[str, ...]:
    if item in CHEMICAL_BOOTSTRAP_LADDER:
        stop = CHEMICAL_BOOTSTRAP_LADDER.index(item)
    elif item in {"battery", "processing-unit"}:
        stop = len(CHEMICAL_BOOTSTRAP_LADDER)
    else:
        return ()
    return CHEMICAL_BOOTSTRAP_LADDER[:stop]


def _unfunded_ladder_ingredient(
    client: RconClient, surface: str, force: str, item: str,
) -> str | None:
    """First unstarted, unstocked ladder rung in `item`'s recipe closure.

    Mall batches for items whose advanced chemical ingredients cannot exist
    yet (no producer, no stock, upstream cell unbuilt) spin borrow/restore
    cycles forever: every pass serves them, nothing can be made (2026-09-05:
    bulk-inserter waited 500s on advanced circuits while plastic-bar was not
    even sited). Only the oil-gated half of the ladder gates: early rungs
    (pipe through pumpjack) have dedicated establishment flows that the
    batch path actively drives, so parking on them would just idle. Ladder
    rungs establish themselves and are never gated; everything else parks
    until its rung flows. Returns None when the item is fundable --
    including when any survey fails, since a blind park is worse than a
    wasted borrow.
    """
    if item in CHEMICAL_BOOTSTRAP_LADDER:
        return None
    try:
        oil_half = CHEMICAL_BOOTSTRAP_LADDER[
            CHEMICAL_BOOTSTRAP_LADDER.index("plastic-bar"):
        ]
    except ValueError:
        return None
    try:
        closure = _recipe_ingredient_closure(item)
    except Exception:
        return None
    try:
        stock = _transferable_or_available_stock(client, surface, force)
    except Exception:
        return None
    for rung in oil_half:
        if rung not in closure:
            continue
        try:
            if int(stock.get(rung, 0)) > 0:
                continue
        except Exception:
            continue
        try:
            started = _chemical_capability_started(client, surface, force, rung)
        except Exception:
            continue
        if not started:
            return rung
    return None


def _missing_chemical_ladder_predecessor(
    client: RconClient, surface: str, force: str, item: str,
) -> str | None:
    return next((
        predecessor
        for predecessor in _chemical_ladder_predecessors(item)
        if not _chemical_capability_started(
            client, surface, force, predecessor,
        )
    ), None)


def _defer_steel_blocked_loan(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    reference_point: Point, emit: Callable[[str], None],
) -> bool:
    """Release a loan that cannot advance until the steel starter exists.

    `pipe` is first in the chemical capability ladder, but it is not an input
    to the dedicated steel furnace.  Letting that capability ordering hold an
    AM2 loan which is itself missing steel creates a circular cell handoff.
    Restoring the blocked loan is safe: it has no path to its target until the
    independent, material-funded steel conversion starts.
    """
    for loan in active_bootstrap_loans(client, surface, force):
        if loan.target_item == "steel-plate":
            continue
        try:
            if "steel-plate" not in _loan_blocked_inputs(
                client, surface, force, loan,
            ):
                continue
        except Exception:
            continue
        _restore_bootstrap_loan(
            client, bridge, surface, force, loan, emit,
            reference_point=reference_point,
            reason=(
                "yielding to the independent steel starter required by its "
                "current recipe"
            ),
        )
        emit(
            f"  STEEL STARTER PRIORITY: deferred {loan.target_item} at "
            f"{loan.machine_position}; pipe does not gate its steel prerequisite"
        )
        return True
    return False


def _ensure_chemical_ladder_predecessor(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    item: str, reference_point: Point, emit: Callable[[str], None],
) -> None:
    """Advance at most one missing rung before constructing `item`."""
    predecessors = _chemical_ladder_predecessors(item)
    if not predecessors:
        return
    predecessor = _missing_chemical_ladder_predecessor(
        client, surface, force, item,
    )
    if predecessor is None:
        return
    if (
        item == "steel-plate"
        and predecessor == "pipe"
        and _defer_steel_blocked_loan(
            client, bridge, surface, force, reference_point, emit,
        )
    ):
        # Steel is a dedicated conversion, not a borrowed mall batch. Its
        # first furnace can start now; the restored AM2 loan is retried only
        # after that producer has a chance to make its missing prerequisite.
        return
    stop = len(predecessors)
    if predecessor is not None:
        target = _CHEMICAL_BATCH_TARGETS.get(predecessor)
        emit(
            f"  CHEMICAL LADDER: {item} waits at {predecessor} "
            f"({stop}/{len(CHEMICAL_BOOTSTRAP_LADDER)} rungs required)"
        )
        if target is not None and _chemical_uses_rotating_batch(
            client, surface, force, predecessor,
        ):
            remedy = _start_bootstrap_loan(
                client, bridge, surface, force, predecessor, target,
                reference_point, emit,
            )
            if remedy is None:
                raise StuckError(
                    f"chemical ladder cannot borrow a mall cell for {predecessor}",
                    code="chemical_ladder_no_borrower",
                    classification="bug",
                    state="supply_wait",
                    details={"item": item, "predecessor": predecessor},
                )
            raise ProductionPrerequisiteDeferred(
                remedy,
                code="chemical_capability_batch",
                state="supply_wait",
                details={
                    "target": item, "rung": predecessor,
                    "required_stock": target,
                },
            )
        ensure_produced(
            client, bridge, surface, force, predecessor, reference_point, emit,
            upgrade_bootstrap=False, stock_target=max(1, target or 1),
            allow_promotion=False,
        )
        raise ProductionPrerequisiteDeferred(
            f"chemical ladder is establishing {predecessor} before {item}",
            code="chemical_capability_wait",
            state="producing",
            details={"target": item, "rung": predecessor},
        )

def ensure_produced(
    client: RconClient, bridge: GameBridge, surface: str, force: str, item: str,
    reference_point: Point, emit: Callable[[str], None], *,
    upgrade_bootstrap: bool = True, stock_target: int = 1,
    minimum_machines: int = 1, allow_promotion: bool = True,
    stock_gate_target: int | None = None, storage_limit: int | None = None,
    fill_provider: bool = False, blocking_stock_target: int | None = None,
    temporary_mall: bool = False,
) -> Point | None:
    """Returns the item's real output chest position if it's already producing;
    otherwise builds exactly ONE missing stage (the deepest unmet ingredient
    first) and returns None so the caller re-surveys and calls again."""
    if item in MANAGED_INTERMEDIATE_SOURCES:
        _complete_material_producer(item)
        return MANAGED_INTERMEDIATE_SOURCES[item]
    _ensure_chemical_ladder_predecessor(
        client, bridge, surface, force, item, reference_point, emit,
    )
    if item == "steel-plate":
        minimum_machines = max(minimum_machines, STEEL_BASELINE_FURNACES)
    if item == "copper-cable":
        if _copper_cable_consumers_active(client, surface, force):
            minimum_machines = max(minimum_machines, 2)
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
            bring_stage_up, emit, target_output=item,
        )
        return outputs[item] if outputs else None
    if item == "sulfuric-acid":
        return ensure_sulfuric_acid_cell(
            client, bridge, surface, force, reference_point,
            bring_stage_up, emit,
        )
    if item == "battery":
        return ensure_battery_cell(
            client, bridge, surface, force, reference_point,
            bring_stage_up, emit,
        )
    if item not in LINE_RECIPES:
        raise StuckError(f"No recipe knowledge for {item!r} -- add it to planners/recipe_data.py "
                          "before asking the builder to produce it")
    if item == "steel-chest" and upgrade_bootstrap:
        # Steel chests only seed the logistic-chest capability before the core
        # mall exists. A generic conversion line defaults to two assemblers
        # and rate-sizes five requester feeders for this recipe, which is
        # wasteful for a one-chest seed and can never be a mall expansion
        # decision. Route early requests through the compact/loan path; once
        # the core mall is ready it may allocate a normal permanent cell.
        upgrade_bootstrap = False
        temporary_mall = True
    if not upgrade_bootstrap:
        temporary_precore = _is_pre_core_temporary_mall_item(
            client, surface, force, item,
        )
        if temporary_mall or temporary_precore:
            if _rationed_mall_batch(
                client, bridge, surface, force, item, max(1, stock_target),
                reference_point, emit, force_temporary=temporary_mall,
            ):
                raise ProductionPrerequisiteDeferred(
                    f"{item} is being made as a temporary bootstrap mall batch",
                    code="temporary_mall_batch",
                    state="supply_wait",
                    details={"item": item, "target": max(1, stock_target)},
                )
        if temporary_precore:
            temporary_target = _rationed_mall_spare_target(
                client, surface, force, item, max(1, stock_target),
            )
            stock_target = max(stock_target, temporary_target)
            storage_limit = min(
                storage_limit if storage_limit is not None else temporary_target,
                temporary_target,
            )
            stock_gate_target = max(
                stock_gate_target or 0, temporary_target,
            )
            fill_provider = False
    if not upgrade_bootstrap and _MATERIAL_RESERVATION_LEDGER is not None:
        loan_wait = _service_bootstrap_loan(
            client, bridge, surface, force, item, reference_point, emit,
        )
        if loan_wait is not None:
            raise ProductionPrerequisiteDeferred(loan_wait)
    if (
        item == "pipe"
        and not upgrade_bootstrap
        and not _pipe_is_temporary_batch(client, surface, force)
        and live_base.find_line(
            client, surface, force, item, LINE_RECIPES[item]["machine"],
        ) is None
    ):
        target = max(stock_target, _CHEMICAL_BATCH_TARGETS["pipe"])
        remedy = _start_bootstrap_loan(
            client, bridge, surface, force, item, target,
            reference_point, emit,
            spare_target_count=target,
            allowed_original_recipes=_PIPE_PERMANENT_DONORS,
            allow_shared_provider=True,
            require_stocked_original=True,
        )
        if remedy is None:
            stock = live_base.available_items(client, surface, force)
            raise StuckError(
                "permanent pipe mall needs one stocked demand slot to convert, "
                "but no eligible slot is currently releasable",
                code="pipe_permanent_slot_unavailable",
                classification="intended_difficulty",
                state="supply_wait",
                details={
                    "eligible_recipes": sorted(_PIPE_PERMANENT_DONORS),
                    "stocked_eligible_recipes": sorted(
                        recipe for recipe in _PIPE_PERMANENT_DONORS
                        if stock.get(recipe, 0) > 0
                    ),
                },
            )
        raise ProductionPrerequisiteDeferred(
            remedy,
            code="pipe_permanent_slot_conversion",
            state="constructing",
            details={"target_stock": target},
        )
    if (
        item == "automation-science-pack"
        and not _metal_starter_transition_complete(client, surface, force)
        and live_base.find_line(
            client, surface, force, item, LINE_RECIPES[item]["machine"],
        ) is None
    ):
        _ensure_automation_science_transition(
            client, bridge, surface, force, reference_point, emit,
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
        shared_provider=(
            not upgrade_bootstrap
            and item in _BOOTSTRAP_SHARED_PROVIDER_ITEMS
            and not _core_mall_ready(client, surface, force)
        ),
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
    if (
        existing
        and plan.at_size
        and not upgrade_bootstrap
        and _allocate_dynamic_belt_capacity(
            client, bridge, surface, force, item, reference_point, emit, plan,
            max(1, blocking_stock_target or plan.production_target),
        )
    ):
        return _serve_healthy_line(
            client, bridge, surface, force, item, reference_point, emit,
            plan, mall_provider, upgrade_bootstrap=upgrade_bootstrap,
        )
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


def _core_mall_ready(
    client: RconClient, surface: str, force: str,
) -> bool:
    """Whether the mall can now afford permanent one-recipe cell ownership."""
    return all(
        _production_started(client, surface, force, item)
        for item in CORE_MALL_PRODUCERS
    )


#: Announced once per run when the post-starter program begins.
_POST_STARTER_TRANSITION_ANNOUNCED = False


def _post_starter_phase(client: RconClient, surface: str, force: str) -> bool:
    """Whether advanced circuits are in production.

    The user's trigger to leave the starter phase: first the mall expands,
    then mines and furnaces, then serial steel and serial science for the
    mining-productivity goal. This is the leading edge; full core-mall
    readiness (all five producers) follows through the existing gates.
    """
    try:
        return _production_started(client, surface, force, "advanced-circuit")
    except Exception:
        return False


def _maybe_announce_post_starter(
    client: RconClient, surface: str, force: str,
    emit: Callable[[str], None],
) -> bool:
    """Emit the post-starter transition line once per run when earned."""
    global _POST_STARTER_TRANSITION_ANNOUNCED
    if _POST_STARTER_TRANSITION_ANNOUNCED:
        return False
    if not _post_starter_phase(client, surface, force):
        return False
    _POST_STARTER_TRANSITION_ANNOUNCED = True
    emit(
        "POST-STARTER TRANSITION: advanced circuits are producing -- "
        "starter phase over; expanding the mall, then mines and furnaces, "
        "then serial steel and serial science"
    )
    return True


def _retrofit_bootstrap_mall_outputs(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    mall_targets: dict[str, int], reference_point: Point,
    emit: Callable[[str], None],
) -> bool:
    """Give one shared-output right half its permanent provider each pass."""
    candidate = next_shared_provider_retrofit_plan(
        client, surface, force, reference_point,
    )
    if candidate is None:
        return False
    recipe, origin, plan = candidate
    stock = live_base.available_items(client, surface, force)
    project_id = (
        f"retrofit_shared_mall_{recipe}_{origin[0]}_{origin[1]}"
    )
    bill = plan_material_bill(plan)
    if _MATERIAL_RESERVATION_LEDGER is not None:
        sources, rates = _material_sources_and_rates(
            client, surface, force, bill, stock,
        )
        _MATERIAL_RESERVATION_LEDGER.declare(
            project_id, bill, stock, target_item=recipe,
            source_producers=sources, expected_rates=rates,
            priority=95, hold_until_producing=True,
        )
        shortage = _MATERIAL_RESERVATION_LEDGER.shortage_targets(
            project_id, stock,
        )
    else:
        shortage = {
            item: required for item, required in bill.items()
            if stock.get(item, 0) < required
        }
    if shortage:
        for item, target in shortage.items():
            mall_targets[item] = max(mall_targets.get(item, 0), target)
        emit(
            f"  MALL RETROFIT WAIT: {recipe} at {origin} reserves "
            + ", ".join(
                f"{item}={target}" for item, target in sorted(shortage.items())
            )
            + " before separating its shared output"
        )
        return False
    _submit(
        client, bridge, surface, plan,
        project_id, emit,
    )
    if _MATERIAL_RESERVATION_LEDGER is not None:
        _MATERIAL_RESERVATION_LEDGER.complete(project_id)
    emit(
        f"  MALL RETROFIT: {recipe} at {origin} now has its own provider; "
        "the bootstrap-shared chest remains with the other half"
    )
    return True


def _rationed_mall_wip_gap(
    client: RconClient, surface: str, force: str, item: str,
) -> int:
    """Items counted in force stock but locked in requester/buffer WIP.

    ``available_items`` includes requester-held ingredients while
    ``transferable_items`` (what a foundation can actually spend) does not.
    That difference is the margin a zero-spare batch is missing: producing
    exactly ``required`` available items can still leave transferable stock
    short by this gap.
    """
    if not hasattr(client, "command"):
        return 0
    try:
        available = live_base.available_items(client, surface, force).get(item, 0)
        transferable = live_base.transferable_items(
            client, surface, force,
        ).get(item, 0)
    except Exception:
        return 0
    return max(0, int(available) - int(transferable))


def _rationed_mall_spare_target(
    client: RconClient, surface: str, force: str, item: str, required: int,
) -> int:
    """Bound optional rotating-slot output without delaying its current bill."""
    reserve = mall_reserve_for(client, surface, force, item, required)
    if item == "splitter":
        if _metal_starter_transition_complete(client, surface, force):
            return max(required, reserve.storage_count)
        iron_available = (
            live_base.available_items(client, surface, force).get("iron-plate", 0)
            if hasattr(client, "command") else 0
        )
        if iron_available >= 50:
            return max(required, 15)
    if reserve.storage_count <= required:
        return required
    spare = max(
        _RATIONED_MALL_EXTRA_SPARES,
        math.ceil(required * _RATIONED_MALL_SPARE_FRACTION),
        _rationed_mall_wip_gap(client, surface, force, item),
    )
    return min(reserve.storage_count, required + spare)


def _rationed_mall_completion_target(
    client: RconClient, surface: str, force: str, item: str, required: int,
) -> int:
    """Transferable stock that retires a rationed batch, spares included.

    A batch is done only when the base holds the exact bill PLUS its spare
    margin as spendable provider/storage stock. Checking the exact bill
    against force-wide stock (requester WIP included) is how 148 available
    belts read as done while the stone foundation sat at 89% transferable.
    """
    try:
        if not _is_pre_core_temporary_mall_item(client, surface, force, item):
            return required
    except Exception:
        return required
    try:
        return _rationed_mall_spare_target(client, surface, force, item, required)
    except Exception:
        return required


def _drain_aware_pop_due(
    client: RconClient, surface: str, force: str, item: str, target: int,
    transferable_have: int, *, loan_advanced_this_pass: bool,
) -> bool:
    """Whether a bill-met demand should retire while spares are still short.

    User direction 2026-09-03: retire at the exact bill once drain is proven
    -- the loan advanced this pass yet spendable stock did not grow since the
    last survey, so a live consumer is eating output as fast as it is made.
    The borrowed cell keeps producing spares in the background and preempt
    frees it on contention; holding the demand would pin the cell until the
    livelock guard fires. Climbing stock, idle loans, and missing history all
    keep the demand queued for the full spare target.
    """
    try:
        done_at = _rationed_mall_completion_target(
            client, surface, force, item, target,
        )
    except Exception:
        return False
    if transferable_have < target or transferable_have >= done_at:
        return False
    if not loan_advanced_this_pass:
        return False
    previous = _DRAIN_WATCH_PREVIOUS_TRANSFERABLE.get((surface, force, item))
    if previous is None or transferable_have > previous:
        return False
    try:
        loans = active_bootstrap_loans(client, surface, force)
    except Exception:
        return False
    return any(loan.target_item == item for loan in loans)


def _bootstrap_demand_cell_affordable(
    client: RconClient, surface: str, force: str, item: str, target: int,
    reference_point: Point,
) -> tuple[bool, dict[str, int]]:
    """Whether one more shared-output cell can be funded without stealing."""
    if mall_slot_count(
        client, surface, reference_point,
    ) >= BOOTSTRAP_MALL_SLOT_TARGET:
        return False, {}
    allocation = preview_mall_allocation(
        client, surface, item, reference_point,
    )
    if allocation is None:
        return False, {}
    _origin, side = allocation
    bill = compact_mall_project_bill(
        item, stock_target=max(1, target), side=side, shared_provider=True,
        machine_name="assembling-machine-1",
    )
    stock = _transferable_or_available_stock(client, surface, force)
    available = (
        _MATERIAL_RESERVATION_LEDGER.allocatable_stock(
            stock, claimant=_material_project_id(item),
        )
        if _MATERIAL_RESERVATION_LEDGER is not None else stock
    )
    shortage = {
        ingredient: required
        for ingredient, required in bill.items()
        if available.get(ingredient, 0) < required
    }
    if shortage and hasattr(client, "command"):
        # A reserved-but-flowing input is not stolen by one more cell: when
        # its producer chain runs to raw extraction and spendable stock is
        # present, the cell bill draws from surplus flow that refills behind
        # it (2026-09-03: 5 mall assemblers idle with free pool slots while
        # reserved iron-plate sat in the provider and belts serialized on one
        # AM1). A stagnant stockpile with no scheduled producer still blocks.
        flowed = set()
        for ingredient in shortage:
            try:
                scheduled = construction_supply_chain_is_scheduled(
                    client, surface, force, ingredient,
                )
            except Exception:
                scheduled = False
            if scheduled and int(stock.get(ingredient, 0)) > 0:
                flowed.add(ingredient)
        for ingredient in flowed:
            del shortage[ingredient]
    return not shortage, shortage


def _bootstrap_reserve_machine_target(
    client: RconClient, surface: str, force: str, item: str, target: int,
    reference_point: Point, emit: Callable[[str], None], *, background: bool,
) -> int:
    """Fund temporary capacity when a transition reserve is slow.

    The eight-slot bootstrap pool is capacity, not just recipe coverage. Once a
    producer exists, a blocking one-stack circuit or splitter reserve may use
    one additional shared-output slot when its remaining backlog exceeds a
    minute. Gear and cable may likewise grow by bounded temporary cells when a
    large construction reserve needs them; their protected anchor remains.
    Exact cell materials and the pool limit remain hard gates.
    """
    if background or _core_mall_ready(client, surface, force):
        return 1
    capability = BaseCapability(
        produces_tier2=_production_started(
            client, surface, force, "assembling-machine-2",
        ),
        produces_tier3=_production_started(
            client, surface, force, "assembling-machine-3",
        ),
        stock=live_base.available_items(client, surface, force),
    )
    active_machine = mall_machine(item, capability)
    is_tier1 = active_machine == "assembling-machine-1"
    metal_complete = _metal_starter_transition_complete(client, surface, force)
    if not metal_complete and not is_tier1:
        return 1
    backlog_threshold = 30.0 if is_tier1 else _PARALLEL_BOOTSTRAP_BACKLOG_SECONDS
    stack_size = ITEM_STACK_SIZES.get(item, FALLBACK_STACK_SIZE)

    if item in _MALL_RECIPE_ANCHORS:
        if not is_tier1 and target < stack_size:
            return 1
        spec = LINE_RECIPES[item]
        existing = live_base.find_line(
            client, surface, force, item, str(spec["machine"]),
        )
        existing_count = existing.machine_count if existing is not None else 0
        if existing_count <= 0:
            return 1
        stock = live_base.available_items(client, surface, force)
        outstanding = max(0, target - int(stock.get(item, 0)))
        backlog = backlog_seconds(
            item, outstanding, existing_count, active_machine,
        )
        if backlog <= backlog_threshold:
            return existing_count
        affordable, shortage = _bootstrap_demand_cell_affordable(
            client, surface, force, item, target, reference_point,
        )
        if not affordable:
            if shortage:
                emit(
                    f"  DYNAMIC ANCHOR CAPACITY WAIT: another {item} "
                    "producer would consume reserved "
                    + ", ".join(sorted(shortage))
                )
            return existing_count
        wanted = existing_count + 1
        _BOOTSTRAP_SHARED_PROVIDER_ITEMS.add(item)
        emit(
            f"  DYNAMIC ANCHOR CAPACITY: {item} has {backlog:.0f}s of "
            f"blocking backlog; funding {wanted} temporary producers "
            f"within the {BOOTSTRAP_MALL_SLOT_TARGET}-assembler pool"
        )
        return wanted
    if item not in _PARALLEL_BOOTSTRAP_RESERVE_ITEMS:
        return 1
    if not is_tier1 and target < stack_size:
        return 1
    spec = LINE_RECIPES[item]
    existing = live_base.find_line(
        client, surface, force, item, str(spec["machine"]),
    )
    existing_count = existing.machine_count if existing is not None else 0
    if existing_count != 1:
        return max(1, existing_count)
    stock = live_base.available_items(client, surface, force)
    outstanding = max(0, target - int(stock.get(item, 0)))
    backlog = backlog_seconds(
        item, outstanding, existing_count, active_machine,
    )
    if backlog <= backlog_threshold:
        return existing_count
    affordable, shortage = _bootstrap_demand_cell_affordable(
        client, surface, force, item, target, reference_point,
    )
    if not affordable:
        if shortage:
            emit(
                f"  DYNAMIC MALL CAPACITY WAIT: a second {item} producer "
                "would consume reserved " + ", ".join(sorted(shortage))
            )
        return existing_count
    _BOOTSTRAP_SHARED_PROVIDER_ITEMS.add(item)
    emit(
        f"  DYNAMIC MALL CAPACITY: {item} has {backlog:.0f}s of blocking "
        f"backlog; funding 2 producers within the "
        f"{BOOTSTRAP_MALL_SLOT_TARGET}-assembler pool"
    )
    return 2


def _rationed_mall_batch(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    item: str, target: int, reference_point: Point,
    emit: Callable[[str], None], *, force_temporary: bool = False,
) -> bool:
    """Make a finite construction batch in a borrowed cell when necessary.

    Returns True while the caller should keep the demand queued. The provider
    may temporarily contain products from more than one recipe; stock surveys,
    not chest identity, decide when the batch is complete. ``force_temporary``
    is used by the belt-prep checkpoint: a producer is required even when the
    opening starter already has enough belts for its first craft.
    """
    pipe_batch = item == "pipe" and _pipe_is_temporary_batch(
        client, surface, force,
    )
    if item not in RATIONED_MALL_BATCH_ITEMS and not pipe_batch:
        return False
    if not pipe_batch and _core_mall_ready(client, surface, force):
        return False
    if (
        _recipe_depends_on_fast_transport_belt(item)
        and not _fast_transport_belt_gate_open(client, surface, force)
    ):
        # A recipe loan configures the borrowed assembler directly and would
        # otherwise skip ensure_produced's fast-belt gate for a nested fast
        # belt/underground/splitter shortage.
        return False
    stock = _transferable_or_available_stock(client, surface, force)
    done_at = _rationed_mall_completion_target(
        client, surface, force, item, target,
    )
    if stock.get(item, 0) >= done_at and not force_temporary:
        return False
    ladder_block = _unfunded_ladder_ingredient(client, surface, force, item)
    if ladder_block is not None:
        # The bill's advanced ingredients cannot exist yet; borrowing a cell
        # for it just churns against whoever holds one. Park on the rung
        # like a chemical handoff so the pass stays cheap while oil builds.
        raise ProductionPrerequisiteDeferred(
            f"chemical ladder is establishing {ladder_block} before {item}",
            code="chemical_capability_handoff",
            state="supply_wait",
            details={"target": item, "rung": ladder_block},
        )
    active = active_bootstrap_loans(client, surface, force)
    if not active or any(loan.target_item == item for loan in active):
        actual, usable = _bootstrap_loan_stock(
            client, surface, force, item,
        )
        external = next((
            shortage
            for shortage in bootstrap_external_shortages(
                item, target, usable, actual,
            )
            if not _production_started(
                client, surface, force, shortage.item,
            )
        ), None)
        if external is not None:
            own = next(
                (loan for loan in active if loan.target_item == item), None,
            )
            if own is not None:
                _restore_bootstrap_loan(
                    client, bridge, surface, force, own, emit,
                    reference_point=reference_point,
                    reason=(
                        f"{item} needs unproduced external input "
                        f"{external.item}={external.count}"
                    ),
                )
            else:
                emit(
                    f"  ROTATING MALL SWITCH: {item} needs {external.item}="
                    f"{external.count}, with no stock or live producer; "
                    "establishing that prerequisite before borrowing a slot"
                )
            # Establishing the prerequisite is not optional: restoring the
            # loan and merely deferring re-borrows it every pass while the
            # prerequisite is never built (2026-09-04: an AM2 loan borrowed
            # and restored in the same breath for 300s with steel nowhere).
            # Shortages and waits propagate to the caller, which queues and
            # retries them through the normal paths.
            ensure_produced(
                client, bridge, surface, force, external.item,
                reference_point, emit, upgrade_bootstrap=True,
                stock_target=max(1, external.count), minimum_machines=1,
                allow_promotion=False,
            )
            if own is not None:
                raise ProductionPrerequisiteDeferred(
                    f"rotating mall restored {item} before establishing "
                    f"{external.item}; retrying after re-observation",
                    code="rotating_mall_prerequisite_handoff",
                    state="supply_wait",
                    details={
                        "target": item,
                        "prerequisite": external.item,
                        "required": external.count,
                    },
                )
            return True
    if not active:
        spec = LINE_RECIPES[item]
        existing = live_base.find_line(
            client, surface, force, item, str(spec["machine"]),
        )
        if existing is not None and existing.machine_count > 0:
            return False
        affordable, shortage = _bootstrap_demand_cell_affordable(
            client, surface, force, item, target, reference_point,
        )
        if affordable:
            _BOOTSTRAP_SHARED_PROVIDER_ITEMS.add(item)
            emit(
                f"  BOOTSTRAP MALL CAPACITY: assigning a demand-owned {item} "
                f"slot within the {BOOTSTRAP_MALL_SLOT_TARGET}-assembler pool; "
                "paired halves share one passive provider"
            )
            return False
        if shortage:
            emit(
                f"  BOOTSTRAP MALL CAPACITY WAIT: {item} cannot claim another "
                "slot without consuming reserved "
                + ", ".join(sorted(shortage))
            )
    spare_target = _rationed_mall_spare_target(
        client, surface, force, item, target,
    )
    remedy = _start_bootstrap_loan(
        client, bridge, surface, force, item, target, reference_point, emit,
        spare_target_count=spare_target,
    )
    if remedy is None:
        active = active_bootstrap_loans(client, surface, force)
        raise StuckError(
            f"rationed mall needs a borrowable assembler to batch {item} "
            f"through stock {target}",
            code="rationed_mall_no_borrower",
            classification="bug",
            state="supply_wait",
            details={
                "item": item,
                "target": target,
                "available_stock": stock.get(item, 0),
                "active_loans": [
                    {
                        "original_recipe": loan.original_recipe,
                        "target_item": loan.target_item,
                        "target_count": loan.target_count,
                        "current_recipe": loan.current_recipe,
                        "machine_position": list(loan.machine_position),
                    }
                    for loan in active
                ],
            },
        )
    emit(
        f"  RATIONED MALL: {item} is a finite batch until core cell producers "
        f"are live; required={target}, spare ceiling={spare_target}, mixed "
        f"provider contents are expected ({remedy})"
    )
    return True


def _start_unproduced_rationed_shortage(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    shortage: MaterialShortage, reference_point: Point, emit: Callable[[str], None],
) -> bool:
    """Open one finite producer for a conversion bill before yielding its parent.

    Conversion stages discover their material bill during submission, after
    core-mall preparation may already have queued the same target.  In that
    shape a later priority pass cannot tell the target belongs to the blocked
    conversion, so start its first unproduced rationed prerequisite now.  The
    normal loan allocator still owns capacity, material checks, and restoration.
    """
    stock = _transferable_or_available_stock(client, surface, force)
    for item, target in sorted(shortage.required.items()):
        if item not in RATIONED_MALL_BATCH_ITEMS:
            continue
        if int(stock.get(item, 0)) >= target:
            continue
        if _production_started(client, surface, force, item):
            continue
        if _rationed_mall_batch(
            client, bridge, surface, force, item, target, reference_point, emit,
        ):
            emit(
                f"  CONVERSION MATERIAL BATCH: {shortage.stage} started "
                f"{item}={target} before retrying its parent"
            )
            return True
    return False


def _ensure_mall_item(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    item: str, target: int, mall_targets: dict[str, int],
    reference_point: Point, emit: Callable[[str], None], *, background: bool,
    force_temporary: bool = False, allow_promotion: bool = True,
) -> tuple[bool, Point | None]:
    """Start or repair one mall producer, optionally without a stock wait."""
    if item not in LINE_RECIPES:
        raise StuckError(
            f"Parts mall needs {target} {item}, but no executable recipe "
            "knowledge exists for that construction item"
        )
    mode = "background reserve" if background else "stock target"
    if item == "steel-plate":
        emit(
            f"--- steel starter: ensuring dedicated one-furnace production "
            f"for {mode} {target} ---"
        )
        try:
            output = ensure_produced(
                client, bridge, surface, force, item, reference_point, emit,
                upgrade_bootstrap=True, stock_target=max(1, target),
                minimum_machines=STEEL_BASELINE_FURNACES,
                allow_promotion=False,
            )
        except ProductionPrerequisiteDeferred as deferred:
            emit(f"  STEEL STARTER DEFERRED: {deferred}")
            return False, None
        except MaterialShortage as shortage:
            add_demands(mall_targets, shortage)
            _start_unproduced_rationed_shortage(
                client, bridge, surface, force, shortage, reference_point, emit,
            )
            emit(
                f"  STEEL STARTER DEMAND: {shortage.stage} needs "
                + ", ".join(
                    f"{name}={count}"
                    for name, count in sorted(shortage.required.items())
                )
                + " -- queued"
            )
            return False, None
        return True, output
    emit(f"--- parts mall: ensuring {item} production for {mode} {target} ---")
    try:
        rationed = _rationed_mall_batch(
            client, bridge, surface, force, item, target, reference_point,
            emit, force_temporary=force_temporary,
        )
    except ProductionPrerequisiteDeferred as deferred:
        # The batch's prerequisite chain (e.g. a plate whose own foundation
        # bill is not yet affordable) is someone else's pass. Defer like the
        # steel path above; a shortage escaping here killed a run outright.
        # Chemical-ladder handoffs are NOT swallowed: the rung takes minutes
        # to establish, so per-pass retry would hot-spin borrow/restore
        # forever (2026-09-04 bulk-inserter loop). They propagate to the
        # caller, which parks them on a real retry horizon.
        if getattr(deferred, "code", "") == "chemical_capability_handoff":
            raise
        emit(f"  MALL BATCH DEFERRED: {item} -- {deferred}")
        return False, None
    except MaterialShortage as shortage:
        add_demands(mall_targets, shortage)
        emit(
            f"  MALL BATCH DEMAND: {shortage.stage} needs "
            + ", ".join(
                f"{name}={count}"
                for name, count in sorted(shortage.required.items())
            )
            + " -- queued"
        )
        return False, None
    if rationed:
        return False, None
    pipe_batch = item == "pipe" and _pipe_is_temporary_batch(
        client, surface, force,
    )
    temporary_precore = _is_pre_core_temporary_mall_item(
        client, surface, force, item,
    )
    if (
        (pipe_batch or temporary_precore)
        and not force_temporary
        and _transferable_or_available_stock(
            client, surface, force,
        ).get(item, 0) >= _rationed_mall_completion_target(
            client, surface, force, item, target,
        )
    ):
        emit(
            f"  RATIONED MALL READY: {item} batch has reached {target} "
            f"plus spares ({_rationed_mall_completion_target(client, surface, force, item, target)} "
            "transferable); no permanent cell consumed"
        )
        return True, None
    try:
        reserve = mall_reserve_for(client, surface, force, item, target)
        if temporary_precore and _scarce_metal_startup(client, surface, force):
            # Scarce opening only: a pre-core construction item is a demand
            # batch, not a one-stack standing reserve. Keep the current bill
            # plus at least a 20% spare margin (rounded up); the core mall
            # earns permanent slots after it is live.
            temporary_target = _rationed_mall_spare_target(
                client, surface, force, item, target,
            )
            stack_size = ITEM_STACK_SIZES.get(item, FALLBACK_STACK_SIZE)
            reserve = MallReserve(
                temporary_target,
                temporary_target,
                max(1, math.ceil(temporary_target / stack_size)),
                False,
            )
            emit(
                f"  MALL TEMPORARY RESERVE: {item} is limited to "
                f"need {target} + margin {temporary_target - target} "
                "until the core mall is self-sufficient"
            )
        elif temporary_precore:
            emit(
                f"  MALL STANDING RESERVE: {item} keeps its grown reserve "
                f"{reserve.storage_count} past the {target} bill now that "
                "metal flows; production stays ahead instead of stopping "
                "at need-plus-margin"
            )
        production_target = (
            reserve.storage_count
            if temporary_precore and not reserve.fill_chest
            else (
                target if reserve.fill_chest or reserve.storage_count >= target
                else reserve.storage_count
            )
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
        minimum_machines = _bootstrap_reserve_machine_target(
            client, surface, force, item, production_target, reference_point,
            emit, background=background,
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
            fill_provider=reserve.fill_chest, blocking_stock_target=target,
            minimum_machines=minimum_machines,
            allow_promotion=allow_promotion,
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
    try:
        ready, _ = _ensure_mall_item(
            client, bridge, surface, force, item, target, mall_targets,
            reference_point, emit, background=True,
        )
    except ProductionPrerequisiteDeferred as deferred:
        emit(f"  MALL BACKGROUND DEFERRED: {item} -- {deferred}")
        return True
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
    shortfall = _belt_reserve_shortfall(client, surface, force, item)
    if shortfall is None:
        return None
    ingredient, floor, held = shortfall
    return (
        f"its recipe consumes {ingredient} and only {held} remain -- "
        f"active blueprints hold the reserve until stock recovers "
        f"past {floor}"
    )


def _belt_reserve_shortfall(
    client: RconClient, surface: str, force: str, item: str,
) -> tuple[str, int, int] | None:
    """The (ingredient, floor, held) triple a belt reserve wait rests on."""
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
            return (str(ingredient), floor, int(held))
    return None


def _deliver_cell_ingredients(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    item: str, near: Point, emit: Callable[[str], None],
    requester_position: Point | None = None,
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
            chest = requester_position or live_base.requester_requesting(
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


def _queue_starved_loan_ingredients(
    client: RconClient, surface: str, force: str, item: str, target: int,
    mall_targets: dict[str, int], priorities: PriorityList,
    emit: Callable[[str], None],
) -> None:
    """Demand assembler-made ingredients a waiting loan can never receive.

    A rotating loan waits on ingredients no cell produces, and ingredient
    production needs a demand nobody queued (2026-09-04: 170 circuits locked
    in requester WIP with zero circuit producers while inserter/fast-inserter
    loans stalled 20+ min beside 4 free pool slots). Mined/external inputs
    keep their existing paths; only assembler-made orphans with no producer,
    no covering loan, and no queued demand are added -- the queue itself
    dedupes repeats. One queued ingredient per pass: the first orphan found
    wins, whether it starves the served loan or another active holder.
    """
    try:
        loans = active_bootstrap_loans(client, surface, force)
    except Exception:
        return
    own = next((loan for loan in loans if loan.target_item == item), None)
    if own is not None:
        holders = [(own, item, target)]
    elif loans:
        # A handoff waiter holds no loan of its own, so none of the holders
        # it waits on ever get their starving ingredients demanded through
        # it (2026-09-05: inserter waited on handoff while its holders'
        # circuits had no producer and no demand, freezing rotation until
        # the guard fired). Scan every active holder instead; only served
        # loans queue their own, so without this the ingredient stays
        # unqueued forever.
        holders = [
            (holder, holder.target_item, holder.target_count)
            for holder in loans
        ]
    else:
        return
    for own, item, target in holders:
        step_recipe = (
            getattr(own, "step_recipe", None)
            or getattr(own, "current_recipe", None)
            or item
        )
        spec = LINE_RECIPES.get(step_recipe)
        if spec is None:
            continue
        try:
            stock = _transferable_or_available_stock(client, surface, force)
        except Exception:
            continue
        try:
            remaining = max(0, target - int(stock.get(item, 0)))
            product_amount = max(
                1, math.floor(float(
                    LINE_RECIPES.get(item, {}).get("product_amount", 1),
                )),
            )
            crafts = max(1, math.ceil(remaining / product_amount))
        except Exception:
            continue
        for ingredient, amount in zip(
            spec.get("ingredients", ()), spec.get("amounts", ()), strict=True,
        ):
            ingredient = str(ingredient)
            if ingredient == item:
                continue
            try:
                have = int(stock.get(ingredient, 0))
            except Exception:
                continue
            if have > 0:
                continue
            if LINE_RECIPES.get(ingredient) is None:
                continue
            try:
                if _production_started(client, surface, force, ingredient):
                    continue
            except Exception:
                continue
            if any(
                getattr(loan, "target_item", None) == ingredient
                or getattr(loan, "current_recipe", None) == ingredient
                for loan in loans
            ):
                continue
            if ingredient in mall_targets:
                continue
            need = max(1, math.ceil(float(amount) * crafts))
            mall_targets[ingredient] = max(int(mall_targets.get(ingredient, 0)), need)
            try:
                tick_now = live_base.game_tick(client)
                priorities.promote(ingredient, mall_targets[ingredient], tick_now)
            except Exception:
                pass
            emit(
                f"  MALL INGREDIENT DEMAND: {item} loan waits on {ingredient} "
                f"with no producer and none spendable -- queued {ingredient}={need}"
            )
            return


def _advance_parked_chemical_rung(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    target: str, rung: str, mall_targets: dict[str, int],
    reference_point: Point, emit: Callable[[str], None],
) -> bool:
    """Give a parked mall target one real chemical-capability action.

    Parking prevents hot borrow/restore churn, but it cannot be the only
    action: otherwise no controller path creates the oil stage the target is
    waiting for. Advance the existing chemical ladder once, and expose any
    resulting construction bill to the normal mall scheduler.
    """
    try:
        output = ensure_produced(
            client, bridge, surface, force, rung, reference_point, emit,
            upgrade_bootstrap=False, stock_target=1, allow_promotion=False,
        )
    except MaterialShortage as shortage:
        add_demands(mall_targets, shortage)
        emit(
            f"  CHEMICAL HANDOFF DEMAND: {target} advanced {rung}; "
            + ", ".join(
                f"{item}={count}"
                for item, count in sorted(shortage.required.items())
            )
            + " -- queued"
        )
        return False
    except ProductionPrerequisiteDeferred as deferred:
        emit(f"  CHEMICAL HANDOFF ACTIVE: {target} advanced {rung}; {deferred}")
        return False
    return output is not None


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
        # A wait without a waiter starves: task belt recovery so the reserve
        # rebuilds instead of stalling the run (2026-09-04: splitter waited
        # 243s on belts at 26 while nothing was tasked with making belts).
        try:
            shortfall = _belt_reserve_shortfall(client, surface, force, item)
        except Exception:
            shortfall = None
        if shortfall is not None:
            ingredient, floor, _held = shortfall
            if mall_targets.get(ingredient, 0) < floor:
                mall_targets[ingredient] = floor
                try:
                    priorities.promote(ingredient, floor, tick_now)
                except Exception:
                    pass
                emit(
                    f"  BELT RECOVERY: {item} waits on {ingredient}; queued "
                    f"{ingredient}={floor} so the reserve rebuilds"
                )
        return
    emit(priorities.describe(task, tick))
    loan_revision_before = _BOOTSTRAP_LOAN_PROGRESS_REVISION
    queued_before = dict(mall_targets)
    try:
        ready, output = _ensure_mall_item(
            client, bridge, surface, force, item, target, mall_targets,
            reference_point, emit, background=False,
        )
    except ProductionPrerequisiteDeferred as deferred:
        if getattr(deferred, "code", "") != "chemical_capability_handoff":
            raise
        # A chemical rung takes minutes to establish: park the target on a
        # real retry horizon instead of re-borrowing every pass (2026-09-04:
        # bulk-inserters borrowed and restored the same cell all the way to
        # a livelock trip). A rung that already produces means stale news --
        # fall through and serve normally.
        rung = (getattr(deferred, "details", {}) or {}).get("rung")
        try:
            rung_flowing = bool(
                rung and _production_started(client, surface, force, rung)
            )
        except Exception:
            rung_flowing = False
        if rung_flowing:
            emit(
                f"  CHEMICAL HANDOFF STALE: {item} rung {rung} already "
                "produces; serving normally"
            )
            ready, output = False, None
        else:
            rung_ready = bool(rung) and _advance_parked_chemical_rung(
                client, bridge, surface, force, item, str(rung), mall_targets,
                reference_point, emit,
            )
            if rung_ready:
                emit(
                    f"  CHEMICAL HANDOFF READY: {item} rung {rung} now has "
                    "a live output; retrying the target on the next pass"
                )
                return
            priorities.defer(item, tick, str(deferred), retry_ticks=3600)
            emit(
                f"  CHEMICAL WAIT: {item} parked until {rung} establishes "
                "(retry in 60s)"
            )
            return
    if not ready:
        stock = _transferable_or_available_stock(client, surface, force)
        if _drain_aware_pop_due(
            client, surface, force, item, target,
            int(stock.get(item, 0)),
            loan_advanced_this_pass=(
                _BOOTSTRAP_LOAN_PROGRESS_REVISION > loan_revision_before
            ),
        ):
            priorities.complete(item, live_base.game_tick(client))
            mall_targets.pop(item, None)
            emit(
                f"  DRAIN-AWARE POP: {item} met its bill of {target} "
                f"transferable while its loan keeps advancing -- a live consumer "
                f"is eating output as fast as it is made, so the demand retires "
                f"and the cell finishes spares in the background"
            )
            return
        _queue_starved_loan_ingredients(
            client, surface, force, item, target, mall_targets,
            priorities, emit,
        )
        try:
            done_at = _rationed_mall_completion_target(
                client, surface, force, item, target,
            )
            have = int(stock.get(item, 0))
            if have < done_at and hasattr(client, "command"):
                try:
                    available = int(
                        live_base.available_items(
                            client, surface, force,
                        ).get(item, 0)
                    )
                except Exception:
                    available = have
                wip = max(0, available - have)
                ghosts = 0
                try:
                    ghosts = int(
                        live_base.pending_ghost_count(client, surface, force) or 0
                    )
                except Exception:
                    ghosts = 0
                emit(
                    f"  LAGGING BUILD: {item} holds {have}/{done_at} transferable "
                    f"(bill {target} + spares); {wip} locked in requester/buffer "
                    f"WIP, {ghosts} pending ghost(s) -- "
                    "missing items keep this demand queued while the mall tops up"
                )
        except Exception:
            pass
        actual_prerequisites = _queued_mall_prerequisites(item)
        # A producer build can discover construction material outside its
        # recipe closure (the pre-steel starter's local small poles, for
        # example).  Those targets were not known when this task was picked,
        # but are just as binding as a declared cell bill: retrying the parent
        # first would otherwise keep its new shortage at priority zero.
        actual_prerequisites.update(
            other for other, queued_target in mall_targets.items()
            if other != item and queued_target > queued_before.get(other, 0)
        )
        other_pending = {
            other for other, target in mall_targets.items()
            if (
                other != item
                and other in actual_prerequisites
                and stock.get(other, 0) < target
            )
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
    if output is None:
        if ready:
            priorities.complete(item, live_base.game_tick(client))
            mall_targets.pop(item, None)
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


def _queued_mall_prerequisites(item: str) -> set[str]:
    """Recipe and declared cell-bill prerequisites that may outrank ``item``.

    Unrelated construction batches are peers, not prerequisites. Treating every
    queued item as a dependency made drills wait for splitters while splitters
    simultaneously waited for the active drill loan.
    """
    pending = list(LINE_RECIPES.get(item, {}).get("ingredients", ()))
    prerequisites: set[str] = set()
    while pending:
        ingredient = str(pending.pop())
        if ingredient in prerequisites or ingredient == item:
            continue
        prerequisites.add(ingredient)
        pending.extend(LINE_RECIPES.get(ingredient, {}).get("ingredients", ()))
    ledger = _MATERIAL_RESERVATION_LEDGER
    if ledger is not None:
        project = ledger.projects.get(_material_project_id(item))
        if project is not None:
            prerequisites.update(project.required)
    return prerequisites


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
    lifecycle = _bootstrap_state(short_plate)
    owned_live = (
        len(_live_bootstrap_replacement_furnaces(
            client, surface, short_plate, lifecycle,
        ))
        if lifecycle is not None and lifecycle.lifecycle_state != "pioneer"
        else 0
    )
    have = max(plate_line.machine_count if plate_line else 0, owned_live)
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
        if deferred.state in {
            "constructing", "coverage_wait", "power_wait", "producing",
        }:
            emit(
                f"  PREP {deferred.state.upper()}: {short_plate} foundation -- "
                f"{deferred}; holding startup instead of advancing the goal"
            )
            consume_wait(f"{deferred.state}_{short_plate}_foundation")
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


def _live_bootstrap_replacement_furnaces(
    client: RconClient, surface: str, recipe: str,
    lifecycle: BootstrapDistrictState | None = None,
) -> tuple[Point, ...]:
    """Return only real furnaces at the district's exact owned positions."""
    lifecycle = lifecycle or _bootstrap_state(recipe)
    if lifecycle is None:
        return ()
    machine = LINE_RECIPES[recipe]["machine"]
    owned = tuple(
        (float(action["position"]["x"]), float(action["position"]["y"]))
        for action in lifecycle.replacement_actions
        if action.get("entity") == machine
    )
    live_positions = live_base.live_entity_positions(
        client, surface, lifecycle.force, machine, owned,
    )
    return tuple(position for position in owned if position in live_positions)


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
    lifecycle = _bootstrap_state(recipe)
    if lifecycle is not None:
        # A district owns exact replacement positions. Never merge nearby idle
        # furnaces: that let the stone pioneer adopt copper's six-furnace block
        # and enter retirement before stone provisioning had even begun.
        if lifecycle.lifecycle_state == "pioneer":
            return False
        positions = _live_bootstrap_replacement_furnaces(
            client, surface, recipe, lifecycle,
        )
        required = PLATE_FOUNDATION_FURNACES[recipe]
        if len(positions) < required:
            return False
        return bool(_complete_six_furnace_candidates(tuple(sorted(positions))))

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
        lifecycle = _bootstrap_state(plate)
        if (
            lifecycle is not None
            and lifecycle.lifecycle_state in {
                "provisioning", "validating", "retiring",
            }
            and lifecycle.replacement_submitted
        ):
            # Real furnace bodies may finish before their ore route and output
            # support.  Structural readiness must not bypass the persisted
            # construction reconciliation and spin on a zero output counter.
            try:
                _reconcile_submitted_bootstrap_replacement(
                    client, bridge, surface, force, lifecycle, emit,
                )
            except ProductionPrerequisiteDeferred as deferred:
                # Reconciliation deliberately advances ghost-built coverage
                # one powered roboport wave at a time. That typed deferral is
                # construction in progress, not an episode failure; retain the
                # persisted district and retry it after the normal bounded poll.
                emit(
                    f"  PLATE FOUNDATION CONSTRUCTING: {plate} submitted "
                    f"replacement waits: {deferred}; holding startup"
                )
                consume_wait(f"submitted_{plate}_foundation")
                time.sleep(_PENDING_FOUNDATION_POLL_SECONDS)
                return True
            except MaterialShortage as shortage:
                # A submitted district owns its exact ghosts, but it does not
                # own construction stock that another project may have
                # consumed while bots were working.  Keep the persisted
                # replacement authoritative and hand the missing item back to
                # the mall instead of aborting the whole controller pass.
                add_demands(mall_targets, shortage)
                _mark_binding_demands(shortage)
                emit(
                    f"  PLATE FOUNDATION DEMAND: {plate} submitted replacement "
                    "needs "
                    + ", ".join(
                        f"{item}={target}"
                        for item, target in sorted(shortage.required.items())
                    )
                    + " -- queued for the mall"
                )
                return False
            return True
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
        # Fresh plates start with the beltless direct seed: with zero belt
        # stock the full foundation bill is unaffordable before first plates,
        # so the seed breaks the belt/plate circle (2026-09-04: skipping it
        # stalled the mall-first opening at +73s with nothing producing
        # anything). The mall cells are already placed by the earlier prep
        # step, and the seed self-retires once its foundation validates.
        if lifecycle is not None and lifecycle.lifecycle_state == "released":
            error = BootstrapLifecycleError(
                f"released {plate} replacement is no longer a complete real "
                "foundation; refusing to recreate its pioneer"
            )
            raise _bootstrap_lifecycle_stuck(plate, error) from error
        if lifecycle is not None and lifecycle.lifecycle_state != "pioneer":
            # Provisioning/validation owns recovery of the persisted district.
            # Opening another temporary producer would violate that ownership.
            continue
        try:
            _bootstrap_direct_plate_line(
                client, bridge, surface, force, plate, reference_point, emit,
            )
        except ProductionPrerequisiteDeferred as deferred:
            if deferred.code != "roboport_coverage_construction_wait":
                raise
            emit(
                f"  PLATE STARTER COVERAGE WAIT: {plate} -- {deferred}; "
                "re-observing the bot-built roboport wave before retrying"
            )
            consume_wait(f"coverage_{plate}_starter")
            time.sleep(_PENDING_FOUNDATION_POLL_SECONDS)
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
            minimum_machines=1, allow_promotion=False, temporary_mall=True,
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
        getattr(line, "working_count", 0) > 0
        or getattr(line, "produced_count", 0) > 0
    )


def _core_mall_prerequisites(
    client: RconClient, surface: str, force: str, item: str,
) -> tuple[str, ...]:
    """Return the unstarted inputs that must precede a logistic chest cell.

    The recipe catalog is installed at run start, so the gate follows the
    live recipe rather than assuming every modded chest has the same bill.
    ``steel-chest`` and ``advanced-circuit`` are ordered deliberately because
    the latter must not be admitted until ``ensure_produced`` has walked the
    oil/plastic chemical ladder.
    """
    if item not in _CORE_MALL_RECIPE_ITEMS:
        return ()
    spec = LINE_RECIPES.get(item)
    if spec is None:
        return ()
    ingredient_amounts = {
        str(ingredient): int(amount)
        for ingredient, amount in zip(
            spec.get("ingredients", ()), spec.get("amounts", ()), strict=True,
        )
    }
    stock: dict[str, int] | None = None
    prerequisites: list[str] = []
    for prerequisite in _CORE_MALL_PREREQUISITE_ORDER:
        if prerequisite not in ingredient_amounts:
            continue
        if _production_started(client, surface, force, prerequisite):
            continue
        if prerequisite in _CORE_MALL_TEMPORARY_PREREQUISITES:
            # Query inventory only when the temporary-batch exception is
            # relevant. Advanced-circuit-only callers should stay a pure
            # producer-state check and avoid an unnecessary RCON round trip.
            if stock is None:
                stock = live_base.available_items(client, surface, force)
            if stock.get(prerequisite, 0) >= ingredient_amounts[prerequisite]:
                continue
        if (
            prerequisite == "steel-chest"
            and not _production_started(client, surface, force, "steel-plate")
        ):
            # A steel-chest batch is deliberately temporary, but its steel
            # input is not.  Establish the dedicated furnace capability
            # before borrowing a mall cell, otherwise that loan can only
            # request zero steel and block the chemical ladder from opening
            # the source that would satisfy it.
            prerequisites.append("steel-plate")
        prerequisites.append(prerequisite)
    return tuple(prerequisites)


def _prepare_core_mall_prerequisite(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    item: str, mall_targets: dict[str, int], reference_point: Point,
    emit: Callable[[str], None],
) -> bool | None:
    """Start one missing chest prerequisite and spend the current pass.

    ``True`` means a stage or wait consumed this pass; ``False`` hands a
    material shortage to the mall in the same pass; ``None`` means the core
    recipe is admitted. Steel chests are a finite borrowed-mall batch; the
    advanced-circuit prerequisite remains a dedicated capability line.
    """
    prerequisites = _core_mall_prerequisites(client, surface, force, item)
    if not prerequisites:
        return None
    prerequisite = prerequisites[0]
    emit(
        f"  CORE MALL WAIT: {item} is gated on {prerequisite}; "
        "establishing the prerequisite before admitting its recipe"
    )
    try:
        # Steel chests must stay inside the temporary mall until it is
        # self-sustaining. In particular, never upgrade this one-chest seed
        # into the generic conversion layout: that layout rate-sizes several
        # requester feeders for two assemblers. Advanced circuits retain the
        # dedicated path so their oil/plastic prerequisite ladder is enforced.
        ensure_produced(
            client, bridge, surface, force, prerequisite, reference_point,
            emit,
            upgrade_bootstrap=prerequisite not in _CORE_MALL_TEMPORARY_PREREQUISITES,
            temporary_mall=prerequisite in _CORE_MALL_TEMPORARY_PREREQUISITES,
            stock_target=1,
            minimum_machines=1, allow_promotion=False,
        )
    except MaterialShortage as shortage:
        add_demands(mall_targets, shortage)
        emit(
            f"  CORE MALL WAIT: {item} cannot fund {prerequisite}; "
            + ", ".join(
                f"{name}={count}"
                for name, count in sorted(shortage.required.items())
            )
            + " -- handing the pass to the mall"
        )
        return False
    except ProductionPrerequisiteDeferred as deferred:
        emit(
            f"  CORE MALL WAIT: {item} prerequisite {prerequisite} -- "
            f"{deferred}"
        )
        return True
    return True


def _baseline_recipe_ready(
    client: RconClient, surface: str, force: str, recipe: str,
) -> bool:
    """Whether baseline prep can build `recipe` without recursive bootstrap."""
    return all(
        _production_started(client, surface, force, ingredient)
        for ingredient in LINE_RECIPES[recipe]["ingredients"]
    )


def _reclaim_spent_demand_slot_for_prep(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, reference_point: Point, emit: Callable[[str], None],
    mall_targets: dict[str, int], *, minimum_machines: int,
) -> bool:
    """Convert one spent demand-owned cell in place to a slot-capped standing recipe.

    The eight-slot pool fills with demand-owned cells whose demands complete
    (drills, assemblers), and then a standing prep cell can never claim a
    slot: the cap deferral spins forever because no line promotion frees one
    (2026-09-04: circuit prep blocked at 8/8 while the drill/AM1 cells sat
    idle with their demands met). A cell is convertible only when it makes a
    rationed batch item outside the standing/anchor/core sets, hosts no loan,
    has no queued demand, and sits idle with stock on hand. Conversion
    reconfigures the existing assembler, requester section, provider, and
    stock gate in place -- no ghosts, no churn -- and leaves a paired
    companion half untouched. Returns whether a conversion was submitted.
    """
    if mall_slot_count(client, surface, reference_point) < BOOTSTRAP_MALL_SLOT_TARGET:
        return False
    try:
        stock = _transferable_or_available_stock(client, surface, force)
    except Exception:
        return False
    try:
        busy_origins = {
            _loan_cell_origin(loan)
            for loan in active_bootstrap_loans(client, surface, force)
        }
    except Exception:
        busy_origins = set()
    for donor in sorted(RATIONED_MALL_BATCH_ITEMS):
        if donor in BASELINE_MACHINES or donor in _MALL_RECIPE_ANCHORS:
            continue
        if donor in CORE_MALL_PRODUCERS or donor in PERSISTENT_INTERMEDIATES:
            continue
        if donor in mall_targets:
            continue
        try:
            if int(stock.get(donor, 0)) < 1:
                continue
        except Exception:
            continue
        spec = LINE_RECIPES.get(donor)
        if spec is None:
            continue
        try:
            line = live_base.find_line(
                client, surface, force, donor, str(spec["machine"]),
                include_ghosts=False,
            )
        except Exception:
            continue
        if line is None or line.machine_count < 1:
            continue
        if getattr(line, "working_count", 1) > 0:
            continue
        for machine_position in line.machine_positions:
            try:
                located = locate_mall_cell(machine_position, reference_point)
            except Exception:
                continue
            if located is None:
                continue
            origin, side = located
            if origin in busy_origins:
                continue
            return _convert_mall_cell_to_recipe(
                client, bridge, surface, force, donor, machine_position,
                origin, side, recipe, reference_point, emit,
                minimum_machines=minimum_machines,
            )
    return False


def _convert_mall_cell_to_recipe(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    donor: str, machine_position: Point, origin: tuple[int, int], side: str,
    recipe: str, reference_point: Point, emit: Callable[[str], None], *,
    minimum_machines: int,
) -> bool:
    """Reconfigure one demand-owned half in place to a standing recipe."""
    requester_position = (origin[0] + 4.5, origin[1] + 1.5)
    try:
        machine = live_base.entity_at(client, surface, machine_position)
        requester = live_base.entity_at(client, surface, requester_position)
    except Exception:
        return False
    if (
        not machine or machine["name"] not in live_base.ASSEMBLER_TIERS
        or not requester or requester["name"] != "requester-chest"
    ):
        return False
    machine_name = str(machine["name"])
    spec = LINE_RECIPES.get(recipe)
    if spec is None:
        return False
    requests = recipe_group_requests(spec["ingredients"], spec["amounts"])
    requester_action = {
        "action_type": "configure_entity",
        "entity": "requester-chest",
        "position": {"x": requester_position[0], "y": requester_position[1]},
        "clear_logistic_groups": [
            recipe_group_name(donor),
            recipe_group_name(donor, side),
        ],
        "logistic_sections": [{
            "group": recipe_group_name(recipe, side),
            "requests": requests,
            "multiplier": standard_mall_request_multiplier(
                machine_name, spec["craft_time"],
            ),
        }],
    }
    gate_action = generate_mall_stock_gate_update(
        recipe, machine_name, [machine_position], max(1, minimum_machines),
    )["phases"][0]["actions"]
    actions: list[dict] = [requester_action]
    actions.extend(
        {**action, "action_type": "configure_entity"} for action in gate_action
    )
    try:
        provider_position = _paired_mall_provider(
            client, surface, [machine_position],
        )
    except Exception:
        provider_position = None
    if provider_position is not None:
        try:
            shared = mall_slot_uses_shared_provider(
                client, surface, machine_position, reference_point,
            )
        except Exception:
            shared = True
        if shared:
            # One chest serves both halves: a count limit names one item, so
            # shared providers stay open and the canonical limit is untouched.
            provider_action = generate_mall_provider_limit_update(
                recipe, provider_position, 0, fill_chest=True,
            )["phases"][0]["actions"][0]
        else:
            provider_action = generate_mall_provider_limit_update(
                recipe, provider_position,
                _canonical_mall_provider_limit(
                    surface, force, recipe, max(1, minimum_machines),
                ),
            )["phases"][0]["actions"][0]
        actions.append({**provider_action, "action_type": "configure_entity"})
    plan = {"phases": [{
        "name": f"reclaim_mall_{donor}_to_{recipe}",
        "actions": actions,
    }]}
    plan["surface"], plan["force"] = surface, force
    _submit(
        client, bridge, surface, plan, f"reclaim_mall_{donor}_to_{recipe}",
        emit,
    )
    _MALL_REFRESH_SIGNATURES.clear()
    emit(
        f"  MALL SLOT RECLAIM: converted the spent {donor} demand cell at "
        f"{machine_position} in place to standing {recipe}; no new slot spent"
    )
    return True


def _prep_reclaims_capped_slot(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, reference_point: Point, emit: Callable[[str], None],
    mall_targets: dict[str, int], deferred: ProductionPrerequisiteDeferred,
    *, minimum_machines: int,
) -> bool | None:
    """Try slot reclaim when a standing prep cell hits the pool cap.

    Returns True when a conversion was submitted (pass spent, re-survey next
    pass), False when the mall should take the pass (unreclaimable cap still
    allows loan rotation inside the pool), and None when this deferral is
    not a cap block at all.
    """
    if getattr(deferred, "code", "") != "bootstrap_mall_slot_cap":
        return None
    if _reclaim_spent_demand_slot_for_prep(
        client, bridge, surface, force, recipe, reference_point, emit,
        mall_targets, minimum_machines=minimum_machines,
    ):
        return True
    emit(
        f"  PREP CAPPED: {recipe} needs a slot and no spent demand cell is "
        "reclaimable -- handing the pass to the mall"
    )
    return False


def _prep_intermediate(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    prepped: set[str], mall_targets: dict[str, int], reference_point: Point,
    emit: Callable[[str], None],
) -> bool:
    """Fill the next standing mall cell in the prep set, deepest feeder first.

    The opening six build mall-first: every pending baseline recipe is
    attempted immediately, with no wait for its ingredients to produce. Plate
    and construction shortages route to the mall and the plate foundations
    through the usual shortage/deferral handoffs below.

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
            try:
                ensure_produced(
                    client, bridge, surface, force, recipe, reference_point, emit,
                    upgrade_bootstrap=False, stock_target=wanted,
                    minimum_machines=wanted, allow_promotion=False,
                )
            except MaterialShortage as shortage:
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
                # ensure_produced may have reconfigured this existing cell as
                # a rotating bootstrap loan. That is successful work for this
                # pass; re-observe it on the next pass instead of treating its
                # supply-wait signal as an unhandled controller failure.
                reclaimed = _prep_reclaims_capped_slot(
                    client, bridge, surface, force, recipe, reference_point,
                    emit, mall_targets, deferred, minimum_machines=wanted,
                )
                if reclaimed is None:
                    emit(f"  PREP WAIT: {recipe} -- {deferred}")
                    return True
                return bool(reclaimed)
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
            # A pool-cap block instead tries slot reclaim before yielding.
            reclaimed = _prep_reclaims_capped_slot(
                client, bridge, surface, force, recipe, reference_point,
                emit, mall_targets, deferred, minimum_machines=wanted,
            )
            if reclaimed is None:
                emit(f"  PREP BLOCKED: {recipe} -- {deferred}")
            return bool(reclaimed)
        return True  # built one stage (or all of it); re-survey and carry on
    return False


def _prep_core_mall(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    prepped: set[str], mall_targets: dict[str, int], reference_point: Point,
    emit: Callable[[str], None],
) -> bool:
    """Promote the rationed mall into five self-sustaining core cells."""
    for item in CORE_MALL_PRODUCERS:
        key = f"_core_mall:{item}"
        if key in prepped:
            continue
        if _production_started(client, surface, force, item):
            prepped.add(key)
            emit(f"  CORE MALL READY: {item} has independent production")
            return True
        emit(f"--- core mall promotion: permanent {item} producer ---")
        prerequisite_pass = _prepare_core_mall_prerequisite(
            client, bridge, surface, force, item, mall_targets,
            reference_point, emit,
        )
        if prerequisite_pass is not None:
            return prerequisite_pass
        # Core promotion is the one pre-logistics demand that must be able to
        # reclaim capacity from the hard eight-slot pool.  A stocked seed item
        # would otherwise make _rationed_mall_batch return early, after which
        # _build_assembled_stage sees 10/10 and defers forever.  Force the
        # rotating-batch path so it can borrow an existing non-anchor cell;
        # completion promotes that same cell in _submit_bootstrap_loan.
        if mall_slot_count(
            client, surface, reference_point,
        ) >= BOOTSTRAP_MALL_SLOT_TARGET:
            try:
                if _rationed_mall_batch(
                    client, bridge, surface, force, item, 1,
                    reference_point, emit, force_temporary=True,
                ):
                    return True
            except MaterialShortage as shortage:
                add_demands(mall_targets, shortage)
                emit(
                    f"  CORE MALL WAIT: {item} reserves its complete cell; "
                    + ", ".join(
                        f"{name}={count}"
                        for name, count in sorted(shortage.required.items())
                    )
                    + " -- handing the pass to the mall"
                )
                return False
            except ProductionPrerequisiteDeferred as deferred:
                # Recipe-loan handoffs are expected live transitions.  The
                # forced path used to let this signal escape and terminate the
                # controller before it could re-observe the restored cell.
                emit(f"  CORE MALL BATCH: {item} waits while {deferred}")
                return True
        try:
            ensure_produced(
                client, bridge, surface, force, item, reference_point, emit,
                upgrade_bootstrap=False, stock_target=1,
                minimum_machines=1, allow_promotion=False,
            )
        except MaterialShortage as shortage:
            add_demands(mall_targets, shortage)
            emit(
                f"  CORE MALL WAIT: {item} reserves its complete cell; "
                "rationed batches will make "
                + ", ".join(
                    f"{name}={count}"
                    for name, count in sorted(shortage.required.items())
                )
            )
            return False
        except ProductionPrerequisiteDeferred as deferred:
            emit(f"  CORE MALL BATCH: {item} waits while {deferred}")
            return True
        return True
    return False


def _upgrade_bootstrap_mall(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    mall_targets: dict[str, int], reference_point: Point,
    emit: Callable[[str], None],
) -> bool:
    """Self-fund and order in-place tier upgrades for the compact mall.

    The controller starts exclusively from assembler-1s and regular inserters.
    Once their permanent upgrade producers have demonstrably run, it holds a
    small construction reserve and consumes only the surplus in exact native
    bot upgrade orders. Pending orders are excluded from the next survey.
    """
    stock = live_base.available_items(client, surface, force)
    upgrades = (
        ("assembling-machine-1", "assembling-machine-2"),
        ("inserter", "fast-inserter"),
    )
    for source, target in upgrades:
        if not _production_started(client, surface, force, target):
            continue
        positions = mall_entity_positions(
            client, surface, force, reference_point, source,
        )
        if not positions:
            continue
        wanted = len(positions) + UPGRADE_RESERVE
        held = int(stock.get(target, 0))
        if held <= UPGRADE_RESERVE:
            if mall_targets.get(target, 0) < wanted:
                mall_targets[target] = wanted
                emit(
                    f"  MALL UPGRADE STOCK: {len(positions)} {source} await "
                    f"native replacement; stocking {target} to {wanted}"
                )
            return False
        selected = list(positions[:min(len(positions), held - UPGRADE_RESERVE)])
        if not selected:
            return False
        block = f"bootstrap-mall-{source}-to-{target}"
        plan = entity_upgrade_plan(selected, source=source, target=target)
        for action in plan["actions"]:
            action["block"] = block
        authorization = {
            "approved_actions": ["apply_upgrades"],
            "denied_actions": [],
            "scope_limits": {
                "max_count": len(selected),
                "block_filter": [block],
            },
            "authorization_timestamp": datetime.now(timezone.utc).isoformat(),
            "authorization_source": "policy",
        }
        report = load_json(bridge.execute_upgrade_plan(
            authorization, plan, surface=surface, force=force,
        ))
        failed = [
            action for action in report.get("actions", ())
            if action.get("status") not in {"success", "skipped"}
        ]
        if failed:
            raise StuckError(
                f"Native mall upgrade {source} -> {target} rejected "
                f"{len(failed)}/{len(selected)} exact order(s)",
                code="mall_upgrade_rejected",
                classification="bug",
                details={"source": source, "target": target, "failures": failed},
            )
        emit(
            f"  MALL NATIVE UPGRADE: ordered {len(selected)} exact "
            f"{source} -> {target} replacement(s); recipes and wiring stay in place"
        )
        return True
    return False


def _prep_post_metal_stack_reserves(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    prepped: set[str], mall_targets: dict[str, int],
    reference_point: Point, emit: Callable[[str], None],
) -> None:
    """Keep circuits and splitters filling while stone opens concurrently.

    The opening iron/copper projects consume their small finite splitter batch.
    Start one reserve producer per pass so replenishment overlaps stone
    planning and construction.  Actual stone bills remain normal blocking
    mall targets and can preempt these non-binding batches.
    """
    if not _metal_starter_transition_complete(client, surface, force):
        return
    if mall_targets:
        # Critical construction work owns the controller pass.  A previously
        # started reserve loan keeps crafting in Factorio and can be preempted
        # normally; it does not need another controller poll first.
        return
    stock = live_base.available_items(client, surface, force)
    for item in _POST_METAL_STACK_RESERVES:
        key = f"_post_metal_stack:{item}"
        if key in prepped:
            continue
        target = _POST_METAL_RESERVE_TARGETS.get(
            item, ITEM_STACK_SIZES.get(item, FALLBACK_STACK_SIZE),
        )
        if int(stock.get(item, 0)) >= target:
            prepped.add(key)
            emit(
                f"  POST-METAL RESERVE READY: {item} reached reserve "
                f"({target})"
            )
            continue
        emit(
            f"  POST-METAL RESERVE: iron/copper transition is complete; "
            f"stocking {item} to reserve ({target}) alongside stone"
        )
        _ensure_mall_item(
            client, bridge, surface, force, item, target, mall_targets,
            reference_point, emit, background=True,
        )
        return


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


def _metal_science_transition_status(
    client: RconClient, surface: str, force: str,
) -> tuple[bool, dict[str, object]]:
    """Whether science can safely overlap final metal-starter retirement.

    Measured output from the exact lifecycle-owned replacement is stronger
    evidence than an unspent material reservation: it proves the complete
    mine/refinery bill was funded, built, powered, and supplied. Full spatial
    reservations and the exact six-furnace shape must still agree, so output
    from an unrelated line cannot release the gate.
    """
    if _metal_starter_transition_complete(client, surface, force):
        return True, {"mode": "starters_retired", "districts": {}}
    districts: dict[str, dict[str, object]] = {}
    for recipe in ("iron-plate", "copper-plate"):
        state = _bootstrap_state(recipe)
        foundation_ready = _direct_plate_foundation_ready(
            client, surface, force, recipe,
        )
        measured_output = state.measured_output_count if state is not None else 0
        if state is not None and state.replacement_actions:
            try:
                measured_output = max(
                    _measured_bootstrap_replacement_output(
                        client, surface, recipe, state,
                    ),
                    state.measured_output_count,
                )
            except Exception:
                # Dry harnesses and an interrupted live survey may lack
                # progress counters. Persisted monotonic output remains
                # valid evidence, but absence is never guessed healthy.
                measured_output = max(
                    measured_output, state.measured_output_count,
                )
        elif state is None:
            # Only unmanaged compatibility calls may use recipe-wide output.
            # Managed episodes must prove output at the persisted replacement.
            line = live_base.find_line(
                client, surface, force, recipe,
                LINE_RECIPES[recipe]["machine"],
            )
            measured_output = int(getattr(line, "produced_count", 0) or 0)
        lifecycle_owned = bool(
            state is not None
            and state.lifecycle_state != "pioneer"
            and set(state.reservations) == REQUIRED_RESERVATION_ROLES
            and all(state.reservations.values())
        )
        # Direct calls without an episode ledger cannot persist ownership.
        # Preserve that legacy path only after the physical replacement has
        # produced; managed episodes require complete lifecycle reservations.
        reservation_sufficient = lifecycle_owned or (
            _BOOTSTRAP_DISTRICT_LEDGER is None and state is None
        )
        healthy = bool(
            foundation_ready
            and measured_output > 0
            and reservation_sufficient
        )
        if not reservation_sufficient:
            remedy = "reserve_district"
        elif not foundation_ready:
            remedy = "construct_replacement"
        elif measured_output <= 0:
            remedy = "repair_power_or_transport"
        else:
            remedy = "retire_pioneer"
        districts[recipe] = {
            "healthy": healthy,
            "lifecycle_state": (
                state.lifecycle_state if state is not None else "untracked"
            ),
            "reservation_sufficient": reservation_sufficient,
            "foundation_ready": foundation_ready,
            "measured_output_count": measured_output,
            "remedy": remedy,
        }
    return all(
        bool(district["healthy"]) for district in districts.values()
    ), {"mode": "transition_health", "districts": districts}


def _ensure_automation_science_transition(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    reference_point: Point, emit: Callable[[str], None],
) -> None:
    """Release science on healthy replacement output or act on one blocker."""
    healthy, status = _metal_science_transition_status(
        client, surface, force,
    )
    if healthy:
        emit(
            "  AUTOMATION SCIENCE TRANSITION: "
            + json.dumps(status, sort_keys=True, separators=(",", ":"))
        )
        return
    districts = status.get("districts", {})
    blocked_recipe, blocked = next(
        (recipe, detail)
        for recipe, detail in districts.items()
        if not detail["healthy"]
    )
    emit(
        "  AUTOMATION SCIENCE TRANSITION BLOCKED: "
        + json.dumps(
            {"recipe": blocked_recipe, **blocked},
            sort_keys=True, separators=(",", ":"),
        )
    )
    try:
        build_mining_stage(
            client, bridge, surface, force, blocked_recipe,
            reference_point, emit, require_direct=True,
        )
    except ProductionPrerequisiteDeferred as deferred:
        raise ProductionPrerequisiteDeferred(
            f"automation-science-pack is remedying {blocked_recipe} "
            f"transition ({blocked['remedy']}): {deferred}",
            code=deferred.code,
            classification=deferred.classification,
            state=deferred.state,
            details={
                "recipe": blocked_recipe, "remedy": blocked["remedy"],
                **deferred.details,
            },
        ) from deferred
    raise ProductionPrerequisiteDeferred(
        f"automation-science-pack applied {blocked['remedy']} for "
        f"{blocked_recipe}; resurveying transition health",
        code="bootstrap_transition_resurvey",
        state="constructing",
        details={"recipe": blocked_recipe, "remedy": blocked["remedy"]},
    )


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


#: Construction consumables that must never run dry without a producer.
#: When transferable stock hits the floor with no live line and no active
#: loan, the mall re-queues the standing target on its own instead of waiting
#: for a new bill (2026-09-03: science drew belts to zero after the last loan
#: restored; nothing re-queued and the run died with a full mall).
_EVERGREEN_STOCK = {
    # item: (standing target, restock floor)
    "transport-belt": (200, 25),
}


def _evergreen_producer_live(
    client: RconClient, surface: str, force: str, item: str,
) -> bool:
    """Whether anything is currently making `item`: a live line of any
    assembler tier, or an active bootstrap loan targeting or making it."""
    try:
        spec = LINE_RECIPES.get(item)
        if spec is not None:
            line = live_base.find_line(
                client, surface, force, item, str(spec["machine"]),
            )
            if line is not None and line.machine_count > 0:
                return True
    except Exception:
        pass
    try:
        for loan in active_bootstrap_loans(client, surface, force):
            if (
                getattr(loan, "target_item", None) == item
                or getattr(loan, "current_recipe", None) == item
            ):
                return True
    except Exception:
        pass
    return False


def _loan_craft_proof_complete(
    client: RconClient, surface: str, force: str, item: str,
    stock: dict[str, int], loans: list | None = None,
) -> bool:
    """Whether loan craft evidence proves `item`'s bill was made.

    Transferable stock can sit below the bill forever while bots drain output
    as fast as it is made or requester WIP siphons it (2026-09-03: circuit
    batch held at 195/200 with 20 WIP-locked while crafts advanced past 400).
    A loan whose monotonic counters prove the blocking bill is complete work
    done; the demand retires and re-queues naturally if new need arises.
    Pass pre-fetched loans to avoid one RCON scan per queued item.
    """
    if loans is None:
        try:
            loans = active_bootstrap_loans(client, surface, force)
        except Exception:
            return False
    actual = {item: int(stock.get(item, 0))}
    for loan in loans:
        if getattr(loan, "target_item", None) != item:
            continue
        if int(stock.get(item, 0)) >= loan.target_count:
            return True
        if (
            loan.production_target > loan.target_count
            and (loan.step_minimum_crafts or -1) == 0
        ):
            return True
        try:
            products = _bootstrap_loan_products_finished(client, surface, loan)
        except Exception:
            continue
        try:
            if _bootstrap_loan_minimum_fulfilled(loan, actual, products):
                return True
        except Exception:
            continue
    return False


#: Monotonic crafts already consumed as bill proof, per item. A lingering
#: spare-phase loan must not re-prove a freshly re-queued demand with the
#: same counters (2026-09-04: post-metal circuit reserve re-queued every
#: pass and was instantly popped by the still-active loan, hiding the very
#: task that would service it, until the run starved with no producer).
_CRAFT_PROOF_CONSUMED: dict[str, int] = {}


def _loan_craft_proof_crafts(
    client: RconClient, surface: str, force: str, item: str,
    stock: dict[str, int], loans: list | None = None,
) -> int:
    """Monotonic production backing a craft-proof pop, or 0 when unproven."""
    if not _loan_craft_proof_complete(
        client, surface, force, item, stock, loans=loans,
    ):
        return 0
    proved = int(stock.get(item, 0))
    if loans is None:
        try:
            loans = active_bootstrap_loans(client, surface, force)
        except Exception:
            loans = []
    for loan in loans or ():
        if getattr(loan, "target_item", None) != item:
            continue
        try:
            products = _bootstrap_loan_products_finished(client, surface, loan)
        except Exception:
            continue
        if products is not None:
            proved = max(proved, int(products))
    return proved


def _survey_pass(
    client: RconClient, surface: str, force: str, mall_targets: dict[str, int],
    priorities: PriorityList, prepped: set[str] | frozenset[str] = frozenset(),
) -> tuple[int, object | None]:
    """Read the base, retire targets stock already covers, pick the next task.

    Targets are dropped as soon as stock meets them so the priority list does
    not keep re-selecting work the base already finished while a later pass was
    running.
    """
    stock = _transferable_or_available_stock(client, surface, force)
    tick = live_base.game_tick(client)
    priorities.sync(mall_targets, stock, tick)
    # Binding demands outrank standing reserves while they block placed
    # ghosts; entries retire with their demand so a past bottleneck cannot
    # pin scheduling forever.
    _BLOCKING_MALL_ITEMS.intersection_update(mall_targets)
    promote = getattr(priorities, "promote", None)
    if promote is not None:
        for binding in sorted(_BLOCKING_MALL_ITEMS):
            promote(binding, mall_targets[binding], tick)
    _DRAIN_WATCH_PREVIOUS_TRANSFERABLE.clear()
    _DRAIN_WATCH_PREVIOUS_TRANSFERABLE.update(_DRAIN_WATCH_LAST_TRANSFERABLE)
    _DRAIN_WATCH_LAST_TRANSFERABLE.clear()
    for stocked_item in mall_targets:
        _DRAIN_WATCH_LAST_TRANSFERABLE[(surface, force, stocked_item)] = int(
            stock.get(stocked_item, 0)
        )
    _survey_loans: list | None = None
    for stocked_item, stocked_target in list(mall_targets.items()):
        done_at = _rationed_mall_completion_target(
            client, surface, force, stocked_item, stocked_target,
        )
        if stock.get(stocked_item, 0) >= done_at:
            priorities.complete(stocked_item, tick)
            mall_targets.pop(stocked_item)
            continue
        if _survey_loans is None:
            try:
                _survey_loans = active_bootstrap_loans(client, surface, force)
            except Exception:
                _survey_loans = []
        if _loan_craft_proof_complete(
            client, surface, force, stocked_item, stock,
            loans=_survey_loans,
        ):
            proved = _loan_craft_proof_crafts(
                client, surface, force, stocked_item, stock,
                loans=_survey_loans,
            )
            if proved <= _CRAFT_PROOF_CONSUMED.get(stocked_item, 0):
                # Stale proof for a re-queued demand: the same counters
                # already retired this bill. Leave it queued so the task --
                # and the proving loan -- get serviced instead of hidden.
                continue
            _CRAFT_PROOF_CONSUMED[stocked_item] = proved
            priorities.complete(
                stocked_item, tick,
                reason="loan crafts prove the bill; stock dispersed",
            )
            mall_targets.pop(stocked_item)
    for evergreen, (standing_target, restock_floor) in _EVERGREEN_STOCK.items():
        # Before the standing cell exists, an exact project bill owns the
        # bootstrap target.  Starting the watchdog at tick zero inflated the
        # first 128-belt foundation bill to the 200-belt standing reserve and
        # delayed every downstream stage.  Once prep has established the
        # recipe, the original stockout recovery behavior resumes.
        if evergreen not in prepped:
            continue
        if evergreen in mall_targets:
            continue
        if int(stock.get(evergreen, 0)) > restock_floor:
            continue
        if _evergreen_producer_live(client, surface, force, evergreen):
            continue
        mall_targets[evergreen] = max(
            int(mall_targets.get(evergreen, 0)), standing_target,
        )
        priorities.sync(mall_targets, stock, tick)
        try:
            priorities.items[evergreen].reason = (
                "restocked after stockout with no producer"
            )
        except (AttributeError, KeyError):
            pass
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


def _outstanding_work_signature(signature: tuple) -> tuple:
    """Decision-independent work identity used by the livelock guard.

    Alternating between two blocked tasks is still one unchanged work state.
    Keeping task/progress in `_pass_signature` preserves useful terminal
    diagnostics while this projection prevents the selection order from
    resetting the no-progress counter.
    """
    return signature[2:]


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
            "unbacked_draws": sorted(UNBACKED_DRAWS),
        },
    )


def _reserve_steel_starter_power_seed(
    client: RconClient, surface: str, force: str,
    construction_targets: Mapping[str, int], emit: Callable[[str], None],
) -> None:
    """Fence the steel starter's cyclic pole seed before foundations spend it.

    The mission's construction targets are known at run open, long before the
    steel stage is selected. Use their recipe closure as the intent signal and
    declare the eventual conversion project with only its planner-derived pole
    bill. The normal submission later expands this same project to the complete
    stage bill without losing its critical reservation priority.
    """
    ledger = _MATERIAL_RESERVATION_LEDGER
    needs_steel = any(
        item == "steel-plate"
        or "steel-plate" in _recipe_ingredient_closure(item)
        for item in construction_targets
    )
    if ledger is None or not needs_steel:
        return
    if _production_started(client, surface, force, "steel-plate"):
        return
    bill = _steel_starter_power_seed_bill()
    stock = _transferable_or_available_stock(client, surface, force)
    sources, rates = _material_sources_and_rates(
        client, surface, force, bill, stock,
    )
    project = ledger.declare(
        "conversion_steel-plate", bill, stock, target_item="steel-plate",
        source_producers=sources, expected_rates=rates,
        priority=_STEEL_STARTER_RESERVATION_PRIORITY,
    )
    emit(
        "STEEL STARTER RESERVE: held "
        f"{project.reserved.get('medium-electric-pole', 0)}/"
        f"{bill['medium-electric-pole']} medium poles before foundation spending"
    )


def _open_the_run(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    goal_item: str, mission_items: tuple[str, ...], script_output: Path | str,
    emit: Callable[[str], None], *, bootstrap_profile_name: str,
    reference_point: Point,
) -> tuple[dict[str, int], dict[str, int], PriorityList]:
    """Learn the force's real recipes, then announce what this run is aiming at.

    The catalog load has to happen before the target is validated a second
    time: the first check only knows the hardcoded recipes, and the point of
    loading is that the live force may know more.
    """
    global _STARTUP_METAL_STARTERS_OBSERVED, _STARTUP_MALL_LIMITS_RELEASED
    global _STARTUP_MALL_LIMITS_FALLBACK_PROBED
    global _BOOTSTRAP_LOAN_PROGRESS_REVISION
    global _POST_STARTER_TRANSITION_ANNOUNCED
    UNBACKED_DRAWS.clear()   # module state must not leak between runs
    resource_patches.clear_patch_cache()
    stage_extraction.clear_new_mine_cache()
    MANAGED_INTERMEDIATE_SOURCES.clear()
    _BOOTSTRAP_SHARED_PROVIDER_ITEMS.clear()
    _MALL_REFRESH_SIGNATURES.clear()
    _MALL_PROVIDER_CAPACITY_FLOORS.clear()
    _MALL_STOCK_GATE_FLOORS.clear()
    _MALL_ITEM_PROVIDER_LIMITS.clear()
    _REFINERY_SITE_RESERVATIONS.clear()
    _STARTUP_METAL_STARTERS_OBSERVED = False
    _STARTUP_MALL_LIMITS_RELEASED = False
    _STARTUP_MALL_LIMITS_FALLBACK_PROBED = False
    _BOOTSTRAP_LOAN_PROGRESS_REVISION = 0
    _LOAN_GATE_REFRESH_ATTEMPTS.clear()
    _CRAFT_PROOF_CONSUMED.clear()
    _POST_STARTER_TRANSITION_ANNOUNCED = False
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
    profile = bootstrap_profile(bootstrap_profile_name)
    ledger = _MATERIAL_RESERVATION_LEDGER
    if profile.seed_stock:
        if ledger is not None and ledger.bootstrap_supply_applied(profile.name):
            emit(
                f"BOOTSTRAP SUPPLY: profile={profile.name} v{profile.version} "
                "finite seed was already applied for this episode"
            )
        else:
            try:
                supply = ensure_bootstrap_supply(
                    client, surface, force, profile.seed_stock, reference_point,
                )
            except BootstrapSupplyError as error:
                raise StuckError(
                    str(error),
                    code="bootstrap_profile_seed_failed",
                    classification="bug",
                    state="supply_wait",
                    details={
                        "bootstrap_profile": profile.name,
                        "seed_stock": dict(profile.seed_stock),
                    },
                ) from error
            if ledger is not None:
                ledger.record_bootstrap_supply(
                    profile.name, supply.targets, supply.inserted,
                )
            emit(
                f"BOOTSTRAP SUPPLY: profile={profile.name} v{profile.version} "
                + ", ".join(
                    f"{item} target={target} before={supply.before.get(item, 0)} "
                    f"inserted={supply.inserted.get(item, 0)}"
                    for item, target in sorted(supply.targets.items())
                )
            )
    background_targets = mission_mall_targets(
        mission_items or (goal_item,), LINE_RECIPES,
    )
    _reserve_steel_starter_power_seed(
        client, surface, force, background_targets, emit,
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




#: Ready mall tasks served per pass once the surveyed task is done. One task
#: per pass serialized the bootstrap behind full re-surveys and sleeps; a
#: small bound keeps passes predictable while independent work advances
#: together. Plan-budget accounting is unchanged: the same submissions are
#: merely packed into fewer passes.
_MAX_MALL_TASKS_PER_PASS = 3


def _emit_deferred_control_telemetry(
    client: RconClient, surface: str, force: str, task, tick: int,
    mall_targets: Mapping[str, int], background_targets: Mapping[str, int],
    priorities: PriorityList, goal_item: str, mission_items: Sequence[str],
    emit: Callable[[str], None],
) -> None:
    """Name the exact work state behind a deferred mall control pass.

    This is deliberately observational: it reads the already-persisted
    priority and material ledger, then writes one human log line.  The fast
    belt stall of 2026-09-06 could otherwise show the deferred *producer*
    forever while hiding the expansion bill that requested its output.
    """
    entry = getattr(priorities, "items", {}).get(task.item)
    if entry is None or getattr(entry, "status", "") != "deferred":
        return
    repeat_key = (task.item, str(getattr(entry, "reason", "")))
    repeats = getattr(priorities, "_deferred_control_repeats", {})
    repeat = int(repeats.get(repeat_key, 0)) + 1
    repeats[repeat_key] = repeat
    setattr(priorities, "_deferred_control_repeats", repeats)
    try:
        stock = live_base.available_items(client, surface, force)
    except Exception:
        stock = {}
    ledger = _MATERIAL_RESERVATION_LEDGER
    origins: list[str] = []
    if ledger is not None:
        for project in sorted(ledger.projects.values(), key=lambda value: value.sequence):
            required = project.required
            if int(required.get(task.item, 0)) <= 0:
                continue
            bill = ",".join(
                f"{item}={count}" for item, count in sorted(required.items())
            )
            origins.append(
                f"{project.project_id}[state={project.state}; bill={bill}]"
            )
    origin_text = "; ".join(origins) or "none"
    if task.item == "fast-transport-belt":
        fast = int(stock.get("fast-transport-belt", 0))
        regular = int(stock.get("transport-belt", 0))
        fallback = (
            "not-applied" if origins and fast <= 0 and regular > 0
            else "not-applicable"
        )
        belt_text = (
            f"fast={fast}, regular={regular}, decision={fallback}"
        )
    else:
        belt_text = "not-applicable"
    relevant_items = set(mission_items or (goal_item,))
    relevant_items.update(mall_targets)
    relevant_items.update(background_targets)
    coverage = ",".join(
        f"{item}={int(stock.get(item, 0))}" for item in sorted(relevant_items)
    ) or "none"
    retry_ticks = max(0, int(getattr(entry, "retry_tick", tick)) - tick)
    emit(
        "  DEFERRED CONTROL: "
        f"task={task.item} target={getattr(task, 'target', mall_targets.get(task.item, 0))}; "
        f"reason={getattr(entry, 'reason', '')}; "
        f"repeat={repeat}; backoff={retry_ticks} ticks; "
        f"origin={origin_text}; belt-fallback={belt_text}; "
        f"chemical-credit(goal={goal_item})=[{coverage}]"
    )


def _serve_ready_pass(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    task, tick: int, mall_targets: dict[str, int],
    background_targets: dict[str, int], priorities: PriorityList,
    reference_point: Point, goal_item: str, emit: Callable[[str], None], *,
    mission_items: Sequence[str] = (),
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
    _maybe_announce_post_starter(client, surface, force, emit)
    if task is None and mall_targets:
        # The survey's answer predates prep and reserves, which re-queue
        # demands after it ran. Serve a freshly ready task instead of
        # sleeping on a stale None (2026-09-04: the post-metal reserve
        # re-queued every pass while serve slept on the survey's None).
        task = priorities.next(mall_targets, tick)
    if task is not None:
        # Serve a few ready tasks per pass instead of exactly one: completed
        # tasks pop out of mall_targets, so peers advance in the same pass
        # while independent cells and loans build concurrently. Serving stops
        # at the first task that stays queued behind an explicit defer, which
        # keeps today's behavior for genuinely blocked work, and no item is
        # served twice in one pass.
        served: set[str] = set()
        for _ in range(_MAX_MALL_TASKS_PER_PASS):
            if task.item in served:
                break
            served.add(task.item)
            _serve_mall_task(
                client, bridge, surface, force, task, tick, mall_targets,
                priorities, reference_point, emit,
            )
            if task.item in mall_targets:
                entry = priorities.items.get(task.item)
                if entry is None or entry.status == "deferred":
                    _emit_deferred_control_telemetry(
                        client, surface, force, task, tick, mall_targets,
                        background_targets, priorities, goal_item,
                        mission_items, emit,
                    )
                    break
            task = priorities.next(mall_targets, tick)
            if task is None:
                break
        return _SHORTAGE
    if mall_targets:
        wait_ticks = priorities.wait_ticks(mall_targets, tick)
        wait_seconds = min(
            _MAX_PRIORITY_SLEEP_SECONDS,
            max(1.0, (wait_ticks or 60) / _GAME_TICKS_PER_SECOND),
        )
        emit(
            "PRIORITY WAIT: all unfinished construction tasks are deferred; "
            f"next review in {wait_ticks or 60} ticks "
            f"({wait_seconds:.0f}s bounded sleep)"
        )
        consume_wait("priority_defer")
        time.sleep(wait_seconds)
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
    genuinely stuck (raises StuckError rather than guessing). A ready pass
    serves up to `_MAX_MALL_TASKS_PER_PASS` mall tasks while they keep
    completing, so independent work advances in parallel."""
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
    _TRANSFERABLE_WAITS.clear()
    _STAGE_DELIVERY_ATTEMPTS.clear()
    try:
        mall_targets, background_targets, priorities = _open_the_run(
            client, bridge, surface, force, goal_item, mission_items,
            script_output, emit, bootstrap_profile_name=bootstrap_profile,
            reference_point=reference_point,
        )
        _restore_bootstrap_reservations()
        prepped: set[str] = set()
        deferred_plate_targets: dict[str, int] = {}
        pending_plate_materials: dict[str, dict[str, int]] = {}
        last_signature: tuple | None = None
        last_ghost_count: int | None = None
        last_items_total: int | None = None
        last_loan_progress_revision = _BOOTSTRAP_LOAN_PROGRESS_REVISION
        last_generation_check_tick = -_GENERATION_CHECK_INTERVAL_TICKS
        unchanged_passes = 0
        iteration = 0
        while budget.passes < max_iterations:
            budget.begin_pass()
            tick, task = _survey_pass(
                client, surface, force, mall_targets, priorities, prepped,
            )
            signature = _pass_signature(
                task, mall_targets, prepped, background_targets,
                deferred_plate_targets, pending_plate_materials,
            )
            ghosts_now = live_base.pending_ghost_count(client, surface, force)
            target_stock_now = live_base.available_items(client, surface, force)
            relevant_mission_items = set(mission_items or (goal_item,))
            relevant_mission_items.update(mall_targets)
            relevant_mission_items.update(background_targets)
            for active_loan in active_bootstrap_loans(client, surface, force):
                relevant_mission_items.add(active_loan.target_item)
                if active_loan.step_recipe:
                    relevant_mission_items.add(active_loan.step_recipe)
            items_now = sum(
                int(target_stock_now.get(item, 0))
                for item in relevant_mission_items
            )
            # Count only stock tied to declared outstanding work. This includes
            # mall/core bootstrap batches: their growth is real progress, while
            # unrelated inventory accumulation still cannot mask a deadlock.
            stock_grew = (
                last_items_total is not None and items_now > last_items_total
            )
            loan_progressed = (
                _BOOTSTRAP_LOAN_PROGRESS_REVISION
                > last_loan_progress_revision
            )
            signature_changed = (
                last_signature is None
                or _outstanding_work_signature(signature)
                != _outstanding_work_signature(last_signature)
            )
            construction_progressed = (
                (last_ghost_count is not None and ghosts_now < last_ghost_count)
                or stock_grew or loan_progressed
            )
            # max_iterations bounds unproductive controller decisions. A
            # healthy one-second wait must not kill a run while its reserved
            # stack is visibly growing or bots are consuming ghosts.
            if (
                construction_progressed
                or (last_signature is not None and signature_changed)
            ):
                budget.credit_progress_pass()
            unchanged_passes = _livelock_step(
                signature_changed,
                construction_progressed,
                unchanged_passes,
            )
            last_signature = signature
            last_ghost_count = ghosts_now
            last_items_total = items_now
            last_loan_progress_revision = _BOOTSTRAP_LOAN_PROGRESS_REVISION
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
            # Mall-first opening: prep attempts the six standing cells at once,
            # ungated. Plate and construction shortages route to the mall and
            # the foundations below; prep cannot recursively decide which raw
            # foundation to open: iron and copper remain explicit below.
            if _prep_intermediate(
                client, bridge, surface, force, prepped, mall_targets,
                reference_point, emit,
            ):
                continue
            _prep_post_metal_stack_reserves(
                client, bridge, surface, force, prepped, mall_targets,
                reference_point, emit,
            )
            # Establish iron, then copper, then stone-brick: beltless seeds
            # first (zero belt stock cannot fund a full foundation before
            # first plates), then direct foundations. The mall cells are
            # already placed by the prep step above, ungated.
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
                    mission_items=mission_items,
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
            # Reduced stock cannot afford one permanent cell for every
            # construction item. Borrow existing assemblers for finite batches
            # until the five entities needed to build more mall slots each
            # have an independent producer.
            if not _core_mall_ready(client, surface, force):
                if _prep_core_mall(
                    client, bridge, surface, force, prepped, mall_targets,
                    reference_point, emit,
                ):
                    continue
            elif _upgrade_bootstrap_mall(
                client, bridge, surface, force, mall_targets,
                reference_point, emit,
            ):
                continue
            elif _retrofit_bootstrap_mall_outputs(
                client, bridge, surface, force, mall_targets,
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
                mission_items=mission_items,
            )
            if position is _SHORTAGE:
                continue
            iteration += 1
            if position is not None:
                emit(f"GOAL MET: {goal_item} is producing at {position}")
                return {"ok": True, "iterations": iteration, "output_position": position}
        raise StuckError(
            f"Did not reach a working {goal_item} line within "
            f"{max_iterations} non-progress control passes",
            code="controller_iteration_limit",
            classification="bug",
            state="failed",
            details={
                "goal_item": goal_item,
                "max_non_progress_passes": max_iterations,
                "progress_credits": budget.progress_credits,
            },
        )
    finally:
        _BOOTSTRAP_DISTRICT_LEDGER = None
        _MATERIAL_RESERVATION_LEDGER = None
        set_active_material_ledger(None)
        end_run_budget()
        client.close()
        bridge.close()
