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
    monkeypatch.setattr(builder, "_rationed_mall_batch", lambda *_a, **_k: False)
    monkeypatch.setattr(
        builder, "_is_pre_core_temporary_mall_item", lambda *_a: False,
    )
    monkeypatch.setattr(
        builder, "_bootstrap_reserve_machine_target", lambda *_a, **_k: 1,
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
    monkeypatch.setattr(builder, "_rationed_mall_batch", lambda *_a, **_k: False)
    monkeypatch.setattr(
        builder, "_is_pre_core_temporary_mall_item", lambda *_a: False,
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
    """Mall-first opening: the first pending baseline cell is attempted even
    when nothing produces its ingredients yet -- no readiness gate."""
    line = SimpleNamespace(machine_count=1)
    observed: dict[str, object] = {}
    attempts: list[str] = []
    monkeypatch.setattr(builder, "baseline_build_order", lambda: ["electronic-circuit"])
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_args: line)
    def _record(*args: object, **kwargs: object) -> None:
        attempts.append(str(args[4]))
        observed.update(kwargs)
    monkeypatch.setattr(builder, "ensure_produced", _record)

    spent = builder._prep_intermediate(
        object(), object(), "nauvis", "player", set(), {}, (0.0, 0.0),
        lambda _message: None,
    )

    assert spent is True
    assert attempts == ["electronic-circuit"]
    assert observed["stock_target"] == 1
    assert observed["upgrade_bootstrap"] is False


def test_prep_attempts_baseline_without_live_ingredients(monkeypatch) -> None:
    """The readiness gate is gone: with no line and no production anywhere,
    prep still attempts the first baseline recipe so its shortages can route
    to the mall and the plate foundations."""
    attempts: list[str] = []
    monkeypatch.setattr(
        builder, "baseline_build_order",
        lambda: ["iron-gear-wheel", "copper-cable"],
    )
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_args: None)
    monkeypatch.setattr(
        builder, "ensure_produced",
        lambda *args, **_kwargs: attempts.append(str(args[4])),
    )

    spent = builder._prep_intermediate(
        object(), object(), "nauvis", "player", set(), {}, (0.0, 0.0),
        lambda _message: None,
    )

    assert spent is True
    assert attempts == ["iron-gear-wheel"]


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
    ) == 4
    # Bounded headroom, not hoarding: 3 crafts ask for ceil(3 * 1.2) so the
    # machine never idles on a dry requester, still far below the 12 that
    # stranded the refinery (2026-09-03).


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


def test_post_starter_demands_round_up_to_complete_stacks(monkeypatch) -> None:
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: True,
    )
    monkeypatch.setattr(
        builder, "ITEM_STACK_SIZES", {
            "fast-inserter": 50,
            "electronic-circuit": 200,
            "assembling-machine-1": 50,
            "transport-belt": 100,
            "splitter": 50,
        },
    )
    monkeypatch.setattr(builder, "_has_producer", lambda *_args: False)

    expected = {
        ("fast-inserter", 1): 50,
        ("electronic-circuit", 5): 200,
        ("assembling-machine-1", 4): 50,
        ("transport-belt", 128): 200,
        ("splitter", 51): 100,
    }
    for (item, target), rounded in expected.items():
        assert builder.mall_reserve_for(
            object(), "nauvis", "player", item, target,
        ) == MallReserve(
            rounded,
            rounded,
            rounded // builder.ITEM_STACK_SIZES[item],
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


def test_every_rotating_batch_uses_full_stacks_after_metal_transition(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: True,
    )
    monkeypatch.setattr(
        builder, "ITEM_STACK_SIZES", {"fast-inserter": 50},
    )

    assert builder._rationed_mall_spare_target(
        object(), "nauvis", "player", "fast-inserter", 1,
    ) == 50


def test_post_starter_stack_is_required_before_recipe_switch(monkeypatch) -> None:
    """A one-item fast-inserter demand becomes a blocking 50-item batch."""
    monkeypatch.setitem(
        builder.LINE_RECIPES, "fast-inserter",
        {
            "ingredients": ["inserter"], "amounts": [1],
            "machine": "assembling-machine-1", "set_recipe": True,
        },
    )
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: True,
    )
    monkeypatch.setattr(
        builder, "ITEM_STACK_SIZES", {"fast-inserter": 50},
    )
    observed: list[int] = []
    monkeypatch.setattr(
        builder, "_rationed_mall_batch",
        lambda _client, _bridge, _surface, _force, _item, target, *_a,
        **_k: observed.append(target) or True,
    )
    messages: list[str] = []

    ready, output = builder._ensure_mall_item(
        object(), object(), "nauvis", "player", "fast-inserter", 1, {},
        (0.0, 0.0), messages.append, background=False,
    )

    assert (ready, output) == (False, None)
    assert observed == [50]
    assert any("finish the complete stack" in message for message in messages)


def test_post_starter_expensive_machine_keeps_deployment_sized_batch(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: True,
    )
    monkeypatch.setattr(
        builder, "ITEM_STACK_SIZES", {"assembling-machine-2": 50},
    )
    monkeypatch.setattr(builder, "_has_producer", lambda *_args: False)

    assert builder._rationed_mall_spare_target(
        object(), "nauvis", "player", "assembling-machine-2", 4,
    ) == 4


def test_post_starter_intermediate_keeps_exact_blocking_target(monkeypatch) -> None:
    """A four-stick recipe bill must not become a blocking 100-stick batch."""
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: True,
    )
    observed_batches: list[int] = []
    monkeypatch.setattr(
        builder, "_rationed_mall_batch",
        lambda _c, _b, _s, _f, _item, target, *_a, **_k:
        observed_batches.append(target) or False,
    )
    monkeypatch.setattr(
        builder, "_is_pre_core_temporary_mall_item", lambda *_a: False,
    )
    monkeypatch.setattr(
        builder, "mall_reserve_for", lambda *_a: MallReserve(100, 100, 1),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_reserve_machine_target", lambda *_a, **_k: 1,
    )
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", None)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        builder, "ensure_produced",
        lambda *_a, **kwargs: captured.update(kwargs) or (10.5, 10.5),
    )
    messages: list[str] = []

    ready, output = builder._ensure_mall_item(
        object(), object(), "nauvis", "player", "iron-stick", 4, {},
        (0.0, 0.0), messages.append, background=False,
    )

    assert ready and output == (10.5, 10.5)
    assert observed_batches == [4]
    assert captured["stock_target"] == 4
    assert captured["blocking_stock_target"] == 4
    assert captured["stock_gate_target"] == 100
    assert not any("POST-STARTER STACK BATCH" in message for message in messages)


def test_capped_intermediate_reclaims_completed_demand_cell(monkeypatch) -> None:
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: True,
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_batch", lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        builder, "_is_pre_core_temporary_mall_item", lambda *_a: False,
    )
    monkeypatch.setattr(
        builder, "mall_reserve_for", lambda *_a: MallReserve(100, 100, 1),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_reserve_machine_target", lambda *_a, **_k: 1,
    )
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", None)
    def _capped(*_a, **_k):
        raise builder.ProductionPrerequisiteDeferred(
            "bootstrap mall is capped at 12 assemblers",
            code="bootstrap_mall_slot_cap", state="supply_wait", details={},
        )
    monkeypatch.setattr(builder, "ensure_produced", _capped)
    reclaimed: list[dict[str, object]] = []
    monkeypatch.setattr(
        builder, "_reclaim_spent_demand_slot_for_prep",
        lambda *_a, **kwargs: reclaimed.append(kwargs) or True,
    )

    ready, output = builder._ensure_mall_item(
        object(), object(), "nauvis", "player", "iron-stick", 4, {},
        (0.0, 0.0), lambda _message: None, background=False,
    )

    assert (ready, output) == (False, None)
    assert reclaimed == [{"minimum_machines": 1, "stock_target": 4}]


def test_mall_has_eight_slots_until_advanced_circuits_start(monkeypatch) -> None:
    monkeypatch.setattr(builder, "_production_started", lambda *_a: False)
    assert builder._bootstrap_mall_slot_limit(
        object(), "nauvis", "player",
    ) == 8

    monkeypatch.setattr(builder, "_production_started", lambda *_a: True)
    assert builder._bootstrap_mall_slot_limit(
        object(), "nauvis", "player",
    ) == 12


def test_iron_stick_rotates_until_advanced_circuits_then_becomes_independent(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(builder, "_production_started", lambda *_a: False)
    assert builder._is_pre_core_temporary_mall_item(
        object(), "nauvis", "player", "iron-stick",
    )

    monkeypatch.setattr(builder, "_production_started", lambda *_a: True)
    assert not builder._is_pre_core_temporary_mall_item(
        SimpleNamespace(command=lambda *_a: ""), "nauvis", "player", "iron-stick",
    )


def test_post_metal_reserve_services_circuits_then_splitters(monkeypatch) -> None:
    """After starter retirement, background reserves fill whole stacks."""
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
    serviced: list[tuple[str, int, bool]] = []
    monkeypatch.setattr(
        builder, "_ensure_mall_item",
        lambda _client, _bridge, _surface, _force, item, target,
        _targets, _reference, _emit, *, background: (
            serviced.append((item, target, background)) or (False, None)
        ),
    )

    assert builder._prep_post_metal_stack_reserves(
        object(), object(), "nauvis", "player", prepped, targets,
        (0.0, 0.0), messages.append,
    ) is None
    assert serviced == [("electronic-circuit", 200, True)]
    assert targets == {}

    stock["electronic-circuit"] = 200
    builder._prep_post_metal_stack_reserves(
        object(), object(), "nauvis", "player", prepped, targets,
        (0.0, 0.0), messages.append,
    )
    assert serviced[-1] == ("splitter", 50, True)

    stock["splitter"] = 50
    builder._prep_post_metal_stack_reserves(
        object(), object(), "nauvis", "player", prepped, targets,
        (0.0, 0.0), messages.append,
    )
    assert prepped == {
        "_post_metal_stack:electronic-circuit",
        "_post_metal_stack:splitter",
    }
    assert any("alongside stone" in message for message in messages)


def test_post_metal_reserve_yields_controller_to_construction_demand(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: True,
    )
    serviced: list[str] = []
    monkeypatch.setattr(
        builder, "_ensure_mall_item",
        lambda *_args, **_kwargs: serviced.append("reserve"),
    )

    builder._prep_post_metal_stack_reserves(
        object(), object(), "nauvis", "player", set(), {"inserter": 12},
        (0.0, 0.0), lambda _message: None,
    )

    assert serviced == []


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


def test_pipeline_ready_at_half_stock_with_producers() -> None:
    bill = {"assembling-machine-1": 1, "iron-plate": 2, "inserter": 3}
    stock = {"assembling-machine-1": 1, "inserter": 3}

    assert builder._material_bill_pipeline_ready(
        bill, stock, is_scheduled=lambda _name: True,
    ) is True


def test_pipeline_waits_below_half_stock() -> None:
    bill = {"assembling-machine-1": 1, "iron-plate": 2, "inserter": 3}
    stock = {"inserter": 2}

    assert builder._material_bill_pipeline_ready(
        bill, stock, is_scheduled=lambda _name: True,
    ) is False


def test_pipeline_waits_when_missing_item_has_no_producer() -> None:
    bill = {"assembling-machine-1": 1, "iron-plate": 2}
    stock = {"iron-plate": 2}

    assert builder._material_bill_pipeline_ready(
        bill, stock,
        is_scheduled=lambda name: name != "assembling-machine-1",
    ) is False


def test_pipeline_empty_bill_is_never_ready() -> None:
    assert builder._material_bill_pipeline_ready(
        {}, {}, is_scheduled=lambda _name: True,
    ) is False


def _pipeline_priorities(targets: dict[str, int]):
    items = {
        name: SimpleNamespace(status="ready") for name in targets
    }
    return type("Priorities", (), {
        "items": items,
        "describe": lambda *_args: "task",
        "complete": lambda *_args: None,
        "next": lambda _self, current, _tick: next(
            (
                type("Task", (), {"item": name})()
                for name in current
            ),
            None,
        ),
    })()


def test_material_project_starts_at_half_stock(monkeypatch) -> None:
    """A half-stocked cell whose missing inputs are produced starts now."""
    messages: list[str] = []
    ledger = SimpleNamespace(
        declare=lambda *_a, **_k: SimpleNamespace(state="ready"),
        shortage_targets=lambda _project, _stock: {"iron-plate": 1},
    )
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setattr(
        builder, "compact_mall_project_bill",
        lambda _item, **_kwargs: {"assembling-machine-1": 1, "iron-plate": 1},
    )
    monkeypatch.setattr(
        builder, "_material_sources_and_rates", lambda *_a: ({}, {}),
    )
    monkeypatch.setattr(
        builder, "construction_supply_chain_is_scheduled",
        lambda *_a: True,
    )
    monkeypatch.setattr(
        builder.live_base, "transferable_items",
        lambda *_a: {"assembling-machine-1": 1},
    )
    plan = SimpleNamespace(
        spec={}, mall_storage_limit=1, fill_provider=False,
        mall_request_multiplier=None, shared_provider=False,
    )

    builder._reserve_compact_mall_project(
        object(), "nauvis", "player", "fast-inserter", plan, None,
        messages.append,
    )

    assert any("PIPELINE READY" in message for message in messages)


def test_material_project_waits_without_producer_backing(monkeypatch) -> None:
    """A half-stocked cell still waits when a missing input is unproduced."""
    ledger = SimpleNamespace(
        declare=lambda *_a, **_k: SimpleNamespace(state="ready"),
        shortage_targets=lambda _project, _stock: {"iron-plate": 1},
    )
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setattr(
        builder, "compact_mall_project_bill",
        lambda _item, **_kwargs: {"assembling-machine-1": 1, "iron-plate": 1},
    )
    monkeypatch.setattr(
        builder, "_material_sources_and_rates", lambda *_a: ({}, {}),
    )
    monkeypatch.setattr(
        builder, "construction_supply_chain_is_scheduled",
        lambda *_a: False,
    )
    monkeypatch.setattr(
        builder.live_base, "transferable_items",
        lambda *_a: {"assembling-machine-1": 1},
    )
    plan = SimpleNamespace(
        spec={}, mall_storage_limit=1, fill_provider=False,
        mall_request_multiplier=None, shared_provider=False,
    )

    with pytest.raises(MaterialShortage):
        builder._reserve_compact_mall_project(
            object(), "nauvis", "player", "fast-inserter", plan, None,
            lambda _message: None,
        )


def test_ready_pass_serves_peer_tasks_after_a_completion(monkeypatch) -> None:
    """Completing tasks pop out of the targets, so peers advance same pass."""
    served: list[str] = []
    targets = {"transport-belt": 128, "inserter": 3}

    def serve(_client, _bridge, _surface, _force, task, _tick, current,
              _priorities, _reference, _emit):
        served.append(task.item)
        current.pop(task.item, None)

    monkeypatch.setattr(builder, "_serve_mall_task", serve)
    monkeypatch.setattr(
        builder.time, "sleep",
        lambda _seconds: pytest.fail("ready pass slept with servable peers"),
    )
    task = type("Task", (), {"item": "transport-belt"})()

    result = builder._serve_ready_pass(
        object(), object(), "nauvis", "player", task, 0, targets,
        {}, _pipeline_priorities(targets), (0.0, 0.0),
        "automation-science-pack", lambda _message: None,
    )

    assert result is builder._SHORTAGE
    assert served == ["transport-belt", "inserter"]
    assert targets == {}


def test_ready_pass_serves_peers_after_a_deferred_task(monkeypatch) -> None:
    """One deferred batch must not hide an executable peer."""
    served: list[str] = []
    targets = {"transport-belt": 128, "inserter": 3}
    priorities = _pipeline_priorities(targets)

    def serve(_client, _bridge, _surface, _force, task, _tick, current,
              _priorities, _reference, _emit):
        served.append(task.item)
        priorities.items[task.item].status = "deferred"

    monkeypatch.setattr(builder, "_serve_mall_task", serve)
    task = type("Task", (), {"item": "transport-belt"})()

    result = builder._serve_ready_pass(
        object(), object(), "nauvis", "player", task, 0, targets,
        {}, priorities, (0.0, 0.0),
        "automation-science-pack", lambda _message: None,
    )

    assert result is builder._SHORTAGE
    assert served == ["transport-belt", "inserter"]


def test_ready_pass_serves_at_most_three_tasks(monkeypatch) -> None:
    """Multi-serve is bounded even when every task completes instantly."""
    served: list[str] = []
    targets = {f"item-{index}": 1 for index in range(5)}

    def serve(_client, _bridge, _surface, _force, task, _tick, current,
              _priorities, _reference, _emit):
        served.append(task.item)
        current.pop(task.item, None)

    monkeypatch.setattr(builder, "_serve_mall_task", serve)
    task = type("Task", (), {"item": "item-0"})()

    result = builder._serve_ready_pass(
        object(), object(), "nauvis", "player", task, 0, targets,
        {}, _pipeline_priorities(targets), (0.0, 0.0),
        "automation-science-pack", lambda _message: None,
    )

    assert result is builder._SHORTAGE
    assert len(served) == 3
    assert len(targets) == 2


# --- spares on top: at least 20% more, rounded up ------------------------

def test_rationed_spare_makes_at_least_twenty_percent_more(monkeypatch) -> None:
    """A 148-belt bill retires only after ~178 transferable belts exist."""
    monkeypatch.setattr(
        builder, "mall_reserve_for",
        lambda *_args: MallReserve(1000, 1000, 10),
    )
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: False,
    )
    client = SimpleNamespace(command=lambda *_a: "")
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder.live_base, "transferable_items", lambda *_a: {})

    assert builder._rationed_mall_spare_target(
        client, "nauvis", "player", "transport-belt", 148,
    ) == 178
    assert builder._rationed_mall_spare_target(
        client, "nauvis", "player", "transport-belt", 10,
    ) == 12


