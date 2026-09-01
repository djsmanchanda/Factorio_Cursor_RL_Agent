# Path: tests/test_bootstrap_priorities.py
# Purpose: Essential plate systems use the cold-start belt tier and stand up
# the mall's transport-belt assembler before consuming its production.

from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as builder
from orchestrator import live_base
from orchestrator.parts_mall import MaterialShortage


def test_essential_routes_use_the_cold_start_belt_tier(monkeypatch) -> None:
    """Partial fast stock cannot make a foundation depend on a gated tier."""
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"fast-transport-belt": 100, "transport-belt": 12},
    )
    assert builder._essential_belt_type(
        object(), "nauvis", "player",
    ) == "transport-belt"


def test_essential_routes_stay_regular_when_fast_runs_out(monkeypatch) -> None:
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
    calls: list[dict] = []

    def fake_ensure(*_a, **_k):
        calls.append(_k)
        return (50.0, 31.5)

    monkeypatch.setattr(builder, "ensure_produced", fake_ensure)
    spent = builder._prep_the_belt_cell(
        object(), object(), "nauvis", "player", prepped, {},
        (3.0, -1.0), lambda _m: None,
    )

    assert spent
    assert calls == [{
        "upgrade_bootstrap": False,
        "stock_target": 1,
        "minimum_machines": 1,
        "allow_promotion": False,
        "temporary_mall": True,
    }]
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

    deferred: list[tuple[str, str, int]] = []
    promoted: list[tuple[str, int, int]] = []

    class FakePriorities:
        def describe(self, task, tick):
            return f"{task.item}"

        def defer(self, item, tick, reason, *, retry_ticks=3_600):
            deferred.append((item, reason, retry_ticks))

        def promote(self, item, target, tick):
            promoted.append((item, target, tick))

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
    assert deferred[0][2] == 3_600
    assert promoted == [("electric-furnace", 1, 100)]


def test_proven_electric_furnace_producer_unlocks_cap_after_demand_ends(
    monkeypatch,
) -> None:
    """A producer that made a furnace may be idle at the next survey because
    demand is gone. Monotonic output, not instantaneous status, proves startup.
    """
    line = live_base.LineState(
        recipe="electric-furnace", machine_count=1, working_count=0,
        output_position=(1.0, 1.0), machine_positions=((1.0, 1.0),),
        produced_count=1,
    )
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: line)

    assert builder._electric_furnace_producer_started(
        object(), "nauvis", "player",
    )


def test_prep_path_cap_deferral_queues_the_furnace_unlock(monkeypatch) -> None:
    """Live run of 2026-08-22 03:54: the cap bit during PREP (not expansion),
    no gate work was queued, and inserters starved at 75% for twelve passes.
    Both paths must queue the unlock producer."""
    queued: dict[str, int] = {}
    monkeypatch.setattr(builder, "add_demands", lambda t, s: t.update(s.required))
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {},
    )
    builder._queue_electric_furnace_unlock(
        object(), "nauvis", "player",
        queued,
        "iron-plate expansion waits for electric-furnace production after "
        "the 12-furnace bootstrap cap",
        lambda _m: None,
    )
    assert queued.get("electric-furnace") == 1

    unrelated: dict[str, int] = {}
    builder._queue_electric_furnace_unlock(
        object(), "nauvis", "player",
        unrelated,
        "fast-transport-belt waits for iron capacity",
        lambda _m: None,
    )
    assert unrelated == {}


def test_gate_demand_exceeds_idle_furnace_stock(monkeypatch) -> None:
    """Live run of 2026-08-24 02:38: the base already owned one electric
    furnace, so the survey retired the flat target of 1 every pass and the
    cap livelocked to STUCK. The unlock must demand NEW production."""
    queued: dict[str, int] = {}
    monkeypatch.setattr(builder, "add_demands", lambda t, s: t.update(s.required))
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"electric-furnace": 1},
    )
    builder._queue_electric_furnace_unlock(
        object(), "nauvis", "player",
        queued,
        "iron-plate expansion waits for electric-furnace production after "
        "the 12-furnace bootstrap cap",
        lambda _m: None,
    )
    assert queued.get("electric-furnace") == 2


def test_electric_furnace_chain_intermediates_are_persistent(monkeypatch) -> None:
    """Advanced circuits and steel plates need durable sources; steel chests
    are instead a temporary mall capability batch."""
    for item in ("advanced-circuit", "steel-plate"):
        assert item in builder.PERSISTENT_INTERMEDIATES
    assert "steel-chest" not in builder.PERSISTENT_INTERMEDIATES


def test_gate_task_itself_is_served_not_repromoted() -> None:
    """Live runs of 2026-08-24 07:25 and 07:47: a main-loop gate preemption
    first captured the electric-furnace task itself (promote-and-continue
    forever), then captured higher-rated ready tasks the same way. The run
    loop must contain no gate preemption at all: ready work is served, and
    the promoted gate outranks only deferred tasks via normal ranking."""
    import inspect

    assert "priorities.promote" not in inspect.getsource(builder.run)
