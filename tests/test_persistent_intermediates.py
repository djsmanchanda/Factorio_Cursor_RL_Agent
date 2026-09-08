# Path: tests/test_persistent_intermediates.py
# Purpose: Prove starter reserves bootstrap persistent intermediate producers.

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]

from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator.material_reservations import (  # noqa: E402
    MaterialReservationLedger, plan_material_bill,
)
from planners.infrastructure import strip_local_power  # noqa: E402
from planners.local_layout_planner import LocalLayoutPlanner  # noqa: E402


@pytest.fixture(autouse=True)
def clear_managed_sources():
    builder.MANAGED_INTERMEDIATE_SOURCES.clear()
    yield
    builder.MANAGED_INTERMEDIATE_SOURCES.clear()


def _plan(item: str, ingredient: str) -> SimpleNamespace:
    return SimpleNamespace(
        spec={
            "ingredients": [ingredient], "amounts": [1],
            "product_amount": 1, "craft_time": 0.5,
        },
        promote_to_line=False,
        production_target=1,
    )



def test_compact_mall_seeds_from_one_craft_not_full_stock_target(monkeypatch):
    """A belt cell must be placeable before its whole reserve is available."""
    spec = {
        "ingredients": ["iron-gear-wheel", "iron-plate"],
        "amounts": [1, 1], "product_amount": 2, "craft_time": 0.5,
    }
    plan = SimpleNamespace(spec=spec, promote_to_line=False, production_target=116)
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_args: {"iron-gear-wheel": 1, "iron-plate": 1},
    )
    monkeypatch.setattr(builder, "_has_producer", lambda *_args: False)

    result = builder._ingredient_sources(
        object(), object(), "nauvis", "player", "transport-belt",
        (0.0, 0.0), lambda _message: None, plan, upgrade_bootstrap=False,
    )

    assert result == {}

def test_stocked_iron_stick_schedules_a_real_producer(monkeypatch):
    calls = []
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_args: {"iron-stick": 20},
    )
    monkeypatch.setattr(builder, "_has_producer", lambda *_args: False)
    monkeypatch.setattr(
        builder, "ensure_produced",
        lambda *args, **kwargs: calls.append((args, kwargs)) or None,
    )

    result = builder._ingredient_sources(
        object(), object(), "nauvis", "player", "medium-electric-pole",
        (0.0, 0.0), lambda _message: None, _plan("medium-electric-pole", "iron-stick"),
        upgrade_bootstrap=False,
    )

    assert result is None
    assert len(calls) == 1
    assert calls[0][0][4] == "iron-stick"
    assert calls[0][1]["upgrade_bootstrap"] is False


def test_stocked_inserter_schedules_a_real_producer(monkeypatch):
    """When mall cells draw regular inserters from stock without a live producer,
    schedule a real producer before the initial inventory runs out."""
    calls = []
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_args: {"inserter": 20},
    )
    monkeypatch.setattr(builder, "_has_producer", lambda *_args: False)
    monkeypatch.setattr(
        builder, "ensure_produced",
        lambda *args, **kwargs: calls.append((args, kwargs)) or None,
    )

    result = builder._ingredient_sources(
        object(), object(), "nauvis", "player", "fast-inserter",
        (0.0, 0.0), lambda _message: None, _plan("fast-inserter", "inserter"),
        upgrade_bootstrap=False,
    )

    assert result is None
    assert len(calls) == 1
    assert calls[0][0][4] == "inserter"
    assert calls[0][1]["upgrade_bootstrap"] is False


def test_steel_line_reuses_the_real_iron_provider_not_starter_storage(monkeypatch):
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_args: {"iron-plate": 100},
    )
    monkeypatch.setattr(builder, "_has_producer", lambda *_args: True)
    calls = []
    monkeypatch.setattr(
        builder, "ensure_produced",
        lambda *args, **kwargs: calls.append((args, kwargs)) or (12.5, 43.5),
    )

    result = builder._ingredient_sources(
        object(), object(), "nauvis", "player", "steel-plate",
        (0.0, 0.0), lambda _message: None, _plan("steel-plate", "iron-plate"),
        upgrade_bootstrap=False,
    )

    assert result == {"iron-plate": (12.5, 43.5)}
    assert calls[0][0][4] == "iron-plate"
    assert calls[0][1]["upgrade_bootstrap"] is True