def test_rationed_spare_covers_requester_wip_gap(monkeypatch) -> None:
    """16 belts locked in requesters force the batch to 164, not 150."""
    monkeypatch.setattr(
        builder, "mall_reserve_for",
        lambda *_args: MallReserve(1000, 1000, 10),
    )
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: False,
    )
    client = SimpleNamespace(command=lambda *_a: "")
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {"transport-belt": 148},
    )
    monkeypatch.setattr(
        builder.live_base, "transferable_items",
        lambda *_a: {"transport-belt": 132},
    )

    assert builder._rationed_mall_spare_target(
        client, "nauvis", "player", "transport-belt", 148,
    ) == 178


def test_rationed_ready_needs_transferable_spares_not_force_stock(
    monkeypatch,
) -> None:
    """148 available with 132 transferable is not READY for a 148 bill."""
    monkeypatch.setattr(
        builder, "_is_pre_core_temporary_mall_item", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_batch", lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        builder, "mall_reserve_for",
        lambda *_a: MallReserve(1000, 1000, 10),
    )
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_args: False,
    )
    client = SimpleNamespace(command=lambda *_a: "")
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {"transport-belt": 148},
    )
    monkeypatch.setattr(
        builder.live_base, "transferable_items",
        lambda *_a: {"transport-belt": 132},
    )

    def _short(*_a, **_k):
        raise MaterialShortage("mall", {"transport-belt": 46}, {})

    monkeypatch.setattr(builder, "ensure_produced", _short)

    ready, output = builder._ensure_mall_item(
        client, object(), "nauvis", "player", "transport-belt", 148, {},
        (0.0, 0.0), lambda _message: None, background=False,
    )

    assert not ready


def test_lagging_build_names_wip_and_keeps_demand(monkeypatch) -> None:
    """A WIP-locked shortfall logs its cause instead of hoping it clears."""
    messages: list[str] = []
    monkeypatch.setattr(builder, "_belt_starved_consumer", lambda *_a: None)
    monkeypatch.setattr(
        builder, "_ensure_mall_item", lambda *_a, **_k: (False, None),
    )
    monkeypatch.setattr(builder, "_queued_mall_prerequisites", lambda *_a: set())
    monkeypatch.setattr(builder, "_deliver_cell_ingredients", lambda *_a: False)
    client = SimpleNamespace(command=lambda *_a: "")
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {"transport-belt": 148},
    )
    monkeypatch.setattr(
        builder.live_base, "transferable_items",
        lambda *_a: {"transport-belt": 132},
    )
    monkeypatch.setattr(builder.live_base, "pending_ghost_count", lambda *_a: 41)
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 0)
    task = SimpleNamespace(item="transport-belt", target=148)
    priorities = SimpleNamespace(
        describe=lambda _task, _tick: "transport-belt task",
        defer=lambda *_a, **_k: None,
        promote=lambda *_a, **_k: None,
    )
    targets = {"transport-belt": 148}

    builder._serve_mall_task(
        client, object(), "nauvis", "player", task, 0, targets,
        priorities, (0.0, 0.0), messages.append,
    )

    assert targets == {"transport-belt": 148}
    assert any("LAGGING BUILD" in message for message in messages)
    assert any("16 locked" in message for message in messages)


# --- drain-aware pop: bill met + loan advancing + stock flat ---------------

_DRAIN_KEY = ("nauvis", "player", "transport-belt")


def _drain_priorities(completed: list[str]):
    return SimpleNamespace(
        describe=lambda _task, _tick: "transport-belt task",
        complete=lambda item, _tick: completed.append(item),
        defer=lambda *_a, **_k: None,
        promote=lambda *_a, **_k: None,
    )


def _mock_drain_serve(monkeypatch, *, previous: int, current: int) -> None:
    """Seed transferable history and a stock reading for one serve pass."""
    monkeypatch.setattr(
        builder, "_DRAIN_WATCH_PREVIOUS_TRANSFERABLE", {_DRAIN_KEY: previous},
    )
    monkeypatch.setattr(
        builder, "_DRAIN_WATCH_LAST_TRANSFERABLE", {_DRAIN_KEY: previous},
    )
    monkeypatch.setattr(builder, "_belt_starved_consumer", lambda *_a: None)
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock",
        lambda *_a: {"transport-belt": current},
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_completion_target", lambda *_a: 147,
    )
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 0)


def test_drain_proven_by_advancing_loan_retires_bill_met_demand(
    monkeypatch,
) -> None:
    """138/147 belts with the loan still advancing and stock flat since the
    last survey: a live consumer is eating output, so the demand retires
    instead of pinning the cell until the livelock guard fires."""
    _mock_drain_serve(monkeypatch, previous=138, current=138)
    messages: list[str] = []
    completed: list[str] = []

    def ensure(*_a, **_k):
        builder._BOOTSTRAP_LOAN_PROGRESS_REVISION += 1
        return False, None

    monkeypatch.setattr(builder, "_ensure_mall_item", ensure)
    monkeypatch.setattr(
        builder, "active_bootstrap_loans",
        lambda *_a: [SimpleNamespace(target_item="transport-belt")],
    )
    targets = {"transport-belt": 122}

    builder._serve_mall_task(
        SimpleNamespace(), object(), "nauvis", "player",
        SimpleNamespace(item="transport-belt", target=122), 0, targets,
        _drain_priorities(completed), (0.0, 0.0), messages.append,
    )

    assert targets == {}
    assert completed == ["transport-belt"]
    assert any("DRAIN-AWARE POP" in message for message in messages)


def test_climbing_stock_keeps_demand_for_full_spare(monkeypatch) -> None:
    """130 after 120 with the loan advancing is still accumulating, not
    draining -- the spare target stands."""
    _mock_drain_serve(monkeypatch, previous=120, current=130)
    messages: list[str] = []
    completed: list[str] = []

    def ensure(*_a, **_k):
        builder._BOOTSTRAP_LOAN_PROGRESS_REVISION += 1
        return False, None

    monkeypatch.setattr(builder, "_ensure_mall_item", ensure)
    monkeypatch.setattr(
        builder, "active_bootstrap_loans",
        lambda *_a: [SimpleNamespace(target_item="transport-belt")],
    )
    monkeypatch.setattr(builder, "_queued_mall_prerequisites", lambda *_a: set())
    monkeypatch.setattr(builder, "_deliver_cell_ingredients", lambda *_a: False)
    targets = {"transport-belt": 122}

    builder._serve_mall_task(
        SimpleNamespace(), object(), "nauvis", "player",
        SimpleNamespace(item="transport-belt", target=122), 0, targets,
        _drain_priorities(completed), (0.0, 0.0), messages.append,
    )

    assert targets == {"transport-belt": 122}
    assert completed == []
    assert not any("DRAIN-AWARE POP" in message for message in messages)


def test_idle_loan_keeps_demand_despite_flat_stock(monkeypatch) -> None:
    """Flat stock with no loan progress is a genuine stall, not proven drain:
    the demand stays and the livelock guard keeps jurisdiction."""
    _mock_drain_serve(monkeypatch, previous=138, current=138)
    messages: list[str] = []
    completed: list[str] = []
    monkeypatch.setattr(
        builder, "_ensure_mall_item", lambda *_a, **_k: (False, None),
    )
    monkeypatch.setattr(
        builder, "active_bootstrap_loans",
        lambda *_a: [SimpleNamespace(target_item="transport-belt")],
    )
    monkeypatch.setattr(builder, "_queued_mall_prerequisites", lambda *_a: set())
    monkeypatch.setattr(builder, "_deliver_cell_ingredients", lambda *_a: False)
    targets = {"transport-belt": 122}

    builder._serve_mall_task(
        SimpleNamespace(), object(), "nauvis", "player",
        SimpleNamespace(item="transport-belt", target=122), 0, targets,
        _drain_priorities(completed), (0.0, 0.0), messages.append,
    )

    assert targets == {"transport-belt": 122}
    assert completed == []


def test_survey_snapshots_transferable_history(monkeypatch) -> None:
    """Each survey shifts current levels into previous before re-reading."""
    monkeypatch.setattr(
        builder, "_DRAIN_WATCH_PREVIOUS_TRANSFERABLE", {_DRAIN_KEY: 100},
    )
    monkeypatch.setattr(
        builder, "_DRAIN_WATCH_LAST_TRANSFERABLE", {_DRAIN_KEY: 120},
    )
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock",
        lambda *_a: {"transport-belt": 130},
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_completion_target", lambda *_a: 147,
    )
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 0)
    priorities = SimpleNamespace(
        sync=lambda *_a: None, next=lambda *_a: None,
    )

    builder._survey_pass(
        SimpleNamespace(), "nauvis", "player", {"transport-belt": 122},
        priorities,
    )

    assert builder._DRAIN_WATCH_PREVIOUS_TRANSFERABLE == {_DRAIN_KEY: 120}
    assert builder._DRAIN_WATCH_LAST_TRANSFERABLE == {_DRAIN_KEY: 130}


# --- standing reserves grow once metal flows ---------------------------------

def test_scarce_startup_keeps_tight_batch() -> None:
    """Dry harnesses without RCON keep the protective tight batch."""
    assert builder._scarce_metal_startup(object(), "nauvis", "player") is True


def test_flowing_metal_keeps_grown_reserve(monkeypatch) -> None:
    """Post-transition belts keep the grown standing reserve instead of the
    need-plus-margin squeeze: X stacks ready, growing past the bill."""
    messages: list[str] = []
    monkeypatch.setattr(
        builder, "_rationed_mall_batch", lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        builder, "_is_pre_core_temporary_mall_item", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder, "mall_reserve_for",
        lambda *_a: MallReserve(400, 400, 4),
    )
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder, "_bootstrap_reserve_machine_target", lambda *_a, **_k: 1,
    )
    produced: dict[str, int] = {}

    def _produce(*_a, **_k):
        produced.update(_k)
        return True, (0.0, 0.0)

    monkeypatch.setattr(builder, "ensure_produced", _produce)
    client = SimpleNamespace(command=lambda *_a: "")

    ready, _output = builder._ensure_mall_item(
        client, object(), "nauvis", "player", "transport-belt", 128, {},
        (0.0, 0.0), messages.append, background=False,
    )

    assert ready
    assert produced.get("stock_target") == 400
    assert any("MALL STANDING RESERVE" in message for message in messages)


def test_scarce_metal_squeezes_to_need_plus_margin(monkeypatch) -> None:
    """While starters carry the base, the same item stays a tight batch."""
    messages: list[str] = []
    monkeypatch.setattr(
        builder, "_rationed_mall_batch", lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        builder, "_is_pre_core_temporary_mall_item", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder, "mall_reserve_for",
        lambda *_a: MallReserve(400, 400, 4),
    )
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_a: False,
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_spare_target", lambda *_a: 154,
    )
    monkeypatch.setattr(
        builder, "_bootstrap_reserve_machine_target", lambda *_a, **_k: 1,
    )
    produced: dict[str, int] = {}

    def _produce(*_a, **_k):
        produced.update(_k)
        return True, (0.0, 0.0)

    monkeypatch.setattr(builder, "ensure_produced", _produce)
    client = SimpleNamespace(command=lambda *_a: "")

    ready, _output = builder._ensure_mall_item(
        client, object(), "nauvis", "player", "transport-belt", 128, {},
        (0.0, 0.0), messages.append, background=False,
    )

    assert ready
    assert produced.get("stock_target") == 154
    assert any("MALL TEMPORARY RESERVE" in message for message in messages)


def test_finite_requester_keeps_twenty_percent_headroom(monkeypatch) -> None:
    """A 10-craft finite batch asks for 12 crafts of headroom, still bounded
    by the throughput window so one cell cannot warehouse shared inputs."""
    monkeypatch.setattr(
        builder, "standard_mall_request_multiplier", lambda *_a: 30,
    )
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_a: True,
    )
    spec = {"machine": "assembling-machine-1", "craft_time": 0.5}

    assert builder._mall_request_multiplier(
        object(), "nauvis", "player", "inserter", spec, 10,
        finite_batch=True,
    ) == 12


# --- binding demands outrank standing reserves --------------------------------

def test_blocking_demand_promotes_to_rating_100_and_clears_on_done(
    monkeypatch, tmp_path,
) -> None:
    """Foundation-blocked drills outrank the 75 default while mine ghosts
    wait; stale entries retire with their demand so old bottlenecks cannot
    pin scheduling forever."""
    from orchestrator.priority_list import PriorityList

    monkeypatch.setattr(
        builder, "_BLOCKING_MALL_ITEMS", {"electric-mining-drill", "stale-item"},
    )
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock", lambda *_a: {},
    )
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 0)
    priorities = PriorityList(tmp_path / "priorities.json", 0)

    _tick, _task = builder._survey_pass(
        SimpleNamespace(), "nauvis", "player",
        {"electric-mining-drill": 6, "transport-belt": 122}, priorities,
    )

    assert priorities.items["electric-mining-drill"].base_rating == 100
    assert priorities.items["transport-belt"].base_rating == 100
    assert "stale-item" not in builder._BLOCKING_MALL_ITEMS


def test_unblocked_demand_keeps_its_default_rating(monkeypatch, tmp_path) -> None:
    """Without foundation pressure the static ratings stand: drills stay 75."""
    from orchestrator.priority_list import PriorityList

    monkeypatch.setattr(builder, "_BLOCKING_MALL_ITEMS", set())
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock", lambda *_a: {},
    )
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 0)
    priorities = PriorityList(tmp_path / "priorities.json", 0)

    builder._survey_pass(
        SimpleNamespace(), "nauvis", "player",
        {"electric-mining-drill": 6}, priorities,
    )

    assert priorities.items["electric-mining-drill"].base_rating == 75


# --- binding marks stay narrow; ratings do not leak across episodes --------

def test_prep_bulk_demand_does_not_mark_binding(monkeypatch) -> None:
    """2026-09-03 20:37 run: PREP DEMAND queued belts+inserters+splitters
    together and every one of them promoted to 100, so the alphabetically
    first stuck task (splitter) starved everything behind it for 12 passes.
    Only ghost-driven demands mark binding; bulk prep queues do not."""
    monkeypatch.setattr(builder, "_BLOCKING_MALL_ITEMS", {"sentinel"})
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {},
    )
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: None)
    monkeypatch.setattr(builder, "_bootstrap_state", lambda *_a: None)

    def _short(*_a, **_k):
        raise MaterialShortage("mine", {"electric-mining-drill": 6}, {})

    monkeypatch.setattr(builder, "build_mining_stage", _short)
    targets: dict[str, int] = {}
    pending: dict[str, dict[str, int]] = {}

    assert builder._prep_plate_extraction(
        object(), object(), "nauvis", "player", "iron-plate", set(), {},
        targets, (0.0, 0.0), lambda _message: None,
        pending_materials=pending, furnace_target=6,
    ) is False

    assert targets == {"electric-mining-drill": 6}
    assert builder._BLOCKING_MALL_ITEMS == {"sentinel"}


