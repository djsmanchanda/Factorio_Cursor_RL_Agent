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
    monkeypatch.setattr(stage_services.live_base, "transferable_items", lambda *_a: stock)
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


def test_critical_prerequisite_keeps_its_stock_when_total_is_one_short(
    tmp_path: Path, monkeypatch,
) -> None:
    """Steel's three anchors outrank the downstream project they unlock."""
    ledger = _ledger(tmp_path)
    stock = {"medium-electric-pole": 10}
    ledger.declare(
        "conversion_automation-science-pack",
        {"medium-electric-pole": 4}, stock,
    )
    ledger.declare(
        "modular_stone-brick_refinery",
        {"medium-electric-pole": 4}, stock,
    )
    set_active_material_ledger(ledger)
    monkeypatch.setattr(
        stage_services.live_base, "transferable_items", lambda *_a: stock,
    )
    plan = {"phases": [{"actions": [
        {"action_type": "place_ghost", "entity": "medium-electric-pole"}
        for _ in range(3)
    ]}]}
    try:
        stage_services.assert_affordable(
            object(), "nauvis", "player", plan, "conversion_steel-plate",
            lambda _m: None, True, reservation_priority=100,
        )
    finally:
        set_active_material_ledger(None)

    assert ledger.projects["conversion_steel-plate"].reserved == {
        "medium-electric-pole": 3,
    }
    assert ledger.shortage_targets("conversion_steel-plate", stock) == {}
    assert ledger.projects["modular_stone-brick_refinery"].reserved == {
        "medium-electric-pole": 3,
    }


def test_parent_preflight_can_claim_its_child_packet_reservations(
    tmp_path: Path, monkeypatch,
) -> None:
    """A foundation retry must not demand a second mine/refinery bill.

    The combined ``initial_*_system`` preflight and the submitted
    ``mining_*``/``modular_*_refinery`` packets describe the same physical
    transaction under different persisted names.
    """
    ledger = _ledger(tmp_path)
    stock = {"transport-belt": 128, "splitter": 3, "substation": 1}
    ledger.declare(
        "mining_iron-ore", {"transport-belt": 13, "substation": 1}, stock,
    )
    ledger.declare(
        "modular_iron-plate_refinery",
        {"transport-belt": 115, "splitter": 3}, stock,
    )
    set_active_material_ledger(ledger)
    monkeypatch.setattr(stage_services.live_base, "transferable_items", lambda *_a: stock)
    plan = {"phases": [{"actions": [
        *[
            {"action_type": "place_ghost", "entity": "transport-belt"}
            for _ in range(128)
        ],
        *[
            {"action_type": "place_ghost", "entity": "splitter"}
            for _ in range(3)
        ],
        {"action_type": "place_ghost", "entity": "substation"},
    ]}]}
    messages: list[str] = []
    try:
        stage_services.assert_affordable(
            object(), "nauvis", "player", plan, "initial_iron-plate_system",
            messages.append,
            reservation_claimants=(
                "mining_iron-ore", "modular_iron-plate_refinery",
            ),
        )
    finally:
        set_active_material_ledger(None)

    assert any("132 ghost items allocated" in message for message in messages)


def test_reused_submission_name_gets_a_fresh_transient_reservation(
    tmp_path: Path, monkeypatch,
) -> None:
    ledger = _ledger(tmp_path)
    stock = {"substation": 2}
    set_active_material_ledger(ledger)
    monkeypatch.setattr(stage_services.live_base, "available_items", lambda *_a: stock)
    monkeypatch.setattr(stage_services.live_base, "transferable_items", lambda *_a: stock)
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


def test_parent_defers_behind_newly_discovered_construction_material(
    monkeypatch,
) -> None:
    """A starter build's non-recipe material must run before its retry."""
    task = SimpleNamespace(item="steel-plate", target=4)
    targets = {"steel-plate": 4}
    events: list[tuple] = []

    class Priorities:
        def describe(self, *_a):
            return "steel"

        def promote(self, item, target, tick):
            events.append(("promote", item, target, tick))

        def defer(self, item, tick, reason, **_kwargs):
            events.append(("defer", item, tick, reason))

    def ensure(*_args, **_kwargs):
        targets["small-electric-pole"] = 3
        return False, None

    monkeypatch.setattr(builder, "_belt_starved_consumer", lambda *_a: None)
    monkeypatch.setattr(builder, "_ensure_mall_item", ensure)
    monkeypatch.setattr(builder, "_queued_mall_prerequisites", lambda *_a: set())
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 500)
    monkeypatch.setattr(
        builder, "_deliver_cell_ingredients",
        lambda *_a, **_k: pytest.fail("parent retried before its new material"),
    )

    builder._serve_mall_task(
        object(), object(), "nauvis", "player", task, 100, targets,
        Priorities(), (0.0, 0.0), lambda _message: None,
    )

    assert events[0] == ("promote", "small-electric-pole", 3, 500)
    assert events[1][0:3] == ("defer", "steel-plate", 500)


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
    monkeypatch.setattr(builder.live_base, "transferable_items", lambda *_a: {})
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
    monkeypatch.setattr(builder.live_base, "transferable_items", lambda *_a: {})
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
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"steel-plate": 15},
    )
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
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


def test_inserter_uses_a_rotating_batch_when_the_bootstrap_pool_is_full(
    monkeypatch,
) -> None:
    """Stone's inserter burst must not escape the hard ten-slot cap."""
    assert "inserter" in builder.RATIONED_MALL_BATCH_ITEMS
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {
            "inserter": 0, "electronic-circuit": 12,
            "iron-gear-wheel": 12, "iron-plate": 12,
        },
    )
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
    # A false affordability result with no material shortage is the global
    # slot-cap path.  The item must borrow an existing cell rather than defer
    # until a non-existent promotion releases one.
    monkeypatch.setattr(
        builder, "_bootstrap_demand_cell_affordable", lambda *_a: (False, {}),
    )
    monkeypatch.setattr(builder, "_rationed_mall_spare_target", lambda *_a: 12)
    borrowed: list[tuple[str, int]] = []
    monkeypatch.setattr(
        builder, "_start_bootstrap_loan",
        lambda *_a, **_k: borrowed.append((_a[4], _a[5])) or "borrowed splitter cell",
    )

    assert builder._rationed_mall_batch(
        object(), object(), "nauvis", "player", "inserter", 12,
        (0.0, 0.0), lambda _message: None,
    )
    assert borrowed == [("inserter", 12)]


