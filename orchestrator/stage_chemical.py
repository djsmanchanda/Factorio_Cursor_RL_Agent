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
    _wait_for_ghosts, construction_supply_chain_is_scheduled,
    ensure_logistic_coverage, extend_power,
    extend_roboport_coverage, service_distance,
)
from orchestrator.stage_transport import (
    _direct_single_belt_feed, _publish_output_chest, _swap_infinity_chests,
    transport_grace_seconds,
)
from planners.fluid_layouts import (
    fluid_network_segments, generate_fluid_machine_row, header_attachment,
)
from planners.fluid_layout_search import (
    oriented_compact_paired_fluid_block,
    oriented_fluid_network_segments, oriented_fluid_row_candidates,
    oriented_header_attachment, recover_oriented_fluid_row,
    transform_direction,
)
from planners.fluid_routing import (
    generate_shortest_fluid_chain_link, shortest_fluid_chain_segments,
)
from planners.infrastructure import strip_local_power
from planners.infrastructure_geometry import footprint_tile_indices
from planners.plan_validation import ENTITY_FOOTPRINTS, occupied_tile_indices
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
    merged = {"phases": [phase for plan in plans for phase in plan["phases"]]}
    reserved = {
        tuple(tile) for plan in plans for tile in plan.get("reserved_tiles", ())
    }
    if reserved:
        merged["reserved_tiles"] = [list(tile) for tile in sorted(reserved)]
    return merged


#: Crude/s one basic-or-advanced refinery draws (100 crude per 5s either way).
#: Pumpjacks are sized to saturate the built refinery; a second refinery
#: without the wells to feed it is not capacity (and vice versa).
REFINERY_CRUDE_DRAW_PER_SECOND = 20.0
#: Opening refinery row: four basic refineries make ~36 petroleum/s, matching
#: the two plastic plants' 40/s draw (user standard 2026-09-04). Advanced
#: processing arrives through the existing ladder, not by overbuilding basic.
OPENING_REFINERY_COUNT = 4
#: Crude/s the opening district must be able to deliver (four refineries).
OPENING_CRUDE_TARGET_PER_SECOND = (
    OPENING_REFINERY_COUNT * REFINERY_CRUDE_DRAW_PER_SECOND
)
#: Patches that refused a crude expansion (unroutable, unplaceable). World
#: coordinates, so fresh episodes start clean and entries stay bounded.
_CRUDE_EXPANSION_FAILED_CELLS: set[tuple[int, int]] = set()
#: Radius around the plastic chest inside which pumpjacks count as district
#: supply. Remote patches join through their own pipelines.
DISTRICT_JACK_RADIUS = 120.0
#: Conservative crude/s per pumpjack for build-time sizing (live-measured 9/s
#: at +30% productivity on a middling patch; minimum yield floors near 2/s).
#: Post-build starvation still expands later; this only sizes the opening set.
ESTIMATED_CRUDE_PER_PUMPJACK = 8.0
#: Hard ceiling on opening pumpjacks: patch-bounded, bill-bounded, and enough
#: for one refinery at the estimate above. More wells join on measured
#: starvation, never on speculation.
MAX_OPENING_PUMPJACKS = 4
#: Centre spacing between pumpjack 3x3 footprints: no overlap, with working
#: room for each connector pipe. The fluid router and plan preflight still
#: validate; spacing only proposes.
PUMPJACK_SPOT_PITCH = 4.0


def _crude_patch_tiles(
    client: RconClient, surface: str, center: Point, radius: float = 40.0,
) -> list[tuple[float, float]]:
    """Crude-oil tile centres near `center`, capping the survey."""
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local cx,cy=" + str(center[0]) + "," + str(center[1]) + ";"
        "local r=" + str(radius) + ";local out={};local n=0;"
        "for _,e in pairs(s.find_entities_filtered{name='crude-oil',"
        "area={{cx-r,cy-r},{cx+r,cy+r}}}) do "
        "n=n+1;if n>400 then break end;"
        "out[#out+1]=e.position.x..','..e.position.y end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = client.command("/sc " + lua).strip()
    tiles = []
    for record in raw.split(";"):
        if not record:
            continue
        try:
            x, y = record.split(",", 1)
            tiles.append((float(x), float(y)))
        except (ValueError, TypeError):
            continue
    return tiles


