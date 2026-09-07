# Path: orchestrator/stage_services.py
# Purpose: Execution primitives shared by every stage build -- submitting plans, waiting on bots, and extending power and roboport coverage so a stage is reachable and served.

from __future__ import annotations

import copy
import heapq
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Callable

from core.science_recipe_graph import validate_current_builder_target
from orchestrator.controller_budget import consume_plan_submission, consume_wait
from orchestrator import extraction_state, live_base
from orchestrator.game_bridge import GameBridge, load_json
from orchestrator.material_reservations import active_material_ledger
from orchestrator.parts_mall import MaterialShortage
from orchestrator.placement_clutter import clear_plan_clutter
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

Point = tuple[float, float]


@dataclass(frozen=True)
class PowerExtensionResult:
    """Outcome detail for callers that must separate coverage from a bridge."""

    ready: bool
    changed: bool


def _power_extension_result(
    ready: bool, changed: bool, *, detailed: bool,
) -> bool | PowerExtensionResult:
    return PowerExtensionResult(ready, changed) if detailed else ready

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
# Infrastructure that used to be executor-placed has synchronous callers: a
# submitted pole is immediately surveyed for grid membership and a submitted
# roboport becomes the source of the next coverage hop.  Keep that contract
# while making the entities honest construction jobs by allowing one normal
# five-minute construction window for the ghosts to be revived by bots.
_INFRASTRUCTURE_BUILD_SECONDS = 300.0
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
_POWER_BRIDGE_SETTLE_SECONDS = 3.0
_PENDING_POWER_BRIDGES: dict[tuple[str, str], float] = {}
# Slack subtracted from a chain's final hop so tile rounding can never land it
# a fraction outside the service radius it was placed to satisfy.
_COVERAGE_MARGIN = 2.0
# Settle after a chain lands before verifying coverage: network membership
# and power join lag placement by moments, and a first-check flake would
# otherwise fail runs the chain actually served.
_COVERAGE_VERIFY_SETTLE_SECONDS = 5.0
# Coverage advances one bot-built port at a time.  The current network builds
# the next reachable port, its ordinary ghost-built pole bridge powers it, and
# only then may that port extend construction range to the following hop.
_ROBOPORT_WAVE = 1
_ROBOPORT_WAVE_GENERATION_KW = 100_000.0
# Charge is observed for diagnostics, but never truncates the required
# coverage geometry. Each bot-built port receives a ghost-built power bridge
# before the next production plan is judged on construction progress.
_ROBOPORT_CHARGE_TARGET_J = 95_000_000.0
# Every chest type that is inert unless it is inside a logistic supply area.
_LOGISTIC_CHEST_ENTITIES = frozenset({
    "active-provider-chest", "buffer-chest", "passive-provider-chest",
    "requester-chest", "storage-chest",
})
_BOT_BUILT_INFRASTRUCTURE = frozenset({*POLE_SPECS, "roboport"})
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