def test_assembling_machine_one_uses_a_rotating_batch_at_bootstrap_cap(
    monkeypatch,
) -> None:
    """The AM1 needed to build the core mall must not allocate slot eleven."""
    monkeypatch.setitem(builder.LINE_RECIPES, "assembling-machine-1", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [9], "craft_time": 0.5, "product_amount": 1,
        "set_recipe": True,
    })
    assert "assembling-machine-1" in builder.RATIONED_MALL_BATCH_ITEMS
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {
            "assembling-machine-1": 0, "iron-plate": 9,
        },
    )
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
    monkeypatch.setattr(
        builder, "_bootstrap_demand_cell_affordable", lambda *_a: (False, {}),
    )
    monkeypatch.setattr(builder, "_rationed_mall_spare_target", lambda *_a: 3)
    borrowed: list[tuple[str, int]] = []
    monkeypatch.setattr(
        builder, "_start_bootstrap_loan",
        lambda *_a, **_k: borrowed.append((_a[4], _a[5])) or "borrowed gear cell",
    )

    assert builder._rationed_mall_batch(
        object(), object(), "nauvis", "player", "assembling-machine-1", 1,
        (0.0, 0.0), lambda _message: None,
    )
    assert borrowed == [("assembling-machine-1", 1)]


def test_fast_tier_batch_cannot_bypass_the_fast_belt_capability_gate(
    monkeypatch,
) -> None:
    """Nested fast-belt recipes must wait before a loan configures a cell."""
    monkeypatch.setitem(builder.LINE_RECIPES, "fast-underground-belt", {
        "machine": "assembling-machine-2",
        "ingredients": ["fast-transport-belt"], "amounts": [2],
        "craft_time": 0.5, "product_amount": 2, "set_recipe": True,
    })
    assert "fast-underground-belt" in builder.RATIONED_MALL_BATCH_ITEMS
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(
        builder, "_fast_transport_belt_gate_open", lambda *_a: False,
    )
    monkeypatch.setattr(
        builder, "_start_bootstrap_loan",
        lambda *_a, **_k: pytest.fail("fast-tier loan bypassed its capability gate"),
    )

    assert not builder._rationed_mall_batch(
        object(), object(), "nauvis", "player", "fast-underground-belt", 2,
        (0.0, 0.0), lambda _message: None,
    )


def test_pipe_is_a_rotating_batch_until_all_plate_pioneers_release(
    monkeypatch,
) -> None:
    monkeypatch.setattr(builder, "_all_plate_pioneers_released", lambda: False)
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: True)
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {"iron-plate": 40},
    )
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
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


def test_pipe_stays_rotating_until_the_core_mall_is_self_sufficient(
    monkeypatch,
) -> None:
    """Released plate pioneers alone must not create a permanent pipe slot."""
    monkeypatch.setattr(builder, "_all_plate_pioneers_released", lambda: True)
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {"iron-plate": 40},
    )
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
    monkeypatch.setattr(
        builder, "_bootstrap_demand_cell_affordable",
        lambda *_a: (False, {"assembling-machine-2": 1}),
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_spare_target", lambda *_a: 42,
    )
    started: list[tuple[str, int]] = []
    monkeypatch.setattr(
        builder, "_start_bootstrap_loan",
        lambda *_a, **_k: started.append((_a[4], _a[5])) or "pipe batch",
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
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: True)
    monkeypatch.setattr(builder, "_ensure_chemical_ladder_predecessor", lambda *_a: None)
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", None)
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
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
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: True)
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
        lambda *_a, **_k: SimpleNamespace(
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
        } if position in {machine_position, requester_position} else (
            {"name": "passive-provider-chest"}
            if tuple(position) == (35.5, 31.5) else None
        ),
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


def test_steel_conversion_shortage_starts_small_pole_batch(monkeypatch) -> None:
    """A conversion bill cannot leave its unproduced small poles queued idle."""
    targets: dict[str, int] = {}
    batches: list[tuple[str, int]] = []
    messages: list[str] = []

    def raise_conversion_shortage(*_args, **_kwargs):
        raise MaterialShortage(
            "conversion_steel-plate",
            {"inserter": 20, "small-electric-pole": 3}, {},
        )

    monkeypatch.setattr(builder, "ensure_produced", raise_conversion_shortage)
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock", lambda *_a: {},
    )
    monkeypatch.setattr(
        builder, "_production_started",
        lambda *_a, **_k: _a[3] == "inserter",
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_batch",
        lambda _c, _b, _s, _f, item, target, *_a, **_k:
        batches.append((item, target)) or True,
    )

    ready, output = builder._ensure_mall_item(
        object(), object(), "nauvis", "player", "steel-plate", 4, targets,
        (0.0, 0.0), messages.append, background=False,
    )

    assert not ready and output is None
    assert targets == {"inserter": 20, "small-electric-pole": 3}
    assert batches == [("small-electric-pole", 3)]
    assert any("CONVERSION MATERIAL BATCH" in message for message in messages)


def test_affordable_bootstrap_demand_claims_a_new_shared_output_slot(
    monkeypatch,
) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "electric-mining-drill", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [10], "craft_time": 2.0, "product_amount": 1,
        "set_recipe": True,
    })
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {"iron-plate": 60},
    )
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
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
    assert any("12-assembler pool" in message for message in messages)


def test_post_starter_demand_claims_free_slot_while_another_loan_runs(
    monkeypatch,
) -> None:
    """Independent full-stack batches should run concurrently after scarcity."""
    monkeypatch.setitem(builder.LINE_RECIPES, "electric-mining-drill", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [10], "craft_time": 2.0, "product_amount": 1,
        "set_recipe": True,
    })
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {"iron-plate": 60},
    )
    active = SimpleNamespace(target_item="assembling-machine-2")
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: (active,))
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
    monkeypatch.setattr(
        builder, "_bootstrap_demand_cell_affordable", lambda *_a: (True, {}),
    )
    monkeypatch.setattr(builder, "_BOOTSTRAP_SHARED_PROVIDER_ITEMS", set())
    monkeypatch.setattr(
        builder, "_start_bootstrap_loan",
        lambda *_a, **_k: pytest.fail("free post-starter slot should run in parallel"),
    )

    assert not builder._rationed_mall_batch(
        object(), object(), "nauvis", "player", "electric-mining-drill", 50,
        (0.0, 0.0), lambda _message: None,
    )
    assert builder._BOOTSTRAP_SHARED_PROVIDER_ITEMS == {
        "electric-mining-drill",
    }


