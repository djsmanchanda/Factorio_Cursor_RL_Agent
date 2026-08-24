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
from orchestrator.construction_stock import MallReserve, mall_reserve
from orchestrator.baseline_production import (
    BASELINE_MACHINES, BASELINE_PLATES, BOOTSTRAP_FURNACE_CAPS,
    baseline_build_order, baseline_drill_phase, baseline_plate_draw,
    demand_adjusted_plate_draw, drill_phase_for_draw,
    smelter_count_for_draw,
)
from orchestrator.game_bridge import GameBridge, load_json
from orchestrator.controller_budget import (
    begin_run_budget, consume_diagnosis, consume_remediation, consume_wait,
    end_run_budget,
)
from orchestrator.mine_retirement import retire_depleted_mines
from orchestrator.mine_output_tap import legacy_output_tap_plan
from orchestrator.mall_builder import (
    build_compact_mall_stage,
    mall_cell_needs_rebuild,
    rebuild_incomplete_mall_cell,
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
    ManagedRefineryState, assert_refinery_removals_owned, recover_managed_refinery,
)
from orchestrator.stage_chemical import ensure_coal_mine, ensure_oil_cell
from orchestrator.stage_extraction import (
    LOCAL_MODE_MAX_LINK_TILES, existing_mine_service_geometry,
    candidate_mining_origins as _candidate_mining_origins,  # noqa: F401 - compatibility export
    choose_mining_origin as _choose_mining_origin,  # noqa: F401 - compatibility export
    mining_drill_positions as _mining_drill_positions,  # noqa: F401 - compatibility export
    plan_local_extraction,
    smelter_count_for_drills,
)
from orchestrator.stage_recovery import repair_existing_ingredient_transport
from orchestrator.stage_services import (
    StuckError,
    _BLOCKAGE_INTERVAL,
    _BLOCKAGE_ROUNDS,
    _DEFAULT_BELT,
    _DEFAULT_INSERTER,
    _LOGISTIC_CHEST_ENTITIES,
    _ROBOPORT_SERVICE_AREAS,
    _STAGE_CHEST_REACH,
    _diagnose_machines,
    _logistic_chest_positions,
    _submit,
    _wait_for_ghosts,
    assert_affordable,
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
    transport_grace_seconds,
)
from planners.bootstrap_smelting import (
    generate_logistic_smelter,
    logistic_smelter_origin,
    retire_logistic_smelter_plan,
)
from planners.infrastructure import strip_local_power
from planners.infrastructure_geometry import footprint_tile_indices
from planners.local_layout_planner import LocalLayoutPlanner
from planners.mall_layout import (
    generate_compact_mall_request_update, generate_mall_provider_limit_update,
    generate_mall_stock_gate_update, generate_promoted_mall_retirement_plan,
)
from planners.plan_validation import ENTITY_FOOTPRINTS
from planners.recipe_data import (
    BELT_TIERS,
    LINE_RECIPES,
    MACHINE_SPEEDS,
    ITEM_STACK_SIZES,
    install_catalog_line_recipes,
    install_catalog_machines,
    install_catalog_stack_sizes,
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


# How long one logistic-coverage remedy may wait for a just-connected
# roboport to charge before the round is honestly reported as a no-op. A fresh
# port lands at ~50% of 100 MJ and draws megawatts while topping up; without a
# real wait every round re-diagnosed the same orphaned chest within seconds,
# burned all six rounds on nothing, and killed the run (live, 2026-08-22).
_LOGISTIC_CHARGE_WAIT_SECONDS = 90.0


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
            spots = live_base.chained_clear_spots(
                client, surface,
                [("passive-provider-chest", 1), ("medium-electric-pole", 1)],
                origin,
            )
            chest_spot = next((s for s in spots if s[0] == "passive-provider-chest"), None)
            if chest_spot is not None:
                plan = {"phases": [{
                    "name": f"deliver_{item}",
                    "actions": [
                        {"action_type": "place_entity",
                         "entity": "passive-provider-chest",
                         "position": {"x": chest_spot[1], "y": chest_spot[2]}},
                    ],
                }]}
                plan["surface"], plan["force"] = surface, force
                _submit(client, bridge, surface, plan,
                        f"deliver_{item}", emit)
                moved = live_base.transfer_stock(
                    client, surface, item,
                    max(required * 2, required + 10),
                    (chest_spot[1], chest_spot[2]),
                )
                emit(
                    f"  MATERIAL DELIVERY: moved {moved} {item} from base "
                    f"stock into this stage's network at {origin}"
                )
                acted = True
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
    ensure_logistic_coverage(client, bridge, surface, force, logistic_chest_positions, emit)
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
    _submit(
        client, bridge, surface, plan, f"mining_{extraction.ore}", emit,
        stage_coverage=lambda: _ensure_plan_construction_coverage(
            client, bridge, surface, force, plan, emit,
        ),
    )
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
        if not is_ours:
            collision.append(tile)
    if collision:
        raise StuckError(
            f"{recipe} refinery extension intersects real infrastructure at "
            f"{collision[:3]}; refusing to expand its mine ahead of that conflict"
        )
    water = sorted(footprint & live_base.water_tiles(client, surface, minimum, maximum))
    if not water:
        return None
    return {"phases": [{
        "name": f"{recipe}_smelter_landfill_foundation",
        "actions": [
            {"action_type": "place_tile_ghost", "tile": "landfill",
             "position": {"x": x, "y": y}}
            for x, y in water
        ],
    }]}


def _place_plate_expansion_foundation(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, foundation: dict, emit: Callable[[str], None],
) -> None:
    """Build solid ground before a plate-refinery ghost is submitted there."""
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
    support_positions = {
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity", "").endswith("inserter")
    }
    for position in sorted(support_positions):
        if live_base.entity_status_name(client, surface, position) == "no_power":
            emit(f"  support inserter at {position} has no power -- connecting it")
            if not extend_power(client, bridge, surface, force, position, emit):
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
    ore_output: Point, emit: Callable[[str], None],
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
        raise StuckError(str(error)) from error
    delta["surface"], delta["force"] = surface, force
    emit(
        f"SMELTER COHESION: expanding {recipe} at {origin} from "
        f"{state.furnace_count} to {target_machines}; retire End, add Repeat, finish End"
    )
    _prepare_replacement_services(
        client, bridge, surface, force, full, delta, emit,
    )
    _submit(client, bridge, surface, delta, f"extend_{recipe}_refinery", emit)
    _bring_modular_refinery_up(
        client, bridge, surface, force, recipe, full,
        target_machines, origin, emit, variant=target_variant,
    )
    return interface.provider


def _assert_atomic_plate_expansion_affordable(
    client: RconClient, surface: str, force: str, recipe: str, extraction,
    state: ManagedRefineryState, target_machines: int, emit: Callable[[str], None],
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
        raise StuckError(str(error)) from error
    foundation = _plate_expansion_foundation(
        client, surface, force, recipe, smelter_delta,
        own_action_positions=_planned_entity_positions(
            smelter_delta,
            *( [extraction.build_plan] if extraction.build_plan is not None else [] ),
        ),
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
    assert_affordable(
        client, surface, force, combined, f"expand_{recipe}_system", emit,
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
        existing = recover_managed_refinery(
            client, surface, force, recipe, positions,
        )
    except ValueError as error:
        raise StuckError(str(error)) from error
    total_drills = extraction.system_drill_count_before + extraction.drill_count
    required = smelter_count_for_drills(
        recipe, total_drills, extraction.mining_productivity_bonus,
    )
    target = scheduled_refinery_target(existing.furnace_count, required)
    if target is None:
        raise StuckError(
            f"{recipe} refinery reached its generation-1 cap at "
            f"{existing.furnace_count} furnace(s); refusing to overbuild this "
            "footprint before a new refinery site is planned"
        )
    emit(
        f"SMELTER SYSTEM TARGET: {total_drills} total {extraction.ore} "
        f"drill(s) require {required} furnace(s); scheduled target is {target}"
    )
    return existing, target


def _prepare_initial_refinery(
    client: RconClient, surface: str, force: str, recipe: str,
    extraction, ore_output: Point, emit: Callable[[str], None], *,
    preflight_only: bool,
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
    )
    if route is None:
        raise StuckError(f"{recipe} direct ore route unexpectedly selected logistics")
    route_actions, belt_type = route
    plan["phases"].append({
        "name": f"bridge_{extraction.ore}_to_{recipe}",
        "actions": route_actions,
    })
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
    # Spend the stocked fast tiers before the scarce regular ones: the mine
    # scaffold already chose its tier by stock, and the refinery blueprint
    # must follow the same economy or the base dies one regular belt short
    # (live run of 2026-08-22 03:25).
    try:
        belt_stock = live_base.available_items(client, surface, force)
        swapped = sum(
            _prefer_stocked_belt_tiers(staged, belt_stock)
            for staged in combined_plans
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
        "phases": [phase for staged in combined_plans for phase in staged["phases"]],
    }
    assert_affordable(
        client, surface, force, combined, f"initial_{recipe}_system", emit,
    )
    belt_tiles = sum(
        1 for action in route_actions
        if "transport-belt" in action.get("entity", "")
    )
    return plan, foundation, interface.provider, belt_type, belt_tiles


def _retire_standing_bootstrap_cells(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, ore: str, emit: Callable[[str], None],
) -> int:
    """Deconstruct every standing logistic bootstrap cell for `recipe`.

    User standard, 2026-08-22: the temporary requester-fed cell is a scaffold,
    not a destination -- the moment the real belt-driven system becomes
    affordable, the cell goes FIRST and the proper refinery follows. Leaving
    both up split the plate supply and left orphaned furnaces everywhere
    (live run 15). Returns the number of cells deconstructed."""
    try:
        standing = live_base.bootstrap_cell_origins(client, surface, force, ore)
    except Exception:  # survey unavailable (dry harness): nothing to retire
        return 0
    removed = 0
    for spot in standing:
        origin = (round(spot[0] - 1.5), round(spot[1] - 3.5))
        plan = retire_logistic_smelter_plan(recipe, ore, origin)
        plan["surface"], plan["force"] = surface, force
        _submit(client, bridge, surface, plan,
                f"retire_logistic_{recipe}_cell", emit)
        removed += 1
    if removed:
        emit(
            f"BOOTSTRAP SWAP: deconstructed {removed} temporary {recipe} "
            "cell(s) -- installing the belt-driven system in their place"
        )
    return removed


def _build_initial_plate_smelter(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, extraction, ore_output: Point, reference_point: Point,
    emit: Callable[[str], None], *, preflight_only: bool = False,
) -> Point:
    """Build the first modular refinery with one continuous mine-to-ore belt."""
    del reference_point
    plan, foundation, provider, belt_type, belt_tiles = _prepare_initial_refinery(
        client, surface, force, recipe, extraction, ore_output, emit,
        preflight_only=preflight_only,
    )
    if not preflight_only:
        # Affordability just passed: the temporary requester-fed cell's whole
        # reason to exist is gone. Deconstruct it FIRST (user standard), then
        # install the belt-driven refinery in its place.
        _retire_standing_bootstrap_cells(
            client, bridge, surface, force, recipe, extraction.ore, emit,
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
    _submit(
        client, bridge, surface, plan, f"modular_{recipe}_refinery", emit,
        stage_coverage=lambda: _ensure_plan_construction_coverage(
            client, bridge, surface, force, coverage_plan, emit,
        ),
    )
    _bring_modular_refinery_up(
        client, bridge, surface, force, recipe, plan,
        FURNACES_PER_MODULE,
        extraction.smelter_origin, emit,
        feed_grace_seconds=transport_grace_seconds(belt_type, belt_tiles),
        variant="basic",
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
    expand: bool = False,
) -> Point:
    """Build or expand one cohesive mine-to-smelter system."""
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
    if not expand:
        starved = False
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
        raise StuckError(
            f"{recipe} expansion has no recoverable managed refinery; refusing to "
            "expand its mine ahead of the refinery"
        )
    foundation = None
    if cohesive_target is not None:
        try:
            foundation = _assert_atomic_plate_expansion_affordable(
                client, surface, force, recipe, extraction, existing_smelter,
                cohesive_target, emit,
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
            # A cold base cannot afford the first system's belt bill (belts
            # are made from this very plate). That is not a demand to queue --
            # it resolves once a plate producer exists, which the REAL submit
            # below bootstraps. Anything else stays a normal shortage.
            if not _cold_start_belt_shortage(client, surface, force, recipe, error):
                raise
            emit(
                f"  PLATE BOOTSTRAP PENDING: {error.required} short for the "
                "first system; the mine still builds and the temporary "
                "smelter follows this pass"
            )
    _submit_mining_plan(
        client, bridge, surface, force, extraction, ore_output, emit,
    )
    if cohesive_target is not None:
        provider = _extend_plate_smelter(
            client, bridge, surface, force, recipe, existing_smelter,
            cohesive_target, ore_output, emit,
        )
    else:
        try:
            provider = _build_initial_plate_smelter(
                client, bridge, surface, force, recipe, extraction, ore_output,
                reference_point, emit,
            )
        except StuckError as error:
            if "intersects real infrastructure" not in str(error):
                raise
            raise ProductionPrerequisiteDeferred(str(error)) from error
        except MaterialShortage as error:
            if not _cold_start_belt_shortage(client, surface, force, recipe, error):
                raise
            provider = _bootstrap_logistic_plate_line(
                client, bridge, surface, force, recipe, extraction, emit,
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


def build_logistic_smelter(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, ore: str, origin: Point, ore_output: Point,
    emit: Callable[[str], None], *, ore_pickup: Point | None = None,
) -> Point:
    """Build the first plate line without depending on belt production.

    `ore_pickup` names the logistic chest bots actually draw ore from; it
    must be a chest, not the mine's belt tile, or the coverage check waits
    forever for a network that can never exist."""
    ox, oy = round(origin[0]), round(origin[1])
    plan = generate_logistic_smelter(recipe, ore, (ox, oy))
    plan["surface"], plan["force"] = surface, force
    _submit(client, bridge, surface, plan, f"logistic_{recipe}_bootstrap", emit)
    return _serve_bootstrap_cell(
        client, bridge, surface, force, recipe, ore,
        (ox, oy), ore_pickup, emit,
    )


# A plate refinery's bill is belts AND the inserters that feed its furnaces --
# both are made FROM this very plate. On a cold base either shortfall is
# unaffordable forever even with perfect mall behaviour: the demand for the
# system's own inputs feeding back into itself.
_BELT_FAMILY_ENTITIES = frozenset({
    "transport-belt", "underground-belt",
    "fast-transport-belt", "fast-underground-belt",
    "express-transport-belt", "express-underground-belt",
})


def _cold_start_belt_shortage(
    client: RconClient, surface: str, force: str, recipe: str,
    error: Exception,
) -> bool:
    """Whether the FIRST plate system failed affordability on belts/inserters.

    Only then is the beltless smelter the right answer: it exists to break
    exactly this circle, and any other shortfall has a producer that can grow.
    A standing compact bootstrap cell still counts as cold -- it exists
    precisely because plates do not flow yet."""
    if recipe not in ("iron-plate", "copper-plate"):
        return False
    required = getattr(error, "required", None)
    if not required or not set(required).issubset(_BELT_FAMILY_ENTITIES):
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

    Regular belts are the bootstrap's scarcest commodity -- run 5 spent nearly
    every yellow belt on the iron scaffold and the copper system then could
    not afford its own bridge. The starter kit ships fast belts for exactly
    this phase: spend those first and save regular belts to seed the belt
    mall cell. Falls back to regular once fast stock is gone, so the choice
    self-balances as the base matures."""
    available = live_base.available_items(client, surface, force)
    if available.get("fast-transport-belt", 0) >= _ESSENTIAL_FAST_BELT_MIN:
        return "fast-transport-belt"
    return _DEFAULT_BELT


_ESSENTIAL_FAST_BELT_MIN = 40


_GENERATION_CHECK_INTERVAL_TICKS = 1800  # 30s of game time between grid checks


def _top_up_solar_generation(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    near: Point, emit: Callable[[str], None],
) -> bool:
    """Build one validated rectangular power unit when sizing has not converged."""
    script_output = getattr(bridge, "script_output", Path(""))
    return ensure_power_capacity(
        client=client, bridge=bridge, surface=surface, force=force,
        near=near, script_output=script_output, emit=emit, submit=_submit,
    )


def _mine_logistic_intake(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    ore: str, ore_output: Point, emit: Callable[[str], None],
) -> Point:
    """Chest + inserter lifting ore off a belt-only mine into logistics.

    Modern mines emit a belt-only output with NO logistic interface, so a
    requester-fed smelter starves forever no matter how well it is built --
    observed live 2026-08-22: the cell's chest held a correct copper-ore
    request while the mine belt ended in open air. Candidate anchors are the
    drill drop columns and both run ends (where a dead-ended belt jams); each
    is tried in all four directions because modular mine rows wedge their
    drop columns between two drill bodies -- only the end cap past the last
    column had room (live run 9). Positions keep half-tile centres.
    Idempotent: an existing pair is returned, not     rebuilt."""
    # PREFERRED INTERFACE: a blocked drill's own empty drop tile. Modular rows
    # wedge every belt-side tile between drill bodies, but a drill staring at
    # 'waiting for space' has a bare drop tile that becomes a provider chest
    # with zero demolition (live run 9: no legal inserter placement existed).
    try:
        drop = live_base.blocked_drill_drop_tile(
            client, surface, tuple(ore_output),
        )
    except Exception:  # survey hiccup: fall through to belt-tap candidates
        drop = None
    if drop is not None:
        standing = live_base.entity_at(client, surface, drop)
        if standing is None:
            plan = {"phases": [{
                "name": f"mine_{ore}_drop_chest",
                "actions": [
                    {"action_type": "place_entity",
                     "entity": "passive-provider-chest",
                     "position": {"x": drop[0], "y": drop[1]}},
                ],
            }]}
            plan["surface"], plan["force"] = surface, force
            _submit(client, bridge, surface, plan,
                    f"mine_{ore}_drop_chest", emit)
            emit(
                f"  INTAKE: ore-blocked drill at the {ore} mine gets a drop "
                f"chest at {drop}"
            )
        return drop
    try:
        candidates = live_base.intake_candidate_tiles(
            client, surface, ore_output,
        ) or [(float(ore_output[0]), float(ore_output[1]))]
    except Exception:  # survey hiccup: the output tile remains a safe guess
        candidates = [(float(ore_output[0]), float(ore_output[1]))]
    # End caps first: modular rows wedge their drop columns between drill
    # bodies, and a dead-ended belt JAMS at its far end -- the one place an
    # inserter always finds items (live runs 8-10).
    ends = [c for c in candidates if c in candidates[-2:]]
    drops = [c for c in candidates if c not in ends]
    candidates = ends + drops
    # Facing is what the EXECUTOR makes it: live readback showed a
    # 'north'-facing inserter picking up from its NORTH neighbour and dropping
    # south -- so the label names the PICKUP side here, and each offset below
    # states the pickup neighbour relative to the anchor tile.
    directions = ((0.0, -1.0, "south"), (0.0, 1.0, "north"),
                  (-1.0, 0.0, "east"), (1.0, 0.0, "west"))
    dead_pair_positions: set[tuple[float, float]] = set()
    for anchor in candidates:
        # An existing LIVE pair on this anchor returns its chest. A pair whose
        # inserter has been source-starved with an empty chest is a dead tap
        # (the run-9 legacy pair at the upstream tail): skip that whole anchor
        # -- both the return AND a fresh placement beside the corpse, which
        # would starve identically (live run 10).
        anchor_dead = False
        found_chest = None
        for dx, dy, _facing in directions:
            inserter = (anchor[0] + dx, anchor[1] + dy)
            chest = (anchor[0] + 2 * dx, anchor[1] + 2 * dy)
            standing_inserter = live_base.entity_at(client, surface, inserter)
            standing_chest = live_base.entity_at(client, surface, chest)
            if not (
                standing_inserter is not None
                and standing_inserter.get("type") == "inserter"
                and standing_chest is not None
                and standing_chest.get("name") == "passive-provider-chest"
            ):
                continue
            status = live_base.entity_status_name(
                client, surface, tuple(inserter),
            )
            chest_empty = not live_base.chest_has_items(
                client, surface, tuple(chest),
            )
            if status == "waiting_for_source_items" and chest_empty:
                dead_pair_positions.add(chest)
                anchor_dead = True
                break
            found_chest = chest
            break
        if anchor_dead:
            continue
        if found_chest is not None:
            return found_chest
        for dx, dy, facing in directions:
            inserter = (anchor[0] + dx, anchor[1] + dy)
            chest = (anchor[0] + 2 * dx, anchor[1] + 2 * dy)
            if chest in dead_pair_positions:
                continue
            # Ore ground is buildable: an east-flow collector's head sits at
            # the patch's east edge, so the intake spots past it are resource
            # tiles (live run of 2026-08-24 06:56 starved copper because the
            # resource entity counted as an occupant).
            if any(
                spot is not None and spot.get("type") != "resource"
                for spot in (
                    live_base.entity_at(client, surface, inserter),
                    live_base.entity_at(client, surface, chest),
                )
            ):
                continue
            plan = {"phases": [{
                "name": f"mine_logistic_intake_{ore}",
                "actions": [
                    {"action_type": "place_entity", "entity": "fast-inserter",
                     "position": {"x": inserter[0], "y": inserter[1]},
                     "direction": facing},
                    {"action_type": "place_entity",
                     "entity": "passive-provider-chest",
                     "position": {"x": chest[0], "y": chest[1]}},
                ],
            }]}
            plan["surface"], plan["force"] = surface, force
            _submit(client, bridge, surface, plan, f"mine_{ore}_intake", emit)
            # The head of an east-flow collector sits past the row's power
            # scaffold, so the intake inserter can land outside every pole's
            # supply area and starve the whole logistic feed silently (live
            # run of 2026-08-24 14:05: two temp furnaces no_ingredients for
            # 300s while the chest sat empty).
            extend_power(client, bridge, surface, force, inserter, emit)
            return chest
    raise StuckError(
        f"{ore}: every tile around the mine belt row at {candidates[:2]} is "
        "occupied -- no room for a logistic intake"
    )


# A logistic robot flies loaded at well under its top speed and every delivery
# is a round trip; sizing the health window from the default 20 s called a
# correctly-built cell starved while bots were still in flight (live 2026-08-22:
# two furnaces condemned at exactly the default grace). The window scales with
# the actual pickup distance instead.
_LOGISTIC_BOT_SPEED_TPS = 1.5
_LOGISTIC_CELL_GRACE_CAP_SECONDS = 300.0


def _bot_delivery_grace(pickup: Point, origin: Point) -> float:
    """Health-window seconds one bot round trip of ore may reasonably take."""
    distance = max(
        abs(pickup[0] - origin[0]), abs(pickup[1] - origin[1]),
    )
    flight = 2 * distance / _LOGISTIC_BOT_SPEED_TPS + 6
    return min(_LOGISTIC_CELL_GRACE_CAP_SECONDS, 20.0 + 3 * flight)


def _bootstrap_cell_geometry(origin: Point) -> tuple[list[Point], list[Point]]:
    """Machine and provider positions of the compact bootstrap cell."""
    ox, oy = round(origin[0]), round(origin[1])
    machines = [(ox + 1.5, oy + 0.5), (ox + 1.5, oy + 6.5)]
    providers = [(ox + 1.5, oy - 2.5), (ox + 1.5, oy + 9.5)]
    return machines, providers


def _serve_bootstrap_cell(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, ore: str, origin: Point, ore_pickup: Point | None,
    emit: Callable[[str], None],
) -> Point:
    """Bring the compact cell up and hold it to a bot-aware health standard."""
    ox, oy = round(origin[0]), round(origin[1])
    machines, providers = _bootstrap_cell_geometry(origin)
    substation = (ox - 4.0, oy + 3.5)
    bring_stage_up(
        client, bridge, surface, force, f"logistic bootstrap for {recipe}",
        (ox, oy), ((ox - 10, oy - 10), (ox + 10, oy + 12)), substation,
        machines, emit,
        logistic_chest_positions=[ore_pickup or (ox + 1.5, oy + 3.5), *providers],
    )
    stuck = _diagnose_machines(
        client, surface, machines, emit, bridge=bridge, force=force,
        grace_seconds=_bot_delivery_grace(
            ore_pickup or (ox + 1.5, oy + 3.5), origin,
        ),
    )
    if stuck:
        raise StuckError(
            f"logistic bootstrap for {recipe} built but not healthy: {stuck}"
        )
    return providers[-1]


def _bootstrap_logistic_plate_line(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, extraction, emit: Callable[[str], None],
) -> Point:
    """Temporary requester-fed furnaces that make plates WITHOUT belts.

    The compact logistic cell breaks the belts-need-plates circle so the mall
    can finish belt stock; normal goal planning later upgrades it to a
    belt-fed refinery and retires it (`BOOTSTRAP UPGRADE`). It is deliberately
    NOT cached in MANAGED_INTERMEDIATE_SOURCES -- caching would hide it from
    that upgrade survey forever."""
    emit(
        f"PLATE BOOTSTRAP: the first {recipe} system cannot afford its belts "
        "yet -- opening a temporary requester-fed smelter so plates exist "
        "before the belts that carry them"
    )
    # The cell is bot-fed: give it a real logistic source of ore first, or
    # its requesters can never be served (belt-only mines expose none).
    ore_pickup = _mine_logistic_intake(
        client, bridge, surface, force, extraction.ore,
        extraction.ore_output, emit,
    )
    # A cell from an earlier pass may already stand here. Rebuilding at a
    # re-surveyed origin orphaned the first cell and killed run 4; recognize
    # and SERVICE what exists instead. The cell's REQUESTER is its identity:
    # recipe-less furnaces are invisible to machine surveys (run 8 rebuilt
    # beside a starving twin for exactly that reason).
    standing = live_base.bootstrap_cell_origins(
        client, surface, force, extraction.ore,
    )
    existing_origin = None
    if standing:
        ox, oy = round(standing[0][0] - 1.5), round(standing[0][1] - 3.5)
        existing_origin = (ox, oy)
        emit(
            f"  PLATE BOOTSTRAP: reusing the standing {recipe} cell at "
            f"{existing_origin} rather than opening another"
        )
        provider = _serve_bootstrap_cell(
            client, bridge, surface, force, recipe, extraction.ore,
            existing_origin, ore_pickup, emit,
        )
        UNBACKED_DRAWS.discard(recipe)
        return provider
    provider = build_logistic_smelter(
        client, bridge, surface, force, recipe, extraction.ore,
        extraction.smelter_origin, extraction.ore_output, emit,
        ore_pickup=ore_pickup,
    )
    UNBACKED_DRAWS.discard(recipe)
    return provider


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
    _ensure_plan_construction_coverage(
        client, bridge, surface, force, replacement, emit,
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
        extend_power(client, bridge, surface, force, target, emit)


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
    mall_storage_limit = max(stock_target, storage_limit or stock_target)
    if not upgrade_bootstrap and storage_limit is None:
        mall_storage_limit += live_base.logistic_request_total(
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
        existing=existing, spec=spec, production_target=stock_target,
        mall_storage_limit=mall_storage_limit, fill_provider=fill_provider,
        demand=demand, saturated=saturated, promoted_count=promoted_count,
        promote_to_line=promote_to_line,
        at_size=existing is None or existing.machine_count >= minimum_machines,
    )


def _refresh_mall_cell(
    client: RconClient, bridge: GameBridge, surface: str, force: str, item: str,
    plan: _LinePlan, emit: Callable[[str], None], *, upgrade_bootstrap: bool,
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
        try:
            belt_type = _essential_belt_type(client, surface, force)
            extraction = plan_local_extraction(
                client, surface, force, item, reference_point, 3,
                belt_type=belt_type, inserter_type=_DEFAULT_INSERTER,
                reuse_existing=True,
                belt_stock=live_base.available_items(
                    client, surface, force,
                ).get(belt_type, 0),
            )
        except stage_extraction.PendingSystemDeferred as error:
            emit(f"PLATE SYSTEM PENDING: {error}")
            raise ProductionPrerequisiteDeferred(str(error)) from error
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
        output = build_conversion_stage(
            client, bridge, surface, force, item, sources, reference_point, emit,
            machine_count=1, allow_logistic_inputs=True, side_tap_output=True,
        )
        MANAGED_INTERMEDIATE_SOURCES[item] = output
        emit(
            "  PERSISTENT INTERMEDIATE: steel-plate now has a dedicated "
            "logistic-fed furnace producer"
        )
        return
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
        output = build_compact_mall_stage(
            client, bridge, surface, force, item, sources, reference_point,
            bring_stage_up, emit, stock_target=mall_storage_limit,
            stock_gate_target=stock_gate_target,
            fill_chest=plan.fill_provider,
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
        return MANAGED_INTERMEDIATE_SOURCES[item]
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
        storage_limit=storage_limit, fill_provider=fill_provider,
    )
    mall_provider = _refresh_mall_cell(
        client, bridge, surface, force, item, plan, emit,
        upgrade_bootstrap=upgrade_bootstrap,
        stock_gate_target=stock_gate_target,
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
            mall_provider=mall_provider, upgrade_bootstrap=upgrade_bootstrap,
        )
    if not _mineable(item):
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
        output = ensure_produced(
            client, bridge, surface, force, item, reference_point, emit,
            upgrade_bootstrap=False, stock_target=target,
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

# Cells whose recipes CONSUME belts wait behind the blueprint reserve: an
# underground cell eats two belts per craft, and while construction ghosts
# still need belts the producer must not compete with them for the same stock.
# User standard, 2026-08-22: produced belts go to the active blueprint first;
# the consumer resumes once stock recovers past the floor.
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
    other_pending = {
        other for other, tgt in mall_targets.items() if other != item
        and live_base.available_items(client, surface, force).get(other, 0) < tgt
    }
    ready, output = _ensure_mall_item(
        client, bridge, surface, force, item, target, mall_targets,
        reference_point, emit, background=False,
    )
    if not ready:
        if _deliver_cell_ingredients(
            client, bridge, surface, force, item, reference_point, emit,
        ):
            return
        if other_pending:
            # This item depends on prerequisites still queued beside it.
            # Serving it again first would re-queue the same numbers every
            # pass while they starve behind it in the ranking (live runs
            # 11/16: landfill outranked the belts and chests it demanded).
            # Yield the rank until they are satisfied.
            priorities.defer(
                item, live_base.game_tick(client),
                "waiting on its own queued prerequisite",
            )
            emit(f"  PRIORITY DEFERRED: {item} behind its queued prerequisite")
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
) -> bool:
    """Grow one plate line to the furnace count its own prep draw implies.

    Returns whether it spent the pass. A material shortage hands the pass back
    so the mall can build the drills it is short of -- prep runs before the mall
    now, so holding on would re-hit the identical shortage forever.
    """
    available = live_base.available_items(client, surface, force)
    pending = (pending_materials or {}).get(short_plate)
    if pending and any(available.get(item, 0) < count for item, count in pending.items()):
        return False
    if pending_materials is not None:
        pending_materials.pop(short_plate, None)
    targets = dict(demand_targets or {})
    for item, target in mall_targets.items():
        targets[item] = max(targets.get(item, 0), target)
    adjusted_draw = demand_adjusted_plate_draw(targets, available)
    wanted_furnaces = smelter_count_for_draw(short_plate, adjusted_draw[short_plate])
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
    plate_line = live_base.find_line(
        client, surface, force, short_plate,
        LINE_RECIPES[short_plate]["machine"],
    )
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
        f"{adjusted_draw[short_plate]:.2f}/s "
        f"(have {have}, drill phase {drill_phase_for_draw(adjusted_draw[short_plate])}) ---"
    )
    try:
        output_source = build_mining_stage(
            client, bridge, surface, force, short_plate,
            reference_point, emit, expand=plate_line is not None,
        )
        if output_source is not None:
            MANAGED_INTERMEDIATE_SOURCES[short_plate] = output_source
            emit(
                f"  PLATE SOURCE: recorded {short_plate} provider at "
                f"{output_source} for downstream logistic consumers"
            )
    except ProductionPrerequisiteDeferred as deferred:
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
        return False
    except (StuckError, ValueError) as error:
        deferred_targets[short_plate] = wanted_furnaces
        prepped.add(short_plate)
        emit(
            f"  PREP DEFERRED: {short_plate} extraction stays at {have} "
            f"furnace(s) -- {error}"
        )
    return True


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


def mall_reserve_for(
    client: RconClient, surface: str, force: str, item: str, target: int,
) -> MallReserve:
    """Reserve ahead while scarce; fill the chest once AM3 is self-produced."""
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
        f"finding it -- see the repeated reason above.{unbacked}"
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
    UNBACKED_DRAWS.clear()   # module state must not leak between runs
    MANAGED_INTERMEDIATE_SOURCES.clear()
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
) -> dict:
    """Loop: survey -> decide the single deepest missing stage -> build it ->
    repeat, until `goal_item` has a real, working line or the builder is
    genuinely stuck (raises StuckError rather than guessing)."""
    validate_builder_target(goal_item, surface, LINE_RECIPES)
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
            # PREP BEFORE MALL WORK. These standing cells refill the
            # intermediates used by exact shortages and background reserves.
            # A blocked prep pass hands control back so the mall can build the
            # missing machine instead of retrying the same shortage forever.
            if _prep_intermediate(
                client, bridge, surface, force, prepped, mall_targets,
                reference_point, emit,
            ):
                continue
            # The belt cell goes up before extraction: mines are the biggest
            # belt consumers, and a producer that never existed cannot refill
            # what they spend.
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
        raise StuckError(f"Did not reach a working {goal_item} line within {max_iterations} iterations")
    finally:
        end_run_budget()
        client.close()
        bridge.close()
