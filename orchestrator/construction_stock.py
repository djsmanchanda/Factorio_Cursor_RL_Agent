# Path: orchestrator/construction_stock.py
# Purpose: Decide how much of a construction item to keep standing -- a small opening figure while it is scarce, and a full chest once the base makes its own.

from __future__ import annotations

from collections.abc import Mapping

# Chest slots the mall provider fills once an item is no longer scarce. A
# steel-chest is 48 slots; at 100 to a stack that is 4800 belts, which is the
# scale real blueprint work needs. User standard, 2026-08-02: "after the game is
# out of the starter phase, just let it build and fill up the chest".
PROVIDER_CHEST_SLOTS = 48

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


def chest_full_target(item: str, stack_sizes: Mapping[str, int]) -> int:
    """How many of `item` a full provider chest holds."""
    stack = stack_sizes.get(item) or FALLBACK_STACK_SIZE
    return PROVIDER_CHEST_SLOTS * stack


def standing_target(
    item: str,
    opening: int,
    *,
    self_sufficient: bool,
    stack_sizes: Mapping[str, int] = (),
) -> int:
    """The stock target for `item`, for the phase the base is in.

    While an item is scarce -- nothing on the base makes it, so every one comes
    out of the player's starter kit -- the opening figure stands, and the mall
    builds only what the mission actually asked for.

    Once the base MAKES it, the cap comes off and the chest is simply filled.
    That is self-limiting in the right way: a base cannot overproduce what it
    cannot produce, and an opening figure of 50 belts otherwise caps a base that
    has long outgrown it. A single belted expansion can want a thousand belts
    plus hundreds of splitters and undergrounds.

    Machines are exempt, however self-sufficient the base becomes.
    """
    if not self_sufficient or item not in BULK_CONSTRUCTION_ITEMS:
        return opening
    return max(opening, chest_full_target(item, dict(stack_sizes or {})))


def standing_targets(
    opening: Mapping[str, int],
    self_sufficient: Mapping[str, bool],
    stack_sizes: Mapping[str, int] = (),
) -> dict[str, int]:
    """Apply `standing_target` across a whole mall target table."""
    return {
        item: standing_target(
            item, target,
            self_sufficient=bool(self_sufficient.get(item)),
            stack_sizes=stack_sizes,
        )
        for item, target in opening.items()
    }
