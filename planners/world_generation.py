# Path: planners/world_generation.py
# Purpose: Define and validate the deterministic bounded planner-world contract.

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from jsonschema import Draft7Validator

WORLD_SPEC_VERSION = "1.0.0"
WORLD_SEED = 17290718
_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = _ROOT / "schemas" / "world_spec.schema.json"

_PATCHES = (
    ("iron_north", "iron-ore", 0, -225, 20, -215),
    ("copper_north", "copper-ore", 40, -195, 80, -185),
    ("copper_mid", "copper-ore", 0, -105, 20, -95),
    ("iron_acid", "iron-ore", 40, 90, 55, 100),
    ("copper_south", "copper-ore", -40, 110, 15, 120),
    ("iron_south", "iron-ore", 40, 135, 70, 145),
    ("coal", "coal", 18, -2, 30, 4),
    ("stone_west", "stone", -205, 35, -185, 55),
)

_MATERIALS = {
    "assembling-machine-2": 200,
    "big-electric-pole": 200,
    "chemical-plant": 80,
    "electric-furnace": 100,
    "electric-mining-drill": 100,
    "express-transport-belt": 4000,
    "express-underground-belt": 500,
    "long-handed-inserter": 200,
    "medium-electric-pole": 500,
    "offshore-pump": 10,
    "oil-refinery": 20,
    "passive-provider-chest": 20,
    "pipe": 2000,
    "pipe-to-ground": 400,
    "pumpjack": 20,
    "roboport": 100,
    "stack-inserter": 500,
    "steel-chest": 100,
    "storage-chest": 20,
    "substation": 300,
}


def _drills(start_x: float, y: float, count: int) -> list[list[float]]:
    return [[start_x + index * 3, y] for index in range(count)]


def _electronics_world() -> dict:
    ore_patches = [
        {"id": patch[0], "item": patch[1], "x1": patch[2], "y1": patch[3],
         "x2": patch[4], "y2": patch[5]}
        for patch in _PATCHES if patch[1] != "stone"
    ]
    return {
        "version": "1.0.0",
        "surface": "planner-sandbox",
        "survey_tick": 0,
        "map_bounds": {"x1": -250, "y1": -250, "x2": 250, "y2": 250},
        "ore_patches": ore_patches,
        "ore_lines": [
            {"stage": "iron_plate", "resource": "iron-ore", "patch_id": "iron_north", "capacity_per_second": 3.0, "drill_positions": _drills(1.5, -221.5, 6)},
            {"stage": "copper_plate_ec", "resource": "copper-ore", "patch_id": "copper_north", "capacity_per_second": 6.0, "drill_positions": _drills(41.5, -191.5, 12)},
            {"stage": "copper_plate_ac", "resource": "copper-ore", "patch_id": "copper_mid", "capacity_per_second": 3.0, "drill_positions": _drills(1.5, -101.5, 6)},
            {"stage": "iron_plate_acid", "resource": "iron-ore", "patch_id": "iron_acid", "capacity_per_second": 2.0, "drill_positions": _drills(41.5, 93.5, 4)},
            {"stage": "copper_plate_pu", "resource": "copper-ore", "patch_id": "copper_south", "capacity_per_second": 9.0, "drill_positions": _drills(-38.5, 113.5, 18)},
            {"stage": "iron_plate_pu", "resource": "iron-ore", "patch_id": "iron_south", "capacity_per_second": 4.5, "drill_positions": _drills(41.5, 138.5, 9)},
        ],
        "coal_patch_id": "coal",
        "coal_drill_positions": _drills(20.5, 0.5, 3),
        "coal_output_y": 2.5,
        "coal_output_x": 40.5,
        "coal_capacity_per_second": 1.5,
        "pumpjack_sites": [{"position": [20.5, -43.5], "output": [19, -46], "resource": "crude-oil", "capacity_per_second": 60.0, "direction": "north"}],
        "crude_pipe_tiles": [[19, -46]],
        "offshore_pump_sites": [{"position": [20.5, 83.5], "output": [20, 81], "resource": "water", "capacity_per_second": 1200.0, "direction": "north"}],
        "water_pipe_tiles": [[20, 81]],
    }


def default_world_payload() -> dict:
    """Return a fresh canonical world payload; callers may serialize it safely."""
    electronics = _electronics_world()
    payload = {
        "version": WORLD_SPEC_VERSION,
        "surface": "planner-sandbox",
        "seed": WORLD_SEED,
        "bounds": {"x_min": -250, "y_min": -250, "x_max_exclusive": 250,
                   "y_max_exclusive": 250, "width": 500, "height": 500},
        "map_generation": {"autoplace_enabled": False, "generate_with_lab_tiles": True},
        "resource_patches": [
            {"id": patch[0], "resource": patch[1], "x1": patch[2], "y1": patch[3],
             "x2": patch[4], "y2": patch[5], "amount_per_tile": 1_000_000}
            for patch in _PATCHES
        ],
        "crude_oil_spots": [{"id": "crude_primary", "position": [20.5, -43.5], "amount": 30_000_000}],
        "water_lake": {"x1": 14, "y1": 84, "x2": 27, "y2": 96, "tile": "water",
                       "offshore_edge_candidates": [{"position": [20.5, 83.5], "output": [20, 81], "direction": "north"}]},
        "electronics_world": electronics,
        "starter_kit": {
            "power_source": {"entity": "electric-energy-interface", "position": [-160, -160]},
            "roboport_hub": {"entity": "roboport", "position": [-128, -128], "network_policy": "single-connected"},
            "bots_per_roboport": 50,
            "construction_materials": dict(_MATERIALS),
            "production_ingredients": {},
        },
    }
    validate_world_payload(payload)
    return deepcopy(payload)


