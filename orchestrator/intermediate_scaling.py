# Path: orchestrator/intermediate_scaling.py
# Purpose: Promote high-demand intermediates from one-machine mall cells to shared production lines.

from __future__ import annotations

import math

from orchestrator import live_base
from orchestrator.extraction_capacity import EXTRACTION_DRILL_PHASES
from tools.rcon_client import RconClient
from planners.recipe_data import (
    BELT_TIERS,
    LINE_MAX_INGREDIENTS,
    LINE_RECIPES,
    MACHINE_SPEEDS,
    machine_ingredient_rates,
)

Point = tuple[float, float]

# The mall remains appropriate for occasional construction bursts. Above this
# sustained rate, a shared line is cheaper and more stable than requester-fed
# one-machine cells competing for the same intermediate.
MALL_INTERMEDIATE_RATE_LIMIT = 3.0

# A promoted line grows in the same phases the mining system does, so extraction
# and the assembly it feeds step up together instead of one outrunning the
# other. Deliberately the SAME ladder, not a parallel copy that could drift.
PROMOTED_LINE_PHASES = EXTRACTION_DRILL_PHASES
PROMOTED_LINE_MIN_MACHINES = PROMOTED_LINE_PHASES[0]
PROMOTED_LINE_HEADROOM = 1.25

# Circuits are the one bootstrap intermediate that routinely feeds several
# independent construction chains at once.  Two paired-mall assemblers are a
# useful transition buffer; a third is not.  Once live demand outgrows those
# two cells, move the whole dependency into its first proper belt-fed block.
ELECTRONIC_CIRCUIT_MALL_MACHINE_LIMIT = 2

# How long the existing cells may take to finish what is OUTSTANDING before
# a saturated cell justifies a dedicated line.
#
# Saturation alone is far too weak a trigger. A single mall cell runs flat
# out whenever it has any work at all, so "saturated" fired while filling a
# one-off chest: transport-belt was promoted to six machines -- eighteen a
# second, with six feed requesters to supply -- for a target of two hundred
# that one cell covers in about a minute. That is capacity built for work
# already nearly done, paid for in the plates everything else needed.
#
# A line that is genuinely behind never finishes its backlog inside this
# window, so real demand still promotes.
PROMOTION_PATIENCE_SECONDS = 120.0


def _line_phase(minimum_machines: int) -> int:
    """The smallest phase on the ladder that covers `minimum_machines`."""
    return next(
        (phase for phase in PROMOTED_LINE_PHASES if phase >= minimum_machines),
        PROMOTED_LINE_PHASES[-1],
    )


# Recipes on these machines are already owned by another stage: furnace output
# is sized by the mining phase feeding it, and chemical plants and refineries
# are placed by the fluid stage that routes their pipes. Promoting one here
# would build a second, unfed copy of something that stage already manages.
STAGE_OWNED_MACHINES = frozenset({
    "electric-furnace", "chemical-plant", "oil-refinery",
})


def is_promotable(item: str) -> bool:
    """Whether a shared belt-fed line is the right shape for this recipe.

    Derived from what the line can physically supply rather than from a list of
    remembered names, so a recipe added to LINE_RECIPES scales without also
    having to be remembered here. Two things disqualify one:

    - Its machine belongs to another stage (see STAGE_OWNED_MACHINES).
    - It needs a fluid. A promoted line is belt-fed and inserter-served and has
      no pipe run, so the machines would sit empty waiting on an input that
      never arrives.
    - It takes more ingredients than a line can carry. generate_line_layout
      runs two main belt lanes plus one auxiliary and refuses a fourth, so a
      recipe like assembling-machine-2 is a mall cell for good -- which is the
      right shape for it anyway, being built in small batches on demand.

    The old hand-written list got both edges wrong in the same direction. It
    named processing-unit, which takes sulfuric acid and so could never have run
    on a belt-fed line, while omitting transport-belt and inserter -- the two
    items the mall is most often asked to mass-produce.
    """
    spec = LINE_RECIPES.get(item)
    if spec is None:
        return False
    if spec.get("fluid_ingredients"):
        return False
    if len(spec["ingredients"]) > LINE_MAX_INGREDIENTS:
        return False
    machine = spec["machine"]
    return machine in MACHINE_SPEEDS and machine not in STAGE_OWNED_MACHINES


def live_intermediate_demand(
    client: RconClient, surface: str, force: str, item: str,
) -> float:
    """Sum the live per-second demand from working consumer lines."""
    total = 0.0
    for consumer, spec in LINE_RECIPES.items():
        if item not in spec.get("ingredients", ()) or consumer == item:
            continue
        line = live_base.find_line(client, surface, force, consumer, spec["machine"])
        if line is None or line.working_count <= 0:
            continue
        for index, ingredient in enumerate(spec["ingredients"]):
            if ingredient == item:
                total += machine_ingredient_rates(consumer, line.working_count)[index]
    return total


def output_per_machine(item: str) -> float:
    """One machine's output of `item`, in items per second."""
    spec = LINE_RECIPES[item]
    return (
        spec.get("product_amount", 1)
        * MACHINE_SPEEDS[spec["machine"]]
        / spec["craft_time"]
    )


def backlog_seconds(item: str, outstanding: int, machines: int) -> float:
    """How long the machines already built need to finish `outstanding`.

    Infinite when nothing is built, so the first cell is never blocked from
    being created; zero when there is no backlog to clear.
    """
    if outstanding <= 0:
        return 0.0
    if machines <= 0 or item not in LINE_RECIPES:
        return float("inf")
    rate = output_per_machine(item) * machines
    return outstanding / rate if rate > 0 else float("inf")


