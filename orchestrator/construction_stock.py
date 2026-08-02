# Path: orchestrator/construction_stock.py
# Purpose: Decide how much of a construction item to keep standing -- a small opening figure while it is scarce, and a full chest once the base makes its own.

from __future__ import annotations

from collections.abc import Mapping

# How much of its OWN output a bulk item keeps standing, expressed in seconds
# of production rather than as a count.
#
# This is the rule, and the number is only its consequence: SPEND ON CAPACITY
# WHILE CAPACITY IS SCARCE, AND STOCKPILE ONLY OUT OF SURPLUS. A base making
# three belts a second holds a couple of hundred; the same base at eighteen a
# second holds a thousand, and it can afford to. The buffer follows what the
# base can already make, so it never competes with building the thing that
# would make more.
#
# That is what a fixed figure could not express. 4800 belts was a milestone a
# run climbed toward for forty minutes, expanding iron every sixty seconds to
# reach it -- iron that did not become a drill, an assembler, or a science
# pack. User standard, 2026-08-03: "I set the 10 stack limit so that it doesn't
# waste the limited resources in building transport belt, and the resources can
# be used to build more important things faster", and "conserve resource usage,
# and make more resources, so that it can then use the resources a little more
# freely".
BUFFER_SECONDS = 60.0

# Ceiling, in stacks. Even a base that can afford more has no use for it: past
# this, belts are iron sitting in a chest instead of iron doing something.
MAX_BUFFER_STACKS = 10

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

# Used only when the live stack size is unknown. Deliberately low: understating
# it fills less of the chest, which costs a later top-up. Overstating it asks
# the mall for stock the chest cannot hold, and the cell never reads as done.
FALLBACK_STACK_SIZE = 50


def buffer_ceiling(item: str, stack_sizes: Mapping[str, int]) -> int:
    """The most of `item` worth holding, however productive the base gets."""
    stack = stack_sizes.get(item) or FALLBACK_STACK_SIZE
    return MAX_BUFFER_STACKS * stack


def standing_target(
    item: str,
    opening: int,
    *,
    production_rate: float = 0.0,
    stack_sizes: Mapping[str, int] = (),
) -> int:
    """The stock target for `item`, given what the base can currently make.

    `production_rate` is the item's own live output in items per second -- zero
    while nothing produces it. That zero is what keeps the early game honest:
    an item coming entirely out of the player's starter kit gets the opening
    figure and nothing more, so the mall builds what the mission asked for
    instead of hoarding a resource it cannot replace.

    Once a line exists the buffer is BUFFER_SECONDS of that line's own output,
    which grows as the line does and is by construction affordable -- the base
    is already making them that fast. It is capped at `buffer_ceiling` because
    past that, stock is material sitting in a chest rather than doing something.

    Machines are exempt: a base needs a handful of refineries however large it
    grows, and a chest of them would eat the plates the belts joining them are
    made of.
    """
    if item not in BULK_CONSTRUCTION_ITEMS or production_rate <= 0:
        return opening
    earned = int(production_rate * BUFFER_SECONDS)
    return max(opening, min(earned, buffer_ceiling(item, dict(stack_sizes or {}))))


def standing_targets(
    opening: Mapping[str, int],
    production_rates: Mapping[str, float],
    stack_sizes: Mapping[str, int] = (),
) -> dict[str, int]:
    """Apply `standing_target` across a whole mall target table."""
    return {
        item: standing_target(
            item, target,
            production_rate=production_rates.get(item, 0.0),
            stack_sizes=stack_sizes,
        )
        for item, target in opening.items()
    }
