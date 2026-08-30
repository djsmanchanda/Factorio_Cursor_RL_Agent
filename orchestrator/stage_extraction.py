# Path: orchestrator/stage_extraction.py
# Purpose: Deterministic local extraction geometry that keeps mining egress on ore and smelting off the ore reservation.

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from orchestrator import extraction_capacity, extraction_state, live_base, resource_patches
from orchestrator.work_state import WorkStateSignal
from planners.plan_validation import ENTITY_FOOTPRINTS, actions
from planners.smelter_block import (
    FURNACES_PER_MODULE, REFINERY_GENERATION_1_CAPACITIES,
    generate_managed_refinery_plan, refinery_interfaces,
)
from planners.resource_layouts import (
    DIRECT_EAST_CONTINUATION_TILES,
    generate_direct_mine_row_expansion,
    generate_parallel_mining_row_expansion,
    generate_shared_belt_batch_expansion,
    generate_shared_belt_column_expansion,
    generate_direct_mining_to_chest,
    mine_substation_positions,
)
from planners.recipe_data import LINE_RECIPES, MACHINE_SPEEDS
from planners.zoning_geometry import MINING_APRON_TILES, Rect
from tools.rcon_client import RconClient

Point = tuple[float, float]
ELECTRIC_DRILL_ITEMS_PER_SECOND = 0.5
LOCAL_MODE_MAX_LINK_TILES = 300.0
RESERVED_ADDITIONAL_DRILLS = 20
RESERVED_PAIR_COLUMNS = extraction_state.RESERVED_PAIR_COLUMNS
REFINERY_SITE_CLEARANCE_TILES = 10.0