def test_ghost_driven_demand_marks_binding() -> None:
    builder._BLOCKING_MALL_ITEMS.clear()
    try:
        builder._mark_binding_demands(
            MaterialShortage("mine", {"electric-mining-drill": 4}, {}),
        )

        assert builder._BLOCKING_MALL_ITEMS == {"electric-mining-drill"}
    finally:
        builder._BLOCKING_MALL_ITEMS.clear()


def test_priority_load_resets_ratings_and_defers(tmp_path) -> None:
    """A promoted base_rating and far-future retry from a dead episode must
    not steer the next one; identity, target, and progress survive."""
    from orchestrator.priority_list import PriorityList

    path = tmp_path / "priorities.json"
    path.write_text(
        '{"version": "1.0.0", "tick": 244161, "items": [{'
        '"item": "fast-inserter", "target": 1, "base_rating": 100,'
        '"created_tick": 239929, "progress_percent": 100,'
        '"status": "deferred", "reason": "blocking prerequisite",'
        '"retry_tick": 247761}]}',
        encoding="utf-8",
    )

    loaded = PriorityList(path, 230941)

    task = loaded.items["fast-inserter"]
    assert task.base_rating == 45
    assert task.reason == ""
    assert task.status == "ready"
    assert task.target == 1


# --- starved loan ingredients become mall demands ----------------------------

def _starved_loan(monkeypatch) -> None:
    """A fast-inserter loan whose inserter step needs circuits no one makes."""
    monkeypatch.setitem(builder.LINE_RECIPES, "fast-inserter", {
        "machine": "assembling-machine-1",
        "ingredients": ["electronic-circuit", "iron-plate"],
        "amounts": [2, 2], "product_amount": 1, "craft_time": 0.5,
    })
    monkeypatch.setitem(builder.LINE_RECIPES, "inserter", {
        "machine": "assembling-machine-1",
        "ingredients": ["electronic-circuit", "iron-plate"],
        "amounts": [1, 1], "product_amount": 1, "craft_time": 0.5,
    })
    monkeypatch.setattr(
        builder, "active_bootstrap_loans",
        lambda *_a: [SimpleNamespace(
            target_item="fast-inserter", step_recipe="inserter",
            current_recipe="inserter",
        )],
    )
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock",
        lambda *_a: {"fast-inserter": 0, "electronic-circuit": 0,
                     "iron-plate": 50},
    )
    monkeypatch.setattr(
        builder, "_production_started",
        lambda *args: args[-1] != "electronic-circuit",
    )
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 0)


def test_starved_loan_ingredient_is_queued_as_demand(monkeypatch) -> None:
    """2026-09-04: 170 circuits sat in requester WIP with zero producers
    while inserter/fast-inserter loans stalled 20+ min beside free pool
    slots. The missing assembler-made ingredient must become a demand."""
    _starved_loan(monkeypatch)
    messages: list[str] = []
    promoted: list[str] = []
    priorities = SimpleNamespace(
        promote=lambda item, _target, _tick: promoted.append(item),
    )
    targets: dict[str, int] = {}

    builder._queue_starved_loan_ingredients(
        SimpleNamespace(), "nauvis", "player", "fast-inserter", 1,
        targets, priorities, messages.append,
    )

    assert targets == {"electronic-circuit": 1}
    assert promoted == ["electronic-circuit"]
    assert any("MALL INGREDIENT DEMAND" in message for message in messages)


def test_starved_loan_skips_covered_ingredients(monkeypatch) -> None:
    """Stocked, produced, loan-covered, or queued ingredients stay quiet;
    only true orphans are queued, one per pass."""
    _starved_loan(monkeypatch)
    priorities = SimpleNamespace(promote=lambda *_a: None)

    # Stocked plates are skipped; circuits queue once then stay queued.
    targets: dict[str, int] = {}
    builder._queue_starved_loan_ingredients(
        SimpleNamespace(), "nauvis", "player", "fast-inserter", 1,
        targets, priorities, lambda _message: None,
    )
    assert set(targets) == {"electronic-circuit"}

    messages: list[str] = []
    builder._queue_starved_loan_ingredients(
        SimpleNamespace(), "nauvis", "player", "fast-inserter", 1,
        targets, priorities, messages.append,
    )
    assert messages == []

    # A covering loan suppresses the demand.
    monkeypatch.setattr(
        builder, "active_bootstrap_loans",
        lambda *_a: [
            SimpleNamespace(target_item="fast-inserter", step_recipe="inserter",
                            current_recipe="inserter"),
            SimpleNamespace(target_item="electronic-circuit",
                            step_recipe="electronic-circuit",
                            current_recipe="electronic-circuit"),
        ],
    )
    fresh: dict[str, int] = {}
    builder._queue_starved_loan_ingredients(
        SimpleNamespace(), "nauvis", "player", "fast-inserter", 1,
        fresh, priorities, messages.append,
    )
    assert fresh == {}
    assert messages == []


# --- craft-proof pop + evergreen restock --------------------------------------

def _craft_loan(**overrides):
    fields = {
        "original_recipe": "iron-gear-wheel", "target_item": "transport-belt",
        "target_count": 5, "spare_target_count": 8, "side": "left",
        "requester_position": (39.5, 38.5),
        "current_recipe": "transport-belt", "step_recipe": "transport-belt",
        "step_target_count": 8, "step_baseline_finished": 100,
        "step_required_crafts": 5, "step_minimum_crafts": 5,
    }
    fields.update(overrides)
    return builder.MallBootstrapLoan(**fields)


def test_craft_proof_pops_dispersed_demand(monkeypatch, tmp_path) -> None:
    """2026-09-03: a circuit batch held at 195/200 with WIP siphoning output
    while crafts advanced past 400. Monotonic loan proof retires the demand;
    stock re-queues naturally on new need."""
    from orchestrator.priority_list import PriorityList

    monkeypatch.setattr(
        builder, "_transferable_or_available_stock",
        lambda *_a: {"transport-belt": 3},
    )
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 0)
    monkeypatch.setattr(
        builder, "_rationed_mall_completion_target", lambda *_a: 8,
    )
    monkeypatch.setattr(
        builder, "active_bootstrap_loans",
        lambda *_a: [_craft_loan()],
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 105,
    )
    priorities = PriorityList(tmp_path / "priorities.json", 0)
    targets = {"transport-belt": 5}

    builder._survey_pass(
        SimpleNamespace(), "nauvis", "player", targets, priorities,
    )

    assert targets == {}
    assert priorities.items["transport-belt"].reason == (
        "loan crafts prove the bill; stock dispersed"
    )


def test_stalled_loan_keeps_its_demand(monkeypatch, tmp_path) -> None:
    """No craft progress and short stock is a genuine stall, not proof."""
    from orchestrator.priority_list import PriorityList

    monkeypatch.setattr(
        builder, "_transferable_or_available_stock",
        lambda *_a: {"transport-belt": 3},
    )
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 0)
    monkeypatch.setattr(
        builder, "_rationed_mall_completion_target", lambda *_a: 8,
    )
    monkeypatch.setattr(
        builder, "active_bootstrap_loans",
        lambda *_a: [_craft_loan()],
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 100,
    )
    priorities = PriorityList(tmp_path / "priorities.json", 0)
    targets = {"transport-belt": 5}

    builder._survey_pass(
        SimpleNamespace(), "nauvis", "player", targets, priorities,
    )

    assert targets == {"transport-belt": 5}


def test_stockout_without_producer_requeues_belts(monkeypatch, tmp_path) -> None:
    """2026-09-03: science drew belts to zero after the last loan restored;
    nothing re-queued and the run died with a full mall. An evergreen item
    with no producer restocks itself."""
    from orchestrator.priority_list import PriorityList

    monkeypatch.setattr(
        builder, "_transferable_or_available_stock", lambda *_a: {},
    )
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 0)
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: None)
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: [])
    priorities = PriorityList(tmp_path / "priorities.json", 0)
    targets: dict[str, int] = {}

    builder._survey_pass(
        SimpleNamespace(), "nauvis", "player", targets, priorities,
        {"transport-belt"},
    )

    assert targets == {"transport-belt": 200}
    assert priorities.items["transport-belt"].reason == (
        "restocked after stockout with no producer"
    )


def test_cold_start_does_not_queue_evergreen_reserve_before_belt_prep(
    monkeypatch, tmp_path,
) -> None:
    """The first foundation's exact belt bill must not inflate to 200."""
    from orchestrator.priority_list import PriorityList

    monkeypatch.setattr(
        builder, "_transferable_or_available_stock", lambda *_a: {},
    )
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 0)
    priorities = PriorityList(tmp_path / "priorities.json", 0)
    targets: dict[str, int] = {}

    builder._survey_pass(
        SimpleNamespace(), "nauvis", "player", targets, priorities, set(),
    )

    assert targets == {}


def test_stockout_skips_when_covered(monkeypatch, tmp_path) -> None:
    """Producer lines, active loans, queued demands, and healthy stock each
    suppress the watchdog on their own."""
    from orchestrator.priority_list import PriorityList

    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 0)
    priorities = PriorityList(tmp_path / "priorities.json", 0)

    def _survey(stock, *, line=None, loans=(), queued=None):
        monkeypatch.setattr(
            builder, "_transferable_or_available_stock", lambda *_a: dict(stock),
        )
        monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: line)
        monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: list(loans))
        targets = dict(queued or {})
        builder._survey_pass(
            SimpleNamespace(), "nauvis", "player", targets, priorities,
        )
        return targets

    line = SimpleNamespace(machine_count=2)
    assert _survey({}, line=line) == {}
    assert _survey({}, loans=[_craft_loan()]) == {}
    assert _survey({"transport-belt": 3}, queued={"transport-belt": 5}) == {
        "transport-belt": 5}
    assert _survey({"transport-belt": 100}) == {}


def test_splitter_shortfall_counts_as_cold_start_circular() -> None:
    """The 2026-09-04 mall-first opening died at +6s: the first foundation
    needs belts AND splitters with zero belt stock, and the splitter
    shortfall was not recognized as part of the circle."""
    assert {"splitter", "fast-splitter", "express-splitter"} <= set(
        builder._BOOTSTRAP_CIRCULAR_ENTITIES
    )


def test_first_foundation_bill_is_a_cold_start_shortage(monkeypatch) -> None:
    """The exact +6s bill (belts + splitters + fast inserters, no furnace
    line yet) must take the beltless-seed fallback instead of escaping."""
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: None)
    error = MaterialShortage(
        "initial_iron-plate_system",
        {"fast-inserter": 1, "splitter": 3, "transport-belt": 123},
        {},
    )

    assert builder._cold_start_belt_shortage(
        object(), "nauvis", "player", "iron-plate", error,
    ) is True


def test_mall_batch_shortage_queues_demands_instead_of_dying(monkeypatch) -> None:
    """A MaterialShortage escaping the rationed batch (e.g. a plate whose
    own foundation bill is unaffordable) must hand demands back, not kill
    the run with an unhandled exception."""
    def _raise(*_args: object, **_kwargs: object) -> bool:
        raise MaterialShortage("initial_iron-plate_system", {"transport-belt": 5}, {})
    monkeypatch.setattr(builder, "_rationed_mall_batch", _raise)
    mall_targets: dict[str, int] = {}

    ready, output = builder._ensure_mall_item(
        object(), object(), "nauvis", "player", "transport-belt", 200,
        mall_targets, (0.0, 0.0), lambda _message: None, background=False,
    )

    assert (ready, output) == (False, None)
    assert mall_targets.get("transport-belt", 0) >= 5


def test_shared_provider_cell_is_borrowable_as_fallback(monkeypatch) -> None:
    """2026-09-04: every paired half shares one provider, and skipping all
    of them stalled a run with 7 live cells and no active loans."""
    gear_positions = [(36.5, 32.5), (42.5, 32.5)]
    line = SimpleNamespace(machine_count=2, machine_positions=gear_positions)
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda _c, _s, _f, recipe, _m, **_k: line if recipe == "iron-gear-wheel" else None,
    )
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(
        builder, "locate_mall_cell", lambda *_a: ((35, 31), "left"),
    )
    monkeypatch.setattr(
        builder, "mall_slot_uses_shared_provider", lambda *_a: True,
    )
    def _entity(_client, _surface, position):
        if tuple(position) == (39.5, 32.5):
            return {"name": "requester-chest"}
        if tuple(position) == (39.5, 31.5):
            return {"name": "passive-provider-chest"}
        return {"name": "assembling-machine-1"}
    monkeypatch.setattr(builder.live_base, "entity_at", _entity)

    borrowed = builder._borrow_free_mall_cell(
        object(), "nauvis", "player", "splitter", (3.0, -1.0),
        allowed_original_recipes=None, allow_shared_provider=False,
        require_stocked_original=False, excluded_origins=set(),
    )

    assert borrowed is not None
    assert borrowed[0] == "iron-gear-wheel"


def test_dedicated_cell_ranks_ahead_of_shared_cell(monkeypatch) -> None:
    gear_positions = [(36.5, 32.5), (42.5, 32.5)]
    line = SimpleNamespace(machine_count=2, machine_positions=gear_positions)
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda _c, _s, _f, recipe, _m, **_k: line if recipe == "iron-gear-wheel" else None,
    )
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(
        builder, "locate_mall_cell", lambda *_a: ((35, 31), "left"),
    )
    monkeypatch.setattr(
        builder, "mall_slot_uses_shared_provider",
        lambda _c, _s, position, _r: tuple(position) == (36.5, 32.5),
    )
    def _entity(_client, _surface, position):
        if tuple(position) == (39.5, 32.5):
            return {"name": "requester-chest"}
        if tuple(position) == (39.5, 31.5):
            return {"name": "passive-provider-chest"}
        return {"name": "assembling-machine-1"}
    monkeypatch.setattr(builder.live_base, "entity_at", _entity)

    borrowed = builder._borrow_free_mall_cell(
        object(), "nauvis", "player", "splitter", (3.0, -1.0),
        allowed_original_recipes=None, allow_shared_provider=False,
        require_stocked_original=False, excluded_origins=set(),
    )

    assert borrowed is not None
    assert tuple(borrowed[1]) == (42.5, 32.5)


def test_last_anchor_producer_is_never_borrowed(monkeypatch) -> None:
    line = SimpleNamespace(machine_count=1, machine_positions=[(36.5, 32.5)])
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda _c, _s, _f, recipe, _m, **_k: line if recipe == "iron-gear-wheel" else None,
    )
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})

    borrowed = builder._borrow_free_mall_cell(
        object(), "nauvis", "player", "splitter", (3.0, -1.0),
        allowed_original_recipes=None, allow_shared_provider=False,
        require_stocked_original=False, excluded_origins=set(),
    )

    assert borrowed is None


def test_live_cell_request_window_never_shrinks_below_throughput(monkeypatch) -> None:
    """2026-09-04: a transient finite batch rewrote the circuit base window
    15 -> 6 and starved the 200 reserve for 700s. The refresh floors at the
    standard throughput window."""
    from planners.mall_layout import request_multiplier
    machines = ((50.5, 32.5), (56.5, 32.5))
    provider = (53.5, 31.5)
    overrides: list[int] = []
    monkeypatch.setattr(builder, "_MALL_REFRESH_SIGNATURES", set())
    monkeypatch.setattr(builder, "_mineable", lambda _item: False)
    def _capture(*_args: object, **kwargs: object) -> bool:
        overrides.append(int(kwargs["request_multiplier_override"]))
        return True
    monkeypatch.setattr(builder, "refresh_paired_mall_requests", _capture)
    monkeypatch.setattr(builder, "_paired_mall_provider", lambda *_a: provider)
    monkeypatch.setattr(
        builder, "mall_slot_uses_shared_provider", lambda *_a: False,
    )
    monkeypatch.setattr(builder, "_submit", lambda *_a, **_k: None)
    plan = SimpleNamespace(
        existing=SimpleNamespace(machine_positions=machines),
        spec={"machine": "assembling-machine-1", "craft_time": 0.5},
        production_target=5,
        mall_storage_limit=5,
        fill_provider=False,
        mall_request_multiplier=6,
    )

    builder._refresh_mall_cell(
        object(), object(), "nauvis", "player", "electronic-circuit", plan,
        lambda _message: None, upgrade_bootstrap=False,
        stock_gate_target=5,
    )

    assert overrides == [request_multiplier("assembling-machine-1", 0.5)]
    assert overrides[0] > 6


