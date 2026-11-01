# Path: tests/test_laya_saved_world_required_checks.py | Purpose: Required checks must be verified against current world facts at option and execution time.
import copy
import json

import pytest

from tests.test_laya_saved_world import facts
from training.laya_saved_world import (OPTIMIZER_REQUIRED_CHECKS, SavedWorldAdapter, SavedWorldError, compile_candidates,
                                       evaluate_required_checks, required_check_reason)

CHECKS = list(OPTIMIZER_REQUIRED_CHECKS)
PASS = {name: {"reason": None, "facts": {"grid": "7"}} for name in CHECKS}


class Verifier:
    """World stand-in: reads a mutable dict so a test can change the 'world' between phases."""
    def __init__(self, outcome):
        self.outcome, self.prepared, self.calls = outcome, [], 0

    def prepare(self, candidate):
        self.prepared.append(candidate["option_id"])
        return {**candidate, "inspect_power_topology": True}

    def __call__(self, adapter, candidate, entry, response):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return copy.deepcopy(self.outcome)


def candidate_with_checks(checks=CHECKS):
    c = next(c for c in compile_candidates(facts()) if c["family"] == "expand")
    return {**c, "required_checks": list(checks)}


def adapter_for(tmp_path, candidate, monkeypatch, sent=None):
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    monkeypatch.setattr("training.laya_saved_world.compile_candidates", lambda _facts, _seed: [])
    def query(candidates=None, inspect=None):
        if candidates:
            if sent is not None:
                sent.extend(candidates)
            return {"tick": 100, "validations": {candidates[0]["option_id"]: {"bill": candidate.get("bill", {})}}}
        if inspect:
            return {"built": 3, "pending": 0, "missing": 0}
        value = copy.deepcopy(facts()); value["tick"] = clock[0]; return value
    clock = [100]
    adapter.clock = clock
    adapter.query = query
    return adapter


def test_verified_checks_make_the_candidate_legal_and_carry_evidence(tmp_path, monkeypatch):
    candidate = candidate_with_checks()
    sent = []
    adapter = adapter_for(tmp_path, candidate, monkeypatch, sent)
    verifier = Verifier(PASS)
    options, report = adapter.options(facts(), additional_candidates=[candidate], check_verifier=verifier)
    chosen = next(o for o in options if o["option_id"] == candidate["option_id"])
    assert chosen["check_evidence"] == {name: {"grid": "7"} for name in CHECKS}
    assert sent[0]["inspect_power_topology"] is True  # the query itself is asked for the snapshots
    assert "reason" not in report["validations"][candidate["option_id"]]


@pytest.mark.parametrize("outcome,expected", [
    ({**PASS, "safe_infrastructure_attachment": {"reason": "attachment_grid_merge", "facts": {}}}, "attachment_grid_merge"),
    ({**PASS, "fluid_purity": {"reason": "fluid_topology_unverified", "facts": {}}}, "fluid_topology_unverified"),
    ({"fluid_purity": PASS["fluid_purity"]}, "required_check_unimplemented:safe_infrastructure_attachment"),
    ({**PASS, "fluid_purity": {"reason": None}}, "required_check_verifier_error"),
    ({**PASS, "fluid_purity": {"reason": ""}}, "required_check_verifier_error"),
    ([], "required_check_verifier_error"),
    (RuntimeError("boom"), "required_check_verifier_error"),
])
def test_a_failed_or_malformed_verification_prunes_with_a_precise_reason(tmp_path, monkeypatch, outcome, expected):
    candidate = candidate_with_checks()
    adapter = adapter_for(tmp_path, candidate, monkeypatch)
    options, report = adapter.options(facts(), additional_candidates=[candidate], check_verifier=Verifier(outcome))
    assert all(o["option_id"] != candidate["option_id"] for o in options)
    assert report["validations"][candidate["option_id"]]["reason"] == expected
    assert report["validations"][candidate["option_id"]]["required_check_reason"] == expected


def test_unknown_check_names_stay_fail_closed_even_if_a_verifier_claims_them():
    candidate = {"option_id": "x", "required_checks": ["magic"]}
    reason, evidence = evaluate_required_checks(candidate, Verifier({"magic": {"reason": None, "facts": {}}}), None, {}, {})
    assert reason == "required_check_unimplemented:magic" and evidence is None
    assert required_check_reason(candidate, verified={"magic"}) == "required_check_unimplemented:magic"
    assert required_check_reason({"required_checks": CHECKS}) == "required_check_unimplemented:fluid_purity"
    assert required_check_reason({"required_checks": CHECKS}, verified={"fluid_purity"}) == \
        "required_check_unimplemented:safe_infrastructure_attachment"


