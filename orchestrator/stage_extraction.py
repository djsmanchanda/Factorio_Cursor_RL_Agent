# Path: orchestrator/stage_extraction.py
# Purpose: Deterministic local extraction geometry that keeps mining egress on ore and smelting off the ore reservation.

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from orchestrator import extraction_capacity, extraction_state, live_base, resource_patches
from planners.plan_validation import ENTITY_FOOTPRINTS, actions
from planners.smelter_block import (
    FURNACES_PER_MODULE, generate_managed_refinery_plan, refinery_interfaces,
)
from planners.resource_layouts import (
    generate_direct_mine_row_expansion,
    generate_shared_belt_batch_expansion,
    generate_shared_belt_column_expansion,
    generate_direct_mining_to_chest,
)
from planners.recipe_data import LINE_RECIPES, MACHINE_SPEEDS
from planners.zoning_geometry import MINING_APRON_TILES, Rect
from tools.rcon_client import RconClient

Point = tuple[float, float]
ELECTRIC_DRILL_ITEMS_PER_SECOND = 0.5
LOCAL_MODE_MAX_LINK_TILES = 300.0
RESERVED_ADDITIONAL_DRILLS = 20
RESERVED_PAIR_COLUMNS = extraction_state.RESERVED_PAIR_COLUMNS


@dataclass(frozen=True)
class LocalExtractionPlan:
    ore: str
    mine_origin: Point | None
    drill_count: int
    furnace_count: int
    mining_productivity_bonus: float
    smelter_origin: Point
    ore_output: Point
    build_plan: dict | None
    expansion_positions: tuple[Point, ...] = ()
    row_drill_count: int = 0
    expansion_step: int = -1
    system_drill_count_before: int = 0
    system_drill_target: int = 0
    shared_belt_y: float | None = None
    smelter_flow_direction: str = "east"


def mining_drill_positions(origin: Point, machine_count: int) -> list[Point]:
    """Centres for a south-facing drill row whose output is a local belt."""
    ox, oy = origin
    return [(ox + 1.5 + (3 * index), oy - 1.5) for index in range(machine_count)]

def paired_mining_drill_positions(origin: Point, machine_count: int) -> list[Point]:
    """Three-above/belt/three-below geometry for a complete mine unit."""
    upper = mining_drill_positions(origin, machine_count)
    belt_y = origin[1] + 0.5
    return upper + [(x, belt_y + 2) for x, _ in upper]


def existing_mine_service_geometry(
    output: Point, drill_count: int, expansion_step: int = -1, *,
    shared_belt_y: float | None = None,
) -> tuple[Point, tuple[Point, Point], Point, list[Point]]:
    """Reconstruct immutable direct-mine power/service geometry on retry."""
    belt_y = output[1] if shared_belt_y is None else shared_belt_y
    upper = [(output[0] + expansion_step * (4 + 3 * index), belt_y - 2)
             for index in range(drill_count)]
    drills = upper + [(x, belt_y + 2) for x, _ in upper]
    first_x = min(position[0] for position in upper)
    last_x = max(position[0] for position in upper)
    origin = (first_x - 1.5, belt_y - 0.5)
    area = ((min(first_x, output[0]) - 15, belt_y - 15),
            (max(last_x, output[0]) + 15, belt_y + 15))
    return origin, area, (first_x - 6, belt_y - 4), drills

def adjacent_mining_positions(
    output: Point, drill_count: int, expansion_step: int = -1,
) -> list[Point]:
    """Mirror the primary south-facing row below its shared output belt."""
    return sorted([
        (output[0] + expansion_step * (4 + 3 * index), output[1] + 2)
        for index in range(drill_count)
    ])


def adjacent_mine_row_state(
    client: RconClient, surface: str, output: Point, drill_count: int,
    expansion_step: int = -1,
) -> str:
    """Return missing, complete, or partial for the deterministic second row."""
    entities = [
        live_base.entity_at(client, surface, position)
        for position in adjacent_mining_positions(output, drill_count, expansion_step)
    ]
    drills = [entity is not None and entity["name"] == "electric-mining-drill"
              for entity in entities]
    if all(drills):
        return "complete"
    if any(entity is not None for entity in entities):
        return "partial"
    return "missing"


