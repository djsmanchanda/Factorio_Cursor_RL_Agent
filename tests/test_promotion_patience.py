# Path: tests/test_promotion_patience.py
# Purpose: Prove a dedicated line is built for work that is genuinely backing up, not for a busy cell that is nearly finished.

from __future__ import annotations

import inspect
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator.intermediate_scaling import (  # noqa: E402
    MALL_INTERMEDIATE_RATE_LIMIT,
    PROMOTED_LINE_MIN_MACHINES,
    PROMOTION_PATIENCE_SECONDS,
    backlog_seconds,
    output_per_machine,
    promoted_line_machine_count,
)


def _promote(outstanding: int, machines: int, demand: float = 0.0):
    return promoted_line_machine_count(
        "transport-belt", demand, machines, saturated=True,
        backlog=backlog_seconds("transport-belt", outstanding, machines),
    )


def test_a_busy_cell_that_is_nearly_done_is_not_promoted() -> None:
    """The reported fault: transport-belt promoted to six machines -- eighteen
    a second, with six feed requesters -- for a 200 target that one cell covers
    in about a minute."""
    assert _promote(outstanding=158, machines=1) is None


def test_a_cell_that_cannot_clear_its_backlog_is_promoted() -> None:
    assert _promote(outstanding=100_000, machines=1) == PROMOTED_LINE_MIN_MACHINES


def test_the_boundary_is_the_patience_window() -> None:
    rate = output_per_machine("transport-belt")
    just_inside = int(rate * (PROMOTION_PATIENCE_SECONDS - 10))
    just_outside = int(rate * (PROMOTION_PATIENCE_SECONDS + 10))

    assert _promote(outstanding=just_inside, machines=1) is None
    assert _promote(outstanding=just_outside, machines=1) is not None


def test_real_measured_demand_still_promotes_with_no_backlog() -> None:
    """Saturation is not the only route: a line with consumers drawing more
    than the rate limit is promoted whatever its stock looks like."""
    assert promoted_line_machine_count(
        "transport-belt", MALL_INTERMEDIATE_RATE_LIMIT + 1, 1,
        saturated=False, backlog=0.0,
    ) == PROMOTED_LINE_MIN_MACHINES


def test_nothing_built_yet_never_blocks_the_first_cell() -> None:
    """An infinite backlog is the honest answer with no machines, and must not
    read as 'nearly done'."""
    assert backlog_seconds("transport-belt", 100, 0) == float("inf")


def test_no_outstanding_work_is_no_backlog() -> None:
    assert backlog_seconds("transport-belt", 0, 1) == 0.0


def test_more_machines_clear_a_backlog_faster() -> None:
    assert backlog_seconds("transport-belt", 600, 6) < backlog_seconds(
        "transport-belt", 600, 1
    )


def test_the_survey_measures_the_backlog_from_live_stock() -> None:
    source = inspect.getsource(builder._plan_line)

    assert "backlog_seconds(" in source
    assert "stock_target -" in source, "outstanding is target minus what exists"


def test_the_promotion_log_says_how_much_is_outstanding() -> None:
    """'all 1 machine(s) running flat out' gave no way to see the decision was
    wrong; the backlog is the number that justifies it."""
    source = inspect.getsource(builder._plan_line)
    why = source[source.index("why = ("):source.index("INTERMEDIATE PROMOTION")]

    assert "backlog" in why
    assert "outstanding" in why