def test_a_response_cannot_claim_the_checks(tmp_path, monkeypatch):
    candidate = candidate_with_checks()
    adapter = adapter_for(tmp_path, candidate, monkeypatch)
    adapter.query = lambda _c: {"tick": 1, "validations": {candidate["option_id"]: {"bill": {}, "checks": PASS, "fluid_purity": True}}}
    options, report = adapter.options(facts(), additional_candidates=[candidate])
    assert all(o["option_id"] != candidate["option_id"] for o in options)
    assert report["validations"][candidate["option_id"]]["reason"] == "required_check_unimplemented:fluid_purity"


class Bridge:
    def __init__(self, root):
        self.calls, self.root = 0, root

    def optimizer_build_evidence(self, **kwargs):  # placeholder for flows without configuration
        raise AssertionError("no configuration expected")

    def build_layout_correlated(self, _authorization, _plan, *, request_id, **_kwargs):
        self.calls += 1
        return {"ok": True, "request_id": request_id, "tick": 100}


def executable(tmp_path, candidate, monkeypatch, verifier_world):
    adapter = adapter_for(tmp_path, candidate, monkeypatch)
    adapter.validate_plan = lambda _plan: None
    adapter.advance = lambda ticks: adapter.clock.__setitem__(0, adapter.clock[0] + ticks)
    adapter.bridge = Bridge(tmp_path)
    return adapter


def test_execution_rechecks_fresh_facts_and_submits_only_when_unchanged(tmp_path, monkeypatch):
    candidate = {**candidate_with_checks(), "check_evidence": {name: {"grid": "7"} for name in CHECKS}}
    adapter = executable(tmp_path, candidate, monkeypatch, PASS)
    verifier = Verifier(PASS)
    receipt = adapter.execute(candidate["option_id"], [candidate], 300, check_verifier=verifier)
    assert receipt["status"] == "observed" and adapter.bridge.calls == 1 and verifier.calls == 1
    assert receipt["required_check_evidence"] == {name: {"grid": "7"} for name in CHECKS}
    assert verifier.prepared == [candidate["option_id"]]


@pytest.mark.parametrize("fresh,reason", [
    ({**PASS, "safe_infrastructure_attachment": {"reason": None, "facts": {"grid": "8"}}},
     "required_check_facts_changed:safe_infrastructure_attachment"),
    ({**PASS, "fluid_purity": {"reason": "fluid_topology_conflict", "facts": {}}}, "fluid_topology_conflict"),
    ({**PASS, "safe_infrastructure_attachment": {"reason": "attachment_collision", "facts": {}}}, "attachment_collision"),
])
def test_a_change_between_option_and_execution_rejects_before_any_submission(tmp_path, monkeypatch, fresh, reason):
    candidate = {**candidate_with_checks(), "check_evidence": {name: {"grid": "7"} for name in CHECKS}}
    adapter = executable(tmp_path, candidate, monkeypatch, fresh)
    with pytest.raises(SavedWorldError, match=reason):
        adapter.execute(candidate["option_id"], [candidate], 300, check_verifier=Verifier(fresh))
    assert adapter.bridge.calls == 0
    record = json.loads(next((tmp_path / "evidence").glob("transaction-*.json")).read_text())
    assert record["status"] == "rejected" and record["reason"] == reason


def test_execution_requires_option_time_evidence_and_never_skips_the_verifier(tmp_path, monkeypatch):
    candidate = candidate_with_checks()
    adapter = executable(tmp_path, candidate, monkeypatch, PASS)
    with pytest.raises(SavedWorldError, match="required_check_option_evidence_missing"):
        adapter.execute(candidate["option_id"], [candidate], 300, check_verifier=Verifier(PASS))
    assert adapter.bridge.calls == 0
    adapter.query = lambda *_a, **_k: pytest.fail("unverified plan reached live queries")
    with pytest.raises(SavedWorldError, match="required_check_unimplemented:fluid_purity"):
        adapter.execute(candidate["option_id"], [candidate], 300)
