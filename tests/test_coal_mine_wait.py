# Path: tests/test_coal_mine_wait.py | Purpose: Protect the pending coal mine as a typed construction wait Laya waits on, not an untyped stuck.

import pytest

from orchestrator import stage_chemical
from orchestrator.extraction_state import ResourceMine
from orchestrator.stage_services import StuckError
from training.laya_ops_runner import in_progress


def test_a_pending_coal_mine_is_a_construction_wait(monkeypatch):
    # Batch 2026-10-16 night: 68 untyped "existing coal mine is incomplete" stops in one C4 trial.
    monkeypatch.setattr(stage_chemical, "retire_depleted_mines", lambda *a, **k: None)
    pending = ResourceMine(**{**{f: None for f in ResourceMine.__dataclass_fields__}, "output": (10.5, 20.5), "pending": True})
    monkeypatch.setattr(stage_chemical.extraction_state, "find_resource_mine", lambda *a: pending)
    with pytest.raises(StuckError) as stop:
        stage_chemical.ensure_coal_mine(object(), object(), "nauvis", "player", (0, 0), lambda *a: None, lambda m: None)
    assert stop.value.code == "coal_mine_construction_wait" and stop.value.state == "constructing"
    assert in_progress(stop.value.code)  # Laya waits instead of re-choosing the blocked step
