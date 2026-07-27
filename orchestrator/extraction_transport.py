# Path: orchestrator/extraction_transport.py
# Purpose: Read-only belt preflight for local mine-to-smelter links before structural submission.

from __future__ import annotations

import math

from orchestrator import live_base
from orchestrator.stage_services import _BRIDGE_SURVEY_MARGIN, _DEFAULT_INSERTER
from orchestrator.stage_transport import (
    _clear_side,
    _transport_mode,
    _toward,
    choose_belt_tier,
)
from planners.belt_bridge import bridge_chest_to_chest, opposite
from planners.infrastructure_geometry import footprint_tile_indices
from planners.plan_validation import ENTITY_FOOTPRINTS
from tools.rcon_client import RconClient

Point = tuple[float, float]


def planned_footprint_tiles(plan: dict) -> set[tuple[int, int]]:
    """All tiles occupied by a future plan, including multi-tile bodies."""
    return set().union(*(
        footprint_tile_indices(
            (action["position"]["x"], action["position"]["y"]),
            ENTITY_FOOTPRINTS.get(action["entity"], 1),
        )
        for phase in plan["phases"] for action in phase["actions"]
        if "position" in action
    ))


def preflight_ingredient_transport(
    client: RconClient, surface: str, force: str, recipe: str, ingredient: str,
    source_position: Point, feed_position: Point, machine_count: int, *,
    max_belt_route_tiles: int,
    additional_blocked: set[tuple[int, int]] | None = None,
) -> tuple[list[dict], str] | None:
    """Return one exact legal belt route before structural submission."""
    if _transport_mode(recipe, ingredient, machine_count) == "logistic":
        return None
    span = int(abs(source_position[0] - feed_position[0])
               + abs(source_position[1] - feed_position[1])) + 4
    belt_type = choose_belt_tier(live_base.available_items(client, surface, force), span)
    blocked = live_base.occupied_tiles(
        client, surface,
        (min(source_position[0], feed_position[0]) - _BRIDGE_SURVEY_MARGIN,
         min(source_position[1], feed_position[1]) - _BRIDGE_SURVEY_MARGIN),
        (max(source_position[0], feed_position[0]) + _BRIDGE_SURVEY_MARGIN,
         max(source_position[1], feed_position[1]) + _BRIDGE_SURVEY_MARGIN),
    )
    blocked |= additional_blocked or set()
    blocked -= {
        (math.floor(source_position[0]), math.floor(source_position[1])),
        (math.floor(feed_position[0]), math.floor(feed_position[1])),
    }
    direction = _toward(source_position, feed_position)
    actions = bridge_chest_to_chest(
        source_position, feed_position,
        exit_direction=_clear_side(source_position, direction, blocked),
        entry_direction=_clear_side(feed_position, opposite(direction), blocked),
        belt_type=belt_type, inserter_type=_DEFAULT_INSERTER,
        blocked_tiles=blocked, max_route_tiles=max_belt_route_tiles,
    )
    return actions, belt_type