class StuckError(WorkStateSignal):
    """A fail-closed controller stop with machine-readable blocker context."""

    def __init__(
        self, message: str, *, code: str = "untyped_stuck",
        classification: str = "bug", state: str = "failed",
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(
            message, code=code, classification=classification,
            state=state, details=details,
        )


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
    Direct placements are excluded because they are existing-entity
    configuration/removal operations, never new power or coverage entities."""
    required: dict[str, int] = {}
    for phase in plan["phases"]:
        for action in phase["actions"]:
            if action.get("action_type") == "place_ghost":
                required[action["entity"]] = required.get(action["entity"], 0) + 1
            elif action.get("action_type") == "place_tile_ghost":
                required[action["tile"]] = required.get(action["tile"], 0) + 1
    return required


def _ghostify_direct_infrastructure(plan: dict) -> tuple[tuple[str, Point], ...]:
    """Turn every server-side pole/roboport placement into a bot-built ghost.

    Planner generators still use ``place_entity`` for a mixture of sandbox
    scaffolding and existing-entity configuration.  The deterministic submit
    boundary is therefore the reusable enforcement point: no active real-base
    path can accidentally gain a free pole, substation, or roboport because a
    caller retained the old action type.

    The returned identities are the actions whose former synchronous contract
    must be preserved after submission.  Pre-existing ``place_ghost`` actions
    remain asynchronous as their caller intended.
    """
    converted: list[tuple[str, Point]] = []
    for phase in plan.get("phases", []):
        for action in phase.get("actions", []):
            if (
                action.get("action_type") == "place_entity"
                and action.get("entity") in _BOT_BUILT_INFRASTRUCTURE
            ):
                action["action_type"] = "place_ghost"
                position = action["position"]
                converted.append((
                    str(action["entity"]),
                    (float(position["x"]), float(position["y"])),
                ))
    return tuple(converted)


def _await_bot_built_infrastructure(
    client: RconClient, surface: str,
    placements: Sequence[tuple[str, Point]], emit: Callable[[str], None],
) -> None:
    """Wait for formerly-direct infrastructure to be built by real bots."""
    if not placements or not hasattr(client, "command"):
        return
    expected = {position: entity for entity, position in placements}
    deadline = time.monotonic() + _INFRASTRUCTURE_BUILD_SECONDS
    consume_wait("bot_built_infrastructure")
    while True:
        observed = live_base.entity_names_at(
            client, surface, tuple(expected),
        )
        pending = [
            (entity, position)
            for position, entity in expected.items()
            if observed.get(position) != entity
        ]
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise StuckError(
                "bot-built infrastructure did not finish inside the construction "
                "window: " + ", ".join(
                    f"{entity}@{position}" for entity, position in pending[:8]
                ),
                code="infrastructure_construction_wait",
                classification="intended_difficulty", state="constructing",
                details={
                    "pending": [
                        {"entity": entity, "position": list(position)}
                        for entity, position in pending
                    ],
                },
            )
        time.sleep(2.0)


def _reservation_supply_estimates(
    client: RconClient, surface: str, force: str,
    required: Mapping[str, int], stock: Mapping[str, int],
) -> tuple[dict[str, str | None], dict[str, float | None]]:
    """Observe sources and rates for a project without scheduling new work."""
    sources: dict[str, str | None] = {}
    rates: dict[str, float | None] = {}
    for item, count in required.items():
        if stock.get(item, 0) >= count:
            sources[item], rates[item] = "stock", None
            continue
        spec = LINE_RECIPES.get(item)
        line = (
            live_base.find_line(
                client, surface, force, item, str(spec["machine"]),
            )
            if spec is not None else None
        )
        if line is None:
            sources[item], rates[item] = None, None
            continue
        rate = (
            line.working_count
            * MACHINE_SPEEDS.get(str(spec["machine"]), 1.0)
            * float(spec.get("product_amount", 1))
            / max(float(spec["craft_time"]), 1e-9)
        )
        sources[item] = f"producer:{item}"
        rates[item] = rate if rate > 0 else None
    return sources, rates


def assert_affordable(
    client: RconClient, surface: str, force: str, plan: dict, name: str,
    emit: Callable[[str], None], reserve_project: bool = False,
    reservation_claimants: Sequence[str] = (),
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
    # Requester/buffer contents are already committed work-in-progress. They
    # cannot revive construction ghosts or be moved into a stage provider, so
    # accepting them here creates a false-ready plan that no bot can finish.
    stock = live_base.transferable_items(client, surface, force)
    ledger = active_material_ledger()
    if ledger is not None:
        project = ledger.projects.get(name)
        if project is not None and not ledger.project_is_active(name):
            project = None
        if reserve_project and (
            project is None or dict(project.required) != required
        ):
            sources, rates = _reservation_supply_estimates(
                client, surface, force, required, stock,
            )
            project = ledger.declare(
                name, required, stock, source_producers=sources,
                expected_rates=rates,
            )
        claimant_names = set(reservation_claimants)
        if project is not None:
            claimant_names.add(name)
        available = ledger.allocatable_stock(stock, claimants=claimant_names)
        if project is not None:
            short_targets = ledger.shortage_targets(name, stock)
        else:
            short_targets = {
                item: stock.get(item, 0) + count - available.get(item, 0)
                for item, count in required.items()
                if available.get(item, 0) < count
            }
        if short_targets:
            raise MaterialShortage(name, short_targets, stock)
        emit(
            f"  {name}: material reservation ok "
            f"({sum(required.values())} ghost items allocated)"
        )
        return
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


_DIRECT_MINED_INPUTS = frozenset({"coal", "copper-ore", "iron-ore", "stone"})


def _item_supply_chain_is_scheduled(
    client: RconClient, surface: str, force: str, item: str,
    visiting: frozenset[str] = frozenset(),
) -> bool:
    """Whether `item` and every solid prerequisite have live production."""
    if item in visiting:
        return False
    if item in _DIRECT_MINED_INPUTS:
        return extraction_state.resource_drill_count(
            client, surface, force, item,
        ) > 0
    recipe = LINE_RECIPES.get(item)
    if recipe is None or recipe.get("fluid_ingredients"):
        # Fluid provenance needs the chemical system's connected-flow survey;
        # absent that proof, an unfunded blueprint remains blocked.
        return False
    has_producer = live_base.find_line(
        client, surface, force, item, str(recipe["machine"]),
    ) is not None
    if not has_producer:
        try:
            from orchestrator.mall_bootstrap import active_bootstrap_loans
            has_producer = any(
                loan.target_item == item
                for loan in active_bootstrap_loans(client, surface, force)
            )
        except Exception:
            has_producer = False
    if not has_producer:
        return False
    parents = visiting | {item}
    return all(
        _item_supply_chain_is_scheduled(
            client, surface, force, str(ingredient), parents,
        )
        for ingredient in recipe.get("ingredients", ())
    )


def _shortage_has_complete_supply_chains(
    client: RconClient, surface: str, force: str, shortage: MaterialShortage,
) -> bool:
    """Whether all missing construction items are backed to raw extraction."""
    try:
        return all(
            _item_supply_chain_is_scheduled(
                client, surface, force, str(item),
            )
            for item in shortage.required
        )
    except (ValueError, live_base.TelemetryError):
        return False


def construction_supply_chain_is_scheduled(
    client: RconClient, surface: str, force: str, item: str,
) -> bool:
    """Whether a construction item can keep arriving after ghosts are placed."""
    try:
        return _item_supply_chain_is_scheduled(
            client, surface, force, item,
        )
    except (ValueError, live_base.TelemetryError):
        return False


def _submit(
    client: RconClient, bridge: GameBridge, surface: str, plan: dict, name: str,
    emit: Callable[[str], None], *, max_retries: int = 2,
    stage_coverage: Callable[[], None] | None = None,
    allow_unfunded_ghosts: bool = False,
) -> dict:
    """Submit a plan; if a tile is blocked, clear it ONLY when it's obviously
    safe map clutter (a tree, a rock -- never anything a force built) and
    retry. A collision with anything else means this exact placement is
    genuinely occupied -- raise so the caller picks a different spot instead
    of bulldozing real infrastructure.

    `stage_coverage` runs after the material check and before any ghost is
    submitted. A short plan may proceed when every missing item already has a
    scheduled producer and its complete solid prerequisite chain is active.
    `allow_unfunded_ghosts` still marks a coherent queued expansion, but it does
    not bypass that supply-chain proof. Collision and ownership checks are
    unchanged; only the requirement to warehouse the entire bill first is
    relaxed.
    """
    if not any(phase.get("actions") for phase in plan.get("phases", [])):
        raise StuckError(f"{name}: proposed zero actions")
    # Keep caller-owned/persisted plan identities stable. A failed coverage or
    # material attempt may retry the same object later and must be converted
    # again so its synchronous infrastructure contract is not silently lost.
    plan = copy.deepcopy(plan)
    converted_infrastructure = _ghostify_direct_infrastructure(plan)
    consume_plan_submission(name)
    clear_plan_clutter(client, surface, plan, emit)
    try:
        assert_affordable(
            client, surface, plan.get("force", "player"), plan, name, emit,
            True,
        )
    except MaterialShortage as shortage:
        ledger = active_material_ledger()
        project = ledger.projects.get(name) if ledger is not None else None
        if project is not None and project.hold_until_producing:
            raise
        producer_backed = _shortage_has_complete_supply_chains(
            client, surface, plan.get("force", "player"), shortage,
        )
        if not producer_backed:
            raise
        emit(
            f"  CONSTRUCTION BACKLOG: {name} is short "
            + ", ".join(
                f"{item}={count - shortage.available.get(item, 0)}"
                for item, count in sorted(shortage.required.items())
            )
            + "; placing coherent ghosts while scheduled producers catch up"
        )
    if stage_coverage is not None:
        stage_coverage()
    authorization = build_layout_authorization([(name, plan)])
    # Removal-only replacement plans place nothing by design;
    # the executor counts only place actions, so demanding attempted
    # placements here killed every retirement with "silent churn" after the
    # removals had already run (live run of 2026-08-24 15:16).
    expected_placements = sum(
        1
        for phase in plan.get("phases", [])
        for action in phase.get("actions", [])
        if action.get("action_type") in {
            "place_entity", "place_ghost", "place_tile_ghost",
        }
    )
    for attempt in range(max_retries + 1):
        consume_plan_submission(f"{name}#retry{attempt}")
        report = load_json(bridge.build_layout(authorization, plan))
        if expected_placements and report.get("attempted_placements") == 0:
            raise StuckError(
                f"{name}: execution attempted zero placements; refusing silent churn"
            )
        if report.get("ok"):
            if expected_placements:
                emit(
                    f"{name}: placed {report['succeeded_placements']} actions "
                    f"({report['placed_ghosts']} ghosts, "
                    f"{report['placed_entities']} entities)"
                )
            else:
                configured = sum(
                    action.get("action_type") == "configure_entity"
                    for phase in plan.get("phases", [])
                    for action in phase.get("actions", [])
                )
                emit(
                    f"{name}: applied {configured} configuration action(s); "
                    "no placements required"
                )
            script_output = getattr(bridge, "script_output", None)
            if script_output:
                append_plan_reservation(script_output, name, plan)
            ledger = active_material_ledger()
            if ledger is not None:
                ledger.mark_constructing(
                    name, live_base.available_items(
                        client, surface, plan.get("force", "player"),
                    ),
                )
            _await_bot_built_infrastructure(
                client, surface, converted_infrastructure, emit,
            )
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
        consume_wait(f"ghost_build@{position}")
        time.sleep(poll_seconds)


def _power_bridge_hops(
    start: Point, end: Point, spacing: float, blocked: set[tuple[int, int]],
) -> list[Point]:
    """Shortest deterministic pole chain whose real centres avoid blockers.

    Medium poles snap to half-tile centres. Generic rounded route points can
    therefore move another half tile on each axis when Factorio places them.
    Generate the actual half-tile centres here so route scoring and wire
    geometry agree with the entities that will exist.  `spacing` is wire reach
    (nine tiles for a medium pole), not its seven-tile supply-area width.
    """

    def pole_centre(point: Point) -> Point:
        return (math.floor(point[0]) + 0.5, math.floor(point[1]) + 0.5)

    def leg_hops(
        leg_start: Point, leg_end: Point, *, include_end: bool,
    ) -> list[Point] | None:
        """Return a legal straight-line leg at any angle.

        Fractional samples are snapped before their edges are checked.  This
        lets a clear diagonal use the full wire reach, while increasing the
        segment count when snapping would make an apparent nine-tile edge too
        long.
        """
        actual_end = pole_centre(leg_end) if include_end else leg_end
        span = distance(leg_start, actual_end)
        if span == 0:
            return []
        minimum_segments = max(1, math.ceil(span / spacing))
        for segments in range(minimum_segments, minimum_segments + 9):
            points = [
                pole_centre((
                    leg_start[0] + (actual_end[0] - leg_start[0]) * step / segments,
                    leg_start[1] + (actual_end[1] - leg_start[1]) * step / segments,
                ))
                for step in range(1, segments)
            ]
            points = list(dict.fromkeys(points))
            if include_end:
                points.append(actual_end)
            chain = [leg_start, *points, actual_end]
            if all(
                distance(left, right) <= spacing
                for left, right in zip(chain, chain[1:])
            ):
                return points
        return None

    horizontal = l_route(start, end)
    vertical = [start, (start[0], end[1]), end]
    # A pole may connect at every angle; a direct diagonal is both the shortest
    # route and generally uses fewer poles than the old orthogonal L route.
    routes = [[start, end], horizontal, vertical]
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
        cursor = route[0]
        route_legal = True
        legs = list(zip(route, route[1:]))
        for index, (_leg_start, leg_end) in enumerate(legs):
            # Intermediate route corners are poles and must use their actual
            # snapped centre as the start of the next leg.
            include_end = index < len(legs) - 1
            leg = leg_hops(cursor, leg_end, include_end=include_end)
            if leg is None:
                route_legal = False
                break
            hops.extend(leg)
            cursor = pole_centre(leg_end) if include_end else leg_end
        if not route_legal:
            continue
        hops = list(dict.fromkeys(hops))
        collisions = sum((math.floor(x), math.floor(y)) in blocked for x, y in hops)
        length = sum(distance(a, b) for a, b in zip(route, route[1:]))
        candidates.append((collisions, length, tuple(route), hops))
    if not candidates:
        return _searched_bridge_hops(start, end, spacing, blocked)
    collisions, _length, _route, hops = min(
        candidates,
        key=lambda item: (item[0], len(item[3]), item[1], item[2]),
    )
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
            return [
                (math.floor(hop[0]) + 0.5, math.floor(hop[1]) + 0.5)
                for hop in chain[1:]
                if hop != goal
            ]
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
    blocked: set[tuple[int, int]], toward: Point, pole_name: str,
) -> Point | None:
    """Where to park the pole that will actually SUPPLY an unwired consumer.

    A pole chain that merely arrives near a consumer does not power it: the
    consumer has to sit inside a pole's supply area. Chain hops land up to a
    full spacing apart, so the last one can stop well outside that area -- which
    is how a bridged roboport ends up built, connected and still dead.
    """
    entity = live_base.entity_at(client, surface, consumer)
    footprint = ENTITY_FOOTPRINTS.get(entity["name"], 1) if entity else 1
    reach = POLE_SPECS[pole_name]["supply"] + footprint / 2
    span = math.ceil(reach)
    pole_size = int(POLE_SPECS[pole_name]["size"])
    candidates = [
        spot
        for tile_x in range(math.floor(consumer[0] - span), math.ceil(consumer[0] + span))
        for tile_y in range(math.floor(consumer[1] - span), math.ceil(consumer[1] + span))
        for spot in [
            (tile_x + 0.5, tile_y + 0.5)
            if pole_size == 1 else (float(tile_x), float(tile_y))
        ]
        if max(abs(spot[0] - consumer[0]), abs(spot[1] - consumer[1])) < reach
        and footprint_tile_indices(spot, pole_size).isdisjoint(blocked)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda spot: distance(spot, toward))


def extend_power(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    near_position: Point, emit: Callable[[str], None], *,
    reserved_tiles: set[tuple[int, int]] | None = None,
    detailed: bool = False,
    _retried: bool = False,
) -> bool | PowerExtensionResult:
    """Connect `near_position` to a network that actually generates power.

    Handles both shapes of power fault. If a pole stands at `near_position` its
    network is bridged to a powered one; if NO pole stands there -- a roboport
    or machine that was never wired at all -- a chain is run out and terminated
    on a pole whose supply area covers it. The default boolean says whether the
    position is usable or a bridge was submitted. Callers that need to size a
    grid can request ``detailed`` and distinguish physical coverage from an
    actual network change.

    A hop can lose its tile between the collision survey and the bots' arrival:
    a concurrent build's own substation ghost lands exactly there (live,
    2026-08-22 02:46). That is infrastructure-in-flight, not a wall -- replan
    once on fresh ground instead of ending the run. `reserved_tiles` are a
    sibling plan's future footprint: an emergency pole must route around that
    footprint just as it would around built infrastructure.
    """
    bridge_key = (surface, force)
    pending_since = _PENDING_POWER_BRIDGES.pop(bridge_key, None)
    waited_for_pending = pending_since is not None
    if pending_since is not None:
        remaining = _POWER_BRIDGE_SETTLE_SECONDS - (
            time.monotonic() - pending_since
        )
        if remaining > 0:
            consume_wait(f"power_bridge_settle@{near_position}")
            time.sleep(remaining)
    own_network = live_base.pole_network_id(client, surface, near_position)
    target = live_base.nearest_powered_pole(
        client, surface, force, near_position, exclude_network_id=own_network,
        avoid_resources=True,
    )
    if target is None:
        # A bridge submission can race another stage: enough of the first
        # attempt may land to merge the networks before the retry surveys.
        # That is success, not a reason to tell the caller no repair occurred.
        if (
            (_retried or waited_for_pending)
            and own_network is not None
            and live_base.network_generation_kw(
                client, surface, force, near_position,
            ) > 0
        ):
            emit(f"  power bridge already joined network {own_network} while retrying")
            return _power_extension_result(True, True, detailed=detailed)
        return _power_extension_result(False, False, detailed=detailed)
    target_position, target_name = target
    target_supply = POLE_SPECS.get(
        target_name, POLE_SPECS["medium-electric-pole"],
    )["supply"]
    consumer = live_base.entity_at(client, surface, near_position)
    bridge_pole = (
        "small-electric-pole"
        if consumer is not None and consumer.get("name") == "small-electric-pole"
        else "medium-electric-pole"
    )
    consumer_size = ENTITY_FOOTPRINTS.get(consumer["name"], 1) if consumer else 1
    if (
        own_network is None
        and max(
            abs(target_position[0] - near_position[0]),
            abs(target_position[1] - near_position[1]),
        # A consumer exactly touching the edge of a pole's nominal supply
        # square can still report `no_power` (notably a 4x4 roboport). Treat
        # the boundary as needing a real hookup instead of accepting a bridge
        # that will never charge.
        ) < target_supply + consumer_size / 2
    ):
        emit(f"  {near_position} is already inside the supply area of the powered "
             f"{target_name} at {target_position}; waiting for it to charge")
        return _power_extension_result(True, False, detailed=detailed)
    if own_network is None:
        emit(f"  power gap found: {near_position} is not wired to any pole -- running a "
             f"chain from {target_name} at {target_position} and terminating it in supply range")
    else:
        emit(f"  power gap found: network {own_network} at {near_position} carries no "
             f"generation -- bridging to {target_name} at {target_position}")
    # A stranded low-tier pole must stay low-tier while it is being bridged:
    # the first steel starter cannot require the medium poles that need its
    # own output. Other consumers retain the normal medium-pole bridge.
    target_wire = POLE_SPECS.get(
        target_name, POLE_SPECS["medium-electric-pole"],
    )["wire"]
    endpoint_pole = bridge_pole
    endpoint_wire = POLE_SPECS[endpoint_pole]["wire"]
    if own_network is not None and consumer is not None:
        endpoint_wire = POLE_SPECS.get(
            consumer["name"], POLE_SPECS["medium-electric-pole"],
        )["wire"]
    spacing = min(
        POLE_SPECS[bridge_pole]["wire"],
        target_wire,
        endpoint_wire,
    )
    margin = 132.0
    blocked = live_base.occupied_tiles(
        client, surface,
        (min(target_position[0], near_position[0]) - margin,
         min(target_position[1], near_position[1]) - margin),
        (max(target_position[0], near_position[0]) + margin,
         max(target_position[1], near_position[1]) + margin),
        include_resources=True,
    )
    blocked |= reserved_tiles or set()
    hookup_blocked = set(blocked)
    blocked -= {(math.floor(target_position[0]), math.floor(target_position[1]))}
    if own_network is None:
        # The consumer's own body is an obstacle, not an endpoint: the chain has
        # to stop on a free tile whose supply area covers it.
        endpoint = _hookup_pole_position(
            client, surface, near_position, hookup_blocked, target_position,
            bridge_pole,
        )
        if endpoint is None and bridge_pole == "medium-electric-pole":
            # Dense stage layouts can fill every one-tile medium-pole site
            # around a support entity. A substation reaches farther, so it can
            # terminate the same material-funded bridge from outside that
            # packed footprint. Keep the steel starter on small poles: its
            # first connection may not ask for steel.
            endpoint_pole = "substation"
            endpoint_wire = POLE_SPECS[endpoint_pole]["wire"]
            endpoint = _hookup_pole_position(
                client, surface, near_position, hookup_blocked, target_position,
                endpoint_pole,
            )
            if endpoint is not None:
                emit(
                    f"  power bridge terminal near {near_position} has no free "
                    "medium-pole tile; using a substation outside the packed stage"
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
    action_names = [bridge_pole] * len(hops)
    if own_network is None:
        hops = [*hops, endpoint]
        action_names.append(endpoint_pole)
    if not hops:
        raise StuckError(f"power gap between {near_position} and {target_position} but no room "
                          "for a bridging pole -- they may already be in reach; investigate directly")
    chain_positions = [target_position, *hops]
    chain_names = [target_name, *action_names]
    if own_network is not None:
        chain_positions.append(endpoint)
        chain_names.append(
            consumer["name"] if consumer is not None else "medium-electric-pole"
        )
    for left, right, left_name, right_name in zip(
        chain_positions, chain_positions[1:], chain_names, chain_names[1:],
    ):
        wire_reach = min(
            POLE_SPECS.get(left_name, POLE_SPECS["medium-electric-pole"])["wire"],
            POLE_SPECS.get(right_name, POLE_SPECS["medium-electric-pole"])["wire"],
        )
        if distance(left, right) > wire_reach:
            raise StuckError(
                f"planned power bridge edge {left}->{right} is "
                f"{distance(left, right):.2f} tiles, beyond {wire_reach:.2f} "
                "tile wire reach"
            )
    actions = [
        {"action_type": "place_ghost", "entity": entity, "position": {"x": x, "y": y}}
        for entity, (x, y) in zip(action_names, hops)
    ]
    plan = {"phases": [{"name": "power_bridge", "actions": actions}], "surface": surface, "force": force}
    try:
        _submit(client, bridge, surface, plan, "power_bridge", emit)
        _await_bot_built_infrastructure(
            client, surface,
            tuple(zip(action_names, hops)),
            emit,
        )
    except StuckError as error:
        if (
            _retried
            or "occupied_by_different_entity" not in str(error)
        ):
            raise
        emit(
            "  power_bridge raced a concurrent build for a hop tile -- "
            "resurveying and retrying once"
        )
        return extend_power(
            client, bridge, surface, force, near_position, emit,
            reserved_tiles=reserved_tiles,
            detailed=detailed,
            _retried=True,
        )
    generation = live_base.network_generation_kw(
        client, surface, force, near_position,
    )
    if generation is None or generation <= 0:
        if not _retried:
            emit(
                "  power bridge placement remained electrically disconnected "
                "-- resurveying and retrying once"
            )
            return extend_power(
                client, bridge, surface, force, near_position, emit,
                reserved_tiles=reserved_tiles,
                detailed=detailed,
                _retried=True,
            )
        raise StuckError(
            f"power bridge at {near_position} remained disconnected after "
            "placement and one fresh retry"
        )
    _PENDING_POWER_BRIDGES[bridge_key] = time.monotonic()
    # Connectivity is not capacity: every run has browned out as stages
    # stacked onto the starter array. Top the network's generation up from
    # stocked solar while we are already holding its powered anchor.
    from orchestrator.autonomous_builder import _top_up_solar_generation

    try:
        _top_up_solar_generation(
            client, bridge, surface, force, target_position, emit,
            ensure_main_connection=False,
        )
    except Exception as error:  # generation top-up is opportunistic
        emit(f"  SOLAR TOP-UP skipped: {error}")
    return _power_extension_result(True, True, detailed=detailed)


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

    Every port is pushed as far along the route toward `target` as its link to
    the previous port allows, and only the LAST hop is shortened -- to just
    past the point where `target` enters coverage. Capping every hop that way
    instead put each new port a few tiles from its predecessor whenever the
    gap barely exceeded the radius, stacking full supply areas on top of each
    other for no reach (observed live: ports at (45,-1), (47,-5), (49,-9)).
    Distances along the chain are Euclidean, which is >= the Chebyshev
    distance the square service area actually uses -- so remaining Euclidean
    distance <= radius guarantees square coverage too. A port never stands ON
    the target: a logistic chest IS the target.
    """
    total = math.dist(source, target)
    if total == 0 or total <= radius:
        return []
    # How far along the route the last port must sit for `target` to be inside
    # its service area.
    reach = total - radius + _COVERAGE_MARGIN
    step = _ROBOPORT_LINK_DISTANCE - 4  # slack so a rounded tile never lands on the link cliff edge
    cap = total - _COVERAGE_MARGIN  # never stand on the chest itself
    hops = max(1, math.ceil(reach / step))
    unit = ((target[0] - source[0]) / total, (target[1] - source[1]) / total)
    travels = [min(step * hop, cap) for hop in range(1, hops)]
    travels.append(max(reach, min(step * hops, cap)))
    return [
        (float(round(source[0] + unit[0] * travel)), float(round(source[1] + unit[1] * travel)))
        for travel in travels
    ]


