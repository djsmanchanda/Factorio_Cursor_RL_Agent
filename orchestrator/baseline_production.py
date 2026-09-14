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
# not a target. The opening mall is six fixed cells: one permanent anchor
# each for iron-gear-wheel and copper-cable (never borrowed or reconfigured),
# plus four rotational cells starting as a second gear, a second cable, one
# electronic-circuit, and one transport-belt. A new need below the twelve-slot
# cap adds another rotational cell; at the cap the shared slots rotate
# instead. Steel-plate is deliberately NOT here. It is smelted, not assembled:
# a furnace takes its recipe from what is inserted, so an idle one reports no
# recipe and find_line can never count it. Prep saw zero however many it had
# built and placed another cell every pass -- twelve furnaces across six mall
# cells in one run. Steel belongs to a smelting stage, and is built on demand
# by whatever needs it.
BASELINE_MACHINES = {
    "iron-gear-wheel": 2,
    "copper-cable": 2,
    "electronic-circuit": 1,
    "transport-belt": 1,
}

# A plate the prep set consumes has to be mined and smelted; anything else in
# the set is produced by the set itself.
BASELINE_PLATES = ("iron-plate", "copper-plate")

# Establish the three raw furnace products before following the goal's actual
# dependency chain. Iron and copper still come first; stone-brick follows so a
# base with reduced gifted stock can manufacture electric furnaces instead of
# discovering the missing stone chain only when the first accumulator/power
# expansion is already blocked.
PLATE_FOUNDATION_BUILD_ORDER = (
    "iron-plate", "copper-plate", "stone-brick",
)
PLATE_FOUNDATION_FURNACES = {
    "iron-plate": 6,
    "copper-plate": 6,
    "stone-brick": 6,
}

# Steel is itself a bootstrap ingredient for advanced machines. One furnace
# beside the iron provider is enough to start that dependency chain; demand
# may expand it after the first steel is demonstrably produced.
STEEL_BASELINE_FURNACES = 1
STEEL_IRON_CAPACITY_FLOOR = 1

# The reduced-supply chemical bootstrap is a capability ladder, not a recursive
# free-for-all. Each producer must reach first output before the controller may
# consume scarce machines to open the next rung. Plastic and sulfur name live
# fluid stages; the other entries name ordinary mall producers.
CHEMICAL_BOOTSTRAP_LADDER = (
    "pipe",
    "steel-plate",
    "chemical-plant",
    "oil-refinery",
    "offshore-pump",
    "pumpjack",
    "plastic-bar",
    "advanced-circuit",
    "sulfur",
    "sulfuric-acid",
)

# A normal mall is allowed to dedicate one cell per recipe only after these
# five cell-building items have their own live producers. Before that point,
# finite construction batches borrow an existing baseline mall assembler and
# share its provider chest; the loan is restored after the requested batch.
CORE_MALL_PRODUCERS = (
    "assembling-machine-2",
    "fast-inserter",
    "passive-provider-chest",
    "requester-chest",
    "substation",
)

# Bootstrap owns at most twelve mall assemblers: two permanent anchors (one
# gear, one cable) plus up to ten rotational slots. A new construction need
# below the cap adds a rotational cell for it; at the cap the rotational slots
# borrow and restore instead, and the two anchors are never touched. Twelve
# preserves useful pre-advanced-circuit parallelism while finite-stock
# rationing, without making every demand an unconditional permanent mall
# allocation.
BOOTSTRAP_MALL_SLOT_TARGET = 12

RATIONED_MALL_BATCH_ITEMS = frozenset({
    *CORE_MALL_PRODUCERS,
    # Before the core mall can build its own machines, every construction
    # output below is a demand-owned, need-plus-margin batch.  It may claim a
    # free half of the twelve-slot pool, or borrow an existing half when the pool
    # is full; it must not become a permanent one-recipe cell and consume the
    # slot needed to make the core mall self-sufficient.
    "assembling-machine-1",
    "electronic-circuit",
    "iron-stick",
    "transport-belt",
    "underground-belt",
    "inserter",
    "splitter",
    "bulk-inserter",
    "fast-splitter",
    "fast-transport-belt",
    "fast-underground-belt",
    "small-electric-pole",
    "medium-electric-pole",
    "big-electric-pole",
    "iron-chest",
    "storage-chest",
    "active-provider-chest",
    "buffer-chest",
    # Steel chests are a small bootstrap capability batch. Before the core
    # mall is self-sustaining, borrow an existing mall cell rather than open a
    # two-assembler, belt-fed conversion line for one chest seed.
    "steel-chest",
    "pipe",
    "pipe-to-ground",
    "lab",
    "solar-panel",
    "accumulator",
    "assembling-machine-3",
    "electric-furnace",
    "electric-mining-drill",
    "chemical-plant",
    "oil-refinery",
    "offshore-pump",
    "pumpjack",
})

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

ELECTRIC_DRILL_ITEMS_PER_SECOND = 0.5

#: Lookahead when bounding mine growth by refinery appetite: one ladder row.
#: Mines build in complete six-drill rows, so the cap lets the next row land
#: while its furnaces are funded without stranding rows the refinery cannot
#: eat for the whole oil-gated era.
COHERENT_MINE_HEADROOM_DRILLS = 6


def coherent_drill_cap(
    recipe: str, furnace_count: int, mining_productivity_bonus: float,
) -> int:
    """Drills a refinery of `furnace_count` can consume, plus one row.

    Mine and refinery move as one coherent increment: at live productivity
    six iron furnaces eat ~6 drills' output (1:1) while six stone-brick
    furnaces eat ~12 (2:1 ore ratio) -- the exact ratios live runs are held
    to. Sizing on the base drill rate or plate units instead overbuilds iron
    behind oil-gated refineries and starves stone behind its own ratio.
    Supports only growth decisions; it never shrinks a plan.
    """
    spec = LINE_RECIPES[recipe]
    furnace_ore_rate = (
        MACHINE_SPEEDS[spec["machine"]] * spec["amounts"][0] / spec["craft_time"]
    )
    drill_rate = ELECTRIC_DRILL_ITEMS_PER_SECOND * (
        1.0 + max(0.0, mining_productivity_bonus)
    )
    need = math.ceil(
        max(0, furnace_count) * furnace_ore_rate / drill_rate
    )
    return need + COHERENT_MINE_HEADROOM_DRILLS


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
        # Preserve the declared baseline order among equally ready recipes.
        # Alphabetic sorting put copper-cable ahead of iron-gear-wheel, so an
        # empty-stock save began by recursively planning copper extraction
        # instead of consuming the first live iron foundation.
        for recipe in ready:
            ordered.append(recipe)
            del remaining[recipe]
    return tuple(ordered)
