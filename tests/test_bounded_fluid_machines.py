# Path: tests/test_bounded_fluid_machines.py
# Purpose: Verify the exact bounded-balance-to-fluid-machine adapter, including the historical 2.1.21 oil case and fail-closed adversarial changes.

from __future__ import annotations

import copy
from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path

import pytest

from core.bounded_fluid_machines import (
    BoundedFluidMachineError,
    FluidSourceDemand,
    compute_bounded_fluid_machines,
)
from core.bounded_recipe_balance import optimize_bounded_recipe_balance
from core.fixed_recipe_balance import RecipeSignature

REAL_CATALOG = (
    Path(__file__).resolve().parents[1]
    / "data/factorio-2.1.21/probes/deterministic/server/script-output/factorio_mod"
    / "recipe_catalogs/recipe_catalog_243688.json"
)


def _real_catalog() -> dict:
    if not REAL_CATALOG.exists():
        pytest.skip("historical 2.1.21 recipe catalog export is unavailable")
    return json.loads(REAL_CATALOG.read_text())


def _oil_balance(catalog: dict):
    return optimize_bounded_recipe_balance(
        catalog,
        recipes={
            "advanced-oil-processing": 10,
            "heavy-oil-cracking": 10,
            "light-oil-cracking": 10,
        },
        sources={"crude-oil": 300, "water": 400},
        demand={"petroleum-gas": 195},
        disposals={},
        objective={
            "recipes": {
                "advanced-oil-processing": 1,
                "heavy-oil-cracking": 1,
                "light-oil-cracking": 1,
            },
            "sources": {"crude-oil": -1, "water": -1},
            "disposals": {},
        },
    )


def _oil_speeds() -> dict[str, Fraction]:
    return {
        "advanced-oil-processing": Fraction(1),
        "heavy-oil-cracking": Fraction(1),
        "light-oil-cracking": Fraction(1),
    }


def _facts(result):
    return {fact.recipe: fact for fact in result.facts}


def _sources(result):
    return {source.fluid: source for source in result.fluid_sources}


def _small_catalog() -> dict:
    return {
        "version": "1.0.0",
        "force": "planner",
        "tick": 0,
        "raw_resources": ["ore"],
        "machines": [
            {
                "name": "assembling-machine-2",
                "type": "assembling-machine",
                "ingredient_count": 10,
                "crafting_speed": 0.75,
                "categories": ["crafting"],
            }
        ],
        "recipes": [
            {
                "name": "not-fluid",
                "enabled": True,
                "category": "crafting",
                "energy_ticks": 60,
                "ingredients": [{"name": "ore", "type": "item", "amount": 1}],
                "products": [{"name": "plate", "type": "item", "amount": 1}],
                "supported": True,
            }
        ],
    }


def _small_balance(catalog: dict):
    return optimize_bounded_recipe_balance(
        catalog,
        recipes={"not-fluid": 1},
        sources={"ore": 1},
        demand={"plate": 1},
        disposals={},
        objective={
            "recipes": {"not-fluid": 0},
            "sources": {"ore": 0},
            "disposals": {},
        },
    )


