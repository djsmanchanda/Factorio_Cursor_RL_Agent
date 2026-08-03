# Path: orchestrator/extraction_transport.py
# Purpose: Read-only belt preflight for local mine-to-smelter and through-output links.

from __future__ import annotations

import math

from orchestrator import live_base
from orchestrator.stage_services import (
    _BRIDGE_SURVEY_MARGIN,
    _DEFAULT_INSERTER,
    StuckError,
)
from orchestrator.stage_transport import (
    _clear_side,
    _replace_existing_source_belt,
    _through_belt_source,
    _transport_mode,
    _toward,
    choose_belt_tier,
)
from planners.belt_bridge import (
    UNDERGROUND_REACH,
    bridge_belt_to_chest,
    bridge_belt_to_belt,
    bridge_chest_to_chest,
    opposite,
)
from planners.infrastructure_geometry import footprint_tile_indices
from planners.plan_validation import ENTITY_FOOTPRINTS
from tools.rcon_client import RconClient

Point = tuple[float, float]


def _direct_belt_entry(
    feed_position: Point, route_source: Point, preferred: str,
    blocked: set[tuple[int, int]], belt_direction: str,
) -> str:
    """Choose a clear approach that feeds with, never against, the input belt."""
    if belt_direction not in {"east", "west"}:
        raise ValueError("Direct refinery belt direction must be east or west")
    if preferred == belt_direction:
        preferred = "north" if route_source[1] < feed_position[1] else "south"
    allowed = [opposite(belt_direction), "north", "south"]
    ordered = [preferred, *allowed]
    vectors = {
        "east": (1, 0), "west": (-1, 0),
        "north": (0, -1), "south": (0, 1),
    }
    for direction in dict.fromkeys(ordered):
        vx, vy = vectors[direction]
        approach = {
            (math.floor(feed_position[0] + vx), math.floor(feed_position[1] + vy)),
            (math.floor(feed_position[0] + 2 * vx), math.floor(feed_position[1] + 2 * vy)),
        }
        if not approach & blocked:
            return direction
    raise ValueError(f"No clear direct-belt approach to {feed_position}")

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
    mode: str | None = None,
    destination_is_belt: bool = False,
    destination_belt_direction: str = "east",
) -> tuple[list[dict], str] | None:
    """Return one exact legal belt route before structural submission."""
    if (mode or _transport_mode(recipe, ingredient, machine_count)) == "logistic":
        return None
    belt_source = _through_belt_source(client, surface, ingredient, source_position)
    route_source = belt_source or source_position
    span = int(abs(route_source[0] - feed_position[0])
               + abs(route_source[1] - feed_position[1])) + 4
    belt_type = choose_belt_tier(live_base.available_items(client, surface, force), span)
    blocked = live_base.occupied_tiles(
        client, surface,
        (min(route_source[0], feed_position[0]) - _BRIDGE_SURVEY_MARGIN,
         min(route_source[1], feed_position[1]) - _BRIDGE_SURVEY_MARGIN),
        (max(route_source[0], feed_position[0]) + _BRIDGE_SURVEY_MARGIN,
         max(route_source[1], feed_position[1]) + _BRIDGE_SURVEY_MARGIN),
        ignore_names=(
            *tuple(UNDERGROUND_REACH),
            *(name.replace("transport-belt", "underground-belt")
              for name in UNDERGROUND_REACH),
        ),
    )
    blocked |= additional_blocked or set()
    blocked -= {
        (math.floor(route_source[0]), math.floor(route_source[1])),
        (math.floor(feed_position[0]), math.floor(feed_position[1])),
    }
    direction = _toward(route_source, feed_position)
    entry_direction = (
        _direct_belt_entry(
            feed_position, route_source, opposite(direction), blocked,
            destination_belt_direction,
        )
        if destination_is_belt
        else _clear_side(feed_position, opposite(direction), blocked)
    )
    if entry_direction is None:
        # Same rule as the build path: a plan known to collide is worse
        # than a stated failure, and the preflight exists to find exactly
        # this before anything is placed.
        raise StuckError(
            f"{ingredient} cannot reach its feed endpoint at {feed_position}: "
            "every side of it is already occupied."
        )
    if belt_source is not None:
        if destination_is_belt:
            exit_direction = None
            if ingredient in {"iron-ore", "copper-ore", "coal", "stone"}:
                if belt_source[0] > source_position[0]:
                    exit_direction = "west"
                elif belt_source[0] < source_position[0]:
                    exit_direction = "east"
            actions = bridge_belt_to_belt(
                belt_source, feed_position, entry_direction=entry_direction,
                belt_type=belt_type, blocked_tiles=blocked,
                max_route_tiles=max_belt_route_tiles,
                exit_direction=exit_direction,
            )
        else:
            actions = bridge_belt_to_chest(
                belt_source, feed_position, entry_direction=entry_direction,
                belt_type=belt_type, inserter_type=_DEFAULT_INSERTER,
                blocked_tiles=blocked, max_route_tiles=max_belt_route_tiles,
            )
        actions = _replace_existing_source_belt(
            client, surface, belt_source, actions,
        )
    else:
        exit_direction = _clear_side(source_position, direction, blocked)
        if exit_direction is None:
            # Unguarded, this handed None straight to the bridge as a direction.
            raise StuckError(
                f"{ingredient} cannot leave its source at {source_position}: "
                "every side of it is already occupied."
            )
        actions = bridge_chest_to_chest(
            source_position, feed_position,
            exit_direction=exit_direction,
            entry_direction=entry_direction,
            belt_type=belt_type, inserter_type=_DEFAULT_INSERTER,
            blocked_tiles=blocked, max_route_tiles=max_belt_route_tiles,
        )
    return actions, belt_type
