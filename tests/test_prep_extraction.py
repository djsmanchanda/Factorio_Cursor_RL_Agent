# Path: tests/test_prep_extraction.py
# Purpose: Prove prep grows plate extraction to the draw it declares, before intermediates, and treats a blocked corridor as a deferral rather than a dead run.

from __future__ import annotations

import inspect
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
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
    """Dependency-ready cells may start between explicit foundations."""
    assert _LOOP.index("_prep_intermediate(") < _LOOP.index("_prep_plate_extraction(")


def test_metal_foundations_precede_the_belt_cell() -> None:
    assert _LOOP.index("_prep_plate_foundation(") < _LOOP.index("_prep_the_belt_cell(")


def test_prep_runs_before_the_mall_consumes_the_stock_it_needs() -> None:
    """Standing precursor cells run before either blocking or background mall work."""
    assert _LOOP.index("_prep_intermediate(") < _LOOP.index("_serve_ready_pass(")


def test_blocked_intermediate_hands_the_pass_to_the_mall() -> None:
    """An intermediate shortage cannot stage a coherent mine/refinery plan."""
    source = inspect.getsource(autonomous_builder._prep_intermediate)
    clause = source[source.index("except MaterialShortage"):]

    assert "return False" in clause


def test_intermediate_prep_uses_only_inputs_already_producing(monkeypatch) -> None:
    producing_iron = type("Line", (), {
        "machine_count": 6, "working_count": 1, "produced_count": 3,
    })()
    calls = []

    def find_line(_client, _surface, _force, recipe, _machine):
        return producing_iron if recipe == "iron-plate" else None

    monkeypatch.setattr(autonomous_builder.live_base, "find_line", find_line)
    monkeypatch.setattr(
        autonomous_builder, "ensure_produced",
        lambda *_args, **_kwargs: calls.append(_args[4]),
    )

    assert autonomous_builder._prep_intermediate(
        object(), object(), "nauvis", "player", set(), {},
        (0.0, 0.0), lambda _message: None,
    )
    assert calls == ["iron-gear-wheel"]


def test_extraction_is_grown_to_the_declared_furnace_count() -> None:
    assert "smelter_count_for_draw(short_plate, declared_draw)" in _PREP
    assert "build_mining_stage(" in _PREP


def test_requester_bootstrap_never_counts_as_the_first_direct_refinery(
    monkeypatch,
) -> None:
    calls = []
    line = type("Line", (), {
        "machine_count": 2,
        "machine_positions": ((145.5, -77.5), (145.5, -71.5)),
    })()
    monkeypatch.setattr(
        autonomous_builder.live_base, "available_items", lambda *_args: {},
    )
    monkeypatch.setattr(
        autonomous_builder.live_base, "find_line", lambda *_args: line,
    )
    monkeypatch.setattr(
        autonomous_builder, "smelter_count_for_draw", lambda *_args: 6,
    )
    monkeypatch.setattr(
        autonomous_builder, "_electric_furnace_producer_started",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        autonomous_builder, "build_mining_stage",
        lambda *_args, **kwargs: calls.append(kwargs["expand"]) or (10.5, 10.5),
    )

    autonomous_builder._prep_plate_extraction(
        object(), object(), "nauvis", "player", "copper-plate", set(), {},
        {}, (0.0, 0.0), lambda _message: None,
    )

    assert calls == [False]


def test_later_pipe_demand_reopens_completed_iron_prep(monkeypatch) -> None:
    """A chemical build can need more iron than the opening mall baseline."""
    calls: list[tuple[str, bool]] = []
    line = type(
        "Line", (),
        {"machine_count": 6, "working_count": 0, "produced_count": 0},
    )()
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


