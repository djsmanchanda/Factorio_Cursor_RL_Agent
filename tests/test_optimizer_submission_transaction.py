# Path: tests/test_optimizer_submission_transaction.py | Purpose: Verify journaled, never-resent optimizer submission bookkeeping.

import json

import pytest

from orchestrator import optimizer_submission_transaction as tx
from orchestrator.optimizer_submission_transaction import (OptimizerSubmissionError, OptimizerSubmissionUncertain,
                                                           journal_path, recover_optimizer_transaction,
                                                           submit_optimizer_transaction)
from tests.test_optimizer_reservations import _ledger, bound  # noqa: F401 - fixture
from tests.test_optimizer_submission_preview import staged
from tests.test_silo_component_link import bundle_case  # noqa: F401 - fixture

EPISODE = "synthetic-episode"


class FakeSubmitter:
    """Stands in for a runtime that stores a correlated receipt; never touches Factorio."""

    def __init__(self, mode="ok"):
        self.mode, self.sent, self.reads, self.receipts = mode, [], [], {}

    def submit(self, envelope):
        self.sent.append(envelope)
        if self.mode == "lost_before_runtime":
            raise TimeoutError("no response")
        receipt = {"transaction_id": envelope["transaction_id"], "request_id": envelope["request_id"],
                   "request_sha256": envelope["request_sha256"], "ok": True, "placed": 3}
        if self.mode == "uncorrelated":
            receipt["request_id"] = "f" * 32
        self.receipts[envelope["request_id"]] = receipt
        if self.mode == "lost_response":
            raise TimeoutError("response lost after runtime stored its receipt")
        return receipt

    def read_receipt(self, envelope):
        self.reads.append(envelope["request_id"])
        return self.receipts.get(envelope["request_id"])


@pytest.fixture
def held(bound, tmp_path):
    return staged(bound, tmp_path)


def _submit(bound, ledger, submitter, revision=None):
    return submit_optimizer_transaction(bound, ledger, episode_id=EPISODE, submitter=submitter,
                                        expected_ledger_revision=ledger.revision if revision is None else revision)


def _journal(ledger, bound):
    return json.loads(journal_path(ledger, bound.plan.binding.transaction_id).read_text())


def _assert_hold_kept(ledger, bound, before):
    assert ledger.path.read_bytes() == before
    project = _ledger(ledger.path.parents[3]).projects["optimizer:silo"]
    assert project.state == "ready" and project.reserved == dict(bound.plan.required_materials)


def _fail_write(monkeypatch, failing_call):
    real, calls = tx._write, []
    def write(path, record):
        calls.append(record["attempts"][-1]["status"])
        if len(calls) == failing_call:
            raise OSError("injected journal write failure")
        real(path, record)
    monkeypatch.setattr(tx, "_write", write)


def test_success_journals_exact_correlated_receipt_and_keeps_hold(held):
    bound, ledger = held
    before = ledger.path.read_bytes()
    submitter = FakeSubmitter()
    record = _submit(bound, ledger, submitter)
    attempt = record["attempts"][0]
    assert attempt["status"] == "received" and attempt["receipt"]["placed"] == 3
    assert record["artifact_sha256"] == bound.artifact_sha256
    assert attempt["ledger_revision"] == ledger.revision and attempt["funding"]["reserved"] == attempt["funding"]["required"]
    envelope = submitter.sent[0]
    assert envelope["authorization"]["approved_actions"] == []  # No authority is invented.
    assert all(action["optimizer_ownership"]["optimizer_transaction"] == "a" * 32
               for phase in envelope["build_plan"]["phases"] for action in phase["actions"])
    assert _journal(ledger, bound) == record
    _assert_hold_kept(ledger, bound, before)
    with pytest.raises(OptimizerSubmissionError, match="already has a sent"):
        _submit(bound, ledger, submitter)
    assert len(submitter.sent) == 1


def test_stale_caller_revision_or_competing_funding_refuses_before_journal(held, tmp_path):
    bound, ledger = held
    submitter = FakeSubmitter()
    with pytest.raises(OptimizerSubmissionError, match="funded view"):
        _submit(bound, ledger, submitter, revision=ledger.revision - 1)
    competitor = _ledger(tmp_path)
    competitor.declare("competitor", {"transport-belt": 1}, dict(bound.plan.required_materials),
                       priority=90, hold_until_producing=True)
    with pytest.raises(OptimizerSubmissionError, match="not fully held"):
        _submit(bound, _ledger(tmp_path), submitter)
    assert not submitter.sent and not journal_path(ledger, "a" * 32).exists()


def test_competing_ledger_write_after_prepare_is_not_sent_then_fresh_attempt(held, tmp_path, monkeypatch):
    bound, ledger = held
    real = tx._write
    def write_then_compete(path, record):
        real(path, record)
        if record["attempts"][-1]["status"] == "prepared" and len(record["attempts"]) == 1:
            _ledger(tmp_path).declare("competitor", {"transport-belt": 1}, dict(bound.plan.required_materials),
                                      hold_until_producing=True)
    monkeypatch.setattr(tx, "_write", write_then_compete)
    submitter = FakeSubmitter()
    with pytest.raises(OptimizerSubmissionError, match="nothing was sent") as caught:
        _submit(bound, ledger, submitter)
    assert not isinstance(caught.value, OptimizerSubmissionUncertain) and not submitter.sent
    assert _journal(ledger, bound)["attempts"][0]["status"] == "not_sent"
    reloaded = _ledger(tmp_path)
    record = _submit(bound, reloaded, submitter)
    assert [a["status"] for a in record["attempts"]] == ["not_sent", "received"]
    assert record["attempts"][1]["ledger_revision"] == reloaded.revision > record["attempts"][0]["ledger_revision"]
    assert len(submitter.sent) == 1