def test_startup_request_clamp_keeps_its_deliberate_policy(monkeypatch) -> None:
    """Splitter/underground pre-transition windows stay tight on purpose;
    only accidental finite-batch shrinkage is floored."""
    machines = ((50.5, 32.5), (56.5, 32.5))
    provider = (53.5, 31.5)
    overrides: list[int] = []
    monkeypatch.setattr(builder, "_MALL_REFRESH_SIGNATURES", set())
    monkeypatch.setattr(builder, "_mineable", lambda _item: False)
    def _capture(*_args: object, **kwargs: object) -> bool:
        overrides.append(int(kwargs["request_multiplier_override"]))
        return True
    monkeypatch.setattr(builder, "refresh_paired_mall_requests", _capture)
    monkeypatch.setattr(builder, "_paired_mall_provider", lambda *_a: provider)
    monkeypatch.setattr(
        builder, "mall_slot_uses_shared_provider", lambda *_a: False,
    )
    monkeypatch.setattr(builder, "_submit", lambda *_a, **_k: None)
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_a: False,
    )
    plan = SimpleNamespace(
        existing=SimpleNamespace(machine_positions=machines),
        spec={"machine": "assembling-machine-1", "craft_time": 0.5},
        production_target=3,
        mall_storage_limit=3,
        fill_provider=False,
        mall_request_multiplier=2,
    )

    builder._refresh_mall_cell(
        object(), object(), "nauvis", "player", "splitter", plan,
        lambda _message: None, upgrade_bootstrap=False,
        stock_gate_target=3,
    )

    assert overrides == [2]


def test_canonical_provider_limit_is_monotonic_per_item(monkeypatch) -> None:
    monkeypatch.setattr(builder, "_MALL_ITEM_PROVIDER_LIMITS", {})

    assert builder._canonical_mall_provider_limit("n", "p", "gear", 5) == 5
    assert builder._canonical_mall_provider_limit("n", "p", "gear", 3) == 5
    assert builder._canonical_mall_provider_limit("n", "p", "gear", 240) == 240
    assert builder._canonical_mall_provider_limit("n", "p", "belt", 3) == 3


def test_twin_cells_converge_on_one_provider_limit(monkeypatch) -> None:
    """Same item, two providers, different transient demands: both bars land
    on the max (user standard 2026-09-04)."""
    machines = ((50.5, 32.5), (56.5, 32.5))
    providers = iter([(10.0, 10.0), (20.0, 20.0)])
    submitted: list[dict] = []
    monkeypatch.setattr(builder, "_MALL_REFRESH_SIGNATURES", set())
    monkeypatch.setattr(builder, "_MALL_PROVIDER_CAPACITY_FLOORS", {})
    monkeypatch.setattr(builder, "_MALL_ITEM_PROVIDER_LIMITS", {})
    monkeypatch.setattr(builder, "_mineable", lambda _item: False)
    monkeypatch.setattr(
        builder, "refresh_paired_mall_requests", lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        builder, "_paired_mall_provider", lambda *_a: next(providers),
    )
    monkeypatch.setattr(
        builder, "mall_slot_uses_shared_provider", lambda *_a: False,
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda *_args, **_kwargs: submitted.append(_args[3]),
    )

    for target in (400, 5):
        plan = SimpleNamespace(
            existing=SimpleNamespace(machine_positions=machines),
            spec={"machine": "assembling-machine-2", "craft_time": 0.5},
            production_target=target,
            mall_storage_limit=target,
            fill_provider=False,
            mall_request_multiplier=15,
        )
        builder._refresh_mall_cell(
            object(), object(), "nauvis", "player", "transport-belt", plan,
            lambda _message: None, upgrade_bootstrap=False,
            stock_gate_target=target,
        )

    counts = [
        count for plan in submitted for count in _provider_counts(plan)
    ]
    assert counts == [400, 400]


def _provider_counts(plan: dict) -> list[int]:
    return [
        action["inventory_limit"]["count"]
        for phase in plan["phases"]
        for action in phase["actions"]
        if action.get("entity") == "passive-provider-chest"
        and isinstance(action.get("inventory_limit"), dict)
    ]


def test_stock_parser_survives_a_glued_loan_record() -> None:
    """2026-09-04: '250|39.5|32.5|assembling-machine-1' arrived glued into an
    available_items response and ended the run on a ValueError. Real stock
    parses; the garbled chunk is skipped and retained for diagnosis."""
    from orchestrator.live_base import (
        _MALFORMED_STOCK_CHUNKS,
        _parse_stock_counts,
    )
    del _MALFORMED_STOCK_CHUNKS[:]

    counts = _parse_stock_counts(
        "iron-plate=12,copper-plate=7,"
        "mall-bootstrap:v3:iron-gear-wheel:splitter:50:50:left:"
        "electronic-circuit:1046:250|39.5|32.5|assembling-machine-1,"
        "transport-belt=288,"
    )

    assert counts == {
        "iron-plate": 12, "copper-plate": 7, "transport-belt": 288,
    }
    assert len(_MALFORMED_STOCK_CHUNKS) == 1
    assert "250|39.5|32.5" in _MALFORMED_STOCK_CHUNKS[0]
    del _MALFORMED_STOCK_CHUNKS[:]


def test_stock_parser_keeps_working_on_clean_responses() -> None:
    from orchestrator.live_base import _parse_stock_counts

    assert _parse_stock_counts("") == {}
    assert _parse_stock_counts("iron-plate=3,,copper-plate=0,") == {
        "iron-plate": 3, "copper-plate": 0,
    }


def _yield_harness(monkeypatch, *, finished: int, pipe_flowing: bool):
    """One AM2 loan stalled on steel (no producer, no stock); pipe waits."""
    from orchestrator.mall_bootstrap import MallBootstrapLoan
    monkeypatch.setitem(builder.LINE_RECIPES, "assembling-machine-2", {
        "machine": "assembling-machine-1",
        "ingredients": ["steel-plate", "electronic-circuit", "iron-gear-wheel"],
        "amounts": [2, 3, 5], "product_amount": 1, "craft_time": 0.5,
    })
    loan = MallBootstrapLoan(
        original_recipe="iron-gear-wheel",
        target_item="assembling-machine-2", target_count=1, side="left",
        requester_position=(39.5, 32.5),
        current_recipe="assembling-machine-2",
        step_recipe="assembling-machine-2",
        step_baseline_finished=0,
        step_required_crafts=1,
        step_minimum_crafts=1,
    )
    monkeypatch.setattr(
        builder, "active_bootstrap_loans", lambda *_a: [loan],
    )
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock", lambda *_a: {},
    )
    monkeypatch.setattr(
        builder, "_production_started",
        lambda _c, _s, _f, item: item != "steel-plate" and (
            item != "iron-plate" or pipe_flowing
        ),
    )
    monkeypatch.setattr(
        builder.live_base, "progress_counters",
        lambda *_a, **_k: {(36.5, 32.5): finished * 1000},
    )
    return loan


def test_blocked_loan_yields_its_cell_to_pipe(monkeypatch) -> None:
    """2026-09-04: pipe rung waited 470s on an AM2 batch whose steel had no
    producer, while steel admission waited on pipe output. The stalled loan
    restores and pipe starts."""
    loan = _yield_harness(monkeypatch, finished=0, pipe_flowing=True)
    restored: list[str] = []
    started: list[str] = []
    monkeypatch.setattr(
        builder, "_borrow_free_mall_cell",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder, "_restore_bootstrap_loan",
        lambda *_a, **_k: restored.append(_a[4].target_item),
    )
    def _fake_start(*args: object, **kwargs: object) -> str | None:
        if kwargs.get("preempt_for") is not None:
            return "handoff"
        started.append(str(args[4].target_item))
        return "started"
    monkeypatch.setattr(builder, "_submit_bootstrap_loan", _fake_start)

    # Borrow frees up after the restore: first call finds no cell, the
    # recursion (post-restore) takes the yielded gear half.
    calls = {"n": 0}
    def _borrow(*_args: object, **_kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return ("iron-gear-wheel", (36.5, 32.5), (35, 31), "left",
                "assembling-machine-1")
    monkeypatch.setattr(builder, "_borrow_free_mall_cell", _borrow)
    monkeypatch.setattr(
        builder, "active_bootstrap_loans", lambda *_a: [loan],
    )

    remedy = builder._start_bootstrap_loan(
        object(), object(), "nauvis", "player", "pipe", 10, (3.0, -1.0),
        lambda _message: None,
    )

    assert restored == ["assembling-machine-2"]
    assert started == ["pipe"]
    assert remedy == "started"


def test_progressing_loan_keeps_its_cell(monkeypatch) -> None:
    """A loan that advanced its step is serviced first, not preempted."""
    _yield_harness(monkeypatch, finished=3, pipe_flowing=True)
    restored: list[str] = []
    monkeypatch.setattr(
        builder, "_borrow_free_mall_cell", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder, "_restore_bootstrap_loan",
        lambda *_a, **_k: restored.append(_a[4].target_item),
    )
    monkeypatch.setattr(
        builder, "_submit_bootstrap_loan", lambda *_a, **_k: "handoff",
    )

    remedy = builder._start_bootstrap_loan(
        object(), object(), "nauvis", "player", "pipe", 10, (3.0, -1.0),
        lambda _message: None,
    )

    assert restored == []
    assert remedy == "handoff"


def test_blocked_waiter_does_not_preempt(monkeypatch) -> None:
    """Yielding to a waiter that cannot run either would just thrash."""
    _yield_harness(monkeypatch, finished=0, pipe_flowing=False)
    restored: list[str] = []
    monkeypatch.setattr(
        builder, "_borrow_free_mall_cell", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder, "_restore_bootstrap_loan",
        lambda *_a, **_k: restored.append(_a[4].target_item),
    )
    monkeypatch.setattr(
        builder, "_submit_bootstrap_loan", lambda *_a, **_k: "handoff",
    )

    remedy = builder._start_bootstrap_loan(
        object(), object(), "nauvis", "player", "pipe", 10, (3.0, -1.0),
        lambda _message: None,
    )

    assert restored == []
    assert remedy == "handoff"


def test_find_line_ghost_scan_is_optional() -> None:
    """Anchor protection passes include_ghosts=False so an unbuilt ghost can
    never pose as the producer that remains after a borrow."""
    from orchestrator import live_base

    seen: list[str] = []

    class _Stub:
        def command(self, text: str) -> str:
            seen.append(text)
            return "1 1 36.5:32.5 0"

    live_base.find_line(_Stub(), "nauvis", "player", "copper-cable",
                        "assembling-machine-1", include_ghosts=False)
    live_base.find_line(_Stub(), "nauvis", "player", "copper-cable",
                        "assembling-machine-1", include_ghosts=True)

    assert "type='entity-ghost',force=f" not in seen[0]
    assert "type='entity-ghost',force=f" in seen[1]


def _borrow_harness(monkeypatch, lines: dict) -> None:
    def _find(_c, _s, _f, recipe, _m, **_k):
        spec = lines.get(recipe)
        if spec is None:
            return None
        return SimpleNamespace(
            machine_count=spec[0], machine_positions=spec[1],
        )
    monkeypatch.setattr(builder.live_base, "find_line", _find)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(
        builder, "locate_mall_cell", lambda *_a: ((35, 31), "left"),
    )
    monkeypatch.setattr(
        builder, "mall_slot_uses_shared_provider", lambda *_a: False,
    )
    def _entity(_client, _surface, position):
        if tuple(position) == (39.5, 32.5):
            return {"name": "requester-chest"}
        if tuple(position) == (39.5, 31.5):
            return {"name": "passive-provider-chest"}
        return {"name": "assembling-machine-1"}
    monkeypatch.setattr(builder.live_base, "entity_at", _entity)


def _borrow(monkeypatch, target: str) -> object:
    return builder._borrow_free_mall_cell(
        object(), "nauvis", "player", target, (3.0, -1.0),
        allowed_original_recipes=None, allow_shared_provider=False,
        require_stocked_original=False, excluded_origins=set(),
    )


def test_twin_feedstock_cell_stays_borrowable(monkeypatch) -> None:
    """Borrowing one of two real producers leaves one running: healthy
    rotation (2026-09-04: refusing twin borrows stalled a run on an AM1
    batch with gear twins standing idle). Only the last real producer is
    protected, by the anchor guard on real-only counts."""
    _borrow_harness(monkeypatch, {
        "copper-cable": (2, [(36.5, 38.5), (42.5, 38.5)]),
    })

    for target in ("transport-belt", "inserter"):
        borrowed = _borrow(monkeypatch, target)

        assert borrowed is not None
        assert borrowed[0] == "copper-cable"


def _stalled_gate_loan(monkeypatch):
    """A drill loan whose circuit step sits disabled at 11/18 (the 2026-09-04
    +503s shape)."""
    from orchestrator.mall_bootstrap import MallBootstrapLoan
    loan = MallBootstrapLoan(
        original_recipe="iron-gear-wheel",
        target_item="electronic-circuit", target_count=18, side="left",
        requester_position=(39.5, 32.5),
        current_recipe="electronic-circuit",
        step_recipe="electronic-circuit",
        step_target_count=18,
        step_baseline_finished=0,
        step_required_crafts=7,
        step_minimum_crafts=7,
    )
    stock = {"electronic-circuit": 11, "copper-cable": 60, "iron-plate": 100}
    monkeypatch.setattr(builder, "_LOAN_GATE_REFRESH_ATTEMPTS", {})
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: (dict(stock), dict(stock)),
    )
    monkeypatch.setattr(
        builder.live_base, "progress_counters",
        lambda *_a, **_k: {(36.5, 32.5): 0},
    )
    monkeypatch.setattr(
        builder.live_base, "entity_status_name",
        lambda *_a: "disabled_by_control_behavior",
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_persisted_step", lambda *_a: None,
    )
    monkeypatch.setattr(builder, "_deliver_cell_ingredients", lambda *_a: False)
    monkeypatch.setattr(builder, "consume_wait", lambda *_a: None)
    monkeypatch.setattr(builder.time, "sleep", lambda *_a: None)
    return loan


def test_disabled_loan_gate_refreshes_instead_of_dying(monkeypatch) -> None:
    """2026-09-04: a drill loan's circuit step sat disabled at 11/18 on a
    bill-frozen gate and ended the run. Refresh the gate and continue."""
    loan = _stalled_gate_loan(monkeypatch)
    submitted: list[tuple[str, dict]] = []
    messages: list[str] = []
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, name, _e: submitted.append((name, plan)),
    )

    remedy = builder._submit_bootstrap_loan(
        object(), object(), "nauvis", "player", loan, messages.append,
    )

    assert "electronic-circuit" in remedy
    assert any("GATE REFRESH" in message for message in messages)
    assert submitted and submitted[0][0] == "bootstrap_loan_electronic-circuit"
    gate_actions = [
        action for phase in submitted[0][1]["phases"]
        for action in phase["actions"]
        if "logistic_condition" in action
    ]
    assert gate_actions, "the refresh must re-apply the machine gate"
    assert builder._LOAN_GATE_REFRESH_ATTEMPTS == {
        (loan.group, "electronic-circuit", 18): 1,
    }


def test_ignored_gate_refreshes_eventually_raise(monkeypatch) -> None:
    """A step that ignores repeated refreshes is genuinely stuck."""
    loan = _stalled_gate_loan(monkeypatch)
    messages: list[str] = []
    monkeypatch.setattr(builder, "_submit", lambda *_a, **_k: None)

    for _ in range(3):
        builder._submit_bootstrap_loan(
            object(), object(), "nauvis", "player", loan, messages.append,
        )
    with pytest.raises(builder.StuckError) as failure:
        builder._submit_bootstrap_loan(
            object(), object(), "nauvis", "player", loan, messages.append,
        )

    assert failure.value.code == "mall_loan_gate_mismatch"


