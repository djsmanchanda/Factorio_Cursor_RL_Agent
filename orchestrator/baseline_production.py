# Path: orchestrator/baseline_production.py
# Purpose: The standing prep set a base brings up before chasing a goal, and the extraction phase that draw implies.

from __future__ import annotations

import math

from orchestrator.extraction_capacity import EXTRACTION_DRILL_PHASES
from planners.recipe_data import LINE_RECIPES, MACHINE_SPEEDS

# Kitchen prep: bring the basics up before taking orders, rather than
# discovering mid-goal that nothing upstream exists and thrashing between
# half-built stages. Demand-driven scaling then grows these from a running
# start instead of from zero.
#
# Counts are machines, and deliberately small -- this is a standing baseline,
# not a target. iron-gear-wheel sits in the mall as two cells (both halves of
# one paired cell) until saturation promotes it to a dedicated line.
# steel-plate is deliberately NOT here. It is smelted, not assembled: a furnace
# takes its recipe from what is inserted, so an idle one reports no recipe and
# find_line can never count it. Prep saw zero however many it had built and
# placed another cell every pass -- twelve furnaces across six mall cells in one
# run. It also wants an iron-plate BELT, which does not exist this early. Steel
# belongs to a smelting stage, and is built on demand by whatever needs it.
BASELINE_MACHINES = {
    "iron-gear-wheel": 2,
    "copper-cable": 2,
    "electronic-circuit": 1,
}

# A plate the prep set consumes has to be mined and smelted; anything else in
# the set is produced by the set itself.
BASELINE_PLATES = ("iron-plate", "copper-plate")

ELECTRIC_DRILL_ITEMS_PER_SECOND = 0.5


def _machine_craft_rate(recipe: str) -> float:
    """Crafts per second one machine completes on `recipe`."""
    spec = LINE_RECIPES[recipe]
    return MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]


def baseline_plate_draw() -> dict[str, float]:
    """Items/second of each plate the whole prep set consumes when running.

    Every baseline machine's DIRECT plate consumption counts, including cable
    machines whose output feeds another member of the set: they draw copper
    whoever ends up using the cable.
    """
    draw = {plate: 0.0 for plate in BASELINE_PLATES}
    for recipe, machines in BASELINE_MACHINES.items():
        spec = LINE_RECIPES[recipe]
        crafts = machines * _machine_craft_rate(recipe)
        for ingredient, amount in zip(spec["ingredients"], spec["amounts"]):
            if ingredient in draw:
                draw[ingredient] += amount * crafts
    return draw


def baseline_smelter_count(plate: str) -> int:
    """Furnaces needed to keep up with the prep set's draw on `plate`."""
    spec = LINE_RECIPES[plate]
    per_furnace = _machine_craft_rate(plate) * spec.get("product_amount", 1)
    return math.ceil(baseline_plate_draw()[plate] / per_furnace)


def baseline_drill_phase(plate: str) -> int:
    """The mining phase whose drills feed the prep set's draw on `plate`.

    Sized on the base drill rate with no mining-productivity credit, so a fresh
    base is not planned around research it has not finished. Snapping to
    EXTRACTION_DRILL_PHASES keeps prep on the same ladder the rest of the
    system expands along.
    """
    drills = math.ceil(baseline_plate_draw()[plate] / ELECTRIC_DRILL_ITEMS_PER_SECOND)
    return next(
        (phase for phase in EXTRACTION_DRILL_PHASES if phase >= drills),
        EXTRACTION_DRILL_PHASES[-1],
    )


def baseline_build_order() -> tuple[str, ...]:
    """Prep recipes ordered so a line is built after whatever feeds it."""
    remaining = dict(BASELINE_MACHINES)
    ordered: list[str] = []
    while remaining:
        ready = [
            recipe for recipe in remaining
            if not any(
                ingredient in remaining
                for ingredient in LINE_RECIPES[recipe]["ingredients"]
            )
        ]
        if not ready:  # a cycle inside the prep set would be a definition error
            raise ValueError(
                f"Baseline prep set has a circular dependency among {sorted(remaining)}"
            )
        for recipe in sorted(ready):
            ordered.append(recipe)
            del remaining[recipe]
    return tuple(ordered)
