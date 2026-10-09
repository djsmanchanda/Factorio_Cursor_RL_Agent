# Path: tests/test_mall_growth.py | Purpose: Protect demand-driven mall slot growth after the verified six-drill checkpoint, its honest unknown-aware facts, and the Laya exposure.

import pytest

from orchestrator import autonomous_builder as builder
from orchestrator import mall_growth


def _facts(**overrides):
    base = dict(
        observed_drills={"iron-plate": 6, "copper-plate": 6, "stone-brick": 6},
        committed_slots=4,
        slot_limit=12,
        production_per_s={"iron-plate": 1.0, "copper-plate": 0.5, "stone-brick": 0.2},
        mall_consumption_per_s={"iron-plate": 0.4, "copper-plate": 0.5, "stone-brick": 0.2},
        unmet_demand=True,
        stock={"assembling-machine-1": 3, "long-handed-inserter": 4},
    )
    base.update(overrides)
    return mall_growth.mall_growth_facts(**base)


def test_six_drill_checkpoint_needs_every_plate_known_and_complete():
    assert mall_growth.six_drill_checkpoint(
        {"iron-plate": 6, "copper-plate": 6, "stone-brick": 6},
    )
    assert not mall_growth.six_drill_checkpoint(
        {"iron-plate": 6, "copper-plate": 5, "stone-brick": 6},
    )
    assert not mall_growth.six_drill_checkpoint(
        {"iron-plate": 6, "copper-plate": None, "stone-brick": 6},
    )
    assert not mall_growth.six_drill_checkpoint(
        {"iron-plate": 6, "copper-plate": 6},
    )


def test_surplus_is_unknown_until_both_rates_are_measured():
    surplus = mall_growth.plate_surplus(
        {"iron-plate": 1.0, "copper-plate": None, "stone-brick": 0.3},
        {"iron-plate": 0.4, "copper-plate": 0.2, "stone-brick": None},
    )
    assert surplus["iron-plate"] == pytest.approx(0.6)
    assert surplus["copper-plate"] is None  # production unknown
    assert surplus["stone-brick"] is None  # consumption unknown
    assert all(
        value is None
        for value in mall_growth.plate_surplus({}, {}).values()
    )


def test_demand_known_checkpoint_permits_but_scarce_seed_unknown_readiness_and_no_demand_do_not():
    # Demand + verified checkpoint + free slot + funded seed -> admit.
    assert _facts().admit

    # A scarce seed reserve does not spend the last assemblers on growth.
    scarce = _facts(stock={"assembling-machine-1": 0, "long-handed-inserter": 4})
    assert not scarce.admit and scarce.reason == "seed reserve not funded"

    # Unknown readiness never reads as ready.
    unknown = _facts(observed_drills={"iron-plate": 6, "copper-plate": None, "stone-brick": 6})
    assert not unknown.admit and unknown.reason == "six-drill checkpoint not verified"

    # A verified checkpoint with no unmet item and no measured surplus is not demand.
    idle = _facts(
        unmet_demand=False,
        production_per_s={"iron-plate": 0.4, "copper-plate": 0.5, "stone-brick": 0.2},
    )
    assert not idle.admit and idle.reason == "no measured plate surplus or unmet demand"

    # A measured surplus alone is demand even without an explicit unmet item.
    surplus = _facts(
        unmet_demand=False,
        production_per_s={"iron-plate": 1.0, "copper-plate": 0.5, "stone-brick": 0.2},
    )
    assert surplus.admit


def test_capacity_full_and_unlimited_limits_are_reported_as_facts():
    full = _facts(committed_slots=12, slot_limit=12)
    assert not full.admit and full.reason == "slot capacity full" and full.free_slots == 0
    unlimited = _facts(committed_slots=12, slot_limit=None)
    assert unlimited.free_slots is None and unlimited.admit