def test_drill_count_alone_does_not_force_an_iron_expansion(monkeypatch) -> None:
    calls: list[bool] = []
    line = type(
        "Line", (), {
            "machine_count": 6,
            "working_count": 6,
            "produced_count": 1,
            "machine_positions": (),
        },
    )()
    monkeypatch.setattr(
        autonomous_builder.live_base, "available_items", lambda *_args: {},
    )
    monkeypatch.setattr(autonomous_builder.live_base, "find_line", lambda *_args: line)
    monkeypatch.setattr(
        autonomous_builder, "smelter_count_for_draw", lambda *_args: 6,
    )
    monkeypatch.setattr(
        autonomous_builder, "_electric_furnace_producer_started",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        autonomous_builder, "build_mining_stage",
        lambda *_args, **kwargs: calls.append(kwargs["expand"]) or (10.5, 10.5),
    )

    autonomous_builder._prep_plate_extraction(
        object(), object(), "nauvis", "player", "iron-plate", set(), {},
        {}, (0.0, 0.0), lambda _message: None,
    )

    assert calls == []


def test_plate_foundation_uses_copper_before_permitting_iron_growth(monkeypatch) -> None:
    calls: list[tuple[str, int, tuple[tuple[float, float], ...]]] = []
    ready = {"iron-plate"}
    copper_starter = autonomous_builder.live_base.DirectPlateStarter(
        (57.5, 26.5), "north", 1,
    )
    stone_starter = autonomous_builder.live_base.DirectPlateStarter(
        (54.5, -64.5), "north", 1, ((57.5, -67.5),),
    )
    monkeypatch.setattr(
        autonomous_builder, "_direct_plate_foundation_ready",
        lambda *_args: _args[3] in ready,
    )
    monkeypatch.setattr(
        autonomous_builder.live_base, "direct_plate_starter",
        lambda *_args: {
            "copper-plate": copper_starter,
            "stone-brick": stone_starter,
        }.get(_args[3]),
    )
    monkeypatch.setattr(
        autonomous_builder, "_prep_plate_extraction",
        lambda *_args, **kwargs: calls.append((
            _args[4], kwargs["furnace_target"],
            kwargs["excluded_drill_positions"],
        )) or True,
    )

    assert autonomous_builder._prep_plate_foundation(
        object(), object(), "nauvis", "player", set(), {}, {}, (0.0, 0.0),
        lambda _message: None, {}, {},
    )
    assert calls == [("copper-plate", 6, ((57.5, 26.5),))]


def test_plate_starters_precede_both_full_foundations(monkeypatch) -> None:
    starters: list[str] = []
    standing: set[str] = set()
    monkeypatch.setattr(
        autonomous_builder, "_direct_plate_foundation_ready",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        autonomous_builder.live_base, "direct_plate_starter",
        lambda *_args: object() if _args[3] in standing else None,
    )

    def build_starter(*args):
        plate = args[4]
        starters.append(plate)
        standing.add(plate)
        return (0.0, 0.0)

    monkeypatch.setattr(
        autonomous_builder, "_bootstrap_direct_plate_line", build_starter,
    )
    monkeypatch.setattr(
        autonomous_builder, "_prep_plate_extraction",
        lambda *_args, **_kwargs: pytest.fail(
            "full foundation must wait until both direct starters exist"
        ),
    )

    for _ in range(3):
        assert autonomous_builder._prep_plate_foundation(
            object(), object(), "nauvis", "player", set(), {}, {}, (0.0, 0.0),
            lambda _message: None, {}, {},
        )

    assert starters == ["iron-plate", "copper-plate", "stone-brick"]


def test_stone_foundation_excludes_both_temporary_starter_drills(
    monkeypatch,
) -> None:
    stone = autonomous_builder.live_base.DirectPlateStarter(
        (54.5, -64.5), "north", 1, ((57.5, -67.5),),
    )
    captured = {}
    monkeypatch.setattr(
        autonomous_builder, "_direct_plate_foundation_ready",
        lambda *_args: _args[3] in {"iron-plate", "copper-plate"},
    )
    monkeypatch.setattr(
        autonomous_builder.live_base, "direct_plate_starter",
        lambda *_args: stone if _args[3] == "stone-brick" else None,
    )
    monkeypatch.setattr(
        autonomous_builder, "_prep_plate_extraction",
        lambda *_args, **kwargs: captured.update(kwargs) or True,
    )

    assert autonomous_builder._prep_plate_foundation(
        object(), object(), "nauvis", "player", set(), {}, {}, (0.0, 0.0),
        lambda _message: None, {}, {},
    )
    assert captured["excluded_drill_positions"] == (
        (54.5, -64.5), (57.5, -67.5),
    )


