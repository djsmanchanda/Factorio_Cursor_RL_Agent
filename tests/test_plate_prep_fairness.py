# Path: tests/test_plate_prep_fairness.py
# Purpose: Prove every unprepped plate gets a turn, so one that keeps yielding on materials cannot starve the others out of a whole run.

from __future__ import annotations

import inspect
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator.baseline_production import BASELINE_PLATES  # noqa: E402

_RUN = inspect.getsource(builder.run)


def test_every_plate_is_rechecked_after_mall_targets_change() -> None:
    """A completed baseline must still grow when later work raises demand.

    Iterating the full pair lets iron and copper each react to a changed mall
    bill, while ``any`` still gives the pass to the first one that acts.
    """
    start = _RUN.index("Extraction second")
    block = _RUN[start:_RUN.index("_serve_ready_pass(", start)]

    assert "for plate in BASELINE_PLATES" in block
    assert "next(" not in block, "taking only the head is what starved copper"


def test_the_pass_is_spent_by_whichever_plate_did_work() -> None:
    start = _RUN.index("Extraction second")
    block = _RUN[start:_RUN.index("_serve_ready_pass(", start)]

    assert "any(" in block, "stop at the first plate that spends the pass"


def test_both_plates_are_still_in_the_prep_set() -> None:
    assert set(BASELINE_PLATES) == {"iron-plate", "copper-plate"}


def test_a_yielding_plate_does_not_mark_itself_prepped() -> None:
    """Otherwise it would be skipped for the rest of the run rather than
    retried once the mall has built its drills."""
    source = inspect.getsource(builder._prep_plate_extraction)
    shortage = source[source.index("except MaterialShortage"):]
    shortage = shortage[:shortage.index("except (StuckError")]

    assert "prepped.add" not in shortage
    assert "return False" in shortage