def _extra_pumpjack_spots(
    tiles: list[tuple[float, float]], existing: Point | None, target: Point, *,
    draw_per_second: float = REFINERY_CRUDE_DRAW_PER_SECOND,
    est_per_jack: float = ESTIMATED_CRUDE_PER_PUMPJACK,
    max_jacks: int = MAX_OPENING_PUMPJACKS,
    blocked_tiles: set[tuple[int, int]] = frozenset(),
) -> list[dict]:
    """Extra pumpjack sites on the same patch, nearest first.

    One refinery draws 20 crude/s; a lone 9/s well leaves it (and both
    plastic plants behind it) idle most of the time (2026-09-04: plastic
    crawled on 9/s against 40/s of plant draw). Sites cover at least one
    crude tile, keep footprint pitch from each other and the existing jack,
    and rotate their connector toward the cell. Returns site dicts ready for
    generate_pumpjack_source; empty when the patch holds no more spots.
    With no existing jack (a fresh remote patch), every spot is new and
    tiles sort toward the refinery instead.
    """
    wanted = max(1, min(max_jacks, math.ceil(draw_per_second / est_per_jack)))
    chosen: list[tuple[float, float]] = (
        [(float(existing[0]), float(existing[1]))] if existing is not None else []
    )
    if existing is None:
        order: Callable[[tuple[float, float]], float] = (
            lambda t: (t[0] - target[0]) ** 2 + (t[1] - target[1]) ** 2
        )
    else:
        order = (
            lambda t: (t[0] - existing[0]) ** 2 + (t[1] - existing[1]) ** 2
        )
    spots: list[dict] = []
    for tile in sorted(tiles, key=order):
        if len(chosen) >= wanted:
            break
        candidate = (float(tile[0]), float(tile[1]))
        # A crude tile only grants the resource overlap required by a
        # pumpjack; it does not make a 3x3 pumpjack legal over an existing
        # pole, pipe, rock, or water.  This used to be checked only after the
        # whole oil packet was assembled, when one bad extra well could reject
        # its source packet and leave the other wells on disconnected stubs.
        footprint = footprint_tile_indices(candidate, ENTITY_FOOTPRINTS["pumpjack"])
        if footprint & blocked_tiles:
            continue
        if any(
            math.dist(candidate, placed) < PUMPJACK_SPOT_PITCH
            for placed in chosen
        ):
            continue
        site = _pumpjack_site_nearest(candidate, target)
        if site.get("output") is None or site["output"] in blocked_tiles:
            continue
        chosen.append(candidate)
        spots.append(site)
    return spots


def _pumpjack_sites_for_patch(
    tiles: list[tuple[float, float]], primary: Point, target: Point, *,
    blocked_tiles: set[tuple[int, int]] = frozenset(),
    draw_per_second: float = REFINERY_CRUDE_DRAW_PER_SECOND,
    est_per_jack: float = ESTIMATED_CRUDE_PER_PUMPJACK,
    max_jacks: int = MAX_OPENING_PUMPJACKS,
) -> list[dict]:
    """Choose a legal, compact crude network for one patch.

    The first pumpjack owns the long run to the refinery; each following one
    joins that trunk.  Rank the first output by its route to the refinery and
    each later output by its distance to the growing local network.  This is a
    cheap deterministic proxy for the routed pipe bill, while the fluid router
    remains the authority on a path around remote obstacles.
    """
    wanted = max(1, min(max_jacks, math.ceil(draw_per_second / est_per_jack)))
    candidates = [tuple(map(float, primary))] + [
        (float(x), float(y)) for x, y in tiles
    ]
    unique = list(dict.fromkeys(candidates))
    legal = []
    for candidate in unique:
        if footprint_tile_indices(candidate, ENTITY_FOOTPRINTS["pumpjack"]) & blocked_tiles:
            continue
        site = _pumpjack_site_nearest(candidate, target)
        if site.get("output") is not None and site["output"] not in blocked_tiles:
            legal.append(site)
    if not legal:
        return []

    def _target_cost(site: dict) -> int:
        output = site["output"]
        return abs(output[0] - target[0]) + abs(output[1] - target[1])

    # The long trunk matters more than any later branch, so settle its source
    # first.  Position/direction make ties repeatable across identical seeds.
    legal.sort(key=lambda site: (_target_cost(site), site["position"], site["direction"]))
    chosen = [legal.pop(0)]
    while legal and len(chosen) < wanted:
        chosen_tiles = set().union(*(
            footprint_tile_indices(
                tuple(site["position"]), ENTITY_FOOTPRINTS["pumpjack"],
            ) for site in chosen
        ))
        viable = [
            site for site in legal
            if footprint_tile_indices(
                tuple(site["position"]), ENTITY_FOOTPRINTS["pumpjack"],
            ).isdisjoint(chosen_tiles)
        ]
        if not viable:
            break
        # Branches tap the nearest already-owned crude tile, so this is the
        # incremental pipe cost, with the refinery distance as a stable tie.
        viable.sort(key=lambda site: (
            min(
                abs(site["output"][0] - prior["output"][0])
                + abs(site["output"][1] - prior["output"][1])
                for prior in chosen
            ),
            _target_cost(site), site["position"], site["direction"],
        ))
        selected = viable[0]
        chosen.append(selected)
        legal.remove(selected)
    return chosen


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
        client, surface, oil_position, 64, 44, max_radius=80.0,
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
    return {**plan, "phases": phases}


