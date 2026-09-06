# Path: tests/test_run_loop_bounds.py
# Purpose: Prove the run loop bounds a livelock and that a promoted line is sited beside the input it consumes fastest.

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator import autonomous_builder  # noqa: E402
from orchestrator.autonomous_builder import (  # noqa: E402
    _MAX_UNCHANGED_PASSES,
    _heaviest_source,
    _livelock_step,
    _outstanding_work_signature,
    _pass_signature,
    _refuse_to_spin,
)
from orchestrator.stage_services import StuckError  # noqa: E402

_SOURCE = inspect.getsource(autonomous_builder)


class _Task:
    def __init__(self, item: str, progress_percent: int) -> None:
        self.item, self.progress_percent = item, progress_percent


def test_repeating_the_same_task_at_the_same_completion_aborts() -> None:
    """Every observed livelock re-selected one task and never moved its
    completion -- fast-transport-belt at 8%, inserter at 45%, for hours."""
    signature = _pass_signature(_Task("inserter", 45), {"inserter": 20}, set())

    with pytest.raises(StuckError, match="No progress in"):
        _refuse_to_spin(_MAX_UNCHANGED_PASSES, signature, "inserter")


def test_one_pass_short_of_the_bound_keeps_going() -> None:
    signature = _pass_signature(_Task("inserter", 45), {"inserter": 20}, set())

    assert _refuse_to_spin(_MAX_UNCHANGED_PASSES - 1, signature, "inserter") is None
    assert 0 < _MAX_UNCHANGED_PASSES <= 50


def test_the_abort_names_what_was_stuck_and_at_what_completion() -> None:
    signature = _pass_signature(_Task("inserter", 45), {}, set())

    with pytest.raises(StuckError) as raised:
        _refuse_to_spin(_MAX_UNCHANGED_PASSES, signature, "goal")

    assert "inserter" in str(raised.value)
    assert "45%" in str(raised.value)
    assert raised.value.code == "no_progress"
    assert raised.value.classification == "bug"
    assert raised.value.details["progress_percent"] == 45


def test_the_goal_item_is_named_when_no_task_was_selected() -> None:
    signature = _pass_signature(None, {}, set())

    with pytest.raises(StuckError, match="logistic-science-pack"):
        _refuse_to_spin(_MAX_UNCHANGED_PASSES, signature, "logistic-science-pack")


def test_the_stall_signature_covers_every_kind_of_outstanding_work() -> None:
    """max_iterations never bounded this because the mall and prep paths
    `continue` without advancing it. Progress is therefore measured by what the
    pass chose AND by what work is still outstanding -- each component alone has
    to be able to break the tie."""
    base = _pass_signature(_Task("inserter", 45), {"inserter": 20}, {"copper-cable"})

    assert base == _pass_signature(
        _Task("inserter", 45), {"inserter": 20}, {"copper-cable"})
    assert base != _pass_signature(
        _Task("transport-belt", 45), {"inserter": 20}, {"copper-cable"})
    assert base != _pass_signature(
        _Task("inserter", 46), {"inserter": 20}, {"copper-cable"})
    assert base != _pass_signature(
        _Task("inserter", 45), {"inserter": 20, "lab": 4}, {"copper-cable"})
    assert base != _pass_signature(
        _Task("inserter", 45), {"inserter": 20}, {"copper-cable", "pipe"})


def test_a_raised_target_alone_is_not_progress() -> None:
    """Only the SET of outstanding items counts, not the quantities. Raising a
    target while nothing gets built is exactly the spin this bound catches."""
    assert _pass_signature(_Task("i", 1), {"drill": 6}, set()) == _pass_signature(
        _Task("i", 1), {"drill": 14}, set(),
    )


def test_the_signature_does_not_depend_on_dict_or_set_ordering() -> None:
    """Two passes that differ only in iteration order are the same pass."""
    assert _pass_signature(_Task("i", 1), {"a": 1, "b": 2}, {"x", "y"}) == \
        _pass_signature(_Task("i", 1), {"b": 2, "a": 1}, {"y", "x"})


def test_alternating_tasks_do_not_hide_unchanged_outstanding_work() -> None:
    advanced = _pass_signature(
        _Task("advanced-circuit", 0),
        {"advanced-circuit": 1, "steel-plate": 5}, set(),
    )
    steel = _pass_signature(
        _Task("steel-plate", 0),
        {"advanced-circuit": 1, "steel-plate": 5}, set(),
    )

    assert advanced != steel
    assert _outstanding_work_signature(advanced) == _outstanding_work_signature(steel)


