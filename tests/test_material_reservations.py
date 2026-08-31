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
from planners.plan_validation import actions


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


def test_finite_bootstrap_supply_is_recorded_once_and_validates(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.record_bootstrap_supply(
        "reduced-v1", {"requester-chest": 2}, {"requester-chest": 2},
    )

    assert ledger.bootstrap_supply_applied("reduced-v1")
    resumed = _ledger(tmp_path)
    assert resumed.bootstrap_supply == {
        "reduced-v1": {
            "targets": {"requester-chest": 2},
            "inserted": {"requester-chest": 2},
        },
    }
    payload = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert not list(Draft7Validator(SCHEMA).iter_errors(payload))


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

    shared_right = compact_mall_project_bill(
        "test-provider", side="right", shared_provider=True,
    )
    assert shared_right["assembling-machine-2"] == 1
    assert "requester-chest" not in shared_right
    assert "passive-provider-chest" not in shared_right
    assert "substation" not in shared_right


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

    monkeypatch.setitem(builder.LINE_RECIPES, "passive-provider-chest", {
        "machine": "assembling-machine-2",
        "ingredients": ["steel-chest", "electronic-circuit"],
        "amounts": [1, 3], "product_amount": 1, "craft_time": 0.5,
    })
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


def test_unrelated_queued_batches_are_not_reported_as_prerequisites(
    monkeypatch,
) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "electric-mining-drill", {
        "machine": "assembling-machine-2",
        "ingredients": ["electronic-circuit", "iron-gear-wheel", "iron-plate"],
        "amounts": [3, 5, 10], "product_amount": 1, "craft_time": 2.0,
    })

    prerequisites = builder._queued_mall_prerequisites("electric-mining-drill")

    assert {
        "electronic-circuit", "copper-cable", "iron-gear-wheel", "iron-plate",
    } <= prerequisites
    assert "splitter" not in prerequisites
    assert "inserter" not in prerequisites


def test_active_drill_batch_is_not_deferred_behind_splitter_and_inserter(
    monkeypatch,
) -> None:
    task = SimpleNamespace(item="electric-mining-drill", target=6)
    targets = {"electric-mining-drill": 6, "splitter": 3, "inserter": 12}
    events: list[tuple] = []

    class Priorities:
        def describe(self, *_args):
            return "drill"

        def promote(self, *args):
            events.append(("promote", *args))

        def defer(self, *args, **_kwargs):
            events.append(("defer", *args))

    monkeypatch.setitem(builder.LINE_RECIPES, "electric-mining-drill", {
        "machine": "assembling-machine-2",
        "ingredients": ["electronic-circuit", "iron-gear-wheel", "iron-plate"],
        "amounts": [3, 5, 10], "product_amount": 1, "craft_time": 2.0,
    })
    monkeypatch.setattr(builder, "_belt_starved_consumer", lambda *_args: None)
    monkeypatch.setattr(
        builder, "_ensure_mall_item", lambda *_args, **_kwargs: (False, None),
    )
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_args: {})
    monkeypatch.setattr(
        builder, "_deliver_cell_ingredients", lambda *_args, **_kwargs: False,
    )

    builder._serve_mall_task(
        object(), object(), "nauvis", "player", task, 100, targets,
        Priorities(), (0.0, 0.0), lambda _message: None,
    )

    assert events == []


def test_missing_self_seed_after_profile_application_is_typed_bug(
    tmp_path: Path, monkeypatch,
) -> None:
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
    assert raised.value.classification == "bug"
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
    monkeypatch.setattr(
        builder, "preview_mall_allocation",
        lambda *_a: ((35, 31), "left"),
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


def test_rationed_mall_batches_low_demand_buildings_without_a_new_cell(
    monkeypatch,
) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "oil-refinery", {
        "machine": "assembling-machine-2", "ingredients": ["steel-plate"],
        "amounts": [15], "craft_time": 8.0, "product_amount": 1,
        "set_recipe": True,
    })
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: None)
    monkeypatch.setattr(
        builder, "_bootstrap_demand_cell_affordable",
        lambda *_a: (False, {"assembling-machine-2": 1}),
    )
    started = []
    monkeypatch.setattr(
        builder, "_start_bootstrap_loan",
        lambda *_a, **_k: started.append((_a[4], _a[5], _k))
        or "borrowed gear cell",
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_spare_target", lambda *_a: 3,
    )
    messages = []

    assert builder._rationed_mall_batch(
        object(), object(), "nauvis", "player", "oil-refinery", 1,
        (0.0, 0.0), messages.append,
    )
    assert started == [(
        "oil-refinery", 1, {"spare_target_count": 3},
    )]
    assert any("mixed provider contents are expected" in line for line in messages)


