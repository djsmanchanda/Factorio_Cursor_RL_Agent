# Path: tests/test_construction_stock.py
# Purpose: Prove mall cells prebuild deterministic stack reserves without making the mission wait for the full reserve.

from __future__ import annotations

import inspect
import tempfile
from types import SimpleNamespace
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator.construction_stock import (  # noqa: E402
    BULK_CONSTRUCTION_ITEMS,
    FALLBACK_STACK_SIZE,
    MallReserve,
    mall_reserve,
)
from orchestrator.parts_mall import MaterialShortage, add_demands  # noqa: E402
from orchestrator.priority_list import PriorityItem, PriorityList  # noqa: E402

_STACKS = {
    "transport-belt": 200,
    "inserter": 50,
    "assembling-machine-2": 50,
}


def test_belts_prebuild_four_live_game_stacks() -> None:
    assert mall_reserve(
        "transport-belt", 250, stack_sizes=_STACKS,
    ) == MallReserve(800, 800, 4)


def test_a_job_using_exactly_half_the_reserve_does_not_expand_it() -> None:
    reserve = mall_reserve("transport-belt", 400, stack_sizes=_STACKS)

    assert reserve.storage_stacks == 4
    assert reserve.storage_count == 800


def test_a_job_over_half_the_reserve_expands_in_two_stack_steps() -> None:
    reserve = mall_reserve("transport-belt", 401, stack_sizes=_STACKS)

    assert reserve.storage_stacks == 6
    assert reserve.storage_count == 1200


def test_machine_reserve_starts_at_one_stack_then_grows_for_a_large_job() -> None:
    reserve = mall_reserve("assembling-machine-2", 30, stack_sizes=_STACKS)

    assert reserve.storage_stacks == 2
    assert reserve.storage_count == 100


def test_other_bulk_parts_use_the_same_four_stack_policy() -> None:
    reserve = mall_reserve("inserter", 60, stack_sizes=_STACKS)

    assert "inserter" in BULK_CONSTRUCTION_ITEMS
    assert reserve.storage_stacks == 4
    assert reserve.storage_count == 200


def test_an_unknown_stack_size_uses_the_conservative_fallback() -> None:
    reserve = mall_reserve("mystery-machine", 1, stack_sizes={})

    assert reserve.storage_count == FALLBACK_STACK_SIZE


def test_a_mature_base_removes_the_bar_and_the_stock_gate() -> None:
    assert mall_reserve(
        "transport-belt", 250, stack_sizes=_STACKS, mature=True,
    ) == MallReserve(None, 250, None, fill_chest=True)


def test_a_reserve_requires_a_real_job() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        mall_reserve("transport-belt", 0, stack_sizes=_STACKS)


def test_reserve_capacity_is_monotonic_in_job_size() -> None:
    capacities = [
        mall_reserve("transport-belt", size, stack_sizes=_STACKS).storage_count
        for size in (1, 250, 400, 401, 600, 601, 1200)
    ]

    assert capacities == sorted(capacities)


def test_the_runner_waits_for_the_job_not_the_reserve() -> None:
    ensured = inspect.getsource(builder._ensure_mall_item)
    served = inspect.getsource(builder._serve_mall_task)

    assert "stock_target=production_target" in ensured
    assert "stock_gate_target=reserve.gate_target" in ensured
    assert "storage_limit=reserve.storage_count" in ensured
    assert "fill_provider=reserve.fill_chest" in ensured
    assert "wait_for_stock" in served


def test_producer_backed_job_releases_before_full_stock(monkeypatch) -> None:
    task = type("Task", (), {"item": "pipe", "target": 408})()
    targets = {"pipe": 408}
    completed: list[str] = []
    priorities = type("Priorities", (), {
        "describe": lambda *_args: "pipe task",
        "complete": lambda _self, item, _tick: completed.append(item),
    })()
    monkeypatch.setattr(builder, "_belt_starved_consumer", lambda *_a: None)
    monkeypatch.setattr(
        builder, "_ensure_mall_item",
        lambda *_a, **_k: (True, (10.5, 10.5)),
    )
    monkeypatch.setattr(
        builder, "construction_supply_chain_is_scheduled",
        lambda *_a: True,
    )
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a: SimpleNamespace(working_count=1, produced_count=1),
    )
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 123)
    monkeypatch.setattr(
        builder, "wait_for_stock",
        lambda *_a, **_k: pytest.fail("producer-backed task waited for full stock"),
    )

    builder._serve_mall_task(
        object(), object(), "nauvis", "player", task, 100, targets,
        priorities, (0.0, 0.0), lambda _message: None,
    )

    assert targets == {}
    assert completed == ["pipe"]


