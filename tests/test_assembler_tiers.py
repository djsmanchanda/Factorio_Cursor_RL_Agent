# Path: tests/test_assembler_tiers.py
# Purpose: Prove the assembler tier a line or mall cell gets follows the base from a hand-stocked start, through making its own tier-2 machines, to spending tier 3 only where it pays.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
from planners import recipe_data  # noqa: E402
from planners.assembler_tiers import (  # noqa: E402
    TIER3_MIN_CRAFT_SECONDS,
    TIERS,
    UPGRADE_RESERVE,
    BaseCapability,
    line_machine,
    machines_to_upgrade,
    mall_machine,
    tier3_is_worth_it,
    upgrade_plan,
)
from planners.recipe_data import LINE_RECIPES, install_catalog_machines  # noqa: E402

_AM1, _AM2, _AM3 = TIERS
# Slot counts as the live game reports them; the whole point is that these are
# read, not assumed.
_LIVE_MACHINES = {"machines": [
    {"name": _AM1, "ingredient_count": 2, "crafting_speed": 0.5, "categories": ["crafting"]},
    {"name": _AM2, "ingredient_count": 4, "crafting_speed": 0.75, "categories": ["crafting"]},
    {"name": _AM3, "ingredient_count": 6, "crafting_speed": 1.25, "categories": ["crafting"]},
]}

_BOOTSTRAP = BaseCapability()
_MAKES_AM2 = BaseCapability(produces_tier2=True)
_MAKES_AM3 = BaseCapability(produces_tier2=True, produces_tier3=True)


@pytest.fixture(autouse=True)
def _live():
    # Cleared and refilled IN PLACE: assembler_tiers imported this dict by
    # reference, so rebinding the module attribute would leave it reading the
    # original object and the two would silently disagree.
    recipe_data.MACHINE_CAPABILITIES.clear()
    install_catalog_machines(_LIVE_MACHINES)
    yield
    recipe_data.MACHINE_CAPABILITIES.clear()


# --- phase 1: the hand-stocked start ---------------------------------------

def test_a_narrow_line_takes_tier_one_while_tier_two_is_finite() -> None:
    """Tier-2 machines come from the player's kit and run out. A gear line uses
    one ingredient slot, so spending a four-slot machine on it wastes the thing
    the mall cannot do without."""
    assert line_machine("iron-gear-wheel", _BOOTSTRAP) == _AM1
    assert line_machine("copper-cable", _BOOTSTRAP) == _AM1


def test_a_recipe_too_wide_for_tier_one_takes_tier_two_anyway() -> None:
    """Slots are physics, not policy: a three-ingredient recipe cannot run on a
    two-slot machine however scarce the alternative is."""
    assert len(LINE_RECIPES["inserter"]["ingredients"]) == 3
    assert line_machine("inserter", _BOOTSTRAP) == _AM2


def test_the_mall_gets_first_claim_on_tier_two() -> None:
    assert mall_machine("inserter", _BOOTSTRAP) == _AM2


# --- phase 2: the base makes its own ---------------------------------------

def test_everything_moves_to_tier_two_once_the_base_makes_it() -> None:
    """The scarcity is what justified tier 1; once it ends, so does the rule."""
    for recipe in ("iron-gear-wheel", "copper-cable", "electronic-circuit"):
        assert line_machine(recipe, _BOOTSTRAP) == _AM1
        assert line_machine(recipe, _MAKES_AM2) == _AM2


# --- phase 3: tier 3, demand based -----------------------------------------

def test_tier_three_goes_to_the_slow_expensive_recipes() -> None:
    assert LINE_RECIPES["chemical-science-pack"]["craft_time"] >= TIER3_MIN_CRAFT_SECONDS
    assert line_machine("chemical-science-pack", _MAKES_AM3) == _AM3


def test_a_half_second_recipe_never_justifies_tier_three() -> None:
    """A gear takes 0.5s; making it 40% quicker saves nothing worth an
    advanced-circuit machine."""
    assert LINE_RECIPES["iron-gear-wheel"]["craft_time"] < TIER3_MIN_CRAFT_SECONDS
    assert not tier3_is_worth_it("iron-gear-wheel")
    assert line_machine("iron-gear-wheel", _MAKES_AM3) == _AM2


