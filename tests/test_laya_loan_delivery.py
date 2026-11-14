# Path: tests/test_laya_loan_delivery.py | Purpose: Protect the empty-requester loan delivery predicate.

from types import SimpleNamespace

import orchestrator.autonomous_builder as builder
from orchestrator import live_base


def _run(monkeypatch, *, held, local):
    monkeypatch.setattr(builder, "LINE_RECIPES", {"advanced-circuit": {
        "ingredients": ["plastic-bar", "copper-cable", "electronic-circuit"],
        "amounts": [2, 4, 2], "machine": "assembling-machine-2"}})
    monkeypatch.setattr(live_base, "chest_contents", lambda *a, **k: held)
    monkeypatch.setattr(live_base, "network_item_count", lambda *a, **k: local)
    loan = SimpleNamespace(requester_position=(10.0, 10.0))
    step = SimpleNamespace(recipe="advanced-circuit")
    return builder._loan_step_requester_unreachable(None, "nauvis", "player", loan, step)


def test_an_empty_requester_outside_any_network_needs_delivery(monkeypatch):
    assert _run(monkeypatch, held={}, local=None) is True


def test_an_empty_requester_inside_a_fed_network_keeps_the_gate_path(monkeypatch):
    assert _run(monkeypatch, held={}, local=5) is False


def test_a_stocked_requester_needs_no_delivery(monkeypatch):
    assert _run(monkeypatch, held={"plastic-bar": 2, "copper-cable": 4,
                                   "electronic-circuit": 2}, local=None) is False