def test_core_promotion_reclaims_a_slot_when_the_bootstrap_pool_is_full(
    monkeypatch,
) -> None:
    """A stocked core seed must not make the eight-slot cap a circular wait."""
    monkeypatch.setattr(builder, "_production_started", lambda *_a: False)
    monkeypatch.setattr(
        builder, "mall_slot_count",
        lambda *_a: builder.BOOTSTRAP_MALL_SLOT_TARGET,
    )
    calls: list[tuple[str, int, dict]] = []
    monkeypatch.setattr(
        builder,
        "_rationed_mall_batch",
        lambda *args, **kwargs: calls.append((args[4], args[5], kwargs)) or True,
    )
    monkeypatch.setattr(
        builder,
        "ensure_produced",
        lambda *_a, **_kwargs: pytest.fail(
            "a full bootstrap pool must reclaim a temporary cell first"
        ),
    )

    assert builder._prep_core_mall(
        object(), object(), "nauvis", "player", set(), {},
        (0.0, 0.0), lambda _message: None,
    )
    assert calls == [(
        "assembling-machine-2", 1, {"force_temporary": True},
    )]


def test_rotating_mall_establishes_unproduced_external_input_first(
    monkeypatch,
) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "assembling-machine-2", {
        "machine": "assembling-machine-2", "ingredients": ["steel-plate"],
        "amounts": [2], "product_amount": 1, "craft_time": 0.5,
    })
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())
    monkeypatch.setattr(builder, "_bootstrap_loan_stock", lambda *_a: ({}, {}))
    monkeypatch.setattr(builder, "_production_started", lambda *_a: False)
    ensured: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        builder, "ensure_produced",
        lambda *args, **kwargs: ensured.append((args[4], kwargs)),
    )
    monkeypatch.setattr(
        builder, "_start_bootstrap_loan",
        lambda *_a, **_k: pytest.fail(
            "the rotating slot must not request an unproduced input"
        ),
    )
    messages: list[str] = []

    assert builder._rationed_mall_batch(
        object(), object(), "nauvis", "player", "assembling-machine-2", 1,
        (0.0, 0.0), messages.append, force_temporary=True,
    )
    assert ensured == [(
        "steel-plate",
        {
            "upgrade_bootstrap": True,
            "stock_target": 2,
            "minimum_machines": 1,
            "allow_promotion": False,
        },
    )]
    assert any("ROTATING MALL SWITCH" in message for message in messages)


def test_active_rotating_loan_restores_before_external_handoff(
    monkeypatch,
) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "assembling-machine-2", {
        "machine": "assembling-machine-2", "ingredients": ["steel-plate"],
        "amounts": [2], "product_amount": 1, "craft_time": 0.5,
    })
    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable", target_item="assembling-machine-2",
        target_count=1, side="left", requester_position=(50.5, 32.5),
        current_recipe="assembling-machine-2",
    )
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: (loan,))
    monkeypatch.setattr(builder, "_bootstrap_loan_stock", lambda *_a: ({}, {}))
    monkeypatch.setattr(builder, "_production_started", lambda *_a: False)
    restored: list[str] = []
    monkeypatch.setattr(
        builder, "_restore_bootstrap_loan",
        lambda *_a, **kwargs: restored.append(kwargs["reason"]),
    )
    established: list[tuple[str, int]] = []
    monkeypatch.setattr(
        builder, "ensure_produced",
        lambda *args, **kwargs: established.append(
            (str(args[4]), int(kwargs["stock_target"]))
        ),
    )

    with pytest.raises(
        builder.ProductionPrerequisiteDeferred,
    ) as deferred:
        builder._rationed_mall_batch(
            object(), object(), "nauvis", "player", "assembling-machine-2", 1,
            (0.0, 0.0), lambda _message: None, force_temporary=True,
        )

    assert restored == [
        "assembling-machine-2 needs unproduced external input steel-plate=2",
    ]
    assert established == [("steel-plate", 2)]
    assert deferred.value.code == "rotating_mall_prerequisite_handoff"


def test_logistic_chest_core_builds_steel_before_the_temporary_chest_batch(
    monkeypatch,
) -> None:
    """A chest loan cannot wait on the durable steel source it consumes."""
    monkeypatch.setitem(builder.LINE_RECIPES, "passive-provider-chest", {
        "machine": "assembling-machine-2",
        "ingredients": ["advanced-circuit", "electronic-circuit", "steel-chest"],
        "amounts": [1, 3, 1], "product_amount": 1, "craft_time": 0.5,
    })
    monkeypatch.setitem(builder.LINE_RECIPES, "steel-chest", {
        "machine": "assembling-machine-2", "ingredients": ["steel-plate"],
        "amounts": [8], "product_amount": 1, "craft_time": 0.5,
    })
    monkeypatch.setitem(builder.LINE_RECIPES, "advanced-circuit", {
        "machine": "assembling-machine-2", "ingredients": ["plastic-bar"],
        "amounts": [2], "product_amount": 1, "craft_time": 0.5,
    })
    started = {"assembling-machine-2", "fast-inserter"}
    monkeypatch.setattr(
        builder, "_production_started",
        lambda _c, _s, _f, item: item in started,
    )
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_args: {})
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        builder,
        "ensure_produced",
        lambda *_args, **kwargs: calls.append((_args[4], kwargs)),
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_batch",
        lambda *_args, **_kwargs: pytest.fail(
            "a chest prerequisite must be established before the borrowed cell"
        ),
    )

    assert builder._prep_core_mall(
        object(), object(), "nauvis", "player",
        {"_core_mall:assembling-machine-2", "_core_mall:fast-inserter"},
        {}, (0.0, 0.0), lambda _message: None,
    )
    assert calls == [(
        "steel-plate",
        {
            "upgrade_bootstrap": True,
            "temporary_mall": False,
            "stock_target": 1,
            "minimum_machines": 1,
            "allow_promotion": False,
        },
    )]


def test_steel_chest_upgrade_is_routed_to_the_temporary_mall(monkeypatch) -> None:
    """One chest seed must never open the generic two-assembler conversion line."""
    monkeypatch.setitem(builder.LINE_RECIPES, "steel-chest", {
        "machine": "assembling-machine-2", "ingredients": ["steel-plate"],
        "amounts": [8], "product_amount": 1, "craft_time": 0.5,
    })
    calls: list[tuple[str, int, bool]] = []

    def rationed(*args, **kwargs):
        calls.append((args[4], args[5], kwargs["force_temporary"]))
        return True

    monkeypatch.setattr(builder, "_rationed_mall_batch", rationed)
    monkeypatch.setattr(
        builder, "_is_pre_core_temporary_mall_item", lambda *_args: False,
    )

    with pytest.raises(builder.ProductionPrerequisiteDeferred):
        builder.ensure_produced(
            object(), object(), "nauvis", "player", "steel-chest",
            (0.0, 0.0), lambda _message: None,
        )

    assert calls == [("steel-chest", 1, True)]
    assert "steel-chest" not in builder.PERSISTENT_INTERMEDIATES