def test_foundation_resurvey_survives_a_just_retired_starter(monkeypatch) -> None:
    """A transient second survey may lose readiness after the starter is gone."""
    copper_checks = iter((True, False))

    def ready(*args):
        plate = args[3]
        return next(copper_checks) if plate == "copper-plate" else True

    captured: dict[str, object] = {}
    monkeypatch.setattr(autonomous_builder, "_direct_plate_foundation_ready", ready)
    monkeypatch.setattr(
        autonomous_builder.live_base, "direct_plate_starter", lambda *_args: None,
    )
    monkeypatch.setattr(
        autonomous_builder, "_prep_plate_extraction",
        lambda *_args, **kwargs: captured.update(kwargs) or True,
    )

    assert autonomous_builder._prep_plate_foundation(
        object(), object(), "nauvis", "player", set(), {}, {}, (0.0, 0.0),
        lambda _message: None, {}, {},
    )
    assert captured["excluded_drill_positions"] == ()


def test_fixed_foundation_target_suppresses_iron_proactive_growth(monkeypatch) -> None:
    line = type("Line", (), {
        "machine_count": 6,
        "machine_positions": (),
    })()
    calls = []
    monkeypatch.setattr(autonomous_builder.live_base, "available_items", lambda *_args: {})
    monkeypatch.setattr(autonomous_builder.live_base, "find_line", lambda *_args: line)
    monkeypatch.setattr(
        autonomous_builder, "_electric_furnace_producer_started", lambda *_args: False,
    )
    monkeypatch.setattr(
        autonomous_builder.extraction_state, "resource_drill_count", lambda *_args: 12,
    )
    monkeypatch.setattr(
        autonomous_builder, "build_mining_stage", lambda *_args, **_kwargs: calls.append(True),
    )

    assert autonomous_builder._prep_plate_extraction(
        object(), object(), "nauvis", "player", "iron-plate", set(), {}, {},
        (0.0, 0.0), lambda _message: None, furnace_target=6,
    )
    assert calls == []


def test_foundation_ready_merges_fed_and_unset_furnaces(monkeypatch) -> None:
    """A furnace has no recipe until ore reaches it, so four fed plus two
    unset machines can still be the complete opening module."""
    visible = (
        (95.5, 16.5), (101.5, 16.5),
        (95.5, 19.5), (101.5, 19.5),
    )
    idle = ((95.5, 22.5), (101.5, 22.5))
    line = type("Line", (), {
        "machine_count": 4,
        "machine_positions": visible,
        "output_position": visible[-1],
    })()
    idle_row = type("Line", (), {"machine_positions": idle})()
    monkeypatch.setattr(
        autonomous_builder.live_base, "find_line", lambda *_args: line,
    )
    monkeypatch.setattr(
        autonomous_builder.live_base, "find_idle_machine_row",
        lambda *_args, **_kwargs: idle_row,
    )
    monkeypatch.setattr(
        autonomous_builder, "logistic_smelter_origin", lambda _positions: None,
    )

    assert autonomous_builder._direct_plate_foundation_ready(
        object(), "nauvis", "player", "iron-plate",
    )


