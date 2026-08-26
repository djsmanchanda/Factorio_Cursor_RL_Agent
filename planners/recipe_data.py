# Path: planners/recipe_data.py
# Purpose: Deterministic recipe, throughput, geometry, and electric-only constants.

from __future__ import annotations

from typing import Dict, Mapping
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
    "steel-plate": {
        "machine": "electric-furnace", "ingredients": ["iron-plate"],
        "amounts": [5], "product_amount": 1, "craft_time": 16.0, "set_recipe": False,
    },
    "pipe": {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [1], "product_amount": 1, "craft_time": 0.5,
    },
    "engine-unit": {
        "machine": "assembling-machine-2",
        "ingredients": ["iron-gear-wheel", "pipe", "steel-plate"],
        "amounts": [1, 2, 1], "product_amount": 1, "craft_time": 10.0,
        "auxiliary_ingredient_index": 1,
    },
    "plastic-bar": {
        "machine": "chemical-plant", "ingredients": ["coal"], "amounts": [1],
        "fluid_ingredients": {"petroleum-gas": 20},
        "product_amount": 2, "craft_time": 1.0,
    },
    "sulfur": {
        "machine": "chemical-plant", "ingredients": [], "amounts": [],
        "fluid_ingredients": {"petroleum-gas": 30, "water": 30},
        "product_amount": 2, "craft_time": 1.0,
    },
    "battery": {
        "machine": "chemical-plant",
        "ingredients": ["copper-plate", "iron-plate"],
        "amounts": [1, 1],
        "fluid_ingredients": {"sulfuric-acid": 20},
        "product_amount": 1, "craft_time": 4.0,
    },
    "chemical-science-pack": {
        "machine": "assembling-machine-2",
        "ingredients": ["advanced-circuit", "engine-unit", "sulfur"],
        "amounts": [3, 2, 1], "product_amount": 2, "craft_time": 24.0,
        "auxiliary_ingredient_index": 1,
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
        "direct_extraction": True,
    },
    "copper-plate": {
        "machine": "electric-furnace", "ingredients": ["copper-ore"],
        "amounts": [1], "product_amount": 1, "craft_time": 3.2, "set_recipe": False,
        "direct_extraction": True,
    },
    "stone-brick": {
        "machine": "electric-furnace", "ingredients": ["stone"],
        "amounts": [2], "product_amount": 1, "craft_time": 3.2, "set_recipe": False,
        "direct_extraction": True,
    },
}

_GENERIC_ASSEMBLER_CATEGORIES = {
    "advanced-crafting", "basic-crafting", "crafting", "electronics", "pressing",
}
# Recipes allowed more than the line layout's three ingredients, because they
# are only ever built as MALL CELLS -- a requester-fed single machine, which
# does not care how many ingredients it asks for. A belt-fed line does: it
# carries two main lanes and one auxiliary, and refuses a fourth.
#
# assembling-machine-2 is here because every line in the system runs on one
# (LINE_RECIPES[*]["machine"]), and its recipe takes four ingredients -- so
# without this the agent could never build the machine it builds everything
# with, and depended on the player having stocked them by hand. Same for
# bulk-inserter, which the rate-driven selector reaches for on busy lines.
MALL_ONLY_RECIPES = {
    "chemical-plant", "oil-refinery", "pumpjack",
    "assembling-machine-2", "bulk-inserter", "flying-robot-frame",
}
MALL_ONLY_MAX_INGREDIENTS = 5
LINE_MAX_INGREDIENTS = 3


def install_catalog_line_recipes(catalog: Mapping) -> tuple[str, ...]:
    """Install live, deterministic solid recipes the current line can execute."""
    learned: list[str] = []
    for recipe in catalog.get("recipes", []):
        name = recipe.get("name")
        ingredients = recipe.get("ingredients", [])
        products = recipe.get("products", [])
        if (
            not isinstance(name, str)
            or name in LINE_RECIPES
            or not recipe.get("enabled")
            or not recipe.get("supported")
            or recipe.get("category") not in _GENERIC_ASSEMBLER_CATEGORIES
            or not (
                1 <= len(ingredients) <= LINE_MAX_INGREDIENTS
                or (name in MALL_ONLY_RECIPES
                    and len(ingredients) <= MALL_ONLY_MAX_INGREDIENTS)
            )
            or any(part.get("type") != "item" for part in ingredients)
            or len(products) != 1
            or products[0].get("type") != "item"
            or products[0].get("name") != name
        ):
            continue
        spec = {
            "machine": "assembling-machine-2",
            "ingredients": [part["name"] for part in ingredients],
            "amounts": [part["amount"] for part in ingredients],
            "product_amount": products[0]["amount"],
            "craft_time": recipe["energy_ticks"] / 60,
        }
        if len(ingredients) == LINE_MAX_INGREDIENTS:
            spec["auxiliary_ingredient_index"] = 1
        LINE_RECIPES[name] = spec
        learned.append(name)
    return tuple(sorted(learned))
