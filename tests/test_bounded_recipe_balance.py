# Path: tests/test_bounded_recipe_balance.py
# Purpose: Verify the bounded catalog material optimizer against exact equations, caps, objectives, and inventory provenance.

from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path

import pytest

from core.bounded_linear import MAX_CANDIDATE_BUDGET
from core.bounded_recipe_balance import (
    BoundedRecipeBalanceError,
    BoundedRecipeBalanceResult,
    MaterialIdentity,
    optimize_bounded_recipe_balance,
)

REAL_CATALOG = (
    Path(__file__).resolve().parents[1]
    / "data/factorio-2.1.21/probes/deterministic/server/script-output/factorio_mod"
    / "recipe_catalogs/recipe_catalog_243688.json"
)


def _part(name: str, amount: int | float, kind: str = "item") -> dict:
    return {"name": name, "type": kind, "amount": amount}


def _recipe(
    name: str,
    ingredients: list[tuple],
    products: list[tuple],
    *,
    enabled: bool = True,
    supported: bool = True,
    unsupported_reason: str | None = None,
) -> dict:
    def parts(entries: list[tuple]) -> list[dict]:
        return [_part(entry[0], entry[1], entry[2] if len(entry) > 2 else "item") for entry in entries]

    recipe = {
        "name": name,
        "enabled": enabled,
        "category": "crafting",
        "energy_ticks": 60,
        "ingredients": parts(ingredients),
        "products": parts(products),
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


def _objective(
    recipes: dict[str, int | Fraction],
    sources: dict[str, int | Fraction],
    disposals: dict[str, int | Fraction] | None = None,
) -> dict:
    return {
        "recipes": recipes,
        "sources": sources,
        "disposals": {} if disposals is None else disposals,
    }


def _zero_objective(
    recipes: dict[str, object],
    sources: dict[str, object],
    disposals: dict[str, object] | None = None,
) -> dict:
    return _objective(
        {name: 0 for name in recipes},
        {name: 0 for name in sources},
        {name: 0 for name in (disposals or {})},
    )


def _recipe_rates(result: BoundedRecipeBalanceResult) -> dict[str, Fraction]:
    assert result.recipe_rates is not None
    return {decision.name: rate for decision, rate in zip(result.recipes, result.recipe_rates)}


def _source_rates(result: BoundedRecipeBalanceResult) -> dict[str, Fraction]:
    assert result.source_rates is not None
    return {decision.material.name: rate for decision, rate in zip(result.sources, result.source_rates)}


def _disposal_rates(result: BoundedRecipeBalanceResult) -> dict[str, Fraction]:
    assert result.disposal_rates is not None
    return {
        decision.material.name: rate
        for decision, rate in zip(result.disposals, result.disposal_rates)
    }


def _net_rates(result: BoundedRecipeBalanceResult) -> dict[str, Fraction]:
    assert result.material_net_rates is not None
    return {
        material.name: rate
        for material, rate in zip(result.materials, result.material_net_rates)
    }


def test_historical_2_1_21_oil_processing_and_cracking_balances_exactly():
    if not REAL_CATALOG.exists():
        pytest.skip("historical 2.1.21 recipe catalog export is unavailable")
    catalog = json.loads(REAL_CATALOG.read_text())

    result = optimize_bounded_recipe_balance(
        catalog,
        recipes={
            "advanced-oil-processing": 10,
            "heavy-oil-cracking": 10,
            "light-oil-cracking": 10,
        },
        sources={"crude-oil": 300, "water": 400},
        demand={"petroleum-gas": 195},
        disposals={},
        objective=_objective(
            {"advanced-oil-processing": 1, "heavy-oil-cracking": 1, "light-oil-cracking": 1},
            {"crude-oil": -1, "water": -1},
        ),
    )

    assert result.status == "optimal"
    assert result.optimal is True
    assert result.candidates_evaluated == 1
    assert _recipe_rates(result) == {
        "advanced-oil-processing": Fraction(2),
        "heavy-oil-cracking": Fraction(5, 4),
        "light-oil-cracking": Fraction(17, 4),
    }
    assert _source_rates(result) == {
        "crude-oil": Fraction(200),
        "water": Fraction(265),
    }
    assert _disposal_rates(result) == {}
    assert result.objective_value == Fraction(-915, 2)
    assert _net_rates(result) == {
        "crude-oil": Fraction(0),
        "heavy-oil": Fraction(0),
        "light-oil": Fraction(0),
        "petroleum-gas": Fraction(195),
        "water": Fraction(0),
    }
    assert {material.name: material.type for material in result.materials} == {
        "crude-oil": "fluid",
        "heavy-oil": "fluid",
        "light-oil": "fluid",
        "petroleum-gas": "fluid",
        "water": "fluid",
    }
    assert all(signature.recipe for signature in (decision.signature for decision in result.recipes))


def test_synthetic_coproduct_balances_exactly_and_retains_types():
    catalog = _catalog(_recipe("split", [("crude-oil", 2, "fluid")], [("a", 2), ("b", 1)]))
    result = optimize_bounded_recipe_balance(
        catalog,
        recipes={"split": 5},
        sources={"crude-oil": 10},
        demand={"a": 2, "b": 1},
        disposals={},
        objective=_objective({"split": 1}, {"crude-oil": -1}),
    )

    assert result.status == "optimal"
    assert result.optimal is True
    assert _recipe_rates(result) == {"split": Fraction(1)}
    assert _source_rates(result) == {"crude-oil": Fraction(2)}
    assert result.objective_value == Fraction(-1)
    assert _net_rates(result) == {
        "a": Fraction(2),
        "b": Fraction(1),
        "crude-oil": Fraction(0),
    }
    assert result.materials == (
        MaterialIdentity("a", "item"),
        MaterialIdentity("b", "item"),
        MaterialIdentity("crude-oil", "fluid"),
    )


def test_infeasible_source_cap_is_not_reported_as_optimal_or_feasible():
    catalog = _catalog(_recipe("split", [("crude-oil", 2, "fluid")], [("a", 2), ("b", 1)]))
    result = optimize_bounded_recipe_balance(
        catalog,
        recipes={"split": 5},
        sources={"crude-oil": 1},
        demand={"a": 2, "b": 1},
        disposals={},
        objective=_zero_objective({"split": 5}, {"crude-oil": 1}),
    )

    assert result.status == "infeasible"
    assert result.optimal is False
    assert result.objective_value is None
    assert result.recipe_rates is None
    assert result.source_rates is None
    assert result.material_net_rates is None


def test_competing_alternatives_follow_the_explicit_objective_only():
    catalog = _catalog(
        _recipe("x-from-a", [("a", 1)], [("x", 1)]),
        _recipe("x-from-b", [("b", 2)], [("x", 1)]),
    )
    common = dict(
        catalog=catalog,
        recipes={"x-from-a": 10, "x-from-b": 10},
        sources={"a": 10, "b": 20},
        demand={"x": 10},
        disposals={},
    )

    prefer_a = optimize_bounded_recipe_balance(
        **common,
        objective=_objective(
            {"x-from-a": 0, "x-from-b": 0},
            {"a": 0, "b": -1},
        ),
    )
    assert prefer_a.status == "optimal"
    assert _recipe_rates(prefer_a) == {"x-from-a": Fraction(10), "x-from-b": Fraction(0)}
    assert _source_rates(prefer_a) == {"a": Fraction(10), "b": Fraction(0)}
    assert prefer_a.objective_value == Fraction(0)

    prefer_b = optimize_bounded_recipe_balance(
        **common,
        objective=_objective(
            {"x-from-a": 0, "x-from-b": 0},
            {"a": -1, "b": 0},
        ),
    )
    assert prefer_b.status == "optimal"
    assert _recipe_rates(prefer_b) == {"x-from-a": Fraction(0), "x-from-b": Fraction(10)}
    assert _source_rates(prefer_b) == {"a": Fraction(0), "b": Fraction(20)}
    assert prefer_b.objective_value == Fraction(0)
    assert prefer_b.recipes[1].objective_coefficient == 0


def test_disposal_is_explicitly_bounded_and_required_for_surplus():
    catalog = _catalog(_recipe("split", [("crude-oil", 2, "fluid")], [("a", 2), ("b", 1)]))
    result = optimize_bounded_recipe_balance(
        catalog,
        recipes={"split": 5},
        sources={"crude-oil": 10},
        demand={"a": 2},
        disposals={"b": 1},
        objective=_objective({"split": 0}, {"crude-oil": -1}, {"b": -1}),
    )
    assert result.status == "optimal"
    assert _recipe_rates(result) == {"split": Fraction(1)}
    assert _source_rates(result) == {"crude-oil": Fraction(2)}
    assert _disposal_rates(result) == {"b": Fraction(1)}
    assert result.objective_value == Fraction(-3)
    assert _net_rates(result)["b"] == Fraction(0)

    capped = optimize_bounded_recipe_balance(
        catalog,
        recipes={"split": 5},
        sources={"crude-oil": 10},
        demand={"a": 2},
        disposals={"b": Fraction(1, 2)},
        objective=_zero_objective({"split": 5}, {"crude-oil": 10}, {"b": 1}),
    )
    assert capped.status == "infeasible"
    assert capped.optimal is False

    with pytest.raises(BoundedRecipeBalanceError, match="explicitly bounded disposal"):
        optimize_bounded_recipe_balance(
            catalog,
            recipes={"split": 5},
            sources={"crude-oil": 10},
            demand={"a": 2},
            disposals={},
            objective=_zero_objective({"split": 5}, {"crude-oil": 10}),
        )


def test_low_budget_never_claims_optimality():
    catalog = _catalog(
        _recipe("x-from-a", [("a", 1)], [("x", 1)]),
        _recipe("x-from-b", [("b", 2)], [("x", 1)]),
    )
    objective = _objective(
        {"x-from-a": 0, "x-from-b": 0},
        {"a": -1, "b": 0},
    )

    empty = optimize_bounded_recipe_balance(
        catalog,
        recipes={"x-from-a": 10, "x-from-b": 10},
        sources={"a": 10, "b": 20},
        demand={"x": 10},
        disposals={},
        objective=objective,
        max_candidate_solves=0,
    )
    assert empty.status == "budget_exhausted"
    assert empty.optimal is False
    assert empty.candidates_evaluated == 0
    assert empty.candidate_budget == 0
    assert empty.recipe_rates is None
    assert "no verified incumbent" in empty.reason

    partial = optimize_bounded_recipe_balance(
        catalog,
        recipes={"x-from-a": 10, "x-from-b": 10},
        sources={"a": 10, "b": 20},
        demand={"x": 10},
        disposals={},
        objective=objective,
        max_candidate_solves=1,
    )
    assert partial.status == "budget_exhausted"
    assert partial.optimal is False
    assert partial.candidates_evaluated == 1
    assert partial.candidate_budget == 1
    assert "optimality is not claimed" in partial.reason


def test_fractional_catalog_amounts_and_float_caps_are_converted_exactly():
    catalog = _catalog(_recipe("half-output", [("ore", 1)], [("plate", 0.5)]))
    result = optimize_bounded_recipe_balance(
        catalog,
        recipes={"half-output": 5.0},
        sources={"ore": 10.0},
        demand={"plate": 1},
        disposals={},
        objective=_zero_objective({"half-output": 5.0}, {"ore": 10.0}),
    )

    assert result.status == "optimal"
    assert _recipe_rates(result) == {"half-output": Fraction(2)}
    assert _source_rates(result) == {"ore": Fraction(2)}
    assert result.recipes[0].cap == Fraction(5)


def test_objective_must_exactly_cover_every_decision():
    catalog = _catalog(_recipe("x-from-a", [("a", 1)], [("x", 1)]))
    base = dict(
        catalog=catalog,
        recipes={"x-from-a": 1},
        sources={"a": 1},
        demand={"x": 1},
        disposals={},
    )

    with pytest.raises(BoundedRecipeBalanceError, match="missing 'x-from-a'"):
        optimize_bounded_recipe_balance(**base, objective=_objective({}, {"a": 0}))
    with pytest.raises(BoundedRecipeBalanceError, match="unexpected 'bogus'"):
        optimize_bounded_recipe_balance(
            **base,
            objective=_objective({"x-from-a": 0, "bogus": 1}, {"a": 0}),
        )
    with pytest.raises(BoundedRecipeBalanceError, match="missing 'disposals'"):
        optimize_bounded_recipe_balance(
            **base,
            objective={"recipes": {"x-from-a": 0}, "sources": {"a": 0}},
        )
    with pytest.raises(BoundedRecipeBalanceError, match="objective\\['sources'\\]"):
        optimize_bounded_recipe_balance(
            **base,
            objective=_objective({"x-from-a": 0}, {}),
        )


def test_missing_boundaries_and_names_fail_closed():
    catalog = _catalog(_recipe("y-to-x", [("y", 1)], [("x", 1)]))
    with pytest.raises(BoundedRecipeBalanceError, match="no explicitly bounded source"):
        optimize_bounded_recipe_balance(
            catalog,
            recipes={"y-to-x": 1},
            sources={},
            demand={"x": 1},
            disposals={},
            objective=_zero_objective({"y-to-x": 1}, {}),
        )
    with pytest.raises(BoundedRecipeBalanceError, match="source 'z' is not consumed"):
        optimize_bounded_recipe_balance(
            catalog,
            recipes={"y-to-x": 1},
            sources={"y": 1, "z": 1},
            demand={"x": 1},
            disposals={},
            objective=_zero_objective({"y-to-x": 1}, {"y": 1, "z": 1}),
        )
    with pytest.raises(BoundedRecipeBalanceError, match="fixed net demand 'z' is not produced"):
        optimize_bounded_recipe_balance(
            catalog,
            recipes={"y-to-x": 1},
            sources={"y": 1},
            demand={"x": 1, "z": 1},
            disposals={},
            objective=_zero_objective({"y-to-x": 1}, {"y": 1}),
        )
    with pytest.raises(BoundedRecipeBalanceError, match="disposal target 'z' is not produced"):
        optimize_bounded_recipe_balance(
            catalog,
            recipes={"y-to-x": 1},
            sources={"y": 1},
            demand={"x": 1},
            disposals={"z": 1},
            objective=_zero_objective({"y-to-x": 1}, {"y": 1}, {"z": 1}),
        )
    with pytest.raises(BoundedRecipeBalanceError, match="both explicit sources"):
        optimize_bounded_recipe_balance(
            catalog,
            recipes={"y-to-x": 1},
            sources={"y": 1},
            demand={"x": 1},
            disposals={"y": 1},
            objective=_zero_objective({"y-to-x": 1}, {"y": 1}, {"y": 1}),
        )


@pytest.mark.parametrize(
    "recipe, message",
    [
        (_recipe("disabled", [("y", 1)], [("x", 1)], enabled=False), "not enabled"),
        (_recipe("unsupported", [("y", 1)], [("x", 1)], supported=False), "not supported"),
        (_recipe("free", [], [("x", 1)]), "free production is unsupported"),
        (_recipe("catalyst", [("a", 1)], [("a", 1)]), "catalyst or self-referential"),
    ],
)
def test_unsupported_recipes_fail_closed(recipe, message):
    catalog = _catalog(recipe, raw=("y",))
    sources = {} if recipe["name"] == "free" else {"y": 1}
    with pytest.raises(BoundedRecipeBalanceError, match=message):
        optimize_bounded_recipe_balance(
            catalog,
            recipes={recipe["name"]: 1},
            sources=sources,
            demand={"x": 1},
            disposals={},
            objective=_zero_objective({recipe["name"]: 1}, sources),
        )


@pytest.mark.parametrize("field, value", [("probability", 0.5), ("temperature", 150)])
def test_recipe_level_probability_or_temperature_fields_fail_closed(field, value):
    recipe = _recipe("x-from-y", [("y", 1)], [("x", 1)])
    recipe[field] = value
    catalog = _catalog(recipe, raw=("y",))
    with pytest.raises(BoundedRecipeBalanceError, match="unsupported fields"):
        optimize_bounded_recipe_balance(
            catalog,
            recipes={"x-from-y": 1},
            sources={"y": 1},
            demand={"x": 1},
            disposals={},
            objective=_zero_objective({"x-from-y": 1}, {"y": 1}),
        )


@pytest.mark.parametrize("field, value", [("probability", 0.5), ("temperature", 150)])
def test_part_level_probability_or_temperature_fields_fail_closed(field, value):
    recipe = _recipe("x-from-y", [("y", 1)], [("x", 1)])
    recipe["products"][0][field] = value
    catalog = _catalog(recipe, raw=("y",))
    with pytest.raises(BoundedRecipeBalanceError, match="unsupported fields"):
        optimize_bounded_recipe_balance(
            catalog,
            recipes={"x-from-y": 1},
            sources={"y": 1},
            demand={"x": 1},
            disposals={},
            objective=_zero_objective({"x-from-y": 1}, {"y": 1}),
        )


def test_item_and_fluid_identity_collision_fails_closed():
    catalog = _catalog(
        _recipe("make-x", [("ore", 1)], [("x", 1)]),
        _recipe("x-to-y", [("x", 1, "fluid")], [("y", 1)]),
    )
    with pytest.raises(BoundedRecipeBalanceError, match="declared as both item and fluid"):
        optimize_bounded_recipe_balance(
            catalog,
            recipes={"make-x": 1, "x-to-y": 1},
            sources={"ore": 1},
            demand={"y": 1},
            disposals={},
            objective=_zero_objective({"make-x": 1, "x-to-y": 1}, {"ore": 1}),
        )


def test_duplicate_catalog_recipe_names_fail_closed():
    duplicate = _recipe("same", [("y", 1)], [("x", 1)])
    catalog = _catalog(duplicate, dict(duplicate), raw=("y",))
    with pytest.raises(BoundedRecipeBalanceError, match="ambiguous duplicate recipe"):
        optimize_bounded_recipe_balance(
            catalog,
            recipes={"same": 1},
            sources={"y": 1},
            demand={"x": 1},
            disposals={},
            objective=_zero_objective({"same": 1}, {"y": 1}),
        )


def test_invalid_bounds_objectives_and_budgets_are_rejected():
    catalog = _catalog(_recipe("x-from-y", [("y", 1)], [("x", 1)]))
    base = dict(
        catalog=catalog,
        recipes={"x-from-y": 1},
        sources={"y": 1},
        demand={"x": 1},
        disposals={},
    )
    with pytest.raises(BoundedRecipeBalanceError, match="must not be negative"):
        optimize_bounded_recipe_balance(
            **{**base, "recipes": {"x-from-y": -1}},
            objective=_zero_objective({"x-from-y": 1}, {"y": 1}),
        )
    with pytest.raises(BoundedRecipeBalanceError, match="must not be negative"):
        optimize_bounded_recipe_balance(
            **{**base, "sources": {"y": -1}},
            objective=_zero_objective({"x-from-y": 1}, {"y": 1}),
        )
    with pytest.raises(BoundedRecipeBalanceError, match="must be an int"):
        optimize_bounded_recipe_balance(
            **base,
            objective=_zero_objective({"x-from-y": 1}, {"y": 1}),
            max_candidate_solves=True,
        )
    with pytest.raises(BoundedRecipeBalanceError, match="may not exceed"):
        optimize_bounded_recipe_balance(
            **base,
            objective=_zero_objective({"x-from-y": 1}, {"y": 1}),
            max_candidate_solves=MAX_CANDIDATE_BUDGET + 1,
        )
