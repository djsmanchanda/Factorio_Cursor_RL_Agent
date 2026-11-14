# Path: tests/test_laya_operation_outcome.py | Purpose: Protect typed receipt distinctions.
import pytest
from training.laya_operation_outcome import normalize, count

@pytest.mark.fast
def test_receipt_states_and_unknowns():
    rows = [{}, {"ok": False}, {"error_type": "ValueError"},
            {"code": "prerequisite_advanced"}, {"code": "material_shortage"},
            {"status": "construction_pending"}, {"status": "observed", "construction": {"pending": 0, "missing": 0}},
            {"code": "producing", "ok": True}, {"code": "done", "ok": True}]
    outcomes = [normalize(r) for r in rows]
    assert [o["state"] for o in outcomes] == ["unknown", "unknown", "fault", "prerequisite_advanced", "deferred", "constructing", "delivered", "producing", "completed"]
    totals = count(outcomes)
    assert totals["observed_decisions"] == 9
    assert sum(totals["states"].values()) == 9
    assert totals["states"]["fault"] == 1

@pytest.mark.fast
def test_done_requires_explicit_consistent_success():
    assert normalize({"code": "done", "ok": False})["state"] == "unknown"
    assert normalize({"code": "done"})["state"] == "unknown"
    assert normalize({"code": "done", "ok": True})["state"] == "completed"