def test_historical_2_1_21_oil_cracking_machines_and_fluid_sources_are_exact():
    catalog = _real_catalog()
    balance = _oil_balance(catalog)
    result = compute_bounded_fluid_machines(
        catalog, balance=balance, machine_speeds=_oil_speeds()
    )

    facts = _facts(result)
    assert set(facts) == {
        "advanced-oil-processing",
        "heavy-oil-cracking",
        "light-oil-cracking",
    }

    advanced = facts["advanced-oil-processing"]
    assert advanced.machine == "oil-refinery"
    assert advanced.required_crafts_per_second == Fraction(2)
    assert advanced.craft_seconds == Fraction(5)
    assert advanced.machine_speed == Fraction(1)
    assert advanced.exact_machines == Fraction(10)
    assert advanced.machine_count == 10
    assert advanced.nominal_crafts_per_second == Fraction(2)
    assert advanced.spare_crafts_per_second == 0
    assert advanced.capacity_shortfall_crafts_per_second == 0

    heavy = facts["heavy-oil-cracking"]
    assert heavy.machine == "chemical-plant"
    assert heavy.required_crafts_per_second == Fraction(5, 4)
    assert heavy.craft_seconds == Fraction(2)
    assert heavy.exact_machines == Fraction(5, 2)
    assert heavy.machine_count == 3
    assert heavy.nominal_crafts_per_second == Fraction(3, 2)
    assert heavy.spare_crafts_per_second == Fraction(1, 4)
    assert heavy.capacity_shortfall_crafts_per_second == 0

    light = facts["light-oil-cracking"]
    assert light.machine == "chemical-plant"
    assert light.required_crafts_per_second == Fraction(17, 4)
    assert light.craft_seconds == Fraction(2)
    assert light.exact_machines == Fraction(17, 2)
    assert light.machine_count == 9
    assert light.nominal_crafts_per_second == Fraction(9, 2)
    assert light.spare_crafts_per_second == Fraction(1, 4)
    assert light.capacity_shortfall_crafts_per_second == 0

    assert result.total_machines == 22

    sources = _sources(result)
    assert set(sources) == {
        "crude-oil",
        "water",
        "heavy-oil",
        "light-oil",
        "petroleum-gas",
    }
    assert sources["crude-oil"] == FluidSourceDemand(
        fluid="crude-oil",
        consumption_rate=Fraction(200),
        external_source_rate=Fraction(200),
        internally_produced_rate=0,
        final_demand_rate=0,
        cap=Fraction(300),
        shortfall_rate=0,
    )
    assert sources["water"] == FluidSourceDemand(
        fluid="water",
        consumption_rate=Fraction(265),
        external_source_rate=Fraction(265),
        internally_produced_rate=0,
        final_demand_rate=0,
        cap=Fraction(400),
        shortfall_rate=0,
    )
    assert sources["heavy-oil"] == FluidSourceDemand(
        fluid="heavy-oil",
        consumption_rate=Fraction(50),
        external_source_rate=0,
        internally_produced_rate=Fraction(50),
        final_demand_rate=0,
        cap=None,
        shortfall_rate=0,
    )
    assert sources["light-oil"] == FluidSourceDemand(
        fluid="light-oil",
        consumption_rate=Fraction(255, 2),
        external_source_rate=0,
        internally_produced_rate=Fraction(255, 2),
        final_demand_rate=0,
        cap=None,
        shortfall_rate=0,
    )
    assert sources["petroleum-gas"] == FluidSourceDemand(
        fluid="petroleum-gas",
        consumption_rate=0,
        external_source_rate=0,
        internally_produced_rate=Fraction(195),
        final_demand_rate=Fraction(195),
        cap=None,
        shortfall_rate=0,
    )

    assert all(
        isinstance(value, Fraction)
        for fact in result.facts
        for value in (
            fact.required_crafts_per_second,
            fact.craft_seconds,
            fact.machine_speed,
            fact.exact_machines,
            fact.nominal_crafts_per_second,
            fact.spare_crafts_per_second,
            fact.capacity_shortfall_crafts_per_second,
        )
    )
    assert any("runtime" in claim for claim in result.non_claims)
    assert any("source site" in claim for claim in result.non_claims)


