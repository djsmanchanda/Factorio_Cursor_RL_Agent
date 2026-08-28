# Path: tests/test_material_reservations.py
# Purpose: Protect complete producer startup bills from competing construction work.

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft7Validator

from orchestrator import autonomous_builder as builder
from orchestrator import stage_services
from orchestrator.mall_builder import compact_mall_project_bill
from orchestrator.material_reservations import (
    MaterialReservationLedger, set_active_material_ledger,
)
from orchestrator.parts_mall import MaterialShortage


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


def test_nested_projects_reserve_two_bootstrap_chests_before_either_builds(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    stock = {"passive-provider-chest": 2, "assembling-machine-2": 2}

    parent = ledger.declare(
        "paired_mall_passive-provider-chest",
        {"passive-provider-chest": 1, "assembling-machine-2": 1}, stock,
        target_item="passive-provider-chest", priority=100,
        hold_until_producing=True,
    )
    child = ledger.declare(
        "paired_mall_steel-chest",
        {"passive-provider-chest": 1, "assembling-machine-2": 1}, stock,
        target_item="steel-chest", priority=100, hold_until_producing=True,
    )

    assert parent.reserved["passive-provider-chest"] == 1
    assert child.reserved["passive-provider-chest"] == 1
    assert ledger.required_stock("passive-provider-chest") == 2
    assert ledger.shortage_targets(child.project_id, stock) == {}


def test_third_nested_cell_requests_the_total_not_only_its_local_shortfall(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    stock = {"passive-provider-chest": 2}
    for name in ("parent", "child", "grandchild"):
        ledger.declare(
            name, {"passive-provider-chest": 1}, stock,
            target_item=name, priority=100, hold_until_producing=True,
        )

    assert ledger.shortage_targets("grandchild", stock) == {
        "passive-provider-chest": 3,
    }


def test_sources_rates_eta_and_lifecycle_persist_and_validate(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    project = ledger.declare(
        "iron-expansion", {"transport-belt": 20}, {"transport-belt": 5},
        source_producers={"transport-belt": "producer:transport-belt"},
        expected_rates={"transport-belt": 3.0},
    )

    assert project.state == "supply_wait"
    assert project.eta_seconds["transport-belt"] == pytest.approx(5.0)
    payload = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert not list(Draft7Validator(SCHEMA).iter_errors(payload))

    ledger.mark_constructing(project.project_id, {"transport-belt": 20})
    assert ledger.projects[project.project_id].reserved == {}
    ledger.complete(project.project_id)
    assert ledger.projects[project.project_id].state == "completed"


def test_affordability_cannot_spend_stock_reserved_by_another_project(
    tmp_path: Path, monkeypatch,
) -> None:
    ledger = _ledger(tmp_path)
    stock = {"passive-provider-chest": 2}
    ledger.declare(
        "producer-a", {"passive-provider-chest": 2}, stock,
        hold_until_producing=True,
    )
    set_active_material_ledger(ledger)
    monkeypatch.setattr(stage_services.live_base, "available_items", lambda *_a: stock)
    plan = {"phases": [{"actions": [{
        "action_type": "place_ghost", "entity": "passive-provider-chest",
    }]}]}
    try:
        with pytest.raises(MaterialShortage) as raised:
            stage_services.assert_affordable(
                object(), "nauvis", "player", plan, "unrelated", lambda _m: None,
            )
    finally:
        set_active_material_ledger(None)

    assert raised.value.required == {"passive-provider-chest": 3}


def test_reused_submission_name_gets_a_fresh_transient_reservation(
    tmp_path: Path, monkeypatch,
) -> None:
    ledger = _ledger(tmp_path)
    stock = {"substation": 2}
    set_active_material_ledger(ledger)
    monkeypatch.setattr(stage_services.live_base, "available_items", lambda *_a: stock)
    plan = {"phases": [{"actions": [{
        "action_type": "place_ghost", "entity": "substation",
    }]}]}
    try:
        stage_services.assert_affordable(
            object(), "nauvis", "player", plan, "power_bridge", lambda _m: None,
            True,
        )
        ledger.mark_constructing("power_bridge", stock)
        stage_services.assert_affordable(
            object(), "nauvis", "player", plan, "power_bridge", lambda _m: None,
            True,
        )
    finally:
        set_active_material_ledger(None)

    assert ledger.projects["power_bridge"].revision == 3


def test_compact_bill_includes_cell_entities_and_first_recipe_craft(
    monkeypatch,
) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "test-provider", {
        "machine": "assembling-machine-2",
        "ingredients": ["steel-chest", "electronic-circuit"],
        "amounts": [1, 3],
        "craft_time": 0.5,
        "product_amount": 1,
        "set_recipe": True,
    })

    bill = compact_mall_project_bill("test-provider")

    assert bill["passive-provider-chest"] == 1
    assert bill["requester-chest"] == 1
    assert bill["assembling-machine-2"] == 1
    assert bill["steel-chest"] == 1
    assert bill["electronic-circuit"] == 3


def test_parent_defers_and_promotes_prerequisite_before_cell_delivery(
    monkeypatch,
) -> None:
    task = SimpleNamespace(item="passive-provider-chest", target=1)
    targets = {"passive-provider-chest": 1}
    events: list[tuple] = []

    class Priorities:
        def describe(self, *_a):
            return "parent"

        def promote(self, item, target, tick):
            events.append(("promote", item, target, tick))

        def defer(self, item, tick, reason, **_kwargs):
            events.append(("defer", item, tick, reason))

    def ensure(*_args, **_kwargs):
        targets["steel-chest"] = 1
        return False, None

    monkeypatch.setattr(builder, "_belt_starved_consumer", lambda *_a: None)
    monkeypatch.setattr(builder, "_ensure_mall_item", ensure)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 500)
    monkeypatch.setattr(
        builder, "_deliver_cell_ingredients",
        lambda *_a, **_k: pytest.fail("parent delivery ran before its prerequisite"),
    )

    builder._serve_mall_task(
        object(), object(), "nauvis", "player", task, 100, targets,
        Priorities(), (0.0, 0.0), lambda _message: None,
    )

    assert events[0] == ("promote", "steel-chest", 1, 500)
    assert events[1][0:3] == ("defer", "passive-provider-chest", 500)


def test_missing_self_seed_is_typed_intended_supply_wait(tmp_path: Path, monkeypatch) -> None:
    ledger = _ledger(tmp_path)
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setattr(
        builder, "compact_mall_project_bill",
        lambda *_a, **_k: {"passive-provider-chest": 1},
    )
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "_material_sources_and_rates", lambda *_a: ({}, {}))
    monkeypatch.setattr(builder, "_has_producer", lambda *_a: False)
    plan = SimpleNamespace(
        mall_storage_limit=1, fill_provider=False, mall_request_multiplier=None,
    )

    with pytest.raises(builder.StuckError) as raised:
        builder._reserve_compact_mall_project(
            object(), "nauvis", "player", "passive-provider-chest", plan,
            None, lambda _message: None,
        )

    assert raised.value.code == "producer_bootstrap_seed_shortage"
    assert raised.value.classification == "intended_difficulty"
    assert raised.value.state == "supply_wait"


def test_missing_self_seed_starts_a_borrowed_mall_producer(
    tmp_path: Path, monkeypatch,
) -> None:
    ledger = _ledger(tmp_path)
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setattr(
        builder, "compact_mall_project_bill",
        lambda *_a, **_k: {"requester-chest": 1},
    )
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "_material_sources_and_rates", lambda *_a: ({}, {}))
    monkeypatch.setattr(
        builder, "_start_bootstrap_loan",
        lambda *_a, **_k: "borrowed copper-cable cell is producing the seed",
    )
    plan = SimpleNamespace(
        mall_storage_limit=2, fill_provider=False, mall_request_multiplier=None,
    )

    with pytest.raises(builder.ProductionPrerequisiteDeferred, match="borrowed copper"):
        builder._reserve_compact_mall_project(
            object(), "nauvis", "player", "requester-chest", plan,
            None, lambda _message: None,
            bridge=object(), reference_point=(0.0, 0.0),
        )

    assert ledger.required_stock("requester-chest") == 1
