# Path: planners/recipe_data.py
# Purpose: Deterministic recipe, throughput, geometry, and electric-only constants.

from __future__ import annotations

from typing import Dict
LINE_RECIPES: Dict[str, dict] = {
    "iron-gear-wheel": {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [2], "product_amount": 1, "craft_time": 0.5,
    },
    "copper-cable": {
        "machine": "assembling-machine-2", "ingredients": ["copper-plate"],
        "amounts": [1], "product_amount": 2, "craft_time": 0.5,
    },
    "iron-stick": {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [1], "product_amount": 2, "craft_time": 0.5,
    },
    "electronic-circuit": {
        "machine": "assembling-machine-2", "ingredients": ["copper-cable", "iron-plate"],
        "amounts": [3, 1], "product_amount": 1, "craft_time": 0.5,
    },
    "automation-science-pack": {
        "machine": "assembling-machine-2", "ingredients": ["copper-plate", "iron-gear-wheel"],
        "amounts": [1, 1], "product_amount": 1, "craft_time": 5.0,
    },
    # Logistic-science chain. Every field below is transcribed from the live
    # player-force export (tests/fixtures/player_recipe_catalog.json) rather
    # than from memory -- Factorio 2.0 Space Age recipes differ from 1.1, and
    # tests/test_recipe_catalog_contract.py re-checks them against that export.
    # transport-belt's category is "pressing" and electronic-circuit's is
    # "electronics"; assembling-machine-2 supports both (verified live).
    "transport-belt": {
        "machine": "assembling-machine-2", "ingredients": ["iron-gear-wheel", "iron-plate"],
        "amounts": [1, 1], "product_amount": 2, "craft_time": 0.5,
    },
    "inserter": {
        "machine": "assembling-machine-2",
        "ingredients": ["electronic-circuit", "iron-gear-wheel", "iron-plate"],
        "amounts": [1, 1, 1], "product_amount": 1, "craft_time": 0.5,
        # Three solid ingredients: the middle one rides the auxiliary corridor,
        # which generate_line_layout only supports at index 1.
        "auxiliary_ingredient_index": 1,
    },
    "logistic-science-pack": {
        "machine": "assembling-machine-2", "ingredients": ["inserter", "transport-belt"],
        "amounts": [1, 1], "product_amount": 1, "craft_time": 6.0,
    },
    "advanced-circuit": {
        "machine": "assembling-machine-2",
        "ingredients": ["plastic-bar", "copper-cable", "electronic-circuit"],
        "amounts": [2, 4, 2], "product_amount": 1, "craft_time": 6.0,
        "auxiliary_ingredient_index": 1,
    },
    "processing-unit": {
        "machine": "assembling-machine-2",
        "ingredients": ["electronic-circuit", "advanced-circuit"],
        "amounts": [20, 2], "fluid_ingredients": {"sulfuric-acid": 5},
        "product_amount": 1, "craft_time": 10.0,
    },
    "iron-plate": {
        "machine": "electric-furnace", "ingredients": ["iron-ore"],
        "amounts": [1], "product_amount": 1, "craft_time": 3.2, "set_recipe": False,
    },
    "copper-plate": {
        "machine": "electric-furnace", "ingredients": ["copper-ore"],
        "amounts": [1], "product_amount": 1, "craft_time": 3.2, "set_recipe": False,
    },
}

MACHINE_SPEEDS = {"assembling-machine-2": 0.75, "electric-furnace": 2.0}

# Feeder inserter chest->belt throughput estimates (items/s, research-boosted;
# docs/21). Inserter swings are rotation-bound: 180 degrees to load, 180 to
# unload, so one feeder cannot supply a hungry line - feed points scale with
# per-ingredient demand: feeders = ceil(demand / rate).
FEEDER_RATES = {"fast-inserter": 4.0, "bulk-inserter": 8.0, "stack-inserter": 12.0}

# Capacity headroom (user standard, 2026-07-18): provision feed capacity with
# a 20-25% buffer over raw demand so supply never runs at the ragged edge.
FEED_HEADROOM = 1.25

# Vertical pitch between stacked lines: 8 rows of layout plus 8 reserved for
# expansion, so lines can grow east (more machines) and south (more lines).
LINE_PITCH_Y = 16