def candidate_mining_origins(
    preferred: Point, patch_min: Point, patch_max: Point, machine_count: int,
) -> list[Point]:
    """Integer row origins whose drill footprints can overlap the patch box."""
    min_x = math.floor(patch_min[0]) - (3 * machine_count) + 1
    max_x = math.floor(patch_max[0])
    min_y = math.floor(patch_min[1]) + 1
    max_y = math.floor(patch_max[1]) + 3
    origins = [
        (float(x), float(y))
        for x in range(min_x, max_x + 1)
        for y in range(min_y, max_y + 1)
    ]
    return sorted(
        origins, key=lambda point: (math.dist(point, preferred), point[1], point[0])
    )


def choose_mining_origin(
    preferred: Point,
    patch_min: Point,
    patch_max: Point,
    machine_count: int,
    area_is_clear: Callable[[Point, Point], bool],
    footprint_has_resource: Callable[[list[Point]], bool],
    reserved_pair_columns: int = 0,
) -> tuple[Point, int] | None:
    """Choose a complete paired row; never shrink below requested capacity."""
    for origin in candidate_mining_origins(
        preferred, patch_min, patch_max, machine_count
    ):
        ox, oy = origin
        total_columns = machine_count + reserved_pair_columns
        if not area_is_clear(
            (ox - 6, oy - 5), (ox + total_columns * 3 + 4, oy + 7)
        ):
            continue
        if footprint_has_resource(
            paired_mining_drill_positions(origin, total_columns)
        ):
            return origin, machine_count
    return None

def _supported_pair_reserve(
    origin: Point,
    row_drill_count: int,
    maximum: int,
    area_is_clear: Callable[[Point, Point], bool],
    footprint_has_resource: Callable[[list[Point]], bool],
) -> int:
    """Largest contiguous future corridor supported by this exact patch slice."""
    ox, oy = origin
    low, high, best = 0, maximum, 0
    while low <= high:
        candidate = (low + high) // 2
        total_columns = row_drill_count + candidate
        fits = area_is_clear(
            (ox - 6, oy - 5), (ox + total_columns * 3 + 4, oy + 7),
        ) and footprint_has_resource(
            paired_mining_drill_positions(origin, total_columns)
        )
        if fits:
            best, low = candidate, candidate + 1
        else:
            high = candidate - 1
    return best

def direct_mine_plan(
    origin: Point,
    machine_count: int,
    *,
    belt_type: str,
    inserter_type: str,
    reserved_pair_columns: int = RESERVED_PAIR_COLUMNS,
    prebuilt_pair_columns: int = 0,
) -> tuple[dict, Point]:
    """Return a paired mine start with its measured future corridor reserved."""
    ox, oy = origin
    upper = mining_drill_positions(origin, machine_count)
    belt_anchor = (ox - 2.5, oy + 0.5)
    # Dedicated refinery feeds are belt-only: belt_anchor is the west turn tile.
    # Side taps remain available to generic multi-input layouts, but never sit
    # in the raw ore path.
    output_chest = (belt_anchor[0] + 2, belt_anchor[1])
    inserter_type = "inserter"
    plan = generate_direct_mining_to_chest(
        upper, output_chest, belt_type=belt_type, inserter_type=inserter_type,
        output_side="west", reserved_pair_columns=reserved_pair_columns,
        prebuilt_pair_columns=prebuilt_pair_columns,
        include_side_tap=False,
    )
    lower = [(x, belt_anchor[1] + 2) for x, _ in upper]
    mirrored = generate_direct_mine_row_expansion(lower, belt_anchor[1])
    plan["phases"].extend(mirrored["phases"])
    return plan, belt_anchor


