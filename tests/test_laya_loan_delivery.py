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
