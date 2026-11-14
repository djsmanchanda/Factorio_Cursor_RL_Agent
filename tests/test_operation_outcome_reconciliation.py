# Path: tests/test_operation_outcome_reconciliation.py | Purpose: Verify receipt joins, unknowns and full accounting denominators.
import json

import pytest

from training.laya_operation_outcome import planned_job_accounting, reconcile_records, summarize_records

pytestmark = pytest.mark.fast


def decision(index, outcome, *, family="assemble", operation_id=None):
    return {"run_id": "one", "index": index, "phase": "C6", "operation_id": operation_id,
            "choice": {"option_id": f"build.{index}", "family": family, "need_id": "advanced-circuit"},
            "outcome": outcome}


def test_pending_construction_joins_its_actual_receipt_without_completing_objective(tmp_path):
    name = "transaction-" + "a" * 32 + ".json"
    rows = [decision(0, {"status": "construction_pending", "transaction_id": name}),
            decision(1, {"status": "construction_pending"})]
    records = tmp_path / "decision-states.jsonl"
    records.write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / name).write_text(json.dumps({"transaction_id": name,
        "candidate": {"option_id": "build.0"}, "status": "observed", "construction": {"pending": 0, "missing": 0}}))
    result = summarize_records(records)
    assert result["initial_outcome_counts"]["states"]["constructing"] == 2
    assert result["outcome_counts"]["states"]["delivered"] == 1
    assert result["outcome_counts"]["states"]["constructing"] == 1
    assert result["outcome_counts"]["states"]["completed"] == 0
    assert result["objective_attempted"] is True


def test_operation_observations_match_the_invocation_and_preserve_initial_faults():
    rows = [decision(0, {"code": "submitted"}, operation_id="first"),
            decision(1, {"error_type": "ValueError"}, operation_id="second")]
    updates = [{"event": "operation_observation", "operation_id": "first",
                "outcome": {"operation": "assemble", "code": "done", "ok": True}},
               {"event": "operation_observation", "operation_id": "other",
                "outcome": {"code": "done", "ok": True}},
               {"event": "operation_observation", "operation_id": "second",
                "outcome": {"operation": "retire", "code": "done", "ok": True}}]
    result = reconcile_records(rows, observations=updates)
    assert result["outcome_counts"]["states"]["completed"] == 1
    assert result["outcome_counts"]["states"]["fault"] == 1
    assert result["initial_outcome_counts"]["states"]["fault"] == 1
    assert result["unmatched_observations"] == 1


def test_duplicate_and_malformed_rows_do_not_inflate_or_disappear(tmp_path):
    row = decision(0, {"code": "done", "ok": True})
    conflict = decision(0, {"code": "done", "ok": False})
    records = tmp_path / "decision-states.jsonl"
    records.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n" + json.dumps(conflict) + "\n{broken\n[]\n")
    result = summarize_records(records)
    assert result["outcome_counts"]["observed_decisions"] == 3
    assert result["outcome_counts"]["states"]["unknown"] == 3
    assert result["duplicate_records"] == 2
    assert result["conflicting_records"] == 1
    assert result["operation_attempts"] == {"attempted": 1, "wait_or_plan": 0,
        "identity_unknown": 2, "definition": "selected_nonwait_operation"}


def test_receipt_with_other_transaction_or_option_cannot_complete_work():
    name = "transaction-" + "a" * 32 + ".json"
    rows = [decision(0, {"status": "construction_pending", "transaction_id": name})]
    for changes in ({"transaction_id": "transaction-" + "b" * 32 + ".json"},
                    {"candidate": {"option_id": "build.8"}}):
        receipt = {"status": "observed", "construction": {"pending": 0, "missing": 0}, **changes}
        result = reconcile_records(rows, receipt_loader=lambda _: receipt)
        assert result["outcome_counts"]["states"]["constructing"] == 1
        assert result["unmatched_observations"] == 1


def test_all_planned_jobs_include_cancelled_failed_and_missing_status():
    planned = [{"id": x} for x in ("success", "cancelled", "fault", "missing")]
    jobs = [{"id": "success", "status": "completed", "objective_attempted": True, "successor_passed": True},
            {"id": "cancelled", "status": "cancelled", "objective_attempted": False},
            {"id": "fault", "status": "infrastructure_failure", "successor_passed": False}]
    result = planned_job_accounting(planned, jobs)
    assert result["planned_jobs"] == sum(result["process_states"].values()) == 4
    assert result["missing_status_jobs"] == ["missing"]
    assert result["objective_attempted_jobs"] == result["objective_completed_jobs"] == 1
    assert result["objective_attempt_unknown_jobs"] == result["objective_result_unknown_jobs"] == 2


def test_wait_is_an_observed_decision_and_legacy_anonymous_outcome_stays_unknown():
    result = reconcile_records([decision(0, {"ok": True}, family="wait"), {"outcome": {}}])
    assert result["outcome_counts"]["observed_decisions"] == 2
    assert result["operation_attempts"]["attempted"] == 0
    assert result["operation_attempts"]["wait_or_plan"] == 1
    assert result["objective_attempted"] is None