def test_rationed_mall_ready_completes_task_when_output_is_none(monkeypatch) -> None:
    """Rationed pre-core batches that reached their target complete immediately."""
    task = type("Task", (), {"item": "transport-belt", "target": 148})()
    targets = {"transport-belt": 148}
    completed: list[str] = []
    priorities = type("Priorities", (), {
        "describe": lambda *_args: "transport-belt task",
        "complete": lambda _self, item, _tick: completed.append(item),
    })()
    monkeypatch.setattr(builder, "_belt_starved_consumer", lambda *_a: None)
    monkeypatch.setattr(
        builder, "_ensure_mall_item",
        lambda *_a, **_k: (True, None),
    )
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 456)

    builder._serve_mall_task(
        object(), object(), "nauvis", "player", task, 100, targets,
        priorities, (0.0, 0.0), lambda _message: None,
    )

    assert targets == {}
    assert completed == ["transport-belt"]



def test_background_reserve_starts_a_producer_without_waiting(monkeypatch) -> None:
    background = {"transport-belt": 200}
    blocking: dict[str, int] = {}
    calls: list[tuple[str, int, bool]] = []
    monkeypatch.setattr(
        builder, "_ensure_mall_item",
        lambda *_args, **kwargs: (
            calls.append((_args[4], _args[5], kwargs["background"])) or (True, None)
        ),
    )
    monkeypatch.setattr(
        builder, "wait_for_stock",
        lambda *_args, **_kwargs: pytest.fail("background reserve waited for stock"),
    )

    spent = builder._serve_background_mall_task(
        object(), object(), "nauvis", "player", background, blocking,
        (0.0, 0.0), lambda _message: None,
    )

    assert spent is True
    assert calls == [("transport-belt", 200, True)]
    assert background == {}


def test_background_reserve_is_part_of_the_progress_signature() -> None:
    before = builder._pass_signature(
        None, {}, set(), {"transport-belt": 200, "inserter": 20},
    )
    after = builder._pass_signature(None, {}, set(), {"inserter": 20})

    assert before != after


def test_the_reserve_uses_live_stack_sizes() -> None:
    source = inspect.getsource(builder.mall_reserve_for)

    assert "ITEM_STACK_SIZES" in source


def test_maturity_requires_a_live_assembler_three_producer() -> None:
    source = inspect.getsource(builder.mall_reserve_for)

    assert '"assembling-machine-3"' in source
    assert "_has_producer" in source


def test_live_reserve_passes_the_job_size_and_stack_catalog(monkeypatch) -> None:
    monkeypatch.setattr(
        builder, "ITEM_STACK_SIZES", {"transport-belt": 200},
    )
    monkeypatch.setattr(builder, "_has_producer", lambda *_args: False)

    reserve = builder.mall_reserve_for(
        object(), "nauvis", "player", "transport-belt", 250,
    )

    assert reserve == MallReserve(800, 800, 4)


def test_live_reserve_lifts_after_an_assembler_three_producer(monkeypatch) -> None:
    monkeypatch.setattr(builder, "_has_producer", lambda *_args: True)
    assert builder.mall_reserve_for(
        object(), "nauvis", "player", "transport-belt", 250,
    ).fill_chest is True


def test_starter_migration_caps_electronics_and_belt_components(monkeypatch) -> None:
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: False,
    )

    expected = {
        "electronic-circuit": 5,
        "splitter": 2,
        "underground-belt": 5,
    }
    for item, cap in expected.items():
        assert builder.mall_reserve_for(
            object(), "nauvis", "player", item, 200,
        ) == MallReserve(cap, cap, None)


