# Path: tests/test_laya_direct_delivery.py | Purpose: Protect the direct requester replenishment gate and its fallbacks.

import orchestrator.autonomous_builder as builder
from orchestrator import live_base

ITEM = "copper-cable"          # one copper-plate per craft, amount 1
AMOUNT = 1


def _run(monkeypatch, *, held, local, moved, scope=None):
    calls = {"transfer": [], "nearest": 0}
    monkeypatch.setattr(builder, "_DIRECT_DELIVERY_ITEMS", scope)
    monkeypatch.setattr(builder, "LINE_RECIPES", {ITEM: {
        "ingredients": ["copper-plate"], "amounts": [AMOUNT], "machine": "assembling-machine-1"}})
    monkeypatch.setattr(live_base, "requester_requesting", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(live_base, "network_item_count", lambda *a, **k: local)
    monkeypatch.setattr(live_base, "chest_contents", lambda *a, **k: {"copper-plate": held})

    def transfer(client, surface, item, count, to):
        calls["transfer"].append((item, count, to))
        return moved

    monkeypatch.setattr(live_base, "transfer_stock", transfer)

    def nearest(*a, **k):
        calls["nearest"] += 1
        return None

    monkeypatch.setattr(live_base, "nearest_container", nearest)
    builder._deliver_cell_ingredients(None, None, "nauvis", "player", ITEM, (0.0, 0.0), lambda *_: None,
                                      requester_position=(10.0, 10.0))
    return calls


def test_a_short_requester_is_replenished_directly_when_its_network_holds_the_item(monkeypatch):
    calls = _run(monkeypatch, held=0, local=100, moved=20)
    assert calls["transfer"] == [("copper-plate", 20, (10.0, 10.0))]
    assert calls["nearest"] == 0


def test_a_stocked_requester_is_not_replenished(monkeypatch):
    calls = _run(monkeypatch, held=AMOUNT, local=100, moved=20)
    assert calls["transfer"] == []
    assert calls["nearest"] == 0


def test_no_direct_delivery_when_the_network_does_not_hold_the_item(monkeypatch):
    calls = _run(monkeypatch, held=0, local=0, moved=20)
    assert calls["transfer"] == []
    assert calls["nearest"] == 1


def test_the_plastic_scope_ignores_other_ingredients(monkeypatch):
    calls = _run(monkeypatch, held=0, local=1, moved=20, scope=frozenset({"plastic-bar"}))
    assert calls["transfer"] == []
    assert calls["nearest"] == 1
