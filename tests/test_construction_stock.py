# Path: tests/test_construction_stock.py
# Purpose: Prove mall cells prebuild deterministic stack reserves without making the mission wait for the full reserve.

from __future__ import annotations

import inspect
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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

    assert "stock_target=target" in ensured
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