def test_pipe_is_a_rotating_batch_until_all_plate_pioneers_release(
    monkeypatch,
) -> None:
    monkeypatch.setattr(builder, "_all_plate_pioneers_released", lambda: False)
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: True)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: None)
    monkeypatch.setattr(
        builder, "_bootstrap_demand_cell_affordable",
        lambda *_a: (False, {"assembling-machine-2": 1}),
    )
    started: list[tuple[str, int]] = []
    monkeypatch.setattr(
        builder, "_start_bootstrap_loan",
        lambda *_a, **_k: started.append((_a[4], _a[5])) or "pipe batch",
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_spare_target", lambda *_a: 100,
    )

    assert builder._rationed_mall_batch(
        object(), object(), "nauvis", "player", "pipe", 40,
        (0.0, 0.0), lambda _message: None,
    )
    assert started == [("pipe", 40)]


def test_released_plate_districts_convert_a_stocked_demand_slot_for_pipe(
    monkeypatch,
) -> None:
    monkeypatch.setattr(builder, "_all_plate_pioneers_released", lambda: True)
    monkeypatch.setattr(builder, "_ensure_chemical_ladder_predecessor", lambda *_a: None)
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", None)
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: None)
    started: list[dict] = []

    def start(*_args, **kwargs):
        started.append(kwargs)
        return "converted splitter slot"

    monkeypatch.setattr(builder, "_start_bootstrap_loan", start)

    with pytest.raises(
        builder.ProductionPrerequisiteDeferred,
        match="converted splitter slot",
    ):
        builder.ensure_produced(
            object(), object(), "nauvis", "player", "pipe", (0.0, 0.0),
            lambda _message: None, upgrade_bootstrap=False, stock_target=20,
        )

    assert started == [{
        "spare_target_count": builder._CHEMICAL_BATCH_TARGETS["pipe"],
        "allowed_original_recipes": builder._PIPE_PERMANENT_DONORS,
        "allow_shared_provider": True,
        "require_stocked_original": True,
    }]


def test_completed_pipe_loan_is_promoted_only_from_a_demand_slot(
    monkeypatch,
) -> None:
    loan = builder.MallBootstrapLoan(
        original_recipe="splitter", target_item="pipe", target_count=100,
        side="left", requester_position=(39.5, 32.5), current_recipe="pipe",
    )
    submitted: list[str] = []
    monkeypatch.setattr(builder, "_all_plate_pioneers_released", lambda: True)
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock",
        lambda *_a: ({"pipe": 100}, {"pipe": 100}),
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, _p, name, _e: submitted.append(name),
    )

    result = builder._submit_bootstrap_loan(
        object(), object(), "nauvis", "player", loan,
        lambda _message: None,
    )

    assert result == (
        "converted borrowed splitter producer into the permanent pipe mall"
    )
    assert submitted == ["promote_bootstrap_loan_pipe"]


def test_permanent_pipe_conversion_accepts_a_stocked_shared_demand_slot(
    monkeypatch,
) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "splitter", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [5], "product_amount": 1, "craft_time": 1.0,
        "set_recipe": True,
    })
    machine_position = (32.5, 32.5)
    requester_position = (35.5, 32.5)
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {"splitter": 2},
    )
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a: SimpleNamespace(
            machine_count=1, machine_positions=(machine_position,),
        ) if _a[3] == "splitter" else None,
    )
    monkeypatch.setattr(
        builder, "locate_mall_cell", lambda *_a: ((31, 31), "left"),
    )
    monkeypatch.setattr(
        builder, "mall_slot_uses_shared_provider", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder.live_base, "entity_at",
        lambda _c, _s, position: {
            "name": (
                "assembling-machine-2"
                if position == machine_position else "requester-chest"
            ),
        } if position in {machine_position, requester_position} else None,
    )
    selected: list[builder.MallBootstrapLoan] = []
    monkeypatch.setattr(
        builder, "_submit_bootstrap_loan",
        lambda *_a, **_k: selected.append(_a[4]) or "started pipe conversion",
    )

    result = builder._start_bootstrap_loan(
        object(), object(), "nauvis", "player", "pipe", 100,
        (0.0, 0.0), lambda _message: None,
        allowed_original_recipes=builder._PIPE_PERMANENT_DONORS,
        allow_shared_provider=True,
        require_stocked_original=True,
    )

    assert result == "started pipe conversion"
    assert selected[0].original_recipe == "splitter"
    assert selected[0].requester_position == requester_position