# Logistics tiers (docs/21): higher tiers raise line throughput. Turbo belts
# and stack inserters have off-planet sourcing constraints in real supply
# chains; on the sandbox they arrive via scaffolding.
BELT_TIERS = {"transport-belt": 15, "fast-transport-belt": 30, "express-transport-belt": 45, "turbo-transport-belt": 60}
INSERTER_TIERS = {"fast-inserter", "bulk-inserter", "stack-inserter"}

# Sideload feeder geometry (feed_style="sideload"). Instead of chest+inserter
# pairs placed directly on the input belt, each ingredient rides a dedicated
# feeder BELT column that T-junctions into the input belt (belt buffering
# sustains far higher throughput than chest+inserter feeding). Both feeder
# columns sit WEST of x=0 so they never touch machines (x>=0), input inserters
# (x=3i+1.5) or poles (x=6j+0.5). Ingredient 0 approaches from the north side
# (belt runs south into the belt's north edge); ingredient 1 from the south
# side (belt runs north into the south edge). Loading chest+inserter pairs sit
# two/one tiles further west of each feeder belt.
SIDELOAD_NORTH_COL = -2  # tile column of the north feeder belt (ingredient 0)
SIDELOAD_SOUTH_COL = -3  # tile column of the south feeder belt (ingredient 1)
# feed_style="chained": one or more ingredients arrive over a chain-link belt
# (generate_chain_link) instead of local scaffolding. Chained ingredients emit
# NO infinity chest / feeder at all; the input belt still extends west to host
# the chain junction tile, and non-chained ingredients keep their chest feeders.
FEED_STYLES = {"chest", "sideload", "chained"}

# generate_chain_link junction modes. "head_on" (default, legacy): connector
# corners east straight into the consumer input-belt west end. "north"/"south":
# the connector sideloads one lane of the consumer input belt from that edge, so
# two producers can feed one consumer on opposite lanes (verified: north entry
# fills lane 1, south entry lane 2).
CHAIN_JUNCTION_SIDES = {"head_on", "north", "south"}

# Chained-line geometry (all columns are tile indices relative to the consumer
# origin, all WEST of x=0 so nothing touches machines/inserters/poles):
#   -1  north junction: connector descends here, its last tile at row -1 faces
#       south and sideloads the input belt tile (-1, 0) onto the north lane.
#   -2  south junction: connector's last tile at row +1 faces north and
#       sideloads (-2, 0) onto the south lane.
#   -4  south approach: the south connector descends here (west of the input
#       belt so it can pass row 0 safely), then runs east along row +1.
# Output inserters drop onto one lane only, so a chain delivers a single lane -
# hence two producers must enter on opposite sides to stay separated.
CHAINED_BELT_WEST = -3
CHAIN_NORTH_JUNCTION_COL = -1
CHAIN_SOUTH_JUNCTION_COL = -2
CHAIN_SOUTH_APPROACH_COL = -4

# Poles connect only within the SHORTER of the two wire reaches, so a
# substation must sit within a medium pole's 9 tiles of the line's first pole
# (x=0.5) or the line has no power at all. -7 clears the connector columns
# (-4 approach, -2/-1 junctions) and the south connector's row +1 run.
MEDIUM_POLE_WIRE_REACH = 9.0
CHAINED_SUBSTATION_X = -7.0

# Invariant (docs/20 §12): all equipment is electric. Plans containing any of
# these fuel-burning entities are rejected at validation time.
FORBIDDEN_FUEL_ENTITIES = {
    "burner-mining-drill",
    "stone-furnace",
    "steel-furnace",
    "burner-inserter",
    "boiler",
    "steam-engine",
}


def _reject_fuel_entities(plan: dict) -> None:
    offenders = sorted({
        action["entity"]
        for phase in plan.get("phases", [])
        for action in phase.get("actions", [])
        if action.get("entity") in FORBIDDEN_FUEL_ENTITIES
    })
    if offenders:
        raise ValueError(f"Electric-only invariant violated by: {', '.join(offenders)}")

MACHINE_WIDTH = 3  # tiles; assembling machines are 3x3
