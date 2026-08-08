# Path: tests/test_prep_extraction.py
# Purpose: Prove prep grows plate extraction to the draw it declares, before intermediates, and treats a blocked corridor as a deferral rather than a dead run.

from __future__ import annotations

import inspect
import pytest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder  # noqa: E402
from orchestrator.baseline_production import (  # noqa: E402
    BASELINE_PLATES, BOOTSTRAP_FURNACE_CAPS,
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
    """Standing precursor cells run before either blocking or background mall work."""
    assert _LOOP.index("_prep_intermediate(") < _LOOP.index("_serve_ready_pass(")


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
    assert "smelter_count_for_draw(short_plate, adjusted_draw[short_plate])" in _PREP
    assert "build_mining_stage(" in _PREP


def test_an_existing_line_is_expanded_rather_than_duplicated() -> None:
    assert "expand=plate_line is not None" in _PREP


def test_later_pipe_demand_reopens_completed_iron_prep(monkeypatch) -> None:
    """A chemical build can need more iron than the opening mall baseline."""
    calls: list[tuple[str, bool]] = []
    line = type("Line", (), {"machine_count": 6})()
    autonomous_builder.MANAGED_INTERMEDIATE_SOURCES.clear()
    monkeypatch.setattr(
        autonomous_builder.live_base, "available_items", lambda *_args: {"pipe": 77},
    )
    monkeypatch.setattr(autonomous_builder.live_base, "find_line", lambda *_args: line)
    monkeypatch.setattr(
        autonomous_builder, "build_mining_stage",
        lambda *_args, **kwargs: calls.append((_args[4], kwargs["expand"]))
        or (13.5, 43.5),
    )

    spent = autonomous_builder._prep_plate_extraction(
        object(), object(), "nauvis", "player", "iron-plate", {"iron-plate"}, {},
        {"pipe": 685}, (0.0, 0.0), lambda _message: None,
    )

    assert spent is True
    assert calls == [("iron-plate", True)]
    assert autonomous_builder.MANAGED_INTERMEDIATE_SOURCES["iron-plate"] == (13.5, 43.5)
    autonomous_builder.MANAGED_INTERMEDIATE_SOURCES.clear()


def test_a_blocked_corridor_defers_instead_of_ending_the_run() -> None:
    """Reserved drill sites have been blocked for days of runs; that should
    cost a pass, not the run -- the ladder still climbs on demand."""
    assert "except (StuckError, ValueError)" in _PREP
    assert "PREP DEFERRED" in _PREP


def test_plate_shortage_stops_later_plate_from_spending_belts() -> None:
    """The first blocked plate must not let the other baseline plate submit."""
    assert "plate in pending_plate_materials" in _LOOP
    assert "plate_spent = False" in _LOOP
    assert "if plate_spent:" in _LOOP


def test_material_blocked_plate_waits_for_its_exact_construction_bill(monkeypatch) -> None:
    available = {"fast-transport-belt": 73}
    attempts: list[str] = []
    pending = {"iron-plate": {"fast-transport-belt": 74}}
    monkeypatch.setattr(autonomous_builder.live_base, "available_items", lambda *_a: available)
    monkeypatch.setattr(autonomous_builder.live_base, "find_line", lambda *_a: None)
    monkeypatch.setattr(
        autonomous_builder, "build_mining_stage",
        lambda *_a, **_k: attempts.append("build"),
    )
    prepped: set[str] = set()
    deferred: dict[str, int] = {}

    assert not autonomous_builder._prep_plate_extraction(
        object(), object(), "nauvis", "player", "iron-plate", prepped,
        deferred, {"fast-transport-belt": 74}, (0.0, 0.0), lambda _m: None,
        pending_materials=pending,
    )
    assert attempts == [] and pending

    available["fast-transport-belt"] = 74
    assert autonomous_builder._prep_plate_extraction(
        object(), object(), "nauvis", "player", "iron-plate", prepped,
        deferred, {"fast-transport-belt": 74}, (0.0, 0.0), lambda _m: None,
        pending_materials=pending,
    )
    assert attempts == ["build"] and not pending


def test_fast_belts_wait_for_an_electric_furnace_producer(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        autonomous_builder, "_electric_furnace_producer_started", lambda *_a: False,
    )
    monkeypatch.setattr(
        autonomous_builder, "build_mining_stage",
        lambda *_a, **_k: calls.append((_a[4], _k["expand"])),
    )

    with pytest.raises(autonomous_builder.ProductionPrerequisiteDeferred):
        autonomous_builder.ensure_produced(
            object(), object(), "nauvis", "player", "fast-transport-belt",
            (0.0, 0.0), lambda _message: None,
        )

    assert calls == []


def test_fast_belts_expand_iron_after_furnace_production_starts(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        autonomous_builder, "_electric_furnace_producer_started", lambda *_a: True,
    )
    monkeypatch.setattr(
        autonomous_builder, "_iron_capacity_for_fast_belts", lambda *_a: (6, 6),
    )
    monkeypatch.setattr(
        autonomous_builder, "build_mining_stage",
        lambda *_a, **_k: calls.append((_a[4], _k["expand"])),
    )

    with pytest.raises(autonomous_builder.ProductionPrerequisiteDeferred):
        autonomous_builder.ensure_produced(
            object(), object(), "nauvis", "player", "fast-transport-belt",
            (0.0, 0.0), lambda _message: None,
        )

    assert calls == [("iron-plate", True)]

def test_bootstrap_furnace_caps_match_the_early_resource_policy() -> None:
    assert BOOTSTRAP_FURNACE_CAPS == {
        "iron-plate": 12, "copper-plate": 6,
        "stone-brick": 6, "steel-plate": 6,
    }


def test_furnace_cap_lifts_only_after_a_working_furnace_producer(monkeypatch) -> None:
    line = type("Line", (), {"working_count": 0})()
    monkeypatch.setattr(
        autonomous_builder.live_base, "find_line", lambda *_args: line,
    )
    assert not autonomous_builder._electric_furnace_producer_started(
        object(), "nauvis", "player",
    )
    line.working_count = 1
    assert autonomous_builder._electric_furnace_producer_started(
        object(), "nauvis", "player",
    )


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