def test_core_chest_accepts_a_stocked_temporary_steel_seed(monkeypatch) -> None:
    """A completed loan may restore before its one chest seeds core promotion."""
    monkeypatch.setitem(builder.LINE_RECIPES, "passive-provider-chest", {
        "machine": "assembling-machine-2",
        "ingredients": ["advanced-circuit", "steel-chest"],
        "amounts": [1, 1], "product_amount": 1, "craft_time": 0.5,
    })
    monkeypatch.setattr(
        builder, "_production_started",
        lambda _c, _s, _f, item: item == "advanced-circuit",
    )
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_args: {"steel-chest": 1},
    )

    assert builder._core_mall_prerequisites(
        object(), "nauvis", "player", "passive-provider-chest",
    ) == ()


def test_logistic_chest_core_waits_for_advanced_circuit_ladder(
    monkeypatch,
) -> None:
    """Advanced circuits are not admitted until their oil/plastic gate runs."""
    monkeypatch.setitem(builder.LINE_RECIPES, "passive-provider-chest", {
        "machine": "assembling-machine-2",
        "ingredients": ["advanced-circuit", "electronic-circuit", "steel-chest"],
        "amounts": [1, 3, 1], "product_amount": 1, "craft_time": 0.5,
    })
    started = {"assembling-machine-2", "fast-inserter", "steel-chest"}
    monkeypatch.setattr(
        builder, "_production_started",
        lambda _c, _s, _f, item: item in started,
    )
    calls: list[str] = []

    def defer(*args, **_kwargs):
        calls.append(args[4])
        raise builder.ProductionPrerequisiteDeferred("plastic waits for oil")

    monkeypatch.setattr(builder, "ensure_produced", defer)
    messages: list[str] = []

    assert builder._prep_core_mall(
        object(), object(), "nauvis", "player",
        {"_core_mall:assembling-machine-2", "_core_mall:fast-inserter"},
        {}, (0.0, 0.0), messages.append,
    )
    assert calls == ["advanced-circuit"]
    assert any("gated on advanced-circuit" in message for message in messages)


def test_core_promotion_catches_forced_loan_prerequisite_deferral(
    monkeypatch,
) -> None:
    """A loan handoff is a wait, not an unhandled controller exception."""
    monkeypatch.setattr(builder, "_production_started", lambda *_args: False)
    monkeypatch.setattr(
        builder, "mall_slot_count",
        lambda *_args: builder.BOOTSTRAP_MALL_SLOT_TARGET,
    )
    monkeypatch.setattr(
        builder,
        "_rationed_mall_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            builder.ProductionPrerequisiteDeferred("restore then reobserve")
        ),
    )
    messages: list[str] = []

    assert builder._prep_core_mall(
        object(), object(), "nauvis", "player", set(), {},
        (0.0, 0.0), messages.append,
    )
    assert any("waits while restore then reobserve" in message for message in messages)


def test_completed_core_loan_is_promoted_in_place_even_when_seed_is_stocked(
    monkeypatch,
) -> None:
    """Core conversion must finish instead of restoring into the same cap."""
    monkeypatch.setitem(builder.LINE_RECIPES, "assembling-machine-2", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [1], "product_amount": 1, "craft_time": 0.5,
        "set_recipe": True,
    })
    loan = builder.MallBootstrapLoan(
        original_recipe="iron-gear-wheel", target_item="assembling-machine-2",
        target_count=1, side="left", requester_position=(39.5, 32.5),
        current_recipe="iron-gear-wheel",
    )
    stock = {"assembling-machine-2": 1}
    submitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(builder, "_bootstrap_loan_stock", lambda *_a: (stock, stock))
    monkeypatch.setattr(
        builder,
        "_submit",
        lambda _c, _b, _s, plan, name, _e: submitted.append((name, plan)),
    )
    shared = {"assembling-machine-2"}
    monkeypatch.setattr(builder, "_BOOTSTRAP_SHARED_PROVIDER_ITEMS", shared)

    result = builder._submit_bootstrap_loan(
        object(), object(), "nauvis", "player", loan,
        lambda _message: None,
    )

    assert result == (
        "converted borrowed iron-gear-wheel producer into the permanent "
        "assembling-machine-2 mall"
    )
    assert [name for name, _plan in submitted] == [
        "promote_bootstrap_loan_assembling-machine-2",
    ]
    actions = submitted[0][1]["phases"][0]["actions"]
    requester = next(
        action for action in actions if action["entity"] == "requester-chest"
    )
    machine = next(
        action for action in actions if action["entity"] == loan.machine_name
    )
    assert requester["clear_logistic_groups"] == [
        loan.group, "mall:iron-gear-wheel", "mall:iron-gear-wheel:left",
    ]
    assert machine["logistic_condition"]["constant"] == 2
    assert shared == set()


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
    # No borrowable cell is free, so the new target serializes behind the
    # active loan instead of opening a parallel one.
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
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
    # No borrowable cell is free, so the new batch restores the completed
    # loan first instead of opening a parallel one.
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
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
        if action["entity"] == loan.machine_name
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


def _belt_loan() -> builder.MallBootstrapLoan:
    return builder.MallBootstrapLoan(
        original_recipe="iron-gear-wheel", target_item="transport-belt",
        target_count=122, spare_target_count=147, side="left",
        requester_position=(39.5, 38.5),
        current_recipe="transport-belt", step_recipe="transport-belt",
        step_baseline_finished=100, step_required_crafts=12,
    )


def test_drained_loan_keeps_producing_instead_of_restore_churn(
    monkeypatch,
) -> None:
    """2026-09-03 belt stall: 150 available hit the 147 spare ceiling while
    transferable sat at 138. The loan restored and re-borrowed every pass
    until the 12-pass guard tripped. Spendable stock below the bill must keep
    the cell producing, not release it."""
    monkeypatch.setitem(builder.LINE_RECIPES, "transport-belt", {
        "machine": "assembling-machine-2",
        "ingredients": ["iron-gear-wheel", "iron-plate"],
        "amounts": [1, 1], "product_amount": 2, "craft_time": 0.5,
    })
    stock = {"transport-belt": 150, "iron-gear-wheel": 100, "iron-plate": 100}
    submitted: list[str] = []
    messages: list[str] = []
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: (dict(stock), dict(stock)),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 112,
    )
    monkeypatch.setattr(
        builder.live_base, "transferable_items", lambda *_a: {},
    )
    monkeypatch.setattr(
        builder, "_missing_chemical_ladder_predecessor", lambda *_a: None,
    )
    monkeypatch.setattr(builder, "_deliver_cell_ingredients", lambda *_a: False)
    monkeypatch.setattr(builder, "consume_wait", lambda *_a: None)
    monkeypatch.setattr(builder.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(
        builder.live_base, "entity_status_name", lambda *_a: "working",
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, _p, name, _e: submitted.append(name),
    )
    client = SimpleNamespace(command=lambda *_a: "")

    result = builder._submit_bootstrap_loan(
        client, object(), "nauvis", "player", _belt_loan(), messages.append,
    )

    assert result.startswith("borrowed iron-gear-wheel cell is producing")
    assert submitted == []
    assert not any("RESTORED" in message for message in messages)
    assert not any("LOAN FULFILLED" in message for message in messages)


def test_spendable_loan_still_restores_on_true_completion(monkeypatch) -> None:
    """The churn fix must not pin the cell forever: 150 spendable belts for
    a 122 bill with a 147 ceiling restores exactly as before."""
    monkeypatch.setitem(builder.LINE_RECIPES, "transport-belt", {
        "machine": "assembling-machine-2",
        "ingredients": ["iron-gear-wheel", "iron-plate"],
        "amounts": [1, 1], "product_amount": 2, "craft_time": 0.5,
    })
    stock = {"transport-belt": 150, "iron-gear-wheel": 100, "iron-plate": 100}
    submitted: list[str] = []
    messages: list[str] = []
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: (dict(stock), dict(stock)),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 112,
    )
    monkeypatch.setattr(
        builder.live_base, "transferable_items", lambda *_a: dict(stock),
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, _p, name, _e: submitted.append(name),
    )
    client = SimpleNamespace(command=lambda *_a: "")

    result = builder._submit_bootstrap_loan(
        client, object(), "nauvis", "player", _belt_loan(), messages.append,
    )

    assert result == "restored borrowed iron-gear-wheel producer after seed completion"
    assert submitted == ["restore_bootstrap_loan_transport-belt"]


