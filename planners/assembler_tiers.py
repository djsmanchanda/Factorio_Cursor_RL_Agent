# Path: planners/assembler_tiers.py
# Purpose: Choose which assembler tier a line or a mall cell gets, as the base moves from a hand-stocked start to making its own machines.

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from planners.recipe_data import (
    LINE_RECIPES,
    MACHINE_CAPABILITIES,
    machine_supports_recipe,
)

# Cheapest first. The tiers are NOT interchangeable: what separates them is
# ingredient slots, and only secondarily speed.
TIERS = ("assembling-machine-1", "assembling-machine-2", "assembling-machine-3")

# Recipes quicker than this do not justify a tier-3 machine. A gear takes half
# a second; making it 40% quicker saves nothing worth an advanced-circuit
# machine, while a 24-second science pack is where that speed is actually paid
# back. User standard, 2026-08-02: tier 3 is "demand based, and for more
# expensive and longer build time products".
TIER3_MIN_CRAFT_SECONDS = 6.0

# Spare tier-2 machines to hold before rebuilding a tier-1 line with them.
# Upgrading is optional work: it must never consume the machines a NEW stage is
# waiting on, or the base stops growing in order to tidy itself up.
UPGRADE_RESERVE = 4


@dataclass(frozen=True)
class BaseCapability:
    """What the base can currently make for itself, not merely what it holds.

    Stock is not capability. A hand-placed starter kit shows the same numbers
    as a working machine line and runs out; `produces_*` is what decides the
    phase, and stock only decides whether a choice is affordable today.
    """

    produces_tier2: bool = False
    produces_tier3: bool = False
    stock: Mapping[str, int] = ()

    def held(self, machine: str) -> int:
        return dict(self.stock).get(machine, 0) if self.stock else 0


def _fits(machine: str, recipe: str) -> bool:
    return machine_supports_recipe(machine, recipe)


def _craft_seconds(recipe: str) -> float:
    return float(LINE_RECIPES[recipe]["craft_time"])


def tier3_is_worth_it(recipe: str) -> bool:
    """Whether this recipe is slow enough for a tier-3 machine to earn its cost."""
    return _craft_seconds(recipe) >= TIER3_MIN_CRAFT_SECONDS


def _is_assembler_recipe(recipe: str) -> bool:
    """Whether choosing a tier is even the question for this recipe.

    A furnace or chemical-plant recipe belongs to the stage that places those
    machines. Handing it an assembler because an assembler has enough slots is
    how a smelter turns into an assembling machine that cannot smelt.
    """
    return LINE_RECIPES[recipe]["machine"] in TIERS


def mall_machine(recipe: str, capability: BaseCapability) -> str:
    """The tier a MALL cell gets -- first claim on the best available.

    The mall builds the widest recipes on the base (four and five ingredients),
    so it needs the slots a line never uses, and it is where a new tier shows up
    first: tier 3 is "first prioritized for the mall".
    """
    if not _is_assembler_recipe(recipe):
        return LINE_RECIPES[recipe]["machine"]
    if capability.produces_tier3 and _fits(TIERS[2], recipe):
        return TIERS[2]
    if capability.produces_tier2 and _fits(TIERS[1], recipe):
        return TIERS[1]
    return _cheapest_fitting(recipe)


def line_machine(recipe: str, capability: BaseCapability) -> str:
    """The tier a production LINE gets, for the phase the base is in.

    1. Bootstrap -- tier 2 is hand-stocked and finite, so lines take tier 1 and
       every tier-2 machine is saved for the mall cells that need four slots.
    2. Once the base MAKES tier 2, the scarcity is over and everything uses it.
    3. Tier 3 is demand-based on top of that: only for recipes slow enough to
       pay for it, and only once the base makes those too.
    """
    if not _is_assembler_recipe(recipe):
        return LINE_RECIPES[recipe]["machine"]
    if (
        capability.produces_tier3
        and tier3_is_worth_it(recipe)
        and _fits(TIERS[2], recipe)
    ):
        return TIERS[2]
    if capability.produces_tier2 and _fits(TIERS[1], recipe):
        return TIERS[1]
    return _cheapest_fitting(recipe)


def _cheapest_fitting(recipe: str) -> str:
    """Lowest tier with enough ingredient slots, or the recipe's own machine.

    Falling back to the declared machine matters on a base that has not
    exported its machine prototypes yet: with no slot counts known, nothing can
    be chosen safely, and the recipe's own machine is what already worked.
    """
    if not MACHINE_CAPABILITIES:
        return LINE_RECIPES[recipe]["machine"]
    for tier in TIERS:
        if _fits(tier, recipe):
            return tier
    return LINE_RECIPES[recipe]["machine"]


def machines_to_upgrade(
    existing: Mapping[str, int], capability: BaseCapability,
) -> dict[str, int]:
    """How many tier-1 machines to promote to tier 2, per recipe.

    Only once the base MAKES tier 2, and only out of spare stock above
    UPGRADE_RESERVE -- a rebuild is housekeeping, and housekeeping must not
    outbid a stage that is waiting to be built at all.
    """
    if not capability.produces_tier2:
        return {}
    # Stock of the machine being installed. Construction bots recover each
    # tier-1 machine after its native upgrade, so it is not the constraint.
    spare = max(0, capability.held(TIERS[1]) - UPGRADE_RESERVE)
    if spare <= 0:
        return {}
    plan: dict[str, int] = {}
    for recipe, count in sorted(existing.items()):
        if (
            count <= 0
            or recipe not in LINE_RECIPES
            or not _is_assembler_recipe(recipe)
            or not _fits(TIERS[1], recipe)
        ):
            continue
        take = min(count, spare)
        if take <= 0:
            break
        plan[recipe] = take
        spare -= take
    return plan


def upgrade_plan(
    positions: Mapping[str, list], target: str = TIERS[1], *,
    source: str = TIERS[0],
) -> dict:
    """Order bot-driven in-place promotion of exact tier-1 machines.

    Factorio's native upgrade path preserves the configured machine while
    replacing its tier. The expected recipe remains in the action as an
    ownership guard: a repurposed cell must never be upgraded by stale intent.
    """
    if not positions:
        raise ValueError("An assembler upgrade plan needs at least one machine")
    actions: list[dict] = []
    for recipe, machine_positions in sorted(positions.items()):
        for position in machine_positions:
            actions.append({
                "action": "assembler_tier_upgrade",
                "from_name": source, "to_name": target,
                "recipe": recipe,
                "position": {"x": position[0], "y": position[1]},
            })
    return {"actions": actions}


def entity_upgrade_plan(
    positions: list, *, source: str, target: str,
) -> dict:
    """Order an exact non-crafter tier replacement without a recipe guard."""
    if not positions:
        raise ValueError("An entity upgrade plan needs at least one position")
    return {"actions": [{
        "action": "entity_tier_upgrade",
        "from_name": source,
        "to_name": target,
        "position": {"x": position[0], "y": position[1]},
    } for position in positions]}
