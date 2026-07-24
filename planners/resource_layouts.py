# Path: planners/resource_layouts.py
# Purpose: Deterministic real coal, crude-oil, and water source layouts from supplied coordinates.

from __future__ import annotations

from planners.infrastructure import POLE_SPECS
from planners.plan_validation import ENTITY_FOOTPRINTS, validate_build_plan
from planners.recipe_data import BELT_TIERS

# Pumpjack and offshore-pump connection geometry is intentionally not inferred.
# Callers supply exact entity and output pipe coordinates until live verification lands.

ROW_POLE = "medium-electric-pole"


def _row_pole_positions(
    anchor: tuple[float, float], entity: str, last_x: float,
) -> list[tuple[float, float]]:
    """Medium poles across the row at a pitch derived from the pole's own supply
    radius, so EVERY machine in the row sits inside a supply square.

    A single pole at the anchor only reaches `supply` tiles, which silently left
    the far end of a wide mining row unpowered (a three-drill coal row already
    overshoots it). The pitch is `2 * supply`, at which neighbouring supply
    squares meet, and stays inside the pole's `wire` reach so the row is still
    one connected chain. strip_local_power() drops the row's EEI and substation
    but keeps these poles, so they are what actually feeds the row in a factory.
    """
    supply = POLE_SPECS[ROW_POLE]["supply"]
    pitch = min(POLE_SPECS[ROW_POLE]["wire"], 2 * supply)
    # A `size`x`size` body centred at cx is inside the supply square while
    # |pole_x - cx| < supply + size/2.
    reach = supply + ENTITY_FOOTPRINTS.get(entity, 1) / 2
    positions = [tuple(anchor)]
    while positions[-1][0] + reach <= last_x:
        positions.append((positions[-1][0] + pitch, anchor[1]))
    return positions


def _power_scaffold(
    anchor: tuple[float, float], entity: str = "electric-mining-drill", last_x: float | None = None,
    include_row_poles: bool = True,
) -> list[dict]:
    x, y = anchor
    poles = _row_pole_positions(anchor, entity, last_x if last_x is not None else x)
    return [
        {"action_type": "place_entity", "entity": "electric-energy-interface",
         "position": {"x": x - 8, "y": y}},
        {"action_type": "place_entity", "entity": "substation",
         "position": {"x": x - 4, "y": y}},
    ] + ([
        {"action_type": "place_ghost", "entity": ROW_POLE,
         "position": {"x": pole_x, "y": pole_y}}
        for pole_x, pole_y in poles
    ] if include_row_poles else [])


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
        {"name": "coal_power", "actions": _power_scaffold(
            (first_x - 2, output_y - 4), "electric-mining-drill",
            max(x for x, _ in drill_positions),
        )},
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
    # A north-facing offshore pump is supplied from the land side, never from
    # the lake terrain it draws from. The retained row pole and managed
    # replacement substation therefore move north of the shoreline.
    power_anchor = (anchor[0] - 3, anchor[1] - 3) if entity == "offshore-pump" else (anchor[0] - 3, anchor[1] + 3)
    plan = {"phases": [
        {"name": f"{kind}_power", "actions": _power_scaffold(
            power_anchor, entity,
            max(site["position"][0] for site in sites),
            include_row_poles=entity != "offshore-pump",
        )},
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
