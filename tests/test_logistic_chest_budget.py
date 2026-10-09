# Path: tests/test_logistic_chest_budget.py
# Purpose: Protect finite kit logistic chests for required startup dependency bills.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from orchestrator import autonomous_builder as builder
from orchestrator.logistic_chest_budget import (
    DependencyChestBudget, FINITE_LOGISTIC_CHESTS, plan_dependency_chest_budget,
)
from orchestrator.material_reservations import MaterialReservationLedger

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "schemas" / "material_reservation_ledger.schema.json").read_text(
        encoding="utf-8",
    )
)


def _ledger(tmp_path: Path) -> MaterialReservationLedger:
    return MaterialReservationLedger(
        tmp_path / "script-output" / "factorio_cursor_rl",
        episode_id="episode-1", surface="nauvis", force="player",
    )


def _fake_client() -> object:
    return type("Client", (), {"command": lambda self, _text: ""})()


# --- Ledger primitive -------------------------------------------------------


def test_dependency_budget_keeps_the_seed_chest_from_a_discretionary_partial(
    tmp_path: Path,
) -> None:
    """The long-handed seed keeps its chest; a partial cell may not hold it."""
    ledger = _ledger(tmp_path)
    stock = {
        "passive-provider-chest": 1, "assembling-machine-1": 2, "inserter": 0,
    }
    ledger.set_dependency_budget(
        {"passive-provider-chest": 1},
        claimants=("paired_mall_long-handed-inserter",),
    )

    discretionary = ledger.declare(
        "quad_mall_electronic-circuit_top",
        {"passive-provider-chest": 1, "assembling-machine-1": 2, "inserter": 1},
        stock,
    )
    # The discretionary cell is only partially fundable (no inserter), so it
    # must not take the held chest.
    assert discretionary.reserved == {"assembling-machine-1": 2}

    seed = ledger.declare(
        "paired_mall_long-handed-inserter",
        {"passive-provider-chest": 1, "assembling-machine-1": 1, "inserter": 1},
        stock, target_item="long-handed-inserter",
    )
    assert seed.reserved["passive-provider-chest"] == 1


