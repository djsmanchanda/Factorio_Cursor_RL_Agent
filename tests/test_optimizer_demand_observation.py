# Path: tests/test_optimizer_demand_observation.py | Purpose: Relate fluid proposals to exact scoped recipe demand without asserting supply.
from types import SimpleNamespace
import copy

import pytest

from tests.test_catalog_demand import _bytes, _catalog, _provenance, _recipe
from tools.optimizer_demand_observation import make_phase_fluid_demand_observer


@pytest.fixture
def inputs():
    catalog = _catalog(
        _recipe("plastic-bar", [("coal", 1), ("petroleum-gas", 20, "fluid")], [("plastic-bar", 2)]),
        _recipe("other", [("petroleum-gas", 3, "fluid")], [("other", 1)]))
    raw = _bytes(catalog)
    mods = [{"name": "base", "version": "2.1.21"}]
    provenance = _provenance(raw, catalog, mods=mods, surface="nauvis")
    model = {"catalog_json": raw.decode(), "provenance": provenance,
             "boundaries": [{"name": "coal", "cap": 10}, {"name": "petroleum-gas", "cap": 100}],
             "preferences": {}}
    scope = SimpleNamespace(surface="nauvis", force="player")
    bound = SimpleNamespace(plan=SimpleNamespace(throughput_output="petroleum-gas", throughput_unit="fluid/second",
                                                 binding=SimpleNamespace(scope=scope)))
    facts = {"surface": "nauvis", "force": "player", "tick": 100, "mods": {"base": "2.1.21"},
             "enabled_recipes": {"plastic-bar": True, "other": True}}
    return bound, model, facts


def test_fluid_input_is_exact_model_demand_not_observed_spare_capacity(inputs):
    bound, model, facts = inputs
    observe = make_phase_fluid_demand_observer(bound, model)
    result = observe(facts, {"targets": {"plastic-bar": .5}})
    assert result["status"] == "required_by_selected_recipe_model"
    assert result["gross_consumption_per_second"] == "5"
    assert result["net_external_input_per_second"] == "5"
    assert result["internal_production_per_second"] == "0"
    assert result["available_supply_per_second"] is None
    assert result["boundary"] == {"rate": None, "cap": "100"}
    assert result["dispatch_authorized"] is False
    assert result["phase_targets_per_second"] == {"plastic-bar": "1/2"}


def test_shared_fluid_requirement_counts_each_goal_once(inputs):
    bound, model, facts = inputs
    result = make_phase_fluid_demand_observer(bound, model)(facts, {"targets": {"plastic-bar": .5, "other": 1}})
    assert result["net_external_input_per_second"] == "8"
    assert set(result["selected_recipes"]) == {"plastic-bar", "other"}


@pytest.mark.parametrize("mutation,reason", [
    ("surface", "scope_mismatch"), ("mods", "mod_set_mismatch"), ("tick", "catalog_tick_mismatch"),
    ("enabled", "selected_recipe_not_observed_enabled"), ("unknown_enabled", "enabled_recipes_unavailable"),
    ("target", "phase_targets_invalid"),
])
def test_unscoped_stale_or_unavailable_facts_never_create_demand(inputs, mutation, reason):
    bound, model, facts = inputs
    board = {"targets": {"plastic-bar": .5}}
    if mutation == "surface": facts["surface"] = "foreign"
    elif mutation == "mods": facts["mods"] = {"base": "different"}
    elif mutation == "tick": facts["tick"] = 99
    elif mutation == "enabled": facts["enabled_recipes"]["plastic-bar"] = False
    elif mutation == "unknown_enabled": facts.pop("enabled_recipes")
    else: board["targets"]["plastic-bar"] = True
    result = make_phase_fluid_demand_observer(bound, model)(facts, board)
    assert result["status"] == "unavailable" and result["reason"] == reason
    assert result["dispatch_authorized"] is False


def test_catalog_hash_and_ambiguous_recipe_choice_fail_closed(inputs):
    bound, model, facts = inputs
    changed = copy.deepcopy(model)
    changed["catalog_json"] += " "
    with pytest.raises(ValueError, match="hash"):
        make_phase_fluid_demand_observer(bound, changed)
    catalog = _catalog(
        _recipe("plastic-a", [("coal", 1), ("petroleum-gas", 20, "fluid")], [("plastic-bar", 2)]),
        _recipe("plastic-b", [("coal", 1), ("petroleum-gas", 10, "fluid")], [("plastic-bar", 2)]))
    raw = _bytes(catalog)
    model["catalog_json"] = raw.decode()
    model["provenance"] = _provenance(raw, catalog, mods=[{"name": "base", "version": "2.1.21"}], surface="nauvis")
    result = make_phase_fluid_demand_observer(bound, model)(facts, {"targets": {"plastic-bar": .5}})
    assert result["status"] == "unavailable" and result["reason"] == "ambiguity"


def test_caller_cap_is_not_mistaken_for_measured_supply(inputs):
    bound, model, facts = inputs
    model["boundaries"][1]["cap"] = 4
    observe = make_phase_fluid_demand_observer(bound, model)
    result = observe(facts, {"targets": {"plastic-bar": .5}})
    assert result["net_external_input_per_second"] == "5" and result["balance_feasible"] is False
    assert result["boundary"] == {"rate": None, "cap": "4"}
    assert result["available_supply_per_second"] is None
    increased = observe(facts, {"targets": {"plastic-bar": 1}})
    assert increased["net_external_input_per_second"] == "10"


def test_item_output_cannot_be_relabelled_as_fluid_demand(inputs):
    bound, model, facts = inputs
    bound.plan.throughput_unit = "items/second"
    result = make_phase_fluid_demand_observer(bound, model)(facts, {"targets": {"plastic-bar": .5}})
    assert result["status"] == "unavailable" and result["reason"] == "proposal_output_not_fluid"


def test_failed_recipe_selection_is_cached_per_phase_targets(inputs, monkeypatch):
    import tools.optimizer_demand_observation as module
    bound, model, facts = inputs
    catalog = _catalog(
        _recipe("plastic-a", [("coal", 1), ("petroleum-gas", 20, "fluid")], [("plastic-bar", 2)]),
        _recipe("plastic-b", [("coal", 1), ("petroleum-gas", 10, "fluid")], [("plastic-bar", 2)]))
    raw = _bytes(catalog)
    model["catalog_json"] = raw.decode()
    model["provenance"] = _provenance(raw, catalog, mods=[{"name": "base", "version": "2.1.21"}], surface="nauvis")
    original = module.plan_catalog_demand
    calls = []
    def count(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "plan_catalog_demand", count)
    observe = module.make_phase_fluid_demand_observer(bound, model)
    assert observe(facts, {"targets": {"plastic-bar": .5}})["reason"] == "ambiguity"
    assert observe(facts, {"targets": {"plastic-bar": .5}})["reason"] == "ambiguity"
    assert len(calls) == 1
    assert observe(facts, {"targets": {"plastic-bar": 1}})["reason"] == "ambiguity"
    assert len(calls) == 2


def test_cached_balance_still_checks_fresh_recipe_availability(inputs):
    bound, model, facts = inputs
    observe = make_phase_fluid_demand_observer(bound, model)
    assert observe(facts, {"targets": {"plastic-bar": .5}})["status"] == "required_by_selected_recipe_model"
    facts["enabled_recipes"]["plastic-bar"] = False
    result = observe(facts, {"targets": {"plastic-bar": .5}})
    assert result["status"] == "unavailable" and result["reason"] == "selected_recipe_not_observed_enabled"
