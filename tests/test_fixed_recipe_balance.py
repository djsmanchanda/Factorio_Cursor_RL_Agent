# Path: tests/test_fixed_recipe_balance.py
# Purpose: Verify the catalog fixed-recipe adapter, exact coproduct balance, DAG agreement, and fail-closed boundaries.

from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path

import pytest

from core.fixed_recipe_balance import (
    FixedRecipeBalanceError,
    FixedRecipeBalanceResult,
    RecipePartSignature,
    SourceShortage,
    solve_fixed_recipe_balance,
)
from core.recipe_dag import compile_recipe_dag

CATALOG = json.loads((Path(__file__).parent / "fixtures/recipe_catalog.json").read_text())


def _part(name: str, amount: int | float) -> dict:
    return {"name": name, "type": "item", "amount": amount}


def _recipe(
    name: str,
    ingredients: list[tuple[str, int | float]],
    products: list[tuple[str, int | float]],
    *,
    enabled: bool = True,
    supported: bool = True,
    unsupported_reason: str | None = None,
) -> dict:
    recipe = {
        "name": name,
        "enabled": enabled,
        "category": "crafting",
        "energy_ticks": 60,
        "ingredients": [_part(item, amount) for item, amount in ingredients],
        "products": [_part(item, amount) for item, amount in products],
        "supported": supported,
    }
    if unsupported_reason is not None:
        recipe["unsupported_reason"] = unsupported_reason
    return recipe


def _catalog(*recipes: dict, raw: tuple[str, ...] = ()) -> dict:
    return {
        "version": "1.0.0",
        "force": "planner",
        "tick": 0,
        "raw_resources": list(raw),
        "recipes": list(recipes),
    }


def _rates(result: FixedRecipeBalanceResult) -> dict[str, Fraction]:
    assert result.rates is not None
    return dict(zip(result.recipe_names, result.rates))


def _source_rates(result: FixedRecipeBalanceResult) -> dict[str, Fraction]:
    assert result.source_rates is not None
    return dict(zip(result.source_names, result.source_rates))


def test_result_binds_normalized_recipe_part_signatures():
    ingredient = {"name": "water", "type": "fluid", "amount": 0.5}
    catalog = _catalog(
        _recipe("x-from-y", [("y", 1)], [("x", 2)]),
        raw=("y", "water"),
    )
    catalog["recipes"][0]["ingredients"].append(ingredient)
    result = solve_fixed_recipe_balance(
        catalog,
        recipes=("x-from-y",),
        demand={"x": 2},
        sources={"y": 1, "water": 0.5},
    )

    assert result.status == "unique" and result.feasible is True
    assert len(result.recipe_signatures) == 1
    signature = result.recipe_signatures[0]
    assert signature.recipe == "x-from-y"
    assert signature.ingredients == (
        RecipePartSignature("water", "fluid", Fraction(1, 2)),
        RecipePartSignature("y", "item", Fraction(1)),
    )
    assert signature.products == (
        RecipePartSignature("x", "item", Fraction(2)),
    )


def test_single_output_chain_matches_existing_recipe_dag():
    goal = {"version": "1.0.0", "target": "copper-cable", "delta": {"rate": 2, "unit": "per_second"}}
    graph = compile_recipe_dag(goal, CATALOG)
    result = solve_fixed_recipe_balance(
        CATALOG,
        recipes=("copper-plate", "copper-cable"),
        demand={"copper-cable": 2},
        sources={"copper-ore": 1},
    )

    assert result.status == "unique"
    assert result.feasible is True
    rates = _rates(result)
    dag_rates = {node["recipe"]: node["crafts_per_second"] for node in graph["nodes"]}
    assert set(rates) == set(dag_rates)
    for name, rate in rates.items():
        assert rate == Fraction(str(dag_rates[name]))


