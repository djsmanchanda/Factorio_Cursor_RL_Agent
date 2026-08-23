# Path: tests/test_power_district.py
# Purpose: Prove deterministic rectangular power geometry, reconciliation, and sizing.

from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as builder
from orchestrator import live_base
from orchestrator.power_district import (
    EARLY_MEDIUM_UNIT,
    LARGER_SUBSTATION_UNIT,
    absolute_placements,
    cell_origin,
    classify_unit,
    coverage_faults,
    district_origin,
    ensure_power_capacity,
    load_state,
    required_units,
    unit_bounds,
    unit_tiles,
    validate_candidate,
)
from planners.infrastructure import POLE_SPECS


def _record(name: str, x: float, y: float, *, ghost: bool = False) -> dict:
    kind = "entity-ghost" if ghost else "entity"
    result = {
        "name": "entity-ghost" if ghost else name,
        "type": kind,
        "force": "player",
        "position": (x, y),
        "ghost_name": name if ghost else None,
        "tile_name": None,
        "deconstructed": False,
        "is_ghost": ghost,
    }
    return result


def test_templates_are_rectangular_grid_aligned_and_adjacent() -> None:
    for template in (EARLY_MEDIUM_UNIT, LARGER_SUBSTATION_UNIT):
        first_tiles = unit_tiles(0, (100, 100), template)
        second_tiles = unit_tiles(1, (100, 100), template)
        (first_min_x, first_min_y), (first_max_x, first_max_y) = unit_bounds(
            0, (100, 100), template,
        )
        assert first_max_x - first_min_x == template.width
        assert first_max_y - first_min_y == template.height
        assert min(x for x, _y in first_tiles) >= first_min_x
        assert max(x for x, _y in first_tiles) < first_max_x
        assert not first_tiles & second_tiles
        assert cell_origin(1, (100, 100), template)[0] > first_max_x
        assert coverage_faults(0, (100, 100), template) == []


def test_adjacent_units_have_declared_wire_connections() -> None:
    for template in (EARLY_MEDIUM_UNIT, LARGER_SUBSTATION_UNIT):
        for axis in ("x", "y"):
            offset = (
                template.stride_x if axis == "x" else 0,
                0 if axis == "x" else template.stride_y,
            )
            poles_a = [
                (x, y) for name, x, y in absolute_placements(0, (0, 0), template)
                if name in POLE_SPECS
            ]
            poles_b = [
                (x, y)
                for name, x, y in absolute_placements(1, (0, 0), template)
                if name in POLE_SPECS
            ]
            connected = any(
                ((a[0]-b[0]) ** 2 + (a[1]-b[1]) ** 2) ** 0.5 <= 9.0001
                for a in poles_a for b in poles_b
            )
            assert connected


def test_power_plans_request_runtime_atomic_preflight() -> None:
    from orchestrator.power_district import build_unit_plan

    plan = build_unit_plan(
        surface="nauvis", force="player", index=0, origin=(0, 0),
        template=EARLY_MEDIUM_UNIT,
    )
    assert plan["atomic"] is True
    executor = (Path(__file__).parents[1] / "factorio_mod" / "layout_executor.lua").read_text()
    assert "if build_plan.atomic == true then" in executor
    assert 'counts.error = "atomic_footprint_blocked"' in executor


@pytest.mark.parametrize("obstacle", [(102, 101), (108, 104), (111, 104)])
def test_any_obstacle_rejects_the_whole_early_unit(obstacle: tuple[int, int]) -> None:
    reason = validate_candidate(
        index=0,
        origin=(100, 100),
        template=EARLY_MEDIUM_UNIT,
        occupied_tiles={obstacle},
        deconstruction_tiles=set(),
        reserved_tiles=set(),
        pending_plan_tiles=set(),
    )
    assert reason is not None and f"({obstacle[0]},{obstacle[1]})" in reason


def test_pending_plan_and_deconstruction_are_separate_hard_blocks() -> None:
    kwargs = {
        "index": 0,
        "origin": (0, 0),
        "template": LARGER_SUBSTATION_UNIT,
        "occupied_tiles": set(),
        "reserved_tiles": set(),
    }
    assert validate_candidate(
        deconstruction_tiles={(8, 2)}, pending_plan_tiles=set(), **kwargs
    ).startswith("deconstruction_order")
    assert validate_candidate(
        deconstruction_tiles=set(), pending_plan_tiles={(8, 2)}, **kwargs
    ).startswith("pending_plan")


