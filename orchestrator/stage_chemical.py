# Path: orchestrator/stage_chemical.py
# Purpose: Build the smallest real-Nauvis oil cell required by chemical science.

from __future__ import annotations

import math
from collections.abc import Callable

from orchestrator import chemical_survey, extraction_state, live_base, resource_patches
from orchestrator.game_bridge import GameBridge
from orchestrator.parts_mall import MaterialShortage
from orchestrator.extraction_transport import (
    planned_footprint_tiles, preflight_ingredient_transport,
)
from orchestrator.mine_retirement import retire_depleted_mines
from orchestrator.stage_extraction import (
    candidate_mining_origins, choose_mining_origin, direct_mine_plan,
    existing_mine_service_geometry,
)
from orchestrator.stage_services import (
    StuckError, _ROBOPORT_SERVICE_AREAS, _diagnose_machines,
    _ghost_materials, _logistic_chest_positions, _submit,
    _wait_for_ghosts, construction_supply_chain_is_scheduled, extend_power,
    extend_roboport_coverage, service_distance,
)
from orchestrator.stage_transport import (
    _direct_single_belt_feed, _publish_output_chest, _swap_infinity_chests,
    transport_grace_seconds,
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
from planners.resource_layouts import (
    generate_offshore_pump_source, generate_pumpjack_source,
    verified_offshore_pump_output_tile, verified_pumpjack_output_tile,
)
from tools.rcon_client import RconClient

Point = tuple[float, float]
ServiceStage = Callable[..., None]

_CHEMICAL_BELT_ROUTE_LIMIT = 400


def oil_processing_recipe(existing_refineries: int) -> str:
    """Only the first bootstrap refinery may use basic processing."""
    if existing_refineries < 0:
        raise ValueError("existing_refineries must be non-negative")
    return "basic-oil-processing" if existing_refineries == 0 else "advanced-oil-processing"


def pumpjack_crude_rate(yield_fraction: float, productivity_bonus: float) -> float:
    """Factorio pumpjack output in crude/s at speed 1."""
    if yield_fraction < 0 or productivity_bonus < 0:
        raise ValueError("oil yield and productivity must be non-negative")
    return 10.0 * yield_fraction * (1.0 + productivity_bonus)


def petroleum_rate(recipe: str, *, crack_all_outputs: bool = True) -> float:
    """Petroleum/s from one refinery, including complete cracking when asked."""
    if recipe == "basic-oil-processing":
        return 45.0 / 5.0
    if recipe != "advanced-oil-processing":
        raise ValueError(f"unknown oil processing recipe: {recipe}")
    if not crack_all_outputs:
        return 55.0 / 5.0
    # 25 heavy -> 18.75 light; 63.75 total light -> 42.5 petroleum.
    return (55.0 + (45.0 + 25.0 * 30.0 / 40.0) * 20.0 / 30.0) / 5.0


def _merge(*plans: dict) -> dict:
    return {"phases": [phase for plan in plans for phase in plan["phases"]]}


def _pumpjack_site_nearest(oil_position: Point, target: Point) -> dict:
    """Rotate a pumpjack so its real connector is closest to the local cell."""
    sites = []
    directions = ("north", "east", "south", "west")
    vectors = {
        "north": (0, -1), "east": (1, 0),
        "south": (0, 1), "west": (-1, 0),
    }
    for direction in directions:
        site = {
            "position": oil_position, "resource": "crude-oil",
            "direction": direction,
        }
        site["output"] = verified_pumpjack_output_tile(site)
        sites.append(site)
    return min(
        sites,
        key=lambda site: (
            -(
                vectors[site["direction"]][0] * (target[0] - oil_position[0])
                + vectors[site["direction"]][1] * (target[1] - oil_position[1])
            ),
            abs(site["output"][0] - target[0])
            + abs(site["output"][1] - target[1]),
            directions.index(site["direction"]),
        ),
    )


def _find_oil_cell_site(
    client: RconClient, surface: str, oil_position: Point,
) -> Point | None:
    """Reserve the chemical district around crude oil, not around the base."""
    return live_base.find_clear_area(
        client, surface, oil_position, 42, 34, max_radius=80.0,
        avoid_resources=True, resource_clearance=5,
    )


def _find_plastic_site(
    client: RconClient, surface: str, refinery_centre: Point, coal_output: Point,
) -> Point | None:
    """Put plastic between its two persistent sources when they are separated."""
    midpoint = (
        (refinery_centre[0] + coal_output[0]) / 2,
        (refinery_centre[1] + coal_output[1]) / 2,
    )
    return live_base.find_clear_area(
        client, surface, midpoint, 12, 12, max_radius=60.0,
        avoid_resources=True, resource_clearance=5,
    )


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
    plan: dict, emit: Callable[[str], None], *,
    reserved_tiles: set[tuple[int, int]] | None = None,
) -> None:
    """Cover every action position the chemical plan places.

    Positions are covered directly rather than through their bounding-box
    corners: the oil cell's pipe routes span hundreds of tiles, so two box
    corners routinely land on empty map with no action near them and chained a
    roboport chain out to nothing."""
    if not hasattr(client, "command"):
        return
    positions = sorted({
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if "position" in action
    })
    if not positions:
        return
    reserved = reserved_tiles or planned_footprint_tiles(plan)
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
            reserved_tiles=reserved,
        )
        ports = live_base.roboport_positions(client, surface, force)
        pending = uncovered(pending)
        if pending and pending[0] == target and not acted:
            raise StuckError(
                f"{target} needs construction coverage but the surface has no "
                "roboport to chain from"
            )
    raise StuckError(
        "chemical construction coverage did not converge after 64 chain "
        "attempts; investigate the coverage survey"
    )