def test_a_line_sites_beside_its_single_input() -> None:
    assert _heaviest_source("copper-cable", {"copper-plate": (80.0, -19.0)}, 6) == (80.0, -19.0)


def test_a_line_sites_beside_its_heaviest_input_not_its_first() -> None:
    """electronic-circuit eats three cable per one plate; the belt that would
    cost most is the one worth not building."""
    chosen = _heaviest_source(
        "electronic-circuit",
        {"copper-cable": (5.0, 5.0), "iron-plate": (-34.0, -26.0)}, 6,
    )

    assert chosen == (5.0, 5.0)


def test_an_unknown_source_leaves_the_callers_reference() -> None:
    assert _heaviest_source("copper-cable", {}, 6) is None
    assert "or reference_point" in _SOURCE


def test_bots_building_resets_the_livelock_bound() -> None:
    """A falling ghost count is construction, not a spin: the research-queue
    run of 2026-08-21 died while copper was mid-construction."""
    assert _livelock_step(False, True, 11) == 0
    assert _livelock_step(True, True, 11) == 0


def test_a_repeated_signature_with_no_ground_progress_accumulates() -> None:
    assert _livelock_step(False, False, 3) == 4
    assert _livelock_step(True, False, 3) == 0


def test_deferred_control_telemetry_preserves_bill_fallback_and_credit_coverage(
    monkeypatch,
) -> None:
    """The chemical fast-belt stall must identify its bill, not just its mall task."""
    task = SimpleNamespace(item="fast-transport-belt", target=9)
    priorities = SimpleNamespace(items={
        "fast-transport-belt": SimpleNamespace(
            status="deferred", reason="fast belts wait for electric furnaces",
            retry_tick=4_600,
        ),
    })
    project = SimpleNamespace(
        sequence=1, project_id="expand_stone-brick_system", state="reserved",
        required={"fast-transport-belt": 9, "transport-belt": 4},
    )
    monkeypatch.setattr(
        autonomous_builder, "_MATERIAL_RESERVATION_LEDGER",
        SimpleNamespace(projects={project.project_id: project}),
    )
    monkeypatch.setattr(
        autonomous_builder.live_base, "available_items",
        lambda *_args: {
            "fast-transport-belt": 0,
            "transport-belt": 52,
            "chemical-science-pack": 0,
            "automation-science-pack": 190,
        },
    )
    emitted: list[str] = []

    autonomous_builder._emit_deferred_control_telemetry(
        object(), "nauvis", "player", task, 1_000,
        {"fast-transport-belt": 9}, {}, priorities,
        "chemical-science-pack", ("automation-science-pack",), emitted.append,
    )
    autonomous_builder._emit_deferred_control_telemetry(
        object(), "nauvis", "player", task, 1_000,
        {"fast-transport-belt": 9}, {}, priorities,
        "chemical-science-pack", ("automation-science-pack",), emitted.append,
    )

    assert "repeat=1; backoff=3600 ticks" in emitted[0]
    assert "expand_stone-brick_system[state=reserved; bill=fast-transport-belt=9,transport-belt=4]" in emitted[0]
    assert "belt-fallback=fast=0, regular=52, decision=not-applied" in emitted[0]
    assert "chemical-credit(goal=chemical-science-pack)=[automation-science-pack=190,fast-transport-belt=0]" in emitted[0]
    assert "repeat=2" in emitted[1]


@pytest.mark.parametrize(("wait_ticks", "expected_seconds"), [
    (3507, 30.0),
    (291, 291 / 60),
    (0, 1.0),
])
def test_priority_wait_sleeps_to_due_tick_without_busy_polling(
    monkeypatch, wait_ticks: int, expected_seconds: float,
) -> None:
    slept = []
    priorities = type("Priorities", (), {
        "wait_ticks": lambda self, _targets, _tick: wait_ticks,
        "next": lambda self, _targets, _tick: None,
    })()
    monkeypatch.setattr(autonomous_builder.time, "sleep", slept.append)

    result = autonomous_builder._serve_ready_pass(
        object(), object(), "nauvis", "player", None, 0,
        {"splitter": 3}, {}, priorities, (0.0, 0.0),
        "automation-science-pack", lambda _message: None,
    )

    assert result is autonomous_builder._SHORTAGE
    assert slept == [expected_seconds]