def _install_checkpoint(monkeypatch, drills, *, demand=True, stock=None, committed=4):
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder, "_managed_drill_counts", lambda *_a: drills)
    monkeypatch.setattr(builder, "mall_slot_count", lambda *_a: committed)
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: stock if stock is not None
        else {"assembling-machine-1": 3, "long-handed-inserter": 4},
    )
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", None)
    monkeypatch.setattr(builder, "_BLOCKING_MALL_ITEMS", {"splitter"} if demand else set())
    monkeypatch.setattr(builder, "_BOOTSTRAP_SHARED_PROVIDER_ITEMS", set())


def test_builder_dense_expansion_requires_the_verified_six_drill_checkpoint(monkeypatch):
    monkeypatch.setitem(
        builder.LINE_RECIPES, "long-handed-inserter",
        {"machine": "assembling-machine-1", "ingredients": ["iron-plate"],
         "amounts": [1], "product_amount": 1, "craft_time": 0.5},
    )
    _install_checkpoint(monkeypatch, {"iron-plate": 6, "copper-plate": 6, "stone-brick": 6})
    assert builder._dense_bootstrap_mall_enabled(object(), "nauvis", "player")

    _install_checkpoint(monkeypatch, {"iron-plate": 6, "copper-plate": 6, "stone-brick": None})
    assert not builder._dense_bootstrap_mall_enabled(object(), "nauvis", "player")

    _install_checkpoint(monkeypatch, {"iron-plate": 6, "copper-plate": 6, "stone-brick": 3})
    assert not builder._dense_bootstrap_mall_enabled(object(), "nauvis", "player")


def test_builder_dense_expansion_grows_toward_twelve_from_the_eight_bank(monkeypatch):
    """The admission target is twelve, so a full eight-slot opening bank still grows."""
    monkeypatch.setitem(
        builder.LINE_RECIPES, "long-handed-inserter",
        {"machine": "assembling-machine-1", "ingredients": ["iron-plate"],
         "amounts": [1], "product_amount": 1, "craft_time": 0.5},
    )
    drills = {"iron-plate": 6, "copper-plate": 6, "stone-brick": 6}
    _install_checkpoint(monkeypatch, drills, committed=8)
    assert builder._dense_bootstrap_mall_enabled(object(), "nauvis", "player")

    _install_checkpoint(monkeypatch, drills, committed=12)
    assert not builder._dense_bootstrap_mall_enabled(object(), "nauvis", "player")


def test_builder_dense_expansion_needs_demand_and_a_funded_seed(monkeypatch):
    monkeypatch.setitem(
        builder.LINE_RECIPES, "long-handed-inserter",
        {"machine": "assembling-machine-1", "ingredients": ["iron-plate"],
         "amounts": [1], "product_amount": 1, "craft_time": 0.5},
    )
    drills = {"iron-plate": 6, "copper-plate": 6, "stone-brick": 6}
    _install_checkpoint(monkeypatch, drills, demand=False)
    assert not builder._dense_bootstrap_mall_enabled(object(), "nauvis", "player")

    _install_checkpoint(monkeypatch, drills, demand=True, stock={"assembling-machine-1": 0})
    assert not builder._dense_bootstrap_mall_enabled(object(), "nauvis", "player")


def test_managed_drill_counts_reports_unknown_for_unqueryable_clients():
    assert builder._managed_drill_counts(object(), "nauvis", "player") == {
        "iron-plate": None, "copper-plate": None, "stone-brick": None,
    }