def _starved_gate_loan(monkeypatch):
    """The 2026-09-11 +1899s shape: an AM2 loan's e-circuit step (target 3)
    sits disabled with cable net 4, requester 0, crafts 0/2."""
    from orchestrator.mall_bootstrap import MallBootstrapLoan, MallBootstrapStep
    loan = MallBootstrapLoan(
        original_recipe="iron-gear-wheel",
        target_item="assembling-machine-2", target_count=1, side="left",
        requester_position=(39.5, 32.5),
        current_recipe="electronic-circuit",
        step_recipe="electronic-circuit",
        step_target_count=3,
        step_baseline_finished=862,
        step_required_crafts=2,
        step_minimum_crafts=2,
    )
    stock = {"electronic-circuit": 1, "copper-cable": 4, "iron-plate": 956}
    monkeypatch.setattr(builder, "_LOAN_GATE_REFRESH_ATTEMPTS", {})
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: (dict(stock), dict(stock)),
    )
    monkeypatch.setattr(
        builder, "next_bootstrap_step",
        lambda *_a, **_k: MallBootstrapStep("electronic-circuit", 3, 2),
    )
    monkeypatch.setattr(
        builder.live_base, "progress_counters",
        lambda *_a, **_k: {(36.5, 32.5): 862000},
    )
    monkeypatch.setattr(
        builder.live_base, "entity_status_name",
        lambda *_a: "disabled_by_control_behavior",
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_persisted_step", lambda *_a: None,
    )
    monkeypatch.setattr(builder, "_deliver_cell_ingredients", lambda *_a: False)
    monkeypatch.setattr(builder, "consume_wait", lambda *_a: None)
    monkeypatch.setattr(builder.time, "sleep", lambda *_a: None)
    # Nothing deliverable: providers drained, cell requester empty.
    monkeypatch.setattr(
        builder.live_base, "transferable_items", lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        builder.live_base, "chest_contents", lambda *_a, **_k: {},
    )
    return loan


def test_starved_loan_gate_waits_without_refresh_budget(monkeypatch) -> None:
    """2026-09-11: a starved AM2 loan step burned 3 no-op gate refreshes
    (cable net 4, requester 0, crafts 0/2) and ended the run. Starvation
    must wait for supply without consuming refresh budget or raising."""
    loan = _starved_gate_loan(monkeypatch)
    messages: list[str] = []
    monkeypatch.setattr(builder, "_submit", lambda *_a, **_k: None)

    for _ in range(5):
        remedy = builder._submit_bootstrap_loan(
            object(), object(), "nauvis", "player", loan, messages.append,
        )

    assert "waits for" in remedy
    assert any("SUPPLY WAIT" in message for message in messages)
    assert builder._LOAN_GATE_REFRESH_ATTEMPTS == {}


def test_half_without_provider_chest_is_not_borrowed(monkeypatch) -> None:
    """2026-09-04: a loan on the provider-less half of a shared cell died at
    submit with configure_target_missing. Only complete halves borrow."""
    line = SimpleNamespace(
        machine_count=2, machine_positions=[(42.5, 32.5), (53.5, 32.5)],
    )
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda _c, _s, _f, recipe, _m, **_k: (
            line if recipe == "iron-gear-wheel" else None
        ),
    )
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(
        builder, "locate_mall_cell", lambda *_a: ((35, 31), "right"),
    )
    monkeypatch.setattr(
        builder, "mall_slot_uses_shared_provider", lambda *_a: True,
    )
    def _entity(_client, _surface, position):
        if tuple(position) == (39.5, 32.5):
            return {"name": "requester-chest"}
        return {"name": "assembling-machine-1"}
    monkeypatch.setattr(builder.live_base, "entity_at", _entity)

    borrowed = builder._borrow_free_mall_cell(
        object(), "nauvis", "player", "transport-belt", (3.0, -1.0),
        allowed_original_recipes=None, allow_shared_provider=False,
        require_stocked_original=False, excluded_origins=set(),
    )

    assert borrowed is None


def _reclaim_world(monkeypatch, *, pool_full=True, donor_stock=6,
                   working=0):
    """Full pre-plastic pool with one idle spent drill cell."""
    monkeypatch.setitem(builder.LINE_RECIPES, "electric-mining-drill", {
        "machine": "assembling-machine-1",
        "ingredients": ["iron-plate", "iron-gear-wheel", "electronic-circuit"],
        "amounts": [10, 5, 3], "product_amount": 1, "craft_time": 2.0,
    })
    monkeypatch.setattr(
        builder, "mall_slot_count",
        lambda *_a: 8 if pool_full else 7,
    )
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock", lambda *_a: {
            "electric-mining-drill": donor_stock,
            "electronic-circuit": 0,
        },
    )
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())
    drill = SimpleNamespace(machine_count=1, working_count=working,
                            machine_positions=[(47.5, 38.5)])
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda _c, _s, _f, recipe, _m, **_k: (
            drill if recipe == "electric-mining-drill" else None
        ),
    )
    monkeypatch.setattr(
        builder, "locate_mall_cell", lambda *_a: ((46, 37), "left"),
    )
    def _entity(_client, _surface, position):
        if tuple(position) == (47.5, 38.5):
            return {"name": "assembling-machine-1"}
        if tuple(position) == (50.5, 38.5):
            return {"name": "requester-chest"}
        if tuple(position) == (50.5, 37.5):
            return {"name": "passive-provider-chest"}
        return None
    monkeypatch.setattr(builder.live_base, "entity_at", _entity)
    monkeypatch.setattr(
        builder, "_paired_mall_provider", lambda *_a: (50.5, 37.5),
    )
    monkeypatch.setattr(
        builder, "mall_slot_uses_shared_provider", lambda *_a: True,
    )


def test_capped_prep_reclaims_a_spent_demand_cell(monkeypatch) -> None:
    """2026-09-04: circuit prep blocked at 8/8 while a drill cell sat idle
    with its demand met. The cell converts in place to the standing recipe."""
    _reclaim_world(monkeypatch)
    submitted: list[dict] = []
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, name, _e: submitted.append(plan),
    )

    reclaimed = builder._reclaim_spent_demand_slot_for_prep(
        object(), object(), "nauvis", "player", "electronic-circuit",
        (3.0, -1.0), lambda _message: None, {}, minimum_machines=1,
    )

    assert reclaimed is True
    assert len(submitted) == 1
    actions = submitted[0]["phases"][0]["actions"]
    by_entity = {action["entity"]: action for action in actions}
    assert by_entity["assembling-machine-1"]["recipe"] == "electronic-circuit"
    assert by_entity["requester-chest"]["logistic_sections"][0]["group"] == (
        "mall:electronic-circuit:left"
    )
    cleared = by_entity["requester-chest"]["clear_logistic_groups"]
    assert "mall:electric-mining-drill:left" in cleared


def test_capped_demand_reclaim_uses_the_actual_bill_target(monkeypatch) -> None:
    """Reusing a slot for four sticks must gate on four, not one machine."""
    _reclaim_world(monkeypatch)
    submitted: list[dict] = []
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, _name, _e: submitted.append(plan),
    )

    assert builder._reclaim_spent_demand_slot_for_prep(
        object(), object(), "nauvis", "player", "iron-stick",
        (3.0, -1.0), lambda _message: None, {}, minimum_machines=1,
        stock_target=4,
    )

    machine = next(
        action for action in submitted[0]["phases"][0]["actions"]
        if action["entity"] == "assembling-machine-1"
    )
    assert machine["recipe"] == "iron-stick"
    assert machine["logistic_condition"]["constant"] == 4


def test_reclaim_skips_rooms_and_busy_cells(monkeypatch) -> None:
    """Room in the pool, queued demand, or a working donor: no reclaim."""
    _reclaim_world(monkeypatch, pool_full=False)
    monkeypatch.setattr(builder, "_submit", lambda *_a, **_k: pytest.fail(
        "room in the pool builds normally"))
    assert builder._reclaim_spent_demand_slot_for_prep(
        object(), object(), "nauvis", "player", "electronic-circuit",
        (3.0, -1.0), lambda _message: None, {}, minimum_machines=1,
    ) is False

    _reclaim_world(monkeypatch, working=1)
    calls: list[str] = []
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, name, _e: calls.append(name),
    )
    assert builder._reclaim_spent_demand_slot_for_prep(
        object(), object(), "nauvis", "player", "electronic-circuit",
        (3.0, -1.0), lambda _message: None, {}, minimum_machines=1,
    ) is False
    assert calls == []

    _reclaim_world(monkeypatch)
    assert builder._reclaim_spent_demand_slot_for_prep(
        object(), object(), "nauvis", "player", "electronic-circuit",
        (3.0, -1.0), lambda _message: None,
        {"electric-mining-drill": 6}, minimum_machines=1,
    ) is False
    assert calls == []


def test_prep_hand_off_slot_cap_to_reclaim(monkeypatch) -> None:
    """A capped standing prep reclaims instead of spinning on the cap."""
    monkeypatch.setattr(
        builder, "baseline_build_order", lambda: ["electronic-circuit"],
    )
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
    def _capped(*_args: object, **_kwargs: object) -> None:
        raise builder.ProductionPrerequisiteDeferred(
            "bootstrap mall is capped at 8 assemblers",
            code="bootstrap_mall_slot_cap", state="supply_wait",
            details={},
        )
    monkeypatch.setattr(builder, "ensure_produced", _capped)
    monkeypatch.setattr(
        builder, "_reclaim_spent_demand_slot_for_prep", lambda *_a, **_k: True,
    )

    spent = builder._prep_intermediate(
        object(), object(), "nauvis", "player", set(), {}, (0.0, 0.0),
        lambda _message: None,
    )

    assert spent is True


def test_prep_ignores_reclaim_for_other_gates(monkeypatch) -> None:
    """Non-cap deferrals keep their original path."""
    monkeypatch.setattr(
        builder, "baseline_build_order", lambda: ["electronic-circuit"],
    )
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
    def _gated(*_args: object, **_kwargs: object) -> None:
        raise builder.ProductionPrerequisiteDeferred(
            "waiting on furnaces", code="electric_furnace_supply_wait",
            state="supply_wait", details={},
        )
    monkeypatch.setattr(builder, "ensure_produced", _gated)
    monkeypatch.setattr(
        builder, "_reclaim_spent_demand_slot_for_prep",
        lambda *_a, **_k: pytest.fail("only the cap triggers reclaim"),
    )

    spent = builder._prep_intermediate(
        object(), object(), "nauvis", "player", set(), {}, (0.0, 0.0),
        lambda _message: None,
    )

    assert spent is False


def _proof_loan():
    from orchestrator.mall_bootstrap import MallBootstrapLoan
    return MallBootstrapLoan(
        original_recipe="copper-cable",
        target_item="electronic-circuit", target_count=200, side="left",
        requester_position=(39.5, 32.5),
        current_recipe="electronic-circuit",
        step_recipe="electronic-circuit",
        step_target_count=200,
        step_baseline_finished=0,
        step_required_crafts=7,
        step_minimum_crafts=7,
    )


def _proof_world(monkeypatch, tmp_path, *, products: int):
    from orchestrator.priority_list import PriorityList
    loan = _proof_loan()
    stock = {"electronic-circuit": 42, "copper-cable": 60, "iron-plate": 100}
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock", lambda *_a: dict(stock),
    )
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 1000)
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: [loan])
    monkeypatch.setattr(
        builder.live_base, "progress_counters",
        lambda *_a, **_k: {(36.5, 32.5): products * 1000},
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_completion_target", lambda *_a: 9999,
    )
    monkeypatch.setattr(builder, "_evergreen_producer_live", lambda *_a: True)
    monkeypatch.setattr(builder, "_CRAFT_PROOF_CONSUMED", {})
    return PriorityList(tmp_path / "priorities.json", 1000)


def test_stale_craft_proof_does_not_pop_a_requeued_demand(
    monkeypatch, tmp_path,
) -> None:
    """2026-09-04: a lingering spare-phase loan re-proved the post-metal
    circuit reserve every pass, hiding the task that would service it. The
    same counters may only retire a bill once; new crafts re-prove."""
    priorities = _proof_world(monkeypatch, tmp_path, products=10)
    mall_targets = {"electronic-circuit": 200}

    _, task = builder._survey_pass(
        object(), "nauvis", "player", mall_targets, priorities,
    )

    assert mall_targets == {}
    assert priorities.items["electronic-circuit"].status == "complete"

    mall_targets["electronic-circuit"] = 200
    _, task = builder._survey_pass(
        object(), "nauvis", "player", mall_targets, priorities,
    )

    assert mall_targets == {"electronic-circuit": 200}
    assert priorities.items["electronic-circuit"].status != "complete"
    assert task is not None and task.item == "electronic-circuit"


def test_further_crafts_reprove_after_consumption(
    monkeypatch, tmp_path,
) -> None:
    """Genuinely new production past the consumed baseline retires again."""
    priorities = _proof_world(monkeypatch, tmp_path, products=10)
    mall_targets = {"electronic-circuit": 200}
    builder._survey_pass(object(), "nauvis", "player", mall_targets, priorities)
    assert builder._CRAFT_PROOF_CONSUMED == {"electronic-circuit": 42}

    mall_targets["electronic-circuit"] = 200
    monkeypatch.setattr(
        builder.live_base, "progress_counters",
        lambda *_a, **_k: {(36.5, 32.5): 250 * 1000},
    )
    builder._survey_pass(object(), "nauvis", "player", mall_targets, priorities)

    assert mall_targets == {}
    assert builder._CRAFT_PROOF_CONSUMED == {"electronic-circuit": 250}


def test_serve_recomputes_after_late_requeue(monkeypatch, tmp_path) -> None:
    """2026-09-04: reserves re-queue after the survey ran, and serve slept
    on the survey's stale None. A freshly ready task is served."""
    from orchestrator.priority_list import PriorityList
    priorities = PriorityList(tmp_path / "priorities.json", 1000)
    priorities.sync({"inserter": 12}, {"inserter": 0}, 1000)
    served: list[str] = []
    monkeypatch.setattr(
        builder, "_serve_mall_task",
        lambda _c, _b, _s, _f, task, _t, _m, _p, _r, _e: served.append(task.item),
    )
    monkeypatch.setattr(builder, "consume_wait", lambda *_a: None)
    slept: list[float] = []
    monkeypatch.setattr(builder.time, "sleep", slept.append)

    result = builder._serve_ready_pass(
        object(), object(), "nauvis", "player", None, 1000,
        {"inserter": 12}, {}, priorities, (3.0, -1.0),
        "automation-science-pack", lambda _message: None,
    )

    assert served == ["inserter"]
    assert slept == []
    assert result is builder._SHORTAGE


def _feeder_harness(monkeypatch, *, waiter_flowing_producer=True,
                    waiter_stock=0, holder_minimum_met=False):
    """A splitter loan eating circuits; the circuit waiter starves."""
    from orchestrator.mall_bootstrap import MallBootstrapLoan
    monkeypatch.setitem(builder.LINE_RECIPES, "splitter", {
        "machine": "assembling-machine-1",
        "ingredients": ["iron-plate", "electronic-circuit"],
        "amounts": [1, 2], "product_amount": 1, "craft_time": 0.5,
    })
    loan = MallBootstrapLoan(
        original_recipe="iron-gear-wheel",
        target_item="splitter", target_count=50, side="left",
        requester_position=(39.5, 32.5),
        current_recipe="splitter",
        step_recipe="splitter",
        step_baseline_finished=580,
        step_required_crafts=50,
        step_minimum_crafts=50,
    )
    monkeypatch.setattr(
        builder, "active_bootstrap_loans", lambda *_a: [loan],
    )
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock",
        lambda *_a: {"electronic-circuit": waiter_stock},
    )
    monkeypatch.setattr(
        builder, "_production_started",
        lambda _c, _s, _f, item: (
            False if item == "electronic-circuit"
            else waiter_flowing_producer
        ),
    )
    monkeypatch.setattr(
        builder.live_base, "progress_counters",
        lambda *_a, **_k: {(36.5, 32.5): (
            (580 + 50) * 1000 if holder_minimum_met else 585 * 1000
        )},
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: ({"splitter": 0}, {}),
    )
    return loan