def test_steel_target_bypasses_mall_reservation_policy(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        builder, "ensure_produced",
        lambda *_a, **kwargs: calls.append(kwargs) or (10.5, 8.5),
    )
    monkeypatch.setattr(
        builder, "mall_reserve_for",
        lambda *_a: pytest.fail("steel must not use mall reserve policy"),
    )
    messages: list[str] = []

    ready, output = builder._ensure_mall_item(
        object(), object(), "nauvis", "player", "steel-plate", 5, {},
        (0.0, 0.0), messages.append, background=False,
    )

    assert ready and output == (10.5, 8.5)
    assert calls == [{
        "upgrade_bootstrap": True,
        "stock_target": 5,
        "minimum_machines": builder.STEEL_BASELINE_FURNACES,
        "allow_promotion": False,
    }]
    assert messages[0].startswith("--- steel starter:")


def test_affordable_bootstrap_demand_claims_a_new_shared_output_slot(
    monkeypatch,
) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "electric-mining-drill", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [10], "craft_time": 2.0, "product_amount": 1,
        "set_recipe": True,
    })
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: None)
    monkeypatch.setattr(
        builder, "_bootstrap_demand_cell_affordable",
        lambda *_a: (True, {}),
    )
    monkeypatch.setattr(builder, "_BOOTSTRAP_SHARED_PROVIDER_ITEMS", set())
    monkeypatch.setattr(
        builder, "_start_bootstrap_loan",
        lambda *_a, **_k: pytest.fail("affordable demand should own a slot"),
    )
    messages: list[str] = []

    assert not builder._rationed_mall_batch(
        object(), object(), "nauvis", "player", "electric-mining-drill", 6,
        (0.0, 0.0), messages.append,
    )
    assert builder._BOOTSTRAP_SHARED_PROVIDER_ITEMS == {
        "electric-mining-drill",
    }
    assert any("10-assembler pool" in message for message in messages)


def test_bootstrap_shared_output_retrofit_queues_its_exact_bill(monkeypatch) -> None:
    plan = {"phases": [{"actions": [
        {"action_type": "place_entity", "entity": "passive-provider-chest"},
        {"action_type": "place_entity", "entity": "fast-inserter"},
    ]}]}
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", None)
    monkeypatch.setattr(
        builder, "next_shared_provider_retrofit_plan",
        lambda *_a: ("advanced-circuit", (35, 31), plan),
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"passive-provider-chest": 1},
    )
    targets: dict[str, int] = {}

    assert not builder._retrofit_bootstrap_mall_outputs(
        object(), object(), "nauvis", "player", targets,
        (3.0, -1.0), lambda _message: None,
    )
    assert targets == {"fast-inserter": 1}


def test_bootstrap_shared_output_retrofits_once_funded(monkeypatch) -> None:
    plan = {"phases": [{"actions": [
        {"action_type": "place_entity", "entity": "passive-provider-chest"},
        {"action_type": "place_entity", "entity": "fast-inserter"},
    ]}]}
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", None)
    monkeypatch.setattr(
        builder, "next_shared_provider_retrofit_plan",
        lambda *_a: ("advanced-circuit", (35, 31), plan),
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"passive-provider-chest": 1, "fast-inserter": 1},
    )
    submitted: list[str] = []
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, _p, name, _e: submitted.append(name),
    )

    assert builder._retrofit_bootstrap_mall_outputs(
        object(), object(), "nauvis", "player", {},
        (3.0, -1.0), lambda _message: None,
    )
    assert submitted == ["retrofit_shared_mall_advanced-circuit_35_31"]


