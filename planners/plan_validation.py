# Path: planners/plan_validation.py
# Purpose: Shared schema, electric-only, collision, and production-source validation.

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from jsonschema import Draft7Validator

from planners.infrastructure_geometry import boxes_overlap
from planners.recipe_data import _reject_fuel_entities

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PLACEMENTS = {"place_entity", "place_ghost"}
INFINITY_ENTITIES = {"infinity-chest", "infinity-pipe"}
ENTITY_FOOTPRINTS = {
    "assembling-machine-2": 3,
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


def actions(plan: dict) -> Iterable[dict]:
    for phase in plan.get("phases", []):
        yield from phase.get("actions", [])


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


# A fluid source hands its output to a pipe sitting ON its connector tile, so
# the two legitimately share ground. Every other overlap is a fault.
_FLUID_SOURCE_ENTITIES = {"pumpjack", "offshore-pump"}


def is_verified_pumpjack_attachment(left: dict, right: dict) -> bool:
    """Whether these two are a fluid source and its own output pipe.

    A pumpjack's port is live-probed and must match exactly. An offshore pump
    declares its output tile in the plan that places it (`_fluid_resource_plan`
    refuses a plan whose pipe tiles do not include every supplied output), so
    the pipe standing there is by construction the pump's own connector rather
    than a stray placement.
    """
    source, pipe = (
        (left, right) if left["entity"] in _FLUID_SOURCE_ENTITIES else (right, left)
    )
    if source["entity"] not in _FLUID_SOURCE_ENTITIES or pipe["entity"] != "pipe":
        return False
    if source["entity"] == "offshore-pump":
        return True
    if source.get("direction") != "west":
        return False
    position = source["position"]
    connector = {"x": position["x"] - 1, "y": position["y"] + 1}
    return pipe["position"] == connector

def validate_no_collisions(named_plans: list[tuple[str, dict]]) -> None:
    placements = [
        (name, action)
        for name, plan in named_plans
        for action in actions(plan)
        if action.get("action_type") in _PLACEMENTS
    ]
    for index, (left_name, left) in enumerate(placements):
        left_position = (left["position"]["x"], left["position"]["y"])
        left_size = ENTITY_FOOTPRINTS.get(left["entity"], 1)
        for right_name, right in placements[index + 1:]:
            right_position = (right["position"]["x"], right["position"]["y"])
            right_size = ENTITY_FOOTPRINTS.get(right["entity"], 1)
            if boxes_overlap(left_position, left_size, right_position, right_size) and not is_verified_pumpjack_attachment(left, right):
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
            size = ENTITY_FOOTPRINTS.get(action["entity"], 1)
            x, y = action["position"]["x"], action["position"]["y"]
            left = int(x - size / 2)
            top = int(y - size / 2)
            occupied.update(
                (tile_x, tile_y)
                for tile_x in range(left, left + size)
                for tile_y in range(top, top + size)
            )
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
