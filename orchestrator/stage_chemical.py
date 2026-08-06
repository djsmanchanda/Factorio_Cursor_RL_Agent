# Path: orchestrator/stage_chemical.py
# Purpose: Build the smallest real-Nauvis oil cell required by chemical science.

from __future__ import annotations

import math
from collections.abc import Callable

from orchestrator import chemical_survey, extraction_state, live_base, resource_patches
from orchestrator.game_bridge import GameBridge
from orchestrator.mine_retirement import retire_depleted_mines
from orchestrator.stage_extraction import (
    choose_mining_origin, direct_mine_plan, existing_mine_service_geometry,
)
from orchestrator.stage_services import (
    StuckError, _diagnose_machines, _logistic_chest_positions, _submit,
    _wait_for_ghosts, extend_roboport_coverage,
)
from orchestrator.stage_transport import (
    _publish_output_chest, _swap_infinity_chests, ensure_ingredient_transport,
)
from planners.fluid_layouts import (
    fluid_network_segments, generate_fluid_machine_row, header_attachment,
)
from planners.fluid_routing import (
    generate_shortest_fluid_chain_link, shortest_fluid_chain_segments,
)
from planners.infrastructure import strip_local_power
from planners.infrastructure_geometry import footprint_tile_indices
from planners.plan_validation import ENTITY_FOOTPRINTS
from planners.resource_layouts import generate_offshore_pump_source, generate_pumpjack_source
from tools.rcon_client import RconClient

Point = tuple[float, float]
ServiceStage = Callable[..., None]
EnsureItem = Callable[[str], Point | None]


def _merge(*plans: dict) -> dict:
    return {"phases": [phase for plan in plans for phase in plan["phases"]]}


def _separate_landfill_ghosts(*plans: dict) -> tuple[dict | None, dict]:
    """Keep tile construction ahead of pipes that need the new land."""
    landfill = []
    fluid_phases = []
    for plan in plans:
        for phase in plan["phases"]:
            actions = phase["actions"]
            landfill.extend(
                action for action in actions
                if action.get("action_type") == "place_tile_ghost"
            )
            fluid_actions = [
                action for action in actions
                if action.get("action_type") != "place_tile_ghost"
            ]
            if fluid_actions:
                fluid_phases.append({"name": phase["name"], "actions": fluid_actions})
    foundation = None
    if landfill:
        foundation = {"phases": [{"name": "oil_landfill_foundation", "actions": landfill}]}
    return foundation, {"phases": fluid_phases}


def _ensure_plan_construction_coverage(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    plan: dict, emit: Callable[[str], None],
) -> None:
    """Cover the complete chemical footprint before any construction ghost lands."""
    if not hasattr(client, "command"):
        return
    positions = [
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
    ]
    if not positions:
        return
    xs, ys = zip(*positions)
    for target in sorted({
        (min(xs), min(ys)), (min(xs), max(ys)),
        (max(xs), min(ys)), (max(xs), max(ys)),
    }):
        extend_roboport_coverage(client, bridge, surface, force, target, emit)


def _submit_oil_cell_plans(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    plans: list[dict], links: list[dict], emit: Callable[[str], None],
) -> None:
    """Submit landfill, wait for solid ground, then submit its pipe route."""
    landfill, fluid_links = _separate_landfill_ghosts(*links)
    if landfill is None:
        combined = _merge(*plans, *links)
        combined["surface"], combined["force"] = surface, force
        _ensure_plan_construction_coverage(client, bridge, surface, force, combined, emit)
        _submit(client, bridge, surface, combined, "chemical_oil_cell", emit)
        return
    foundation = _merge(*plans, landfill)
    foundation["surface"], foundation["force"] = surface, force
    _ensure_plan_construction_coverage(client, bridge, surface, force, foundation, emit)
    _submit(client, bridge, surface, foundation, "chemical_oil_cell_foundation", emit)
    remaining = _wait_for_ghosts(
        client, surface, force, _area(landfill), include_entity_ghosts=False,
    )
    if remaining:
        raise StuckError(
            f"chemical_oil_cell landfill foundation has {remaining} ghost(s) remaining; "
            "refusing to place pipe ghosts on water before the landfill is built"
        )
    fluid_links["surface"], fluid_links["force"] = surface, force
    _ensure_plan_construction_coverage(client, bridge, surface, force, fluid_links, emit)
    _submit(client, bridge, surface, fluid_links, "chemical_oil_cell_fluid_links", emit)


def _positions(plan: dict, entity: str) -> list[Point]:
    return [
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == entity
    ]


def _area(plan: dict, margin: float = 12) -> tuple[Point, Point]:
    points = [
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if "position" in action
    ]
    return (
        (min(x for x, _ in points) - margin, min(y for _, y in points) - margin),
        (max(x for x, _ in points) + margin, max(y for _, y in points) + margin),
    )


def _substation(plan: dict) -> Point:
    return _positions(plan, "substation")[0]


