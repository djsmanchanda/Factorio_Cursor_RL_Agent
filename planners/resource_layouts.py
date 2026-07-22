# Path: planners/resource_layouts.py
# Purpose: Deterministic real coal, crude-oil, and water source layouts from supplied coordinates.

from __future__ import annotations

from planners.plan_validation import validate_build_plan
from planners.recipe_data import BELT_TIERS

# Pumpjack and offshore-pump connection geometry is intentionally not inferred.
# Callers supply exact entity and output pipe coordinates until live verification lands.


def _power_scaffold(anchor: tuple[float, float]) -> list[dict]:
    x, y = anchor
    return [
        {"action_type": "place_entity", "entity": "electric-energy-interface",
         "position": {"x": x - 8, "y": y}},
        {"action_type": "place_entity", "entity": "substation",
         "position": {"x": x - 4, "y": y}},
        {"action_type": "place_ghost", "entity": "medium-electric-pole",
         "position": {"x": x, "y": y}},
    ]


def generate_coal_mine(
    drill_positions: list[tuple[float, float]],
    output_y: float,
    output_x: float,
    belt_type: str = "express-transport-belt",
) -> dict:
    if not drill_positions:
        raise ValueError("Coal mine needs supplied drill coordinates")
    if belt_type not in BELT_TIERS:
        raise ValueError(f"Unknown belt tier: {belt_type}")
    if any(y + 2 != output_y for _, y in drill_positions):
        raise ValueError("Each south-facing drill must drop exactly two tiles onto output_y")
    first_x = min(x for x, _ in drill_positions)
    if output_x <= max(x for x, _ in drill_positions):
        raise ValueError("Coal output_x must lie east of every drill")
    belts = []
    x = first_x
    while x < output_x:
        belts.append({"action_type": "place_ghost", "entity": belt_type,
                      "position": {"x": x, "y": output_y}, "direction": "east"})
        x += 1
    drills = [
        {"action_type": "place_ghost", "entity": "electric-mining-drill",
         "position": {"x": x, "y": y}, "direction": "south"}
        for x, y in drill_positions
    ]
    plan = {"phases": [
        {"name": "coal_power", "actions": _power_scaffold((first_x - 2, output_y - 4))},
        {"name": "coal_mining", "actions": drills + belts},
    ]}
    validate_build_plan(plan)
    return plan


def _fluid_resource_plan(
    kind: str,
    entity: str,
    sites: list[dict],
    pipe_tiles: list[tuple[float, float]],
) -> dict:
    if not sites or not pipe_tiles:
        raise ValueError(f"{kind} source needs supplied entity and pipe coordinates")
    outputs = {tuple(site["output"]) for site in sites}
    if not outputs <= set(map(tuple, pipe_tiles)):
        raise ValueError(f"{kind} pipe tiles must include every supplied output coordinate")
    entities = [
        {"action_type": "place_ghost", "entity": entity,
         "position": {"x": site["position"][0], "y": site["position"][1]},
         "direction": site.get("direction", "north")}
        for site in sites
    ]
    pipes = [
        {"action_type": "place_ghost", "entity": "pipe",
         "position": {"x": x + 0.5, "y": y + 0.5}}
        for x, y in pipe_tiles
    ]
    anchor = tuple(sites[0]["position"])
    plan = {"phases": [
        {"name": f"{kind}_power", "actions": _power_scaffold((anchor[0] - 3, anchor[1] + 3))},
        {"name": f"{kind}_source", "actions": entities + pipes},
    ]}
    validate_build_plan(plan)
    return plan


def generate_pumpjack_source(sites: list[dict], pipe_tiles: list[tuple[float, float]]) -> dict:
    return _fluid_resource_plan("crude_oil", "pumpjack", sites, pipe_tiles)


def generate_offshore_pump_source(sites: list[dict], pipe_tiles: list[tuple[float, float]]) -> dict:
    return _fluid_resource_plan("water", "offshore-pump", sites, pipe_tiles)


def resource_fluid_segment(fluid: str, pipe_tiles: list[tuple[float, float]]) -> dict:
    return {"fluid": fluid, "separated_by_pump": False, "tiles": list(pipe_tiles)}