def test_item_ingredients_are_excluded_from_fluid_source_demands():
    catalog = _real_catalog()
    balance = optimize_bounded_recipe_balance(
        catalog,
        recipes={"plastic-bar": 5},
        sources={"coal": 2, "petroleum-gas": 40},
        demand={"plastic-bar": 4},
        disposals={},
        objective={
            "recipes": {"plastic-bar": 0},
            "sources": {"coal": 0, "petroleum-gas": 0},
            "disposals": {},
        },
    )
    assert balance.status == "optimal"
    result = compute_bounded_fluid_machines(
        catalog,
        balance=balance,
        machine_speeds={"plastic-bar": Fraction(1)},
    )

    fact = _facts(result)["plastic-bar"]
    assert fact.machine == "chemical-plant"
    assert fact.required_crafts_per_second == Fraction(2)
    assert fact.craft_seconds == Fraction(1)
    assert fact.exact_machines == Fraction(2)
    assert fact.machine_count == 2
    assert fact.nominal_crafts_per_second == Fraction(2)
    assert fact.capacity_shortfall_crafts_per_second == 0

    sources = _sources(result)
    assert set(sources) == {"petroleum-gas"}
    assert sources["petroleum-gas"].consumption_rate == Fraction(40)
    assert sources["petroleum-gas"].external_source_rate == Fraction(40)
    assert sources["petroleum-gas"].cap == Fraction(40)
    assert sources["petroleum-gas"].internally_produced_rate == 0
    assert sources["petroleum-gas"].final_demand_rate == 0
    assert sources["petroleum-gas"].shortfall_rate == 0


def test_requires_an_asserted_completed_optimal_balance():
    catalog = _real_catalog()
    balance = _oil_balance(catalog)

    exhausted = optimize_bounded_recipe_balance(
        catalog,
        recipes={
            "advanced-oil-processing": 10,
            "heavy-oil-cracking": 10,
            "light-oil-cracking": 10,
        },
        sources={"crude-oil": 300, "water": 400},
        demand={"petroleum-gas": 195},
        disposals={},
        objective={
            "recipes": {
                "advanced-oil-processing": 1,
                "heavy-oil-cracking": 1,
                "light-oil-cracking": 1,
            },
            "sources": {"crude-oil": -1, "water": -1},
            "disposals": {},
        },
        max_candidate_solves=0,
    )
    with pytest.raises(BoundedFluidMachineError, match="completed optimal solve"):
        compute_bounded_fluid_machines(
            catalog, balance=exhausted, machine_speeds=_oil_speeds()
        )

    with pytest.raises(BoundedFluidMachineError, match="completed optimal solve"):
        compute_bounded_fluid_machines(
            catalog,
            balance=replace(balance, optimal=False),
            machine_speeds=_oil_speeds(),
        )

    with pytest.raises(BoundedFluidMachineError, match="BoundedRecipeBalanceResult"):
        compute_bounded_fluid_machines(
            catalog, balance=object(), machine_speeds=_oil_speeds()
        )

    with pytest.raises(BoundedFluidMachineError, match="cannot independently re-derive"):
        compute_bounded_fluid_machines(
            catalog,
            balance=balance,
            machine_speeds=_oil_speeds(),
            verification_candidate_budget=0,
        )


def test_forged_feasible_but_suboptimal_balance_is_rejected():
    catalog = _real_catalog()
    balance = optimize_bounded_recipe_balance(
        catalog,
        recipes={"basic-oil-processing": 10, "plastic-bar": 10},
        sources={"crude-oil": 1000, "petroleum-gas": 1000, "coal": 1000},
        demand={"plastic-bar": 20},
        disposals={},
        objective={
            "recipes": {"basic-oil-processing": -1, "plastic-bar": 0},
            "sources": {"coal": 0, "crude-oil": -1, "petroleum-gas": 1},
            "disposals": {},
        },
    )
    assert balance.status == "optimal"
    assert balance.objective_value == Fraction(200)

    forged = replace(
        balance,
        recipe_rates=(Fraction(4), Fraction(10)),
        source_rates=(Fraction(10), Fraction(400), Fraction(20)),
        disposal_rates=(),
        material_net_rates=(Fraction(0), Fraction(0), Fraction(0), Fraction(20)),
        objective_value=Fraction(-384),
    )
    assert [material.name for material in forged.materials] == [
        "coal",
        "crude-oil",
        "petroleum-gas",
        "plastic-bar",
    ]

    with pytest.raises(BoundedFluidMachineError, match="feasible but suboptimal"):
        compute_bounded_fluid_machines(
            catalog,
            balance=forged,
            machine_speeds={
                "basic-oil-processing": Fraction(1),
                "plastic-bar": Fraction(1),
            },
        )