def test_blocking_project_bill_overrides_the_starter_idle_cap(monkeypatch) -> None:
    """Caps limit idle stock, never the exact bill of scheduled construction."""
    captured: dict[str, object] = {}
    messages: list[str] = []
    monkeypatch.setattr(
        builder, "mall_reserve_for", lambda *_args: MallReserve(5, 5, None),
    )
    monkeypatch.setattr(
        builder, "ensure_produced",
        lambda *_args, **kwargs: captured.update(kwargs),
    )

    ready, output = builder._ensure_mall_item(
        object(), object(), "nauvis", "player", "electronic-circuit", 200,
        {}, (0.0, 0.0), messages.append, background=False,
    )

    assert (ready, output) == (True, None)
    assert captured["stock_target"] == 200
    assert captured["storage_limit"] == 200
    assert any("SCHEDULED BILL OVERRIDE" in message for message in messages)


def test_pre_core_mall_cell_keeps_only_need_plus_margin(monkeypatch) -> None:
    """A temporary construction cell must not reserve a whole machine stack."""
    monkeypatch.setitem(builder.LINE_RECIPES, "assembling-machine-1", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [9], "craft_time": 0.5, "product_amount": 1,
        "set_recipe": True,
    })
    monkeypatch.setattr(builder, "_rationed_mall_batch", lambda *_a, **_k: False)
    monkeypatch.setattr(
        builder, "_is_pre_core_temporary_mall_item", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {},
    )
    monkeypatch.setattr(
        builder, "mall_reserve_for", lambda *_a: MallReserve(50, 50, 1),
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_spare_target", lambda *_a: 3,
    )
    monkeypatch.setattr(builder, "_bootstrap_reserve_machine_target", lambda *_a, **_k: 1)
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", None)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        builder, "ensure_produced",
        lambda *_a, **kwargs: captured.update(kwargs),
    )

    ready, output = builder._ensure_mall_item(
        object(), object(), "nauvis", "player", "assembling-machine-1", 1,
        {}, (0.0, 0.0), lambda _message: None, background=False,
    )

    assert (ready, output) == (True, None)
    assert captured["stock_target"] == 3
    assert captured["storage_limit"] == 3
    assert captured["stock_gate_target"] == 3
    assert captured["fill_provider"] is False


def test_baseline_prep_cannot_expand_the_circuit_provider_past_its_cap(monkeypatch) -> None:
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: False,
    )
    monkeypatch.setattr(builder.live_base, "logistic_request_total", lambda *_args: 200)
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_args: None)
    monkeypatch.setattr(builder, "live_intermediate_demand", lambda *_args: 0.0)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_args: {})
    monkeypatch.setattr(builder, "promoted_line_machine_count", lambda *_args, **_kwargs: None)

    plan = builder._plan_line(
        object(), "nauvis", "player", "electronic-circuit", lambda _message: None,
        upgrade_bootstrap=False, stock_target=1, minimum_machines=1,
        allow_promotion=False,
    )

    assert plan.mall_storage_limit == 5


def test_starter_circuit_cap_is_applied_to_new_and_existing_assembler_gates(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: False,
    )
    observed: dict[str, int | None] = {}
    line_plan = SimpleNamespace(existing=None, at_size=True, promote_to_line=False)
    monkeypatch.setattr(builder, "_plan_line", lambda *_args, **_kwargs: line_plan)
    monkeypatch.setattr(
        builder, "_refresh_mall_cell",
        lambda *_args, **kwargs: observed.setdefault(
            "existing", kwargs["stock_gate_target"],
        ),
    )
    monkeypatch.setattr(
        builder, "_build_assembled_stage",
        lambda *_args, **kwargs: observed.setdefault(
            "new", kwargs["stock_gate_target"],
        ),
    )

    builder.ensure_produced(
        object(), object(), "nauvis", "player", "electronic-circuit",
        (0.0, 0.0), lambda _message: None,
        upgrade_bootstrap=False, stock_target=1, allow_promotion=False,
    )

    assert observed == {"existing": 5, "new": 5}


def test_baseline_ready_cell_runs_the_central_gate_refresh(monkeypatch) -> None:
    line = SimpleNamespace(machine_count=1)
    observed: dict[str, object] = {}
    monkeypatch.setattr(builder, "baseline_build_order", lambda: ["electronic-circuit"])
    monkeypatch.setattr(builder, "_baseline_recipe_ready", lambda *_args: True)
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_args: line)
    monkeypatch.setattr(
        builder, "ensure_produced",
        lambda *_args, **kwargs: observed.update(kwargs),
    )

    spent = builder._prep_intermediate(
        object(), object(), "nauvis", "player", set(), {}, (0.0, 0.0),
        lambda _message: None,
    )

    assert spent is True
    assert observed["stock_target"] == 1
    assert observed["upgrade_bootstrap"] is False


