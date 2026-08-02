# Path: tests/test_precursor_check.py
# Purpose: Prove the builder notices when it is eating stock nothing is refilling, and that the standing prep cells are built before the mall consumes them.

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator.autonomous_builder import (  # noqa: E402
    _MAX_UNCHANGED_PASSES,
    _has_producer,
    _pass_signature,
    _refuse_to_spin,
)
from orchestrator.baseline_production import BASELINE_MACHINES  # noqa: E402
from orchestrator.stage_services import StuckError  # noqa: E402


class _Line:
    def __init__(self, machine_count: int) -> None:
        self.machine_count = machine_count


@pytest.fixture(autouse=True)
def _clean_state():
    builder.UNBACKED_DRAWS.clear()
    yield
    builder.UNBACKED_DRAWS.clear()


@pytest.fixture
def lines(monkeypatch):
    """Model which recipes have a producing line on the base."""
    def _set(present: dict[str, int]):
        monkeypatch.setattr(
            builder.live_base, "find_line",
            lambda _c, _s, _f, recipe, _m: (
                _Line(present[recipe]) if recipe in present else None
            ),
        )
    return _set


def test_an_item_with_a_running_line_is_backed(lines) -> None:
    lines({"copper-cable": 2})

    assert _has_producer(None, "nauvis", "player", "copper-cable")


def test_an_item_with_no_line_is_not_backed(lines) -> None:
    """The live stall: 94 copper-cable in a chest, no cell making any."""
    lines({})

    assert not _has_producer(None, "nauvis", "player", "copper-cable")


def test_an_empty_line_counts_as_no_producer(lines) -> None:
    lines({"copper-cable": 0})

    assert not _has_producer(None, "nauvis", "player", "copper-cable")


def test_a_mined_input_is_not_ours_to_produce(lines) -> None:
    """iron-ore has no recipe; demanding a line for it would never be satisfied."""
    lines({})

    assert _has_producer(None, "nauvis", "player", "iron-ore")


def test_the_stall_report_names_what_nothing_is_producing() -> None:
    """A mystery stall becomes a named cause. This is the message the player
    needed on the run that ate its starter chest."""
    builder.UNBACKED_DRAWS.update({"copper-cable", "iron-gear-wheel"})
    signature = _pass_signature(None, {}, set())

    with pytest.raises(StuckError) as raised:
        _refuse_to_spin(_MAX_UNCHANGED_PASSES, signature, "electronic-circuit")

    message = str(raised.value)
    assert "Nothing is producing copper-cable, iron-gear-wheel" in message
    assert "before the buffer runs out" in message


def test_a_clean_stall_says_nothing_about_precursors() -> None:
    signature = _pass_signature(None, {}, set())

    with pytest.raises(StuckError) as raised:
        _refuse_to_spin(_MAX_UNCHANGED_PASSES, signature, "goal")

    assert "Nothing is producing" not in str(raised.value)


def test_an_unbacked_draw_is_flagged_in_the_log() -> None:
    source = inspect.getsource(builder._ingredient_sources)

    assert "NOTHING IS PRODUCING IT" in source
    assert "UNBACKED_DRAWS.add(ingredient)" in source


def test_a_draw_stops_being_unbacked_once_a_line_exists() -> None:
    """Otherwise the stall report keeps naming something already fixed."""
    source = inspect.getsource(builder._ingredient_sources)

    assert "UNBACKED_DRAWS.discard(ingredient)" in source


def test_the_record_does_not_leak_between_runs() -> None:
    assert "UNBACKED_DRAWS.clear()" in inspect.getsource(builder._open_the_run)


def test_the_essentials_the_player_named_are_in_the_prep_set() -> None:
    """'copper cable and gear assembly machines are absolutely necessary'."""
    assert BASELINE_MACHINES["copper-cable"] >= 2
    assert BASELINE_MACHINES["iron-gear-wheel"] >= 2
