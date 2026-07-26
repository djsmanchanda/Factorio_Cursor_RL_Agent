# Path: orchestrator/stage_transport.py
# Purpose: How a stage receives each ingredient -- belt versus logistic bots, which belt tier, and which side of a chest a link attaches to.

from __future__ import annotations

import math

from orchestrator.stage_services import (
    StuckError,
    _BOT_THROUGHPUT_LIMIT,
    _LOGISTIC_REQUEST,
)
from planners.belt_bridge import DIRECTION_VECTORS, UNDERGROUND_REACH
from planners.recipe_data import LINE_RECIPES, MACHINE_SPEEDS

Point = tuple[float, float]


def _toward(source: Point, dest: Point) -> str:
    dx, dy = dest[0] - source[0], dest[1] - source[1]
    if abs(dx) >= abs(dy):
        return "east" if dx > 0 else "west"
    return "south" if dy > 0 else "north"


def _clear_side(chest: Point, preferred: str, blocked: set[tuple[int, int]]) -> str:
    """Pick the side of `chest` a bridge can actually attach to.

    bridge_chest_to_chest puts an inserter one tile out and the belt's first
    tile two tiles out, so a side is usable only when BOTH are free. Facing the
    destination is merely the preference: a production stage sits on one side
    of its own output chest, so the direct side is frequently its own machine
    row -- observed live, where every attempt drove the belt back through the
    furnaces it had just built. Falls back to the preferred side when nothing
    is clear, so the caller still gets a plan and a real placement error
    rather than a silent no-op.
    """
    ordered = [preferred, *(d for d in DIRECTION_VECTORS if d != preferred)]
    for direction in ordered:
        vx, vy = DIRECTION_VECTORS[direction]
        tiles = {
            (math.floor(chest[0] + vx * step), math.floor(chest[1] + vy * step))
            for step in (1, 2)
        }
        if not (tiles & blocked):
            return direction
    return preferred


def _ingredient_demand(recipe: str, ingredient: str, machine_count: int) -> float:
    """Items/second of `ingredient` a `machine_count`-machine stage consumes."""
    spec = LINE_RECIPES[recipe]
    crafts_per_second = machine_count * MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]
    amount = spec["amounts"][spec["ingredients"].index(ingredient)]
    return amount * crafts_per_second


def _transport_mode(recipe: str, ingredient: str, machine_count: int) -> str:
    """"logistic" (requester chest, bots deliver) or "belt" (physical corridor).

    Small demand does not justify a belt run across the base; large demand
    cannot be served by bots at all. Chosen per ingredient, because one stage
    can easily need a trickle of one input and a torrent of another.
    """
    demand = _ingredient_demand(recipe, ingredient, machine_count)
    return "belt" if demand > _BOT_THROUGHPUT_LIMIT else "logistic"


def choose_belt_tier(stock: dict[str, int], needed: int) -> str:
    """Cheapest belt tier the base actually holds enough of.

    The default belt is only a preference. Picking a tier that is out of stock
    places ghosts nothing can build, so availability decides; ties break toward
    the slowest adequate tier, leaving faster belts for links that need them.
    """
    for tier in ("transport-belt", "fast-transport-belt", "express-transport-belt",
                  "turbo-transport-belt"):
        if stock.get(tier, 0) >= needed:
            return tier
    raise StuckError(
        f"No belt tier has {needed} in stock (have: "
        + ", ".join(f"{t}={stock.get(t, 0)}" for t in UNDERGROUND_REACH) + ")"
    )


def _swap_infinity_chests(
    plan: dict, modes: dict[str, str], *, request_count: int = _LOGISTIC_REQUEST,
) -> dict[str, Point]:
    """Turn every infinity-chest feeder into a real REQUESTER chest asking for
    its ingredient, so logistic bots deliver it.

    The sandbox pipeline used infinity chests (an infinite cheat source); on a
    real base the alternative is a physical belt from the upstream stage. A
    stage-to-stage belt costs ~50 belts and has to route around everything
    already built, while a requester costs one chest and no corridor at all --
    which matters because belts are a consumable the base has to produce.
    Returns {ingredient: chest_position}.
    """
    positions: dict[str, Point] = {}
    for phase in plan["phases"]:
        for action in phase["actions"]:
            if action.get("entity") == "infinity-chest":
                ingredient = action.pop("infinity_filter")
                if modes.get(ingredient, "logistic") == "logistic":
                    action["entity"] = "requester-chest"
                    action["logistic_request"] = {"name": ingredient, "count": request_count}
                else:
                    # Belt-fed: a plain chest the incoming belt unloads into.
                    action["entity"] = "steel-chest"
                positions[ingredient] = (action["position"]["x"], action["position"]["y"])
    return positions


def _publish_output_chest(plan: dict) -> None:
    """Make a stage's collection chest a passive provider, so its product is
    visible to the logistic network and can be requested by downstream stages
    (and by construction bots for building material)."""
    for phase in plan["phases"]:
        for action in phase["actions"]:
            if action.get("entity") == "steel-chest":
                action["entity"] = "passive-provider-chest"
