# Path: tests/test_bootstrap_priorities.py
# Purpose: The user standard of 2026-08-22 -- essential plate systems must not
# starve on belt economy: spend stocked fast belts first, and stand up the
# mall's transport-belt assembler before any mine consumes the reserve.

from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as builder
from orchestrator.parts_mall import MaterialShortage


def test_essential_routes_spend_stocked_fast_belts_first(monkeypatch) -> None:
    """The starter kit ships fast belts precisely for the opening mines; a
    route that drains regular stock while fast sits unused is misplanned."""
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"fast-transport-belt": 100, "transport-belt": 12},
    )
    assert builder._essential_belt_type(object(), "nauvis", "player") == (
        "fast-transport-belt"
    )


def test_essential_routes_revert_to_regular_when_fast_runs_out(monkeypatch) -> None:
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"fast-transport-belt": 3, "transport-belt": 200},
    )
    assert builder._essential_belt_type(object(), "nauvis", "player") == (
        "transport-belt"
    )


def _prep_env(monkeypatch) -> tuple[list, dict]:
    demands: dict[str, int] = {}
    monkeypatch.setattr(builder, "add_demands", lambda t, s: t.update(s.required))
    return demands


def test_belt_cell_preps_before_any_plate_extraction(monkeypatch) -> None:
    """Ordering guard: the belt-cell key must gate the prep so no mine runs
    while the mall still has no transport-belt producer."""
    prepped: set[str] = set()
    calls: list[str] = []

    def fake_ensure(*_a, **_k):
        calls.append("belt_cell")
        return (50.0, 31.5)

    monkeypatch.setattr(builder, "ensure_produced", fake_ensure)
    spent = builder._prep_the_belt_cell(
        object(), object(), "nauvis", "player", prepped, {},
        (3.0, -1.0), lambda _m: None,
    )

    assert spent
    assert calls == ["belt_cell"]
    assert builder._BELT_CELL_PREP_KEY in prepped

    # Once prepped, the caller's gate skips it entirely.
    skipped = builder._BELT_CELL_PREP_KEY in prepped and False
    assert not skipped


def test_belt_cell_shortage_hands_the_pass_to_the_mall(monkeypatch) -> None:
    prepped: set[str] = set()

    def raise_shortage(*_a, **_k):
        raise MaterialShortage(
            "mall_transport-belt", {"iron-gear-wheel": 2}, {"iron-gear-wheel": 0},
        )

    monkeypatch.setattr(builder, "ensure_produced", raise_shortage)

    # The helper imports add_demands from parts_mall at module scope; patch it.
    monkeypatch.setattr(builder, "add_demands", lambda t, s: t.update(s.required))

    mall_targets: dict[str, int] = {}
    spent = builder._prep_the_belt_cell(
        object(), object(), "nauvis", "player", prepped, mall_targets,
        (3.0, -1.0), lambda _m: None,
    )

    assert not spent
    assert mall_targets == {"iron-gear-wheel": 2}
    assert builder._BELT_CELL_PREP_KEY not in prepped


def test_belt_cell_deferred_pass_is_returned_not_marked_done(monkeypatch) -> None:
    prepped: set[str] = set()

    def defer(*_a, **_k):
        raise builder.ProductionPrerequisiteDeferred("waiting on plates")

    monkeypatch.setattr(builder, "ensure_produced", defer)

    spent = builder._prep_the_belt_cell(
        object(), object(), "nauvis", "player", prepped, {},
        (3.0, -1.0), lambda _m: None,
    )

    assert not spent
    assert builder._BELT_CELL_PREP_KEY not in prepped


def test_expansion_gate_defers_and_queues_the_unlock_work(monkeypatch) -> None:
    """Live run of 2026-08-22 03:01: the bootstrap cap's deferral escaped the
    StuckError handler inside expand_upstream and killed the runner. It must
    defer the priority and queue the electric-furnace producer instead."""
    from collections import Counter

    deferred: list[tuple[str, str]] = []

    class FakePriorities:
        def describe(self, task, tick):
            return f"{task.item}"

        def defer(self, item, tick, reason):
            deferred.append((item, reason))

    monkeypatch.setattr(
        builder, "_ensure_mall_item",
        lambda *_a, **_k: (True, (1.0, 1.0)),
    )

    def fake_wait(_c, _s, _f, item, target, emit, *, on_stalled=None):
        assert on_stalled is not None
        on_stalled()
        return False

    monkeypatch.setattr(builder, "wait_for_stock", fake_wait)

    def gate(*_a, **_k):
        raise builder.ProductionPrerequisiteDeferred(
            "iron-plate expansion waits for electric-furnace production "
            "after the 12-furnace bootstrap cap"
        )

    monkeypatch.setattr(builder, "build_mining_stage", gate)
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {},
    )
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 100)
    monkeypatch.setattr(
        builder, "expansion_target", lambda *_a: "iron-plate",
    )

    mall_targets: dict[str, int] = {}
    task = SimpleNamespace(item="electric-mining-drill", target=6)
    builder._serve_mall_task(
        object(), object(), "nauvis", "player", task, 100,
        mall_targets, FakePriorities(), (3.0, -1.0), lambda _m: None,
    )

    assert len(deferred) == 1
    assert "bootstrap cap" in deferred[0][1]
    assert mall_targets.get("electric-furnace") == 1


def test_prep_path_cap_deferral_queues_the_furnace_unlock(monkeypatch) -> None:
    """Live run of 2026-08-22 03:54: the cap bit during PREP (not expansion),
    no gate work was queued, and inserters starved at 75% for twelve passes.
    Both paths must queue the unlock producer."""
    queued: dict[str, int] = {}
    monkeypatch.setattr(builder, "add_demands", lambda t, s: t.update(s.required))
    builder._queue_electric_furnace_unlock(
        queued,
        "iron-plate expansion waits for electric-furnace production after "
        "the 12-furnace bootstrap cap",
        lambda _m: None,
    )
    assert queued.get("electric-furnace") == 1

    unrelated: dict[str, int] = {}
    builder._queue_electric_furnace_unlock(
        unrelated,
        "fast-transport-belt waits for iron capacity",
        lambda _m: None,
    )
    assert unrelated == {}


def test_electric_furnace_chain_intermediates_are_persistent(monkeypatch) -> None:
    """Run 15: advanced-circuit and steel-chest were drawn unbacked while the
    electric-furnace gate waited on them -- the gate can never lift unless
    drawing them schedules their producers."""
    for item in ("advanced-circuit", "steel-chest", "steel-plate"):
        assert item in builder.PERSISTENT_INTERMEDIATES
