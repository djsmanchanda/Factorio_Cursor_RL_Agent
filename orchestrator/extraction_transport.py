# Path: orchestrator/extraction_transport.py
# Purpose: Read-only belt preflight for local mine-to-smelter and through-output links.

from __future__ import annotations

from orchestrator.stage_transport import _plan_belt_transport, _transport_mode
from planners.infrastructure_geometry import footprint_tile_indices
from planners.plan_validation import ENTITY_FOOTPRINTS
from tools.rcon_client import RconClient

Point = tuple[float, float]


def planned_footprint_tiles(plan: dict) -> set[tuple[int, int]]:
    """All tiles occupied by future placements, excluding retirement actions."""
    return set().union(*(
        footprint_tile_indices(
            (action["position"]["x"], action["position"]["y"]),
            ENTITY_FOOTPRINTS.get(action["entity"], 1),
        )
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("action_type") in {"place_entity", "place_ghost"}
    ))


def planned_entity_count(plan: dict, entity: str) -> int:
    """Count ghost placements of one entity already reserved by a plan."""
    return sum(
        1
        for phase in plan["phases"]
        for action in phase["actions"]
        if action.get("action_type") == "place_ghost"
        and action.get("entity") == entity
    )


def preflight_ingredient_transport(
    client: RconClient, surface: str, force: str, recipe: str, ingredient: str,
    source_position: Point, feed_position: Point, machine_count: int, *,
    max_belt_route_tiles: int,
    additional_blocked: set[tuple[int, int]] | None = None,
    mode: str | None = None,
    destination_is_belt: bool = False,
    reserved_transport_belts: int = 0,
    planned_belt_source: Point | None = None,
    destination_belt_direction: str = "east",
    through_flow_direction: str | None = None,
    required_belt_type: str | None = None,
    defer_required_tier_affordability: bool = False,
) -> tuple[list[dict], str] | None:
    """Return one exact affordable route from the shared belt planner."""
    if (mode or _transport_mode(recipe, ingredient, machine_count)) == "logistic":
        return None
    actions, belt_type, _reused = _plan_belt_transport(
        client, surface, force, ingredient, source_position, feed_position,
        reuse_existing=True, max_belt_route_tiles=max_belt_route_tiles,
        additional_blocked=additional_blocked,
        destination_is_belt=destination_is_belt,
        reserved_transport_belts=reserved_transport_belts,
        planned_belt_source=planned_belt_source,
        destination_belt_direction=destination_belt_direction,
        through_flow_direction=through_flow_direction,
        required_belt_type=required_belt_type,
        defer_required_tier_affordability=defer_required_tier_affordability,
    )
    return actions, belt_type
