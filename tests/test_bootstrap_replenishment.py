# Path: tests/test_bootstrap_replenishment.py
# Purpose: Protect generic bootstrap replenishment and finite-cell capability fences.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as builder
from orchestrator.bootstrap_work import BootstrapWorkLedger
from orchestrator.build_decisions import ReplenishmentDiagnosis, diagnose_replenishment
from orchestrator.parts_mall import MaterialShortage
from orchestrator.priority_list import PriorityList
from orchestrator.material_reservations import MaterialReservationLedger
from planners.recipe_data import LINE_RECIPES


def test_missing_intermediate_is_restored_before_raw_expansion(monkeypatch) -> None:
    monkeypatch.setitem(LINE_RECIPES, "replenishment-widget", {
        "machine": "assembling-machine-1", "ingredients": ["replenishment-stick"],
        "amounts": [1], "product_amount": 1, "craft_time": 1,
    })
    monkeypatch.setitem(LINE_RECIPES, "replenishment-stick", {
        "machine": "assembling-machine-1", "ingredients": ["iron-plate"],
        "amounts": [1], "product_amount": 1, "craft_time": 1,
    })

    diagnosis = diagnose_replenishment(
        "replenishment-widget", {},
        producer_is_live=lambda item: item == "replenishment-widget",
    )

    assert diagnosis.target == "replenishment-stick"
    assert diagnosis.kind == "missing_producer"
    assert diagnosis.path == ("replenishment-widget", "replenishment-stick")


def test_bootstrap_work_ledger_detects_cycle_after_restart(tmp_path) -> None:
    ledger = BootstrapWorkLedger(tmp_path / "run.json", episode_id="episode")
    assert ledger.depend("medium-electric-pole", "iron-stick", reason="missing") is None
    restored = BootstrapWorkLedger(tmp_path / "run.json", episode_id="episode")

    cycle = restored.depend("iron-stick", "medium-electric-pole", reason="retry")

    assert cycle is not None
    assert cycle.members == ("iron-stick", "medium-electric-pole", "iron-stick")


def test_bootstrap_work_stock_poll_is_nonblocking_and_restart_safe(tmp_path) -> None:
    ledger = BootstrapWorkLedger(tmp_path / "run.json", episode_id="episode")

    assert ledger.observe_stock("iron-stick", 4, 0, now=100) == "growing"
    assert ledger.observe_stock("iron-stick", 4, 0, now=159) == "waiting"
    assert ledger.observe_stock("iron-stick", 4, 0, now=160) == "stalled"

    restored = BootstrapWorkLedger(tmp_path / "run.json", episode_id="episode")
    assert restored.observe_stock("iron-stick", 4, 0, now=161) == "waiting"
    assert restored.observe_stock("iron-stick", 4, 4, now=162) == "ready"
    assert "iron-stick" not in restored.waits


def test_bootstrap_work_graph_limit_is_explicit(tmp_path) -> None:
    ledger = BootstrapWorkLedger(tmp_path / "run.json", episode_id="episode")
    ledger.MAX_EDGES = 1

    assert ledger.depend("a", "b", reason="first") is None
    limited = ledger.depend("c", "d", reason="second")

    assert limited is not None
    assert limited.reason == "dependency_graph_limit"


def test_managed_mall_stock_wait_yields_without_sleeping(tmp_path, monkeypatch) -> None:
    ledger = BootstrapWorkLedger(tmp_path / "run.json", episode_id="episode")
    monkeypatch.setattr(builder, "_BOOTSTRAP_WORK_LEDGER", ledger)
    monkeypatch.setattr(builder, "_belt_starved_consumer", lambda *_a: None)
    monkeypatch.setattr(builder, "_ensure_mall_item", lambda *_a, **_k: (True, (1.5, 1.5)))
    monkeypatch.setattr(builder, "construction_supply_chain_is_scheduled", lambda *_a: False)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {"iron-stick": 0})
    monkeypatch.setattr(builder.live_base, "transferable_items", lambda *_a: {"iron-stick": 0})
    monkeypatch.setattr(
        builder, "wait_for_stock", lambda *_a, **_k: pytest.fail("managed run slept"),
    )
    messages: list[str] = []
    priorities = SimpleNamespace(describe=lambda *_a: "iron-stick task")
    task = SimpleNamespace(item="iron-stick", target=1)

    builder._serve_mall_task(
        object(), object(), "nauvis", "player", task, 0, {"iron-stick": 1},
        priorities, (0.0, 0.0), messages.append,
    )

    assert any(message.startswith("  MALL YIELD: iron-stick") for message in messages)