# A drill REACHES further than it stands on. The electric drill occupies 3x3 but
# mines a 5x5 area; the big drill occupies 5x5 but mines 13x13. Where two ore
# types touch, a drill sited entirely on one of them can still reach the other,
# mine both, and jam its output with a second item -- so siting must clear the
# MINING AREA of foreign ore, not just the footprint.
DRILL_MINING_AREAS = {
    "electric-mining-drill": 5,
    "big-mining-drill": 13,
}


def drill_mining_reach(drill: str) -> float:
    """Half-width of `drill`'s mining area, in tiles from its centre."""
    if drill not in DRILL_MINING_AREAS:
        raise ValueError(
            f"No mining area known for {drill!r}; add it to DRILL_MINING_AREAS "
            "so its siting can be checked for foreign ore"
        )
    return DRILL_MINING_AREAS[drill] / 2.0


MACHINE_SPEEDS = {
    # Tiers 1 and 3 are here so a mall cell upgraded to a faster machine
    # recomputes its own input rate instead of failing an unknown lookup.
    "assembling-machine-1": 0.5, "assembling-machine-2": 0.75,
    "assembling-machine-3": 1.25, "electric-furnace": 2.0,
    "chemical-plant": 1.0, "oil-refinery": 1.0,
}

# Feeder inserter chest->belt throughput CEILINGS (items/s), not typical rates.
# Inserter swings are rotation-bound -- 180 degrees to load, 180 to unload -- so
# one feeder cannot supply a hungry line; feed points scale with per-ingredient
# demand as feeders = ceil(demand / rate).
#
# These are best case: fully researched hand capacity, unloading onto a belt
# fast enough not to hold the swing. docs/21 records that a slower belt worsens
# unload time, and that dependence is NOT modelled here -- a fast-inserter
# feeding a plain transport-belt measured well under this table's 4.0/s. Sizing
# off a ceiling understates how many feeders a line needs, and an underfed
# machine still reports as working, which is how a throttled cell once read as
# saturated and had five more equally throttled machines built beside it.
# UNMEASURED_FEEDER_RATES carries the derating that guards against that; see
# `feeder_rate`, which is what callers should use.
FEEDER_RATES = {
    "inserter": 1.4, "fast-inserter": 4.0, "bulk-inserter": 8.0, "stack-inserter": 12.0,
}

# Tiers whose ceiling above was never observed on this base. The plain inserter
# is arithmetic -- Factorio's base chest->belt rates put it at ~0.36x a fast one,
# applied to the researched fast-inserter figure -- and the two high-capacity
# tiers are prototype claims. Only fast-inserter has a live datapoint behind it.
UNMEASURED_FEEDER_RATES = frozenset({"inserter", "bulk-inserter", "stack-inserter"})

# What an unmeasured ceiling is discounted by before anything is sized from it.
# Erring low buys a cheaper-than-needed tier or one extra feed point; erring
# high buys a line that runs throttled and reports itself healthy. Removing this
# requires measuring the tiers on a live base, not a better guess -- the
# procedure is in docs/21.
UNMEASURED_RATE_DERATING = 0.75


def feeder_rate(inserter: str) -> float:
    """Throughput to size a feed array from: derated unless it was measured."""
    ceiling = FEEDER_RATES.get(inserter)
    if ceiling is None:
        raise ValueError(
            f"No feeder throughput known for {inserter!r}; add it to FEEDER_RATES"
        )
    if inserter in UNMEASURED_FEEDER_RATES:
        return ceiling * UNMEASURED_RATE_DERATING
    return ceiling

# Cheapest first. stack-inserter is deliberately absent: docs/21 records it as
# Gleba-only production, so it is never auto-selected on Nauvis even when the
# demand would justify it -- a caller wanting one must ask by name.
_AUTO_INSERTER_TIERS = ("inserter", "fast-inserter", "bulk-inserter")

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
INSERTER_TIERS = {"inserter", "fast-inserter", "bulk-inserter", "stack-inserter"}


def machine_ingredient_rates(recipe: str, machine_count: int = 1) -> list[float]:
    """Items per second of each ingredient a line of `machine_count` consumes."""
    spec = LINE_RECIPES[recipe]
    crafts = machine_count * MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]
    return [amount * crafts for amount in spec["amounts"]]