def test_lost_response_is_recovered_by_read_without_resend(held):
    bound, ledger = held
    before = ledger.path.read_bytes()
    submitter = FakeSubmitter("lost_response")
    with pytest.raises(OptimizerSubmissionUncertain):
        _submit(bound, ledger, submitter)
    assert _journal(ledger, bound)["attempts"][0]["status"] == "submitting"
    with pytest.raises(OptimizerSubmissionError, match="unrecovered"):
        _submit(bound, ledger, submitter)
    record = recover_optimizer_transaction(ledger, "a" * 32, submitter)
    assert record["attempts"][0]["status"] == "recovered" and record["attempts"][0]["receipt"]["ok"] is True
    assert len(submitter.sent) == 1 and len(submitter.reads) == 1
    _assert_hold_kept(ledger, bound, before)


def test_unknown_outcome_without_receipt_stays_uncertain_and_held(held):
    bound, ledger = held
    before = ledger.path.read_bytes()
    submitter = FakeSubmitter("lost_before_runtime")
    with pytest.raises(OptimizerSubmissionUncertain):
        _submit(bound, ledger, submitter)
    for _ in range(2):
        with pytest.raises(OptimizerSubmissionUncertain, match="remains unknown"):
            recover_optimizer_transaction(ledger, "a" * 32, submitter)
    assert _journal(ledger, bound)["attempts"][0]["status"] == "submitting"
    assert len(submitter.sent) == 1
    _assert_hold_kept(ledger, bound, before)


def test_uncorrelated_receipt_is_terminal_ambiguity(held):
    bound, ledger = held
    submitter = FakeSubmitter("uncorrelated")
    with pytest.raises(OptimizerSubmissionUncertain, match="not correlated"):
        _submit(bound, ledger, submitter)
    attempt = _journal(ledger, bound)["attempts"][0]
    assert attempt["status"] == "ambiguous" and attempt["reason"] == "receipt_not_correlated"
    with pytest.raises(OptimizerSubmissionError):
        _submit(bound, ledger, submitter)
    assert len(submitter.sent) == 1


def test_prepared_journal_write_failure_sends_nothing(held, monkeypatch):
    bound, ledger = held
    _fail_write(monkeypatch, 1)
    submitter = FakeSubmitter()
    with pytest.raises(OptimizerSubmissionError, match="nothing was sent"):
        _submit(bound, ledger, submitter)
    assert not submitter.sent and not journal_path(ledger, "a" * 32).exists()


def test_submitting_write_failure_recovers_as_unsent_then_retries(held, monkeypatch):
    bound, ledger = held
    _fail_write(monkeypatch, 2)
    submitter = FakeSubmitter()
    with pytest.raises(OptimizerSubmissionError, match="nothing was sent") as caught:
        _submit(bound, ledger, submitter)
    assert not isinstance(caught.value, OptimizerSubmissionUncertain) and not submitter.sent
    monkeypatch.undo()
    with pytest.raises(OptimizerSubmissionError, match="unrecovered"):
        _submit(bound, ledger, submitter)
    recovered = recover_optimizer_transaction(ledger, "a" * 32, submitter)
    assert recovered["attempts"][0] | {"status": "not_sent"} == recovered["attempts"][0]
    assert recovered["attempts"][0]["reason"] == "interrupted_before_send" and not submitter.reads
    record = _submit(bound, ledger, submitter)
    assert [a["status"] for a in record["attempts"]] == ["not_sent", "received"]
    assert record["attempts"][0]["request_id"] != record["attempts"][1]["request_id"]


def test_receipt_journal_write_failure_recovers_from_receipt(held, monkeypatch):
    bound, ledger = held
    _fail_write(monkeypatch, 3)
    submitter = FakeSubmitter()
    with pytest.raises(OptimizerSubmissionUncertain, match="never by resending"):
        _submit(bound, ledger, submitter)
    monkeypatch.undo()
    assert _journal(ledger, bound)["attempts"][0]["status"] == "submitting"
    record = recover_optimizer_transaction(ledger, "a" * 32, submitter)
    assert record["attempts"][0]["status"] == "recovered" and len(submitter.sent) == 1


def test_tampered_request_or_foreign_journal_is_rejected(held, tmp_path):
    bound, ledger = held
    submitter = FakeSubmitter("lost_response")
    with pytest.raises(OptimizerSubmissionUncertain):
        _submit(bound, ledger, submitter)
    path = journal_path(ledger, "a" * 32)
    record = json.loads(path.read_text())
    record["attempts"][0]["request"]["build_plan"]["phases"][0]["actions"].pop()
    path.write_text(json.dumps(record))
    with pytest.raises(OptimizerSubmissionError, match="digest mismatch"):
        recover_optimizer_transaction(ledger, "a" * 32, submitter)
    assert not submitter.reads


def test_in_memory_funding_that_differs_from_disk_is_not_sent(held):
    bound, ledger = held
    payload = json.loads(ledger.path.read_text())
    project = next(item for item in payload["projects"] if item["project_id"] == "optimizer:silo")
    item = next(iter(project["required"]))
    project["reserved"][item], project["state"] = 0, "supply_wait"
    ledger.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    partial = _ledger(ledger.path.parents[3])
    loaded = partial.projects["optimizer:silo"]
    loaded.reserved[item] = loaded.required[item]  # Unchanged bytes, forged full hold in memory.
    object.__setattr__(loaded, "state", "ready")
    submitter = FakeSubmitter()
    with pytest.raises(OptimizerSubmissionError, match="nothing was sent"):
        _submit(bound, partial, submitter)
    assert not submitter.sent
    assert _journal(partial, bound)["attempts"][0]["status"] == "not_sent"