def test_fully_fundable_producer_is_not_starved_by_a_dependency_hold(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    stock = {"passive-provider-chest": 1}
    ledger.set_dependency_budget(
        {"passive-provider-chest": 1}, claimants=("paired_mall_long-handed-inserter",),
    )

    producer = ledger.declare(
        "producer_chest", {"passive-provider-chest": 1}, stock,
        target_item="passive-provider-chest", priority=100,
    )

    assert producer.state == "ready"
    assert producer.reserved == {"passive-provider-chest": 1}


def test_allocatable_stock_hides_the_hold_from_discretionary_callers(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    stock = {"passive-provider-chest": 2, "transport-belt": 5}
    ledger.set_dependency_budget(
        {"passive-provider-chest": 1}, claimants=("paired_mall_long-handed-inserter",),
    )

    assert ledger.allocatable_stock(stock)["passive-provider-chest"] == 1
    assert ledger.allocatable_stock(
        stock, claimant="paired_mall_long-handed-inserter",
    )["passive-provider-chest"] == 2


def test_dependency_budget_persists_and_validates(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.set_dependency_budget(
        {"passive-provider-chest": 1, "requester-chest": 1},
        claimants=("paired_mall_long-handed-inserter", "paired_mall_passive-provider-chest"),
    )

    resumed = _ledger(tmp_path)
    assert resumed.dependency_budget == {
        "passive-provider-chest": 1, "requester-chest": 1,
    }
    assert resumed.dependency_claimants == (
        "paired_mall_long-handed-inserter", "paired_mall_passive-provider-chest",
    )
    payload = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert not list(Draft7Validator(SCHEMA).iter_errors(payload))

    resumed.clear_dependency_budget()
    assert _ledger(tmp_path).dependency_budget == {}


def test_dependency_budget_requires_a_claimant(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    with pytest.raises(Exception, match="claimant"):
        ledger.set_dependency_budget({"passive-provider-chest": 1})


# --- Derivation module ------------------------------------------------------


def test_dependency_chest_budget_sums_distinct_standing_dependency_bills() -> None:
    bills = {
        "long-handed-inserter": {
            "passive-provider-chest": 1, "requester-chest": 1, "inserter": 2,
        },
        "passive-provider-chest": {
            "passive-provider-chest": 1, "requester-chest": 1, "steel-chest": 1,
        },
    }
    plan = plan_dependency_chest_budget(
        bills,
        bill_for=bills.__getitem__,
        project_id_for=lambda item: f"paired_mall_{item}",
    )

    assert plan == DependencyChestBudget(
        targets={"passive-provider-chest": 2, "requester-chest": 2},
        claimants=(
            "paired_mall_long-handed-inserter", "paired_mall_passive-provider-chest",
        ),
    )


def test_dependency_chest_budget_skips_bills_without_finite_chests() -> None:
    bills = {
        "pipe": {"pipe": 100, "iron-plate": 1},
        "pumpjack": {"passive-provider-chest": 1},
    }
    plan = plan_dependency_chest_budget(
        bills,
        bill_for=bills.__getitem__,
        project_id_for=lambda item: f"paired_mall_{item}",
    )

    assert plan.targets == {"passive-provider-chest": 1}
    assert plan.claimants == ("paired_mall_pumpjack",)


# --- Builder wiring ---------------------------------------------------------


def test_pending_startup_dependencies_reads_live_capability_facts(
    monkeypatch,
) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "long-handed-inserter", {
        "machine": "assembling-machine-2",
        "ingredients": ["inserter", "iron-plate"], "amounts": [1, 2],
        "product_amount": 1, "craft_time": 0.5, "set_recipe": True,
    })
    monkeypatch.setitem(builder.LINE_RECIPES, "passive-provider-chest", {
        "machine": "assembling-machine-2",
        "ingredients": ["steel-chest", "advanced-circuit"], "amounts": [1, 1],
        "product_amount": 1, "craft_time": 0.5, "set_recipe": True,
    })
    monkeypatch.setitem(builder.LINE_RECIPES, "requester-chest", {
        "machine": "assembling-machine-2",
        "ingredients": ["steel-chest", "advanced-circuit"], "amounts": [1, 1],
        "product_amount": 1, "craft_time": 0.5, "set_recipe": True,
    })
    monkeypatch.setattr(
        builder, "_production_started",
        lambda _c, _s, _f, item: item == "requester-chest",
    )
    monkeypatch.setattr(builder, "_chemical_capability_started", lambda *_a: False)
    # Pipe is a rotating batch before the independent mall: it reuses a cell.
    monkeypatch.setattr(
        builder, "_chemical_uses_rotating_batch",
        lambda _c, _s, _f, item: item == "pipe",
    )

    pending = builder._pending_startup_dependencies(object(), "nauvis", "player")

    assert "long-handed-inserter" in pending
    assert "passive-provider-chest" in pending
    assert "requester-chest" not in pending
    assert "pipe" not in pending


def test_refresh_holds_seed_chests_and_releases_when_established(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "long-handed-inserter", {
        "machine": "assembling-machine-2",
        "ingredients": ["inserter", "iron-plate"], "amounts": [1, 2],
        "product_amount": 1, "craft_time": 0.5, "set_recipe": True,
    })
    monkeypatch.setitem(builder.LINE_RECIPES, "passive-provider-chest", {
        "machine": "assembling-machine-2",
        "ingredients": ["steel-chest", "advanced-circuit"], "amounts": [1, 1],
        "product_amount": 1, "craft_time": 0.5, "set_recipe": True,
    })
    ledger = _ledger(tmp_path)
    monkeypatch.setattr(
        builder, "_pending_startup_dependencies",
        lambda *_a: ("long-handed-inserter", "passive-provider-chest"),
    )
    messages: list[str] = []

    builder._refresh_logistic_chest_budget(
        _fake_client(), "nauvis", "player", ledger, messages.append,
    )
    assert ledger.dependency_budget == {
        "passive-provider-chest": 2, "requester-chest": 2,
    }
    assert ledger.dependency_claimants == (
        "paired_mall_long-handed-inserter", "paired_mall_passive-provider-chest",
    )
    assert any("LOGISTIC CHEST BUDGET: holding" in line for line in messages)

    monkeypatch.setattr(builder, "_pending_startup_dependencies", lambda *_a: ())
    builder._refresh_logistic_chest_budget(
        _fake_client(), "nauvis", "player", ledger, messages.append,
    )
    assert ledger.dependency_budget == {}
    assert ledger.dependency_claimants == ()
    assert any("released" in line for line in messages)


def test_fundable_discretionary_cell_cannot_spend_the_finite_dependency_hold(tmp_path):
    ledger = _ledger(tmp_path)
    stock = {'passive-provider-chest': 1, 'assembling-machine-1': 1}
    ledger.set_dependency_budget({'passive-provider-chest':1}, claimants=('seed',))
    project = ledger.declare('optional', dict(stock), stock, target_item='copper-cable')
    assert project.reserved.get('passive-provider-chest', 0) == 0
    assert project.state != 'ready'


def test_funded_claimant_does_not_hold_its_chest_twice(tmp_path):
    ledger = _ledger(tmp_path)
    stock = {'passive-provider-chest':2}
    ledger.set_dependency_budget({'passive-provider-chest':1}, claimants=('seed',))
    ledger.declare('seed', {'passive-provider-chest':1}, stock)
    partial = ledger.declare('optional', {'passive-provider-chest':1, 'inserter':1}, stock)
    assert partial.reserved.get('passive-provider-chest') == 1


def test_laya_uses_the_shared_dependency_budget_before_operations(monkeypatch):
    from training.laya_ops import DeterministicSession
    calls = []
    monkeypatch.setattr(builder, '_refresh_logistic_chest_budget', lambda *args:calls.append(args))
    monkeypatch.setattr(builder, '_MATERIAL_RESERVATION_LEDGER', None)
    session = DeterministicSession.__new__(DeterministicSession)
    session.client, session.bridge, session.emit = object(), object(), lambda _:None
    monkeypatch.setattr(session, '_operations', lambda: {'reserve_steel_seed': lambda:True})
    session.run('reserve_steel_seed')
    assert len(calls) == 1