def test_reserved_but_flowing_input_funds_another_mall_cell(monkeypatch) -> None:
    """2026-09-03: free pool slots sat empty while reserved iron-plate sat in
    the provider and belts serialized on one AM1. A ledger-reserved input
    with a scheduled producer and spendable stock funds the cell from flow."""
    monkeypatch.setattr(builder, "mall_slot_count", lambda *_a: 5)
    monkeypatch.setattr(
        builder, "preview_mall_allocation",
        lambda *_a, **_k: ((0.0, 0.0), "left"),
    )
    monkeypatch.setattr(
        builder, "compact_mall_project_bill",
        lambda *_a, **_k: {"iron-plate": 2, "inserter": 2},
    )
    ledger = SimpleNamespace(
        allocatable_stock=lambda stock, **_k: {"inserter": 10},
    )
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setattr(
        builder.live_base, "transferable_items",
        lambda *_a: {"iron-plate": 20, "inserter": 10},
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"iron-plate": 20, "inserter": 10},
    )
    monkeypatch.setattr(
        builder, "construction_supply_chain_is_scheduled", lambda *_a: True,
    )
    client = SimpleNamespace(command=lambda *_a: "")
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: False)

    affordable, shortage = builder._bootstrap_demand_cell_affordable(
        client, "nauvis", "player", "transport-belt", 122, (0.0, 0.0),
    )

    assert affordable and shortage == {}


def test_stagnant_reserve_still_blocks_another_mall_cell(monkeypatch) -> None:
    """Flow funding must not spend a stagnant stockpile: with no scheduled
    producer the reserved shortage still waits."""
    monkeypatch.setattr(builder, "mall_slot_count", lambda *_a: 5)
    monkeypatch.setattr(
        builder, "preview_mall_allocation",
        lambda *_a, **_k: ((0.0, 0.0), "left"),
    )
    monkeypatch.setattr(
        builder, "compact_mall_project_bill",
        lambda *_a, **_k: {"iron-plate": 2, "inserter": 2},
    )
    ledger = SimpleNamespace(
        allocatable_stock=lambda stock, **_k: {"inserter": 10},
    )
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setattr(
        builder.live_base, "transferable_items",
        lambda *_a: {"iron-plate": 20, "inserter": 10},
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"iron-plate": 20, "inserter": 10},
    )
    monkeypatch.setattr(
        builder, "construction_supply_chain_is_scheduled", lambda *_a: False,
    )
    client = SimpleNamespace(command=lambda *_a: "")
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: False)

    affordable, shortage = builder._bootstrap_demand_cell_affordable(
        client, "nauvis", "player", "transport-belt", 122, (0.0, 0.0),
    )

    assert not affordable and shortage == {"iron-plate": 2}


def test_large_belt_backlog_earns_a_second_mall_cell(monkeypatch) -> None:
    """A lone belt cell facing a multi-minute backlog funds one parallel
    producer from the pool instead of serializing the whole foundation."""
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder, "_production_started", lambda *_a: False)
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_a: True,
    )
    monkeypatch.setitem(builder.LINE_RECIPES, "transport-belt", {
        "machine": "assembling-machine-2",
        "ingredients": ["iron-gear-wheel", "iron-plate"],
        "amounts": [1, 1], "product_amount": 2, "craft_time": 0.5,
    })
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a: SimpleNamespace(machine_count=1),
    )
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "backlog_seconds", lambda *_a: 240.0)
    monkeypatch.setattr(
        builder, "_bootstrap_demand_cell_affordable", lambda *_a: (True, {}),
    )
    monkeypatch.setattr(builder, "_BOOTSTRAP_SHARED_PROVIDER_ITEMS", set())
    messages: list[str] = []

    wanted = builder._bootstrap_reserve_machine_target(
        object(), "nauvis", "player", "transport-belt", 122, (0.0, 0.0),
        messages.append, background=False,
    )

    assert wanted == 2
    assert any("DYNAMIC MALL CAPACITY" in message for message in messages)


def _drill_loan(**overrides) -> builder.MallBootstrapLoan:
    fields = {
        "original_recipe": "iron-gear-wheel", "target_item": "electric-mining-drill",
        "target_count": 6, "spare_target_count": 8, "side": "left",
        "requester_position": (39.5, 38.5),
        "current_recipe": "electric-mining-drill",
        "step_recipe": "electric-mining-drill",
        "step_target_count": 6, "step_baseline_finished": 100,
        "step_required_crafts": 4, "step_minimum_crafts": 2,
    }
    fields.update(overrides)
    return builder.MallBootstrapLoan(**fields)


def _mock_drill_recipe(monkeypatch) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "electric-mining-drill", {
        "machine": "assembling-machine-2",
        "ingredients": ["electronic-circuit", "iron-gear-wheel", "iron-plate"],
        "amounts": [3, 5, 10], "product_amount": 1, "craft_time": 2.0,
    })


