# Path: tests/test_run_loop_bounds.py
# Purpose: Prove the run loop bounds a livelock and that a promoted line is sited beside the input it consumes fastest.

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder  # noqa: E402
from orchestrator.autonomous_builder import (  # noqa: E402
    _MAX_UNCHANGED_PASSES,
    _heaviest_source,
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