_POWER_ENTITIES = frozenset({
    "small-electric-pole", "medium-electric-pole", "big-electric-pole",
    "substation",
})


def _filter_plan_actions(plan: dict, predicate: Callable[[dict], bool]) -> dict:
    """Copy the phases whose actions match one construction packet."""
    phases = []
    for phase in plan["phases"]:
        actions = [action for action in phase["actions"] if predicate(action)]
        if actions:
            phases.append({**phase, "actions": actions})
    return {"phases": phases}


def _submit_oil_cell_packets(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    packets: list[tuple[str, dict]], emit: Callable[[str], None], *,
    after_packet: Callable[[str], None] | None = None,
) -> None:
    """Ghost independent oil packets as soon as each material chain is ready."""
    future = _merge(*(plan for _name, plan in packets))
    reserved = planned_footprint_tiles(future)
    required = _ghost_materials(future)
    available = live_base.available_items(client, surface, force)
    unscheduled = {
        item: count for item, count in required.items()
        if available.get(item, 0) < count
        and not construction_supply_chain_is_scheduled(
            client, surface, force, item,
        )
    }
    if unscheduled:
        # Do not leave a remote district half-submitted. Once every missing
        # item has a complete production chain, individual packets may be
        # ghosted immediately and construction can overlap production.
        raise MaterialShortage(
            "chemical_oil_cell_packets", unscheduled, available,
        )
    for name, packet in packets:
        landfill, construction = _separate_landfill_ghosts(packet)
        if landfill is not None:
            landfill["surface"], landfill["force"] = surface, force
            _submit(
                client, bridge, surface, landfill, f"{name}_foundation", emit,
                stage_coverage=lambda plan=landfill: _ensure_plan_construction_coverage(
                    client, bridge, surface, force, plan, emit,
                    reserved_tiles=reserved,
                ),
            )
            remaining = _wait_for_ghosts(
                client, surface, force, _area(landfill),
                include_entity_ghosts=False,
            )
            if remaining:
                raise StuckError(
                    f"{name} landfill foundation has {remaining} ghost(s) "
                    "remaining; refusing to place pipes on water"
                )
        if not construction["phases"]:
            continue
        construction["surface"], construction["force"] = surface, force
        ghost_count = sum(
            1 for phase in construction["phases"]
            for action in phase["actions"]
            if action.get("action_type") == "place_ghost"
        )
        emit(f"  OIL PACKET: releasing {name} ({ghost_count} ghost(s))")
        _submit(
            client, bridge, surface, construction, name, emit,
            stage_coverage=lambda plan=construction: _ensure_plan_construction_coverage(
                client, bridge, surface, force, plan, emit,
                reserved_tiles=reserved,
            ),
        )
        if after_packet is not None:
            after_packet(name)


def _link_corridor_tiles(links: list[tuple[str, dict]]) -> set[tuple[int, int]]:
    """Pipe tiles later packets still have to ghost.

    Power bridging runs mid-pass (right after the backbone packet) while
    pipelines submit last, so a chain planned against a fresh snapshot still
    marches through the corridor unless it is reserved explicitly.
    """
    tiles: set[tuple[int, int]] = set()
    for _name, link in links:
        for phase in link["phases"]:
            for action in phase["actions"]:
                if (
                    action.get("entity") in {"pipe", "pipe-to-ground"}
                    and "position" in action
                ):
                    tiles.add((
                        math.floor(action["position"]["x"]),
                        math.floor(action["position"]["y"]),
                    ))
    return tiles


def _connect_oil_cell_power(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    plans: list[dict], emit: Callable[[str], None], *,
    reserved_tiles: set[tuple[int, int]] | None = None,
) -> None:
    """Bring every local oil scaffold onto the generated grid immediately."""
    substations = sorted({
        position for plan in plans for position in _positions(plan, "substation")
    })
    for position in substations:
        extend_power(
            client, bridge, surface, force, position, emit,
            reserved_tiles=reserved_tiles,
        )


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
    substations = _positions(plan, "substation")
    if substations:
        return substations[0]
    pumps = _positions(plan, "offshore-pump")
    if pumps:
        return pumps[0]
    raise StuckError("chemical stage has no power or source service anchor")


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


def _drill_footprint_tiles(centre: Point) -> set[tuple[int, int]]:
    """Tile indices checked by the drill's positive 'do I have ore?' probe."""
    x, y = centre
    return {
        (tile_x, tile_y)
        for tile_x in range(math.floor(x - 1.5), math.ceil(x + 1.5) + 1)
        for tile_y in range(math.floor(y - 1.5), math.ceil(y + 1.5) + 1)
    }