def test_new_rationed_target_services_and_restores_the_active_loan(
    monkeypatch,
) -> None:
    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable",
        target_item="splitter",
        target_count=3,
        side="left",
        requester_position=(50.5, 32.5),
        current_recipe="splitter",
    )
    submitted = []
    messages: list[str] = []
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"electric-mining-drill": 5, "splitter": 10},
    )
    monkeypatch.setattr(
        builder, "active_bootstrap_loans", lambda *_a: (loan,),
    )
    monkeypatch.setattr(
        builder, "_submit_bootstrap_loan",
        lambda *_a, **_k: submitted.append(_a[4])
        or "restored borrowed copper-cable producer after seed completion",
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_spare_target", lambda *_a: 8,
    )

    assert builder._rationed_mall_batch(
        object(), object(), "nauvis", "player", "electric-mining-drill", 6,
        (0.0, 0.0), messages.append,
    )

    assert submitted == [loan]
    assert any("LOAN HANDOFF" in message for message in messages)
    assert not any("no borrowable assembler" in message for message in messages)


def test_completed_prior_loan_is_restored_before_the_new_batch(
    monkeypatch,
) -> None:
    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable",
        target_item="splitter",
        target_count=3,
        side="left",
        requester_position=(50.5, 32.5),
        current_recipe="splitter",
    )
    submitted: list[tuple[str, dict]] = []
    messages: list[str] = []
    monkeypatch.setattr(
        builder, "active_bootstrap_loans", lambda *_a: (loan,),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock",
        lambda *_a: ({"splitter": 10}, {"splitter": 10}),
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, name, _e: submitted.append((name, plan)),
    )

    result = builder._start_bootstrap_loan(
        object(), object(), "nauvis", "player", "electric-mining-drill", 6,
        (0.0, 0.0), messages.append,
    )

    assert result == (
        "restored borrowed copper-cable producer after spare production was preempted"
    )
    assert submitted[0][0] == "restore_bootstrap_loan_splitter"
    machine = next(
        action for action in submitted[0][1]["phases"][0]["actions"]
        if action["entity"] == "assembling-machine-2"
    )
    assert machine["recipe"] == "copper-cable"
    assert any("LOAN RESTORED" in message for message in messages)


def test_consumed_loan_output_is_fulfilled_by_monotonic_craft_count(
    monkeypatch,
) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "splitter", {
        "machine": "assembling-machine-2",
        "ingredients": ["electronic-circuit", "iron-plate", "transport-belt"],
        "amounts": [5, 5, 4], "product_amount": 1, "craft_time": 1.0,
    })
    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable", target_item="splitter",
        target_count=3, side="left", requester_position=(50.5, 32.5),
        current_recipe="splitter", step_recipe="splitter",
        step_baseline_finished=40, step_required_crafts=3,
    )
    ingredients = {
        item: 100 for item in builder.LINE_RECIPES["splitter"]["ingredients"]
    }
    stock = {**ingredients, "splitter": 0}
    submitted: list[str] = []
    messages: list[str] = []
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: (stock, stock),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 43,
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, _p, name, _e: submitted.append(name),
    )

    result = builder._submit_bootstrap_loan(
        object(), object(), "nauvis", "player", loan, messages.append,
    )

    assert result == "restored borrowed copper-cable producer after seed completion"
    assert submitted == ["restore_bootstrap_loan_splitter"]
    assert any("LOAN FULFILLED" in message for message in messages)


def test_rotating_loan_restores_before_advanced_circuit_without_plastic(
    monkeypatch,
) -> None:
    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable", target_item="requester-chest",
        target_count=3, side="left", requester_position=(50.5, 32.5),
        current_recipe="copper-cable",
    )
    step = SimpleNamespace(
        recipe="advanced-circuit", target_count=1, crafts=1,
    )
    submitted: list[tuple[str, dict]] = []
    ladder_calls: list[tuple[str, tuple[float, float]]] = []
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: ({}, {}),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 0,
    )
    monkeypatch.setattr(builder, "next_bootstrap_step", lambda *_a: step)
    monkeypatch.setattr(
        builder, "_missing_chemical_ladder_predecessor",
        lambda *_a: "plastic-bar",
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, name, _e, **_k: submitted.append((name, plan)),
    )

    def defer_to_ladder(_c, _b, _s, _f, item, reference, _emit):
        ladder_calls.append((item, reference))
        raise builder.ProductionPrerequisiteDeferred("plastic first")

    monkeypatch.setattr(builder, "_ensure_chemical_ladder_predecessor", defer_to_ladder)

    with pytest.raises(builder.ProductionPrerequisiteDeferred, match="plastic first"):
        builder._submit_bootstrap_loan(
            object(), object(), "nauvis", "player", loan,
            lambda _message: None, reference_point=(3.0, -1.0),
        )

    assert [name for name, _plan in submitted] == [
        "restore_bootstrap_loan_requester-chest",
    ]
    restored_machine = next(
        action for action in actions(submitted[0][1])
        if action.get("entity") == "assembling-machine-2"
    )
    assert restored_machine["recipe"] == "copper-cable"
    assert ladder_calls == [("advanced-circuit", (3.0, -1.0))]