def test_starter_migration_reduces_belt_component_requesters(monkeypatch) -> None:
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: False,
    )
    monkeypatch.setattr(
        builder, "_is_pre_core_temporary_mall_item", lambda *_args: False,
    )
    spec = {"machine": "assembling-machine-2", "craft_time": 1.0}

    assert builder._mall_request_multiplier(
        object(), "nauvis", "player", "splitter", spec, 50,
    ) == 2
    assert builder._mall_request_multiplier(
        object(), "nauvis", "player", "electronic-circuit", spec, 200,
    ) is None
    assert builder._mall_request_multiplier(
        object(), "nauvis", "player", "transport-belt", spec, 200,
    ) == 30
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: True,
    )
    assert builder._mall_request_multiplier(
        object(), "nauvis", "player", "splitter", spec, 50,
    ) == 12
    assert builder._mall_request_multiplier(
        object(), "nauvis", "player", "transport-belt", spec, 200,
    ) == 30


def test_pre_core_finite_requester_cannot_hoard_construction_inputs(
    monkeypatch,
) -> None:
    """The failed AM1 run needed one fast inserter plus two spares, but its
    throughput buffer pulled all 12 regular inserters into the requester and
    stranded the refinery's final 10 ghosts."""
    monkeypatch.setattr(
        builder, "_is_pre_core_temporary_mall_item", lambda *_args: True,
    )
    spec = {
        "machine": "assembling-machine-1", "craft_time": 0.5,
        "product_amount": 1,
    }

    assert builder._mall_request_multiplier(
        object(), "nauvis", "player", "fast-inserter", spec, 3,
        finite_batch=True,
    ) == 3


def test_automation_science_repairs_an_unhealthy_metal_transition(monkeypatch) -> None:
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: False,
    )
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_args: None)
    monkeypatch.setattr(
        builder, "_metal_science_transition_status",
        lambda *_args: (
            False,
            {
                "mode": "transition_health",
                "districts": {
                    "iron-plate": {
                        "healthy": False,
                        "remedy": "repair_power_or_transport",
                    },
                },
            },
        ),
    )
    repaired: list[str] = []
    monkeypatch.setattr(
        builder, "build_mining_stage",
        lambda _c, _b, _s, _f, recipe, *_a, **_k: repaired.append(recipe),
    )

    with pytest.raises(
        builder.ProductionPrerequisiteDeferred,
        match="applied repair_power_or_transport for iron-plate",
    ):
        builder.ensure_produced(
            object(), object(), "nauvis", "player", "automation-science-pack",
            (0.0, 0.0), lambda _message: None,
        )
    assert repaired == ["iron-plate"]


def test_automation_science_allows_measured_transition_before_retirement(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        builder, "_metal_science_transition_status",
        lambda *_args: (
            True,
            {
                "mode": "transition_health",
                "districts": {
                    "iron-plate": {"healthy": True},
                    "copper-plate": {"healthy": True},
                },
            },
        ),
    )
    monkeypatch.setattr(
        builder, "build_mining_stage",
        lambda *_a, **_k: pytest.fail("healthy transition needs no remedy"),
    )
    messages: list[str] = []

    builder._ensure_automation_science_transition(
        object(), object(), "nauvis", "player", (0.0, 0.0), messages.append,
    )

    assert any("transition_health" in message for message in messages)


def test_transition_reserves_use_one_stack_after_starter_migration(monkeypatch) -> None:
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: True,
    )
    monkeypatch.setattr(
        builder, "ITEM_STACK_SIZES", {
            "electronic-circuit": 200, "splitter": 50,
            "underground-belt": 50,
        },
    )
    monkeypatch.setattr(builder, "_has_producer", lambda *_args: False)

    for item in ("electronic-circuit", "splitter", "underground-belt"):
        assert builder.mall_reserve_for(
            object(), "nauvis", "player", item, 200,
        ) == MallReserve(
            builder.ITEM_STACK_SIZES[item], builder.ITEM_STACK_SIZES[item], 1,
        )


