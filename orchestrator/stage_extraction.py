# Path: orchestrator/stage_extraction.py
# Purpose: Deterministic local extraction geometry that keeps mining egress on ore and smelting off the ore reservation.

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from orchestrator import extraction_state, live_base
from planners.local_layout_planner import LocalLayoutPlanner
from planners.plan_validation import ENTITY_FOOTPRINTS, actions
from planners.resource_layouts import generate_direct_mining_to_chest
from planners.recipe_data import LINE_RECIPES, MACHINE_SPEEDS
from planners.zoning_geometry import MINING_APRON_TILES, Rect
from tools.rcon_client import RconClient

Point = tuple[float, float]
ELECTRIC_DRILL_ITEMS_PER_SECOND = 0.5
LOCAL_MODE_MAX_LINK_TILES = 300.0


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


def mining_drill_positions(origin: Point, machine_count: int) -> list[Point]:
    """Centres for a south-facing drill row whose output is a local belt."""
    ox, oy = origin
    return [(ox + 1.5 + (3 * index), oy - 1.5) for index in range(machine_count)]


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
) -> tuple[Point, int] | None:
    """Choose a clear row whose every drill can mine, reducing capacity safely."""
    for count in range(machine_count, 0, -1):
        for origin in candidate_mining_origins(
            preferred, patch_min, patch_max, count
        ):
            ox, oy = origin
            if not area_is_clear(
                (ox, oy - 5), (ox + (count * 3) + 12, oy + 7)
            ):
                continue
            if footprint_has_resource(mining_drill_positions(origin, count)):
                return origin, count
    return None


def direct_mine_plan(
    origin: Point,
    machine_count: int,
    *,
    belt_type: str,
    inserter_type: str,
) -> tuple[dict, Point]:
    """Return an ore-only drill/egress plan and its collection chest."""
    ox, oy = origin
    drills = mining_drill_positions(origin, machine_count)
    output = (ox + machine_count * 3 + 2.5, oy + 0.5)
    return (
        generate_direct_mining_to_chest(
            drills, output, belt_type=belt_type, inserter_type=inserter_type
        ),
        output,
    )


def smelter_count_for_drills(
    recipe: str, drill_count: int, mining_productivity_bonus: float,
) -> int:
    """Size furnaces from drill and force rates instead of pairing machines."""
    if drill_count <= 0:
        raise ValueError("drill_count must be positive")
    if not math.isfinite(mining_productivity_bonus) or mining_productivity_bonus < 0:
        raise ValueError("mining_productivity_bonus must be finite and non-negative")
    spec = LINE_RECIPES[recipe]
    furnace_rate = (
        MACHINE_SPEEDS[spec["machine"]]
        * spec.get("product_amount", 1)
        / spec["craft_time"]
    )
    ore_rate = (
        drill_count
        * ELECTRIC_DRILL_ITEMS_PER_SECOND
        * (1.0 + mining_productivity_bonus)
    )
    return max(1, math.ceil(ore_rate / furnace_rate))


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
) -> list[Point]:
    """Cardinal search anchors; live resource clearance remains authoritative."""
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
    return sorted(
        anchors,
        key=lambda point: (
            math.dist(
                (point[0] + width / 2, point[1] + height / 2),
                reference_point,
            ),
            point[1],
            point[0],
        ),
    )