def test_spare_phase_refreshes_the_frozen_bill_gate(monkeypatch) -> None:
    """2026-09-03 drill stall: the machine gate froze at the bill of 6 while
    the loan step advanced to the spare ceiling of 8, so the cell disabled at
    6 and the run died on gate_mismatch. A drifted gate must reconfigure."""
    _mock_drill_recipe(monkeypatch)
    stock = {
        "electric-mining-drill": 6, "electronic-circuit": 100,
        "iron-gear-wheel": 100, "iron-plate": 100,
    }
    plans: list[dict] = []
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: (dict(stock), dict(stock)),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 101,
    )
    monkeypatch.setattr(
        builder.live_base, "transferable_items", lambda *_a: dict(stock),
    )
    monkeypatch.setattr(
        builder, "_missing_chemical_ladder_predecessor", lambda *_a: None,
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, name, _e: plans.append((name, plan)),
    )
    client = SimpleNamespace(command=lambda *_a: "")

    builder._submit_bootstrap_loan(
        client, object(), "nauvis", "player", _drill_loan(),
        lambda _message: None,
    )

    assert [name for name, _plan in plans] == [
        "bootstrap_loan_electric-mining-drill",
    ]
    machine = next(
        action for action in plans[0][1]["phases"][0]["actions"]
        if action.get("entity") == "assembling-machine-1"
    )
    assert machine["logistic_condition"] == {
        "signal": "electric-mining-drill", "comparator": "<", "constant": 8,
    }


def test_stale_disabled_reading_resumes_instead_of_ending_the_run(
    monkeypatch,
) -> None:
    """Status and stock are sampled seconds apart while bots drain the batch:
    a machine that already re-enabled must not end the run on the stale
    disabled reading."""
    _mock_drill_recipe(monkeypatch)
    stock = {
        "electric-mining-drill": 6, "electronic-circuit": 100,
        "iron-gear-wheel": 100, "iron-plate": 100,
    }
    messages: list[str] = []
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: (dict(stock), dict(stock)),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 100,
    )
    monkeypatch.setattr(
        builder.live_base, "transferable_items", lambda *_a: dict(stock),
    )
    statuses = iter(["disabled_by_control_behavior", "working"])
    monkeypatch.setattr(
        builder.live_base, "entity_status_name", lambda *_a: next(statuses),
    )
    monkeypatch.setattr(builder, "_deliver_cell_ingredients", lambda *_a: False)
    monkeypatch.setattr(builder, "consume_wait", lambda *_a: None)
    monkeypatch.setattr(builder.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(
        builder, "_missing_chemical_ladder_predecessor", lambda *_a: None,
    )
    client = SimpleNamespace(command=lambda *_a: "")

    result = builder._submit_bootstrap_loan(
        client, object(), "nauvis", "player",
        _drill_loan(step_target_count=8), messages.append,
    )

    assert "producing temporary" in result
    assert any("LOAN RESUMED" in message for message in messages)


def test_consumed_prerequisite_advances_and_persists_its_credit(
    monkeypatch,
) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "electric-mining-drill", {
        "machine": "assembling-machine-2",
        "ingredients": ["electronic-circuit", "iron-gear-wheel", "iron-plate"],
        "amounts": [3, 5, 10], "product_amount": 1, "craft_time": 2.0,
    })
    monkeypatch.setitem(builder.LINE_RECIPES, "iron-gear-wheel", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [2], "product_amount": 1, "craft_time": 0.5,
    })
    loan = builder.MallBootstrapLoan(
        original_recipe="electronic-circuit", target_item="electric-mining-drill",
        target_count=6, spare_target_count=8, side="right",
        requester_position=(50.5, 32.5), current_recipe="iron-gear-wheel",
        step_recipe="iron-gear-wheel", step_baseline_finished=324,
        step_required_crafts=25, step_minimum_crafts=25,
    )
    stock = {
        "electric-mining-drill": 1, "electronic-circuit": 15,
        "iron-gear-wheel": 0, "iron-plate": 100,
    }
    submitted: list[dict] = []
    messages: list[str] = []
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: (stock, stock),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 356,
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, _name, _e: submitted.append(plan),
    )

    result = builder._submit_bootstrap_loan(
        object(), object(), "nauvis", "player", loan, messages.append,
    )

    assert "temporary electric-mining-drill" in result
    actions = submitted[0]["phases"][0]["actions"]
    machine = next(
        action for action in actions
        if action["entity"] == loan.machine_name
    )
    requester = next(
        action for action in actions if action["entity"] == "requester-chest"
    )
    assert machine["recipe"] == "electric-mining-drill"
    assert "iron-gear-wheel=25" in requester["logistic_sections"][0]["group"]
    assert loan.group in requester["clear_logistic_groups"]
    assert any("PREREQUISITE FULFILLED" in message for message in messages)


def test_started_parent_step_does_not_regress_when_stock_is_consumed(
    monkeypatch,
) -> None:
    monkeypatch.setitem(builder.LINE_RECIPES, "electric-mining-drill", {
        "machine": "assembling-machine-2",
        "ingredients": ["electronic-circuit", "iron-gear-wheel", "iron-plate"],
        "amounts": [3, 5, 10], "product_amount": 1, "craft_time": 2.0,
    })
    monkeypatch.setitem(builder.LINE_RECIPES, "iron-gear-wheel", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [2], "product_amount": 1, "craft_time": 0.5,
    })
    loan = builder.MallBootstrapLoan(
        original_recipe="electronic-circuit", target_item="electric-mining-drill",
        target_count=6, spare_target_count=8, side="right",
        requester_position=(50.5, 32.5), current_recipe="electric-mining-drill",
        step_recipe="electric-mining-drill", step_target_count=6,
        step_baseline_finished=356, step_required_crafts=5,
        step_minimum_crafts=5,
        completed_step_targets=(("iron-gear-wheel", 25),),
    )
    stock = {
        "electric-mining-drill": 0, "electronic-circuit": 15,
        "iron-gear-wheel": 0, "iron-plate": 100,
    }
    counters = iter((356, 356))
    submitted: list[dict] = []
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: (stock, stock),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: next(counters),
    )
    monkeypatch.setattr(builder, "_deliver_cell_ingredients", lambda *_a: None)
    monkeypatch.setattr(builder, "consume_wait", lambda *_a: None)
    monkeypatch.setattr(builder.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(builder.live_base, "entity_status_name", lambda *_a: "working")
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, _name, _e: submitted.append(plan),
    )

    result = builder._submit_bootstrap_loan(
        object(), object(), "nauvis", "player", loan,
        lambda _message: None,
    )

    assert "temporary electric-mining-drill" in result
    assert submitted == []


