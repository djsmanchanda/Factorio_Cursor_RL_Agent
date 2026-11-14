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
def test_collector_reconciles_only_correlated_receipt(tmp_path):
    import json
    from tools.laya_regression_scheduler import collect_result
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    identity = "transaction-" + "a" * 32 + ".json"
    (evidence / identity).write_text(json.dumps({"status": "observed", "construction": {"pending": 0, "missing": 0}}))
    rows = [{"outcome": {"status": "construction_pending", "transaction_id": identity}},
            {"outcome": {"status": "construction_pending"}}, {"outcome": {}},
            {"outcome": {"status": "failed", "error_type": "ValueError"}}]
    (evidence / "decision-states.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    result = collect_result(tmp_path, {"origin": "C0"})["outcome_counts"]
    assert result["observed_decisions"] == 4
    assert result["states"]["delivered"] == result["states"]["constructing"] == result["states"]["unknown"] == result["states"]["fault"] == 1

@pytest.mark.fast
def test_done_requires_explicit_consistent_success():
    assert normalize({"code": "done", "ok": False})["state"] == "unknown"
    assert normalize({"code": "done"})["state"] == "unknown"
    assert normalize({"code": "done", "ok": True})["state"] == "completed"

@pytest.mark.fast
def test_collector_checks_sibling_and_contradictory_envelopes(tmp_path):
    import json
    from tools.laya_regression_scheduler import collect_result
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    claim = normalize({"code": "done", "ok": True}, "build")
    rows = [{"outcome": {"code": "done", "ok": False}, "typed_outcome": claim},
            {"outcome": {"code": "done", "ok": True}, "typed_outcome": claim},
            {"outcome": {"typed_outcome": {"version": 1, "state": "completed", "evidence": {}}}}]
    (evidence / "decision-states.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    states = collect_result(tmp_path, {"origin": "C0"})["outcome_counts"]["states"]
    assert states["unknown"] == 2
    assert states["completed"] == 1


@pytest.mark.fast
def test_collector_preserves_unknown_rows_and_uses_shared_summary(tmp_path):
    import json
    from tools.laya_regression_scheduler import collect_result
    from training.laya_operation_outcome import summarize_records
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    records = evidence / 'decision-states.jsonl'
    row = {'run_id': 'one', 'index': 0, 'choice': {'family': 'wait'}, 'outcome': {'ok': True}}
    records.write_text(json.dumps(row) + '\n' + json.dumps(row) + '\n{broken\n')
    (evidence / 'clean-decisions.jsonl').write_text('[]\n' + json.dumps({'event': 'summary', 'loaded_tick': 1}) + '\n')
    result = collect_result(tmp_path, {'origin': 'C0', 'strict_target': 'C6'})
    assert result['outcome_counts'] == summarize_records(records)['outcome_counts']
    assert result['outcome_counts']['observed_decisions'] == 2
    assert result['duplicate_records'] == 1
    assert result['successor_passed'] is None


@pytest.mark.fast
def test_inherited_current_pass_is_not_an_earned_target(tmp_path):
    import json
    from tools.laya_regression_scheduler import collect_result
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    contract = {'observed_events': [], 'inherited_start': {'predicates': {'C1': {'status': 'passed'}}}}
    rows = [{'event': 'strict_current', 'tick': 20, 'loaded_tick': 10, 'statuses': {'C1': True},
             'predicate_evidence': {'_contract': contract}}]
    (evidence / 'clean-decisions.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    result = collect_result(tmp_path, {'origin': 'C0', 'strict_target': 'C1'})
    assert result['legacy_current_pass_reached'] == {'C1': 20}
    assert result['strict_reached'] == {}
    assert result['objective_eligible'] is False and result['successor_passed'] is None


@pytest.mark.fast
def test_status_publication_counts_every_frozen_planned_job(tmp_path):
    import json
    from tools.laya_regression_scheduler import _publish
    (tmp_path / 'planned-jobs.json').write_text(json.dumps({'jobs': [{'id': 'one'}, {'id': 'missing'}]}))
    (tmp_path / 'job-status.json').write_text(json.dumps({'jobs': [{'id': 'one', 'status': 'cancelled'}]}))
    _publish(tmp_path, lambda _: None)
    result = json.loads((tmp_path / 'job-status.json').read_text())['accounting']
    assert result['planned_jobs'] == 2 and result['missing_status_jobs'] == ['missing']
    assert sum(result['process_states'].values()) == 2
