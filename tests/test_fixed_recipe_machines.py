# Path: tests/test_fixed_recipe_machines.py
# Purpose: Verify exact machine sizing for feasible fixed-recipe balances and fail-closed validation.

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path

import pytest

from core.fixed_recipe_balance import (
    FixedRecipeBalanceResult,
    RecipeSignature,
    SourceShortage,
    solve_fixed_recipe_balance,
)
from core.fixed_recipe_machines import (
    FixedRecipeMachineError,
    compute_fixed_recipe_machines,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
PLAYER_CATALOG = json.loads((FIXTURE_DIR / "player_recipe_catalog.json").read_text())
HISTORICAL_EXPORT = (
    Path(__file__).resolve().parents[1]
    / "data/factorio-2.1.21/probes/deterministic/server/script-output"
    / "factorio_mod/recipe_catalogs/recipe_catalog_243688.json"
)


def _part(name: str, amount: int | float = 1, kind: str = "item") -> dict:
    return {"name": name, "type": kind, "amount": amount}


def _recipe(
    name: str,
    ingredients: list[dict] | None = None,
    products: list[dict] | None = None,
    *,
    energy: int | float = 60,
    category: str = "crafting",
    enabled: bool = True,
    supported: bool = True,
    unsupported_reason: str | None = None,
) -> dict:
    recipe = {
        "name": name,
        "enabled": enabled,
        "category": category,
        "energy_ticks": energy,
        "ingredients": list(ingredients or []),
        "products": list(products if products is not None else [_part(name)]),
        "supported": supported,
    }
    if unsupported_reason is not None:
        recipe["unsupported_reason"] = unsupported_reason
    return recipe


def _machine(
    name: str,
    *,
    kind: str = "assembling-machine",
    speed: int | float = 1,
    slots: int = 99,
    categories: tuple[str, ...] = ("crafting",),
) -> dict:
    return {
        "name": name,
        "type": kind,
        "ingredient_count": slots,
        "crafting_speed": speed,
        "categories": list(categories),
    }


def _catalog(*recipes: dict, machines: list[dict] | None = None) -> dict:
    return {
        "version": "1.0.0",
        "force": "planner",
        "tick": 0,
        "raw_resources": [],
        "recipes": list(recipes),
        "machines": list(machines or []),
    }


def _unique(names: tuple[str, ...], rates: tuple[Fraction, ...]) -> FixedRecipeBalanceResult:
    return FixedRecipeBalanceResult(
        status="unique",
        feasible=True,
        recipe_names=names,
        rates=rates,
        reason="unique exact nonnegative fixed-recipe material balance",
    )


def _facts(result):
    return {fact.recipe: fact for fact in result.facts}


def test_exact_craft_seconds_ceiling_and_spare_facts():
    catalog = _catalog(
        _recipe("plate", [_part("ore")], energy=192, category="smelting"),
        _recipe("cable", [_part("plate")], [_part("cable", 2)], energy=30),
        machines=[
            _machine("am1", speed=0.5),
            _machine("furnace", kind="furnace", speed=2, slots=1, categories=("smelting",)),
        ],
    )
    balance = solve_fixed_recipe_balance(
        catalog,
        recipes=("plate", "cable"),
        demand={"cable": 2},
        sources={"ore": 1},
    )
    assert balance.status == "unique" and balance.feasible is True

    result = compute_fixed_recipe_machines(
        catalog,
        balance=balance,
        machine_by_recipe={"plate": "furnace", "cable": "am1"},
    )

    cable = _facts(result)["cable"]
    assert cable.base_craft_seconds == Fraction(1, 2)
    assert cable.craft_seconds_per_second == Fraction(1, 2)
    assert cable.exact_machines == Fraction(1)
    assert cable.machine_count == 1
    assert cable.nominal_crafts_per_second == Fraction(1)
    assert cable.spare_crafts_per_second == 0
    assert cable.spare_machine_equivalents == 0

    plate = _facts(result)["plate"]
    assert plate.base_craft_seconds == Fraction(16, 5)
    assert plate.craft_seconds_per_second == Fraction(16, 5)
    assert plate.exact_machines == Fraction(8, 5)
    assert plate.machine_count == 2
    assert plate.nominal_crafts_per_second == Fraction(5, 4)
    assert plate.spare_crafts_per_second == Fraction(1, 4)
    assert plate.spare_machine_equivalents == Fraction(2, 5)
    assert result.total_machines == 3


def test_fractional_speed_rounds_up_and_reports_nominal_rate():
    catalog = _catalog(
        _recipe("cable", [_part("plate")], [_part("cable", 2)], energy=30),
        machines=[_machine("am2", speed=0.75)],
    )
    balance = solve_fixed_recipe_balance(
        catalog,
        recipes=("cable",),
        demand={"cable": 2},
        sources={"plate": 1},
    )
    assert balance.status == "unique" and balance.feasible is True
    result = compute_fixed_recipe_machines(
        catalog,
        balance=balance,
        machine_by_recipe={"cable": "am2"},
    )

    fact = result.facts[0]
    assert fact.exact_machines == Fraction(2, 3)
    assert fact.machine_count == 1
    assert fact.nominal_crafts_per_second == Fraction(3, 2)
    assert fact.spare_crafts_per_second == Fraction(1, 2)
    assert fact.spare_machine_equivalents == Fraction(1, 3)


def test_exact_integer_boundary_does_not_add_a_machine():
    catalog = _catalog(
        _recipe("r", energy=60),
        machines=[_machine("am1", speed=1)],
    )
    balance = solve_fixed_recipe_balance(
        catalog,
        recipes=("r",),
        demand={"r": 3},
    )
    assert balance.status == "unique" and balance.feasible is True
    result = compute_fixed_recipe_machines(
        catalog,
        balance=balance,
        machine_by_recipe={"r": "am1"},
    )

    fact = result.facts[0]
    assert fact.exact_machines == 3
    assert fact.machine_count == 3
    assert fact.spare_crafts_per_second == 0
    assert fact.spare_machine_equivalents == 0


def test_zero_rate_reports_zero_machines_without_inventing_work():
    catalog = _catalog(
        _recipe("r", energy=60),
        machines=[_machine("am1", speed=1)],
    )
    balance = solve_fixed_recipe_balance(
        catalog,
        recipes=("r",),
        demand={"r": 0},
    )
    assert balance.status == "unique" and balance.feasible is True
    result = compute_fixed_recipe_machines(
        catalog,
        balance=balance,
        machine_by_recipe={"r": "am1"},
    )

    fact = result.facts[0]
    assert fact.exact_machines == 0
    assert fact.machine_count == 0
    assert fact.nominal_crafts_per_second == 0
    assert fact.spare_crafts_per_second == 0
    assert result.total_machines == 0


def _balance_for_provenance(catalog: dict) -> FixedRecipeBalanceResult:
    balance = solve_fixed_recipe_balance(
        catalog,
        recipes=("r",),
        demand={"out": 1},
        sources={"ore": 1},
    )
    assert balance.status == "unique" and balance.feasible is True
    return balance


def test_matching_recipe_provenance_is_accepted():
    catalog = _catalog(
        _recipe("r", [_part("ore")], [_part("out")]),
        machines=[_machine("am1")],
    )
    result = compute_fixed_recipe_machines(
        catalog,
        balance=_balance_for_provenance(catalog),
        machine_by_recipe={"r": "am1"},
    )

    assert result.facts[0].recipe == "r"


@pytest.mark.parametrize(
    "change",
    [
        ("ingredient-amount", lambda parts: parts["ingredients"][0].update({"amount": 2})),
        ("ingredient-type", lambda parts: parts["ingredients"][0].update({"type": "fluid"})),
        ("product-amount", lambda parts: parts["products"][0].update({"amount": 2})),
        ("product-type", lambda parts: parts["products"][0].update({"type": "fluid"})),
    ],
)
def test_changed_catalog_recipe_parts_are_rejected(change):
    _, mutate = change
    original = _catalog(
        _recipe("r", [_part("ore")], [_part("out")]),
        machines=[_machine("am1")],
    )
    balance = _balance_for_provenance(original)

    changed_recipe = _recipe("r", [_part("ore")], [_part("out")])
    mutate(changed_recipe)
    changed = _catalog(changed_recipe, machines=[_machine("am1")])

    with pytest.raises(FixedRecipeMachineError, match="does not match the current catalog"):
        compute_fixed_recipe_machines(
            changed,
            balance=balance,
            machine_by_recipe={"r": "am1"},
        )


def test_different_catalog_with_same_recipe_names_is_rejected():
    original = _catalog(
        _recipe("r", [_part("ore")], [_part("out")]),
        machines=[_machine("am1")],
    )
    balance = _balance_for_provenance(original)
    different = _catalog(
        _recipe("r", [_part("ore", 2)], [_part("out")]),
        machines=[_machine("am1")],
    )

    with pytest.raises(FixedRecipeMachineError, match="does not match the current catalog"):
        compute_fixed_recipe_machines(
            different,
            balance=balance,
            machine_by_recipe={"r": "am1"},
        )


def test_forged_recipe_provenance_is_rejected():
    catalog = _catalog(
        _recipe("r", [_part("ore")], [_part("out")]),
        machines=[_machine("am1")],
    )
    balance = _balance_for_provenance(catalog)
    forged_signature = RecipeSignature.from_parts(
        "r",
        [("ore", "item", Fraction(2))],
        [("out", "item", Fraction(1))],
    )
    forged = replace(balance, recipe_signatures=(forged_signature,))

    with pytest.raises(FixedRecipeMachineError, match="does not match the current catalog"):
        compute_fixed_recipe_machines(
            catalog,
            balance=forged,
            machine_by_recipe={"r": "am1"},
        )


def test_missing_recipe_provenance_is_rejected():
    catalog = _catalog(
        _recipe("r", [_part("ore")], [_part("out")]),
        machines=[_machine("am1")],
    )
    balance = replace(_balance_for_provenance(catalog), recipe_signatures=())

    with pytest.raises(FixedRecipeMachineError, match="no recipe provenance signatures"):
        compute_fixed_recipe_machines(
            catalog,
            balance=balance,
            machine_by_recipe={"r": "am1"},
        )


@pytest.mark.parametrize(
    "balance, message",
    [
        (
            FixedRecipeBalanceResult("underdetermined", None, ("r",), None, "underdetermined"),
            "underdetermined",
        ),
        (
            FixedRecipeBalanceResult("inconsistent", False, ("r",), None, "inconsistent"),
            "inconsistent",
        ),
        (
            FixedRecipeBalanceResult(
                "unique", False, ("r",), (Fraction(-1),), "negative exact rates"
            ),
            "negative exact rates",
        ),
        (
            FixedRecipeBalanceResult(
                "unique",
                False,
                ("r",),
                (Fraction(1),),
                "exceeds fixed source caps",
                source_names=("ore",),
                source_rates=(Fraction(3),),
                source_caps=(Fraction(2),),
                shortages=(SourceShortage("ore", Fraction(3), Fraction(2)),),
            ),
            "source-cap-short",
        ),
    ],
)
def test_unusable_balance_states_fail_closed(balance, message):
    catalog = _catalog(_recipe("r"), machines=[_machine("am1")])

    with pytest.raises(FixedRecipeMachineError, match=message):
        compute_fixed_recipe_machines(
            catalog, balance=balance, machine_by_recipe={"r": "am1"}
        )


def test_inconsistent_claimed_feasibility_and_rate_alignment_fail_closed():
    catalog = _catalog(_recipe("r"), machines=[_machine("am1")])

    with pytest.raises(FixedRecipeMachineError, match="must not be negative"):
        compute_fixed_recipe_machines(
            catalog,
            balance=_unique(("r",), (Fraction(-1),)),
            machine_by_recipe={"r": "am1"},
        )
    with pytest.raises(FixedRecipeMachineError, match="must align"):
        compute_fixed_recipe_machines(
            catalog,
            balance=_unique(("r",), ()),
            machine_by_recipe={"r": "am1"},
        )
    with pytest.raises(FixedRecipeMachineError, match="must be a FixedRecipeBalanceResult"):
        compute_fixed_recipe_machines(
            catalog, balance="unique", machine_by_recipe={"r": "am1"}
        )


def test_malformed_or_missing_machine_catalog_fails_closed():
    balance = _unique(("r",), (Fraction(1),))
    with pytest.raises(FixedRecipeMachineError, match="catalog must be a mapping"):
        compute_fixed_recipe_machines([], balance=balance, machine_by_recipe={"r": "am1"})
    with pytest.raises(FixedRecipeMachineError, match="nonempty machines list"):
        compute_fixed_recipe_machines(
            {"recipes": [_recipe("r")]}, balance=balance, machine_by_recipe={"r": "am1"}
        )


def test_mapping_completeness_and_duplicates_fail_closed():
    recipe = _recipe("r")
    catalog = _catalog(recipe, machines=[_machine("am1")])

    with pytest.raises(FixedRecipeMachineError, match="incomplete"):
        compute_fixed_recipe_machines(
            catalog, balance=_unique(("r",), (Fraction(1),)), machine_by_recipe={}
        )
    with pytest.raises(FixedRecipeMachineError, match="unselected recipes"):
        compute_fixed_recipe_machines(
            catalog,
            balance=_unique(("r",), (Fraction(1),)),
            machine_by_recipe={"r": "am1", "other": "am1"},
        )
    with pytest.raises(FixedRecipeMachineError, match="must be a mapping"):
        compute_fixed_recipe_machines(
            catalog,
            balance=_unique(("r",), (Fraction(1),)),
            machine_by_recipe=[("r", "am1")],
        )
    with pytest.raises(FixedRecipeMachineError, match="ambiguous duplicate machine"):
        compute_fixed_recipe_machines(
            _catalog(recipe, machines=[_machine("am1"), _machine("am1")]),
            balance=_unique(("r",), (Fraction(1),)),
            machine_by_recipe={"r": "am1"},
        )
    with pytest.raises(FixedRecipeMachineError, match="ambiguous duplicate recipe"):
        compute_fixed_recipe_machines(
            _catalog(recipe, dict(recipe), machines=[_machine("am1")]),
            balance=_unique(("r",), (Fraction(1),)),
            machine_by_recipe={"r": "am1"},
        )
    with pytest.raises(FixedRecipeMachineError, match="is repeated"):
        compute_fixed_recipe_machines(
            catalog,
            balance=_unique(("r", "r"), (Fraction(1), Fraction(1))),
            machine_by_recipe={"r": "am1"},
        )


def test_recipe_validation_fails_closed():
    cases = [
        (_recipe("r", enabled=False), "not enabled"),
        (_recipe("r", supported=False, unsupported_reason="hand-only"), "not supported: hand-only"),
        (_recipe("r", energy=0), "energy_ticks must be positive"),
        (_recipe("r", energy=-1), "energy_ticks must be positive"),
        (_recipe("r", energy=float("inf")), "energy_ticks must be finite"),
        (_recipe("r", category=""), "category must be a nonempty string"),
        (_recipe("r", products=[]), "no deterministic products"),
        (_recipe("r", products=[_part("r", 0)]), "amount must be positive"),
        (_recipe("r", ingredients=[_part("a"), _part("a")]), "repeats 'a'"),
    ]
    for recipe, message in cases:
        with pytest.raises(FixedRecipeMachineError, match=message):
            compute_fixed_recipe_machines(
                _catalog(recipe, machines=[_machine("am1")]),
                balance=_unique(("r",), (Fraction(1),)),
                machine_by_recipe={"r": "am1"},
            )

    claimed = _recipe("r")
    claimed["modules"] = 2
    with pytest.raises(FixedRecipeMachineError, match="claims unsupported effects"):
        compute_fixed_recipe_machines(
            _catalog(claimed, machines=[_machine("am1")]),
            balance=_unique(("r",), (Fraction(1),)),
            machine_by_recipe={"r": "am1"},
        )
    malformed = _recipe("r")
    malformed["custom"] = 1
    with pytest.raises(FixedRecipeMachineError, match="unsupported fields"):
        compute_fixed_recipe_machines(
            _catalog(malformed, machines=[_machine("am1")]),
            balance=_unique(("r",), (Fraction(1),)),
            machine_by_recipe={"r": "am1"},
        )


def test_unsupported_claimed_effects_fail_closed():
    recipe = _recipe("r")
    recipe["productivity_bonus"] = 0.1
    with pytest.raises(FixedRecipeMachineError, match="claims unsupported effects"):
        compute_fixed_recipe_machines(
            _catalog(recipe, machines=[_machine("am1")]),
            balance=_unique(("r",), (Fraction(1),)),
            machine_by_recipe={"r": "am1"},
        )

    machine = _machine("am1")
    machine["beacons"] = 4
    with pytest.raises(FixedRecipeMachineError, match="claims unsupported effects"):
        compute_fixed_recipe_machines(
            _catalog(_recipe("r"), machines=[machine]),
            balance=_unique(("r",), (Fraction(1),)),
            machine_by_recipe={"r": "am1"},
        )


@pytest.mark.parametrize(
    "machine, message",
    [
        (_machine("am1", kind="lab"), "unsupported type"),
        (_machine("am1", slots=-1), "nonnegative integer"),
        (_machine("am1", slots=True), "nonnegative integer"),
        (_machine("am1", speed=0), "crafting_speed must be positive"),
        (_machine("am1", speed=-1), "crafting_speed must be positive"),
        (_machine("am1", speed=float("inf")), "crafting_speed must be finite"),
        (_machine("am1", speed="1"), "crafting_speed must be an int"),
        ({**_machine("am1"), "categories": "crafting"}, "categories must be a list"),
        ({**_machine("am1"), "custom": 1}, "unsupported fields"),
    ],
)
def test_machine_validation_fails_closed(machine, message):
    with pytest.raises(FixedRecipeMachineError, match=message):
        compute_fixed_recipe_machines(
            _catalog(_recipe("r"), machines=[machine]),
            balance=_unique(("r",), (Fraction(1),)),
            machine_by_recipe={"r": "am1"},
        )


def test_missing_mapped_machine_fails_closed():
    with pytest.raises(FixedRecipeMachineError, match="not in the catalog"):
        compute_fixed_recipe_machines(
            _catalog(_recipe("r"), machines=[_machine("am1")]),
            balance=_unique(("r",), (Fraction(1),)),
            machine_by_recipe={"r": "am2"},
        )


def test_category_and_ingredient_slot_mismatches_fail_closed():
    with pytest.raises(FixedRecipeMachineError, match="does not support recipe category"):
        compute_fixed_recipe_machines(
            _catalog(
                _recipe("r", category="crafting"),
                machines=[_machine("furnace", kind="furnace", categories=("smelting",))],
            ),
            balance=_unique(("r",), (Fraction(1),)),
            machine_by_recipe={"r": "furnace"},
        )

    recipe = _recipe("r", [_part("ore"), _part("water", kind="fluid")])
    with pytest.raises(FixedRecipeMachineError, match="ingredient slots"):
        compute_fixed_recipe_machines(
            _catalog(recipe, machines=[_machine("am1", slots=1)]),
            balance=_unique(("r",), (Fraction(1),)),
            machine_by_recipe={"r": "am1"},
        )

    catalog = _catalog(recipe, machines=[_machine("am1", slots=2)])
    balance = solve_fixed_recipe_balance(
        catalog,
        recipes=("r",),
        demand={"r": 1},
        sources={"ore": 1, "water": 1},
    )
    result = compute_fixed_recipe_machines(
        catalog,
        balance=balance,
        machine_by_recipe={"r": "am1"},
    )
    assert result.facts[0].ingredient_count == 2


@pytest.mark.parametrize("categories,valid", [
    (["crafting", "smelting"], True),
    (["crafting"], False),
    (["smelting"], False),
    (["crafting", "smelting", "smelting"], False),
    ([], False),
])
def test_recipe_category_sets(categories, valid):
    recipe = _recipe("r", [_part("ore")], category="crafting")
    recipe["categories"] = categories
    catalog = _catalog(recipe, machines=[_machine("furnace", kind="furnace", categories=("smelting",))])
    balance = solve_fixed_recipe_balance(catalog, recipes=("r",), demand={"r": 1}, sources={"ore": 1})
    if valid:
        compute_fixed_recipe_machines(catalog, balance=balance, machine_by_recipe={"r": "furnace"})
    else:
        with pytest.raises(FixedRecipeMachineError):
            compute_fixed_recipe_machines(catalog, balance=balance, machine_by_recipe={"r": "furnace"})


def test_historical_player_catalog_smelting_fixture():
    # The tracked fixture is a real player-force export; these two machines are
    # the live 2.1.21 values from the historical export below.
    catalog = dict(PLAYER_CATALOG)
    catalog["machines"] = [
        {
            "name": "electric-furnace",
            "type": "furnace",
            "ingredient_count": 1,
            "crafting_speed": 2,
            "categories": ["smelting"],
        }
    ]
    balance = solve_fixed_recipe_balance(
        catalog,
        recipes=("copper-plate",),
        demand={"copper-plate": 2},
        sources={"copper-ore": 2},
    )
    assert balance.status == "unique" and balance.feasible is True

    result = compute_fixed_recipe_machines(
        catalog,
        balance=balance,
        machine_by_recipe={"copper-plate": "electric-furnace"},
    )

    fact = result.facts[0]
    assert fact.base_craft_seconds == Fraction(16, 5)
    assert fact.exact_machines == Fraction(16, 5)
    assert fact.machine_count == 4
    assert fact.nominal_crafts_per_second == Fraction(5, 2)
    assert fact.spare_crafts_per_second == Fraction(1, 2)
    assert fact.ingredient_count == 1
    assert result.total_machines == 4


@pytest.mark.skipif(
    not HISTORICAL_EXPORT.exists(),
    reason="historical live recipe export is not present in this checkout",
)
def test_historical_live_export_matches_catalog_machines():
    catalog = json.loads(HISTORICAL_EXPORT.read_text())
    balance = solve_fixed_recipe_balance(
        catalog,
        recipes=("copper-plate", "copper-cable"),
        demand={"copper-cable": 2},
        sources={"copper-ore": 1},
    )
    assert balance.status == "unique" and balance.feasible is True

    result = compute_fixed_recipe_machines(
        catalog,
        balance=balance,
        machine_by_recipe={
            "copper-plate": "electric-furnace",
            "copper-cable": "assembling-machine-1",
        },
    )

    facts = _facts(result)
    assert facts["copper-plate"].machine_count == 2
    assert facts["copper-plate"].spare_crafts_per_second == Fraction(1, 4)
    assert facts["copper-cable"].machine_count == 1
    assert facts["copper-cable"].spare_crafts_per_second == 0
    assert result.total_machines == 3