def _provider(plan: dict) -> Point:
    return _positions(plan, "passive-provider-chest")[0]


def _planned_hard_tiles(*plans: dict) -> set[tuple[int, int]]:
    hard: set[tuple[int, int]] = set()
    for plan in plans:
        for phase in plan["phases"]:
            for action in phase["actions"]:
                if action.get("entity") in {"pipe", "pipe-to-ground"}:
                    continue
                if "position" not in action:
                    continue
                hard |= footprint_tile_indices(
                    (action["position"]["x"], action["position"]["y"]),
                    ENTITY_FOOTPRINTS.get(action["entity"], 1),
                )
    return hard


def ensure_coal_mine(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    reference: Point, service_stage: ServiceStage, emit: Callable[[str], None],
) -> Point | None:
    try:
        retire_depleted_mines(client, bridge, surface, force, "coal", reference, emit)
    except RuntimeError as error:
        raise StuckError(str(error)) from error
    existing = extraction_state.find_resource_mine(client, surface, force, "coal", reference)
    if existing is not None:
        if existing.pending:
            raise StuckError("existing coal mine is incomplete; refusing to duplicate it")
        return existing.output
    found = resource_patches.nearest_viable_patch(client, surface, "coal", reference)
    if found is None:
        raise StuckError("No coal patch found within the local 400-tile search")
    _nearest, patch_min, patch_max = found.nearest, found.minimum, found.maximum
    chosen = choose_mining_origin(
        reference, patch_min, patch_max, 2,
        lambda lo, hi: live_base.area_clear(client, surface, lo, hi),
        lambda drills: live_base.drill_footprints_have_resource(
            client, surface, "coal", drills,
        ),
    )
    if chosen is None:
        raise StuckError("No clear two-drill coal extraction site found")
    origin, count = chosen
    plan, output = direct_mine_plan(
        origin, count, belt_type="transport-belt", inserter_type="fast-inserter",
    )
    plan = strip_local_power(plan, remove_substations=False)
    _publish_output_chest(plan)
    plan["surface"], plan["force"] = surface, force
    _submit(client, bridge, surface, plan, "mining_coal", emit)
    service_origin, area, substation, drills = existing_mine_service_geometry(
        output, count, shared_belt_y=output[1] + 2,
    )
    # `direct_mine_plan` emits a continuous belt endpoint, not a chest. Passing
    # that belt tile to the logistic probe makes its nil logistic network look
    # like an orphaned chest and spends every remediation round waiting on a
    # coverage fault that cannot exist. The belt is serviced by power only.
    service_stage(
        client, bridge, surface, force, "mining stage for coal", service_origin,
        area, substation, drills, emit,
        logistic_chest_positions=[],
    )
    return None


def _existing_outputs(
    client: RconClient, surface: str, force: str,
) -> dict[str, Point] | None:
    result: dict[str, Point] = {}
    present = []
    for recipe in ("plastic-bar", "sulfur"):
        line = live_base.find_line(client, surface, force, recipe, "chemical-plant")
        present.append(line is not None)
        if line and line.working_count:
            chest = live_base.nearest_container(client, surface, force, line.machine_positions[-1])
            if chest:
                result[recipe] = chest
    if len(result) == 2:
        return result
    if any(present):
        raise StuckError("partial oil cell exists but is not healthy; repair it before adding another")
    return None


