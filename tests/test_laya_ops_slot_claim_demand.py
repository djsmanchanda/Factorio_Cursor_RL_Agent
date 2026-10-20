# Path: tests/test_laya_ops_slot_claim_demand.py | Purpose: Keep rejected mall-slot bills visible as owned Laya produce demand.

import pytest

from orchestrator import autonomous_builder as ab
from orchestrator.material_reservations import MaterialReservationLedger
from training.laya_ops import DeterministicSession
from training.laya_ops_runner import compile_options, shortage_levels, shortages, unchanged_failures, with_economics

pytestmark = [pytest.mark.training, pytest.mark.fast]
BOARD = {"needs": []}
SLOT = {"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}}


def rejected_claim(monkeypatch, tmp_path, blocker, *, capacity=True, funded=False):
    stock = {blocker: 3}
    ledger = MaterialReservationLedger(tmp_path / "script-output", episode_id="test", surface="nauvis", force="player")
    ledger.declare("foundation", {blocker: 2}, stock)
    ledger.declare(ab._material_project_id("transport-belt"), {blocker: 1}, stock)
    monkeypatch.setattr(ab, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setattr(ab, "_BOOTSTRAP_SHARED_PROVIDER_ITEMS", set())
    fixed = {
        "_independent_mall_ready": False, "_recipe_depends_on_fast_transport_belt": False,
        "_rationed_mall_completion_target": 10, "_unfunded_ladder_ingredient": None,
        "active_bootstrap_loans": [], "bootstrap_external_shortages": [],
        "_quad_bootstrap_mall_enabled": False, "mall_slot_count": 4,
        "_bootstrap_mall_slot_limit": 8 if capacity else 4,
        "preview_mall_allocation": ((0, 0), "top"), "_production_started": False,
        "compact_mall_project_bill": {blocker: 1 if funded else 2},
        "_rationed_mall_spare_target": 10, "_start_bootstrap_loan": None,
        "_transferable_or_available_stock": stock, "_bootstrap_loan_stock": (stock, stock),
    }
    for name, value in fixed.items():
        monkeypatch.setattr(ab, name, lambda *args, _value=value, **kwargs: _value)
    monkeypatch.setattr(ab.live_base, "available_items", lambda *args: stock)
    monkeypatch.setattr(ab.live_base, "find_line", lambda *args: None)
    session = DeterministicSession.__new__(DeterministicSession)
    monkeypatch.setattr(session, "_maintain_mall", lambda: None)
    monkeypatch.setattr(session, "_operations", lambda: {
        "produce": lambda **kwargs: ab._rationed_mall_batch(
            object(), object(), "nauvis", "player", "transport-belt", 10, (0, 0), lambda text: None),
    })
    return session.run("produce", item="transport-belt", stock_target=10)


@pytest.mark.parametrize("blocker", ["assembling-machine-1", "long-handed-inserter", "inserter", "passive-provider-chest"])
def test_reserved_claim_reaches_produce_menu_with_fixed_network_target(monkeypatch, tmp_path, blocker):
    result = rejected_claim(monkeypatch, tmp_path, blocker)
    assert result["code"] == "rationed_mall_borrow_deferred"
    assert result["details"]["slot_claim"] == {
        "owner": "paired_mall_transport-belt", "required": {blocker: 2},
        "available": {blocker: 1}, "stock_at": {blocker: 3},
        "reserved_by": {"foundation": {blocker: 2}},
    }
    results = [SLOT, result]
    sources = {}
    assert shortages(results, sources) == {blocker: 1}
    assert sources == {blocker: {"transport-belt"}}
    assert shortage_levels(results) == {blocker: 4}
    menu = with_economics(compile_options(BOARD, results, stock={blocker: 3}), results, None)
    options = {o["option_id"]: o for o in menu["options"]}
    assert options[f"op.produce.{blocker}"]["params"] == {"item": blocker, "stock_target": 4}
    # The bill is also available to the existing economics projection for the blocked option.
    blocked = {"family": "produce", "params": {"item": "transport-belt"}, "product": "transport-belt", "features": {}}
    assert with_economics({"options": [blocked]}, results, None)["options"][0]["bill"] == {blocker: 2}
    assert f"op.produce.{blocker}" not in {
        o["option_id"] for o in compile_options(BOARD, results, stock={blocker: 4})["options"]}


@pytest.mark.parametrize("capacity,funded", [(False, False), (True, True)])
def test_capacity_or_funded_claim_does_not_invent_material_demand(monkeypatch, tmp_path, capacity, funded):
    result = rejected_claim(monkeypatch, tmp_path, "assembling-machine-1", capacity=capacity, funded=funded)
    assert "slot_claim" not in (result.get("details") or {})
    assert shortages([result]) == {}


def test_latest_receipt_replaces_claim_and_preserves_independent_demand():
    def receipt(item, required):
        return {"ok": False, "operation": "produce", "params": {"item": item},
                "code": "rationed_mall_borrow_deferred", "details": {"slot_claim": {
                    "owner": f"paired_mall_{item}", "required": required, "available": {}, "stock_at": {}}}}
    old = receipt("transport-belt", {"assembling-machine-1": 2})
    independent = receipt("splitter", {"inserter": 2})
    latest = receipt("transport-belt", {"long-handed-inserter": 1})
    assert shortages([old, independent, latest]) == {"inserter": 2, "long-handed-inserter": 1}
    cleared = dict(latest, ok=True, code="producing", details={})
    assert shortages([old, independent, latest, cleared]) == {"inserter": 2}
    assert shortage_levels([old, independent, latest, cleared]) == {"inserter": 2}


def test_slot_claim_demand_keeps_existing_production_legality_guards():
    result = {"ok": False, "operation": "produce", "params": {"item": "transport-belt"},
              "code": "rationed_mall_borrow_deferred", "details": {"slot_claim": {
                  "owner": "paired_mall_transport-belt", "required": {"inserter": 2},
                  "available": {}, "stock_at": {}}}}
    recipes = {"inserter": {"ingredients": [{"name": "iron-plate", "amount": 1}]},
               "iron-plate": {"ingredients": [{"name": "iron-ore", "amount": 1}]}}
    started = {"ok": True, "operation": "starter", "params": {"recipe": "iron-plate"}}
    def offered(results):
        return {o["option_id"] for o in compile_options(BOARD, results, recipes=recipes)["options"]}
    assert "op.produce.inserter" not in offered([SLOT, result])
    assert "op.produce.inserter" not in offered([started, result])
    assert "op.produce.inserter" in offered([started, SLOT, result])


def test_unchanged_claim_deferral_uses_its_blocker_snapshot(monkeypatch, tmp_path):
    result = rejected_claim(monkeypatch, tmp_path, "assembling-machine-1")
    key = ("produce", "transport-belt")
    assert unchanged_failures([result], {"assembling-machine-1": 3}) == {key}
    assert unchanged_failures([result], {"assembling-machine-1": 4}) == set()
    belt_bill = {"operation": "foundation", "params": {"recipe": "iron-plate"},
                 "ok": False, "code": "material_shortage", "stock_at": {"transport-belt": 0},
                 "details": {"required": {"transport-belt": 10}, "available": {}}}
    options = compile_options(BOARD, [SLOT, belt_bill, result], stock={"assembling-machine-1": 3})["options"]
    blocked = next(o for o in options if o["option_id"] == "op.produce.transport-belt")
    assert blocked["features"]["unchanged_failure"] is True
    # A handoff with no observed material bill cannot establish an unchanged blocker.
    handoff = dict(result, code="rotating_mall_prerequisite_handoff", details={"required": 2})
    assert unchanged_failures([handoff], {}) == set()
    assert unchanged_failures([result, dict(result, ok=True)], {}) == set()


@pytest.mark.parametrize("shortfall", [1, 2])
def test_small_slot_construction_bill_survives_larger_shortages(shortfall):
    from training.laya_guided_policy import GuidedPolicy

    larger = {"transport-belt": 100, "inserter": 30, "electric-mining-drill": 8,
              "medium-electric-pole": 6}
    claim = {"ok": False, "operation": "produce", "params": {"item": "transport-belt"},
             "code": "rationed_mall_borrow_deferred", "details": {"slot_claim": {
                 "owner": "paired_mall_transport-belt", "required": {"assembling-machine-2": 2},
                 "available": {"assembling-machine-2": 2 - shortfall},
                 "stock_at": {"assembling-machine-2": 10}}}}
    blocked = {"ok": False, "operation": "foundation", "params": {"recipe": "iron-plate"},
               "code": "material_shortage", "details": {"required": larger, "available": {}},
               "stock_at": {}}
    started = {"ok": True, "operation": "starter", "params": {"recipe": "iron-plate"}}
    recipes = {item: {"ingredients": [{"name": "iron-plate", "amount": 1}]}
               for item in (*larger, "assembling-machine-2")}
    recipes["iron-plate"] = {"ingredients": [{"name": "iron-ore", "amount": 1}]}
    results = [started, SLOT, blocked, claim]
    menu = compile_options(BOARD, results, recipes=recipes, stock={"assembling-machine-2": 10})
    produce = {o["product"]: o for o in menu["options"] if o["family"] == "produce"}
    assert set(produce) == set(larger) | {"assembling-machine-2"}
    assert produce["assembling-machine-2"]["params"]["stock_target"] == 10 + shortfall
    probabilities = {r["option_id"]: r["probability"] for r in GuidedPolicy(1).distribution({}, menu)}
    assert all(probabilities[o["option_id"]] > 0 for o in produce.values())
    # More choices do not bypass missing plate supply or an unmakeable recipe.
    no_starter = compile_options(BOARD, results[1:], recipes=recipes)
    assert not any(o["family"] == "produce" for o in no_starter["options"])
    del recipes["assembling-machine-2"]
    unknown = compile_options(BOARD, results, recipes=recipes)
    assert "op.produce.assembling-machine-2" not in {o["option_id"] for o in unknown["options"]}
    # A fixed target is retired once stock arrives; no new moving demand is invented.
    funded = compile_options(BOARD, results, recipes={**recipes, "assembling-machine-2": {
        "ingredients": [{"name": "iron-plate", "amount": 1}]}},
        stock={"assembling-machine-2": 10 + shortfall})
    assert "op.produce.assembling-machine-2" not in {o["option_id"] for o in funded["options"]}