def _coal_compatible_mining_origins(
    client: RconClient, surface: str, patch_min: Point, patch_max: Point,
    preferred: Point,
) -> list[Point]:
    """One bulk survey removes origins whose two drills cannot reach coal."""
    candidates = candidate_mining_origins(preferred, patch_min, patch_max, 2)
    coal_tiles = resource_patches.resource_tiles(
        client, surface,
        (patch_min[0] - 3, patch_min[1] - 3),
        (patch_max[0] + 4, patch_max[1] + 4),
    )
    return [
        origin for origin in candidates
        if all(
            _drill_footprint_tiles(centre) & coal_tiles
            for centre in (
                (origin[0] + 1.5, origin[1] - 1.5),
                (origin[0] + 1.5, origin[1] + 2.5),
            )
        )
    ]


def _mine_overlaps_patch(
    mine: extraction_state.ResourceMine,
    patch: resource_patches.ResourcePatch,
) -> bool:
    """Whether the mine's first drill row belongs to this resource patch."""
    probe_x = mine.first_column_x if mine.first_column_x is not None else mine.output[0]
    probe_y = mine.shared_belt_y
    margin = 3
    return (
        patch.minimum[0] - margin <= probe_x <= patch.maximum[0] + margin
        and patch.minimum[1] - margin <= probe_y <= patch.maximum[1] + margin
    )


def _coal_belt_source(
    client: RconClient, surface: str,
    mine: extraction_state.ResourceMine,
) -> Point:
    """Recover the downstream end of either legacy west- or new east-flow rows."""
    if not hasattr(client, "command"):
        return mine.output
    if (
        mine.haul_head is not None
        and live_base.transport_belt_direction_at(
            client, surface, mine.haul_head,
        ) == "east"
    ):
        return mine.haul_head
    if live_base.transport_belt_direction_at(client, surface, mine.output) == "west":
        return mine.output
    # Chest-terminal legacy mines and incomplete telemetry retain their
    # established output contract rather than guessing another endpoint.
    return mine.output


def ensure_coal_mine(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    reference: Point, service_stage: ServiceStage, emit: Callable[[str], None],
    *, prefer_nearest_patch: bool = False,
) -> Point | None:
    try:
        retire_depleted_mines(client, bridge, surface, force, "coal", reference, emit)
    except RuntimeError as error:
        raise StuckError(str(error)) from error
    existing = extraction_state.find_resource_mine(client, surface, force, "coal", reference)
    found = None
    if existing is not None:
        if existing.pending:
            raise StuckError("existing coal mine is incomplete; refusing to duplicate it")
        source = _coal_belt_source(client, surface, existing)
        if prefer_nearest_patch:
            found = resource_patches.nearest_viable_patch(
                client, surface, "coal", reference,
            )
        existing_distance = math.dist(source, reference)
        patch_distance = (
            math.dist(found.nearest, reference) if found is not None else math.inf
        )
        same_patch = found is not None and _mine_overlaps_patch(existing, found)
        if found is None or same_patch or existing_distance <= patch_distance:
            if same_patch:
                emit(
                    f"  COAL DISTRICT: reusing owned belt mine at {source}; "
                    "the nearest viable coal belongs to its existing patch"
                )
            return source
        emit(
            f"  COAL DISTRICT: local patch at {found.nearest} is "
            f"{patch_distance:.0f} tiles from chemical processing versus "
            f"{existing_distance:.0f} for the existing mine at {source}"
        )
    if found is None:
        found = resource_patches.nearest_viable_patch(client, surface, "coal", reference)
    if found is None:
        raise StuckError("No coal patch found within the local 400-tile search")
    _nearest, patch_min, patch_max = found.nearest, found.minimum, found.maximum
    ore_compatible = _coal_compatible_mining_origins(
        client, surface, patch_min, patch_max, reference,
    )
    chosen = choose_mining_origin(
        reference, patch_min, patch_max, 2,
        lambda lo, hi: live_base.area_clear(client, surface, lo, hi),
        lambda drills: live_base.drill_footprints_have_resource(
            client, surface, "coal", drills,
        ),
        allowed_origins=set(ore_compatible),
    )
    if chosen is None:
        raise StuckError(
            "No clear two-drill coal extraction site found: "
            f"amount={found.amount}, patch=({patch_min[0]},{patch_min[1]}).."
            f"({patch_max[0]},{patch_max[1]}), "
            f"ore_compatible_candidates={len(ore_compatible)}"
        )
    origin, count = chosen
    output_side = "east" if reference[0] >= origin[0] else "west"
    plan, output = direct_mine_plan(
        origin, count, belt_type="transport-belt", inserter_type="fast-inserter",
        output_side=output_side, continuation_tiles=0,
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
) -> dict[str, Point]:
    result: dict[str, Point] = {}
    incomplete: list[str] = []
    for recipe in ("plastic-bar", "sulfur"):
        line = live_base.find_line(client, surface, force, recipe, "chemical-plant")
        if line is not None:
            chest = live_base.nearest_container(
                client, surface, force, line.machine_positions[-1],
                names=("passive-provider-chest",),
            )
            healthy = line.working_count > 0 or getattr(line, "produced_count", 0) > 0
            if chest:
                if healthy:
                    result[recipe] = chest
                else:
                    incomplete.append(recipe)
            else:
                incomplete.append(recipe)
    if incomplete:
        # Repair-before-duplicate: a half-built cell is construction in
        # flight, and killing the run (the old StuckError) guaranteed it
        # never finished. A healthy plastic-only district is NOT incomplete;
        # sulfur is deliberately added after advanced circuits.
        from orchestrator.autonomous_builder import ProductionPrerequisiteDeferred

        raise ProductionPrerequisiteDeferred(
            "oil district has incomplete stage(s) "
            + ", ".join(sorted(incomplete))
            + "; waiting for construction/service before adding another"
        )
    return result


