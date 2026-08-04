# Path: orchestrator/construction_stock.py
# Purpose: Set deterministic mall reserve stacks: bounded during bootstrap, expandable for large jobs, and unrestricted once the base is mature.

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

INITIAL_BULK_STACKS = 4
INITIAL_MACHINE_STACKS = 1
RESERVE_GROWTH_STACKS = 2
MAX_JOB_SHARE = 0.5

# Used only when the live stack size is unknown. Deliberately low: understating
# it holds less, which costs a later top-up. Overstating it asks the mall for
# stock the chest cannot hold, and the cell never reads as done.
FALLBACK_STACK_SIZE = 50

# Items whose demand scales with how much the base BUILDS. Machines are not
# here: a base needs a handful of refineries however large it grows, and a
# chest full of them would eat the plates the belts joining them are made of.
BULK_CONSTRUCTION_ITEMS = frozenset({
    "transport-belt", "fast-transport-belt", "express-transport-belt",
    "underground-belt", "fast-underground-belt", "express-underground-belt",
    "splitter", "fast-splitter", "express-splitter",
    "inserter", "fast-inserter", "bulk-inserter",
    "small-electric-pole", "medium-electric-pole", "big-electric-pole",
    "pipe", "pipe-to-ground", "steel-chest", "iron-chest",
})


@dataclass(frozen=True)
class MallReserve:
    """One cell's reserve and bar policy.

    `gate_target=None` deliberately leaves a mature cell running until its
    unrestricted provider is full.
    """

    gate_target: int | None
    storage_count: int
    storage_stacks: int | None
    fill_chest: bool = False


def _round_growth(stacks: int, opening: int, growth: int) -> int:
    if stacks <= opening:
        return opening
    return opening + math.ceil((stacks - opening) / growth) * growth


def mall_reserve(
    item: str,
    job_size: int,
    *,
    stack_sizes: Mapping[str, int] = (),
    mature: bool = False,
) -> MallReserve:
    """Reserve construction stock ahead of demand without making a job wait.

    During bootstrap, high-volume construction parts start at four stacks and
    machines at one. If one job is larger than half the opening reserve, grow
    in deterministic stack increments until that job again uses at most half.
    A mature self-producing base removes the bar and stock gate; the provider
    chest itself becomes the limit.
    """
    if job_size <= 0:
        raise ValueError("Mall job size must be positive")
    if mature:
        return MallReserve(None, job_size, None, fill_chest=True)
    stack_size = dict(stack_sizes or {}).get(item) or FALLBACK_STACK_SIZE
    opening = (
        INITIAL_BULK_STACKS
        if item in BULK_CONSTRUCTION_ITEMS
        else INITIAL_MACHINE_STACKS
    )
    stacks = opening
    if job_size > opening * stack_size * MAX_JOB_SHARE:
        needed = math.ceil(job_size / (stack_size * MAX_JOB_SHARE))
        growth = RESERVE_GROWTH_STACKS if opening > 1 else 1
        stacks = _round_growth(needed, opening, growth)
    count = stacks * stack_size
    return MallReserve(count, count, stacks)