def test_coproduct_cycle_balances_exactly_with_explicit_source():
    catalog = _catalog(
        _recipe("split", [("crude-oil", 2)], [("a", 2), ("b", 1)]),
        _recipe("refine-b", [("a", 1)], [("b", 1)]),
        raw=("crude-oil",),
    )
    result = solve_fixed_recipe_balance(
        catalog,
        recipes=("split", "refine-b"),
        demand={"a": 1, "b": 1},
        sources={"crude-oil": Fraction(4, 3)},
    )

    assert result.status == "unique"
    assert result.feasible is True
    assert _rates(result) == {"refine-b": Fraction(1, 3), "split": Fraction(2, 3)}


def test_fractional_catalog_amounts_are_converted_exactly():
    catalog = _catalog(
        _recipe("half-output", [("ore", 1)], [("plate", 0.5)]),
        raw=("ore",),
    )
    result = solve_fixed_recipe_balance(
        catalog,
        recipes=("half-output",),
        demand={"plate": 1},
        sources={"ore": 2},
    )

    assert result.status == "unique"
    assert result.feasible is True
    assert _rates(result) == {"half-output": Fraction(2)}


def test_underdetermined_system_is_not_claimed_feasible():
    catalog = _catalog(
        _recipe("make-a", [], [("x", 1)]),
        _recipe("make-b", [], [("x", 1)]),
    )
    result = solve_fixed_recipe_balance(
        catalog,
        recipes=("make-a", "make-b"),
        demand={"x": 1},
        sources={},
    )

    assert result.status == "underdetermined"
    assert result.feasible is None
    assert result.rates is None
    assert "not established" in result.reason


def test_unique_negative_rates_are_not_claimed_feasible():
    catalog = _catalog(
        _recipe("make-a", [], [("a", 1)]),
        _recipe("make-b", [("a", 1)], [("b", 1)]),
    )
    result = solve_fixed_recipe_balance(
        catalog,
        recipes=("make-a", "make-b"),
        demand={"b": 0},
        sources={"a": 1},
    )

    assert result.status == "unique"
    assert result.feasible is False
    assert _rates(result)["make-a"] == Fraction(-1)
    assert "negative" in result.reason


def test_inconsistent_system_is_reported_without_rates():
    catalog = _catalog(
        _recipe("x-from-y", [("y", 1)], [("x", 1)]),
        raw=("y",),
    )
    result = solve_fixed_recipe_balance(
        catalog,
        recipes=("x-from-y",),
        demand={"x": 1},
        sources={"y": 2},
    )

    assert result.status == "inconsistent"
    assert result.feasible is False
    assert result.rates is None


def test_unconsumed_coproduct_without_demand_fails_closed():
    catalog = _catalog(
        _recipe("split", [("crude-oil", 2)], [("a", 2), ("b", 1)]),
        raw=("crude-oil",),
    )
    with pytest.raises(FixedRecipeBalanceError, match="disposal is unsupported"):
        solve_fixed_recipe_balance(
            catalog,
            recipes=("split",),
            demand={"a": 1},
            sources={"crude-oil": 1},
        )


def test_consumed_material_without_producer_or_source_fails_closed():
    catalog = _catalog(_recipe("x-from-y", [("y", 1)], [("x", 1)]))
    with pytest.raises(FixedRecipeBalanceError, match="no explicit source boundary"):
        solve_fixed_recipe_balance(
            catalog,
            recipes=("x-from-y",),
            demand={"x": 1},
            sources={},
        )


def test_source_boundary_must_supply_a_selected_ingredient():
    catalog = _catalog(
        _recipe("x-from-y", [("y", 1)], [("x", 1)]),
        raw=("y",),
    )
    with pytest.raises(FixedRecipeBalanceError, match="not consumed by any selected recipe"):
        solve_fixed_recipe_balance(
            catalog,
            recipes=("x-from-y",),
            demand={"x": 1},
            sources={"y": 1, "z": 1},
        )