def test_active_loan_polls_once_and_reports_monotonic_progress(monkeypatch) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "splitter", {
        "machine": "assembling-machine-2",
        "ingredients": ["electronic-circuit", "iron-plate", "transport-belt"],
        "amounts": [5, 5, 4], "product_amount": 1, "craft_time": 1.0,
    })
    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable", target_item="splitter",
        target_count=3, side="left", requester_position=(50.5, 32.5),
        current_recipe="splitter", step_recipe="splitter",
        step_baseline_finished=40, step_required_crafts=3,
    )
    ingredients = {
        item: 100 for item in builder.LINE_RECIPES["splitter"]["ingredients"]
    }
    # Two of the original three outputs still exist. The current stock bill is
    # only one, but the durable loan must still require all three crafts.
    stock = {**ingredients, "splitter": 2}
    counters = iter((42, 43))
    waits: list[str] = []
    sleeps: list[float] = []
    messages: list[str] = []
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: (stock, stock),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: next(counters),
    )
    monkeypatch.setattr(builder, "_deliver_cell_ingredients", lambda *_a: None)
    monkeypatch.setattr(builder, "consume_wait", waits.append)
    monkeypatch.setattr(builder.time, "sleep", sleeps.append)
    monkeypatch.setattr(builder, "_BOOTSTRAP_LOAN_PROGRESS_REVISION", 7)

    result = builder._submit_bootstrap_loan(
        object(), object(), "nauvis", "player", loan, messages.append,
    )

    assert "producing temporary splitter" in result
    assert waits == ["bootstrap_loan_splitter"]
    assert sleeps == [builder._BOOTSTRAP_LOAN_POLL_SECONDS]
    assert builder._BOOTSTRAP_LOAN_PROGRESS_REVISION == 8
    assert any("craft count advanced 42 -> 43" in message for message in messages)


def test_loan_gate_rechecks_stock_after_the_poll(monkeypatch) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "splitter", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [1], "product_amount": 1, "craft_time": 1.0,
    })
    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable", target_item="splitter",
        target_count=3, side="left", requester_position=(50.5, 32.5),
        current_recipe="splitter", step_recipe="splitter",
        step_baseline_finished=40, step_required_crafts=3,
    )
    stock_reads = iter((
        ({"iron-plate": 100, "splitter": 0},) * 2,
        ({"iron-plate": 100, "splitter": 3},) * 2,
    ))
    monkeypatch.setattr(builder, "_bootstrap_loan_stock", lambda *_a: next(stock_reads))
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 40,
    )
    monkeypatch.setattr(builder, "_deliver_cell_ingredients", lambda *_a: None)
    monkeypatch.setattr(builder, "consume_wait", lambda *_a: None)
    monkeypatch.setattr(builder.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(
        builder.live_base, "entity_status_name",
        lambda *_a: "disabled_by_control_behavior",
    )

    result = builder._submit_bootstrap_loan(
        object(), object(), "nauvis", "player", loan, lambda _message: None,
    )

    assert "producing temporary splitter" in result


def test_competing_batch_preempts_optional_spares_after_minimum_crafts(
    monkeypatch,
) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "splitter", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [1], "product_amount": 1, "craft_time": 1.0,
    })
    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable", target_item="splitter",
        target_count=3, spare_target_count=50, side="left",
        requester_position=(50.5, 32.5), current_recipe="splitter",
        step_recipe="splitter", step_baseline_finished=40,
        step_required_crafts=50, step_minimum_crafts=3,
    )
    stock = {"iron-plate": 100, "splitter": 0}
    submitted: list[str] = []
    messages: list[str] = []
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: (loan,))
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: (stock, stock),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 43,
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, _p, name, _e: submitted.append(name),
    )

    result = builder._start_bootstrap_loan(
        object(), object(), "nauvis", "player", "electric-mining-drill", 6,
        (0.0, 0.0), messages.append,
    )

    assert "spare production was preempted" in result
    assert submitted == ["restore_bootstrap_loan_splitter"]
    assert any("LOAN PREEMPT" in message for message in messages)


