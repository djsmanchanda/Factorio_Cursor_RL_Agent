# Path: tests/test_rocket_silo_supply.py
# Purpose: Verify scoped silo observations become explicit, non-authorizing component demand.

import copy
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import pytest

from core.catalog_demand import CatalogDemandError, mod_set_sha256
from core.fixed_recipe_machines import compute_fixed_recipe_machines
from core.rocket_silo_supply import plan_silo_supply
from tests.test_catalog_demand import _bytes, _catalog, _provenance, _recipe
from tests.test_rocket_silo_observation import _available, _success


def _scenario():
    catalog = _catalog(
        _recipe("rocket-part", [("component-a", 2), ("component-b", 1)], [("rocket-part", 1)]),
        _recipe("component-a", [("ore", 3)], [("component-a", 1)]),
        _recipe("component-b", [("ore", 2)], [("component-b", 1)]),
        raw=("ore",),
    )
    observation = _success()
    observation["surface"] = "test-surface"
    observation["requested"]["unit_number"] = _available(7)
    observation["silos"][0]["recipe"] = _available("rocket-part")
    data = _bytes(catalog)
    return catalog, observation, data, _provenance(data, catalog)


def _plan(data, observation, provenance, *, horizon=3600, cap=100):
    return plan_silo_supply(
        data, provenance=provenance, observation=observation, unit_number=7,
        horizon_ticks=horizon, boundaries=[{"name": "ore", "cap": cap}],
    )


def test_scoped_parts_form_exact_component_demand_and_shared_source_balance():
    _, observation, data, provenance = _scenario()
    result = _plan(data, observation, provenance)
    assert result.remaining_parts == 58
    assert dict(result.input_bill) == {"component-a": 116, "component-b": 58}
    assert dict(result.input_rates_per_second) == {
        "component-a": Fraction(29, 15), "component-b": Fraction(29, 30),
    }
    assert result.demand.balance.feasible is True
    assert dict(zip(result.demand.balance.source_names, result.demand.balance.source_rates)) == {
        "ore": Fraction(116, 15)
    }
    assert any("not verified runtime" in note for note in result.notes)


def test_source_shortage_remains_visible_not_a_scheduled_supply():
    _, observation, data, provenance = _scenario()
    result = _plan(data, observation, provenance, cap=1)
    assert result.demand.balance.feasible is False
    assert result.demand.balance.shortages


@pytest.mark.parametrize("damage", ["wrong_scope", "wrong_unit", "unfiltered", "missing_silo", "count",
                                    "absent_recipe", "wrong_recipe", "missing_parts", "ready", "catalog_future"])
def test_silo_must_be_scoped_identified_and_have_remaining_parts(damage):
    _, observation, data, provenance = _scenario()
    if damage == "wrong_scope":
        observation["surface"] = "other"
    elif damage == "wrong_unit":
        observation["silos"][0]["unit_number"] = _available(8)
    elif damage == "unfiltered":
        observation["requested"]["unit_number"] = {"state": "absent"}
    elif damage == "missing_silo":
        observation["silos"] = []
        observation["silo_count"] = 0
    elif damage == "count":
        observation["silo_count"] = 2
    elif damage == "absent_recipe":
        observation["silos"][0]["recipe"] = {"state": "absent"}
    elif damage == "wrong_recipe":
        observation["silos"][0]["recipe"] = _available("other")
    elif damage == "missing_parts":
        observation["silos"][0]["parts"]["actual"] = {"state": "unavailable", "reason": "unknown"}
    elif damage == "ready":
        observation["silos"][0]["parts"]["actual"] = _available(100)
    else:
        observation["tick"] = 99
    with pytest.raises(ValueError):
        _plan(data, observation, provenance)


@pytest.mark.parametrize("horizon", [0, -1, True, 1.5])
def test_invalid_horizon_rejected(horizon):
    _, observation, data, provenance = _scenario()
    with pytest.raises(ValueError, match="horizon"):
        _plan(data, observation, provenance, horizon=horizon)


@pytest.mark.parametrize("damage", ["disabled", "unsupported", "product", "fluid", "fractional", "duplicate"])
def test_unsupported_rocket_part_bill_fails_closed(damage):
    catalog, observation, _, _ = _scenario()
    recipe = catalog["recipes"][0]
    if damage == "disabled":
        recipe["enabled"] = False
    elif damage == "unsupported":
        recipe["supported"] = False
    elif damage == "product":
        recipe["products"][0]["amount"] = 2
    elif damage == "fluid":
        recipe["ingredients"][0]["type"] = "fluid"
    elif damage == "fractional":
        recipe["ingredients"][0]["amount"] = 0.5
    else:
        recipe["ingredients"].append(copy.deepcopy(recipe["ingredients"][0]))
    data = _bytes(catalog)
    with pytest.raises(ValueError):
        _plan(data, observation, _provenance(data, catalog))


def test_catalog_hash_mismatch_fails_closed():
    _, observation, data, provenance = _scenario()
    provenance["catalog_sha256"] = "0" * 64
    with pytest.raises(CatalogDemandError):
        _plan(data, observation, provenance)


def test_historical_recipe_chain_is_an_offline_synthetic_silo_scenario():
    path = Path("data/factorio-2.1.21/probes/deterministic/server/script-output/factorio_mod/recipe_catalogs/recipe_catalog_243688.json")
    if not path.is_file():
        pytest.skip("historical catalog unavailable")
    data = path.read_bytes()
    catalog = json.loads(data)
    mods = [{"name": "synthetic-test-mod", "version": "0.0.0"}]
    provenance = {"catalog_sha256": hashlib.sha256(data).hexdigest(), "game_version": "0.0.0",
                  "force": catalog["force"], "surface": "synthetic-surface", "export_tick": catalog["tick"],
                  "export_command": "/synthetic", "mods": mods, "mod_set_sha256": mod_set_sha256(mods)}
    observation = _success()
    observation.update(surface="synthetic-surface", tick=catalog["tick"] + 1)
    observation["requested"]["unit_number"] = _available(7)
    observation["silos"][0]["recipe"] = _available("rocket-part")
    boundaries = [{"name": name, "cap": 100} for name in (
        "light-oil", "solid-fuel", "advanced-circuit", "electronic-circuit",
        "sulfuric-acid", "copper-plate", "plastic-bar", "steel-plate")]
    names = ("rocket-fuel", "processing-unit", "low-density-structure")
    result = plan_silo_supply(data, provenance=provenance, observation=observation, unit_number=7,
                              horizon_ticks=3600, boundaries=boundaries,
                              preferences={name: name for name in names})
    assert dict(result.input_bill) == {name: 58 for name in names}
    assert set(result.demand.selected_recipes) == set(names)
    assert result.demand.balance.feasible is True
    machines = compute_fixed_recipe_machines(
        catalog, balance=result.demand.balance,
        machine_by_recipe={name: "assembling-machine-2" for name in names},
    )
    assert {fact.recipe: fact.machine_count for fact in machines.facts} == {
        "low-density-structure": 20, "processing-unit": 13, "rocket-fuel": 20,
    }