class PendingSystemDeferred(WorkStateSignal):
    """A plate system for this ore is still constructing; demand must wait.

    The typed state lets callers wait for construction without matching prose."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            code="pending_system_construction",
            classification="intended_difficulty",
            state="constructing",
        )


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
    smelter_vertical_mirror: bool = False
    first_column_x: float | None = None
    smelter_reserved_area: tuple[Point, Point] | None = None


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
    first_column_x: float | None = None,
) -> tuple[Point, tuple[Point, Point], Point, list[Point]]:
    """Reconstruct immutable direct-mine power/service geometry on retry."""
    belt_y = output[1] if shared_belt_y is None else shared_belt_y
    if first_column_x is not None:
        upper = [(first_column_x + 3 * index, belt_y - 2)
                 for index in range(drill_count)]
    else:
        upper = [(output[0] + expansion_step * (4 + 3 * index), belt_y - 2)
                 for index in range(drill_count)]
    drills = upper + [(x, belt_y + 2) for x, _ in upper]
    first_x = min(position[0] for position in upper)
    last_x = max(position[0] for position in upper)
    origin = (first_x - 1.5, belt_y - 0.5)
    area = ((min(first_x, output[0]) - 15, belt_y - 15),
            (max(last_x, output[0]) + 15, belt_y + 15))
    substation = mine_substation_positions(
        sorted({x for x, _y in drills}), belt_y,
    )[0]
    return origin, area, substation, drills

def adjacent_mining_positions(
    output: Point, drill_count: int, expansion_step: int = -1,
    *, first_column_x: float | None = None,
) -> list[Point]:
    """Mirror the primary south-facing row below its shared output belt."""
    return sorted([
        ((first_column_x + 3 * index) if first_column_x is not None
         else output[0] + expansion_step * (4 + 3 * index), output[1] + 2)
        for index in range(drill_count)
    ])


def adjacent_mine_row_state(
    client: RconClient, surface: str, output: Point, drill_count: int,
    expansion_step: int = -1, *, first_column_x: float | None = None,
) -> str:
    """Return missing, complete, or partial for the deterministic second row."""
    entities = [
        live_base.entity_at(client, surface, position)
        for position in adjacent_mining_positions(
            output, drill_count, expansion_step, first_column_x=first_column_x,
        )
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
    allowed_origins: set[Point] | None = None,
    max_area_probes: int = 300,
) -> tuple[Point, int] | None:
    """Choose a complete paired row; never shrink below requested capacity.

    Probing is bounded: candidate origins span the whole patch box, and each
    costs two live RCON surveys. Live run 28 (2026-08-22) spent ~4 minutes
    silently probing 2226 candidates on a patch already saturated by our own
    drill rows before failing -- exhaustion must surface in seconds.
    """
    probes = 0
    for origin in candidate_mining_origins(
        preferred, patch_min, patch_max, machine_count
    ):
        if allowed_origins is not None and origin not in allowed_origins:
            continue
        ox, oy = origin
        total_columns = machine_count + reserved_pair_columns
        probes += 1
        if probes > max_area_probes:
            return None
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


def choose_mining_origin_with_reserve(
    preferred: Point,
    patch_min: Point,
    patch_max: Point,
    machine_count: int,
    maximum_reserve: int,
    area_is_clear: Callable[[Point, Point], bool],
    footprint_has_resource: Callable[[list[Point]], bool],
    *,
    max_candidates: int = 300,
) -> tuple[Point, int] | None:
    """Choose the nearest row with the largest supported future reserve.

    Each candidate origin is visited once. The previous descending reserve
    loop restarted the same 300-candidate live survey for every reserve size;
    the reduced-v1 copper patch therefore spent about 162 seconds repeating
    identical RCON probes before reaching the same six-drill row.
    """
    best: tuple[Point, int] | None = None
    for probes, origin in enumerate(candidate_mining_origins(
        preferred, patch_min, patch_max, machine_count,
    ), start=1):
        if probes > max_candidates:
            break
        ox, oy = origin
        if not area_is_clear(
            (ox - 6, oy - 5), (ox + machine_count * 3 + 4, oy + 7),
        ):
            continue
        if not footprint_has_resource(
            paired_mining_drill_positions(origin, machine_count),
        ):
            continue
        reserve = _supported_pair_reserve(
            origin, machine_count, maximum_reserve,
            area_is_clear, footprint_has_resource,
        )
        if best is None or reserve > best[1]:
            best = (origin, reserve)
        # Candidate ordering already encodes proximity. No later candidate can
        # beat the maximum reserve or win its nearest-candidate tie.
        if reserve == maximum_reserve:
            return best
    return best

def direct_mine_plan(
    origin: Point,
    machine_count: int,
    *,
    belt_type: str,
    inserter_type: str,
    reserved_pair_columns: int = RESERVED_PAIR_COLUMNS,
    prebuilt_pair_columns: int = 0,
    output_side: str = "west",
    continuation_tiles: int | None = None,
) -> tuple[dict, Point]:
    """Return a paired mine start with its measured future corridor reserved.

    ``output_side`` picks which end of the shared collector belt is the haul
    head. Managed refineries are east-flow and their feed must be approached
    travelling east, so east of the row is the only side that connects without
    a detour around the whole patch (live run of 2026-08-24 04:00 hauled from
    a west-flowing head, misconnected diagonally, and starved the refinery).
    """
    ox, oy = origin
    upper = mining_drill_positions(origin, machine_count)
    belt_y = oy + 0.5
    if output_side not in {"east", "west"}:
        raise ValueError(f"Unknown mine output side: {output_side}")
    if output_side == "east":
        last_x = upper[-1][0]
        # Keep the collector straight east for a bounded reserve before the
        # refinery haul turns. This leaves a reusable line for later columns
        # instead of forcing every expansion through the first corner.
        if continuation_tiles is None:
            continuation_tiles = DIRECT_EAST_CONTINUATION_TILES
        collector_end = last_x + 2 + continuation_tiles
        belt_anchor = (collector_end, belt_y)
        # The generator extends from the actual drill head. Passing the
        # already-extended anchor as its chest would count the reserve twice.
        output_chest = (last_x + 2, belt_y)
    else:
        if continuation_tiles not in (None, 0):
            raise ValueError("continuation_tiles only applies to east-flow mines")
        belt_anchor = (ox - 2.5, belt_y)
        # Dedicated refinery feeds are belt-only: belt_anchor is the west turn tile.
        # Side taps remain available to generic multi-input layouts, but never sit
        # in the raw ore path.
        output_chest = (belt_anchor[0] + 2, belt_anchor[1])
    inserter_type = "inserter"
    plan = generate_direct_mining_to_chest(
        upper, output_chest, belt_type=belt_type, inserter_type=inserter_type,
        output_side=output_side, reserved_pair_columns=reserved_pair_columns,
        prebuilt_pair_columns=prebuilt_pair_columns,
        continuation_tiles=continuation_tiles or 0,
        include_side_tap=False,
    )
    plan["phases"][0]["actions"] = [
        {"action_type": "place_entity", "entity": "substation",
         "position": {"x": x, "y": y}}
        for x, y in mine_substation_positions(
            [x for x, _y in upper], belt_anchor[1],
        )
    ]
    lower = [(x, belt_anchor[1] + 2) for x, _ in upper]
    mirrored = generate_direct_mine_row_expansion(
        lower, belt_anchor[1], include_power=False,
    )
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
        # Geography, not a bug: standing infrastructure owns every staging
        # strip. Defer (PendingSystemDeferred) so background gates such as the
        # fast-belt capacity ladder pause instead of killing the run.
        raise PendingSystemDeferred(
            f"No clear staging area found near the {ore} patch at {nearest_tile}"
        )
    preferred = (round(preferred_box[0]), round(preferred_box[1] + 5))
    area_is_clear = lambda lower, upper: live_base.area_clear(
        client, surface, lower, upper
    )
    footprint_has_resource = lambda centres: live_base.drill_footprints_have_resource(
        client, surface, ore, centres
    )
    # Reserve the corridor *west* of the initial row.  The east end is the
    # permanent haul head, so reserving east made phase 2 place drills on the
    # first outbound belt tile (live iron mine: x=25.5).
    selected = choose_mining_origin_with_reserve(
        preferred, patch_min, patch_max, row_drill_count, maximum_reserve,
        area_is_clear, footprint_has_resource,
    )
    if selected is None:
        raise PendingSystemDeferred(
            f"No clear position near the {ore} patch at {nearest_tile} "
            "puts every drill on ore -- the patch is saturated by standing "
            "infrastructure; expansion defers instead of bulldozing it"
        )
    selected_origin, reserved_columns = selected
    # `selected_origin` is the west edge of a fully verified strip.  Start at
    # its east edge so every future column extends west, away from the head.
    origin = (
        int(selected_origin[0] + 3 * reserved_columns),
        int(selected_origin[1]),
    )
    plan, output = direct_mine_plan(
        origin, row_drill_count, belt_type=belt_type, inserter_type=inserter_type,
        reserved_pair_columns=reserved_columns,
        output_side="east",
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


def complete_six_drill_prefix(positions: tuple[Point, ...]) -> tuple[Point, ...]:
    """Keep only complete six-drill modules from a buildable corridor.

    A two- or four-drill tail cannot advance a 6 -> 12 -> 24 capacity phase.
    Building it anyway created the observed 24 -> 26 mine while the refinery
    remained at six furnaces.  The remainder stays reserved for a later full
    module or the planner tries a parallel band.
    """
    complete = len(positions) - (len(positions) % 6)
    return positions[:complete]


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
    # Physical ceiling: every current smelt consumes at least as fast as a
    # drill produces, so a D-drill mine can never legitimately feed more than
    # D furnaces. Catalog-learned recipes (e.g. 'landfill') sometimes arrive
    # with optimistic numbers -- without this cap their rounding built 24
    # stone furnaces on a six-drill patch (live run 15).
    # Ceiling tolerates force-productivity gains (~1.5x) while still blocking
    # catalog-spec inflation (the 24-furnace run was a 4x on six drills).
    return max(1, min(math.ceil(ore_rate / furnace_input_rate), drill_count * 2))


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
    # Metal plate districts grow mine and smelter capacity on the same
    # six-machine phase boundary. Mining-productivity headroom may calculate
    # one extra furnace, but it must not turn a 12-drill phase into an 18- or
    # 24-furnace build. Other recipes keep their measured input-rate sizing.
    supported = (
        max(FURNACES_PER_MODULE, drill_count)
        if recipe in {"iron-plate", "copper-plate"}
        else required
    )
    return max(
        FURNACES_PER_MODULE,
        supported // FURNACES_PER_MODULE * FURNACES_PER_MODULE,
    )


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
    """Generate cardinal candidates, surveying the ore interfaces first.

    Starting near the actual collector output avoids wasting the first clear
    area probes at an unrelated edge of a long patch. This ordering does not
    choose the winning refinery site: `plan_local_extraction` evaluates every
    clear candidate by legal flow, total belt cost, and demand proximity.
    Live resource clearance remains authoritative over all of them.
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