def test_external_source_may_exceed_machine_consumption_and_feed_final_demand():
    catalog = _real_catalog()
    balance = optimize_bounded_recipe_balance(
        catalog,
        recipes={"basic-oil-processing": 10, "plastic-bar": 10},
        sources={"crude-oil": 1000, "petroleum-gas": 1000, "coal": 1000},
        demand={"petroleum-gas": 50, "plastic-bar": 20},
        disposals={},
        objective={
            "recipes": {"basic-oil-processing": -1, "plastic-bar": 0},
            "sources": {"coal": 0, "crude-oil": -1, "petroleum-gas": 1},
            "disposals": {},
        },
    )
    assert balance.status == "optimal"
    assert balance.recipe_rates == (Fraction(0), Fraction(10))
    assert balance.source_rates == (Fraction(10), Fraction(0), Fraction(250))
    assert balance.objective_value == Fraction(250)

    result = compute_bounded_fluid_machines(
        catalog,
        balance=balance,
        machine_speeds={
            "basic-oil-processing": Fraction(1),
            "plastic-bar": Fraction(1),
        },
    )

    sources = _sources(result)
    assert set(sources) == {"crude-oil", "petroleum-gas"}
    assert sources["petroleum-gas"] == FluidSourceDemand(
        fluid="petroleum-gas",
        consumption_rate=Fraction(200),
        external_source_rate=Fraction(250),
        internally_produced_rate=Fraction(0),
        final_demand_rate=Fraction(50),
        cap=Fraction(1000),
        shortfall_rate=0,
    )
    assert sources["crude-oil"] == FluidSourceDemand(
        fluid="crude-oil",
        consumption_rate=0,
        external_source_rate=0,
        internally_produced_rate=0,
        final_demand_rate=0,
        cap=Fraction(1000),
        shortfall_rate=0,
    )


def test_internal_production_is_gross_not_consumption_minus_external():
    catalog = _real_catalog()
    balance = optimize_bounded_recipe_balance(
        catalog,
        recipes={"basic-oil-processing": 10, "plastic-bar": 10},
        sources={"crude-oil": 200, "petroleum-gas": 1000, "coal": 1000},
        demand={"petroleum-gas": 50, "plastic-bar": 20},
        disposals={},
        objective={
            "recipes": {"basic-oil-processing": 1, "plastic-bar": 0},
            "sources": {"coal": 0, "crude-oil": 0, "petroleum-gas": -1},
            "disposals": {},
        },
    )
    assert balance.status == "optimal"
    assert balance.recipe_rates == (Fraction(2), Fraction(10))
    assert balance.source_rates == (Fraction(10), Fraction(200), Fraction(160))
    assert balance.objective_value == Fraction(-158)

    result = compute_bounded_fluid_machines(
        catalog,
        balance=balance,
        machine_speeds={
            "basic-oil-processing": Fraction(1),
            "plastic-bar": Fraction(1),
        },
    )

    sources = _sources(result)
    assert sources["petroleum-gas"] == FluidSourceDemand(
        fluid="petroleum-gas",
        consumption_rate=Fraction(200),
        external_source_rate=Fraction(160),
        internally_produced_rate=Fraction(90),
        final_demand_rate=Fraction(50),
        cap=Fraction(1000),
        shortfall_rate=0,
    )
    assert sources["petroleum-gas"].internally_produced_rate != (
        sources["petroleum-gas"].consumption_rate
        - sources["petroleum-gas"].external_source_rate
    )