def test_rotating_loan_restores_and_defers_before_missing_chemical_rung(
    monkeypatch,
) -> None:
    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable", target_item="pumpjack",
        target_count=1, side="left", requester_position=(50.5, 32.5),
        current_recipe="pumpjack",
    )
    step = SimpleNamespace(
        recipe="pumpjack", target_count=1, crafts=1,
    )
    submitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: ({}, {}),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 0,
    )
    monkeypatch.setattr(builder, "next_bootstrap_step", lambda *_a: step)
    monkeypatch.setattr(
        builder, "_missing_chemical_ladder_predecessor",
        lambda *_a: "chemical-plant",
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, name, _e, **_k: submitted.append((name, plan)),
    )

    monkeypatch.setattr(
        builder, "_ensure_chemical_ladder_predecessor",
        lambda *_a, **_k: pytest.fail(
            "the chemical ladder must be re-observed on the next pass"
        ),
    )

    with pytest.raises(
        builder.ProductionPrerequisiteDeferred,
        match="retrying after re-observation",
    ) as deferred:
        builder._submit_bootstrap_loan(
            object(), object(), "nauvis", "player", loan,
            lambda _message: None, reference_point=(3.0, -1.0),
        )

    assert [name for name, _plan in submitted] == [
        "restore_bootstrap_loan_pumpjack",
    ]
    restored_machine = next(
        action for action in actions(submitted[0][1])
        if action.get("entity") == loan.machine_name
    )
    assert restored_machine["recipe"] == "copper-cable"
    assert deferred.value.code == "chemical_capability_handoff"
    assert deferred.value.details == {
        "target": "pumpjack", "rung": "chemical-plant",
    }


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
    # No borrowable cell is free, so the competing batch serializes behind
    # the active loan instead of opening a parallel one.
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
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
        if action["entity"] == loan.machine_name
    )
    requester = next(
        action for action in actions if action["entity"] == "requester-chest"
    )
    assert machine["recipe"] == "iron-gear-wheel"
    # 3 step crafts ask for ceil(3 * 1.2): bounded headroom past the exact
    # batch so bots lagging the plan cannot dry the requester (2026-09-03).
    assert requester["logistic_sections"][0]["multiplier"] == 4


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
    assert ":splitter:50:43:50:0:" in group


def test_rationing_ends_after_plastic_releases_independent_mall(monkeypatch) -> None:
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: True)
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

    def line(_client, _surface, _force, recipe, _machine, **_kwargs):
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


def test_recipe_loan_never_reclaims_a_core_mall_producer(monkeypatch) -> None:
    """A permanent core cell is not spare capacity for a temporary batch."""
    core_machine = (36.5, 32.5)
    other_machine = (47.5, 32.5)
    origins = {
        core_machine: (35, 31),
        other_machine: (46, 31),
    }
    requesters = {(39.5, 32.5), (50.5, 32.5)}
    monkeypatch.setitem(builder.LINE_RECIPES, "assembling-machine-2", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [9], "product_amount": 1, "craft_time": 1.0,
        "set_recipe": True,
    })
    monkeypatch.setitem(builder.LINE_RECIPES, "splitter", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [5], "product_amount": 1, "craft_time": 1.0,
        "set_recipe": True,
    })
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"assembling-machine-2": 100, "splitter": 1},
    )

    def line(_client, _surface, _force, recipe, _machine, **_kwargs):
        positions = {
            "assembling-machine-2": (core_machine,),
            "splitter": (other_machine,),
        }.get(recipe)
        return (
            SimpleNamespace(machine_count=1, machine_positions=positions)
            if positions is not None else None
        )

    monkeypatch.setattr(builder.live_base, "find_line", line)
    monkeypatch.setattr(
        builder, "locate_mall_cell", lambda position, *_a: (origins[position], "left"),
    )
    monkeypatch.setattr(builder, "mall_slot_uses_shared_provider", lambda *_a: False)
    monkeypatch.setattr(
        builder.live_base, "entity_at",
        lambda _c, _s, position: (
            {"name": "assembling-machine-2"} if position in origins
            else {"name": "requester-chest"} if position in requesters
            else {"name": "passive-provider-chest"}
            if tuple(position) in {(39.5, 31.5), (50.5, 31.5)}
            else None
        ),
    )
    selected: list[builder.MallBootstrapLoan] = []
    monkeypatch.setattr(
        builder, "_submit_bootstrap_loan",
        lambda *_a, **_k: selected.append(_a[4]) or "started temporary batch",
    )

    assert builder._start_bootstrap_loan(
        object(), object(), "nauvis", "player", "temporary-target", 1,
        (3.0, -1.0), lambda _message: None,
    ) == "started temporary batch"
    assert selected[0].original_recipe == "splitter"


def _parallel_loan_world(monkeypatch, free_origin):
    """One active splitter loan plus a three-machine gear line."""
    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable", target_item="splitter",
        target_count=3, side="left",
        requester_position=(50.5, 32.5), current_recipe="splitter",
    )
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: (loan,))
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"iron-gear-wheel": 5},
    )

    def line(_client, _surface, _force, recipe, _machine, **_kwargs):
        if recipe != "iron-gear-wheel":
            return None
        return SimpleNamespace(
            machine_count=3,
            machine_positions=((36.5, 32.5), (37.5, 32.5), (38.5, 32.5)),
        )

    monkeypatch.setattr(builder.live_base, "find_line", line)
    monkeypatch.setattr(
        builder, "locate_mall_cell", lambda *_a: (free_origin, "left"),
    )
    monkeypatch.setattr(
        builder, "mall_slot_uses_shared_provider", lambda *_a: False,
    )
    monkeypatch.setattr(
        builder.live_base, "entity_at",
        lambda _c, _s, position: (
            {"name": "assembling-machine-1"}
            if position == (38.5, 32.5)
            else {"name": "requester-chest"}
            if position == (free_origin[0] + 4.5, free_origin[1] + 1.5)
            else {"name": "passive-provider-chest"}
            if tuple(position) == (free_origin[0] + 4.5, free_origin[1] + 0.5)
            else None
        ),
    )
    return loan


def test_parallel_loan_opens_on_a_free_cell(monkeypatch) -> None:
    """A new batch borrows a free cell instead of queueing behind a loan."""
    _parallel_loan_world(monkeypatch, (35, 31))
    submitted: list[builder.MallBootstrapLoan] = []
    messages: list[str] = []
    monkeypatch.setattr(
        builder, "_submit_bootstrap_loan",
        lambda *_a, **_k: submitted.append(_a[4]) or "parallel started",
    )

    result = builder._start_bootstrap_loan(
        object(), object(), "nauvis", "player", "electric-mining-drill", 6,
        (0.0, 0.0), messages.append,
    )

    assert result == "parallel started"
    assert submitted[0].target_item == "electric-mining-drill"
    assert submitted[0].original_recipe == "iron-gear-wheel"
    assert any("LOAN PARALLEL" in message for message in messages)
    assert not any("LOAN HANDOFF" in message for message in messages)