def _infer_fluid_row_origin(recipe: str, machine_position: Point) -> tuple[int, int]:
    """Recover the integer origin used by `generate_fluid_machine_row`."""
    width = 5 if recipe in {"basic-oil-processing", "advanced-oil-processing"} else 3
    return (
        round(machine_position[0] - width / 2),
        round(machine_position[1] - 2 - width / 2),
    )


def _extend_sulfur_stage(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    service_stage: ServiceStage, emit: Callable[[str], None],
    existing: dict[str, Point],
) -> dict[str, Point] | None:
    """Attach sulfur to the already-producing plastic oil district."""
    refinery_recipe = "basic-oil-processing"
    refinery_line = live_base.find_line(
        client, surface, force, refinery_recipe, "oil-refinery",
    )
    if refinery_line is None:
        refinery_recipe = "advanced-oil-processing"
        refinery_line = live_base.find_line(
            client, surface, force, refinery_recipe, "oil-refinery",
        )
    if refinery_line is None or not refinery_line.machine_positions:
        raise StuckError(
            "plastic is producing but its planner-owned oil refinery cannot be recovered"
        )
    ox, oy = _infer_fluid_row_origin(
        refinery_recipe, refinery_line.machine_positions[0],
    )
    water = live_base.nearest_entity_site(
        client, surface, force, "offshore-pump", (ox + 21.0, oy + 17.0),
    )
    if water is None:
        raise StuckError(
            "plastic district has no recoverable offshore pump for the sulfur stage"
        )
    water["resource"] = "water"
    water["output"] = verified_offshore_pump_output_tile(water)
    if water["output"] is None:
        raise StuckError("existing offshore pump has an unsupported direction")

    sulfur_x, sulfur_y = ox + 18, oy + 16
    sulfur = strip_local_power(
        generate_fluid_machine_row("sulfur", 2, sulfur_x, sulfur_y),
        remove_substations=False,
    )
    _publish_output_chest(sulfur)
    petroleum_from = header_attachment(
        refinery_recipe, "petroleum-gas", 1, ox, oy,
    )["attach"]
    sulfur_gas = header_attachment(
        "sulfur", "petroleum-gas", 2, sulfur_x, sulfur_y,
    )["attach"]
    sulfur_water = header_attachment(
        "sulfur", "water", 2, sulfur_x, sulfur_y,
    )["attach"]
    endpoints = [petroleum_from, sulfur_gas, water["output"], sulfur_water]
    margin = 24
    hard = live_base.occupied_tiles(
        client, surface,
        (min(x for x, _ in endpoints) - margin, min(y for _, y in endpoints) - margin),
        (max(x for x, _ in endpoints) + margin, max(y for _, y in endpoints) + margin),
        include_water=False,
    )
    hard |= _planned_hard_tiles(sulfur)
    terrain_water = live_base.water_tiles(
        client, surface,
        (min(x for x, _ in endpoints) - margin, min(y for _, y in endpoints) - margin),
        (max(x for x, _ in endpoints) + margin, max(y for _, y in endpoints) + margin),
    )
    endpoint_tiles = {(math.floor(x), math.floor(y)) for x, y in endpoints}
    terrain_water -= hard | endpoint_tiles
    hard -= endpoint_tiles
    foreign = [
        {"fluid": "water", "separated_by_pump": False, "tiles": [water["output"]]},
        *fluid_network_segments(refinery_recipe, 1, ox, oy),
        *fluid_network_segments("sulfur", 2, sulfur_x, sulfur_y),
    ]
    links: list[tuple[str, dict]] = []
    for name, source, target, fluid, existing_tiles in (
        (
            "chemical_sulfur_petroleum_pipeline", petroleum_from,
            [sulfur_gas], "petroleum-gas", [],
        ),
        (
            "chemical_sulfur_water_pipeline", water["output"],
            [sulfur_water], "water", [water["output"]],
        ),
    ):
        try:
            link, segments, _crossed_water = _route_oil_fluid_link(
                source, target, fluid, foreign=foreign, hard=hard,
                terrain_water=terrain_water, existing_tiles=existing_tiles,
            )
        except ValueError as error:
            raise StuckError(
                f"{fluid} cannot be routed to the deferred sulfur stage: {error}"
            ) from error
        links.append((name, link))
        foreign.extend(segments)
        dives = _link_dive_tiles(segments, hard)
        if dives:
            emit(
                f"  FLUID ROUTE: {fluid} dives under "
                f"{len(dives)} blocked tile(s) with pipe-to-ground "
                "instead of detouring"
            )

    power = _filter_plan_actions(
        sulfur, lambda action: action.get("entity") in _POWER_ENTITIES,
    )
    machines = _filter_plan_actions(
        sulfur, lambda action: action.get("entity") not in _POWER_ENTITIES,
    )
    packets = [
        ("chemical_sulfur_power", power),
        ("chemical_sulfur_machines", machines),
        *links,
    ]
    _submit_oil_cell_packets(
        client, bridge, surface, force, packets, emit,
        after_packet=lambda name: (
            _connect_oil_cell_power(
                client, bridge, surface, force, [sulfur], emit,
                reserved_tiles=_link_corridor_tiles(links),
            )
            if name == "chemical_sulfur_power" else None
        ),
    )
    positions = _positions(sulfur, "chemical-plant")
    service_stage(
        client, bridge, surface, force, "sulfur stage", positions[0],
        _area(sulfur), _substation(sulfur), positions, emit,
        logistic_chest_positions=_logistic_chest_positions(sulfur),
    )
    stuck = _diagnose_machines(
        client, surface, positions, emit, grace_seconds=180.0,
        bridge=bridge, force=force,
    )
    if stuck:
        raise StuckError(f"sulfur stage built but not healthy: {stuck}")
    return {**existing, "sulfur": _provider(sulfur)}