def _smelter_geometry(
    recipe: str, machine_count: int, belt_type: str, inserter_type: str,
) -> tuple[Rect, Point]:
    """Exact retained entity bounds and feed-chest offset at line origin zero."""
    plan = LocalLayoutPlanner().generate_line_layout(
        recipe, machine_count, 0, 0,
        belt_type=belt_type, inserter_type=inserter_type,
        feed_style="chest", terminal_collector=True,
    )
    retained = [
        action for action in actions(plan)
        if action.get("entity") != "electric-energy-interface"
    ]
    extents = []
    for action in retained:
        size = ENTITY_FOOTPRINTS.get(action["entity"], 1)
        x, y = action["position"]["x"], action["position"]["y"]
        extents.append((x - size / 2, y - size / 2, x + size / 2, y + size / 2))
    feed = next(
        action for action in retained if action["entity"] == "infinity-chest"
    )["position"]
    return (
        Rect(
            min(box[0] for box in extents), min(box[1] for box in extents),
            max(box[2] for box in extents), max(box[3] for box in extents),
        ),
        (feed["x"], feed["y"]),
    )


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
) -> LocalExtractionPlan:
    """Reconcile mining, then reserve an exact, bounded, off-ore smelter."""
    ore = LINE_RECIPES[recipe]["ingredients"][0]
    existing = extraction_state.find_resource_mine(
        client, surface, force, ore, reference_point
    )
    if existing is not None and existing.pending:
        raise ValueError(
            f"Matching {ore} extraction ghosts are still pending at "
            f"{existing.output}; refusing to submit a duplicate mine"
        )
    if existing is not None and extraction_state.pending_plate_smelter(
        client, surface, force, ore, existing.output
    ):
        raise ValueError(
            f"A pending off-ore smelter already exists near {existing.output}; "
            "refusing to submit a duplicate line"
        )
    survey_near = existing.output if existing is not None else reference_point
    found = live_base.nearest_resource(client, surface, ore, survey_near)
    if found is None:
        raise ValueError(
            f"No {ore} found within survey radius of {survey_near} -- "
            "cannot mine what isn't on the map"
        )
    nearest_tile, patch_min, patch_max = found
    productivity = extraction_state.mining_productivity_bonus(client, force)
    mine_origin: Point | None = None
    build_plan: dict | None = None
    if existing is not None:
        drill_count, ore_output = existing.drill_count, existing.output
    else:
        preferred_box = live_base.find_clear_area(
            client, surface, (nearest_tile[0] - 5, nearest_tile[1] - 5),
            machine_count * 3 + 12, 12,
        )
        if preferred_box is None:
            raise ValueError(
                f"No clear staging area found near the {ore} patch at {nearest_tile}"
            )
        preferred = (round(preferred_box[0]), round(preferred_box[1] + 5))
        selected = choose_mining_origin(
            preferred, patch_min, patch_max, machine_count,
            lambda lower, upper: live_base.area_clear(
                client, surface, lower, upper
            ),
            lambda centres: live_base.drill_footprints_have_resource(
                client, surface, ore, centres
            ),
        )
        if selected is None:
            raise ValueError(
                f"No clear position near the {ore} patch at {nearest_tile} "
                "puts every drill on ore"
            )
        selected_origin, drill_count = selected
        mine_origin = (int(selected_origin[0]), int(selected_origin[1]))
        build_plan, ore_output = direct_mine_plan(
            mine_origin, drill_count,
            belt_type=belt_type, inserter_type=inserter_type,
        )

    furnace_count = smelter_count_for_drills(recipe, drill_count, productivity)
    bounds, feed_offset = _smelter_geometry(
        recipe, furnace_count, belt_type, inserter_type
    )
    footprint = (bounds.max_x - bounds.min_x, bounds.max_y - bounds.min_y)
    smelter_origin = None
    for anchor in smelter_search_anchors(
        patch_min, patch_max, footprint, reference_point
    ):
        anchor = _align_area_anchor(anchor, bounds)
        area_min = live_base.find_clear_area(
            client, surface, anchor, *footprint,
            max_radius=60.0, avoid_resources=True,
            resource_clearance=MINING_APRON_TILES,
        )
        if area_min is None:
            continue
        candidate = (area_min[0] - bounds.min_x, area_min[1] - bounds.min_y)
        feed = (candidate[0] + feed_offset[0], candidate[1] + feed_offset[1])
        link_tiles = abs(feed[0] - ore_output[0]) + abs(feed[1] - ore_output[1])
        if link_tiles <= LOCAL_MODE_MAX_LINK_TILES:
            smelter_origin = candidate
            break
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
    )