def test_machine_speed_must_be_explicit_and_match_catalog_and_fixed_facts():
    catalog = _real_catalog()
    balance = _oil_balance(catalog)

    missing = _oil_speeds()
    missing.pop("light-oil-cracking")
    with pytest.raises(BoundedFluidMachineError, match="machine_speeds is incomplete"):
        compute_bounded_fluid_machines(catalog, balance=balance, machine_speeds=missing)

    extra = _oil_speeds()
    extra["bogus"] = Fraction(1)
    with pytest.raises(BoundedFluidMachineError, match="unselected recipe"):
        compute_bounded_fluid_machines(catalog, balance=balance, machine_speeds=extra)

    wrong = _oil_speeds()
    wrong["heavy-oil-cracking"] = Fraction(2)
    with pytest.raises(BoundedFluidMachineError, match="FLUID_MACHINE_SPEEDS"):
        compute_bounded_fluid_machines(catalog, balance=balance, machine_speeds=wrong)

    slower_catalog = copy.deepcopy(catalog)
    for machine in slower_catalog["machines"]:
        if machine["name"] == "chemical-plant":
            machine["crafting_speed"] = 2
    with pytest.raises(BoundedFluidMachineError, match="catalog machine 'chemical-plant'"):
        compute_bounded_fluid_machines(
            slower_catalog, balance=balance, machine_speeds=_oil_speeds()
        )

    no_machines = copy.deepcopy(catalog)
    del no_machines["machines"]
    with pytest.raises(BoundedFluidMachineError, match="nonempty machines list"):
        compute_bounded_fluid_machines(
            no_machines, balance=balance, machine_speeds=_oil_speeds()
        )


def test_catalog_recipe_identity_and_craft_time_disagreements_fail_closed():
    catalog = _real_catalog()
    balance = _oil_balance(catalog)

    wrong_ingredient = copy.deepcopy(catalog)
    for recipe in wrong_ingredient["recipes"]:
        if recipe["name"] == "advanced-oil-processing":
            recipe["ingredients"][0]["amount"] = 101
    with pytest.raises(BoundedFluidMachineError, match="ingredients disagree"):
        compute_bounded_fluid_machines(
            wrong_ingredient, balance=balance, machine_speeds=_oil_speeds()
        )

    wrong_product = copy.deepcopy(catalog)
    for recipe in wrong_product["recipes"]:
        if recipe["name"] == "advanced-oil-processing":
            recipe["products"][2]["amount"] = 56
    with pytest.raises(BoundedFluidMachineError, match="products disagree"):
        compute_bounded_fluid_machines(
            wrong_product, balance=balance, machine_speeds=_oil_speeds()
        )

    wrong_type = copy.deepcopy(catalog)
    for recipe in wrong_type["recipes"]:
        if recipe["name"] == "advanced-oil-processing":
            recipe["ingredients"][1]["type"] = "item"
    with pytest.raises(BoundedFluidMachineError, match="ingredients disagree"):
        compute_bounded_fluid_machines(
            wrong_type, balance=balance, machine_speeds=_oil_speeds()
        )

    wrong_time = copy.deepcopy(catalog)
    for recipe in wrong_time["recipes"]:
        if recipe["name"] == "advanced-oil-processing":
            recipe["energy_ticks"] = 240
    with pytest.raises(BoundedFluidMachineError, match="craft time disagrees"):
        compute_bounded_fluid_machines(
            wrong_time, balance=balance, machine_speeds=_oil_speeds()
        )

    reordered = copy.deepcopy(catalog)
    for recipe in reordered["recipes"]:
        if recipe["name"] == "advanced-oil-processing":
            recipe["ingredients"].reverse()
            recipe["products"].reverse()
    result = compute_bounded_fluid_machines(
        reordered, balance=balance, machine_speeds=_oil_speeds()
    )
    assert result.total_machines == 22