def _link_dive_tiles(
    segments: list[dict], hard: set[tuple[int, int]],
) -> set[tuple[int, int]]:
    """Blocked tiles a routed link's tunnels pass under (not water)."""
    buried: set[tuple[int, int]] = set()
    for segment in segments:
        for (ax, ay), (bx, by) in segment.get("tunnel_endpoints", ()):
            if ax == bx:
                step = 1 if by > ay else -1
                buried.update((ax, y) for y in range(ay + step, by, step))
            elif ay == by:
                step = 1 if bx > ax else -1
                buried.update((x, ay) for x in range(ax + step, bx, step))
    return buried & set(hard)


def _route_oil_fluid_link(
    source: Point, targets: list[Point], fluid: str, *,
    foreign: list[dict], hard: set[tuple[int, int]],
    terrain_water: set[tuple[int, int]], existing_tiles: list[Point],
) -> tuple[dict, list[dict], bool]:
    """Prefer a land route; dive under blockers before crossing water.

    Pass 2 bridges blocked hard tiles (poles, machines, planned footprints)
    with landfill-free pipe-to-ground spans -- a single-tile blocker costs
    one pair instead of a detour or a dead run (2026-09-05: the crude
    pipeline ended two runs on a mid-pass power pole). Water crossings stay
    last: landfill is more expensive than a dive.
    """
    last_error: ValueError | None = None
    for allow_dives, allow_water_crossing in (
        (False, False), (True, False), (True, True),
    ):
        try:
            link = generate_shortest_fluid_chain_link(
                source, targets, fluid, foreign=foreign, hard_tiles=hard,
                tunnelable_tiles=terrain_water, clearance=0, search_margin=48,
                existing_tiles=existing_tiles, mixing_margin=True,
                allow_terrain_tunnels=allow_water_crossing,
                allow_dives=allow_dives,
            )
            segments = shortest_fluid_chain_segments(
                source, targets, fluid, foreign=foreign, hard_tiles=hard,
                tunnelable_tiles=terrain_water, clearance=0, search_margin=48,
                mixing_margin=True,
                allow_terrain_tunnels=allow_water_crossing,
                allow_dives=allow_dives,
            )
            return link, segments, allow_water_crossing
        except ValueError as error:
            last_error = error
    raise last_error or ValueError("No bounded fluid route found")