def test_loan_on_the_same_cell_falls_back_to_handoff(monkeypatch) -> None:
    """The active loan's own cell is never borrowed twice."""
    _parallel_loan_world(monkeypatch, (46, 31))
    submitted: list[builder.MallBootstrapLoan] = []
    messages: list[str] = []
    monkeypatch.setattr(
        builder, "_submit_bootstrap_loan",
        lambda *_a, **_k: submitted.append(_a[4]) or "handoff served",
    )

    result = builder._start_bootstrap_loan(
        object(), object(), "nauvis", "player", "electric-mining-drill", 6,
        (0.0, 0.0), messages.append,
    )

    assert result == "handoff served"
    assert submitted[0].target_item == "splitter"
    assert any("LOAN HANDOFF" in message for message in messages)


def test_handoff_emits_yield_decision_without_changing_behavior(monkeypatch) -> None:
    """The handoff names why yield/feeder did not fire; the decision is unchanged."""
    _parallel_loan_world(monkeypatch, (46, 31))
    submitted: list[builder.MallBootstrapLoan] = []
    messages: list[str] = []
    monkeypatch.setattr(
        builder, "_submit_bootstrap_loan",
        lambda *_a, **_k: submitted.append(_a[4]) or "handoff served",
    )

    result = builder._start_bootstrap_loan(
        object(), object(), "nauvis", "player", "electric-mining-drill", 6,
        (0.0, 0.0), messages.append,
    )

    assert result == "handoff served"
    assert submitted[0].target_item == "splitter"
    decision = next(
        message for message in messages if "LOAN YIELD DECISION" in message
    )
    assert "waiter=electric-mining-drill" in decision
    assert "splitter:splitter" in decision
    assert "decision=handoff" in decision


def test_service_routes_to_the_matching_loan(monkeypatch) -> None:
    """With several loans, each batch services its own cell."""
    splitter_loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable", target_item="splitter",
        target_count=3, side="left",
        requester_position=(50.5, 32.5), current_recipe="splitter",
    )
    drill_loan = builder.MallBootstrapLoan(
        original_recipe="iron-gear-wheel", target_item="electric-mining-drill",
        target_count=6, side="left",
        requester_position=(39.5, 32.5), current_recipe="drill",
    )
    monkeypatch.setattr(
        builder, "active_bootstrap_loans",
        lambda *_a: (splitter_loan, drill_loan),
    )
    submitted: list[tuple] = []
    monkeypatch.setattr(
        builder, "_submit_bootstrap_loan",
        lambda *_a, **_k: submitted.append((_a[4], _k.get("preempt_for")))
        or "served",
    )

    assert builder._service_bootstrap_loan(
        object(), object(), "nauvis", "player", "electric-mining-drill",
        (0.0, 0.0), lambda _message: None,
    ) == "served"
    assert submitted == [(drill_loan, None)]
    assert builder._service_bootstrap_loan(
        object(), object(), "nauvis", "player", "unrelated-item",
        (0.0, 0.0), lambda _message: None,
    ) is None


def test_service_keeps_handoff_for_a_single_other_loan(monkeypatch) -> None:
    """One foreign loan is still serviced so the new batch can borrow next."""
    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable", target_item="splitter",
        target_count=3, side="left",
        requester_position=(50.5, 32.5), current_recipe="splitter",
    )
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: (loan,))
    submitted: list[tuple] = []
    monkeypatch.setattr(
        builder, "_submit_bootstrap_loan",
        lambda *_a, **_k: submitted.append((_a[4], _k.get("preempt_for")))
        or "served",
    )

    assert builder._service_bootstrap_loan(
        object(), object(), "nauvis", "player", "electric-mining-drill",
        (0.0, 0.0), lambda _message: None,
    ) == "served"
    assert submitted == [(loan, "electric-mining-drill")]


def _binding_drill_loan() -> builder.MallBootstrapLoan:
    return builder.MallBootstrapLoan(
        original_recipe="iron-gear-wheel", target_item="electric-mining-drill",
        target_count=6, spare_target_count=8, side="left",
        requester_position=(39.5, 38.5),
        current_recipe="electric-mining-drill",
        step_recipe="electric-mining-drill",
        step_baseline_finished=100, step_required_crafts=4,
        step_minimum_crafts=2,
    )


def _mock_binding_serve(monkeypatch, *, transferable: int) -> list[str]:
    messages: list[str] = []
    monkeypatch.setitem(builder.LINE_RECIPES, "electric-mining-drill", {
        "machine": "assembling-machine-2",
        "ingredients": ["electronic-circuit", "iron-gear-wheel", "iron-plate"],
        "amounts": [3, 5, 10], "product_amount": 1, "craft_time": 2.0,
    })
    stock = {
        "electric-mining-drill": transferable, "electronic-circuit": 100,
        "iron-gear-wheel": 100, "iron-plate": 100,
    }
    monkeypatch.setattr(builder, "_BLOCKING_MALL_ITEMS", {"electric-mining-drill"})
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: (dict(stock), dict(stock)),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 100,
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_minimum_fulfilled", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder.live_base, "transferable_items",
        lambda *_a: {"electric-mining-drill": transferable},
    )
    monkeypatch.setattr(
        builder, "_missing_chemical_ladder_predecessor", lambda *_a: None,
    )
    monkeypatch.setattr(builder, "_deliver_cell_ingredients", lambda *_a: False)
    monkeypatch.setattr(builder, "consume_wait", lambda *_a: None)
    monkeypatch.setattr(builder.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(
        builder.live_base, "entity_status_name", lambda *_a: "working",
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda *_a, **_k: messages.append("SUBMIT") or None,
    )
    return messages


def test_binding_loan_short_of_bill_shields_against_stockpile_preempt(
    monkeypatch,
) -> None:
    """2026-09-03: drills at 2/8 with mine ghosts pending yielded their cell
    to circuits-200 stockpiling. A binding loan below its bill keeps the cell
    against a non-binding preemptor."""
    messages = _mock_binding_serve(monkeypatch, transferable=2)
    client = SimpleNamespace(command=lambda *_a: "")

    result = builder._submit_bootstrap_loan(
        client, object(), "nauvis", "player", _binding_drill_loan(),
        messages.append, preempt_for="electronic-circuit",
    )

    assert "producing temporary" in result
    assert not any("PREEMPT" in message for message in messages)
    assert "SUBMIT" not in messages


def test_binding_loan_at_bill_yields_to_preempt(monkeypatch) -> None:
    """Once the blocking bill is spendable, spares yield normally."""
    messages = _mock_binding_serve(monkeypatch, transferable=6)
    monkeypatch.setattr(
        builder, "_restore_bootstrap_loan", lambda *_a, **_k: messages.append("RESTORED"),
    )
    client = SimpleNamespace(command=lambda *_a: "")

    builder._submit_bootstrap_loan(
        client, object(), "nauvis", "player", _binding_drill_loan(),
        messages.append, preempt_for="electronic-circuit",
    )

    assert any("PREEMPT" in message for message in messages)
    assert "RESTORED" in messages
