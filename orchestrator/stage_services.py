# Path: orchestrator/stage_services.py
# Purpose: Validate stage targets and diagnose ghosts/machines; re-export the shared construction and coverage owner.

from __future__ import annotations

import copy
import heapq
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Callable

from core.science_recipe_graph import validate_current_builder_target
from orchestrator.controller_budget import consume_plan_submission, consume_wait
from orchestrator.construction_coverage import (
    POWER_LOW_REMAINING_ORE_THRESHOLD,
    Point,
    PowerExtensionResult,
    StuckError,
    _COVERAGE_MARGIN,
    _COVERAGE_PROGRESS,
    _COVERAGE_VERIFY_SETTLE_SECONDS,
    _CoverageProgressState,
    _DIRECT_MINED_INPUTS,
    _GHOST_BUILD_SECONDS,
    _INFRASTRUCTURE_BUILD_SECONDS,
    _INFRASTRUCTURE_DIAGNOSIS_SECONDS,
    _LOGISTIC_CHEST_ENTITIES,
    _PENDING_POWER_BRIDGES,
    _POWER_BRIDGE_SETTLE_SECONDS,
    _ROBOPORT_CHARGE_TARGET_J,
    _ROBOPORT_CONSTRUCTION_RADIUS,
    _ROBOPORT_LINK_DISTANCE,
    _ROBOPORT_LOGISTIC_RADIUS,
    _ROBOPORT_SERVICE_AREAS,
    _ROBOPORT_WAVE,
    _ROBOPORT_WAVE_GENERATION_KW,
    _await_bot_built_infrastructure,
    _await_built_status,
    _await_roboport_charge,
    _corridor_preference,
    _defer_power_hookup,
    _defer_roboport_coverage_wave,
    _ghost_materials,
    _ghostify_direct_infrastructure,
    _hookup_pole_candidates,
    _hookup_pole_position,
    _item_supply_chain_is_scheduled,
    _logistic_chest_positions,
    _long_reach_bridge_hops,
    _observe_coverage_frontier,
    _power_bridge_hops,
    _power_extension_result,
    _repair_existing_roboport_power,
    _reservation_supply_estimates,
    _searched_bridge_hops,
    _shortage_has_complete_supply_chains,
    _submit,
    assert_affordable,
    construction_supply_chain_is_scheduled,
    coverage_progress_revision,
    ensure_logistic_coverage,
    extend_power,
    extend_roboport_coverage,
    reset_coverage_progress,
    roboport_chain,
    service_distance,
)
from orchestrator import extraction_state, live_base, resource_patches
from orchestrator.game_bridge import GameBridge, load_json
from orchestrator.material_reservations import active_material_ledger
from orchestrator.parts_mall import MaterialShortage
from orchestrator.placement_clutter import clear_plan_clutter
from orchestrator.pole_obstruction import PoleObstruction, relocate_obstructing_poles
from orchestrator.power_district import append_plan_reservation
from orchestrator.roboport_placement import clear_chain_positions
from orchestrator.work_state import WorkStateSignal
from planners.belt_bridge import _ROUTE_SEARCH_MARGIN
from planners.infrastructure import POLE_SPECS
from planners.infrastructure_geometry import distance, footprint_tile_indices, l_route
from planners.plan_validation import ENTITY_FOOTPRINTS
from planners.recipe_data import LINE_RECIPES, MACHINE_SPEEDS
from planners.sandbox_infrastructure import build_layout_authorization
from tools.rcon_client import RconClient

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
# Give a newly submitted job five minutes before a flat backlog may fail.
# Visible construction progress can still extend that window below.
_BLOCKAGE_INTERVAL = 30.0
_BLOCKAGE_ROUNDS = 10
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


def validate_builder_target(
    target: str, surface: str, recipes: Mapping[str, Mapping],
) -> None:
    """Convert symbolic target rejection into the builder's fail-closed error."""
    try:
        validate_current_builder_target(target, surface, recipes)
    except ValueError as error:
        raise StuckError(str(error)) from error


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
    consume_wait("ghost_construction")
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