def ensure_oil_cell(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    reference: Point, service_stage: ServiceStage,
    emit: Callable[[str], None], *, target_output: str = "sulfur",
) -> dict[str, Point] | None:
    if target_output not in {"plastic-bar", "sulfur"}:
        raise ValueError(f"unsupported oil-cell output {target_output!r}")
    existing = _existing_outputs(client, surface, force) or {}
    if target_output in existing:
        return existing
    if target_output == "sulfur" and "plastic-bar" in existing:
        emit(
            "  CHEMICAL LADDER: plastic is healthy; attaching the deferred "
            "sulfur and water branch"
        )
        return _extend_sulfur_stage(
            client, bridge, surface, force, service_stage, emit, existing,
        )
    include_sulfur = target_output == "sulfur"
    oil = live_base.nearest_resource(client, surface, "crude-oil", reference)
    if oil is None:
        raise StuckError("No crude-oil patch found within the local 400-tile search")
    oil_pos = oil[0]
    cell = _find_oil_cell_site(client, surface, oil_pos)
    if cell is None:
        raise StuckError("No ore-free 42x34 area found near the crude-oil source")
    ox, oy = round(cell[0]), round(cell[1])
    cell_centre = (ox + 21.0, oy + 17.0)
    coal = ensure_coal_mine(
        client, bridge, surface, force, cell_centre, service_stage, emit,
        prefer_nearest_patch=True,
    )
    if coal is None:
        return None
    plastic_site = _find_plastic_site(client, surface, cell_centre, coal)
    if plastic_site is None:
        raise StuckError(
            "No ore-free 12x12 plastic site found between the local coal mine "
            "and oil refinery"
        )
    px, py = round(plastic_site[0]), round(plastic_site[1])
    water = chemical_survey.nearest_offshore_pump_site(
        client, surface, cell_centre,
    )
    if water is None:
        raise StuckError("No buildable straight shoreline found near the oil cell")
    oil_site = _pumpjack_site_nearest(oil_pos, cell_centre)
    emit(
        f"  OIL DISTRICT: chemical processing at {(ox, oy)} near crude source "
        f"{oil_pos}; pumpjack faces {oil_site['direction']} toward its local pipe"
    )
    emit(
        f"  OIL DISTRICT: plastic at {(px, py)} between refinery "
        f"{cell_centre} and local coal belt {coal}"
    )
    refinery_recipe = oil_processing_recipe(0)
    refinery = generate_fluid_machine_row(refinery_recipe, 1, ox, oy)
    plastic = generate_fluid_machine_row("plastic-bar", 2, px, py)
    sulfur = generate_fluid_machine_row("sulfur", 2, ox + 18, oy + 16)
    crude_source = generate_pumpjack_source([oil_site], [oil_site["output"]])
    water_source = generate_offshore_pump_source([water], [water["output"]])
    plans = [
        strip_local_power(plan, remove_substations=False)
        for plan in (crude_source, water_source, refinery, plastic, sulfur)
    ]
    crude_source, water_source, refinery, plastic, sulfur = plans
    plastic_feed = _direct_single_belt_feed(plastic, "coal", "east")
    _publish_output_chest(plastic)
    _publish_output_chest(sulfur)

    crude_to = header_attachment(refinery_recipe, "crude-oil", 1, ox, oy)["attach"]
    petroleum_from = header_attachment(refinery_recipe, "petroleum-gas", 1, ox, oy)["attach"]
    plastic_gas = header_attachment("plastic-bar", "petroleum-gas", 2, px, py)["attach"]
    sulfur_gas = header_attachment("sulfur", "petroleum-gas", 2, ox + 18, oy + 16)["attach"]
    sulfur_water = header_attachment("sulfur", "water", 2, ox + 18, oy + 16)["attach"]
    endpoints = [oil_site["output"], water["output"], crude_to, petroleum_from,
                 plastic_gas]
    if include_sulfur:
        endpoints.extend((sulfur_gas, sulfur_water))
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
        + fluid_network_segments(refinery_recipe, 1, ox, oy)
        + fluid_network_segments("plastic-bar", 2, px, py)
        + (
            fluid_network_segments("sulfur", 2, ox + 18, oy + 16)
            if include_sulfur else []
        )
    )
    links: list[tuple[str, dict]] = []
    requested_links = [
        (
            "chemical_crude_pipeline", oil_site["output"], [crude_to],
            "crude-oil", [oil_site["output"]],
        ),
        (
            "chemical_plastic_petroleum_pipeline", petroleum_from,
            [plastic_gas], "petroleum-gas", [],
        ),
    ]
    if include_sulfur:
        requested_links.extend((
            (
            "chemical_sulfur_petroleum_pipeline", petroleum_from,
            [sulfur_gas], "petroleum-gas", [],
            ),
            (
            "chemical_sulfur_water_pipeline", water["output"],
            [sulfur_water], "water", [water["output"]],
            ),
        ))
    for link_name, source, targets, fluid, existing_tiles in requested_links:
        try:
            link, segments, crossed_water = _route_oil_fluid_link(
                source, targets, fluid, foreign=foreign, hard=hard,
                terrain_water=terrain_water, existing_tiles=existing_tiles,
            )
            links.append((link_name, link))
            foreign.extend(segments)
            dives = _link_dive_tiles(segments, hard)
            if dives:
                emit(
                    f"  FLUID ROUTE: {fluid} dives under "
                    f"{len(dives)} blocked tile(s) with pipe-to-ground "
                    "instead of detouring"
                )
            if crossed_water:
                emit(
                    f"  FLUID ROUTE: {fluid} has no bounded land detour; "
                    "using a water crossing with landfill endpoints"
                )
        except ValueError as error:
            raise StuckError(
                f"{fluid} cannot be routed from {source} to {targets}: {error}"
            ) from error
    route = preflight_ingredient_transport(
        client, surface, force, "plastic-bar", "coal", coal, plastic_feed, 2,
        max_belt_route_tiles=_CHEMICAL_BELT_ROUTE_LIMIT,
        additional_blocked=planned_footprint_tiles(
            _merge(*plans, *(link for _name, link in links))
        ),
        mode="belt", destination_is_belt=True,
        destination_belt_direction="east",
    )
    if route is None:
        raise StuckError("plastic coal route unexpectedly selected logistics")
    coal_actions, coal_belt_type = route
    coal_plan = {"phases": [{
        "name": "bridge_coal_to_plastic-bar", "actions": coal_actions,
    }]}
    power_plan = _merge(*(
        _filter_plan_actions(
            plan, lambda action: action.get("entity") in _POWER_ENTITIES,
        )
        for plan in plans
    ))
    unpowered = [
        _filter_plan_actions(
            plan, lambda action: action.get("entity") not in _POWER_ENTITIES,
        )
        for plan in plans
    ]
    crude_source_stage, water_source_stage, refinery_stage, plastic_stage, sulfur_stage = (
        unpowered
    )
    link_packets = dict(links)
    packets = [
        ("chemical_coal_belt", coal_plan),
        ("chemical_power_backbone", power_plan),
        (
            "chemical_refinery_and_plastic_machines",
            _merge(water_source_stage, crude_source_stage, refinery_stage, plastic_stage),
        ),
        ("chemical_crude_pipeline", link_packets["chemical_crude_pipeline"]),
        (
            "chemical_plastic_petroleum_pipeline",
            link_packets["chemical_plastic_petroleum_pipeline"],
        ),
    ]
    if include_sulfur:
        packets.extend((
            (
            "chemical_sulfur_machines",
            sulfur_stage,
            ),
            (
            "chemical_sulfur_petroleum_pipeline",
            link_packets["chemical_sulfur_petroleum_pipeline"],
            ),
            (
            "chemical_sulfur_water_pipeline",
            link_packets["chemical_sulfur_water_pipeline"],
            ),
        ))
    _submit_oil_cell_packets(
        client, bridge, surface, force, packets, emit,
        after_packet=lambda name: (
            _connect_oil_cell_power(
                client, bridge, surface, force, plans, emit,
                reserved_tiles=_link_corridor_tiles(links),
            )
            if name == "chemical_power_backbone" else None
        ),
    )

    service_plans = [
        ("crude-oil source", crude_source, "pumpjack"),
        ("water source", water_source, "offshore-pump"),
        ("oil refinery", refinery, "oil-refinery"),
        ("plastic-bar stage", plastic, "chemical-plant"),
    ]
    if include_sulfur:
        service_plans.append(("sulfur stage", sulfur, "chemical-plant"))
    for name, plan, machine in service_plans:
        machines = _positions(plan, machine)
        service_stage(
            client, bridge, surface, force, name, machines[0], _area(plan),
            _substation(plan), machines, emit,
            logistic_chest_positions=_logistic_chest_positions(plan),
        )
    coal_belt_tiles = sum(
        1 for action in coal_actions
        if "transport-belt" in action.get("entity", "")
    )
    coal_grace = transport_grace_seconds(coal_belt_type, coal_belt_tiles)
    emit(
        f"  plastic-bar: coal arrives by local {coal_belt_type} "
        f"({coal_belt_tiles} belt tiles from {coal})"
    )
    production_positions = _positions(plastic, "chemical-plant")
    if include_sulfur:
        production_positions += _positions(sulfur, "chemical-plant")
    stuck = _diagnose_machines(
        client, surface, production_positions,
        emit, grace_seconds=coal_grace, bridge=bridge, force=force,
    )
    if stuck:
        raise StuckError(f"oil cell built but not healthy: {stuck}")
    outputs = {"plastic-bar": _provider(plastic)}
    if include_sulfur:
        outputs["sulfur"] = _provider(sulfur)
    return outputs