def test_factory_status_exposes_measured_plate_surplus_for_growth():
    from training.laya_factory_status import compile_status

    recipes = {"iron-plate": {"ingredients": [{"name": "iron-ore", "amount": 1}],
                              "product_amount": 1, "energy_ticks": 192}}
    facts = {"tick": 600, "recipes": recipes, "machines": [], "pending_ghosts": 0,
             "networks": {"1": {"items": {"assembling-machine-1": 2, "long-handed-inserter": 2},
                                "reserved": {}}}}
    measurement = {"window_ticks": 600,
                   "production": {"iron-plate": {"per_second": 0.8}},
                   "consumption": {"iron-plate": {"per_second": 0.3}}}
    board = compile_status(facts, {"iron-plate": 0.5}, measurement)
    growth = board["mall_growth"]
    assert growth["plate_surplus_per_s"]["iron-plate"] == pytest.approx(0.5)
    assert growth["plate_surplus_per_s"]["copper-plate"] is None
    assert growth["seed_ready"] is True

    unmeasured = compile_status(facts, {"iron-plate": 0.5})
    assert unmeasured["mall_growth"]["plate_surplus_per_s"]["iron-plate"] is None


def test_laya_mall_slot_options_expose_growth_facts():
    from training.laya_ops_runner import compile_options

    recipes = {
        "iron-plate": {"ingredients": [{"name": "iron-ore", "amount": 1}]},
        "copper-plate": {"ingredients": [{"name": "copper-ore", "amount": 1}]},
        "iron-gear-wheel": {"ingredients": [{"name": "iron-plate", "amount": 2}]},
        "copper-cable": {"ingredients": [{"name": "copper-plate", "amount": 1}]},
        "electronic-circuit": {"ingredients": [{"name": "iron-plate", "amount": 1},
                                               {"name": "copper-cable", "amount": 3}]},
        "transport-belt": {"ingredients": [{"name": "iron-gear-wheel", "amount": 1},
                                           {"name": "iron-plate", "amount": 1}]},
    }
    board = {
        "needs": [{"item": "iron-plate", "condition": "unbuilt", "goal_required_per_s": 0.5}],
        "mall_growth": {"plate_surplus_per_s": {"iron-plate": 0.5}, "seed_ready": True},
    }
    results = [
        {"ok": True, "operation": "starter", "params": {"recipe": "iron-plate"}},
        {"ok": False, "operation": "foundation", "params": {"recipe": "iron-plate"},
         "code": "material_shortage", "details": {"required": {"transport-belt": 126}, "available": {}}},
    ]
    options = {
        o["option_id"]: o
        for o in compile_options(board, results, recipes=recipes)["options"]
    }
    gear = options["op.mall_slot.iron-gear-wheel"]["features"]
    assert gear["plate_surplus_per_s"] == {"iron-plate": 0.5}
    assert gear["seed_ready"] is True



def test_unknown_slot_count_is_not_an_empty_mall_with_room():
    facts = mall_growth.mall_growth_facts(observed_drills={p:6 for p in mall_growth.PLATE_ITEMS},
        committed_slots=None, slot_limit=12, production_per_s={}, mall_consumption_per_s={},
        unmet_demand=True, stock=dict(mall_growth.SEED_RESERVE))
    assert facts.free_slots is None and not facts.admit
    assert 'unknown' in facts.reason


def test_unknown_seed_stock_stays_unknown():
    assert mall_growth.seed_ready(None) is None


def test_laya_economics_reports_live_committed_capacity(monkeypatch):
    from types import SimpleNamespace
    from training.laya_ops import DeterministicSession
    from orchestrator import mall_bootstrap
    monkeypatch.setattr(builder, '_MATERIAL_RESERVATION_LEDGER', SimpleNamespace(projects={}))
    monkeypatch.setattr(builder, '_BOOTSTRAP_DISTRICT_LEDGER', SimpleNamespace(states=lambda:[]))
    monkeypatch.setattr(mall_bootstrap, 'active_bootstrap_loans', lambda *_:[])
    monkeypatch.setattr(builder, 'mall_slot_count', lambda *_:7)
    monkeypatch.setattr(builder, '_bootstrap_mall_slot_limit', lambda *_:12)
    session = DeterministicSession.__new__(DeterministicSession)
    session.client, session.reference_point = object(), (0,0)
    facts = session.economics()
    assert facts['free_slots'] == 5
    assert facts['mall_capacity'] == {'committed_slots':7,'slot_limit':12,'unlimited':False}
