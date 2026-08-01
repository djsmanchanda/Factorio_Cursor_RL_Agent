# Path: orchestrator/intermediate_scaling.py
# Purpose: Promote high-demand intermediates from one-machine mall cells to shared production lines.

from __future__ import annotations

import math

from orchestrator import live_base
from orchestrator.extraction_capacity import EXTRACTION_DRILL_PHASES
from tools.rcon_client import RconClient
from planners.recipe_data import BELT_TIERS, LINE_RECIPES, MACHINE_SPEEDS, machine_ingredient_rates

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


def _line_phase(minimum_machines: int) -> int:
    """The smallest phase on the ladder that covers `minimum_machines`."""
    return next(
        (phase for phase in PROMOTED_LINE_PHASES if phase >= minimum_machines),
        PROMOTED_LINE_PHASES[-1],
    )
PROMOTABLE_INTERMEDIATES = frozenset({
    "iron-gear-wheel", "copper-cable", "iron-stick", "electronic-circuit",
    "advanced-circuit", "processing-unit", "engine-unit", "pipe",
})


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


def promoted_line_machine_count(
    item: str, demand_per_second: float, existing_machines: int = 0,
    *, saturated: bool = False,
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
    if item not in PROMOTABLE_INTERMEDIATES:
        return None
    if not saturated and demand_per_second <= MALL_INTERMEDIATE_RATE_LIMIT:
        return None
    spec = LINE_RECIPES.get(item)
    if spec is None or spec["machine"] not in MACHINE_SPEEDS:
        return None
    per_machine = (
        spec.get("product_amount", 1)
        * MACHINE_SPEEDS[spec["machine"]]
        / spec["craft_time"]
    )
    existing_capacity = max(0, existing_machines) * per_machine
    required = max(0.0, demand_per_second - existing_capacity)
    needed = math.ceil(required * PROMOTED_LINE_HEADROOM / per_machine)
    target = _line_phase(max(needed, PROMOTED_LINE_MIN_MACHINES))
    if saturated:
        # Flat out at the current size means the current size is not enough,
        # so step past it rather than re-proposing what is already built.
        target = max(target, _line_phase(existing_machines + 1))
    return target

def promoted_line_belt_type(
    item: str, machine_count: int, available: dict[str, int],
) -> str:
    """Choose the cheapest stocked belt whose full rate carries the line input."""
    spec = LINE_RECIPES[item]
    demand = max(machine_ingredient_rates(item, machine_count), default=0.0)
    tiers = ("transport-belt", "fast-transport-belt", "express-transport-belt", "turbo-transport-belt")
    for tier in tiers:
        if available.get(tier, 0) > 0 and BELT_TIERS[tier] >= demand:
            return tier
    stocked = [tier for tier in tiers if available.get(tier, 0) > 0]
    return max(stocked, key=lambda tier: BELT_TIERS[tier], default="transport-belt")