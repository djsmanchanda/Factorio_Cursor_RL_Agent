# Path: planners/rocket_silo_artifact.py | Purpose: Plan a catalog-backed rocket-silo ghost without runtime authority.
"""Offline silo placement candidate; the supplied capture is not authenticated."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from math import isfinite

from core.recipe_dag import validate_recipe_catalog
from planners.ghost_line_artifact import derive_ghost_artifact_from_runtime_catalog
from planners.recipe_data import BELT_TIERS


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate recipe catalog key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non-finite recipe catalog number")


def _recipe_ingredients(recipe_bytes: bytes, recipe_sha256: str, force: str) -> list[dict]:
    if (not isinstance(recipe_bytes, bytes) or not isinstance(recipe_sha256, str)
            or hashlib.sha256(recipe_bytes).hexdigest() != recipe_sha256):
        raise ValueError("rocket-part recipe catalog digest mismatch")
    try:
        catalog = json.loads(recipe_bytes, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        validate_recipe_catalog(catalog)
    except (ValueError, TypeError, UnicodeDecodeError) as error:
        raise ValueError("invalid rocket-part recipe catalog") from error
    recipes = [recipe for recipe in catalog["recipes"] if recipe["name"] == "rocket-part"]
    if (catalog["force"] != force or len(recipes) != 1 or recipes[0]["enabled"] is not True
            or recipes[0]["supported"] is not True
            or recipes[0]["products"] != [{"name": "rocket-part", "type": "item", "amount": 1}]):
        raise ValueError("rocket-part recipe is unavailable or has a mismatched force or product")
    ingredients = recipes[0]["ingredients"]
    if (not 1 <= len(ingredients) <= 3
            or any(part["type"] != "item" or type(part["amount"]) is not int or part["amount"] <= 0
                   for part in ingredients)
            or len({part["name"] for part in ingredients}) != len(ingredients)):
        raise ValueError("rocket-part recipe requires one to three unique whole-item ingredients")
    return sorted(ingredients, key=lambda part: part["name"])


def build_rocket_silo_artifact(
    *, origin: Sequence[int], placement_capture: Mapping[str, object],
    recipe_bytes: bytes | None = None, recipe_sha256: str | None = None,
    belt_type: str = "transport-belt", inserter_type: str = "fast-inserter",
) -> dict:
    """Place one standard 9x9 silo ghost at a caller-selected northwest tile.

    The captured prototype and placement item are checked but the capture,
    research, site, construction funding and feeding arrangements are not
    verified. A pinned recipe can add dedicated belt/inserter stubs; those
    remain unconnected and do not establish source capacity or actual pickup.
    No equipment is configured and no launch is requested.
    """
    if (not isinstance(origin, (list, tuple)) or len(origin) != 2
            or any(type(value) is not int or abs(value) > 1_000_000 for value in origin)):
        raise ValueError("silo origin must be two bounded integer tile coordinates")
    plan = {"phases": [{"name": "rocket_silo", "actions": [{
        "action_type": "place_ghost", "entity": "rocket-silo",
        "position": {"x": origin[0] + 4.5, "y": origin[1] + 4.5},
    }]}]}
    ports = []
    if recipe_bytes is not None or recipe_sha256 is not None:
        if (not isinstance(placement_capture, Mapping) or not isinstance(belt_type, str)
                or belt_type not in BELT_TIERS or not isinstance(inserter_type, str)
                or inserter_type not in {"inserter", "fast-inserter"}):
            raise ValueError("unsupported feeder belt or inserter")
        ingredients = _recipe_ingredients(recipe_bytes, recipe_sha256, placement_capture.get("force"))
        for index, ingredient in enumerate(ingredients):
            x = origin[0] + 1.5 + 3 * index
            belt_position = {"x": x, "y": origin[1] - 1.5}
            plan["phases"][0]["actions"].extend([
                {"action_type": "place_ghost", "entity": belt_type, "position": belt_position, "direction": "east"},
                {"action_type": "place_ghost", "entity": inserter_type,
                 "position": {"x": x, "y": origin[1] - 0.5}, "direction": "north"},
            ])
            ports.append({"item": ingredient["name"], "amount_per_part": ingredient["amount"],
                          "belt_position": dict(belt_position)})
    artifact = derive_ghost_artifact_from_runtime_catalog(plan, placement_capture=placement_capture)
    catalog = json.loads(placement_capture["catalog_bytes"])
    prototype = next(entity for entity in catalog["entities"] if entity["name"] == "rocket-silo")
    if (prototype.get("type") != "rocket-silo" or type(prototype.get("tile_width")) is not int
            or type(prototype.get("tile_height")) is not int
            or prototype["tile_width"] != 9 or prototype["tile_height"] != 9):
        raise ValueError("silo requires a captured standard 9x9 rocket-silo prototype")
    if ports:
        entities = {entity["name"]: entity for entity in catalog["entities"]}
        for name, expected_type in ((belt_type, "transport-belt"), (inserter_type, "inserter")):
            entity = entities[name]
            if (entity.get("type") != expected_type or type(entity.get("tile_width")) is not int
                    or type(entity.get("tile_height")) is not int
                    or entity["tile_width"] != 1 or entity["tile_height"] != 1):
                raise ValueError("feeder requires captured 1x1 belt and inserter prototypes")
    box = prototype.get("collision_box")
    if (not isinstance(box, dict) or set(box) != {"min_x", "min_y", "max_x", "max_y"}
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or (isinstance(value, float) and not isfinite(value))
                   for value in box.values())
            or not (-4.5 <= box["min_x"] < box["max_x"] <= 4.5)
            or not (-4.5 <= box["min_y"] < box["max_y"] <= 4.5)):
        raise ValueError("silo collision box must fit its captured 9x9 footprint")
    artifact["pending_checks"].extend(["silo_research", "silo_input_delivery"])
    artifact["unknowns"].update({
        "silo_research": "Research and availability of the silo item are not verified.",
        "silo_input_delivery": ("Feeder stubs are unconnected; belt contents, inserter reach, throughput, "
                                "power, and launch control remain unverified." if ports else
                                "No rocket-part inputs, inserters, power, or launch control are planned."),
    })
    if ports:
        artifact["pending_checks"].append("silo_recipe_provenance")
        artifact["unknowns"]["silo_recipe_provenance"] = (
            "Recipe and prototype captures are caller assertions; matching forces do not establish a common runtime."
        )
    artifact["planning_context"] = {
        "version": "planner-context-v1", "trust": "unverified", "data": {
            "kind": "rocket-silo-placement-v1", "capture_sha256": placement_capture["expected_sha256"],
            "surface": placement_capture["surface"], "force": placement_capture["force"],
            "origin": list(origin), "footprint": [9, 9], "collision_box": dict(box),
            "recipe_catalog_sha256": recipe_sha256, "input_ports": ports,
        },
    }
    return artifact
