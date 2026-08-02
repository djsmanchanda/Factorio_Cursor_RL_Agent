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


def test_the_standing_cells_come_before_the_expensive_extraction() -> None:
    """Superseded the reverse ordering, 2026-08-02. Plates first was defensible
    -- an intermediate over a starved plate line starves too -- but extraction
    prep asks for 14 drills, and drills need circuits, which need the very
    copper-cable cell that was queued behind it. The cheap half goes first."""
    assert _LOOP.index("_prep_intermediate(") < _LOOP.index("_prep_plate_extraction(")


def test_prep_runs_before_the_mall_consumes_the_stock_it_needs() -> None:
    """The live stall: mall_targets starts with ten entries and only empties
    when all are met, so prep ordered after it never ran. The mall spent every
    pass eating the player's starter intermediates through MALL BOOTSTRAP while
    the lines that would refill them were never built."""
    assert _LOOP.index("_prep_intermediate(") < _LOOP.index("_serve_mall_task(")


def test_blocked_prep_hands_the_pass_to_the_mall() -> None:
    """Prep runs first now, so keeping the pass on a shortage would re-hit the
    identical shortage every pass and never reach the mall that fixes it."""
    import inspect

    from orchestrator import autonomous_builder as builder

    for helper in (builder._prep_intermediate, builder._prep_plate_extraction):
        source = inspect.getsource(helper)
        clause = source[source.index("except MaterialShortage"):]
        clause = clause[:clause.index("return") + len("return False")]
        assert "return False" in clause, f"{helper.__name__} keeps a blocked pass"


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
    """7.5 plate/s needs 12 furnaces; a standard row builds 7."""
    assert baseline_smelter_count("iron-plate") == 12
    assert baseline_drill_phase("iron-plate") == 20


def test_copper_prep_is_satisfied_by_a_smaller_row() -> None:
    assert baseline_smelter_count("copper-plate") == 5


def test_every_prep_plate_has_a_declared_size() -> None:
    for plate in BASELINE_PLATES:
        assert baseline_smelter_count(plate) > 0
        assert baseline_drill_phase(plate) > 0
