# Path: planners/plan_validation.py
# Purpose: Shared schema, electric-only, collision, and production-source validation.

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable

from jsonschema import Draft7Validator

from planners.recipe_data import _reject_fuel_entities

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PLACEMENTS = {"place_entity", "place_ghost"}
INFINITY_ENTITIES = {"infinity-chest", "infinity-pipe"}
ENTITY_FOOTPRINTS = {
    "assembling-machine-1": 3,
    "assembling-machine-2": 3,
    "assembling-machine-3": 3,
    "electric-furnace": 3,
    "electric-mining-drill": 3,
    "chemical-plant": 3,
    "oil-refinery": 5,
    "pumpjack": 3,
    "offshore-pump": 2,
    "roboport": 4,
    "substation": 2,
    "big-electric-pole": 2,
    "electric-energy-interface": 2,
    "storage-tank": 3,
}

# Splitters are the only rectangular placement used by the deterministic
# planners. Their long axis is perpendicular to belt flow.
ENTITY_RECTANGULAR_FOOTPRINTS = {
    "boiler": (3, 2),
    "steam-engine": (3, 5),
    "splitter": (2, 1),
    "fast-splitter": (2, 1),
    "express-splitter": (2, 1),
    "turbo-splitter": (2, 1),
}


def entity_footprint_dimensions(
    entity: str, direction: str | None = None,
) -> tuple[int, int]:
    """Return the grid footprint width/height for one oriented entity."""
    rectangular = ENTITY_RECTANGULAR_FOOTPRINTS.get(entity)
    if rectangular is not None:
        width, height = rectangular
        if direction in {"east", "west"}:
            width, height = height, width
        return width, height
    size = ENTITY_FOOTPRINTS.get(entity, 1)
    return int(size), int(size)


def entity_footprint_tiles(action: dict) -> frozenset[tuple[int, int]]:
    """Return every occupied tile for one cardinal grid placement."""
    position = action["position"]
    width, height = entity_footprint_dimensions(
        action["entity"], action.get("direction"),
    )
    left = math.floor(float(position["x"]) - width / 2)
    top = math.floor(float(position["y"]) - height / 2)
    return frozenset(
        (x, y)
        for x in range(left, left + width)
        for y in range(top, top + height)
    )


def actions(plan: dict) -> Iterable[dict]:
    for phase in plan.get("phases", []):
        yield from phase.get("actions", [])


def is_verified_offshore_attachment(left: dict, right: dict) -> bool:
    """Whether a pipe occupies the real land-side connector of a pump.

    The generic square footprint is intentionally conservative, but an
    offshore pump's legal pipe connector lies inside that coarse 2x2 tile
    box. Only this exact entity/direction/position relationship is exempt.
    """
    source, pipe = (
        (left, right) if left.get("entity") == "offshore-pump" else (right, left)
    )
    if source.get("entity") != "offshore-pump" or pipe.get("entity") != "pipe":
        return False
    offsets = {
        # The entity direction faces water; its pipe output is opposite.
        "north": (0, 1), "east": (-1, 0),
        "south": (0, -1), "west": (1, 0),
    }
    offset = offsets.get(source.get("direction", "north"))
    if offset is None:
        return False
    position = source["position"]
    return pipe.get("position") == {
        "x": position["x"] + offset[0],
        "y": position["y"] + offset[1],
    }


def validate_build_plan(plan: dict) -> None:
    schema = json.loads(
        (_REPO_ROOT / "schemas" / "build_plan.schema.json").read_text(encoding="utf-8")
    )
    errors = list(Draft7Validator(schema).iter_errors(plan))
    if errors:
        details = "\n".join(
            f"- {'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ValueError("BuildPlan validation FAILED:\n" + details)
    _reject_fuel_entities(plan)
    # A plan must not collide with ITSELF. validate_no_collisions existed but
    # was only ever called with separate plans, so a generator that put a 2x2
    # substation over its own feed chest produced a plan nothing rejected: the
    # line layout at (53,45) placed a substation at (49,47), covering the
    # infinity-chest at (49.5,47.5) and its inserter at (49.5,46.5).
    #
    # The live executor cannot catch it either -- its occupancy check matches
    # entities whose CENTRE is identical, which a 2x2 over a 1x1 never is.
    validate_no_collisions([("plan", plan)])


def validate_no_collisions(named_plans: list[tuple[str, dict]]) -> None:
    placements = [
        (name, action)
        for name, plan in named_plans
        for action in actions(plan)
        if action.get("action_type") in _PLACEMENTS
    ]
    for index, (left_name, left) in enumerate(placements):
        left_position = (left["position"]["x"], left["position"]["y"])
        left_tiles = entity_footprint_tiles(left)
        for right_name, right in placements[index + 1:]:
            right_position = (right["position"]["x"], right["position"]["y"])
            if (
                left_tiles & entity_footprint_tiles(right)
                and not is_verified_offshore_attachment(left, right)
            ):
                raise ValueError(
                    f"Plan collision: {left_name} {left['entity']} at {left_position} overlaps "
                    f"{right_name} {right['entity']} at {right_position}"
                )



def occupied_tile_indices(named_plans: list[tuple[str, dict]]) -> set[tuple[int, int]]:
    occupied: set[tuple[int, int]] = set()
    for _, plan in named_plans:
        for action in actions(plan):
            if action.get("action_type") not in _PLACEMENTS:
                continue
            occupied.update(entity_footprint_tiles(action))
    return occupied

def placement_keys(named_plans: list[tuple[str, dict]]) -> set[str]:
    return {
        json.dumps(action, sort_keys=True)
        for _, plan in named_plans
        for action in actions(plan)
        if action.get("action_type") in _PLACEMENTS
    }


def validate_placement_subset(
    earlier: list[tuple[str, dict]], ultimate: list[tuple[str, dict]],
) -> None:
    missing = placement_keys(earlier) - placement_keys(ultimate)
    if missing:
        raise ValueError(
            "Earlier phase rewrites placements absent from the ultimate block: "
            + sorted(missing)[0]
        )

def assert_no_production_infinity(named_plans: list[tuple[str, dict]]) -> None:
    offenders = sorted({
        f"{name}:{action['entity']}"
        for name, plan in named_plans
        for action in actions(plan)
        if action.get("entity") in INFINITY_ENTITIES
    })
    if offenders:
        raise ValueError("Production plans contain scripted sources: " + ", ".join(offenders))