def _feeder_start(monkeypatch, borrow):
    restored: list[str] = []
    started: list[str] = []
    monkeypatch.setattr(builder, "_borrow_free_mall_cell", borrow)
    monkeypatch.setattr(
        builder, "_restore_bootstrap_loan",
        lambda *_a, **_k: restored.append(_a[4].target_item),
    )
    def _fake_submit(*args: object, **kwargs: object) -> str:
        if kwargs.get("preempt_for") is not None:
            return "handoff"
        started.append(str(args[4].target_item))
        return "started"
    monkeypatch.setattr(builder, "_submit_bootstrap_loan", _fake_submit)
    remedy = builder._start_bootstrap_loan(
        object(), object(), "nauvis", "player", "electronic-circuit", 235,
        (3.0, -1.0), lambda _message: None,
    )
    return restored, started, remedy


def test_feeder_waiter_preempts_its_consumer(monkeypatch) -> None:
    """2026-09-04: a splitter loan ate every circuit while the circuit batch
    waited 300s for a cell, then both stalled. The holder yields so its
    feeder runs first."""
    loan = _feeder_harness(monkeypatch)
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: [loan])
    calls = {"n": 0}
    def _borrow(*_args: object, **_kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return ("iron-gear-wheel", (36.5, 32.5), (35, 31), "left",
                "assembling-machine-1")
    restored, started, remedy = _feeder_start(monkeypatch, _borrow)

    assert restored == ["splitter"]
    assert started == ["electronic-circuit"]
    assert remedy == "started"


def test_feeder_preempt_names_the_fed_step(monkeypatch) -> None:
    """The restore reason distinguishes feeding from unblocking."""
    messages: list[str] = []
    loan = _feeder_harness(monkeypatch)
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: [loan])
    calls = {"n": 0}
    def _borrow(*_args: object, **_kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return ("iron-gear-wheel", (36.5, 32.5), (35, 31), "left",
                "assembling-machine-1")
    monkeypatch.setattr(builder, "_borrow_free_mall_cell", _borrow)
    monkeypatch.setattr(
        builder, "_restore_bootstrap_loan",
        lambda *_a, **_k: messages.append(_k["reason"]),
    )
    monkeypatch.setattr(
        builder, "_submit_bootstrap_loan", lambda *_a, **_k: "started",
    )

    builder._start_bootstrap_loan(
        object(), object(), "nauvis", "player", "electronic-circuit", 235,
        (3.0, -1.0), lambda _message: None,
    )

    assert any("which feeds its splitter step" in message for message in messages)


def test_feeder_waits_when_circuits_flow_elsewhere(monkeypatch) -> None:
    """A live circuit producer means normal handoff, not preemption."""
    _feeder_harness(monkeypatch, waiter_flowing_producer=True)
    monkeypatch.setattr(
        builder, "_production_started", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder, "_borrow_free_mall_cell", lambda *_a, **_k: None,
    )
    restored: list[str] = []
    monkeypatch.setattr(
        builder, "_restore_bootstrap_loan",
        lambda *_a, **_k: restored.append(_a[4].target_item),
    )
    monkeypatch.setattr(
        builder, "_submit_bootstrap_loan", lambda *_a, **_k: "handoff",
    )

    remedy = builder._start_bootstrap_loan(
        object(), object(), "nauvis", "player", "electronic-circuit", 235,
        (3.0, -1.0), lambda _message: None,
    )

    assert restored == []
    assert remedy == "handoff"


def test_fulfilled_holder_keeps_spare_preempt_path(monkeypatch) -> None:
    """Minimum-met holders stay on the normal spare path, not the yield."""
    _feeder_harness(monkeypatch, holder_minimum_met=True)
    monkeypatch.setattr(
        builder, "_borrow_free_mall_cell", lambda *_a, **_k: None,
    )
    restored: list[str] = []
    monkeypatch.setattr(
        builder, "_restore_bootstrap_loan",
        lambda *_a, **_k: restored.append(_a[4].target_item),
    )
    monkeypatch.setattr(
        builder, "_submit_bootstrap_loan", lambda *_a, **_k: "handoff",
    )

    remedy = builder._start_bootstrap_loan(
        object(), object(), "nauvis", "player", "electronic-circuit", 235,
        (3.0, -1.0), lambda _message: None,
    )

    assert restored == []
    assert remedy == "handoff"


def test_unrelated_waiter_does_not_preempt(monkeypatch) -> None:
    """Drills do not feed splitters: ordinary handoff applies."""
    _feeder_harness(monkeypatch)
    monkeypatch.setattr(
        builder, "_borrow_free_mall_cell", lambda *_a, **_k: None,
    )
    restored: list[str] = []
    monkeypatch.setattr(
        builder, "_restore_bootstrap_loan",
        lambda *_a, **_k: restored.append(_a[4].target_item),
    )
    monkeypatch.setattr(
        builder, "_submit_bootstrap_loan", lambda *_a, **_k: "handoff",
    )

    remedy = builder._start_bootstrap_loan(
        object(), object(), "nauvis", "player", "electric-mining-drill", 6,
        (3.0, -1.0), lambda _message: None,
    )

    assert restored == []
    assert remedy == "handoff"


def test_feeder_preempt_overrides_the_binding_shield(monkeypatch) -> None:
    """2026-09-04: the splitter loan was foundation-binding and starved the
    circuits it needed; the shield protected it from its own feeder and both
    stalled. Feeding serves the binding bill, so it preempts anyway. (A
    stockpiling waiter never qualifies, so the original drills case cannot
    recur.)"""
    loan = _feeder_harness(monkeypatch)
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: [loan])
    monkeypatch.setattr(builder, "_BLOCKING_MALL_ITEMS", {"splitter"})
    calls = {"n": 0}
    def _borrow(*_args: object, **_kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return ("iron-gear-wheel", (36.5, 32.5), (35, 31), "left",
                "assembling-machine-1")
    restored, started, remedy = _feeder_start(monkeypatch, _borrow)

    assert restored == ["splitter"]
    assert started == ["electronic-circuit"]
    assert remedy == "started"


def _prereq_harness(monkeypatch):
    """An AM2 loan restored every pass for steel that is never built."""
    from orchestrator.mall_bootstrap import MallBootstrapLoan
    from orchestrator.parts_mall import MaterialShortage
    monkeypatch.setitem(builder.LINE_RECIPES, "assembling-machine-2", {
        "machine": "assembling-machine-1",
        "ingredients": ["steel-plate", "electronic-circuit", "iron-gear-wheel"],
        "amounts": [2, 3, 5], "product_amount": 1, "craft_time": 0.5,
    })
    loan = MallBootstrapLoan(
        original_recipe="iron-gear-wheel",
        target_item="assembling-machine-2", target_count=2, side="left",
        requester_position=(39.5, 32.5),
        current_recipe="assembling-machine-2",
    )
    stock = {"steel-plate": 0, "electronic-circuit": 50,
             "iron-gear-wheel": 50, "assembling-machine-2": 0}
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: [loan])
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock", lambda *_a: dict(stock),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock", lambda *_a: (dict(stock), dict(stock)),
    )
    monkeypatch.setattr(
        builder, "_production_started",
        lambda _c, _s, _f, item: item != "steel-plate",
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_completion_target", lambda *_a: 999,
    )
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    return loan, MaterialShortage


def test_restore_also_establishes_the_prerequisite(monkeypatch) -> None:
    """2026-09-04: an AM2 loan borrowed and restored in the same breath for
    300s while steel was never built. Restore, establish, then defer."""
    loan, _Shortage = _prereq_harness(monkeypatch)
    restored: list[str] = []
    established: list[tuple[str, int]] = []
    monkeypatch.setattr(
        builder, "_restore_bootstrap_loan",
        lambda *_a, **_k: restored.append(_a[4].target_item),
    )
    def _establish(*args: object, **kwargs: object) -> None:
        established.append((str(args[4]), int(kwargs["stock_target"])))
    monkeypatch.setattr(builder, "ensure_produced", _establish)

    with pytest.raises(builder.ProductionPrerequisiteDeferred) as deferred:
        builder._rationed_mall_batch(
            object(), object(), "nauvis", "player", "assembling-machine-2", 2,
            (0.0, 0.0), lambda _message: None,
        )

    assert restored == ["assembling-machine-2"]
    assert established == [("steel-plate", 4)]
    assert deferred.value.code == "rotating_mall_prerequisite_handoff"


def test_prerequisite_shortage_still_propagates(monkeypatch) -> None:
    """If establishing fails on materials, the shortage (not a silent pass)
    reaches the caller that queues it."""
    from orchestrator.parts_mall import MaterialShortage
    loan, _Shortage = _prereq_harness(monkeypatch)
    monkeypatch.setattr(
        builder, "_restore_bootstrap_loan", lambda *_a, **_k: None,
    )
    def _short(*_args: object, **_kwargs: object) -> None:
        raise MaterialShortage("steel_starter", {"iron-plate": 9}, {})
    monkeypatch.setattr(builder, "ensure_produced", _short)

    with pytest.raises(MaterialShortage) as failure:
        builder._rationed_mall_batch(
            object(), object(), "nauvis", "player", "assembling-machine-2", 2,
            (0.0, 0.0), lambda _message: None,
        )

    assert failure.value.required == {"iron-plate": 9}


def _restore_harness(monkeypatch, *, shared: bool):
    from orchestrator.mall_bootstrap import MallBootstrapLoan
    loan = MallBootstrapLoan(
        original_recipe="iron-gear-wheel", target_item="splitter",
        target_count=3, side="left",
        requester_position=(39.5, 32.5), current_recipe="splitter",
    )
    monkeypatch.setattr(
        builder, "mall_slot_uses_shared_provider", lambda *_a: shared,
    )
    monkeypatch.setattr(builder, "_MALL_ITEM_PROVIDER_LIMITS", {
        ("nauvis", "player", "iron-gear-wheel"): 240,
    })
    submitted: list[dict] = []
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, _name, _e: submitted.append(plan),
    )
    return loan, submitted


def test_restore_keeps_shared_provider_open(monkeypatch) -> None:
    """Shared chests must not be re-barred behind another recipe's stacks."""
    loan, submitted = _restore_harness(monkeypatch, shared=True)

    builder._restore_bootstrap_loan(
        object(), object(), "nauvis", "player", loan,
        lambda _message: None, reason="test",
        reference_point=(3.0, -1.0),
    )

    providers = [
        action for phase in submitted[0]["phases"]
        for action in phase["actions"]
        if action.get("entity") == "passive-provider-chest"
    ]
    assert providers and all(
        action["inventory_limit"].get("fill_chest") for action in providers
    )


def test_restore_applies_canonical_to_dedicated(monkeypatch) -> None:
    """Dedicated twins converge on the canonical count at restore."""
    loan, submitted = _restore_harness(monkeypatch, shared=False)

    builder._restore_bootstrap_loan(
        object(), object(), "nauvis", "player", loan,
        lambda _message: None, reason="test",
        reference_point=(3.0, -1.0),
    )

    counts = _provider_counts(submitted[0])
    assert counts and all(count == 240 for count in counts)


def test_chemical_handoff_parks_on_a_retry_horizon(monkeypatch) -> None:
    """2026-09-04: bulk-inserters borrowed and restored the same cell every
    pass while plastic established, tripping the livelock guard on a hot
    spin. The handoff now parks the target for 60s of other work."""
    from orchestrator.priority_list import PriorityList
    monkeypatch.setattr(
        builder, "baseline_build_order", lambda: ["electronic-circuit"],
    )
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
    def _handoff(*_args: object, **_kwargs: object) -> None:
        raise builder.ProductionPrerequisiteDeferred(
            "chemical ladder handoff restored bulk-inserter",
            code="chemical_capability_handoff", state="supply_wait",
            details={"target": "bulk-inserter", "rung": "plastic-bar"},
        )
    monkeypatch.setattr(builder, "_ensure_mall_item", _handoff)
    monkeypatch.setattr(
        builder, "_production_started", lambda *_a: False,
    )
    advanced: list[str] = []
    monkeypatch.setattr(
        builder, "ensure_produced",
        lambda _c, _b, _s, _f, item, *_a, **_k:
        advanced.append(item) or None,
    )
    priorities = PriorityList.__new__(PriorityList)
    priorities.items = {}
    deferred: list[tuple[str, int]] = []
    monkeypatch.setattr(priorities, "describe", lambda *_a: "")
    monkeypatch.setattr(
        priorities, "defer",
        lambda item, tick, reason, **kwargs: deferred.append(
            (item, kwargs.get("retry_ticks", 0))),
    )
    task = type("Task", (), {"item": "bulk-inserter", "target": 1})()

    builder._serve_mall_task(
        object(), object(), "nauvis", "player", task, 1000,
        {"bulk-inserter": 1}, priorities, (0.0, 0.0),
        lambda _message: None,
    )

    assert advanced == ["plastic-bar"]
    assert deferred == [("bulk-inserter", 3600)]


def test_chemical_handoff_queues_the_rungs_construction_bill(monkeypatch) -> None:
    """A parked plastic handoff exposes oil equipment to the mall scheduler."""
    from orchestrator.priority_list import PriorityList

    def _handoff(*_args: object, **_kwargs: object) -> None:
        raise builder.ProductionPrerequisiteDeferred(
            "chemical ladder is establishing plastic-bar before bulk-inserter",
            code="chemical_capability_handoff", state="supply_wait",
            details={"target": "bulk-inserter", "rung": "plastic-bar"},
        )

    def _oil_bill(*_args: object, **_kwargs: object) -> None:
        raise builder.MaterialShortage(
            "oil_cell", {"chemical-plant": 2, "pumpjack": 1}, {},
        )

    monkeypatch.setattr(builder, "_ensure_mall_item", _handoff)
    monkeypatch.setattr(builder, "_production_started", lambda *_a: False)
    monkeypatch.setattr(builder, "ensure_produced", _oil_bill)
    priorities = PriorityList.__new__(PriorityList)
    priorities.items = {}
    monkeypatch.setattr(priorities, "describe", lambda *_a: "")
    monkeypatch.setattr(priorities, "defer", lambda *_a, **_k: None)
    task = type("Task", (), {"item": "bulk-inserter", "target": 1})()
    targets = {"bulk-inserter": 1}
    messages: list[str] = []

    builder._serve_mall_task(
        object(), object(), "nauvis", "player", task, 1000,
        targets, priorities, (0.0, 0.0), messages.append,
    )

    assert targets == {
        "bulk-inserter": 1, "chemical-plant": 2, "pumpjack": 1,
    }
    assert any("CHEMICAL HANDOFF DEMAND" in message for message in messages)


def test_chemical_handoff_with_flowing_rung_serves_normally(
    monkeypatch,
) -> None:
    """A rung that already produces means stale news, not a wait."""
    monkeypatch.setattr(
        builder, "baseline_build_order", lambda: ["electronic-circuit"],
    )
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
    def _handoff(*_args: object, **_kwargs: object) -> None:
        raise builder.ProductionPrerequisiteDeferred(
            "chemical ladder handoff restored bulk-inserter",
            code="chemical_capability_handoff", state="supply_wait",
            details={"target": "bulk-inserter", "rung": "plastic-bar"},
        )
    monkeypatch.setattr(builder, "_ensure_mall_item", _handoff)
    monkeypatch.setattr(builder, "_production_started", lambda *_a: True)
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock", lambda *_a: {},
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_completion_target", lambda *_a: 999,
    )
    monkeypatch.setattr(
        builder, "_queue_starved_loan_ingredients", lambda *_a: None,
    )
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(
        builder.live_base, "pending_ghost_count", lambda *_a: 0,
    )
    from orchestrator.priority_list import PriorityList
    priorities = PriorityList.__new__(PriorityList)
    priorities.items = {}
    monkeypatch.setattr(
        priorities, "defer",
        lambda *_a, **_k: pytest.fail("flowing rung must not defer"),
    )
    monkeypatch.setattr(priorities, "describe", lambda *_a: "")
    task = type("Task", (), {"item": "bulk-inserter", "target": 1})()

    builder._serve_mall_task(
        object(), object(), "nauvis", "player", task, 1000,
        {"bulk-inserter": 1}, priorities, (0.0, 0.0),
        lambda _message: None,
    )


