# Path: tests/test_teardown_storage_room.py
# Purpose: No bot teardown is ordered while logistic storage cannot take its cargo (R5 full-storage bot capture).

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as builder
from orchestrator import live_base
from orchestrator import mine_retirement
from orchestrator import recoverable_retirement as retirement
from orchestrator.extraction_state import ResourceMine
from orchestrator.work_state import WorkStateSignal


class Client:
    """Any client that can answer RCON; storage itself is stubbed per test."""

    def __init__(self, reply: str = "") -> None:
        self.reply = reply

    def command(self, _text: str) -> str:
        return self.reply


def _plan(*entities: tuple[str, float, float]) -> dict:
    return {"phases": [{"name": "retire", "actions": [
        {"action_type": "remove_entity", "entity": name, "position": {"x": x, "y": y}}
        for name, x, y in entities
    ]}]}


def _bridge(orders: list) -> SimpleNamespace:
    def execute(_authorization, plan, **_kwargs):
        orders.append(plan)
        return {"actions": [{"status": "success"} for _ in plan["actions"]]}
    return SimpleNamespace(execute_deconstruction=execute)


def test_shared_helper_orders_nothing_while_storage_is_full(monkeypatch):
    # R5: a full provider was deconstructed into full storage; every construction bot hovered with cargo.
    monkeypatch.setattr(retirement.live_base, "entity_signatures_at",
                        lambda *_a: {(1.5, 2.5): {"name": "electric-furnace"}})
    monkeypatch.setattr(retirement, "load_json", lambda report: report)
    monkeypatch.setattr(retirement.live_base, "teardown_storage_room", lambda *_a: (4, 1))
    orders: list = []

    with pytest.raises(retirement.RecoverableRetirementStorageWait) as raised:
        retirement.retire_entities_via_bots(
            Client(), _bridge(orders), "nauvis", "player",
            _plan(("electric-furnace", 1.5, 2.5)), "iron_starter", lambda _m: None,
        )

    assert orders == []
    # A typed supply wait, resumable as pending, never a bug-class failure.
    assert isinstance(raised.value, WorkStateSignal) and raised.value.state == "supply_wait"
    assert isinstance(raised.value, retirement.RecoverableRetirementPending)
    assert raised.value.details == {"stacks_needed": 4, "empty_storage_stacks": 1}


def test_shared_helper_orders_when_storage_has_room(monkeypatch):
    present = iter(({(1.5, 2.5): {"name": "electric-furnace"}}, {(1.5, 2.5): {"name": "NONE"}}))
    monkeypatch.setattr(retirement.live_base, "entity_signatures_at", lambda *_a: next(present))
    monkeypatch.setattr(retirement, "load_json", lambda report: report)
    monkeypatch.setattr(retirement.live_base, "teardown_storage_room", lambda *_a: (4, 4))
    orders: list = []

    assert retirement.retire_entities_via_bots(
        Client(), _bridge(orders), "nauvis", "player",
        _plan(("electric-furnace", 1.5, 2.5)), "iron_starter", lambda _m: None,
    ) == 1
    assert len(orders) == 1


def test_shared_helper_waits_without_a_covering_network(monkeypatch):
    monkeypatch.setattr(retirement.live_base, "entity_signatures_at",
                        lambda *_a: {(1.5, 2.5): {"name": "electric-furnace"}})
    monkeypatch.setattr(retirement, "load_json", lambda report: report)
    monkeypatch.setattr(retirement.live_base, "teardown_storage_room", lambda *_a: None)
    orders = []
    with pytest.raises(retirement.RecoverableRetirementStorageWait, match="covering"):
        retirement.retire_entities_via_bots(
            Client(), _bridge(orders), "nauvis", "player",
            _plan(("electric-furnace", 1.5, 2.5)), "iron_starter", lambda _m: None,
            timeout=0,
        )
    assert orders == []