def test_tier_three_is_not_used_before_the_base_can_make_it() -> None:
    """Choosing a machine the base cannot produce is choosing nothing."""
    assert line_machine("chemical-science-pack", _MAKES_AM2) == _AM2


def test_the_mall_gets_tier_three_first() -> None:
    """'first prioritized for the mall' -- including for quick recipes a LINE
    would not be given one for."""
    assert mall_machine("iron-gear-wheel", _MAKES_AM3) == _AM3
    assert line_machine("iron-gear-wheel", _MAKES_AM3) == _AM2


# --- safety: no live data means no guessing --------------------------------

def test_without_a_live_export_the_declared_machine_is_kept() -> None:
    """Slot counts decide everything here. With none known, the recipe's own
    machine is the answer that already worked -- guessing strands a
    three-ingredient recipe on a two-slot machine."""
    recipe_data.MACHINE_CAPABILITIES.clear()

    for recipe in ("iron-gear-wheel", "inserter", "chemical-science-pack"):
        assert line_machine(recipe, _BOOTSTRAP) == LINE_RECIPES[recipe]["machine"]


def test_a_furnace_recipe_is_never_given_an_assembler() -> None:
    assert line_machine("iron-plate", _MAKES_AM3) == "electric-furnace"
    assert line_machine("plastic-bar", _MAKES_AM3) == "chemical-plant"


def test_every_choice_can_actually_hold_the_recipe() -> None:
    """The property all of the above are instances of."""
    for capability in (_BOOTSTRAP, _MAKES_AM2, _MAKES_AM3):
        for recipe, spec in LINE_RECIPES.items():
            chosen = line_machine(recipe, capability)
            if chosen not in TIERS:
                continue
            slots = recipe_data.MACHINE_CAPABILITIES[chosen]["ingredient_count"]
            assert slots >= len(spec["ingredients"]), f"{recipe} on {chosen}"


# --- replacing tier-1 machines ---------------------------------------------

def test_nothing_is_upgraded_before_the_base_makes_tier_two() -> None:
    assert machines_to_upgrade({"iron-gear-wheel": 6}, _BOOTSTRAP) == {}


def test_upgrades_come_only_out_of_spare_stock() -> None:
    """The constraint is stock of the machine being INSTALLED. Rebuilding is
    housekeeping; it must not consume the machines a new stage is waiting on,
    or the base stops growing in order to tidy itself up."""
    at_reserve = BaseCapability(produces_tier2=True, stock={_AM2: UPGRADE_RESERVE})
    assert machines_to_upgrade({"iron-gear-wheel": 6}, at_reserve) == {}

    spare = BaseCapability(produces_tier2=True, stock={_AM2: UPGRADE_RESERVE + 3})
    assert machines_to_upgrade({"iron-gear-wheel": 6}, spare) == {"iron-gear-wheel": 3}


def test_an_upgrade_never_promises_more_machines_than_exist() -> None:
    plenty = BaseCapability(produces_tier2=True, stock={_AM2: UPGRADE_RESERVE + 50})

    assert machines_to_upgrade({"iron-gear-wheel": 2}, plenty) == {"iron-gear-wheel": 2}


def test_spare_machines_are_shared_across_lines_not_double_spent() -> None:
    spare = BaseCapability(produces_tier2=True, stock={_AM2: UPGRADE_RESERVE + 4})

    plan = machines_to_upgrade({"copper-cable": 3, "iron-gear-wheel": 3}, spare)

    assert sum(plan.values()) == 4


def test_the_upgrade_plan_orders_native_replacement_with_a_recipe_guard() -> None:
    plan = upgrade_plan({"iron-gear-wheel": [(4.5, 2.5)]})
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "upgrade_plan.schema.json")
        .read_text(encoding="utf-8")
    )

    assert not list(Draft7Validator(schema).iter_errors(plan))
    assert plan["actions"] == [{
        "action": "assembler_tier_upgrade",
        "from_name": _AM1,
        "to_name": _AM2,
        "recipe": "iron-gear-wheel",
        "position": {"x": 4.5, "y": 2.5},
    }]


def test_an_empty_upgrade_is_refused_rather_than_submitted() -> None:
    with pytest.raises(ValueError, match="at least one machine"):
        upgrade_plan({})
