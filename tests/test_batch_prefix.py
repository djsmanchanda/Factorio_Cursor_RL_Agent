# Path: tests/test_batch_prefix.py
# Purpose: Prove a reserved mining corridor that has grown into an obstacle yields a shorter batch instead of failing the same way on every later pass.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import live_base, stage_extraction  # noqa: E402
from orchestrator.extraction_capacity import phase_batch_positions  # noqa: E402
from orchestrator.extraction_state import ResourceMine  # noqa: E402

_CLIENT = object()


def _corridor(pairs: int) -> tuple[tuple[float, float], ...]:
    mine = ResourceMine(
        output=(0.0, 0.0), drill_count=0, row_capacity=pairs, expansion_step=1,
    )
    return phase_batch_positions(mine, pairs * 2)


@pytest.fixture
def survey(monkeypatch):
    """Model the two live probes a corridor check makes."""
    state = {"blocked": set(), "conflicts": set()}

    def area_clear(client, surface, minimum, maximum):
        centre = ((minimum[0] + maximum[0]) / 2, (minimum[1] + maximum[1]) / 2)
        return centre not in state["blocked"]

    def conflicts(client, surface, ore, positions):
        return [(p, "adjacent copper-ore") for p in positions if p in state["conflicts"]]

    monkeypatch.setattr(live_base, "area_clear", area_clear)
    monkeypatch.setattr(live_base, "drill_siting_conflicts", conflicts)
    return state


def _prefix(positions):
    return stage_extraction.buildable_batch_prefix(
        _CLIENT, "nauvis", "iron-ore", positions,
    )


def test_a_clear_corridor_is_returned_whole(survey) -> None:
    positions = _corridor(4)

    assert _prefix(positions) == positions


def test_a_blocked_column_truncates_the_batch(survey) -> None:
    positions = _corridor(4)
    survey["blocked"].add(positions[4])

    assert _prefix(positions) == positions[:4]


def test_either_half_of_a_column_truncates_it(survey) -> None:
    """Half a column is a drill with no partner and a belt paved past it."""
    positions = _corridor(4)
    survey["blocked"].add(positions[5])

    assert _prefix(positions) == positions[:4]


def test_a_clean_column_past_a_blocked_one_is_not_reached(survey) -> None:
    """The shared belt is paved per column, so a gap severs everything beyond
    it -- taking the clean subset would build drills that feed nothing."""
    positions = _corridor(4)
    survey["blocked"].add(positions[2])

    assert _prefix(positions) == positions[:2]


def test_a_foreign_ore_conflict_truncates_like_an_obstacle(survey) -> None:
    positions = _corridor(3)
    survey["conflicts"].add(positions[2])

    assert _prefix(positions) == positions[:2]


def test_a_corridor_blocked_at_its_first_column_is_finished(survey) -> None:
    """An empty answer is the signal to site a new row rather than to fail."""
    positions = _corridor(3)
    survey["blocked"].add(positions[0])

    assert _prefix(positions) == ()


def test_an_empty_corridor_is_finished(survey) -> None:
    assert _prefix(()) == ()


def test_conflicts_are_surveyed_in_one_call(survey, monkeypatch) -> None:
    """One query for the whole batch; per-column probing was the cost this
    batching removed and truncation must not reintroduce it."""
    calls = []
    inner = live_base.drill_siting_conflicts
    monkeypatch.setattr(
        live_base, "drill_siting_conflicts",
        lambda *a: (calls.append(a[3]), inner(*a))[1],
    )

    _prefix(_corridor(4))

    assert len(calls) == 1
    assert len(calls[0]) == 8


def test_an_exhausted_corridor_falls_through_to_a_new_row() -> None:
    """The empty prefix has to reach the branch that sites a fresh mine --
    otherwise it is the old permanent failure wearing a different shape."""
    import inspect

    source = inspect.getsource(stage_extraction.plan_local_extraction)
    body = source[source.index("buildable_batch_prefix("):]
    guard = body[:body.index("else:")]
    fallback = body[body.index("else:"):]

    assert "if positions:" in guard
    assert "_new_direct_mine(" in fallback


def test_the_batch_is_no_longer_all_or_nothing() -> None:
    import inspect

    source = inspect.getsource(stage_extraction.plan_local_extraction)

    assert "expansion drill site" not in source
    assert "cannot mine cleanly" not in source