def _new_direct_mine(
    client: RconClient, surface: str, ore: str, nearest_tile: Point,
    patch_min: Point, patch_max: Point, machine_count: int,
    belt_type: str, inserter_type: str, belt_stock: int,
) -> tuple[Point, int, dict, Point]:
    """Choose the next clear independent row on the same resource patch."""
    patch_columns = max(0, math.floor((patch_max[0] - patch_min[0]) / 3) + 1)
    row_drill_count = min(machine_count, patch_columns)
    if row_drill_count < 1:
        raise ValueError(f"The {ore} patch cannot fit a mining-drill pair")
    maximum_reserve = min(
        RESERVED_PAIR_COLUMNS, patch_columns - row_drill_count,
    )
    preferred_box = live_base.find_clear_area(
        client, surface, (nearest_tile[0] - 5, nearest_tile[1] - 5),
        row_drill_count * 3 + 10, 12,
    )
    if preferred_box is None:
        raise ValueError(f"No clear staging area found near the {ore} patch at {nearest_tile}")
    preferred = (round(preferred_box[0]), round(preferred_box[1] + 5))
    area_is_clear = lambda lower, upper: live_base.area_clear(
        client, surface, lower, upper
    )
    footprint_has_resource = lambda centres: live_base.drill_footprints_have_resource(
        client, surface, ore, centres
    )
    selected = choose_mining_origin(
        preferred, patch_min, patch_max, row_drill_count,
        area_is_clear, footprint_has_resource, 0,
    )
    if selected is None:
        raise ValueError(
            f"No clear position near the {ore} patch at {nearest_tile} "
            "puts every drill on ore"
        )
    selected_origin, row_drill_count = selected
    origin = (int(selected_origin[0]), int(selected_origin[1]))
    reserved_columns = _supported_pair_reserve(
        origin, row_drill_count, maximum_reserve,
        area_is_clear, footprint_has_resource,
    )
    prebuilt_columns = extraction_capacity.affordable_prebuilt_columns(
        belt_stock, reserved_columns,
    )
    plan, output = direct_mine_plan(
        origin, row_drill_count, belt_type=belt_type, inserter_type=inserter_type,
        reserved_pair_columns=reserved_columns,
        prebuilt_pair_columns=prebuilt_columns,
    )
    return origin, row_drill_count * 2, plan, output


def buildable_batch_prefix(
    client: RconClient, surface: str, ore: str, positions: tuple[Point, ...],
) -> tuple[Point, ...]:
    """The leading run of reserved columns that can actually be built today.

    A reserved corridor is checked outward from the mine, and the answer stops
    at the first column that is blocked or would put a drill's mining area over
    a foreign ore. The prefix is what matters rather than the clean subset: the
    shared belt is paved three tiles per column and columns sit three apart, so
    a skipped column is a severed belt, and every drill past it feeds nothing.

    Returning a short prefix -- possibly empty -- instead of raising is what
    lets a corridor that has grown into a tree line or a neighbouring patch stop
    being retried identically forever. An empty answer tells the caller this
    corridor is finished and the phase needs a new row somewhere else.
    """
    columns: list[tuple[Point, Point]] = [
        (positions[index], positions[index + 1])
        for index in range(0, len(positions) - 1, 2)
    ]
    if not columns:
        return ()
    conflicted = {
        centre for centre, _reason in live_base.drill_siting_conflicts(
            client, surface, ore, list(positions),
        )
    }
    buildable: list[Point] = []
    for pair in columns:
        if any(
            site in conflicted or not live_base.area_clear(
                client, surface,
                (site[0] - 1.5, site[1] - 1.5), (site[0] + 1.5, site[1] + 1.5),
            )
            for site in pair
        ):
            break
        buildable.extend(pair)
    return tuple(buildable)


def smelter_count_for_drills(
    recipe: str, drill_count: int, mining_productivity_bonus: float,
) -> int:
    """Size furnaces from drill and force rates instead of pairing machines."""
    if drill_count <= 0:
        raise ValueError("drill_count must be positive")
    if not math.isfinite(mining_productivity_bonus) or mining_productivity_bonus < 0:
        raise ValueError("mining_productivity_bonus must be finite and non-negative")
    spec = LINE_RECIPES[recipe]
    # Size on the actual first-input draw, not merely output units. Stone
    # brick consumes two stone per craft, so treating its product rate as the
    # ore rate overbuilds a row (7 furnaces fed by 6 drills at roughly 45%).
    input_amount = spec.get("amounts", [1])[0]
    furnace_input_rate = (
        MACHINE_SPEEDS[spec["machine"]] * input_amount / spec["craft_time"]
    )
    ore_rate = (
        drill_count
        * ELECTRIC_DRILL_ITEMS_PER_SECOND
        * (1.0 + mining_productivity_bonus)
    )
    return max(1, math.ceil(ore_rate / furnace_input_rate))