def test_rotating_splitter_batch_targets_one_stack_after_metal_transition(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: True,
    )
    monkeypatch.setattr(
        builder, "ITEM_STACK_SIZES", {"splitter": 50},
    )
    monkeypatch.setattr(builder, "_has_producer", lambda *_args: False)

    assert builder._rationed_mall_spare_target(
        object(), "nauvis", "player", "splitter", 3,
    ) == 50


def test_post_metal_reserve_queues_circuits_then_splitters(monkeypatch) -> None:
    """Splitter reserve is a 12-unit buffer, not a full stack.

    2026-09-04 (22:33 run): the 50-splitter reserve held the rotating
    assembler +1538s to +1950s before stone, while measured refinery demand
    is 3 per plate project.
    """
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: True,
    )
    monkeypatch.setattr(
        builder, "ITEM_STACK_SIZES", {"electronic-circuit": 200, "splitter": 50},
    )
    stock = {"electronic-circuit": 199, "splitter": 0}
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_args: dict(stock),
    )
    prepped: set[str] = set()
    targets: dict[str, int] = {}
    messages: list[str] = []

    assert builder._prep_post_metal_stack_reserves(
        object(), "nauvis", "player", prepped, targets, messages.append,
    )
    assert targets == {"electronic-circuit": 200}

    stock["electronic-circuit"] = 200
    targets.clear()
    assert builder._prep_post_metal_stack_reserves(
        object(), "nauvis", "player", prepped, targets, messages.append,
    )
    assert targets == {"splitter": 12}

    stock["splitter"] = 12
    targets.clear()
    assert not builder._prep_post_metal_stack_reserves(
        object(), "nauvis", "player", prepped, targets, messages.append,
    )
    assert prepped == {
        "_post_metal_stack:electronic-circuit",
        "_post_metal_stack:splitter",
    }
    assert any("before stone" in message for message in messages)


def test_rotating_machine_batch_keeps_two_bounded_spares(monkeypatch) -> None:
    monkeypatch.setattr(
        builder, "mall_reserve_for",
        lambda *_args: MallReserve(50, 50, 1),
    )
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: False,
    )

    assert builder._rationed_mall_spare_target(
        object(), "nauvis", "player", "oil-refinery", 1,
    ) == 3


# --- the persisted-target trap ---------------------------------------------

def test_a_persisted_target_never_outlives_the_mission_that_set_it() -> None:
    """max() meant a target could only rise, and it is saved to disk -- so one
    run that raised transport-belt to 4800 left every LATER run waiting for
    4800, with nothing in the log saying where the number came from."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "priorities.json"
        stale = PriorityList(path, 0)
        stale.items["transport-belt"] = PriorityItem(
            item="transport-belt", target=4800, base_rating=100, created_tick=0,
        )
        stale._save()

        fresh = PriorityList(path, 0)
        fresh.sync({"transport-belt": 200}, {"transport-belt": 0}, 10)

        assert fresh.items["transport-belt"].target == 200


def test_a_shortage_can_still_raise_a_target_within_a_run() -> None:
    with tempfile.TemporaryDirectory() as directory:
        priorities = PriorityList(Path(directory) / "p.json", 0)
        targets = {"electric-mining-drill": 6}
        priorities.sync(targets, {}, 10)
        add_demands(targets, MaterialShortage("mine", {"electric-mining-drill": 14}, {}))
        priorities.sync(targets, {}, 20)

        assert priorities.items["electric-mining-drill"].target == 14


def test_goal_shortage_preempts_background_reserve(monkeypatch) -> None:
    """Scarce starter inputs must reach the mission before reserve cells."""
    calls: list[str] = []
    monkeypatch.setattr(
        builder, "_advance_the_goal",
        lambda *_args: calls.append("goal") or builder._SHORTAGE,
    )
    monkeypatch.setattr(
        builder, "_serve_background_mall_task",
        lambda *_args: pytest.fail("background reserve ran before the goal"),
    )

    result = builder._serve_ready_pass(
        object(), object(), "nauvis", "player", None, 0, {},
        {"transport-belt": 200}, object(), (0.0, 0.0),
        "automation-science-pack", lambda _message: None,
    )

    assert result is builder._SHORTAGE
    assert calls == ["goal"]