@pytest.mark.parametrize(
    "recipe, message",
    [
        (_recipe("disabled", [("y", 1)], [("x", 1)], enabled=False), "not enabled"),
        (_recipe("unsupported", [("y", 1)], [("x", 1)], supported=False), "not supported"),
        (_recipe("catalyst", [("a", 1)], [("a", 1)]), "catalyst or self-referential"),
        (_recipe("zero", [("y", 1)], [("x", 0)]), "must be positive"),
    ],
)
def test_unsupported_selected_recipes_fail_closed(recipe, message):
    catalog = _catalog(recipe, raw=("y",))
    with pytest.raises(FixedRecipeBalanceError, match=message):
        solve_fixed_recipe_balance(
            catalog,
            recipes=(recipe["name"],),
            demand={"x": 1},
            sources={"y": 1},
        )


@pytest.mark.parametrize("field, value", [("probability", 0.5), ("catalyst_amount", 1), ("temperature", 150)])
def test_part_fields_beyond_name_type_amount_fail_closed(field, value):
    recipe = _recipe("x-from-y", [("y", 1)], [("x", 1)])
    recipe["products"][0][field] = value
    catalog = _catalog(recipe, raw=("y",))
    with pytest.raises(FixedRecipeBalanceError, match="unsupported fields"):
        solve_fixed_recipe_balance(
            catalog,
            recipes=("x-from-y",),
            demand={"x": 1},
            sources={"y": 1},
        )


def test_material_type_collision_between_item_and_fluid_fails_closed():
    producer = _recipe("make-x", [], [("x", 1)])
    consumer = _recipe("x-to-y", [("x", 1)], [("y", 1)])
    consumer["ingredients"][0]["type"] = "fluid"
    catalog = _catalog(producer, consumer)
    with pytest.raises(FixedRecipeBalanceError, match="declared as both item and fluid"):
        solve_fixed_recipe_balance(
            catalog,
            recipes=("make-x", "x-to-y"),
            demand={"y": 1},
            sources={},
        )


def test_ambiguous_catalog_or_selection_fails_closed():
    duplicate = _recipe("same", [("y", 1)], [("x", 1)])
    catalog = _catalog(duplicate, dict(duplicate), raw=("y",))
    with pytest.raises(FixedRecipeBalanceError, match="ambiguous duplicate recipe"):
        solve_fixed_recipe_balance(
            catalog,
            recipes=("same",),
            demand={"x": 1},
            sources={"y": 1},
        )

    catalog = _catalog(_recipe("same", [("y", 1)], [("x", 1)]), raw=("y",))
    with pytest.raises(FixedRecipeBalanceError, match="is repeated"):
        solve_fixed_recipe_balance(
            catalog,
            recipes=("same", "same"),
            demand={"x": 1},
            sources={"y": 1},
        )


def test_unknown_recipe_and_invalid_rates_fail_closed():
    catalog = _catalog(_recipe("x-from-y", [("y", 1)], [("x", 1)]), raw=("y",))
    with pytest.raises(FixedRecipeBalanceError, match="not in the catalog"):
        solve_fixed_recipe_balance(
            catalog,
            recipes=("missing",),
            demand={"x": 1},
            sources={"y": 1},
        )
    with pytest.raises(FixedRecipeBalanceError, match="must not be negative"):
        solve_fixed_recipe_balance(
            catalog,
            recipes=("x-from-y",),
            demand={"x": -1},
            sources={"y": 1},
        )
    with pytest.raises(FixedRecipeBalanceError, match="must be positive"):
        solve_fixed_recipe_balance(
            catalog,
            recipes=("x-from-y",),
            demand={"x": 1},
            sources={"y": 0},
        )