def test_recipe_outside_supported_fluid_recipes_fails_closed():
    catalog = _small_catalog()
    balance = _small_balance(catalog)
    assert balance.status == "optimal"
    with pytest.raises(BoundedFluidMachineError, match="not a fully supported FLUID_RECIPES"):
        compute_bounded_fluid_machines(
            catalog,
            balance=balance,
            machine_speeds={"not-fluid": Fraction(3, 4)},
        )


def test_nonzero_disposal_is_rejected():
    catalog = _real_catalog()
    balance = optimize_bounded_recipe_balance(
        catalog,
        recipes={"advanced-oil-processing": 5},
        sources={"crude-oil": 200, "water": 100},
        demand={"petroleum-gas": 55},
        disposals={"heavy-oil": 25, "light-oil": 45},
        objective={
            "recipes": {"advanced-oil-processing": 0},
            "sources": {"crude-oil": 0, "water": 0},
            "disposals": {"heavy-oil": 0, "light-oil": 0},
        },
    )
    assert balance.status == "optimal"
    assert balance.disposal_rates == (Fraction(25), Fraction(45))
    with pytest.raises(BoundedFluidMachineError, match="nonzero disposal"):
        compute_bounded_fluid_machines(
            catalog,
            balance=balance,
            machine_speeds={"advanced-oil-processing": Fraction(1)},
        )


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("enabled", False, "not enabled"),
        ("supported", False, "not supported"),
        ("effects", {"speed": 1}, "unsupported effects"),
    ],
)
def test_unsupported_recipe_changes_fail_closed(field, value, message):
    catalog = _real_catalog()
    balance = _oil_balance(catalog)
    changed = copy.deepcopy(catalog)
    for recipe in changed["recipes"]:
        if recipe["name"] == "advanced-oil-processing":
            recipe[field] = value
    with pytest.raises(BoundedFluidMachineError, match=message):
        compute_bounded_fluid_machines(
            changed, balance=balance, machine_speeds=_oil_speeds()
        )


@pytest.mark.parametrize("field,value,message", [
    ("categories", ["crafting"], "does not support recipe category"),
    ("categories", [], "does not support recipe category"),
    ("categories", {"oil-processing": True}, "categories must be a list"),
    ("categories", [""], "categories"),
    ("ingredient_count", 1, "ingredient slots"),
    ("ingredient_count", True, "ingredient_count must be"),
    ("ingredient_count", -1, "ingredient_count must be"),
    ("type", "furnace", "must be an assembling-machine"),
])
def test_machine_compatibility_is_required(field, value, message):
    catalog = _real_catalog()
    balance = _oil_balance(catalog)
    changed = copy.deepcopy(catalog)
    machine = next(machine for machine in changed["machines"] if machine["name"] == "oil-refinery")
    machine[field] = value
    with pytest.raises(BoundedFluidMachineError, match=message):
        compute_bounded_fluid_machines(changed, balance=balance, machine_speeds=_oil_speeds())


@pytest.mark.parametrize("category", ["crafting", "", None])
def test_selected_recipe_requires_compatible_known_category(category):
    catalog = _real_catalog()
    balance = _oil_balance(catalog)
    changed = copy.deepcopy(catalog)
    recipe = next(recipe for recipe in changed["recipes"] if recipe["name"] == "advanced-oil-processing")
    recipe["category"] = category
    with pytest.raises(BoundedFluidMachineError, match="categor"):
        compute_bounded_fluid_machines(changed, balance=balance, machine_speeds=_oil_speeds())


@pytest.mark.parametrize("categories,valid", [
    (["crafting", "oil-processing"], True),
    (["crafting"], False),
    (["oil-processing"], False),
    (["crafting", "oil-processing", "oil-processing"], False),
    ([], False),
])
def test_selected_recipe_category_sets(categories, valid):
    catalog = _real_catalog()
    balance = _oil_balance(catalog)
    recipe = next(recipe for recipe in catalog["recipes"] if recipe["name"] == "advanced-oil-processing")
    recipe["category"] = "crafting"
    recipe["categories"] = categories
    if valid:
        compute_bounded_fluid_machines(catalog, balance=balance, machine_speeds=_oil_speeds())
    else:
        with pytest.raises(BoundedFluidMachineError):
            compute_bounded_fluid_machines(catalog, balance=balance, machine_speeds=_oil_speeds())