def test_managed_stock_yield_defers_real_priority_for_settle_window(tmp_path, monkeypatch) -> None:
    ledger = BootstrapWorkLedger(tmp_path / "run.json", episode_id="episode")
    priorities = PriorityList(tmp_path / "priorities.json", tick=40)
    targets = {"iron-stick": 1, "inserter": 1}
    priorities.sync(targets, {}, 40)
    task = priorities.next(targets, 40)
    assert task is not None
    monkeypatch.setattr(builder, "_BOOTSTRAP_WORK_LEDGER", ledger)
    monkeypatch.setattr(builder, "_belt_starved_consumer", lambda *_a: None)
    monkeypatch.setattr(builder, "_ensure_mall_item", lambda *_a, **_k: (True, (1.5, 1.5)))
    monkeypatch.setattr(builder, "construction_supply_chain_is_scheduled", lambda *_a: False)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {task.item: 0})
    monkeypatch.setattr(builder.live_base, "transferable_items", lambda *_a: {task.item: 0})
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 40)

    builder._serve_mall_task(
        object(), object(), "nauvis", "player", task, 40, targets,
        priorities, (0.0, 0.0), lambda _message: None,
    )

    deferred = priorities.items[task.item]
    assert deferred.status == "deferred"
    assert deferred.retry_tick == 640
    assert priorities.next(targets, 40).item != task.item


def test_managed_replenishment_shortage_is_queued_as_exact_bill(tmp_path, monkeypatch) -> None:
    ledger = BootstrapWorkLedger(tmp_path / "run.json", episode_id="episode")
    ledger.observe_stock("inserter", 1, 0, now=0)
    monkeypatch.setattr(builder, "_BOOTSTRAP_WORK_LEDGER", ledger)
    monkeypatch.setattr(builder, "_belt_starved_consumer", lambda *_a: None)
    monkeypatch.setattr(builder, "_ensure_mall_item", lambda *_a, **_k: (True, (1.5, 1.5)))
    monkeypatch.setattr(builder, "construction_supply_chain_is_scheduled", lambda *_a: False)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {"inserter": 0})
    monkeypatch.setattr(builder.live_base, "transferable_items", lambda *_a: {"inserter": 0})
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 1)
    monkeypatch.setattr(
        builder, "diagnose_replenishment", lambda *_a, **_k:
        ReplenishmentDiagnosis("iron-stick", "missing_producer", ("inserter", "iron-stick")),
    )
    monkeypatch.setattr(
        builder, "ensure_produced", lambda *_a, **_k:
        (_ for _ in ()).throw(MaterialShortage("iron-stick", {"requester-chest": 2}, {})),
    )
    priorities = SimpleNamespace(
        describe=lambda *_a: "inserter task",
        defer=lambda *_a, **_k: None,
    )
    targets = {"inserter": 1}

    builder._serve_mall_task(
        object(), object(), "nauvis", "player", SimpleNamespace(item="inserter", target=1),
        1, targets, priorities, (0.0, 0.0), lambda _message: None,
    )

    assert targets["requester-chest"] == 2


def test_ledger_belt_reserve_uses_other_ghost_bill_not_fixed_floor(tmp_path, monkeypatch) -> None:
    ledger = MaterialReservationLedger(
        tmp_path / "run.json", episode_id="episode", surface="nauvis", force="player",
    )
    ledger.declare(
        "splitter-ghost", {"transport-belt": 10}, {"transport-belt": 20},
        target_item="splitter", hold_until_producing=True,
    )
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setitem(builder.LINE_RECIPES, "ledger-belt-consumer", {
        "machine": "assembling-machine-1", "ingredients": ["transport-belt"],
        "amounts": [2], "product_amount": 1, "craft_time": 1,
    })
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {"transport-belt": 11})
    monkeypatch.setattr(
        builder.live_base, "find_line", lambda *_a: SimpleNamespace(working_count=1),
    )

    shortfall = builder._belt_reserve_shortfall(
        object(), "nauvis", "player", "ledger-belt-consumer", {"splitter": 3},
    )

    assert shortfall == ("transport-belt", 10, 11)