def test_mine_retirement_defers_chests_that_storage_cannot_take(monkeypatch):
    mine = ResourceMine(output=(10.5, 20.5), drill_count=6, expansion_step=1, row_capacity=3)
    monkeypatch.setattr(mine_retirement, "find_resource_mines", lambda *_a: [mine])
    monkeypatch.setattr(mine_retirement, "nearest_patch", lambda *_a: None)
    monkeypatch.setattr(mine_retirement, "_managed_entities", lambda *_a: [
        {"name": "steel-chest", "position": {"x": 14.5, "y": 18.5}},
        {"name": "electric-mining-drill", "position": {"x": 14.5, "y": 16.5}},
    ])
    monkeypatch.setattr(live_base, "teardown_storage_room", lambda *_a: (30, 2))
    orders: list = []
    messages: list[str] = []

    retired = mine_retirement.retire_depleted_mines(
        Client(), _bridge(orders), "nauvis", "player", "iron-ore", (0.0, 0.0), messages.append,
    )

    assert retired == 0 and orders == []
    assert any("MINE RETIREMENT DEFERRED" in m for m in messages)


def test_promoted_mall_teardown_recovers_items_through_bots(monkeypatch):
    # The executor's remove_entity destroys entities with their contents; a promoted half now goes through bots.
    retirement_plan = builder.generate_promoted_mall_retirement_plan(
        "iron-gear-wheel", "assembling-machine-1", (36.5, 32.5), (39.5, 31.5),
    )
    monkeypatch.setattr(builder.live_base, "entity_signatures_at", lambda _c, _s, _f, points: {
        p: {"name": {(36.5, 32.5): "assembling-machine-1", (38.5, 32.5): "inserter",
                     (38.5, 31.5): "inserter", (39.5, 31.5): "passive-provider-chest"}.get(p, "NONE")}
        for p in points})
    bot_orders: list[dict] = []
    submitted: list[dict] = []
    monkeypatch.setattr(builder, "retire_entities_via_bots",
                        lambda _c, _b, _s, _f, plan, _l, _e, **_k: bot_orders.append(plan) or 4)
    monkeypatch.setattr(builder, "_submit", lambda _c, _b, _s, plan, *_a, **_k: submitted.append(plan))

    assert builder._retire_promoted_mall_slot(
        Client(), object(), "nauvis", "player", "iron-gear-wheel", (36.5, 32.5), retirement_plan, print,
    )

    removed = {a["entity"] for a in bot_orders[0]["phases"][0]["actions"]}
    assert removed == {"assembling-machine-1", "inserter", "passive-provider-chest"}
    # Nothing is destroyed through the executor; only the requester section is cleared there.
    assert all(a["action_type"] != "remove_entity" for p in submitted for ph in p["phases"] for a in ph["actions"])


def test_promoted_mall_teardown_waits_for_storage(monkeypatch):
    retirement_plan = builder.generate_promoted_mall_retirement_plan(
        "iron-gear-wheel", "assembling-machine-1", (36.5, 32.5), (39.5, 31.5),
    )
    monkeypatch.setattr(builder.live_base, "entity_signatures_at",
                        lambda _c, _s, _f, points: {p: {"name": "passive-provider-chest"} for p in points})
    def full(*_a, **_k):
        raise retirement.RecoverableRetirementStorageWait("full", needed=9, free=0)
    monkeypatch.setattr(builder, "retire_entities_via_bots", full)
    monkeypatch.setattr(builder, "_submit", lambda *_a, **_k: pytest.fail("no requester change while deferred"))

    assert not builder._retire_promoted_mall_slot(
        Client(), object(), "nauvis", "player", "iron-gear-wheel", (36.5, 32.5), retirement_plan, print,
    )


@pytest.mark.parametrize(("reply", "expected"), [("NONE", None), ("7 3", (7, 3))])
def test_storage_probe_parses_its_survey(reply, expected):
    assert live_base.teardown_storage_room(Client(reply), "nauvis", "player",
                                           [("steel-chest", (1.5, 1.5))]) == expected


def test_storage_probe_rejects_malformed_survey():
    with pytest.raises(live_base.TelemetryError):
        live_base.teardown_storage_room(Client("Error: boom"), "nauvis", "player", [("steel-chest", (1.5, 1.5))])
