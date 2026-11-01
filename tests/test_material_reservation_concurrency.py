# Path: tests/test_material_reservation_concurrency.py | Purpose: Prevent stale ledger writers from overwriting reservations.

import multiprocessing
import json
from pathlib import Path

import pytest

from orchestrator.material_reservations import MaterialReservationError, MaterialReservationLedger


def _ledger(root):
    return MaterialReservationLedger(Path(root) / "script-output", episode_id="episode-1", surface="nauvis", force="player")


def _competing_writer(root, name, ready, start, results):
    ledger = _ledger(root)
    ready.put(name)
    if not start.wait(20):
        results.put((name, "timed_out"))
        return
    try:
        ledger.declare(name, {"pipe": 1}, {"pipe": 2}, hold_until_producing=True)
        results.put((name, "saved"))
    except MaterialReservationError as error:
        results.put((name, "stale" if "changed since it was loaded" in str(error) else str(error)))


def test_concurrent_processes_cannot_overwrite_each_others_ledger(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
    processes = [ctx.Process(target=_competing_writer, args=(str(tmp_path), name, ready, start, results))
                 for name in ("first", "second")]
    for process in processes:
        process.start()
    try:
        assert {ready.get(timeout=20), ready.get(timeout=20)} == {"first", "second"}
        start.set()
        outcomes = dict(results.get(timeout=20) for _ in processes)
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0
        assert sorted(outcomes.values()) == ["saved", "stale"]
        ledger = _ledger(tmp_path)
        assert set(ledger.projects) == {name for name, outcome in outcomes.items() if outcome == "saved"}
        loser = next(name for name, outcome in outcomes.items() if outcome == "stale")
        ledger.declare(loser, {"pipe": 1}, {"pipe": 2}, hold_until_producing=True)
        assert set(_ledger(tmp_path).projects) == {"first", "second"}
    finally:
        start.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)


def test_stale_refresh_does_not_overwrite_and_requires_reload(tmp_path):
    seed = _ledger(tmp_path)
    seed.declare("first", {"pipe": 2}, {"pipe": 2}, hold_until_producing=True)
    stale, writer = _ledger(tmp_path), _ledger(tmp_path)
    before_projects, before_revision = dict(stale.projects), stale.revision
    writer.declare("second", {"pipe": 1}, {"pipe": 3}, hold_until_producing=True)
    current = writer.path.read_bytes()
    with pytest.raises(MaterialReservationError, match="changed since it was loaded"):
        stale.shortage_targets("first", {"pipe": 0})
    assert stale.projects == before_projects and stale.revision == before_revision
    assert writer.path.read_bytes() == current
    with pytest.raises(MaterialReservationError, match="stale"):
        stale.allocatable_stock({"pipe": 3})
    with pytest.raises(MaterialReservationError, match="stale"):
        stale.declare("third", {"pipe": 1}, {"pipe": 4})


def test_failed_atomic_replace_restores_in_memory_state(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    ledger.declare("first", {"pipe": 1}, {"pipe": 2}, hold_until_producing=True)
    before_projects, before_revision, before_file = dict(ledger.projects), ledger.revision, ledger.path.read_bytes()
    def fail_replace(_source, _target):
        raise OSError("simulated replace failure")
    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        ledger.declare("second", {"pipe": 1}, {"pipe": 2}, hold_until_producing=True)
    assert ledger.projects == before_projects and ledger.revision == before_revision
    assert ledger.path.read_bytes() == before_file
    assert not list(ledger.path.parent.glob("*.tmp"))


@pytest.mark.parametrize("bad", [True, 1.5, "1", -1])
def test_invalid_persisted_revision_is_not_coerced(tmp_path, bad):
    ledger = _ledger(tmp_path)
    ledger.declare("first", {"pipe": 1}, {"pipe": 1}, hold_until_producing=True)
    payload = json.loads(ledger.path.read_text())
    payload["revision"] = bad
    ledger.path.write_text(json.dumps(payload))
    with pytest.raises(MaterialReservationError, match="revision"):
        _ledger(tmp_path)
