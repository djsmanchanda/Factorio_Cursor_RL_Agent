# Path: tests/test_laya_production_services.py | Purpose: Verify selectable permanent services, owned bills and truthful evidence.
import copy
from pathlib import Path

import pytest

from training.laya_chain_facts import committed_chain_facts
from training.laya_ops_runner import add_production_service_options, operation_faults
from training.laya_variants import apply, load_variant, VariantError
from tests.test_laya_chain_facts import world

pytestmark = pytest.mark.fast


def test_permanent_service_reuses_shared_executor_without_claiming_sustained_output(monkeypatch):
    from training import laya_ops
    calls = []
    monkeypatch.setattr(laya_ops.ab, "ensure_produced", lambda *a, **kw: calls.append((a, kw)))
    session = laya_ops.DeterministicSession.__new__(laya_ops.DeterministicSession)
    session.client, session.bridge, session.reference_point, session.emit = object(), object(), (0, 0), lambda _: None
    session._maintain_mall = lambda: None
    menu = add_production_service_options({"options": [], "needs": []},
        {"needs": [{"item": "chemical-science-pack", "condition": "unmeasured"}]}, world())
    option = menu["options"][0]
    receipt = session.run(option["family"], **option["params"])
    assert calls[0][0][4] == "chemical-science-pack"
    assert calls[0][1] == {"upgrade_bootstrap": True}
    assert receipt["code"] == "prerequisite_advanced" and receipt["ok"] is False
    assert receipt["sustained_service_verified"] is False
    monkeypatch.setattr(laya_ops.ab, "ensure_produced", lambda *a, **kw: (5, 7))
    receipt = session.run(option["family"], **option["params"])
    assert receipt["code"] == "producer_available"
    assert receipt["sustained_service_verified"] is False


def test_service_rejects_disabled_changed_recipe_and_plate_lifecycle_bypass():
    board = {"needs": [{"item": "chemical-science-pack", "condition": "unmeasured"},
                        {"item": "iron-plate", "condition": "unmeasured"}]}
    for mutation in ("disabled", "fluid", "bill", "satisfied"):
        facts, observed = copy.deepcopy(world()), copy.deepcopy(board)
        recipe = facts["recipe_observations"]["chemical-science-pack"]
        if mutation == "disabled": recipe["enabled"] = False
        elif mutation == "fluid": recipe["ingredients"][0]["type"] = "fluid"
        elif mutation == "bill": recipe["ingredients"][0]["amount"] = 10
        else: observed["needs"][0]["condition"] = "satisfied"
        assert add_production_service_options({"options": [], "needs": []}, observed, facts)["options"] == []


def test_owned_complete_bills_individually_funded_cannot_share_one_stock_pool():
    economics = {"stock": {"network": {"assembling-machine-1": 2}}, "projects": {
        "a": {"state": "ready", "required": {"assembling-machine-1": 2}, "reserved": {"assembling-machine-1": 1}},
        "b": {"state": "ready", "required": {"assembling-machine-1": 1}, "reserved": {}}}, "loans": []}
    before = copy.deepcopy(economics)
    facts = committed_chain_facts(economics)
    assert all(owner["funded_with_own_allocation"] for owner in facts["owners"].values())
    assert facts["jointly_funded"] is False
    assert facts["joint_shortfall"] == {"assembling-machine-1": 1}
    assert facts["future_chain_bill_complete"] is False and facts["requester_draws"] is None
    assert economics == before
    economics["projects"]["a"]["reserved"]["assembling-machine-1"] = 3
    assert committed_chain_facts(economics)["status"] == "unknown"


def test_construction_in_flight_is_not_charged_again_and_lender_loss_is_unknown():
    economics = {"stock": {"network": {}}, "projects": {"a": {"state": "constructing",
        "required": {"chest": 2}, "reserved": {"chest": 2}}},
        "loans": [{"lender": "electronic-circuit", "making": "chest", "remaining_to_target": 5}]}
    facts = committed_chain_facts(economics)
    assert facts["total_unsubmitted_bill"] == {}
    assert facts["owners"]["a"]["funded_with_own_allocation"] is None
    assert facts["lender_costs"][0]["lost_output_per_second"] is None


def test_fault_events_are_not_affected_run_counts_or_expected_blocks():
    facts = operation_faults([
        {"ok": False, "code": "material_shortage"},
        {"ok": False, "code": "power_route", "classification": "bug"},
        {"ok": False, "code": "power_route", "classification": "bug"},
        {"ok": False, "code": "untyped_stuck"}, {"ok": True}])
    assert facts["event_counts"] == {"blocked": {"material_shortage": 1},
        "typed_fault": {"power_route": 2}, "untyped_fault": {"untyped_stuck": 1}}
    assert facts["affected_run"] == {"blocked": True, "typed_fault": True, "untyped_fault": True}


def test_factors_are_separate_loadable_settings_and_live_survey_anchor_is_preserved(tmp_path):
    from training.laya_clean_runner import RECIPE_LINE, PAUSED_ASSERTION
    source = Path("training/laya_saved_world.lua").read_text()
    assert RECIPE_LINE in source and PAUSED_ASSERTION in source
    names = {"consumer-menu-v1": "ops_consumer_choices", "solid-fluid-filter-v1": "ops_solid_fluid_filter",
             "committed-chain-facts-v1": "ops_chain_economics", "production-services-v1": "ops_production_services"}
    for name, field in names.items():
        settings, _, _ = apply(load_variant(Path("docs/rl/laya_variants") / (name + ".json")))
        assert getattr(settings, field) is True
        assert sum(getattr(settings, key) for key in names.values()) == 1
    bad = tmp_path / "bad.json"
    bad.write_text('{"version":"1.0.0","variant_id":"bad","settings":{"ops_production_services":"false"}}')
    with pytest.raises(VariantError, match="boolean"):
        load_variant(bad)


def test_chemical_services_require_the_shared_backend_recipe_contract():
    from planners.fluid_layouts import FLUID_RECIPES
    board = {"needs": [{"item": "sulfuric-acid", "condition": "unmeasured"}]}
    spec = FLUID_RECIPES["sulfuric-acid"]
    recipe = {"enabled": True, "ingredients": [
        {"type": "item", "name": name, "amount": n} for name, n in
        zip(spec["item_ingredients"], spec["item_amounts"]) ] + [
        {"type": "fluid", "name": name, "amount": n} for name, n in spec["fluid_ingredients"].items()],
        "products": [{"type": "fluid", "name": "sulfuric-acid", "amount": 50}]}
    facts = {"recipe_observations": {"sulfuric-acid": recipe}}
    assert len(add_production_service_options({"options": [], "needs": []}, board, facts)["options"]) == 1
    for field in ("ingredients", "products"):
        changed = copy.deepcopy(facts)
        changed["recipe_observations"]["sulfuric-acid"][field][0]["amount"] += 1
        assert not add_production_service_options({"options": [], "needs": []}, board, changed)["options"]