def _post_core_world(monkeypatch):
    """All five core producers started, AM2s stocked, mall placed."""
    monkeypatch.setattr(builder, "_production_started", lambda *_a: True)
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"assembling-machine-2": 10, "fast-inserter": 10},
    )
    monkeypatch.setattr(
        builder.live_base, "transferable_items",
        lambda *_a: {"assembling-machine-2": 10, "fast-inserter": 10},
    )
    monkeypatch.setattr(
        builder, "mall_entity_positions",
        lambda _c, _s, _f, _r, source: [(10.0, 10.0), (20.0, 20.0)],
    )


def test_post_core_smoke_upgrades_retrofit_and_core_prep(
    monkeypatch, tmp_path,
) -> None:
    """Program gate: once the five core producers (incl. advanced circuits)
    run, upgrades order, retrofits pass, and core prep completes -- exercised
    here before any live run arrives."""
    from orchestrator import autonomous_builder as builder_module
    _post_core_world(monkeypatch)
    upgraded: list[dict] = []
    report_path = tmp_path / "upgrade_report.json"
    report_path.write_text('{"actions": [{"status": "success"}]}')

    class _Bridge:
        def execute_upgrade_plan(self, authorization, plan, **kwargs):
            upgraded.append({"authorization": authorization, "plan": plan})
            return str(report_path)

    assert builder_module._core_mall_ready(object(), "nauvis", "player") is True

    prepped: set[str] = set()
    assert builder_module._prep_core_mall(
        object(), _Bridge(), "nauvis", "player", prepped, {},
        (0.0, 0.0), lambda _message: None,
    ) is True
    assert any(key.startswith("_core_mall:") for key in prepped)

    assert builder_module._upgrade_bootstrap_mall(
        object(), _Bridge(), "nauvis", "player", {},
        (0.0, 0.0), lambda _message: None,
    ) is True
    assert upgraded, "expected native upgrade orders"
    assert upgraded[0]["authorization"]["scope_limits"]["max_count"] >= 1

    monkeypatch.setattr(
        builder_module, "next_shared_provider_retrofit_plan",
        lambda *_a: None,
    )
    assert builder_module._retrofit_bootstrap_mall_outputs(
        object(), _Bridge(), "nauvis", "player", {}, (0.0, 0.0),
        lambda _message: None,
    ) is False


def test_core_promotion_yields_to_goal_logistic_chest_demand(monkeypatch) -> None:
    """A requester/passive chest demand must open its chemical ladder first."""
    from orchestrator import autonomous_builder as builder_module

    monkeypatch.setattr(builder_module, "_BLOCKING_MALL_ITEMS", set())
    prepped: set[str] = set()
    targets = {"requester-chest": 3}

    assert builder_module._prep_core_mall(
        object(), object(), "nauvis", "player", prepped, targets,
        (0.0, 0.0), lambda _message: None,
    ) is False
    assert prepped == set()


def test_post_starter_announces_once_when_earned(monkeypatch) -> None:
    """Program gate: advanced circuits producing flips the phase once."""
    from orchestrator import autonomous_builder as builder_module
    monkeypatch.setattr(builder_module, "_POST_STARTER_TRANSITION_ANNOUNCED", False)
    monkeypatch.setattr(
        builder_module, "_production_started",
        lambda *_a: True,
    )
    messages: list[str] = []
    emit = messages.append

    assert builder_module._maybe_announce_post_starter(
        object(), "nauvis", "player", emit,
    ) is True
    assert any("POST-STARTER TRANSITION" in message for message in messages)
    assert builder_module._maybe_announce_post_starter(
        object(), "nauvis", "player", emit,
    ) is False
    assert len(messages) == 1


def test_post_starter_silent_before_advanced_circuits(monkeypatch) -> None:
    from orchestrator import autonomous_builder as builder_module
    monkeypatch.setattr(builder_module, "_POST_STARTER_TRANSITION_ANNOUNCED", False)
    monkeypatch.setattr(
        builder_module, "_production_started", lambda *_a: False,
    )
    messages: list[str] = []

    assert builder_module._maybe_announce_post_starter(
        object(), "nauvis", "player", messages.append,
    ) is False
    assert messages == []
    assert builder_module._post_starter_phase(object(), "nauvis", "player") is False


def _stale_credit_harness(monkeypatch):
    """A splitter loan with credited circuit prerequisites but zero current
    progress, blocked on circuits with no producer (2026-09-05 live state)."""
    from orchestrator.mall_bootstrap import MallBootstrapLoan
    monkeypatch.setitem(builder.LINE_RECIPES, "splitter", {
        "machine": "assembling-machine-1",
        "ingredients": ["iron-plate", "electronic-circuit"],
        "amounts": [1, 2], "product_amount": 1, "craft_time": 0.5,
    })
    return MallBootstrapLoan(
        original_recipe="iron-gear-wheel",
        target_item="splitter", target_count=3, side="left",
        requester_position=(39.5, 32.5),
        current_recipe="splitter",
        step_recipe="splitter",
        step_baseline_finished=100,
        step_required_crafts=3,
        step_minimum_crafts=3,
        completed_step_targets=(("electronic-circuit", 15),),
    )


def test_stale_prerequisite_credit_does_not_shield_a_paralyzed_loan(monkeypatch) -> None:
    """2026-09-05: splitter/fast-inserter loans with credited circuit
    prerequisites sat at zero current progress with no circuit producer;
    the credit blocked every yield until the run died. Only current-step
    progress shields."""
    loan = _stale_credit_harness(monkeypatch)
    monkeypatch.setattr(builder, "_recipe_inputs_flowing", lambda *_a: True)
    monkeypatch.setattr(
        builder, "_binding_loan_shields_preempt", lambda *_a: False,
    )
    monkeypatch.setattr(
        builder, "_loan_blocked_inputs", lambda *_a: ["electronic-circuit"],
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 100,
    )

    assert builder._blocked_loan_to_yield(
        object(), "nauvis", "player", [loan], "transport-belt",
    ) is loan


def test_current_step_progress_still_shields_yield(monkeypatch) -> None:
    """A loan advancing its current step keeps its cell even with stale
    prerequisite credit elsewhere."""
    loan = _stale_credit_harness(monkeypatch)
    monkeypatch.setattr(builder, "_recipe_inputs_flowing", lambda *_a: True)
    monkeypatch.setattr(
        builder, "_binding_loan_shields_preempt", lambda *_a: False,
    )
    monkeypatch.setattr(
        builder, "_loan_blocked_inputs", lambda *_a: ["electronic-circuit"],
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 101,
    )

    assert builder._blocked_loan_to_yield(
        object(), "nauvis", "player", [loan], "transport-belt",
    ) is None


def test_progress_yields_when_holder_is_blocked_behind_its_waiter(monkeypatch) -> None:
    """2026-09-07 (cycle 7): an AM2 loan at 1/2 crafts on missing steel held
    its cell while steel admission waited on the pipe rung, and the pipe loan
    needed the AM2 loan's own cell. Partial progress can never become
    completion without the waiter, so the holder yields despite progress and
    binding status -- yielding sequences the dependency, like the feeder
    path, instead of time-slicing."""
    from orchestrator.mall_bootstrap import MallBootstrapLoan
    loan = MallBootstrapLoan(
        original_recipe="iron-gear-wheel",
        target_item="assembling-machine-2", target_count=2, side="left",
        requester_position=(39.5, 32.5),
        current_recipe="assembling-machine-2",
        step_recipe="assembling-machine-2",
        step_baseline_finished=100,
        step_required_crafts=2,
        step_minimum_crafts=2,
    )
    monkeypatch.setattr(builder, "_recipe_inputs_flowing", lambda *_a: True)
    monkeypatch.setattr(
        builder, "_binding_loan_shields_preempt", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder, "_loan_blocked_inputs", lambda *_a: ["steel-plate"],
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 101,
    )
    monkeypatch.setattr(
        builder, "_missing_chemical_ladder_predecessor",
        lambda _client, _surface, _force, item: (
            "pipe" if item == "steel-plate" else None
        ),
    )

    assert builder._blocked_loan_to_yield(
        object(), "nauvis", "player", [loan], "pipe",
    ) is loan


def test_feeder_fires_despite_completed_prerequisite_steps(monkeypatch) -> None:
    """Same stale-credit shape through the feeder path: the holder yields
    so its feeder runs first."""
    loan = _feeder_harness(monkeypatch)
    from orchestrator.mall_bootstrap import MallBootstrapLoan
    loan = MallBootstrapLoan(
        original_recipe=loan.original_recipe,
        target_item=loan.target_item, target_count=loan.target_count,
        side=loan.side, requester_position=loan.requester_position,
        current_recipe=loan.current_recipe, step_recipe=loan.step_recipe,
        step_baseline_finished=loan.step_baseline_finished,
        step_required_crafts=loan.step_required_crafts,
        step_minimum_crafts=loan.step_minimum_crafts,
        completed_step_targets=(("electronic-circuit", 15),),
    )
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: [loan])
    calls = {"n": 0}
    def _borrow(*_args: object, **_kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return ("iron-gear-wheel", (36.5, 32.5), (35, 31), "left",
                "assembling-machine-1")
    restored, started, remedy = _feeder_start(monkeypatch, _borrow)

    assert restored == ["splitter"]
    assert started == ["electronic-circuit"]
    assert remedy == "started"


def test_handoff_waiter_queues_its_holders_starving_ingredient(monkeypatch) -> None:
    """2026-09-05: inserter waited on handoff (no loan of its own) while its
    holders' circuits had no producer and no demand; rotation froze until
    the guard fired. The holder's orphan still gets demanded."""
    _feeder_harness(monkeypatch)  # splitter loan starved on circuits
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 0)
    targets: dict[str, int] = {}
    promoted: list[str] = []
    priorities = SimpleNamespace(
        promote=lambda item, _target, _tick: promoted.append(item),
    )
    messages: list[str] = []

    builder._queue_starved_loan_ingredients(
        object(), "nauvis", "player", "transport-belt", 148,
        targets, priorities, messages.append,
    )

    assert set(targets) == {"electronic-circuit"}
    assert promoted == ["electronic-circuit"]
    assert any(
        "splitter loan waits on electronic-circuit" in message
        for message in messages
    )


def test_handoff_scan_stays_quiet_when_holders_are_fed(monkeypatch) -> None:
    """The fallback scans holders but queues nothing they do not need."""
    _feeder_harness(monkeypatch, waiter_stock=5)
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 0)
    targets: dict[str, int] = {}
    priorities = SimpleNamespace(promote=lambda *_a: None)
    messages: list[str] = []

    builder._queue_starved_loan_ingredients(
        object(), "nauvis", "player", "transport-belt", 148,
        targets, priorities, messages.append,
    )

    assert targets == {}
    assert messages == []


def _ladder_recipes(monkeypatch) -> None:
    """Bulk needs advanced circuits; advanced needs plastic (live 2.x bills)."""
    monkeypatch.setitem(builder.LINE_RECIPES, "bulk-inserter", {
        "machine": "assembling-machine-1",
        "ingredients": ["iron-gear-wheel", "electronic-circuit",
                        "advanced-circuit", "fast-inserter"],
        "amounts": [15, 15, 1, 1], "product_amount": 1, "craft_time": 8.0,
    })
    monkeypatch.setitem(builder.LINE_RECIPES, "advanced-circuit", {
        "machine": "assembling-machine-1",
        "ingredients": ["plastic-bar", "copper-cable", "electronic-circuit"],
        "amounts": [2, 4, 2], "product_amount": 1, "craft_time": 6.0,
    })


def test_unfundable_batch_parks_on_its_missing_rung(monkeypatch) -> None:
    """2026-09-05: bulk-inserter spun 500s of borrow/restore against
    advanced circuits while plastic-bar was not even sited. The batch parks
    on the rung instead of churning."""
    _ladder_recipes(monkeypatch)
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock", lambda *_a: {},
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_completion_target", lambda *_a: 999,
    )
    monkeypatch.setattr(
        builder, "_chemical_capability_started", lambda *_a: False,
    )

    with pytest.raises(builder.ProductionPrerequisiteDeferred) as parked:
        builder._rationed_mall_batch(
            object(), object(), "nauvis", "player", "bulk-inserter", 1,
            (0.0, 0.0), lambda _message: None,
        )

    assert parked.value.code == "chemical_capability_handoff"
    assert parked.value.details["rung"] == "plastic-bar"
    assert parked.value.details["target"] == "bulk-inserter"


def test_ladder_rungs_establish_themselves(monkeypatch) -> None:
    """Rungs are never gated (they ARE the establishment path)."""
    _ladder_recipes(monkeypatch)
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock", lambda *_a: {},
    )
    monkeypatch.setattr(
        builder, "_chemical_capability_started", lambda *_a: False,
    )

    assert builder._unfunded_ladder_ingredient(
        object(), "nauvis", "player", "plastic-bar",
    ) is None
    assert builder._unfunded_ladder_ingredient(
        object(), "nauvis", "player", "advanced-circuit",
    ) is None


def test_stocked_chemicals_do_not_replace_production_capability(monkeypatch) -> None:
    """Stock alone must not admit a batch before its chemical producers."""
    _ladder_recipes(monkeypatch)
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock",
        lambda *_a: {"advanced-circuit": 5, "plastic-bar": 5},
    )
    monkeypatch.setattr(
        builder, "_chemical_capability_started", lambda *_a: False,
    )

    assert builder._unfunded_ladder_ingredient(
        object(), "nauvis", "player", "bulk-inserter",
    ) == "plastic-bar"


def test_flowing_ladder_never_parks(monkeypatch) -> None:
    """Started rungs admit the batch; unknown production defers safely."""
    _ladder_recipes(monkeypatch)
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock", lambda *_a: {},
    )
    monkeypatch.setattr(
        builder, "_chemical_capability_started", lambda *_a: True,
    )

    assert builder._unfunded_ladder_ingredient(
        object(), "nauvis", "player", "bulk-inserter",
    ) is None

    def _boom(*_args: object, **_kwargs: object) -> dict:
        raise RuntimeError("survey offline")

    monkeypatch.setattr(
        builder, "_chemical_capability_started", _boom,
    )

    with pytest.raises(builder.ProductionPrerequisiteDeferred) as caught:
        builder._unfunded_ladder_ingredient(
            object(), "nauvis", "player", "bulk-inserter",
        )
    assert caught.value.code == "capability_observation_wait"


def test_chemical_handoff_refusal_names_the_blocked_cell_facts(
    monkeypatch,
) -> None:
    """Cycle 11 refused a stocked oil-refinery cell on zero pipe production,
    but pairing that refusal with the cell's missing/requester/pool/loan
    facts needed archaeology across checkpoints. The refusal must carry the
    refusal-time cell telemetry adjacently."""
    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable", target_item="oil-refinery",
        target_count=1, side="left", requester_position=(50.5, 32.5),
        current_recipe="oil-refinery",
    )
    step = SimpleNamespace(
        recipe="oil-refinery", target_count=1, crafts=1,
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
        lambda *_a: "pipe",
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, name, _e, **_k: submitted.append((name, plan)),
    )
    messages: list[str] = []

    with pytest.raises(
        builder.ProductionPrerequisiteDeferred,
        match="retrying after re-observation",
    ) as deferred:
        builder._submit_bootstrap_loan(
            object(), object(), "nauvis", "player", loan,
            messages.append, reference_point=(3.0, -1.0),
        )

    assert deferred.value.code == "chemical_capability_handoff"
    assert any("CHEMICAL LADDER HANDOFF" in message for message in messages)
    cell_lines = [
        message for message in messages
        if "LOAN CELL TELEMETRY" in message and "oil-refinery" in message
    ]
    assert cell_lines, (
        "the refusal must name the blocked cell's missing ingredient, "
        "requester contents, free pool capacity, and active loan state"
    )