def machine_handled_rates(recipe: str, machine_count: int = 1) -> list[float]:
    """Every item flow one machine's inserters carry: each ingredient IN, and
    the product OUT.

    The output side is not always the quieter one -- copper-cable consumes 1.5
    plates/s but emits 3 cables/s -- and a line layout uses the same tier on
    both faces, so sizing on ingredients alone would under-provision the
    collector. A fluid-only recipe has no ingredient inserters at all and is
    sized purely by what it emits.
    """
    spec = LINE_RECIPES[recipe]
    crafts = machine_count * MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]
    return machine_ingredient_rates(recipe, machine_count) + [
        spec.get("product_amount", 1) * crafts
    ]


def inserter_tiers_covering(items_per_second: float) -> tuple[str, ...]:
    """Every auto-selectable tier that carries `items_per_second`, cheapest first.

    More than one tier is usually adequate, and which of them a caller can
    actually BUILD depends on what the base is holding. Returning the whole
    adequate set lets a caller downgrade its preference to an affordable tier
    instead of demanding a part it cannot make yet.
    """
    if items_per_second < 0:
        raise ValueError("Inserter demand cannot be negative")
    covering = tuple(
        tier for tier in _AUTO_INSERTER_TIERS if feeder_rate(tier) >= items_per_second
    )
    return covering or _AUTO_INSERTER_TIERS[-1:]


def inserter_for_demand(items_per_second: float) -> str:
    """Cheapest inserter tier that carries `items_per_second` on its own.

    An electric furnace smelting a plate every 1.6s moves well under one item
    per second, so the fast-inserter baseline docs/21 records was buying nothing
    there: it costs materials and, on a real base, an extra production chain.
    Feed points are sized separately by count (see _feeders_needed), so a
    cheaper tier here widens the feed array rather than starving the line.
    """
    return inserter_tiers_covering(items_per_second)[0]

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


# --- Assembler tier policy -------------------------------------------------
#
# User standard, 2026-08-02: while advanced circuits are scarce, PRODUCTION
# LINES run on assembling-machine-1 and assembling-machine-2 is reserved for
# MALL cells, until assembling-machine-3 can be made.
#
# The reason the two tiers are not interchangeable is ingredient slots, not
# speed. A mall cell builds construction items with four or five ingredients;
# a line carries at most three. Spending a tier-2 machine on a two-ingredient
# gear line uses a slot count the line cannot use, and each one costs a tier-1
# machine plus steel on top.
#
# Slot counts are read from the live game (see MACHINE_CAPABILITIES), never
# assumed: putting a three-ingredient recipe on a machine with two slots leaves
# a line that cannot set its own recipe.
# name -> {"ingredient_count": int, "crafting_speed": float, "categories": [...]}
# Empty until a live catalog is installed; every reader falls back to the
# recipe's declared machine, so an un-exported base behaves exactly as before.
MACHINE_CAPABILITIES: Dict[str, dict] = {}


# item -> stack size, from the live game. "Fill the chest" is only a real
# number if the stack size is the game's rather than a guess.
ITEM_STACK_SIZES: Dict[str, int] = {}


def install_catalog_stack_sizes(catalog: Mapping) -> int:
    """Record live item stack sizes; returns how many were learned."""
    for item, size in (catalog.get("stack_sizes") or {}).items():
        if isinstance(item, str) and isinstance(size, int) and size > 0:
            ITEM_STACK_SIZES[item] = size
    return len(ITEM_STACK_SIZES)


def install_catalog_machines(catalog: Mapping) -> tuple[str, ...]:
    """Record what each crafting machine can actually run, from the live game."""
    learned: list[str] = []
    for machine in catalog.get("machines", []):
        name = machine.get("name")
        if not isinstance(name, str):
            continue
        MACHINE_CAPABILITIES[name] = {
            "ingredient_count": machine.get("ingredient_count", 0),
            "crafting_speed": machine.get("crafting_speed", 0.0),
            "categories": tuple(machine.get("categories", ())),
        }
        if machine.get("crafting_speed"):
            MACHINE_SPEEDS.setdefault(name, machine["crafting_speed"])
        learned.append(name)
    return tuple(sorted(learned))


def machine_holds(machine: str, ingredient_count: int) -> bool:
    """Whether `machine` has enough ingredient slots for that many inputs.

    An unknown machine answers False. Slot counts are the whole reason the
    tiers are not interchangeable, so assuming one fits is the guess that
    strands a three-ingredient recipe on a two-slot machine -- the line builds,
    then cannot set its own recipe. `line_machine` handles the no-data case by
    keeping the recipe's declared machine instead.
    """
    capability = MACHINE_CAPABILITIES.get(machine)
    if capability is None:
        return False
    return capability["ingredient_count"] >= ingredient_count