def planned_smelter_count_for_drills(
    recipe: str, drill_count: int, mining_productivity_bonus: float,
) -> int:
    """Keep the initial furnace lattice proportional to its mine capacity.

    Rate sizing may ask for one more furnace than a six-furnace module can
    represent.  Rounding seven required furnaces to twelve doubles the
    opening plant before the mine can feed it, so cap the planned count at the
    available drill count and let the blueprint use whole modules.
    """
    required = smelter_count_for_drills(
        recipe, drill_count, mining_productivity_bonus,
    )
    return min(required, max(FURNACES_PER_MODULE, drill_count))


def ore_reservation(
    patch_min: Point,
    patch_max: Point,
    *,
    apron: float = MINING_APRON_TILES,
) -> tuple[Point, Point]:
    """Approximate patch bounds used only to seed external search anchors."""
    reserved = Rect(
        patch_min[0] - 0.5,
        patch_min[1] - 0.5,
        patch_max[0] + 0.5,
        patch_max[1] + 0.5,
    ).expanded(apron)
    return (reserved.min_x, reserved.min_y), (reserved.max_x, reserved.max_y)


def smelter_search_anchors(
    patch_min: Point,
    patch_max: Point,
    footprint: tuple[float, float],
    reference_point: Point,
    ore_output: Point | None = None,
) -> list[Point]:
    """Cardinal search anchors, nearest the ORE first.

    A smelter is sited beside the thing it consumes, which for a furnace row
    is unambiguously the ore -- the same rule a promoted line already follows
    through `_heaviest_source`. Ordering by distance to the BASE instead put
    the search on the far side of the patch from where the ore comes out, and
    the site that won was 61 tiles from the mine it was smelting for.

    The ore haul is the expensive side of the trade: it carries the drill
    row's whole output, and it is re-laid every time the row grows
    6 -> 20 -> 50 -> 100, so its length is paid over and over. The plate belt
    leaving the smelter is built once. `reference_point` only breaks ties.

    Live resource clearance remains authoritative over all of this.
    """
    (min_x, min_y), (max_x, max_y) = ore_reservation(patch_min, patch_max)
    width, height = footprint
    centre_x = (min_x + max_x) / 2
    centre_y = (min_y + max_y) / 2
    anchors = [
        (min_x - width, centre_y - height / 2),
        (max_x, centre_y - height / 2),
        (centre_x - width / 2, min_y - height),
        (centre_x - width / 2, max_y),
    ]
    if ore_output is not None:
        # The four cardinal anchors are edges of the whole PATCH. None of
        # them is where the ore actually leaves, so on a long patch the
        # search began tens of tiles from the belt it was meant to meet.
        # These two put the footprint immediately beyond the reservation,
        # level with the output, on whichever side it sits.
        beside_x = min_x - width if ore_output[0] < centre_x else max_x
        beside_y = min_y - height if ore_output[1] < centre_y else max_y
        anchors.append((beside_x, ore_output[1] - height / 2))
        anchors.append((ore_output[0] - width / 2, beside_y))
    near = ore_output if ore_output is not None else reference_point

    def belt_tiles(point: Point, to: Point) -> float:
        # MANHATTAN, because that is what a belt costs and what the candidate
        # scoring downstream measures. Ranking anchors by straight-line
        # distance disagreed with the thing being minimised, and put a site
        # 16.5 belt-tiles from the ore behind one at 18.0.
        centre = (point[0] + width / 2, point[1] + height / 2)
        return abs(centre[0] - to[0]) + abs(centre[1] - to[1])

    return sorted(
        anchors,
        key=lambda point: (
            belt_tiles(point, near),
            belt_tiles(point, reference_point),
            point[1],
            point[0],
        ),
    )


def _smelter_layout_geometry(
    recipe: str, machine_count: int, belt_type: str, inserter_type: str,
    flow_direction: str = "east",
) -> tuple[Rect, Point, Point]:
    """Exact modular bounds plus direct ore-belt and provider interfaces."""
    del belt_type, inserter_type
    if flow_direction != "east":
        raise ValueError("The approved modular refinery blueprint is eastbound")
    plan = generate_managed_refinery_plan(recipe, machine_count)
    retained = list(actions(plan))
    extents = []
    for action in retained:
        size = ENTITY_FOOTPRINTS.get(action["entity"], 1)
        x, y = action["position"]["x"], action["position"]["y"]
        extents.append((x - size / 2, y - size / 2, x + size / 2, y + size / 2))
    interface = refinery_interfaces(machine_count)
    return (
        Rect(
            min(box[0] for box in extents), min(box[1] for box in extents),
            max(box[2] for box in extents), max(box[3] for box in extents),
        ),
        interface.ore_inputs[0],
        interface.provider,
    )