def ensure_oil_cell(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    reference: Point, ensure_item: EnsureItem, service_stage: ServiceStage,
    emit: Callable[[str], None],
) -> dict[str, Point] | None:
    existing = _existing_outputs(client, surface, force)
    if existing is not None:
        return existing
    coal = ensure_item("coal")
    if coal is None:
        return None
    oil = live_base.nearest_resource(client, surface, "crude-oil", reference)
    water = chemical_survey.nearest_offshore_pump_site(client, surface, reference)
    if oil is None:
        raise StuckError("No crude-oil patch found within the local 400-tile search")
    if water is None:
        raise StuckError("No buildable shoreline found within the local 400-tile search")
    oil_pos = oil[0]
    oil_site = {
        "position": oil_pos, "output": (math.floor(oil_pos[0] - 1), math.floor(oil_pos[1] + 1)),
        "resource": "crude-oil", "direction": "west",
    }
    cell = live_base.find_clear_area(
        client, surface, reference, 42, 34, avoid_resources=True, resource_clearance=5,
    )
    if cell is None:
        raise StuckError("No ore-free 42x34 area found for the oil cell")
    ox, oy = round(cell[0]), round(cell[1])
    refinery = generate_fluid_machine_row("basic-oil-processing", 1, ox, oy)
    plastic = generate_fluid_machine_row("plastic-bar", 2, ox + 18, oy)
    sulfur = generate_fluid_machine_row("sulfur", 2, ox + 18, oy + 16)
    crude_source = generate_pumpjack_source([oil_site], [oil_site["output"]])
    water_source = generate_offshore_pump_source([water], [water["output"]])
    plans = [
        strip_local_power(plan, remove_substations=False)
        for plan in (crude_source, water_source, refinery, plastic, sulfur)
    ]
    crude_source, water_source, refinery, plastic, sulfur = plans
    plastic_feed = _swap_infinity_chests(plastic, {"coal": "logistic"})["coal"]
    _publish_output_chest(plastic)
    _publish_output_chest(sulfur)

    crude_to = header_attachment("basic-oil-processing", "crude-oil", 1, ox, oy)["attach"]
    petroleum_from = header_attachment("basic-oil-processing", "petroleum-gas", 1, ox, oy)["attach"]
    plastic_gas = header_attachment("plastic-bar", "petroleum-gas", 2, ox + 18, oy)["attach"]
    sulfur_gas = header_attachment("sulfur", "petroleum-gas", 2, ox + 18, oy + 16)["attach"]
    sulfur_water = header_attachment("sulfur", "water", 2, ox + 18, oy + 16)["attach"]
    endpoints = [oil_site["output"], water["output"], crude_to, petroleum_from,
                 plastic_gas, sulfur_gas, sulfur_water]
    margin = 24
    hard = live_base.occupied_tiles(
        client, surface,
        (min(x for x, _ in endpoints) - margin, min(y for _, y in endpoints) - margin),
        (max(x for x, _ in endpoints) + margin, max(y for _, y in endpoints) + margin),
        include_water=False,
    )
    hard |= _planned_hard_tiles(*plans)
    terrain_water = live_base.water_tiles(
        client, surface,
        (min(x for x, _ in endpoints) - margin, min(y for _, y in endpoints) - margin),
        (max(x for x, _ in endpoints) + margin, max(y for _, y in endpoints) + margin),
    )
    terrain_water -= hard
    endpoint_tiles = {(math.floor(x), math.floor(y)) for x, y in endpoints}
    terrain_water -= endpoint_tiles
    hard -= endpoint_tiles
    # Water is hard for surface pipes, but a narrow contiguous patch may be
    # crossed by a paired pipe-to-ground span. Wider water remains a route
    # obstacle and must be detoured around.
    foreign = (
        [
            {
                "fluid": "crude-oil",
                "separated_by_pump": False,
                "tiles": [oil_site["output"]],
            },
            {
                "fluid": "water",
                "separated_by_pump": False,
                "tiles": [water["output"]],
            },
        ]
        + fluid_network_segments("basic-oil-processing", 1, ox, oy)
        + fluid_network_segments("plastic-bar", 2, ox + 18, oy)
        + fluid_network_segments("sulfur", 2, ox + 18, oy + 16)
    )
    links = []
    for source, targets, fluid, existing_tiles in (
        (oil_site["output"], [crude_to], "crude-oil", [oil_site["output"]]),
        (petroleum_from, [plastic_gas, sulfur_gas], "petroleum-gas", []),
        (water["output"], [sulfur_water], "water", [water["output"]]),
    ):
        # Fluid routing rejects an impossible layout with ValueError. Left
        # uncaught it escapes run() as a raw traceback, which reads as a crash
        # rather than the bounded planning refusal it actually is.
        try:
            link = generate_shortest_fluid_chain_link(
                source, targets, fluid, foreign=foreign, hard_tiles=hard,
                tunnelable_tiles=terrain_water, clearance=0, search_margin=48,
                existing_tiles=existing_tiles, mixing_margin=True,
                allow_terrain_tunnels=True,
            )
            links.append(link)
            foreign.extend(shortest_fluid_chain_segments(
                source, targets, fluid, foreign=foreign, hard_tiles=hard,
                tunnelable_tiles=terrain_water, clearance=0, search_margin=48,
                mixing_margin=True, allow_terrain_tunnels=True,
            ))
        except ValueError as error:
            raise StuckError(
                f"{fluid} cannot be routed from {source} to {targets}: {error}"
            ) from error
    _submit_oil_cell_plans(client, bridge, surface, force, plans, links, emit)

    for name, plan, machine in (
        ("crude-oil source", crude_source, "pumpjack"),
        ("water source", water_source, "offshore-pump"),
        ("oil refinery", refinery, "oil-refinery"),
        ("plastic-bar stage", plastic, "chemical-plant"),
        ("sulfur stage", sulfur, "chemical-plant"),
    ):
        machines = _positions(plan, machine)
        service_stage(
            client, bridge, surface, force, name, machines[0], _area(plan),
            _substation(plan), machines, emit,
            logistic_chest_positions=_logistic_chest_positions(plan),
        )
    ensure_ingredient_transport(
        client, bridge, surface, force, "plastic-bar", "coal", coal,
        plastic_feed, 2, emit,
    )
    stuck = _diagnose_machines(
        client, surface, _positions(plastic, "chemical-plant") + _positions(sulfur, "chemical-plant"),
        emit, bridge=bridge, force=force,
    )
    if stuck:
        raise StuckError(f"oil cell built but not healthy: {stuck}")
    return {"plastic-bar": _provider(plastic), "sulfur": _provider(sulfur)}