def _refinery_site_score(
    ore_output: Point,
    reference_point: Point,
    feed: Point,
    output: Point,
) -> tuple[bool, bool, float, float, float]:
    """Rank legal refinery interfaces by total belt cost, then demand access."""
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
    return (
        input_tiles > mine_to_output,
        output_tiles > target_to_feed,
        input_tiles + output_tiles,
        output_tiles,
        input_tiles,
    )


def _smelter_layout_geometry(
    recipe: str, machine_count: int, belt_type: str, inserter_type: str,
    flow_direction: str = "east", vertical_mirror: bool = False,
) -> tuple[Rect, Point, Point]:
    """Exact modular bounds plus direct ore-belt and provider interfaces."""
    del belt_type, inserter_type
    if flow_direction != "east":
        raise ValueError("The approved modular refinery blueprint is eastbound")
    plan = generate_managed_refinery_plan(
        recipe, machine_count, vertical_mirror=vertical_mirror,
    )
    retained = list(actions(plan))
    extents = []
    for action in retained:
        size = ENTITY_FOOTPRINTS.get(action["entity"], 1)
        x, y = action["position"]["x"], action["position"]["y"]
        extents.append((x - size / 2, y - size / 2, x + size / 2, y + size / 2))
    interface = refinery_interfaces(
        machine_count, vertical_mirror=vertical_mirror,
    )
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