def test_steel_stage_records_its_output_as_a_persistent_source(monkeypatch):
    monkeypatch.setattr(
        builder, "_iron_capacity_for_fast_belts", lambda *_args: (12, 12),
    )
    monkeypatch.setattr(
        builder, "_ingredient_sources", lambda *_args, **_kwargs: {"iron-plate": (1.5, 2.5)},
    )
    calls = []
    monkeypatch.setattr(
        builder, "build_conversion_stage",
        lambda *args, **kwargs: calls.append((args, kwargs)) or (9.5, 8.5),
    )
    plan = SimpleNamespace(
        existing=None, spec={"machine": "electric-furnace"},
        promote_to_line=False, promoted_count=None, mall_storage_limit=1,
    )

    builder._build_assembled_stage(
        object(), object(), "nauvis", "player", "steel-plate", (0.0, 0.0),
        lambda _message: None, plan, None, upgrade_bootstrap=False,
    )

    assert builder.MANAGED_INTERMEDIATE_SOURCES == {"steel-plate": (9.5, 8.5)}
    assert calls[0][0][6] == (1.5, 2.5)
    assert calls[0][1]["machine_count"] == 1
    assert calls[0][1]["allow_logistic_inputs"] is False


def test_existing_single_steel_furnace_completes_the_starter(monkeypatch):
    monkeypatch.setattr(
        builder, "_ingredient_sources", lambda *_args, **_kwargs: {"iron-plate": (4.5, 5.5)},
    )
    monkeypatch.setattr(
        builder, "_iron_capacity_for_fast_belts", lambda *_args: (12, 12),
    )
    calls = []
    monkeypatch.setattr(
        builder, "build_conversion_stage",
        lambda *args, **kwargs: calls.append((args, kwargs)) or (9.5, 8.5),
    )
    plan = SimpleNamespace(
        existing=SimpleNamespace(machine_count=1),
        spec={"machine": "electric-furnace"}, promote_to_line=False,
        promoted_count=None, mall_storage_limit=1,
    )

    builder._build_assembled_stage(
        object(), object(), "nauvis", "player", "steel-plate", (0.0, 0.0),
        lambda _message: None, plan, None, upgrade_bootstrap=False,
    )

    assert calls == []


def test_one_furnace_steel_starter_uses_the_opening_iron_line(monkeypatch):
    monkeypatch.setattr(
        builder, "_ingredient_sources", lambda *_args, **_kwargs: {"iron-plate": (4.5, 5.5)},
    )
    monkeypatch.setattr(
        builder, "_iron_capacity_for_fast_belts", lambda *_args: (6, 12),
    )
    expansions = []
    monkeypatch.setattr(
        builder, "build_mining_stage",
        lambda *args, **kwargs: expansions.append((args, kwargs)),
    )
    builds = []
    monkeypatch.setattr(
        builder, "build_conversion_stage",
        lambda *_args, **kwargs: builds.append(kwargs) or (9.5, 8.5),
    )
    plan = SimpleNamespace(
        existing=None, spec={"machine": "electric-furnace"},
        promote_to_line=False, promoted_count=None, mall_storage_limit=1,
    )

    builder._build_assembled_stage(
        object(), object(), "nauvis", "player", "steel-plate", (0.0, 0.0),
        lambda _message: None, plan, None, upgrade_bootstrap=False,
    )

    assert expansions == []
    assert builds[0]["machine_count"] == 1


def test_steel_starter_power_uses_only_presteel_poles() -> None:
    """Its construction bill must not depend on the steel it starts."""
    plan = LocalLayoutPlanner().generate_line_layout(
        "steel-plate", 1, 0, 0,
        belt_type="transport-belt", inserter_type="inserter",
        feed_style="chest", terminal_collector=True,
    )
    plan = strip_local_power(plan, remove_substations=True)
    builder._side_sample_plate_output(
        plan, (0, 0), 1, "transport-belt", tap_inserter_type="inserter",
    )

    plan, anchor = builder._use_presteel_starter_power(plan)
    bill = plan_material_bill(plan)

    assert anchor == "small-electric-pole"
    assert bill.get("small-electric-pole") == 2
    assert "medium-electric-pole" not in bill
    assert "substation" not in bill