def _repair_existing_roboport_power(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    emit: Callable[[str], None],
) -> tuple[Point, ...]:
    """Repair powerless ports left by an earlier interrupted runner."""
    pending: list[Point] = []
    for position, status in live_base.roboports_needing_power(
        client, surface, force
    ):
        emit(f"  existing roboport at {position} is {status} -- connecting it")
        if not extend_power(client, bridge, surface, force, position, emit):
            raise StuckError(
                f"existing roboport at {position} cannot be powered"
            )
        pending.append(position)
    return tuple(pending)


def _defer_roboport_coverage_wave(
    target_position: Point, purpose: str, wave: Sequence[Point], *,
    state: str,
) -> None:
    """Yield until the current bot-built coverage wave can be re-observed.

    Import lazily because the builder imports these shared services.  A
    coverage ghost or its just-submitted pole bridge is normal construction
    work, not a terminal coverage gap: the next controller pass must survey
    the port before using it as the source for another hop.
    """
    from orchestrator.autonomous_builder import ProductionPrerequisiteDeferred

    raise ProductionPrerequisiteDeferred(
        f"{purpose} coverage waits for bot-built roboport wave "
        + ", ".join(str(position) for position in wave),
        code="roboport_coverage_construction_wait",
        state=state,
        details={
            "purpose": purpose,
            "target": [target_position[0], target_position[1]],
            "wave": [[position[0], position[1]] for position in wave],
        },
    )