def _submit_oil_cell_packets(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    packets: list[tuple[str, dict]], emit: Callable[[str], None], *,
    after_packet: Callable[[str], None] | None = None,
) -> None:
    """Ghost independent oil packets as soon as each material chain is ready."""
    future = _merge(*(plan for _name, plan in packets))
    reserved = planned_footprint_tiles(future) | {
        tuple(tile) for tile in future.get("reserved_tiles", ())
    }
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


def _oil_cell_power_reserve_tiles(
    plans: list[dict], links: list[tuple[str, dict]],
) -> set[tuple[int, int]]:
    """Tiles mid-pass power chains must avoid while a district is in flight.

    Power bridging runs right after the backbone packet while machine rows
    and pipelines submit last. Link corridors and growth reservations were
    already reserved, but the machine rows' own future footprint -- pipes
    included -- was not: a chain hop landed on a refinery-row pipe tile
    (live, 2026-09-06: the oil district died on a power-bridge pole at
    -310.5,-37.5) and the later packet failed terminal. Reserve the complete
    machine footprint so chains route around the district they connect.
    """
    return (
        _link_corridor_tiles(links)
        | planned_footprint_tiles(_merge(*plans))
        | {
            tuple(tile) for plan in plans
            for tile in plan.get("reserved_tiles", ())
        }
    )


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
    _submit(
        client, bridge, surface, plan, "mining_coal", emit,
        stage_coverage=lambda: _ensure_plan_construction_coverage(
            client, bridge, surface, force, plan, emit,
        ),
    )
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