def promoted_line_machine_count(
    item: str, demand_per_second: float, existing_machines: int = 0,
    *, saturated: bool = False, backlog: float | None = None,
) -> int | None:
    """Return the new shared-line size when mall capacity is no longer enough.

    `saturated` -- every machine on this item's own cell is running flat out --
    promotes on its own, without waiting for measured demand to clear the rate
    limit. It has to, because live_intermediate_demand only counts consumers
    that are WORKING, and consumers starved of this very item are not working.
    A saturated gear cell therefore reported 0.00/s of gear demand and was never
    promoted, while every line waiting on gears sat idle proving the point.

    A machine running flat out is already the evidence: it cannot go faster, so
    the only way to raise output is more machines.
    """
    if not is_promotable(item):
        return None
    # A third circuit mall machine is exactly the point at which the compact
    # mall stops being the right topology.  Keep the first two cells for the
    # bootstrap, then promote to the six-machine phase as soon as measured
    # consumers exceed their combined 3.0/s capacity. Queued construction
    # demand is also evidence when it exceeds the two-cell patience window:
    # consumers starved of circuits do not report as working, so relying on
    # their live rate alone caused the controller to keep borrowing mall slots.
    if (
        item == "electronic-circuit"
        and existing_machines >= ELECTRONIC_CIRCUIT_MALL_MACHINE_LIMIT
        and (
            demand_per_second > (
                output_per_machine(item) * ELECTRONIC_CIRCUIT_MALL_MACHINE_LIMIT
            )
            or backlog is not None and backlog > PROMOTION_PATIENCE_SECONDS
        )
    ):
        return PROMOTED_LINE_MIN_MACHINES
    if saturated and backlog is not None and backlog <= PROMOTION_PATIENCE_SECONDS:
        # Busy, but the work is nearly done. A cell filling a one-off chest
        # is saturated the whole time it is filling it, which says nothing
        # about whether a dedicated line is warranted.
        saturated = False
    if not saturated and demand_per_second <= MALL_INTERMEDIATE_RATE_LIMIT:
        return None
    per_machine = output_per_machine(item)
    existing_capacity = max(0, existing_machines) * per_machine
    required = max(0.0, demand_per_second - existing_capacity)
    needed = math.ceil(required * PROMOTED_LINE_HEADROOM / per_machine)
    target = _line_phase(max(needed, PROMOTED_LINE_MIN_MACHINES))
    if saturated:
        # Flat out at the current size means the current size is not enough,
        # so step past it rather than re-proposing what is already built.
        target = max(target, _line_phase(existing_machines + 1))
    return target


def promoted_companion_machine_count(item: str, machine_count: int) -> int | None:
    """Size the direct intermediate companion required by a promoted line.

    A six-machine electronic-circuit line consumes 27 copper cable/s.  With
    assembling-machine-2 that is nine cable assemblers, not another mall pair.
    Keep this derived from live recipe rates so recipe/catalog changes cannot
    silently desynchronise the two lines.
    """
    if item != "electronic-circuit" or machine_count <= 0:
        return None
    cable_index = LINE_RECIPES[item]["ingredients"].index("copper-cable")
    cable_demand = machine_ingredient_rates(item, machine_count)[cable_index]
    return math.ceil(cable_demand / output_per_machine("copper-cable"))

def promoted_line_belt_type(
    item: str, machine_count: int, available: dict[str, int], *,
    full_lane_input: bool = False,
) -> str:
    """Choose the cheapest belt whose required input topology can sustain.

    A full-lane bus carries the aggregate input rate, rather than one side
    lane's rate. If no suitable belt is stocked, return the smallest capable
    tier so ordinary construction-shortage handling schedules that tier instead
    of building a line which cannot ever meet its own throughput.
    """
    spec = LINE_RECIPES[item]
    rates = machine_ingredient_rates(item, machine_count)
    demand = sum(rates) if full_lane_input else max(rates, default=0.0)
    if full_lane_input:
        # The direct input bus may use both lanes, but LocalLayoutPlanner's
        # machine output inserters land on one lane. The selected belt must
        # therefore also carry the whole produced rate on that output lane.
        demand = max(demand, 2 * output_per_machine(item) * machine_count)
    ordered_tiers = (
        "transport-belt", "fast-transport-belt",
        "express-transport-belt", "turbo-transport-belt",
    )
    # A direct full-lane block names its physically required belt even before
    # the dynamic catalog has registered its recipe. The material scheduler
    # then queues that recipe after catalog load instead of silently choosing
    # an incapable yellow belt. Ordinary promoted lines keep the old
    # executable-only rule.
    tiers = (
        ordered_tiers if full_lane_input else tuple(
            tier for tier in ordered_tiers if tier in LINE_RECIPES
        ) or ("transport-belt",)
    )
    for tier in tiers:
        if available.get(tier, 0) > 0 and BELT_TIERS[tier] >= demand:
            return tier
    if full_lane_input:
        capable = next((tier for tier in tiers if BELT_TIERS[tier] >= demand), None)
        if capable is not None:
            return capable
    stocked = [tier for tier in tiers if available.get(tier, 0) > 0]
    return max(stocked, key=lambda tier: BELT_TIERS[tier], default="transport-belt")
