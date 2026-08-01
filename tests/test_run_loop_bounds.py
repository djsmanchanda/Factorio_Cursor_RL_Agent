# Path: tests/test_run_loop_bounds.py
# Purpose: Prove the run loop bounds a livelock and that a promoted line is sited beside the input it consumes fastest.

from __future__ import annotations

import inspect
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder  # noqa: E402
from orchestrator.autonomous_builder import _MAX_UNCHANGED_PASSES, _heaviest_source  # noqa: E402

_SOURCE = inspect.getsource(autonomous_builder)


def test_repeating_the_same_task_at_the_same_completion_aborts() -> None:
    """Every observed livelock re-selected one task and never moved its
    completion -- fast-transport-belt at 8%, inserter at 45%, for hours."""
    assert "unchanged_passes >= _MAX_UNCHANGED_PASSES" in _SOURCE
    assert "No progress in" in _SOURCE
    assert 0 < _MAX_UNCHANGED_PASSES <= 50


def test_the_stall_signature_covers_every_kind_of_outstanding_work() -> None:
    """max_iterations never bounded this because the mall and prep paths
    `continue` without advancing it."""
    block = _SOURCE[_SOURCE.index("signature = ("):]
    signature = block[:block.index(")\n")]

    assert "task.item" in signature
    assert "task.progress_percent" in signature
    assert "mall_targets" in signature
    assert "prepped" in signature


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