def test_empty_cell_delivery_names_the_delivery_time_stock_triple(
    monkeypatch,
) -> None:
    """Cycle 12 died with `moved 0 steel-plate` beside a requester reading 0
    while the net held 2-5: pairing that moved-0 with the delivery-time
    requester/net facts needed archaeology across passes. An empty delivery
    must carry its stock triple adjacently; a landed delivery stays quiet."""
    monkeypatch.setitem(builder.LINE_RECIPES, "steel-chest", {
        "ingredients": ["steel-plate"], "amounts": [8],
    })
    chest = (39.5, 32.5)
    monkeypatch.setattr(
        builder.live_base, "network_item_count", lambda *_a: 0,
    )
    monkeypatch.setattr(
        builder.live_base, "nearest_container",
        lambda *_a, **_k: (40.5, 32.5),
    )
    monkeypatch.setattr(
        builder.live_base, "chest_contents",
        lambda *_a: {"steel-plate": 0},
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"steel-plate": 4},
    )
    monkeypatch.setattr(
        builder.live_base, "transfer_stock", lambda *_a: 0,
    )
    messages: list[str] = []

    assert builder._deliver_cell_ingredients(
        object(), object(), "nauvis", "player", "steel-chest", chest,
        messages.append, requester_position=chest,
    ) is False

    stock_lines = [
        message for message in messages
        if "CELL DELIVERY STOCK" in message and "steel-plate" in message
    ]
    assert stock_lines, (
        "an empty delivery must name requester/local/net adjacently"
    )
    assert "requester=0" in stock_lines[0]
    assert "net=4" in stock_lines[0]

    monkeypatch.setattr(
        builder.live_base, "transfer_stock", lambda *_a: 5,
    )
    landed: list[str] = []

    assert builder._deliver_cell_ingredients(
        object(), object(), "nauvis", "player", "steel-chest", chest,
        landed.append, requester_position=chest,
    ) is True
    assert not any("CELL DELIVERY STOCK" in message for message in landed)


def test_rotating_switch_wait_names_usable_vs_available_stock(monkeypatch) -> None:
    """Cycle 13 repeated `needs steel-plate=2, with no stock` while the net
    held 2: the shortage predicate reads usable (ledger-allocatable) stock,
    so flowing-but-locked units look absent on the SWITCH line. The wait must
    carry its usable/available pair beside the prerequisite requester, free
    pool capacity, and active loan state adjacently; a missing prerequisite
    loan reads `none` instead of raising."""
    loan = SimpleNamespace(
        target_item="steel-chest", step_recipe="steel-plate",
        current_recipe="steel-chest",
        machine_position=(36.5, 32.5), requester_position=(39.5, 32.5),
    )
    monkeypatch.setattr(
        builder.live_base, "chest_contents", lambda *_a: {"steel-plate": 0},
    )
    monkeypatch.setattr(builder, "mall_slot_count", lambda *_a: 6)
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder, "_bootstrap_mall_slot_limit", lambda *_a: 12)
    messages: list[str] = []

    assert builder._emit_rotating_switch_stock(
        object(), "nauvis", "player", "assembling-machine-2",
        "steel-plate", 2, {"steel-plate": 2}, {"steel-plate": 0},
        (loan,), (3.0, -1.0), messages.append,
    ) is None

    assert len(messages) == 1
    line = messages[0]
    assert "ROTATING MALL STOCK" in line
    assert "steel-plate=2" in line
    assert "usable=0" in line
    assert "available=2" in line
    assert "requester=0" in line
    assert "6/12" in line
    assert "steel-chest:steel-plate@(36.5,32.5)" in line

    lonely: list[str] = []

    assert builder._emit_rotating_switch_stock(
        object(), "nauvis", "player", "assembling-machine-2",
        "steel-plate", 2, {"steel-plate": 0}, {"steel-plate": 0},
        (), None, lonely.append,
    ) is None

    assert len(lonely) == 1
    assert "requester=none" in lonely[0]
    assert "pool ? free" in lonely[0]
    assert "loans -" in lonely[0]


def test_no_progress_verdict_names_work_loans_and_pool(monkeypatch) -> None:
    """Cycle 14 died on `electric-furnace 88%` with a true ladder reason
    while establishment advanced elsewhere: the fatal verdict must carry
    the frozen work keys, the selected item's transferable count, per-loan
    fulfillment distances, each loan cell's requester contents, and free
    pool capacity adjacently; a null task with no loans or pool reads safe
    markers instead of raising."""
    loan = SimpleNamespace(
        target_item="pipe", step_recipe="pipe",
        current_recipe="pipe", machine_position=(36.5, 32.5),
        requester_position=(39.5, 32.5), target_count=100,
        spare_target_count=100,
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"pipe": 81, "electric-furnace": 23},
    )
    monkeypatch.setattr(
        builder.live_base, "chest_contents",
        lambda *_a: {"iron-plate": 0},
    )
    monkeypatch.setattr(
        builder, "active_bootstrap_loans", lambda *_a: (loan,),
    )
    monkeypatch.setattr(builder, "mall_slot_count", lambda *_a: 7)
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder, "_bootstrap_mall_slot_limit", lambda *_a: 12)
    messages: list[str] = []
    signature = (
        "electric-furnace", 88, ("electric-furnace", "pipe"), (), (),
        (("iron-plate", (("electric-furnace", 6),)),), (),
    )

    assert builder._emit_no_progress_verdict_stock(
        object(), "nauvis", "player", signature, "automation-science-pack",
        12, 5, 1234, (3.0, -1.0), messages.append,
    ) is None

    assert len(messages) == 1
    line = messages[0]
    assert "NO PROGRESS VERDICT" in line
    assert "task=electric-furnace" in line
    assert "progress=88%" in line
    assert "passes=12" in line
    assert "mall=[electric-furnace,pipe]" in line
    assert "plates=[iron-plate]" in line
    assert "have=23" in line
    assert "ghosts=5" in line
    assert "stock=1234" in line
    assert "5/12" in line
    assert "pipe:pipe@(36.5,32.5) 81/100+100" in line
    assert "iron-plate=0" in line

    lonely: list[str] = []
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())

    assert builder._emit_no_progress_verdict_stock(
        object(), "nauvis", "player", (None, None, (), (), (), (), ()),
        "automation-science-pack", 12, 0, 0, None, lonely.append,
    ) is None

    assert len(lonely) == 1
    assert "task=automation-science-pack" in lonely[0]
    assert "loans -" in lonely[0]
    assert "cells -" in lonely[0]


def test_iteration_limit_verdict_carries_frozen_work(monkeypatch) -> None:
    """Cycle 15 died on the outer 100-pass budget with the inner 12-pass
    guard silent all run: the fatal iteration-limit verdict must carry the
    same frozen-work snapshot adjacently; a missing signature degrades to
    safe markers instead of raising."""
    loan = SimpleNamespace(
        target_item="chemical-plant", step_recipe="chemical-plant",
        current_recipe="chemical-plant", machine_position=(36.5, 32.5),
        requester_position=(39.5, 32.5), target_count=2,
        spare_target_count=2,
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"chemical-plant": 2, "pipe": 101, "steel-plate": 44},
    )
    monkeypatch.setattr(
        builder.live_base, "chest_contents",
        lambda *_a: {"electronic-circuit": 19, "steel-plate": 1},
    )
    monkeypatch.setattr(
        builder, "active_bootstrap_loans", lambda *_a: (loan,),
    )
    monkeypatch.setattr(builder, "mall_slot_count", lambda *_a: 7)
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder, "_bootstrap_mall_slot_limit", lambda *_a: 12)
    messages: list[str] = []
    signature = (
        "chemical-plant", None, ("chemical-plant", "pipe"), (), (),
        (("copper-plate", (("electric-furnace", 6),)),), (),
    )

    assert builder._emit_iteration_limit_verdict(
        object(), "nauvis", "player", signature, "automation-science-pack",
        100, 3, 5678, (3.0, -1.0), messages.append,
    ) is None

    assert len(messages) == 1
    line = messages[0]
    assert "NO PROGRESS VERDICT" in line
    assert "task=chemical-plant" in line
    assert "passes=100" in line
    assert "mall=[chemical-plant,pipe]" in line
    assert "plates=[copper-plate]" in line
    assert "have=2" in line
    assert "ghosts=3" in line
    assert "stock=5678" in line
    assert "5/12" in line
    assert "chemical-plant:chemical-plant@(36.5,32.5) 2/2+2" in line
    assert "electronic-circuit=19" in line

    lonely: list[str] = []
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())

    assert builder._emit_iteration_limit_verdict(
        object(), "nauvis", "player", (), "automation-science-pack",
        0, 0, 0, None, lonely.append,
    ) is None

    assert len(lonely) == 1
    assert "task=automation-science-pack" in lonely[0]
    assert "passes=0" in lonely[0]
    assert "loans -" in lonely[0]
    assert "cells -" in lonely[0]


def test_no_progress_verdict_names_starved_ingredient_and_fulfill_state(monkeypatch) -> None:
    """Cycle 16 died on `splitter 0%` with copper-cable at net 0 freezing
    the e-circuit step: each loan entry must name its fulfill/restore
    state under the same `actual >= target_count` predicate the
    preempt/restore path uses, plus the first step-recipe ingredient
    missing from that cell with its net stock (`-` when the cell holds
    every ingredient or the recipe is unknown)."""
    starved = SimpleNamespace(
        target_item="splitter", step_recipe="electronic-circuit",
        current_recipe="electronic-circuit",
        machine_position=(36.5, 32.5), requester_position=(39.5, 32.5),
        target_count=50, spare_target_count=50,
    )
    fulfilled = SimpleNamespace(
        target_item="pipe", step_recipe="pipe",
        current_recipe="pipe",
        machine_position=(36.5, 38.5), requester_position=(39.5, 38.5),
        target_count=100, spare_target_count=100,
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {
            "splitter": 0, "pipe": 100, "copper-cable": 0,
            "iron-plate": 439,
        },
    )

    def _contents(_client, _surface, position):
        if tuple(position) == (39.5, 32.5):
            return {"iron-plate": 166}
        return {"iron-plate": 135}

    monkeypatch.setattr(builder.live_base, "chest_contents", _contents)
    monkeypatch.setattr(
        builder, "active_bootstrap_loans", lambda *_a: (starved, fulfilled),
    )
    monkeypatch.setattr(builder, "mall_slot_count", lambda *_a: 7)
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder, "_bootstrap_mall_slot_limit", lambda *_a: 12)
    messages: list[str] = []
    signature = ("splitter", 0, ("splitter", "pipe"), (), (), (), ())

    assert builder._emit_no_progress_verdict_stock(
        object(), "nauvis", "player", signature, "automation-science-pack",
        12, 2, 489, (3.0, -1.0), messages.append,
    ) is None

    assert len(messages) == 1
    line = messages[0]
    assert "NO PROGRESS VERDICT" in line
    assert "task=splitter" in line
    assert "passes=12" in line
    assert (
        "splitter:electronic-circuit@(36.5,32.5) 0/50+50 "
        "st=building miss=copper-cable(containers=0,transferable=?)"
    ) in line
    assert "pipe:pipe@(36.5,38.5) 100/100+100 st=fulfilled miss=-" in line
    assert "iron-plate=166" in line
    assert "iron-plate=135" in line

    unknown: list[str] = []
    stranger = SimpleNamespace(
        target_item="splitter", step_recipe="splitter",
        current_recipe="splitter",
        machine_position=(36.5, 32.5), requester_position=(39.5, 32.5),
        target_count=3, spare_target_count=None,
    )
    monkeypatch.setattr(
        builder, "active_bootstrap_loans", lambda *_a: (stranger,),
    )

    assert builder._emit_no_progress_verdict_stock(
        object(), "nauvis", "player", signature, "automation-science-pack",
        12, 2, 489, (3.0, -1.0), unknown.append,
    ) is None

    assert len(unknown) == 1
    assert "st=building miss=?" in unknown[0]


def test_no_progress_verdict_names_borrowed_assembler_status(monkeypatch) -> None:
    """Cycle 17 died on `splitter 0%` with the e-circuit loan frozen at
    177/202 crafts while both intakes read satisfied (cable net 81 with
    requester 0, plate net 731 with requester 0): each loan entry must
    name the borrowed assembler's live entity status (`none` when no
    entity sits at the loan machine position, `?` when the probe
    fails), so the next identical verdict separates an output block
    from input starvation, power loss, or a vanished machine."""
    stalled = SimpleNamespace(
        target_item="splitter", step_recipe="electronic-circuit",
        current_recipe="electronic-circuit",
        machine_position=(36.5, 32.5), requester_position=(39.5, 32.5),
        target_count=50, spare_target_count=50,
    )
    healthy = SimpleNamespace(
        target_item="pipe", step_recipe="pipe",
        current_recipe="pipe",
        machine_position=(36.5, 38.5), requester_position=(39.5, 38.5),
        target_count=100, spare_target_count=100,
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {
            "splitter": 0, "pipe": 100, "copper-cable": 81,
            "iron-plate": 731,
        },
    )

    def _contents(_client, _surface, position):
        if tuple(position) == (39.5, 32.5):
            return {}
        return {"iron-plate": 135}

    monkeypatch.setattr(builder.live_base, "chest_contents", _contents)
    monkeypatch.setattr(
        builder, "active_bootstrap_loans", lambda *_a: (stalled, healthy),
    )

    def _status(_client, _surface, position):
        if tuple(position) == (36.5, 32.5):
            return "full_output"
        return None

    monkeypatch.setattr(builder.live_base, "entity_status_name", _status)
    monkeypatch.setattr(builder, "mall_slot_count", lambda *_a: 7)
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder, "_bootstrap_mall_slot_limit", lambda *_a: 12)
    messages: list[str] = []
    signature = ("splitter", 0, ("splitter", "pipe"), (), (), (), ())

    assert builder._emit_no_progress_verdict_stock(
        object(), "nauvis", "player", signature, "automation-science-pack",
        12, 2, 576, (3.0, -1.0), messages.append,
    ) is None

    assert len(messages) == 1
    line = messages[0]
    assert (
        "splitter:electronic-circuit@(36.5,32.5) 0/50+50 "
        "st=building miss=copper-cable(containers=81,transferable=?) asm=full_output"
    ) in line
    assert "pipe:pipe@(36.5,38.5) 100/100+100 st=fulfilled miss=- asm=none" in line

    unreadable: list[str] = []
    monkeypatch.setattr(
        builder.live_base, "entity_status_name",
        lambda *_a: (_ for _ in ()).throw(RuntimeError("rcon down")),
    )

    assert builder._emit_no_progress_verdict_stock(
        object(), "nauvis", "player", signature, "automation-science-pack",
        12, 2, 576, (3.0, -1.0), unreadable.append,
    ) is None

    assert len(unreadable) == 1
    assert "asm=?" in unreadable[0]


@pytest.mark.parametrize(
    "contents,stock,free,expected",
    [
        ({"copper-cable": 1, "iron-plate": 10}, {"copper-cable": 81}, {},
         "miss=copper-cable(containers=81,transferable=0)"),
        ({}, None, None, "st=? miss=copper-cable(containers=?,transferable=?)"),
        (None, {}, {}, "miss=?"),
    ],
)
def test_verdict_distinguishes_reserved_partial_and_unreadable_stock(
    monkeypatch, contents, stock, free, expected,
) -> None:
    loan = SimpleNamespace(
        target_item="splitter", step_recipe="electronic-circuit",
        current_recipe="electronic-circuit", target_count=50,
        spare_target_count=50, machine_position=(0, 0), requester_position=(3, 0),
    )

    def probe(value):
        def read(*_args):
            if value is None:
                raise RuntimeError("unreadable")
            return value
        return read

    monkeypatch.setattr(builder.live_base, "available_items", probe(stock))
    monkeypatch.setattr(builder.live_base, "transferable_items", probe(free))
    monkeypatch.setattr(builder.live_base, "chest_contents", probe(contents))
    monkeypatch.setattr(builder.live_base, "entity_status_name", lambda *_: "no_ingredients")
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_: (loan,))
    monkeypatch.setattr(builder, "_bootstrap_mall_slot_limit", lambda *_: 12)
    messages = []
    builder._emit_no_progress_verdict_stock(
        object(), "nauvis", "player", ("splitter", 0), "automation-science-pack",
        12, 2, 576, None, messages.append,
    )
    assert expected in messages[0]
    if stock is None:
        assert "have=?" in messages[0]
    if contents is None:
        assert "splitter@(3.0,0.0){?}" in messages[0]
