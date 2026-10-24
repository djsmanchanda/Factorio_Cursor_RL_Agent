# Path: tests/test_output_blocked_diagnosis.py | Purpose: Full-output machines are a built, producing line (service condition), while missing, unpowered and never-fed machines stay construction faults.
from orchestrator import stage_services


def _diagnose(monkeypatch, statuses, counters=None):
    positions = [list(p) for p in statuses]
    counters = counters or {p: 0 for p in statuses}
    monkeypatch.setattr(stage_services.live_base, "progress_counters", lambda *_a: dict(counters))
    monkeypatch.setattr(stage_services.live_base, "machine_health",
                        lambda *_a: (dict(statuses), dict(counters)))
    lines = []
    stuck = stage_services._diagnose_machines(
        object(), "nauvis", positions, lines.append, grace_seconds=0, poll_seconds=0)
    return stuck, lines


def test_all_full_output_is_not_a_construction_fault(monkeypatch):
    stuck, lines = _diagnose(monkeypatch, {(0, 0): "full_output", (3, 0): "full_output"})
    assert stuck == []
    assert any("OUTPUT BLOCKED: 2/2" in line for line in lines)


def test_full_output_beside_broken_machine_keeps_the_defect_visible(monkeypatch):
    stuck, lines = _diagnose(monkeypatch, {(0, 0): "full_output", (3, 0): "no_power"})
    assert stuck == []
    assert any("DEFECT" in line and "no_power" in line for line in lines)


def test_missing_unpowered_and_unfed_lines_are_still_stuck(monkeypatch):
    stuck, _ = _diagnose(monkeypatch, {(0, 0): "no_power", (3, 0): "no_ingredients"})
    assert sorted(s for _, s in stuck) == ["no_ingredients", "no_power"]
    stuck, _ = _diagnose(monkeypatch, {(0, 0): "missing"})
    assert stuck == [([0, 0], "missing")]