def _loan(monkeypatch):
    from orchestrator.mall_bootstrap import MallBootstrapLoan, MallBootstrapStep
    loan = MallBootstrapLoan(
        original_recipe="iron-gear-wheel", target_item="assembling-machine-2",
        target_count=1, side="left", requester_position=(39.5, 32.5),
        current_recipe="electronic-circuit", step_recipe="electronic-circuit",
        step_target_count=3, step_baseline_finished=862,
        step_required_crafts=2, step_minimum_crafts=2)
    stock = {"electronic-circuit": 1, "copper-cable": 4, "iron-plate": 956}
    monkeypatch.setattr(builder, "_LOAN_GATE_REFRESH_ATTEMPTS", {})
    monkeypatch.setattr(builder, "_bootstrap_loan_stock", lambda *_a: (dict(stock), dict(stock)))
    monkeypatch.setattr(builder, "next_bootstrap_step",
                        lambda *_a, **_k: MallBootstrapStep("electronic-circuit", 3, 2))
    monkeypatch.setattr(builder.live_base, "progress_counters",
                        lambda *_a, **_k: {(36.5, 32.5): 862000})
    monkeypatch.setattr(builder.live_base, "entity_status_name",
                        lambda *_a: "disabled_by_control_behavior")
    monkeypatch.setattr(builder, "_bootstrap_loan_persisted_step", lambda *_a: None)
    monkeypatch.setattr(builder, "consume_wait", lambda *_a: None)
    monkeypatch.setattr(builder.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(builder, "_submit", lambda *_a, **_k: None)
    return loan


def test_a_fed_but_unreachable_cell_delivers_without_spending_refresh_budget(monkeypatch):
    loan = _loan(monkeypatch)
    monkeypatch.setattr(builder, "_loan_step_cell_fed", lambda *_a, **_k: True)
    monkeypatch.setattr(builder, "_loan_step_requester_unreachable", lambda *_a, **_k: True)
    monkeypatch.setattr(builder, "_deliver_cell_ingredients", lambda *_a, **_k: True)
    messages = []
    remedy = builder._submit_bootstrap_loan(object(), object(), "nauvis", "player", loan, messages.append)
    assert "delivery" in remedy.lower()
    assert any("CELL DELIVERY" in message for message in messages)
    assert builder._LOAN_GATE_REFRESH_ATTEMPTS == {}


def test_a_failed_delivery_keeps_the_refresh_path(monkeypatch):
    loan = _loan(monkeypatch)
    monkeypatch.setattr(builder, "_loan_step_cell_fed", lambda *_a, **_k: True)
    monkeypatch.setattr(builder, "_loan_step_requester_unreachable", lambda *_a, **_k: True)
    monkeypatch.setattr(builder, "_deliver_cell_ingredients", lambda *_a, **_k: False)
    builder._submit_bootstrap_loan(object(), object(), "nauvis", "player", loan, lambda *_: None)
    assert list(builder._LOAN_GATE_REFRESH_ATTEMPTS.values()) == [1]


def _delivery_env(monkeypatch, *, radii, moves=5):
    monkeypatch.setitem(builder.LINE_RECIPES, "steel-chest", {
        "ingredients": ["steel-plate"], "amounts": [8]})
    monkeypatch.setattr(live_base, "network_item_count", lambda *a, **k: 0)
    monkeypatch.setattr(live_base, "nearest_container", lambda *a, **k: None)
    seen = []

    def spots(client, surface, placements, start, *, step_radius=6.0):
        seen.append(step_radius)
        return radii.get(step_radius, [])

    monkeypatch.setattr(live_base, "chained_clear_spots", spots)
    plans = []
    monkeypatch.setattr(builder, "_submit",
                        lambda client, bridge, surface, plan, name, emit: plans.append(plan))
    monkeypatch.setattr(live_base, "transfer_stock", lambda *a, **k: moves)
    messages = []
    ok = builder._deliver_cell_ingredients(
        object(), object(), "nauvis", "player", "steel-chest", (39.5, 32.5),
        messages.append, requester_position=(40.5, 32.5))
    return ok, seen, plans, messages


def test_delivery_widens_the_search_and_uses_the_clear_pole_spot(monkeypatch):
    ok, seen, plans, messages = _delivery_env(monkeypatch, radii={
        10.0: [("passive-provider-chest", 48.5, 30.5),
               ("medium-electric-pole", 50.5, 31.5)]})
    assert ok is True
    assert seen == [6.0, 10.0]
    pole = next(action for action in plans[0]["phases"][0]["actions"]
                if action["entity"] == "medium-electric-pole")
    assert pole["position"] == {"x": 50.5, "y": 31.5}
    assert any("CELL DELIVERY: moved 5 steel-plate" in message for message in messages)


def test_delivery_reports_when_no_spot_exists(monkeypatch):
    ok, seen, plans, messages = _delivery_env(monkeypatch, radii={})
    assert ok is False
    assert seen == [6.0, 10.0, 14.0]
    assert plans == []
    assert any("CELL DELIVERY BLOCKED" in message and "14 tiles" in message
               for message in messages)


def test_delivery_moves_when_the_network_holds_stock_far_from_an_empty_requester(monkeypatch):
    monkeypatch.setitem(builder.LINE_RECIPES, "steel-chest", {
        "ingredients": ["steel-plate"], "amounts": [8]})
    monkeypatch.setattr(live_base, "network_item_count", lambda *a, **k: 8)
    monkeypatch.setattr(live_base, "chest_contents", lambda *a, **k: {})
    monkeypatch.setattr(live_base, "nearest_container", lambda *a, **k: (41.5, 32.5))
    moved = []
    monkeypatch.setattr(live_base, "transfer_stock",
                        lambda *a, **k: moved.append(a[2]) or 8)
    messages = []
    assert builder._deliver_cell_ingredients(
        object(), object(), "nauvis", "player", "steel-chest", (39.5, 32.5),
        messages.append, requester_position=(40.5, 32.5)) is True
    assert moved == ["steel-plate"]
    assert any("CELL DELIVERY: moved 8 steel-plate" in message for message in messages)


def test_delivery_does_not_move_into_a_requester_that_already_holds_enough(monkeypatch):
    monkeypatch.setitem(builder.LINE_RECIPES, "steel-chest", {
        "ingredients": ["steel-plate"], "amounts": [8]})
    monkeypatch.setattr(live_base, "network_item_count", lambda *a, **k: 8)
    monkeypatch.setattr(live_base, "chest_contents", lambda *a, **k: {"steel-plate": 16})
    monkeypatch.setattr(live_base, "transfer_stock",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not move")))
    assert builder._deliver_cell_ingredients(
        object(), object(), "nauvis", "player", "steel-chest", (39.5, 32.5),
        lambda *_: None, requester_position=(40.5, 32.5)) is False