def test_active_batch_keeps_making_spares_without_a_competitor(monkeypatch) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "splitter", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [1], "product_amount": 1, "craft_time": 1.0,
    })
    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable", target_item="splitter",
        target_count=3, spare_target_count=50, side="left",
        requester_position=(50.5, 32.5), current_recipe="splitter",
        step_recipe="splitter", step_baseline_finished=40,
        step_required_crafts=50, step_minimum_crafts=3,
    )
    stock = {"iron-plate": 100, "splitter": 3}
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: (stock, stock),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 43,
    )
    monkeypatch.setattr(builder, "_deliver_cell_ingredients", lambda *_a: None)
    monkeypatch.setattr(builder, "consume_wait", lambda *_a: None)
    monkeypatch.setattr(builder.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(builder.live_base, "entity_status_name", lambda *_a: "working")

    result = builder._submit_bootstrap_loan(
        object(), object(), "nauvis", "player", loan, lambda _message: None,
    )

    assert "producing temporary splitter" in result


def test_optional_spares_do_not_expand_prerequisites_before_blocking_bill(
    monkeypatch,
) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "splitter", {
        "machine": "assembling-machine-2", "ingredients": ["iron-gear-wheel"],
        "amounts": [1], "product_amount": 1, "craft_time": 1.0,
    })
    monkeypatch.setitem(builder.LINE_RECIPES, "iron-gear-wheel", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [2], "product_amount": 1, "craft_time": 0.5,
    })
    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable", target_item="splitter",
        target_count=3, spare_target_count=50, side="left",
        requester_position=(50.5, 32.5), current_recipe="copper-cable",
    )
    stock = {"iron-plate": 100, "iron-gear-wheel": 0, "splitter": 0}
    submitted: list[dict] = []
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: (stock, stock),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 10,
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, _name, _e: submitted.append(plan),
    )

    builder._submit_bootstrap_loan(
        object(), object(), "nauvis", "player", loan,
        lambda _message: None,
    )

    actions = submitted[0]["phases"][0]["actions"]
    machine = next(
        action for action in actions
        if action["entity"] == "assembling-machine-2"
    )
    requester = next(
        action for action in actions if action["entity"] == "requester-chest"
    )
    assert machine["recipe"] == "iron-gear-wheel"
    assert requester["logistic_sections"][0]["multiplier"] == 3


def test_completed_bill_enters_durable_spare_phase(monkeypatch) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "splitter", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [1], "product_amount": 1, "craft_time": 1.0,
    })
    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable", target_item="splitter",
        target_count=3, spare_target_count=50, side="left",
        requester_position=(50.5, 32.5), current_recipe="splitter",
        step_recipe="splitter", step_baseline_finished=40,
        step_required_crafts=3, step_minimum_crafts=3,
    )
    stock = {"iron-plate": 100, "splitter": 0}
    submitted: list[dict] = []
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: (stock, stock),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 43,
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, _name, _e: submitted.append(plan),
    )

    builder._submit_bootstrap_loan(
        object(), object(), "nauvis", "player", loan,
        lambda _message: None,
    )

    actions = submitted[0]["phases"][0]["actions"]
    requester = next(
        action for action in actions if action["entity"] == "requester-chest"
    )
    group = requester["logistic_sections"][0]["group"]
    assert group.endswith(":splitter:43:50:0")


def test_rationing_ends_after_core_mall_producers_are_live(monkeypatch) -> None:
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: True)
    monkeypatch.setattr(
        builder, "_start_bootstrap_loan",
        lambda *_a: pytest.fail("self-sustaining mall must not borrow a cell"),
    )

    assert not builder._rationed_mall_batch(
        object(), object(), "nauvis", "player", "pumpjack", 1,
        (0.0, 0.0), lambda _message: None,
    )


def test_recipe_loan_never_borrows_the_last_gear_or_cable_machine(
    monkeypatch,
) -> None:
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"iron-gear-wheel": 100, "copper-cable": 100},
    )

    def line(_client, _surface, _force, recipe, _machine):
        if recipe not in {"iron-gear-wheel", "copper-cable"}:
            return None
        return SimpleNamespace(
            machine_count=1, machine_positions=((10.5, 10.5),),
        )

    monkeypatch.setattr(builder.live_base, "find_line", line)

    assert builder._start_bootstrap_loan(
        object(), object(), "nauvis", "player", "splitter", 2,
        (0.0, 0.0), lambda _message: None,
    ) is None