def validate_world_payload(payload: Mapping) -> None:
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    electronics_schema = json.loads(
        (_ROOT / "schemas" / "electronics_world_spec.schema.json").read_text(encoding="utf-8")
    )
    schema["properties"]["electronics_world"] = electronics_schema
    errors = sorted(
        Draft7Validator(schema).iter_errors(dict(payload)), key=lambda error: list(error.path)
    )
    if errors:
        detail = "; ".join(f"{'/'.join(map(str, error.path)) or '<root>'}: {error.message}" for error in errors)
        raise ValueError(f"PlannerWorldSpec validation failed: {detail}")
    _validate_geometry(payload)


def _validate_geometry(payload: Mapping) -> None:
    rectangles = []
    ids = set()
    for patch in payload["resource_patches"]:
        if patch["id"] in ids or patch["x1"] > patch["x2"] or patch["y1"] > patch["y2"]:
            raise ValueError("Resource patches need unique ids and ordered bounds")
        ids.add(patch["id"])
        rect = (patch["x1"], patch["y1"], patch["x2"], patch["y2"])
        if any(not (rect[2] < other[0] or other[2] < rect[0] or rect[3] < other[1] or other[3] < rect[1]) for other in rectangles):
            raise ValueError(f"Resource patch {patch['id']} overlaps another patch")
        rectangles.append(rect)
    if {patch["resource"] for patch in payload["resource_patches"]} != {"iron-ore", "copper-ore", "coal", "stone"}:
        raise ValueError("World must contain iron, copper, coal, and stone")
    lake = payload["water_lake"]
    if lake["x1"] > lake["x2"] or lake["y1"] > lake["y2"]:
        raise ValueError("Water lake bounds are inverted")
    lake_rect = (lake["x1"], lake["y1"], lake["x2"], lake["y2"])
    if any(not (lake_rect[2] < rect[0] or rect[2] < lake_rect[0]
                   or lake_rect[3] < rect[1] or rect[3] < lake_rect[1])
           for rect in rectangles):
        raise ValueError("Water lake overlaps a resource patch")
    for site in lake["offshore_edge_candidates"]:
        x, y = site["position"]
        if not (site["direction"] == "north" and lake["x1"] <= x <= lake["x2"]
                and y == lake["y1"] - 0.5):
            raise ValueError("North-facing offshore pump candidate must sit on the north lake edge")

    starter = payload["starter_kit"]
    if starter["power_source"] != {
        "entity": "electric-energy-interface", "position": [-160, -160],
    } or starter["roboport_hub"] != {
        "entity": "roboport", "position": [-128, -128],
        "network_policy": "single-connected",
    }:
        raise ValueError("Starter kit must use the canonical source and roboport hub")
    if not set(starter["construction_materials"]) <= set(_MATERIALS):
        raise ValueError("Starter kit contains a non-construction material")

    electronics = payload["electronics_world"]
    patch_lookup = {patch["id"]: patch for patch in payload["resource_patches"]}
    for surveyed in electronics["ore_patches"]:
        generated = patch_lookup.get(surveyed["id"])
        expected = None if generated is None else {
            "id": generated["id"], "item": generated["resource"],
            "x1": generated["x1"], "y1": generated["y1"],
            "x2": generated["x2"], "y2": generated["y2"],
        }
        if surveyed != expected:
            raise ValueError(f"Electronics patch {surveyed['id']} differs from generated world")
    crude_positions = {tuple(spot["position"]) for spot in payload["crude_oil_spots"]}
    if any(tuple(site["position"]) not in crude_positions
           for site in electronics["pumpjack_sites"]):
        raise ValueError("Electronics pumpjack has no generated crude-oil spot")
    lake_sites = {
        (tuple(site["position"]), tuple(site["output"]), site["direction"])
        for site in lake["offshore_edge_candidates"]
    }
    if any((tuple(site["position"]), tuple(site["output"]), site.get("direction", "north"))
           not in lake_sites for site in electronics["offshore_pump_sites"]):
        raise ValueError("Electronics offshore pump has no generated lake edge")


@dataclass(frozen=True)
class PlannerWorldSpec:
    payload: dict

    @classmethod
    def canonical(cls) -> "PlannerWorldSpec":
        return cls(default_world_payload())

    def electronics_payload(self) -> dict:
        return deepcopy(self.payload["electronics_world"])

    def to_json(self) -> str:
        return json.dumps(self.payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> "PlannerWorldSpec":
        payload = json.loads(value)
        validate_world_payload(payload)
        return cls(payload)