def test_partial_foundation_never_requests_expansion(monkeypatch) -> None:
    line = type("Line", (), {
        "machine_count": 4,
        "machine_positions": ((95.5, 16.5),),
    })()
    calls: list[bool] = []
    monkeypatch.setattr(autonomous_builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(autonomous_builder.live_base, "find_line", lambda *_a: line)
    monkeypatch.setattr(
        autonomous_builder, "_electric_furnace_producer_started", lambda *_a: False,
    )
    monkeypatch.setattr(
        autonomous_builder, "build_mining_stage",
        lambda *_a, **kwargs: calls.append(kwargs["expand"]) or (10.5, 10.5),
    )

    assert autonomous_builder._prep_plate_extraction(
        object(), object(), "nauvis", "player", "iron-plate", set(), {}, {},
        (0.0, 0.0), lambda _message: None, furnace_target=6,
    )
    assert calls == [False]
    autonomous_builder.MANAGED_INTERMEDIATE_SOURCES.clear()


def test_stone_foundation_has_a_declared_rate_without_plate_draw(monkeypatch) -> None:
    calls: list[bool] = []
    messages: list[str] = []
    monkeypatch.setattr(autonomous_builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(autonomous_builder.live_base, "find_line", lambda *_a: None)
    monkeypatch.setattr(
        autonomous_builder, "demand_adjusted_plate_draw",
        lambda *_a: {"iron-plate": 1.0, "copper-plate": 1.0},
    )
    monkeypatch.setattr(
        autonomous_builder, "_electric_furnace_producer_started", lambda *_a: False,
    )
    monkeypatch.setattr(
        autonomous_builder, "build_mining_stage",
        lambda *_a, **kwargs: calls.append(kwargs["expand"]) or (10.5, 10.5),
    )

    assert autonomous_builder._prep_plate_extraction(
        object(), object(), "nauvis", "player", "stone-brick", set(), {}, {},
        (0.0, 0.0), messages.append, furnace_target=6,
    )
    assert calls == [False]
    assert any("stone-brick extraction" in message for message in messages)
    autonomous_builder.MANAGED_INTERMEDIATE_SOURCES.clear()


def test_a_blocked_corridor_defers_instead_of_ending_the_run() -> None:
    """Reserved drill sites have been blocked for days of runs; that should
    cost a pass, not the run -- the ladder still climbs on demand."""
    assert "except (StuckError, ValueError)" in _PREP
    assert "PREP DEFERRED" in _PREP


def test_pending_foundation_holds_startup_on_a_construction_poll(monkeypatch) -> None:
    waits: list[float] = []
    deferred: dict[str, int] = {}
    monkeypatch.setattr(autonomous_builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(autonomous_builder.live_base, "find_line", lambda *_a: None)
    monkeypatch.setattr(autonomous_builder.time, "sleep", waits.append)
    monkeypatch.setattr(autonomous_builder, "consume_wait", lambda *_a: None)

    def pending(*_args, **_kwargs):
        cause = autonomous_builder.stage_extraction.PendingSystemDeferred(
            "pending off-ore smelter",
        )
        raise autonomous_builder.ProductionPrerequisiteDeferred(
            str(cause), code=cause.code, classification=cause.classification,
            state=cause.state,
        ) from cause

    monkeypatch.setattr(autonomous_builder, "build_mining_stage", pending)

    spent = autonomous_builder._prep_plate_extraction(
        object(), object(), "nauvis", "player", "iron-plate", set(),
        deferred, {}, (0.0, 0.0), lambda _message: None, furnace_target=6,
    )

    assert spent is True
    assert deferred == {}
    assert waits == [autonomous_builder._PENDING_FOUNDATION_POLL_SECONDS]


@pytest.mark.parametrize(
    "reason,state",
    [
        (
            "copper-plate mine power was repaired; waiting for ore delivery",
            "power_wait",
        ),
        ("copper-plate direct refinery has not produced yet", "producing"),
    ],
)
def test_foundation_recovery_holds_startup_instead_of_spinning_the_goal(
    monkeypatch, reason: str, state: str,
) -> None:
    waits: list[float] = []
    deferred: dict[str, int] = {}
    monkeypatch.setattr(autonomous_builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(autonomous_builder.live_base, "find_line", lambda *_a: None)
    monkeypatch.setattr(autonomous_builder.time, "sleep", waits.append)
    monkeypatch.setattr(autonomous_builder, "consume_wait", lambda *_a: None)
    monkeypatch.setattr(
        autonomous_builder, "build_mining_stage",
        lambda *_a, **_k: (_ for _ in ()).throw(
            autonomous_builder.ProductionPrerequisiteDeferred(reason, state=state),
        ),
    )

    spent = autonomous_builder._prep_plate_extraction(
        object(), object(), "nauvis", "player", "copper-plate", set(),
        deferred, {}, (0.0, 0.0), lambda _message: None, furnace_target=6,
    )

    assert spent is True
    assert deferred == {}
    assert waits == [autonomous_builder._PENDING_FOUNDATION_POLL_SECONDS]


def test_earmarked_foundation_retires_starter_after_live_output(monkeypatch) -> None:
    starter = autonomous_builder.live_base.DirectPlateStarter(
        (17.5, -2.5), "north", 1, ((17.5, 9.5),),
    )
    retired: list[str] = []
    monkeypatch.setattr(
        autonomous_builder, "_direct_plate_foundation_ready",
        lambda *_args: _args[-1] == "iron-plate",
    )
    monkeypatch.setattr(
        autonomous_builder.live_base, "direct_plate_starter",
        lambda *_args: starter if _args[3] == "iron-plate" else None,
    )
    line = type("Line", (), {"working_count": 1, "produced_count": 1})()
    monkeypatch.setattr(autonomous_builder.live_base, "find_line", lambda *_a: line)
    monkeypatch.setattr(
        autonomous_builder, "_retire_standing_bootstrap_cells",
        lambda *_args: retired.append(_args[4]),
    )

    spent = autonomous_builder._prep_plate_foundation(
        object(), object(), "nauvis", "player", set(), {}, {}, (0.0, 0.0),
        lambda _message: None, {}, {},
    )

    assert spent is True
    assert retired == ["iron-plate"]


def test_plate_shortage_stops_later_plate_from_spending_belts() -> None:
    """The first blocked plate must not let the other baseline plate submit."""
    assert "plate in pending_plate_materials" in _LOOP
    assert "plate_spent = False" in _LOOP
    assert "if plate_spent:" in _LOOP


def test_plate_foundation_gate_runs_before_demand_driven_extraction() -> None:
    foundation = _LOOP.index("_prep_plate_foundation(")
    expansion = _LOOP.index("Extraction second")
    assert foundation < expansion


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


def test_plate_blueprint_releases_when_pending_material_chain_is_live(monkeypatch) -> None:
    attempts: list[dict] = []
    pending = {"iron-plate": {"transport-belt": 132}}
    monkeypatch.setattr(
        autonomous_builder.live_base, "available_items",
        lambda *_args: {"transport-belt": 0},
    )
    monkeypatch.setattr(autonomous_builder.live_base, "find_line", lambda *_a: None)
    monkeypatch.setattr(
        autonomous_builder, "construction_supply_chain_is_scheduled",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        autonomous_builder, "build_mining_stage",
        lambda *_args, **kwargs: attempts.append(kwargs),
    )

    assert autonomous_builder._prep_plate_extraction(
        object(), object(), "nauvis", "player", "iron-plate", set(),
        {}, {"transport-belt": 132}, (0.0, 0.0), lambda _message: None,
        pending_materials=pending, furnace_target=6,
    )

    assert len(attempts) == 1
    assert attempts[0]["earmark_unfunded"] is True
    assert pending == {}


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
        "stone-brick": 6, "steel-plate": 1,
    }


def test_furnace_cap_lifts_only_after_a_working_furnace_producer(monkeypatch) -> None:
    line = type(
        "Line", (), {"working_count": 0, "produced_count": 0},
    )()
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
    assert baseline_drill_phase("iron-plate") == 24


def test_copper_prep_is_satisfied_by_a_smaller_row() -> None:
    assert baseline_smelter_count("copper-plate") == 5


def test_every_prep_plate_has_a_declared_size() -> None:
    for plate in BASELINE_PLATES:
        assert baseline_smelter_count(plate) > 0
        assert baseline_drill_phase(plate) > 0