def test_district_cells_classify_exactly_and_preserve_foreign_entities() -> None:
    expected = EARLY_MEDIUM_UNIT.placements
    complete = [
        _record(name, 100 + x, 100 + y) for name, x, y in expected
    ]
    assert classify_unit(complete, index=0, origin=(100, 100), template=EARLY_MEDIUM_UNIT)[0] == "complete_owned"

    ghosts = [
        _record(name, 100 + x, 100 + y, ghost=True)
        for name, x, y in list(expected)[:3]
    ]
    assert classify_unit(ghosts, index=0, origin=(100, 100), template=EARLY_MEDIUM_UNIT)[0] == "pending_owned"

    partial = complete[:4]
    assert classify_unit(partial, index=0, origin=(100, 100), template=EARLY_MEDIUM_UNIT)[0] == "incomplete_owned"

    pipe = {
        "name": "pipe", "type": "pipe", "force": "player",
        "position": (103, 102), "ghost_name": None, "tile_name": None,
        "deconstructed": False, "is_ghost": False,
    }
    classification, foreign = classify_unit(
        [], index=0, origin=(100, 100), template=EARLY_MEDIUM_UNIT,
    )
    assert classification == "empty_available"
    classification, foreign = classify_unit(
        [pipe], index=0, origin=(100, 100), template=EARLY_MEDIUM_UNIT,
    )
    assert classification == "obstructed_unrelated"
    assert foreign == [pipe]


def test_sizing_is_convergent_on_generation_storage_and_recharge() -> None:
    initial = required_units(
        LARGER_SUBSTATION_UNIT,
        firm_generation_kw=167.0,
        solar_generation_kw=1200.0,
        storage_mj=0.0,
        peak_load_kw=500.0,
    )
    after_one = required_units(
        LARGER_SUBSTATION_UNIT,
        firm_generation_kw=167.0,
        solar_generation_kw=1200.0 + LARGER_SUBSTATION_UNIT.generation_kw,
        storage_mj=LARGER_SUBSTATION_UNIT.storage_mj,
        peak_load_kw=500.0,
    )
    assert initial >= 1
    assert after_one < initial
    converged = required_units(
        LARGER_SUBSTATION_UNIT,
        firm_generation_kw=167.0,
        solar_generation_kw=1200.0 + initial * LARGER_SUBSTATION_UNIT.generation_kw,
        storage_mj=initial * LARGER_SUBSTATION_UNIT.storage_mj,
        peak_load_kw=500.0,
    )
    assert converged < initial


def _expected_records(index: int, origin):
    from orchestrator.power_district import absolute_placements

    return [
        _record(name, x, y)
        for name, x, y in absolute_placements(index, origin, LARGER_SUBSTATION_UNIT)
    ]


def _converged_metrics():
    from orchestrator.power_district import usable_metrics
    return usable_metrics(167.0, 1200.0 + 4 * LARGER_SUBSTATION_UNIT.generation_kw,
                          4 * LARGER_SUBSTATION_UNIT.storage_mj, 500.0)


def test_one_atomic_unit_is_submitted_then_convergence_stops(
    monkeypatch, tmp_path: Path,
) -> None:
    origin = district_origin((0.0, 0.0), True)
    script_output = tmp_path / "script-output"
    submissions = []
    surveys = iter([[], _expected_records(0, origin)])
    storage = iter([0.0, 20.0])
    convergence_surveys: list[list] | None = None

    monkeypatch.setattr(
        live_base, "network_firm_generation_kw", lambda *_a: 167.0,
    )
    generation = iter([647.0, 1367.0])
    monkeypatch.setattr(
        live_base, "network_generation_kw",
        lambda *_a: next(generation),
    )
    monkeypatch.setattr(
        live_base, "network_accumulator_storage_mj",
        lambda *_a: next(storage),
    )
    monkeypatch.setattr(
        live_base, "available_items",
        lambda *_a: {
            "solar-panel": 20, "accumulator": 10, "substation": 5,
            "medium-electric-pole": 10,
        },
    )
    monkeypatch.setattr(
        live_base, "area_entity_records",
        lambda *_a, **_k: next(surveys),
    )
    monkeypatch.setattr(live_base, "occupied_tiles", lambda *_a, **_k: set())
    monkeypatch.setattr(live_base, "deconstruction_tiles", lambda *_a: set())
    monkeypatch.patch_note = None
    import orchestrator.power_district as power
    monkeypatch.setattr(
        power, "network_peak_consumption_kw", lambda *_a: 500.0,
    )

    def submit(_client, _bridge, _surface, plan, name, _emit):
        submissions.append((name, plan))
        return {"ok": True}

    acted = ensure_power_capacity(
        client=object(),
        bridge=SimpleNamespace(script_output=script_output),
        surface="nauvis",
        force="player",
        near=(0.0, 0.0),
        script_output=script_output,
        emit=lambda _message: None,
        submit=submit,
    )
    assert acted is True
    assert len(submissions) == 1
    assert submissions[0][0] == "power_unit_0"
    state = load_state(script_output)
    assert state["next_index"] == 1
    assert state["active_index"] is None

    # The next call starts at reconstructed index 1; an empty survey plus a
    # converged metric proves it neither restarts at zero nor resubmits.
    monkeypatch.setattr(
        live_base, "area_entity_records", lambda *_a, **_k: [],
    )

    acted_again = ensure_power_capacity(
        client=object(),
        bridge=SimpleNamespace(script_output=script_output),
        surface="nauvis",
        force="player",
        near=(0.0, 0.0),
        script_output=script_output,
        emit=lambda _message: None,
        submit=lambda *_a: submissions.append(("duplicate", {})),
    )
    assert acted_again is False
    assert len(submissions) == 1