def test_steel_starter_uses_fully_stocked_medium_poles_without_wood() -> None:
    """Paid-for medium anchors avoid an impossible wood-gated pole batch."""
    plan = LocalLayoutPlanner().generate_line_layout(
        "steel-plate", 1, 0, 0,
        belt_type="transport-belt", inserter_type="inserter",
        feed_style="chest", terminal_collector=True,
    )
    plan = strip_local_power(plan, remove_substations=True)
    builder._side_sample_plate_output(
        plan, (0, 0), 1, "transport-belt", tap_inserter_type="inserter",
    )

    plan, anchor = builder._use_presteel_starter_power(
        plan, {"medium-electric-pole": 2, "wood": 0},
    )
    bill = plan_material_bill(plan)

    assert anchor == "medium-electric-pole"
    assert bill.get("medium-electric-pole") == 2
    assert {
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == "medium-electric-pole"
    } == {(0.5, 1.5), (3.5, 7.5)}
    assert "small-electric-pole" not in bill
    assert "substation" not in bill


def test_steel_starter_submits_as_critical_material_prerequisite(monkeypatch) -> None:
    """Downstream construction cannot reclaim the starter's cyclic pole stock."""
    submitted: list[dict] = []
    monkeypatch.setattr(builder, "_conversion_origin", lambda *_a, **_k: (0, 0))
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock",
        lambda *_a: {"medium-electric-pole": 2},
    )
    monkeypatch.setattr(
        builder, "_conversion_feed_plan",
        lambda *_a, **_k: ({}, {}, {}, False),
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda *_a, **kwargs: submitted.append(kwargs) or {},
    )
    monkeypatch.setattr(builder, "_power_and_raise_stage", lambda *_a, **_k: None)
    monkeypatch.setattr(builder, "_connect_stage_feeds", lambda *_a, **_k: None)

    builder.build_conversion_stage(
        object(), object(), "nauvis", "player", "steel-plate",
        {"iron-plate": (10.5, 10.5)}, (0.0, 0.0), lambda _m: None,
        machine_count=builder.STEEL_BASELINE_FURNACES,
        inserter_type="inserter",
    )

    assert submitted[0]["reservation_priority"] == 100


def test_conversion_waits_for_local_poles_before_repairing_support(monkeypatch) -> None:
    plan = {"phases": [{"actions": [
        {"entity": "inserter", "position": {"x": 112.5, "y": 50.5}},
        {"entity": "medium-electric-pole", "position": {"x": 111.5, "y": 50.5}},
    ]}]}
    stage_ready = False
    repairs: list[tuple[float, float]] = []

    def raise_stage(*_args, **_kwargs):
        nonlocal stage_ready
        stage_ready = True

    monkeypatch.setattr(builder, "bring_stage_up", raise_stage)
    monkeypatch.setattr(
        builder.live_base, "entity_status_name",
        lambda *_a: "working" if stage_ready else "no_power",
    )
    monkeypatch.setattr(
        builder, "extend_power",
        lambda *_a, **_k: repairs.append(_a[4]) or True,
    )

    builder._power_and_raise_stage(
        object(), object(), "nauvis", "player", "steel-plate", plan,
        [(109.5, 46.5)], (108.5, 44.5), {"iron-plate": (90.5, 30.5)},
        {"iron-plate": "belt"}, 1, 108.0, 43.0, lambda _message: None,
    )

    assert stage_ready
    assert repairs == []


def test_conversion_repairs_support_still_unpowered_after_stage(monkeypatch) -> None:
    plan = {"phases": [{"actions": [{
        "entity": "inserter", "position": {"x": 112.5, "y": 50.5},
    }]}]}
    events: list[object] = []
    monkeypatch.setattr(
        builder, "bring_stage_up", lambda *_a, **_k: events.append("stage"),
    )
    monkeypatch.setattr(
        builder.live_base, "entity_status_name", lambda *_a: "no_power",
    )
    monkeypatch.setattr(
        builder, "extend_power",
        lambda *_a, **_k: events.append(_a[4]) or True,
    )

    builder._power_and_raise_stage(
        object(), object(), "nauvis", "player", "steel-plate", plan,
        [(109.5, 46.5)], (108.5, 44.5), {"iron-plate": (90.5, 30.5)},
        {"iron-plate": "belt"}, 1, 108.0, 43.0, lambda _message: None,
    )

    assert events == ["stage", (112.5, 50.5)]


