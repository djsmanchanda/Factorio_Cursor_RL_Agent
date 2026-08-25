# Path: orchestrator/baseline_production.py
# Purpose: The standing prep set a base brings up before chasing a goal, and the extraction phase that draw implies.

from __future__ import annotations

import math
from collections.abc import Mapping

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

# Establish one direct line for every early raw material before demand-driven
# growth begins. Iron deliberately comes first, but its 12/24 expansion policy
# must not consume the construction window reserved for copper and stone.
PLATE_FOUNDATION_BUILD_ORDER = ("iron-plate", "copper-plate", "stone-brick")
PLATE_FOUNDATION_FURNACES = {
    "iron-plate": 6,
    "copper-plate": 6,
    "stone-brick": 6,
}

# Steel is slow enough that one electric furnace is only a bootstrap token:
# 0.125 plate/s while consuming 0.625 iron plate/s. Six furnaces make the
# first useful construction line, and twelve iron furnaces leave half of the
# opening iron checkpoint available for belts, gears, and other consumers.
STEEL_BASELINE_FURNACES = 6
STEEL_IRON_CAPACITY_FLOOR = 12

# Before the electric-furnace mall chain is alive, extraction is deliberately
# bounded. These are ceilings, not promises to build every line immediately:
# the run may use fewer if the current demand is lower, but it cannot let a
# temporary mall bill jump straight to a 24+ furnace bootstrap.
BOOTSTRAP_FURNACE_CAPS = {
    "iron-plate": 12,
    "copper-plate": 6,
    "stone-brick": 6,
    "steel-plate": STEEL_BASELINE_FURNACES,
}

# Iron is the common construction input for the early factory.  Keep the
# growth ladder explicit so a short-lived mall target cannot leave its direct
# refinery at the opening six-furnace block.  The thresholds are mine capacity
# thresholds, not permission to overbuild a mine: callers still verify that the
# expansion can coherently add that drill phase and refinery module together.
# Beyond 24, measured demand controls the 48/96 phases.
IRON_GROWTH_TARGETS = ((6, 12), (12, 24))

ELECTRIC_DRILL_ITEMS_PER_SECOND = 0.5


def iron_growth_target(drill_count: int) -> int:
    """Return the proactive iron-furnace checkpoint supported by a mine.

    Six live drills trigger the coherent 12-drill/12-furnace expansion, and
    twelve trigger the 24/24 checkpoint. The atomic expansion preflight keeps
    furnace ghosts paired with the mine and transport that will feed them.
    """
    if drill_count <= 0:
        return 0
    target = 6
    for minimum_drills, furnace_target in IRON_GROWTH_TARGETS:
        if drill_count >= minimum_drills:
            target = furnace_target
    return target


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
    return smelter_count_for_draw(plate, baseline_plate_draw()[plate])


def baseline_drill_phase(plate: str) -> int:
    """The mining phase whose drills feed the prep set's draw on `plate`.

    Sized on the base drill rate with no mining-productivity credit, so a fresh
    base is not planned around research it has not finished. Snapping to
    EXTRACTION_DRILL_PHASES keeps prep on the same ladder the rest of the
    system expands along.
    """
    return drill_phase_for_draw(baseline_plate_draw()[plate])


MALL_DEMAND_HORIZON_SECONDS = 300.0
MALL_DEMAND_HEADROOM = 1.10


def mall_plate_draw(
    targets: Mapping[str, int], available: Mapping[str, int] | None = None,
) -> dict[str, float]:
    """Convert outstanding mall targets into raw plate requirements.

    Construction stock is a finite burst, not a steady-state consumer. The
    planner spreads that burst over a bounded five-minute horizon so it raises
    ore capacity early without treating every chest target as an infinite
    production line. Unknown/fluid ingredients are intentionally skipped.
    """
    remaining = dict(available or {})
    draw = {plate: 0.0 for plate in BASELINE_PLATES}

    def visit(item: str, quantity: float, path: set[str]) -> None:
        if quantity <= 0:
            return
        covered = min(quantity, remaining.get(item, 0))
        remaining[item] = remaining.get(item, 0) - covered
        quantity -= covered
        if quantity <= 0:
            return
        if item in draw:
            draw[item] += quantity
            return
        spec = LINE_RECIPES.get(item)
        if spec is None or item in path:
            return
        product_amount = max(1, spec.get("product_amount", 1))
        crafts = quantity / product_amount
        next_path = path | {item}
        for ingredient, amount in zip(
            spec.get("ingredients", ()), spec.get("amounts", ()), strict=True,
        ):
            visit(ingredient, crafts * amount, next_path)

    for item, target in targets.items():
        visit(item, float(max(0, target)), set())
    return draw


def demand_adjusted_plate_draw(
    targets: Mapping[str, int], available: Mapping[str, int] | None = None,
) -> dict[str, float]:
    """Baseline plate draw plus a bounded, headroomed mall burst."""
    burst = mall_plate_draw(targets, available)
    baseline = baseline_plate_draw()
    return {
        plate: baseline[plate]
        + burst[plate] * MALL_DEMAND_HEADROOM / MALL_DEMAND_HORIZON_SECONDS
        for plate in BASELINE_PLATES
    }


def smelter_count_for_draw(plate: str, draw_per_second: float) -> int:
    """Furnaces required for a requested plate rate."""
    spec = LINE_RECIPES[plate]
    per_furnace = _machine_craft_rate(plate) * spec.get("product_amount", 1)
    return max(1, math.ceil(draw_per_second / per_furnace))


def drill_phase_for_draw(draw_per_second: float) -> int:
    """Smallest shared mining phase that covers a plate draw."""
    drills = math.ceil(draw_per_second / ELECTRIC_DRILL_ITEMS_PER_SECOND)
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