def test_exact_ingredient_limit_is_sufficient():
    catalog = _real_catalog()
    balance = _oil_balance(catalog)
    changed = copy.deepcopy(catalog)
    for machine in changed["machines"]:
        if machine["name"] in {"oil-refinery", "chemical-plant"}:
            machine["ingredient_count"] = 2
    result = compute_bounded_fluid_machines(changed, balance=balance, machine_speeds=_oil_speeds())
    assert result.total_machines == 22


def test_malformed_or_inconsistent_balance_results_fail_closed():
    catalog = _real_catalog()
    balance = _oil_balance(catalog)

    with pytest.raises(BoundedFluidMachineError, match="must not be negative"):
        compute_bounded_fluid_machines(
            catalog,
            balance=replace(
                balance,
                recipe_rates=(Fraction(-1), Fraction(5, 4), Fraction(17, 4)),
            ),
            machine_speeds=_oil_speeds(),
        )

    with pytest.raises(BoundedFluidMachineError, match="exceeds its explicit cap"):
        compute_bounded_fluid_machines(
            catalog,
            balance=replace(
                balance,
                recipe_rates=(Fraction(11), Fraction(5, 4), Fraction(17, 4)),
            ),
            machine_speeds=_oil_speeds(),
        )

    with pytest.raises(BoundedFluidMachineError, match="same length"):
        compute_bounded_fluid_machines(
            catalog,
            balance=replace(balance, recipe_rates=(Fraction(2),)),
            machine_speeds=_oil_speeds(),
        )

    with pytest.raises(BoundedFluidMachineError, match="exact material equation"):
        compute_bounded_fluid_machines(
            catalog,
            balance=replace(
                balance,
                recipe_rates=(Fraction(3), Fraction(5, 4), Fraction(17, 4)),
                objective_value=balance.objective_value + 1,
            ),
            machine_speeds=_oil_speeds(),
        )

    bad_net = list(balance.material_net_rates)
    bad_net[0] += 1
    with pytest.raises(BoundedFluidMachineError, match="net rate"):
        compute_bounded_fluid_machines(
            catalog,
            balance=replace(balance, material_net_rates=tuple(bad_net)),
            machine_speeds=_oil_speeds(),
        )

    with pytest.raises(BoundedFluidMachineError, match="materials do not match"):
        compute_bounded_fluid_machines(
            catalog,
            balance=replace(
                balance,
                materials=(
                    replace(balance.materials[0], type="item"),
                    *balance.materials[1:],
                ),
            ),
            machine_speeds=_oil_speeds(),
        )

    with pytest.raises(BoundedFluidMachineError, match="objective_value"):
        compute_bounded_fluid_machines(
            catalog,
            balance=replace(balance, objective_value=balance.objective_value + 1),
            machine_speeds=_oil_speeds(),
        )

    bad_parts = list(balance.recipes)
    originally = bad_parts[0]
    bad_parts[0] = replace(
        originally,
        signature=RecipeSignature.from_parts(
            originally.name,
            [
                (part.name, part.type, part.amount)
                for part in originally.signature.ingredients
            ],
            [
                ("petroleum-gas", "fluid", Fraction(56)),
                *[
                    (part.name, part.type, part.amount)
                    for part in originally.signature.products
                    if part.name != "petroleum-gas"
                ],
            ],
        ),
    )
    with pytest.raises(BoundedFluidMachineError, match="provenance"):
        compute_bounded_fluid_machines(
            catalog,
            balance=replace(balance, recipes=tuple(bad_parts)),
            machine_speeds=_oil_speeds(),
        )