def test_steel_pole_seed_is_reserved_before_concurrent_foundation_spend(
    tmp_path: Path, monkeypatch,
) -> None:
    """The mission fence exists before a later consumer drains pole stock."""
    ledger = MaterialReservationLedger(
        tmp_path / "script-output" / "factorio_cursor_rl",
        episode_id="episode-steel-seed", surface="nauvis", force="player",
    )
    stock = {"medium-electric-pole": 5}
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setattr(builder, "_production_started", lambda *_a: False)
    monkeypatch.setitem(builder.LINE_RECIPES, "assembling-machine-2", {
        "ingredients": ["steel-plate", "iron-gear-wheel"],
    })
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock", lambda *_a: stock,
    )

    builder._reserve_steel_starter_power_seed(
        object(), "nauvis", "player", {"assembling-machine-2": 6},
        lambda _m: None,
    )
    competing = ledger.declare(
        "copper_foundation_power", {"medium-electric-pole": 3}, stock,
    )

    assert ledger.projects["conversion_steel-plate"].reserved == {
        "medium-electric-pole": 2,
    }
    assert competing.reserved == {"medium-electric-pole": 3}
    assert ledger.shortage_targets(
        "conversion_steel-plate", {"medium-electric-pole": 2},
    ) == {}


def test_steel_pole_seed_is_not_reserved_without_mission_steel_dependency(
    tmp_path: Path, monkeypatch,
) -> None:
    ledger = MaterialReservationLedger(
        tmp_path / "script-output" / "factorio_cursor_rl",
        episode_id="episode-no-steel", surface="nauvis", force="player",
    )
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", ledger)

    builder._reserve_steel_starter_power_seed(
        object(), "nauvis", "player", {"transport-belt": 200},
        lambda _m: None,
    )

    assert "conversion_steel-plate" not in ledger.projects


def test_steel_starter_defers_a_loan_blocked_on_steel(monkeypatch) -> None:
    """Pipe capability ordering cannot trap the AM2 loan ahead of steel."""
    from orchestrator.mall_bootstrap import MallBootstrapLoan

    loan = MallBootstrapLoan(
        original_recipe="iron-gear-wheel", target_item="assembling-machine-2",
        target_count=2, side="left", requester_position=(39.5, 32.5),
        current_recipe="assembling-machine-2", step_recipe="assembling-machine-2",
    )
    restored: list[str] = []
    messages: list[str] = []
    monkeypatch.setattr(
        builder, "_missing_chemical_ladder_predecessor", lambda *_a: "pipe",
    )
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: (loan,))
    monkeypatch.setattr(
        builder, "_loan_blocked_inputs", lambda *_a: ["steel-plate"],
    )
    monkeypatch.setattr(
        builder, "_restore_bootstrap_loan",
        lambda *_a, **_k: restored.append(_a[4].target_item),
    )
    monkeypatch.setattr(
        builder, "_start_bootstrap_loan",
        lambda *_a, **_k: pytest.fail("steel must not wait for a pipe loan"),
    )

    builder._ensure_chemical_ladder_predecessor(
        object(), object(), "nauvis", "player", "steel-plate", (3.0, -1.0),
        messages.append,
    )

    assert restored == ["assembling-machine-2"]
    assert any(message.startswith("  STEEL STARTER PRIORITY:") for message in messages)


def test_steel_feed_is_continuous_belt_even_for_partial_upgrade(monkeypatch):
    monkeypatch.setattr(
        builder, "_transport_mode", lambda *_args: "logistic",
    )
    monkeypatch.setattr(
        builder, "_direct_single_belt_feed",
        lambda *_args: (8.5, 9.5),
    )
    monkeypatch.setattr(
        builder, "_swap_infinity_chests",
        lambda *_args: pytest.fail("steel fell back to a requester feed"),
    )

    modes, feeds, _preflighted, direct = builder._conversion_feed_plan(
        object(), object(), "nauvis", "player", "steel-plate", {},
        {"iron-plate": (1.5, 2.5)}, 4, "transport-belt", "east",
        lambda _message: None, allow_logistic_inputs=True,
        max_belt_route_tiles=None,
    )

    assert modes == {"iron-plate": "belt"}
    assert feeds == {"iron-plate": (8.5, 9.5)}
    assert direct is True