def test_unknown_sources_match_existing_recipe_dag():
    goal = {"version": "1.0.0", "target": "electronic-circuit", "delta": {"rate": 2, "unit": "per_second"}}
    graph = compile_recipe_dag(goal, CATALOG)
    result = solve_fixed_recipe_balance(
        CATALOG,
        recipes=("copper-plate", "copper-cable", "iron-plate", "electronic-circuit"),
        demand={"electronic-circuit": 2},
        allowed_sources=("copper-ore", "iron-ore"),
    )

    assert result.status == "unique"
    assert result.feasible is True
    assert result.shortages == ()
    assert _source_rates(result) == {"copper-ore": Fraction(3), "iron-ore": Fraction(2)}
    dag_rates = {node["recipe"]: node["crafts_per_second"] for node in graph["nodes"]}
    assert _rates(result) == {
        "copper-plate": Fraction(str(dag_rates["copper-plate"])),
        "copper-cable": Fraction(str(dag_rates["copper-cable"])),
        "iron-plate": Fraction(str(dag_rates["iron-plate"])),
        "electronic-circuit": Fraction(str(dag_rates["electronic-circuit"])),
    }
    assert graph["raw_leaves_per_second"] == {"copper-ore": 3.0, "iron-ore": 2.0}


def test_overdetermined_unknown_source_system_can_be_unique():
    catalog = _catalog(_recipe("split", [("a", 1), ("b", 1)], [("x", 1), ("y", 1)]))
    result = solve_fixed_recipe_balance(
        catalog,
        recipes=("split",),
        demand={"x": 1, "y": 1},
        allowed_sources=("a", "b"),
    )

    assert result.status == "unique"
    assert result.feasible is True
    assert _rates(result) == {"split": Fraction(1)}
    assert _source_rates(result) == {"a": Fraction(1), "b": Fraction(1)}


def test_overdetermined_inconsistent_unknown_source_system_reports_no_rates():
    catalog = _catalog(_recipe("split", [("a", 1), ("b", 1)], [("x", 1), ("y", 1)]))
    result = solve_fixed_recipe_balance(
        catalog,
        recipes=("split",),
        demand={"x": 1, "y": 2},
        allowed_sources=("a", "b"),
    )

    assert result.status == "inconsistent"
    assert result.feasible is False
    assert result.rates is None
    assert result.source_rates is None


def test_underdetermined_unknown_sources_do_not_claim_feasibility_or_caps():
    catalog = _catalog(
        _recipe("ore-to-x-a", [("ore", 1)], [("x", 1)]),
        _recipe("ore-to-x-b", [("ore", 1)], [("x", 1)]),
    )
    result = solve_fixed_recipe_balance(
        catalog,
        recipes=("ore-to-x-a", "ore-to-x-b"),
        demand={"x": 1},
        allowed_sources=("ore",),
        source_caps={"ore": 0},
    )

    assert result.status == "underdetermined"
    assert result.feasible is None
    assert result.rates is None
    assert result.source_rates is None
    assert result.shortages == ()
    assert "not established" in result.reason


def test_unique_negative_unknown_source_rate_is_not_claimed_feasible():
    catalog = _catalog(
        _recipe("a-to-x", [("a", 1)], [("x", 1)]),
        _recipe("b-to-a", [("b", 1)], [("a", 1)]),
    )
    result = solve_fixed_recipe_balance(
        catalog,
        recipes=("a-to-x", "b-to-a"),
        demand={"x": 1},
        sources={"b": 2},
        allowed_sources=("a",),
    )

    assert result.status == "unique"
    assert result.feasible is False
    assert _rates(result) == {"a-to-x": Fraction(1), "b-to-a": Fraction(2)}
    assert _source_rates(result) == {"a": Fraction(-1)}
    assert "negative" in result.reason


def test_unknown_source_cap_exact_and_within_cap_is_feasible():
    result = solve_fixed_recipe_balance(
        CATALOG,
        recipes=("copper-plate", "copper-cable", "iron-plate", "electronic-circuit"),
        demand={"electronic-circuit": 2},
        allowed_sources=("copper-ore", "iron-ore"),
        source_caps={"copper-ore": 3, "iron-ore": 2},
    )

    assert result.status == "unique"
    assert result.feasible is True
    assert result.shortages == ()
    assert _source_rates(result) == {"copper-ore": Fraction(3), "iron-ore": Fraction(2)}