def ensure_sulfuric_acid_cell(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    reference: Point, service_stage: ServiceStage,
    emit: Callable[[str], None],
) -> Point | None:
    """Build sulfuric acid as its own final chemical-bootstrap rung."""
    existing = live_base.find_line(
        client, surface, force, "sulfuric-acid", "chemical-plant",
    )
    if existing is not None and (
        existing.working_count > 0 or existing.produced_count > 0
    ):
        return existing.machine_positions[0]
    if existing is not None:
        from orchestrator.autonomous_builder import ProductionPrerequisiteDeferred

        raise ProductionPrerequisiteDeferred(
            "sulfuric-acid stage exists but has not produced; service its "
            "water, sulfur, iron, or power before opening another"
        )

    oil_outputs = ensure_oil_cell(
        client, bridge, surface, force, reference, service_stage, emit,
        target_output="sulfur",
    )
    if oil_outputs is None:
        return None
    sulfur_provider = oil_outputs["sulfur"]
    site = live_base.find_clear_area(
        client, surface, sulfur_provider, 18, 18, max_radius=80.0,
        avoid_resources=True, resource_clearance=5,
    )
    if site is None:
        raise StuckError("No ore-free 18x18 area found for sulfuric acid")
    ox, oy = round(site[0]), round(site[1])
    water = chemical_survey.nearest_offshore_pump_site(
        client, surface, (ox + 9.0, oy + 9.0),
    )
    if water is None:
        raise StuckError("No buildable straight shoreline found for acid water")

    acid = generate_fluid_machine_row("sulfuric-acid", 1, ox, oy)
    water_source = generate_offshore_pump_source([water], [water["output"]])
    _swap_infinity_chests(acid, {})
    plans = [
        strip_local_power(plan, remove_substations=False)
        for plan in (water_source, acid)
    ]
    water_source, acid = plans
    water_to = header_attachment(
        "sulfuric-acid", "water", 1, ox, oy,
    )["attach"]
    endpoints = [water["output"], water_to]
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
    endpoint_tiles = {(math.floor(x), math.floor(y)) for x, y in endpoints}
    terrain_water -= hard | endpoint_tiles
    hard -= endpoint_tiles
    foreign = [
        {"fluid": "water", "separated_by_pump": False, "tiles": [water["output"]]},
        *fluid_network_segments("sulfuric-acid", 1, ox, oy),
    ]
    try:
        water_link, _segments, _crossed = _route_oil_fluid_link(
            water["output"], [water_to], "water", foreign=foreign, hard=hard,
            terrain_water=terrain_water, existing_tiles=[water["output"]],
        )
    except ValueError as error:
        raise StuckError(f"water cannot be routed to sulfuric acid: {error}") from error

    power = _merge(*(
        _filter_plan_actions(
            plan, lambda action: action.get("entity") in _POWER_ENTITIES,
        )
        for plan in plans
    ))
    water_stage, acid_stage = [
        _filter_plan_actions(
            plan, lambda action: action.get("entity") not in _POWER_ENTITIES,
        )
        for plan in plans
    ]
    packets = [
        ("acid_power_backbone", power),
        ("acid_water_source_and_machine", _merge(water_stage, acid_stage)),
        ("acid_water_pipeline", water_link),
    ]
    _submit_oil_cell_packets(
        client, bridge, surface, force, packets, emit,
        after_packet=lambda name: (
            _connect_oil_cell_power(client, bridge, surface, force, plans, emit)
            if name == "acid_power_backbone" else None
        ),
    )
    for name, plan, machine in (
        ("acid water source", water_source, "offshore-pump"),
        ("sulfuric-acid stage", acid, "chemical-plant"),
    ):
        positions = _positions(plan, machine)
        service_stage(
            client, bridge, surface, force, name, positions[0], _area(plan),
            _substation(plan), positions, emit,
            logistic_chest_positions=_logistic_chest_positions(plan),
        )
    return _positions(acid, "chemical-plant")[0]