def _recover_fluid_row_layout(
    client: RconClient, surface: str, recipe: str,
    machine_positions: list[Point] | tuple[Point, ...],
) -> tuple[Point, str, str | None]:
    """Recover rotation/mirror from live machines and their verified pipes."""
    if not machine_positions:
        raise StuckError(f"Cannot recover empty {recipe} fluid row")
    margin = 24
    lo = (
        min(x for x, _ in machine_positions) - margin,
        min(y for _, y in machine_positions) - margin,
    )
    hi = (
        max(x for x, _ in machine_positions) + margin,
        max(y for _, y in machine_positions) + margin,
    )
    try:
        pipes = live_base.entity_tile_indices(
            client, surface, ("pipe", "pipe-to-ground"), lo, hi,
        )
        return recover_oriented_fluid_row(recipe, machine_positions, pipes)
    except Exception:
        # Legacy rows predate orientation search and are north/unmirrored.
        return (
            tuple(map(float, _infer_fluid_row_origin(recipe, machine_positions[0]))),
            "north", None,
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
    (ox, oy), refinery_direction, refinery_mirror = _recover_fluid_row_layout(
        client, surface, refinery_recipe, refinery_line.machine_positions,
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
    petroleum_from = oriented_header_attachment(
        refinery_recipe, "petroleum-gas",
        max(1, len(refinery_line.machine_positions)), (ox, oy),
        direction=refinery_direction, mirror=refinery_mirror,
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
        # The keepout must cover the WHOLE built row: a 1-machine phantom
        # leaves the eastern headers unreserved and later links route gas
        # straight through crude (2026-09-05: petroleum-gas merged into the
        # crude header it could not see).
        *oriented_fluid_network_segments(
            refinery_recipe, max(1, len(refinery_line.machine_positions)),
            (ox, oy), direction=refinery_direction, mirror=refinery_mirror,
        ),
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
                reserved_tiles=_oil_cell_power_reserve_tiles([sulfur], links),
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


def _nearest_crude_tile(foreign: list[dict], source: Point) -> tuple[int, int]:
    """Nearest already-routed crude tile for an extra pumpjack tap.

    Extra jacks join the trunk (or the refinery crude header) where it is
    closest instead of each running a full-length pipeline to the refinery.
    """
    crude = {
        tuple(tile) for segment in foreign
        if segment.get("fluid") == "crude-oil"
        for tile in segment.get("tiles", ())
    }
    if not crude:
        raise StuckError(
            f"extra pumpjack at {source} has no routed crude network to tap"
        )
    sx, sy = source
    return min(
        crude,
        key=lambda tile: (abs(tile[0] - sx) + abs(tile[1] - sy), tile),
    )


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


def _district_pumpjacks(
    client: RconClient, surface: str, anchor: Point,
) -> tuple[list[Point], int]:
    """Live pumpjack positions near `anchor`, plus unbuilt pumpjack ghosts.

    One survey answers both capacity (real jacks) and in-flight work
    (ghosts mean a previous expansion is still constructing: wait, don't
    duplicate it).
    """
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local ax,ay=" + str(anchor[0]) + "," + str(anchor[1]) + ";"
        "local real={};local ghosts=0;"
        "for _,e in pairs(s.find_entities_filtered{name='pumpjack'}) do "
        "local dx,dy=e.position.x-ax,e.position.y-ay;"
        "if dx*dx+dy*dy<=" + str(DISTRICT_JACK_RADIUS ** 2) + " then "
        "real[#real+1]=e.position.x..','..e.position.y end end;"
        "for _,e in pairs(s.find_entities_filtered{type='entity-ghost'}) do "
        "if e.ghost_name=='pumpjack' then "
        "local dx,dy=e.position.x-ax,e.position.y-ay;"
        "if dx*dx+dy*dy<=" + str(400.0 ** 2) + " then ghosts=ghosts+1 end end end;"
        "rcon.print(table.concat(real,';')..'|'..ghosts)"
    )
    raw = client.command("/sc " + lua).strip()
    positions: list[Point] = []
    ghost_count = 0
    head, _, tail = raw.partition("|")
    for record in head.split(";"):
        if not record:
            continue
        try:
            x, y = record.split(",", 1)
            positions.append((float(x), float(y)))
        except (ValueError, TypeError):
            continue
    try:
        ghost_count = int(tail or 0)
    except ValueError:
        ghost_count = 0
    return positions, ghost_count


def _crude_patch_grid(
    client: RconClient, surface: str, center: Point, radius: float = 400.0,
) -> dict[tuple[int, int], int]:
    """Crude tile counts per 50-tile cell around `center` (one survey)."""
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local cx,cy=" + str(center[0]) + "," + str(center[1]) + ";"
        "local r=" + str(radius) + ";local counts={};"
        "for _,e in pairs(s.find_entities_filtered{name='crude-oil',"
        "area={{cx-r,cy-r},{cx+r,cy+r}}}) do "
        "local k=math.floor(e.position.x/50)..','..math.floor(e.position.y/50);"
        "counts[k]=(counts[k] or 0)+1 end;"
        "local out={};for k,v in pairs(counts) do out[#out+1]=k..':'..v end;"
        "rcon.print(table.concat(out,'|'))"
    )
    grid: dict[tuple[int, int], int] = {}
    for record in client.command("/sc " + lua).strip().split("|"):
        if not record or ":" not in record:
            continue
        try:
            key, raw_count = record.split(":", 1)
            x, y = key.split(",", 1)
            grid[(int(x), int(y))] = int(raw_count)
        except (ValueError, TypeError):
            continue
    return grid


def _pick_remote_crude_cell(
    grid: dict[tuple[int, int], int], near: Point,
    used: set[tuple[int, int]],
) -> tuple[int, int] | None:
    """Biggest unused crude patch cell, nearest wins ties.

    Pure function over the grid survey: biggest patch first (fewer pipelines
    for the same crude), skipping cells already serving jacks and cells that
    previously refused expansion. Lone tiles qualify; the spot search bounds
    what actually builds.
    """
    options = [
        (count, cell) for cell, count in grid.items()
        if cell not in used and cell not in _CRUDE_EXPANSION_FAILED_CELLS
    ]
    if not options:
        return None
    options.sort(
        key=lambda entry: (
            -entry[0],
            (entry[1][0] * 50 - near[0]) ** 2
            + (entry[1][1] * 50 - near[1]) ** 2,
        ),
    )
    return options[0][1]


def _expand_remote_crude_if_starved(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    service_stage: ServiceStage, emit: Callable[[str], None],
    plastic_chest: Point,
) -> None:
    """Add one remote patch of pumpjacks when plastic idles for lack of crude.

    Best-effort and never fatal: surveys are gated behind idle plants, one
    patch builds at a time (ghosts in flight defer), failures are remembered
    per cell, and material shortages ride the normal queue. Each expansion
    that lands raises district capacity toward the refinery draw.
    """
    try:
        plastic_line = live_base.find_line(
            client, surface, force, "plastic-bar", "chemical-plant",
        )
        if plastic_line is None or plastic_line.working_count > 0:
            return
        jacks, ghosts = _district_pumpjacks(client, surface, plastic_chest)
        if ghosts > 0:
            emit("  OIL EXPANSION WAIT: pumpjack ghosts still constructing")
            return
        capacity = len(jacks) * ESTIMATED_CRUDE_PER_PUMPJACK
        if capacity >= OPENING_CRUDE_TARGET_PER_SECOND:
            return
        refinery_pos: Point | None = None
        refinery_positions: tuple[Point, ...] = ()
        refinery_recipe = "basic-oil-processing"
        for recipe in ("basic-oil-processing", "advanced-oil-processing"):
            line = live_base.find_line(client, surface, force, recipe, "oil-refinery")
            if line is not None and line.machine_positions:
                refinery_pos = line.machine_positions[0]
                refinery_positions = tuple(line.machine_positions)
                refinery_recipe = recipe
                break
        if refinery_pos is None:
            return
        origin, refinery_direction, refinery_mirror = _recover_fluid_row_layout(
            client, surface, refinery_recipe, refinery_positions,
        )
        crude_to = oriented_header_attachment(
            refinery_recipe, "crude-oil", len(refinery_positions), origin,
            direction=refinery_direction, mirror=refinery_mirror,
        )["attach"]
        grid = _crude_patch_grid(client, surface, plastic_chest)
        used = {
            (math.floor(x / 50), math.floor(y / 50)) for x, y in jacks
        }
        cell = _pick_remote_crude_cell(grid, plastic_chest, used)
        if cell is None:
            emit("  OIL EXPANSION WAIT: no unused crude patch in survey range")
            return
        cell_centre = (cell[0] * 50.0 + 25.0, cell[1] * 50.0 + 25.0)
        tiles = _crude_patch_tiles(client, surface, cell_centre, radius=60.0)
        try:
            placement_blocked = live_base.occupied_tiles(
                client, surface,
                (min(x for x, _ in tiles) - 4, min(y for _, y in tiles) - 4),
                (max(x for x, _ in tiles) + 5, max(y for _, y in tiles) + 5),
                include_resources=False, include_clutter=True,
            ) if tiles else set()
        except Exception:
            placement_blocked = set()
        spots = _extra_pumpjack_spots(
            tiles, None, crude_to,
            draw_per_second=max(
                1.0, OPENING_CRUDE_TARGET_PER_SECOND - capacity,
            ),
            max_jacks=6,
            blocked_tiles=placement_blocked,
        )
        if not spots:
            _CRUDE_EXPANSION_FAILED_CELLS.add(cell)
            emit(
                f"  OIL EXPANSION WAIT: patch {cell} holds no pumpjack spot"
            )
            return
        outputs = [site["output"] for site in spots]
        source = generate_pumpjack_source(spots, outputs)
        links: list[tuple[str, dict]] = []
        foreign: list[dict] = []
        lo = (
            min(x for x, _ in [crude_to, *outputs]) - 24,
            min(y for _, y in [crude_to, *outputs]) - 24,
        )
        hi = (
            max(x for x, _ in [crude_to, *outputs]) + 24,
            max(y for _, y in [crude_to, *outputs]) + 24,
        )
        try:
            hard = live_base.occupied_tiles(
                client, surface, lo, hi, include_water=False,
            )
            terrain_water = live_base.water_tiles(client, surface, lo, hi)
        except Exception:
            hard, terrain_water = set(), set()
        try:
            for index, output in enumerate(outputs):
                link, segments, _crossed = _route_oil_fluid_link(
                    output, [crude_to], "crude-oil", foreign=foreign,
                    hard=set(hard), terrain_water=set(terrain_water),
                    existing_tiles=[output],
                )
                links.append((f"chemical_crude_expansion_{index}", link))
                foreign.extend(segments)
        except ValueError as error:
            _CRUDE_EXPANSION_FAILED_CELLS.add(cell)
            emit(f"  OIL EXPANSION WAIT: crude unroutable from {cell}: {error}")
            return
        _submit_oil_cell_packets(
            client, bridge, surface, force,
            [(name, link) for name, link in links]
            + [(f"chemical_crude_expansion_machines", source)],
            emit,
        )
        machines = _positions(source, "pumpjack")
        area = _area(source)
        try:
            substation_position = _substation(source)
        except Exception:
            substation_position = None
        if substation_position is None:
            try:
                substation_position = live_base.nearest_powered_pole(
                    client, surface, force, machines[0],
                )
            except Exception:
                substation_position = None
        service_stage(
            client, bridge, surface, force,
            f"remote crude {cell}", machines[0], area,
            substation_position if substation_position is not None else machines[0],
            machines, emit,
        )
        emit(
            f"  OIL EXPANSION: {len(spots)} pumpjack(s) on patch {cell} "
            f"toward {OPENING_CRUDE_TARGET_PER_SECOND:.0f}/s crude"
        )
    except Exception as error:
        emit(f"  OIL EXPANSION WAIT: {type(error).__name__}: {error}")


def ensure_oil_cell(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    reference: Point, service_stage: ServiceStage,
    emit: Callable[[str], None], *, target_output: str = "sulfur",
) -> dict[str, Point] | None:
    if target_output not in {"plastic-bar", "sulfur"}:
        raise ValueError(f"unsupported oil-cell output {target_output!r}")
    existing = _existing_outputs(client, surface, force) or {}
    if target_output in existing:
        # A built output chest outside every logistic network supplies
        # nothing no matter how healthy its machines are (2026-09-04: the
        # plastic provider sat 28 tiles from its port). Re-check on every
        # visit; already-covered chests cost one survey and no action.
        ensure_logistic_coverage(
            client, bridge, surface, force, list(existing.values()), emit,
        )
        _expand_remote_crude_if_starved(
            client, bridge, surface, force, service_stage, emit,
            existing["plastic-bar"] if "plastic-bar" in existing
            else next(iter(existing.values())),
        )
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
        raise StuckError("No ore-free 64x44 area found near the crude-oil source")
    ox, oy = round(cell[0]), round(cell[1])
    cell_centre = (ox + 32.0, oy + 22.0)
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
    try:
        patch_tiles = _crude_patch_tiles(client, surface, oil_pos)
    except Exception:
        patch_tiles = []
    # A crude tile is only the required resource overlap.  Survey the complete
    # 3x3 body before choosing a well, so a nearby pole, pipe, water tile, or
    # clutter cannot poison the all-or-nothing source packet.
    patch_points = [oil_pos, *patch_tiles]
    try:
        pumpjack_blocked = live_base.occupied_tiles(
            client, surface,
            (min(x for x, _ in patch_points) - 4, min(y for _, y in patch_points) - 4),
            (max(x for x, _ in patch_points) + 5, max(y for _, y in patch_points) + 5),
            include_resources=False, include_clutter=True,
        )
    except Exception:
        pumpjack_blocked = set()
    pumpjack_sites = _pumpjack_sites_for_patch(
        patch_tiles, oil_pos, cell_centre, blocked_tiles=pumpjack_blocked,
    )
    if not pumpjack_sites:
        raise StuckError("No legal pumpjack footprint on the selected crude-oil patch")
    oil_site, *extra_sites = pumpjack_sites
    if extra_sites:
        emit(
            f"  OIL DISTRICT: {len(pumpjack_sites)} pumpjacks to saturate the "
            f"{REFINERY_CRUDE_DRAW_PER_SECOND:.0f}/s refinery draw "
            f"({len(patch_tiles)} crude tiles surveyed)"
        )
    emit(
        f"  OIL DISTRICT: plastic at {(px, py)} between refinery "
        f"{cell_centre} and local coal belt {coal}"
    )
    refinery_recipe = oil_processing_recipe(0)
    plastic = generate_fluid_machine_row("plastic-bar", 2, px, py)
    plastic_gas = header_attachment(
        "plastic-bar", "petroleum-gas", 2, px, py,
    )["attach"]
    normal_refinery = generate_fluid_machine_row(
        refinery_recipe, OPENING_REFINERY_COUNT, ox, oy,
    )
    normal_tiles = occupied_tile_indices([("normal-refinery", normal_refinery)])
    refinery_center = (
        (min(x for x, _ in normal_tiles) + max(x for x, _ in normal_tiles) + 1) / 2,
        (min(y for _, y in normal_tiles) + max(y for _, y in normal_tiles) + 1) / 2,
    )
    layout_points = [oil_site["output"], plastic_gas, *normal_tiles]
    try:
        live_layout_blocked = live_base.occupied_tiles(
            client, surface,
            (
                min(x for x, _ in layout_points) - 24,
                min(y for _, y in layout_points) - 24,
            ),
            (
                max(x for x, _ in layout_points) + 24,
                max(y for _, y in layout_points) + 24,
            ),
            include_water=False,
        )
    except Exception:
        live_layout_blocked = set()
    layout_blocked = set(live_layout_blocked)
    layout_blocked |= _planned_hard_tiles(plastic)
    for site in pumpjack_sites:
        layout_blocked |= footprint_tile_indices(
            tuple(site["position"]), ENTITY_FOOTPRINTS["pumpjack"],
        )
    refinery_candidates = oriented_fluid_row_candidates(
        refinery_recipe, OPENING_REFINERY_COUNT, refinery_center,
        {
            "crude-oil": [oil_site["output"]],
            "petroleum-gas": [plastic_gas],
        },
        blocked_tiles=layout_blocked,
    )
    if not refinery_candidates:
        raise StuckError("No legal rotated or mirrored oil-refinery layout")
    refinery_choice = refinery_candidates[0]
    refinery = refinery_choice.plan
    (refinery_ox, refinery_oy) = refinery_choice.origin
    refinery_direction = refinery_choice.direction
    refinery_mirror = refinery_choice.mirror
    crude_to = refinery_choice.attachments["crude-oil"]
    petroleum_from = refinery_choice.attachments["petroleum-gas"]
    try:
        paired_growth = oriented_compact_paired_fluid_block(
            refinery_recipe, 2 * OPENING_REFINERY_COUNT,
            refinery_choice.origin, direction=refinery_choice.direction,
            mirror=refinery_choice.mirror,
        )
        opening_tiles = occupied_tile_indices([("opening-refinery", refinery)])
        growth_tiles = occupied_tile_indices([("paired-growth", paired_growth.plan)])
        refinery["reserved_tiles"] = [
            list(tile) for tile in sorted(growth_tiles - opening_tiles)
        ]
    except ValueError:
        refinery["reserved_tiles"] = []
    emit(
        f"  OIL LAYOUT: {refinery_choice.shape} {refinery_direction}"
        f"{'/' + refinery_mirror if refinery_mirror else ''} refinery row selected; "
        f"estimated pipe bill={refinery_choice.pipe_tiles}, "
        f"poles={refinery_choice.pole_count}, land={refinery_choice.occupied_tiles}, "
        f"expansion seam={refinery_choice.expansion_seam_tiles}"
    )
    emit(
        f"  OIL DISTRICT: chemical processing at "
        f"{(refinery_ox, refinery_oy)} near crude source {oil_pos}; "
        f"pumpjack faces {oil_site['direction']} toward its local pipe"
    )
    plastic_tiles = occupied_tile_indices([("normal-plastic", plastic)])
    plastic_center = (
        (min(x for x, _ in plastic_tiles) + max(x for x, _ in plastic_tiles) + 1) / 2,
        (min(y for _, y in plastic_tiles) + max(y for _, y in plastic_tiles) + 1) / 2,
    )
    plastic_blocked = set(live_layout_blocked) | _planned_hard_tiles(refinery)
    for site in pumpjack_sites:
        plastic_blocked |= footprint_tile_indices(
            tuple(site["position"]), ENTITY_FOOTPRINTS["pumpjack"],
        )
    plastic_candidates = oriented_fluid_row_candidates(
        "plastic-bar", 2, plastic_center,
        {"petroleum-gas": [petroleum_from]},
        blocked_tiles=plastic_blocked,
    )
    if not plastic_candidates:
        raise StuckError("No legal rotated or mirrored plastic-bar layout")
    plastic_choice = plastic_candidates[0]
    plastic = plastic_choice.plan
    plastic_gas = plastic_choice.attachments["petroleum-gas"]
    plastic_flow_direction = transform_direction(
        "east", direction=plastic_choice.direction, mirror=plastic_choice.mirror,
    )
    emit(
        f"  OIL LAYOUT: {plastic_choice.shape} {plastic_choice.direction}"
        f"{'/' + plastic_choice.mirror if plastic_choice.mirror else ''} plastic row selected; "
        f"estimated pipe bill={plastic_choice.pipe_tiles}, "
        f"poles={plastic_choice.pole_count}, land={plastic_choice.occupied_tiles}"
    )
    refinery_east = max(
        action["position"]["x"]
        for phase in refinery["phases"] for action in phase["actions"]
    )
    refinery_south = max(
        action["position"]["y"]
        for phase in refinery["phases"] for action in phase["actions"]
    )
    sulfur_ox, sulfur_oy = round(refinery_east) + 10, round(refinery_south) + 6
    sulfur = generate_fluid_machine_row("sulfur", 2, sulfur_ox, sulfur_oy)
    crude_source = generate_pumpjack_source(
        pumpjack_sites, [site["output"] for site in pumpjack_sites],
    )
    water_source = generate_offshore_pump_source([water], [water["output"]])
    plans = [
        strip_local_power(plan, remove_substations=False)
        for plan in (crude_source, water_source, refinery, plastic, sulfur)
    ]
    crude_source, water_source, refinery, plastic, sulfur = plans
    plastic_feed = _direct_single_belt_feed(
        plastic, "coal", plastic_flow_direction,
    )
    _publish_output_chest(plastic)
    _publish_output_chest(sulfur)

    sulfur_gas = header_attachment("sulfur", "petroleum-gas", 2, sulfur_ox, sulfur_oy)["attach"]
    sulfur_water = header_attachment("sulfur", "water", 2, sulfur_ox, sulfur_oy)["attach"]
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
        + oriented_fluid_network_segments(
            refinery_recipe, OPENING_REFINERY_COUNT,
            (refinery_ox, refinery_oy),
            direction=refinery_direction, mirror=refinery_mirror,
        )
        + oriented_fluid_network_segments(
            "plastic-bar", 2, plastic_choice.origin,
            direction=plastic_choice.direction, mirror=plastic_choice.mirror,
        )
        + (
            fluid_network_segments("sulfur", 2, sulfur_ox, sulfur_oy)
            if include_sulfur else []
        )
    )
    links: list[tuple[str, dict]] = []
    # The first jack owns the trunk to the refinery; every extra jack taps
    # the nearest crude tile (trunk or header) with targets=None resolved
    # after the trunk exists. Separate full-length pipelines per jack wasted
    # pipe, crossed each other oddly, and left jacks unconnected when their
    # packet died (2026-09-05: two of three jacks sat on lone stub pipes).
    requested_links = [
        (
            "chemical_crude_pipeline", oil_site["output"], [crude_to],
            "crude-oil", [oil_site["output"]],
        ),
        *(
            (
                f"chemical_crude_pipeline_{index}", site["output"], None,
                "crude-oil", [site["output"]],
            )
            for index, site in enumerate(extra_sites, start=2)
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
        if targets is None:
            targets = [_nearest_crude_tile(foreign, source)]
            emit(
                f"  FLUID ROUTE: {link_name} taps the crude trunk at "
                f"{targets[0]} instead of running to the refinery"
            )
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
        destination_belt_direction=plastic_flow_direction,
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
        *(
            (link_name, link_packets[link_name])
            for link_name in sorted(link_packets)
            if link_name.startswith("chemical_crude_pipeline_")
        ),
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
                reserved_tiles=_oil_cell_power_reserve_tiles(plans, links),
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
    # Providers must sit inside a live logistic network, not merely inside
    # construction reach (see the early-return recheck above) -- and before
    # health is measured, so an unreachable chest cannot masquerade as an
    # unhealthy machine. Verify while power and ports are fresh.
    ensure_logistic_coverage(
        client, bridge, surface, force,
        [_provider(plastic)]
        + ([_provider(sulfur)] if include_sulfur else []),
        emit,
    )
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