def test_unknown_source_cap_shortage_preserves_the_unique_solution():
    catalog_result = solve_fixed_recipe_balance(
        CATALOG,
        recipes=("copper-plate", "copper-cable", "iron-plate", "electronic-circuit"),
        demand={"electronic-circuit": 2},
        allowed_sources=("copper-ore", "iron-ore"),
    )
    result = solve_fixed_recipe_balance(
        CATALOG,
        recipes=("copper-plate", "copper-cable", "iron-plate", "electronic-circuit"),
        demand={"electronic-circuit": 2},
        allowed_sources=("copper-ore", "iron-ore"),
        source_caps={"copper-ore": 2, "iron-ore": 2},
    )

    assert result.status == "unique"
    assert result.feasible is False
    assert result.shortages == (SourceShortage("copper-ore", Fraction(3), Fraction(2)),)
    assert _source_rates(result) == {"copper-ore": Fraction(3), "iron-ore": Fraction(2)}
    assert _rates(result) == _rates(catalog_result)
    assert "exceed fixed source caps" in result.reason


def test_unknown_source_caps_are_not_evaluated_for_inconsistent_systems():
    catalog = _catalog(_recipe("split", [("a", 1), ("b", 1)], [("x", 1), ("y", 1)]))
    result = solve_fixed_recipe_balance(
        catalog,
        recipes=("split",),
        demand={"x": 1, "y": 2},
        allowed_sources=("a", "b"),
        source_caps={"a": 0, "b": 0},
    )

    assert result.status == "inconsistent"
    assert result.feasible is False
    assert result.shortages == ()
    assert result.source_rates is None


def test_unknown_source_selection_conflicts_fail_closed():
    catalog = _catalog(_recipe("x-from-y", [("y", 1)], [("x", 1)]), raw=("y",))
    with pytest.raises(FixedRecipeBalanceError, match="both a fixed source and an allowed unknown source"):
        solve_fixed_recipe_balance(
            catalog,
            recipes=("x-from-y",),
            demand={"x": 1},
            sources={"y": 1},
            allowed_sources=("y",),
        )
    with pytest.raises(FixedRecipeBalanceError, match="allowed unknown source .* is not consumed"):
        solve_fixed_recipe_balance(
            catalog,
            recipes=("x-from-y",),
            demand={"x": 1},
            sources={"y": 1},
            allowed_sources=("z",),
        )
    with pytest.raises(FixedRecipeBalanceError, match="non-string sequence"):
        solve_fixed_recipe_balance(
            catalog,
            recipes=("x-from-y",),
            demand={"x": 1},
            sources={"y": 1},
            allowed_sources="y",
        )
    with pytest.raises(FixedRecipeBalanceError, match="is repeated"):
        solve_fixed_recipe_balance(
            catalog,
            recipes=("x-from-y",),
            demand={"x": 1},
            sources={"y": 1},
            allowed_sources=("z", "z"),
        )


def test_unknown_source_cap_validation_fails_closed():
    catalog = _catalog(_recipe("x-from-y", [("y", 1)], [("x", 1)]), raw=("y",))
    with pytest.raises(FixedRecipeBalanceError, match="does not name an allowed unknown source"):
        solve_fixed_recipe_balance(
            catalog,
            recipes=("x-from-y",),
            demand={"x": 1},
            sources={"y": 1},
            source_caps={"y": 1},
        )
    with pytest.raises(FixedRecipeBalanceError, match="must not be negative"):
        solve_fixed_recipe_balance(
            catalog,
            recipes=("x-from-y",),
            demand={"x": 1},
            allowed_sources=("y",),
            source_caps={"y": -1},
        )