def _await_roboport_charge(
    client: RconClient, surface: str, force: str,
    near: Point, positions: Sequence[Point], emit: Callable[[str], None],
) -> None:
    """Report a charging wave without turning it into control flow.

    Each fresh port draws up to ~2.1 MW while charging from ~50%. Coverage now
    advances as one bot-built hop, so charging is visible natural backpressure:
    later geometry remains planned, but cannot build until this port becomes a
    powered member of the network."""
    generation = live_base.network_generation_kw(client, surface, force, near)
    if generation is not None and generation >= _ROBOPORT_WAVE_GENERATION_KW:
        return
    energies = [
        live_base.roboport_energy(client, surface, force, position)
        for position in positions
    ]
    if all(
        energy is not None and energy >= _ROBOPORT_CHARGE_TARGET_J
        for energy in energies
    ):
        return

    emit(
        "  ROBOport POWER PENDING: this coverage wave is still charging; "
        "the next bot-built coverage hop waits on this network member"
    )


def extend_roboport_coverage(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    target_position: Point, emit: Callable[[str], None], *,
    purpose: str = "construction",
    reserved_tiles: set[tuple[int, int]] | None = None,
) -> bool:
    """If `target_position` is beyond every existing roboport's service area
    for `purpose` ("construction" for ghosts, "logistic" for chests), chain new
    roboports out to it (each within link distance of the previous one). Returns
    True if roboports were added (caller should re-check ghost completion),
    False if coverage was already fine."""
    if client is not None:
        repaired = _repair_existing_roboport_power(
            client, bridge, surface, force, emit
        )
        if repaired:
            _await_roboport_charge(
                client, surface, force, repaired[0], repaired, emit,
            )
            _defer_roboport_coverage_wave(
                target_position, purpose, repaired, state="constructing",
            )
    radius, square = _ROBOPORT_SERVICE_AREAS[purpose]
    nearest = live_base.nearest_roboport(client, surface, force, target_position)
    if nearest is None:
        return False
    if (
        client is not None
        and live_base.entity_status_name(client, surface, nearest) == "low_power"
    ):
        _await_roboport_charge(
            client, surface, force, nearest, [nearest], emit,
        )
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
            reserved_tiles=reserved_tiles,
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
    wave_starts = (
        range(0, len(placed), _ROBOPORT_WAVE)
        if client is None else range(0, _ROBOPORT_WAVE, _ROBOPORT_WAVE)
    )
    for wave_start in wave_starts:
        wave = placed[wave_start:wave_start + _ROBOPORT_WAVE]
        actions = [
            {"action_type": "place_ghost", "entity": "roboport", "position": {"x": x, "y": y}}
            for x, y in wave
        ]
        plan = {"phases": [{"name": "roboport_bridge", "actions": actions}], "surface": surface, "force": force}
        _submit(client, bridge, surface, plan, "roboport_bridge", emit)
        if client is None:
            # Offline geometry callers have no live entity state to reobserve.
            # They retain the complete deterministic chain calculation while
            # real runs stop after one submitted, bot-built wave below.
            continue
        # A roboport with no power provides NO coverage of either kind, so chaining
        # one out without connecting it just moves the stall. Observed live: a
        # bridged roboport sat at no_power and its ghosts never built.
        #
        # The status has to be read AFTER the roboport actually exists. Checking
        # straight after submitting the plan read the status of a ghost, which is
        # None rather than "no_power", so the guard passed vacuously and left an
        # unpowered roboport serving nothing.
        for position in wave:
            status = _await_built_status(
                client, surface, position,
                timeout_seconds=_INFRASTRUCTURE_BUILD_SECONDS,
            )
            if status is None:
                _defer_roboport_coverage_wave(
                    target_position, purpose, wave, state="constructing",
                )
            if status in {"no_power", "low_power"}:
                emit(f"  bridged roboport at {position} is {status} -- connecting it")
                # The roboport dodged reserved tiles, but its power chain has
                # to dodge them too: a hop through a sibling plan's future
                # footprint becomes a pole on a pipe ghost (2026-09-05: the
                # crude pipeline died on a roboport-chain pole at -297.5,-57.5).
                if not extend_power(
                    client, bridge, surface, force, position, emit,
                    reserved_tiles=reserved_tiles,
                ):
                    raise StuckError(
                        f"roboport at {position} cannot be powered; it would provide no "
                        f"{purpose} coverage"
                    )
                _defer_roboport_coverage_wave(
                    target_position, purpose, wave, state="power_wait",
                )
        if client is not None or wave_start + _ROBOPORT_WAVE < len(placed):
            _await_roboport_charge(client, surface, force, nearest, wave, emit)
        if client is not None:
            verified = live_base.nearest_roboport(
                client, surface, force, target_position,
            )
            if verified is not None and service_distance(
                verified, target_position, square=square,
            ) <= radius:
                return True
            _defer_roboport_coverage_wave(
                target_position, purpose, wave, state="constructing",
            )
    # Verify the target actually entered coverage instead of assuming the
    # chain did: dropped final hops (blocked tiles) used to return success
    # with the gap intact, and a built chest would then sit in no network
    # forever (2026-09-04: a plastic provider 28 tiles from its port while
    # construction coverage reported fine).
    verified = live_base.nearest_roboport(client, surface, force, target_position)
    if verified is not None and service_distance(
        verified, target_position, square=square,
    ) <= radius:
        return True
    time.sleep(_COVERAGE_VERIFY_SETTLE_SECONDS)
    verified = live_base.nearest_roboport(client, surface, force, target_position)
    if verified is not None and service_distance(
        verified, target_position, square=square,
    ) <= radius:
        return True
    raise StuckError(
        f"{target_position} is still outside {purpose} coverage "
        f"(radius {radius:.0f}) after chaining; nearest roboport is "
        f"{verified} -- investigate directly",
        code="coverage_gap",
        details={
            "purpose": purpose,
            "target": [target_position[0], target_position[1]],
            "nearest": (
                [verified[0], verified[1]] if verified is not None else None
            ),
            "gap": (
                service_distance(verified, target_position, square=square)
                if verified is not None else None
            ),
            "radius": radius,
            "waves_placed": len(placed),
        },
    )


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