def _attach_battery_to_acid(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    acid_position: Point, service_stage: ServiceStage,
    emit: Callable[[str], None],
) -> Point | None:
    """Add the battery consumer to a separately validated acid producer."""
    acid_x, acid_y = _infer_fluid_row_origin("sulfuric-acid", acid_position)
    battery_x, battery_y = acid_x + 14, acid_y + 12
    acid = strip_local_power(
        generate_fluid_machine_row("sulfuric-acid", 1, acid_x, acid_y),
        remove_substations=False,
    )
    battery = strip_local_power(
        generate_fluid_machine_row("battery", 1, battery_x, battery_y),
        remove_substations=False,
    )
    _swap_infinity_chests(battery, {})
    _publish_output_chest(battery)
    acid_from = header_attachment(
        "sulfuric-acid", "sulfuric-acid", 1, acid_x, acid_y,
    )["attach"]
    acid_to = header_attachment(
        "battery", "sulfuric-acid", 1, battery_x, battery_y,
    )["attach"]
    endpoints = [acid_from, acid_to]
    margin = 18
    hard = live_base.occupied_tiles(
        client, surface,
        (min(x for x, _ in endpoints) - margin, min(y for _, y in endpoints) - margin),
        (max(x for x, _ in endpoints) + margin, max(y for _, y in endpoints) + margin),
        include_water=False,
    )
    hard |= _planned_hard_tiles(battery)
    endpoint_tiles = {(math.floor(x), math.floor(y)) for x, y in endpoints}
    hard -= endpoint_tiles
    foreign = [
        *fluid_network_segments("sulfuric-acid", 1, acid_x, acid_y),
        *fluid_network_segments("battery", 1, battery_x, battery_y),
    ]
    try:
        link, _segments, _crossed = _route_oil_fluid_link(
            acid_from, [acid_to], "sulfuric-acid", foreign=foreign, hard=hard,
            terrain_water=set(), existing_tiles=[],
        )
    except ValueError as error:
        raise StuckError(f"acid cannot be routed to battery: {error}") from error
    power = _filter_plan_actions(
        battery, lambda action: action.get("entity") in _POWER_ENTITIES,
    )
    machine = _filter_plan_actions(
        battery, lambda action: action.get("entity") not in _POWER_ENTITIES,
    )
    _submit_oil_cell_packets(
        client, bridge, surface, force,
        [
            ("battery_power", power),
            ("battery_machine", machine),
            ("battery_acid_pipeline", link),
        ],
        emit,
        after_packet=lambda name: (
            _connect_oil_cell_power(client, bridge, surface, force, [battery], emit)
            if name == "battery_power" else None
        ),
    )
    positions = _positions(battery, "chemical-plant")
    service_stage(
        client, bridge, surface, force, "battery stage", positions[0],
        _area(battery), _substation(battery), positions, emit,
        logistic_chest_positions=_logistic_chest_positions(battery),
    )
    return _provider(battery)


def ensure_battery_cell(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    reference: Point, service_stage: ServiceStage,
    emit: Callable[[str], None],
) -> Point | None:
    """Build the explicit water -> acid -> battery chain.

    Fluid-bearing item recipes must never fall through the generic mall-line
    planner. It has no pipe actions, which is why accumulator production ended
    with "battery has no recipe" in the 2026-08-26 20:57 run even though the
    live recipe catalog contained the recipe.
    """
    existing = live_base.find_line(
        client, surface, force, "battery", "chemical-plant",
    )
    if existing is not None:
        positions = list(existing.machine_positions)
        provider = live_base.nearest_container(
            client, surface, force,
            positions[-1],
            names=("passive-provider-chest",),
        )
        if provider is not None and existing.working_count > 0:
            return provider
        xs = [position[0] for position in positions]
        ys = [position[1] for position in positions]
        service_stage(
            client, bridge, surface, force, "battery chemical cell",
            positions[0],
            ((min(xs) - 15, min(ys) - 15), (max(xs) + 15, max(ys) + 15)),
            positions[0], positions, emit,
            logistic_chest_positions=[provider] if provider is not None else [],
        )
        refreshed = live_base.find_line(
            client, surface, force, "battery", "chemical-plant",
        )
        if provider is not None and refreshed is not None and refreshed.working_count > 0:
            return provider
        from orchestrator.autonomous_builder import ProductionPrerequisiteDeferred

        raise ProductionPrerequisiteDeferred(
            "battery chemical cell exists but is not healthy; service its "
            "water, acid, item supply, or power before opening another"
        )

    acid_position = ensure_sulfuric_acid_cell(
        client, bridge, surface, force, reference, service_stage, emit,
    )
    if acid_position is None:
        return None
    return _attach_battery_to_acid(
        client, bridge, surface, force, acid_position, service_stage, emit,
    )