def _smelter_geometry(
    recipe: str, machine_count: int, belt_type: str, inserter_type: str,
    flow_direction: str = "east",
) -> tuple[Rect, Point]:
    """Compatibility view used by existing footprint callers and tests."""
    bounds, feed, _output = _smelter_layout_geometry(
        recipe, machine_count, belt_type, inserter_type, flow_direction,
    )
    return bounds, feed

def _align_area_anchor(anchor: Point, bounds: Rect) -> Point:
    """Align a bounds corner so translating it yields an integer line origin."""
    return (
        math.floor(anchor[0] - bounds.min_x) + bounds.min_x,
        math.floor(anchor[1] - bounds.min_y) + bounds.min_y,
    )


def plan_local_extraction(
    client: RconClient,
    surface: str,
    force: str,
    recipe: str,
    reference_point: Point,
    machine_count: int,
    *,
    belt_type: str,
    inserter_type: str,
    reuse_existing: bool = True,
    belt_stock: int = 0,
) -> LocalExtractionPlan:
    """Reconcile mining, then reserve an exact, bounded, off-ore smelter."""
    ore = LINE_RECIPES[recipe]["ingredients"][0]
    mines = extraction_state.find_resource_mines(
        client, surface, force, ore, reference_point
    )
    observed = mines[0] if mines else None
    if observed is not None and extraction_state.pending_plate_smelter(
        client, surface, force, ore, observed.output
    ):
        raise ValueError(
            f"A pending off-ore smelter already exists near {observed.output}; "
            "refusing to submit a duplicate line"
        )
    system_before = extraction_state.resource_drill_count(
        client, surface, force, ore
    )
    phase_target = extraction_capacity.next_drill_phase(system_before)
    if not reuse_existing and phase_target is None:
        raise ValueError(
            f"{ore} extraction is already at the final "
            f"{extraction_capacity.EXTRACTION_DRILL_PHASES[-1]}-drill phase"
        )
    existing = observed if reuse_existing else None
    active = extraction_capacity.expandable_mine(mines) if not reuse_existing else None
    survey_mine = active or observed
    survey_near = survey_mine.output if survey_mine is not None else reference_point
    found = resource_patches.patch_for_extraction(client, surface, ore, survey_near, active=survey_mine is not None)
    if found is None:
        raise ValueError(
            f"No {ore} found within survey radius of {survey_near} -- "
            "cannot mine what isn't on the map"
        )
    nearest_tile, patch_min, patch_max = found.nearest, found.minimum, found.maximum
    productivity = extraction_state.mining_productivity_bonus(client, force)
    mine_origin: Point | None = None
    build_plan: dict | None = None
    expansion_positions: tuple[Point, ...] = ()
    row_drill_count = 0
    expansion_step = -1
    if existing is not None:
        expansion_step = existing.expansion_step
        row_drill_count = existing.drill_count
        row_state = adjacent_mine_row_state(
            client, surface, (existing.output[0], existing.shared_belt_y),
            row_drill_count, expansion_step
        )
        # A partially built row is recoverable: ghosts and powered/service
        # gaps must reach the normal remediation path instead of aborting the
        # run before coverage and power can finish the row. Count only the
        # observed row here; the next expansion pass will re-survey capacity.
        drill_count = row_drill_count * (
            2 if existing.pending or row_state == "complete" else 1
        )
        ore_output = existing.output
    elif mines:
        requested = phase_target - system_before
        requested += requested % 2
        positions = (
            extraction_capacity.phase_batch_positions(active, requested)
            if active is not None else ()
        )
        positions = buildable_batch_prefix(client, surface, ore, positions)
        if positions:
            drill_xs = sorted({x for x, _y in positions})
            mine_origin = (drill_xs[0] - 1.5, active.shared_belt_y + 3.5)
            build_plan = generate_shared_belt_batch_expansion(
                drill_xs, active.shared_belt_y,
                belt_direction="west" if active.expansion_step > 0 else "east",
            )
            drill_count = len(positions)
            ore_output = active.output
            row_drill_count = len(drill_xs)
            expansion_step = active.expansion_step
            expansion_positions = positions
        else:
            pair_count = max(3, math.ceil(requested / 2))
            mine_origin, drill_count, build_plan, ore_output = _new_direct_mine(
                client, surface, ore, nearest_tile, patch_min, patch_max,
                pair_count, belt_type, inserter_type, belt_stock,
            )
            row_drill_count = drill_count // 2
            expansion_step = 1
    else:
        mine_origin, drill_count, build_plan, ore_output = _new_direct_mine(
            client, surface, ore, nearest_tile, patch_min, patch_max,
            machine_count, belt_type, inserter_type, belt_stock,
        )
        row_drill_count = drill_count // 2
        expansion_step = 1
    furnace_count = planned_smelter_count_for_drills(
        recipe, drill_count, productivity,
    )
    geometries = {
        "east": _smelter_layout_geometry(
            recipe, furnace_count, belt_type, inserter_type, "east",
        )
    }
    east_bounds = geometries["east"][0]
    footprint = (
        east_bounds.max_x - east_bounds.min_x,
        east_bounds.max_y - east_bounds.min_y,
    )
    smelter_origin = None
    smelter_flow_direction = "east"
    candidates: list[tuple[bool, bool, float, float, str, Point]] = []
    for anchor in smelter_search_anchors(
        patch_min, patch_max, footprint, reference_point, ore_output,
    ):
        for direction, (bounds, feed_offset, output_offset) in geometries.items():
            oriented_anchor = _align_area_anchor(anchor, bounds)
            area_min = live_base.find_clear_area(
                client, surface, oriented_anchor,
                bounds.max_x - bounds.min_x, bounds.max_y - bounds.min_y,
                max_radius=60.0, avoid_resources=True,
                resource_clearance=MINING_APRON_TILES,
            )
            if area_min is None:
                continue
            candidate = (
                area_min[0] - bounds.min_x, area_min[1] - bounds.min_y,
            )
            feed = (
                candidate[0] + feed_offset[0], candidate[1] + feed_offset[1],
            )
            output = (
                candidate[0] + output_offset[0],
                candidate[1] + output_offset[1],
            )
            input_tiles = abs(feed[0] - ore_output[0]) + abs(feed[1] - ore_output[1])
            output_tiles = (
                abs(output[0] - reference_point[0])
                + abs(output[1] - reference_point[1])
            )
            mine_to_output = (
                abs(output[0] - ore_output[0])
                + abs(output[1] - ore_output[1])
            )
            target_to_feed = (
                abs(feed[0] - reference_point[0])
                + abs(feed[1] - reference_point[1])
            )
            if input_tiles <= LOCAL_MODE_MAX_LINK_TILES:
                candidates.append((
                    input_tiles > mine_to_output,
                    output_tiles > target_to_feed,
                    # ORE HAUL FIRST. Summing the two hauls let a site 61
                    # tiles from the mine beat one far closer that happened
                    # to sit further from the base -- but the ore belt is
                    # re-laid every time the drill row grows, and the plate
                    # belt out is built once.
                    input_tiles,
                    input_tiles + output_tiles,
                    direction,
                    candidate,
                ))
    if candidates:
        (
            _input_wrong_way, _output_wrong_way, _input, _total,
            smelter_flow_direction, smelter_origin,
        ) = min(candidates)
    if smelter_origin is None:
        raise ValueError(
            f"No off-ore {recipe} site is available within the "
            f"{LOCAL_MODE_MAX_LINK_TILES:.0f}-tile local-mode link limit; "
            "CityPlanner rail handoff is required"
        )
    return LocalExtractionPlan(
        ore=ore,
        mine_origin=mine_origin,
        drill_count=drill_count,
        furnace_count=furnace_count,
        mining_productivity_bonus=productivity,
        smelter_origin=smelter_origin,
        ore_output=ore_output,
        build_plan=build_plan,
        expansion_positions=expansion_positions,
        row_drill_count=row_drill_count,
        expansion_step=expansion_step,
        system_drill_count_before=system_before,
        smelter_flow_direction=smelter_flow_direction,
        shared_belt_y=(
            (existing or active).shared_belt_y if (existing or active) is not None
            else ore_output[1] + 2
        ),
        system_drill_target=(
            phase_target if not reuse_existing or not mines else system_before
        ),
    )
