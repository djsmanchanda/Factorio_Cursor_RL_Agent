# Path: tests/test_prep_extraction.py
# Purpose: Prove prep grows plate extraction to the draw it declares, before intermediates, and treats a blocked corridor as a deferral rather than a dead run.

from __future__ import annotations

import inspect
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder  # noqa: E402
from orchestrator.baseline_production import (  # noqa: E402
    BASELINE_PLATES,
    baseline_drill_phase,
    baseline_smelter_count,
)

_SOURCE = inspect.getsource(autonomous_builder)
_PREP = inspect.getsource(autonomous_builder._prep_plate_extraction)
_LOOP = inspect.getsource(autonomous_builder.run)


def test_plates_are_prepped_before_intermediates() -> None:
    """An intermediate built over a starved plate line just starves too."""
    assert _LOOP.index("_prep_plate_extraction(") < _LOOP.index("_prep_intermediate(")


def test_extraction_is_grown_to_the_declared_furnace_count() -> None:
    assert "baseline_smelter_count(short_plate)" in _PREP
    assert "build_mining_stage(" in _PREP


def test_an_existing_line_is_expanded_rather_than_duplicated() -> None:
    assert "expand=plate_line is not None" in _PREP


def test_a_blocked_corridor_defers_instead_of_ending_the_run() -> None:
    """Reserved drill sites have been blocked for days of runs; that should
    cost a pass, not the run -- the ladder still climbs on demand."""
    assert "except (StuckError, ValueError)" in _PREP
    assert "PREP DEFERRED" in _PREP


def test_iron_prep_asks_for_more_than_a_starting_row() -> None:
    """8.75 plate/s needs 14 furnaces; a standard row builds 7."""
    assert baseline_smelter_count("iron-plate") == 14
    assert baseline_drill_phase("iron-plate") == 20


def test_copper_prep_is_satisfied_by_a_smaller_row() -> None:
    assert baseline_smelter_count("copper-plate") == 5


def test_every_prep_plate_has_a_declared_size() -> None:
    for plate in BASELINE_PLATES:
        assert baseline_smelter_count(plate) > 0
        assert baseline_drill_phase(plate) > 0