def _keeps_refinery_clearance(
    minimum: Point, maximum: Point,
    reserved_areas: tuple[tuple[Point, Point], ...],
) -> bool:
    """Keep the next refinery's full growth block out of another's corridor."""
    for reserved_min, reserved_max in reserved_areas:
        horizontally_clear = (
            maximum[0] + REFINERY_SITE_CLEARANCE_TILES <= reserved_min[0]
            or minimum[0] >= reserved_max[0] + REFINERY_SITE_CLEARANCE_TILES
        )
        vertically_clear = (
            maximum[1] + REFINERY_SITE_CLEARANCE_TILES <= reserved_min[1]
            or minimum[1] >= reserved_max[1] + REFINERY_SITE_CLEARANCE_TILES
        )
        if not horizontally_clear and not vertically_clear:
            return False
    return True


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
    excluded_drill_positions: tuple[Point, ...] = (),
    reserved_refinery_areas: tuple[tuple[Point, Point], ...] = (),
    owned_smelter_origin: Point | None = None,
    owned_smelter_vertical_mirror: bool = False,
    defer_pending_owned_refinery: bool = False,
    observe: Callable[[str], None] | None = None,
) -> LocalExtractionPlan:
    """Reconcile mining, then reserve an exact, bounded, off-ore smelter."""
    def surveyed(label: str, operation):
        started = time.monotonic()
        if observe is not None:
            observe(f"SURVEY START: {recipe} {label}")
        result = operation()
        if observe is not None:
            observe(
                f"SURVEY END: {recipe} {label} "
                f"elapsed={time.monotonic() - started:.2f}s"
            )
        return result

    ore = LINE_RECIPES[recipe]["ingredients"][0]
    mines = surveyed(
        "existing_mines",
        lambda: extraction_state.find_resource_mines(
            client, surface, force, ore, reference_point,
            excluded_drill_positions,
        ),
    )
    observed = mines[0] if mines else None
    if (
        observed is not None
        and (owned_smelter_origin is None or defer_pending_owned_refinery)
        and extraction_state.pending_plate_smelter(
            client, surface, force, ore, observed.output
        )
    ):
        raise PendingSystemDeferred(
            f"A pending off-ore smelter already exists near {observed.output}; "
            "refusing to submit a duplicate line"
        )
    system_before = surveyed(
        "drill_capacity",
        lambda: extraction_state.resource_drill_count(
            client, surface, force, ore, excluded_drill_positions,
        ),
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
    found = surveyed(
        "resource_patch",
        lambda: resource_patches.patch_for_extraction(
            client, surface, ore, survey_near,
            active=survey_mine is not None,
        ),
    )
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
    first_column_x: float | None = None
    if existing is not None:
        expansion_step = existing.growth_direction if existing.first_column_x is not None else existing.expansion_step
        first_column_x = existing.first_column_x
        row_drill_count = existing.drill_count
        row_state_kwargs = (
            {"first_column_x": existing.first_column_x}
            if existing.first_column_x is not None else {}
        )
        row_state = adjacent_mine_row_state(
            client, surface, (existing.output[0], existing.shared_belt_y),
            row_drill_count, expansion_step, **row_state_kwargs,
        )
        # A partially built row is recoverable: ghosts and powered/service
        # gaps must reach the normal remediation path instead of aborting the
        # run before coverage and power can finish the row. Count only the
        # observed row here; the next expansion pass will re-survey capacity.
        drill_count = row_drill_count * (
            2 if existing.pending or row_state == "complete" else 1
        )
        ore_output = existing.haul_head or existing.output
        if existing.first_column_x is None and expansion_step > 0:
            # East-flow collector: the haul head is the row's east end, not
            # the surveyed west anchor (which only books expansion columns).
            ore_output = (
                existing.output[0] + 4 + 3 * (row_drill_count - 1) + 2,
                existing.shared_belt_y,
            )
    elif mines:
        requested = phase_target - system_before
        requested += requested % 2
        positions = (
            extraction_capacity.phase_batch_positions(active, requested)
            if active is not None else ()
        )
        positions = complete_six_drill_prefix(
            buildable_batch_prefix(client, surface, ore, positions),
        )
        if positions:
            drill_xs = sorted({x for x, _y in positions})
            mine_origin = (drill_xs[0] - 1.5, active.shared_belt_y + 3.5)
            build_plan = generate_shared_belt_batch_expansion(
                drill_xs, active.shared_belt_y,
                belt_direction="east" if active.first_column_x is not None or active.expansion_step > 0 else "west",
            )
            drill_count = len(positions)
            ore_output = active.haul_head or active.output
            if active.first_column_x is None and active.expansion_step > 0:
                ore_output = (
                    active.output[0] + 4 + 3 * (active.drill_count + len(drill_xs) - 1) + 2,
                    active.shared_belt_y,
                )
            row_drill_count = len(drill_xs)
            expansion_step = active.growth_direction if active.first_column_x is not None else active.expansion_step
            first_column_x = min(drill_xs) if active.first_column_x is not None else None
            expansion_positions = positions
        else:
            # The owned district remains the source of truth when its first
            # collector is full or physically blocked. Reuse its straight
            # trunk and add a splitter-fed parallel band instead of opening a
            # duplicate mine or deferring the same demand forever.
            parallel_source = active or observed
            parallel_positions = extraction_capacity.parallel_phase_batch_positions(
                parallel_source, requested,
            )
            parallel_positions = complete_six_drill_prefix(
                buildable_batch_prefix(
                    client, surface, ore, parallel_positions,
                ),
            )
            if parallel_positions:
                drill_xs = sorted({x for x, _y in parallel_positions})
                parallel_belt_y = parallel_source.shared_belt_y + (
                    extraction_capacity.PARALLEL_BAND_PITCH
                )
                mine_origin = (drill_xs[0] - 1.5, parallel_belt_y + 3.5)
                try:
                    build_plan = generate_parallel_mining_row_expansion(
                        drill_xs, parallel_source.shared_belt_y,
                        belt_type=belt_type, parallel_belt_y=parallel_belt_y,
                    )
                except ValueError as error:
                    raise PendingSystemDeferred(
                        f"The parallel {ore} batch is surveyed but its merge "
                        f"cannot be built with {belt_type}: {error}"
                    ) from error
                drill_count = len(parallel_positions)
                ore_output = parallel_source.haul_head or parallel_source.output
                row_drill_count = len(drill_xs)
                expansion_step = parallel_source.growth_direction
                first_column_x = min(drill_xs)
                expansion_positions = parallel_positions
            else:
                raise PendingSystemDeferred(
                    f"The owned {ore} resource district at {parallel_source.output} "
                    "has no buildable straight or parallel collector batch for "
                    "the next six-drill checkpoint. Expansion stays in this "
                    "district instead of opening a duplicate mine or refinery."
                )
    else:
        mine_origin, drill_count, build_plan, ore_output = surveyed(
            "new_mine_site",
            lambda: _new_direct_mine(
                client, surface, ore, nearest_tile, patch_min, patch_max,
                machine_count, belt_type, inserter_type, belt_stock,
            ),
        )
        row_drill_count = drill_count // 2
        expansion_step = -1
        first_column_x = mine_origin[0] + 1.5
    furnace_count = planned_smelter_count_for_drills(
        recipe, drill_count, productivity,
    )
    geometries = {}
    for vertical_mirror in (False, True):
        _current_bounds, feed_offset, output_offset = _smelter_layout_geometry(
            recipe, furnace_count, belt_type, inserter_type, "east",
            vertical_mirror,
        )
        maximum_bounds, _maximum_feed, _maximum_output = _smelter_layout_geometry(
            recipe, REFINERY_GENERATION_1_CAPACITIES[-1], belt_type,
            inserter_type, "east", vertical_mirror,
        )
        geometries[vertical_mirror] = (
            maximum_bounds, feed_offset, output_offset,
        )
    default_bounds = geometries[False][0]
    footprint = (
        default_bounds.max_x - default_bounds.min_x,
        default_bounds.max_y - default_bounds.min_y,
    )
    smelter_origin = owned_smelter_origin
    smelter_flow_direction = "east"
    smelter_vertical_mirror = owned_smelter_vertical_mirror
    candidates: list[
        tuple[bool, bool, float, float, float, str, bool, Point]
    ] = []
    if smelter_origin is not None:
        owned_bounds, owned_feed_offset, _owned_output = geometries[
            smelter_vertical_mirror
        ]
        owned_min = (
            smelter_origin[0] + owned_bounds.min_x,
            smelter_origin[1] + owned_bounds.min_y,
        )
        owned_max = (
            smelter_origin[0] + owned_bounds.max_x,
            smelter_origin[1] + owned_bounds.max_y,
        )
        if not _keeps_refinery_clearance(
            owned_min, owned_max, reserved_refinery_areas,
        ):
            raise ValueError(
                f"Owned {recipe} refinery at {smelter_origin} conflicts with "
                "another reserved refinery district"
            )
        owned_feed = (
            smelter_origin[0] + owned_feed_offset[0],
            smelter_origin[1] + owned_feed_offset[1],
        )
        if (
            abs(owned_feed[0] - ore_output[0])
            + abs(owned_feed[1] - ore_output[1])
            > LOCAL_MODE_MAX_LINK_TILES
        ):
            raise ValueError(
                f"Owned {recipe} refinery at {smelter_origin} exceeds the "
                f"{LOCAL_MODE_MAX_LINK_TILES:.0f}-tile local-mode link limit"
            )
    anchors = (
        [] if smelter_origin is not None else smelter_search_anchors(
            patch_min, patch_max, footprint, reference_point, ore_output,
        )
    )
    aligned_anchors = {
        (anchor, vertical_mirror): _align_area_anchor(
            anchor, geometries[vertical_mirror][0],
        )
        for anchor in anchors for vertical_mirror in geometries
    }
    clear_areas = surveyed(
        "clear_refinery_sites",
        lambda: live_base.find_clear_areas(
            client, surface, list(dict.fromkeys(aligned_anchors.values())),
            footprint[0], footprint[1], max_radius=60.0,
            avoid_resources=True, resource_clearance=MINING_APRON_TILES,
        ),
    ) if anchors else {}
    for anchor in anchors:
        for vertical_mirror, (bounds, feed_offset, output_offset) in geometries.items():
            oriented_anchor = aligned_anchors[(anchor, vertical_mirror)]
            area_min = clear_areas.get(oriented_anchor)
            if area_min is None:
                continue
            candidate = (
                area_min[0] - bounds.min_x, area_min[1] - bounds.min_y,
            )
            candidate_min = (
                candidate[0] + bounds.min_x,
                candidate[1] + bounds.min_y,
            )
            candidate_max = (
                candidate[0] + bounds.max_x,
                candidate[1] + bounds.max_y,
            )
            if not _keeps_refinery_clearance(
                candidate_min, candidate_max, reserved_refinery_areas,
            ):
                continue
            feed = (
                candidate[0] + feed_offset[0], candidate[1] + feed_offset[1],
            )
            output = (
                candidate[0] + output_offset[0],
                candidate[1] + output_offset[1],
            )
            site_score = _refinery_site_score(
                ore_output, reference_point, feed, output,
            )
            input_tiles = site_score[-1]
            if input_tiles <= LOCAL_MODE_MAX_LINK_TILES:
                candidates.append((
                    *site_score,
                    "east",
                    vertical_mirror,
                    candidate,
                ))
    if candidates:
        (
            _input_wrong_way, _output_wrong_way, _total, _output, _input,
            smelter_flow_direction, smelter_vertical_mirror, smelter_origin,
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
        smelter_vertical_mirror=smelter_vertical_mirror,
        first_column_x=first_column_x,
        shared_belt_y=(
            (existing or active).shared_belt_y if (existing or active) is not None
            else ore_output[1]
        ),
        system_drill_target=(
            phase_target if not reuse_existing or not mines else system_before
        ),
        smelter_reserved_area=(
            (
                smelter_origin[0] + geometries[smelter_vertical_mirror][0].min_x,
                smelter_origin[1] + geometries[smelter_vertical_mirror][0].min_y,
            ),
            (
                smelter_origin[0] + geometries[smelter_vertical_mirror][0].max_x,
                smelter_origin[1] + geometries[smelter_vertical_mirror][0].max_y,
            ),
        ),
    )
