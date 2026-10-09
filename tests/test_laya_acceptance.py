# Path: tests/test_laya_acceptance.py
# Purpose: Protect checkpoint evidence, monotonic reach and telemetry uncertainty.

import json
from pathlib import Path

from orchestrator.checkpoint_milestones import CheckpointEvaluator, FactorySnapshot
from orchestrator.checkpoint_observer import StructuredObservation, ObservationUnsupported
from training.laya_acceptance import acceptance_packet, summary_details
from training.laya_ops import DeterministicSession


def observation(*, working=True, tick=100, idle_status="full_output"):
    raw = {"tick": tick, "tick_end": tick + 2, "base_valid": True,
           "starters": {n: {"present": True, "healthy": True, "producing": True, "output_count": 8}
                        for n in ("iron", "copper", "stone")},
           "mall_assemblers": [{"recipe": r, "working": working, "healthy": True, "producing": working,
                                "craft_count": 7, "status": "working" if working else idle_status,
                                "position": {"x": i, "y": 0}}
                               for i, r in enumerate(("iron-gear-wheel", "copper-cable", "electronic-circuit", "transport-belt"))]}
    return StructuredObservation(FactorySnapshot.from_mapping(raw), "2026-10-08T00:00:00Z", "fixture", raw)


def packet(obs, evaluator=None):
    return acceptance_packet(obs, evaluator or CheckpointEvaluator(), baseline=obs.snapshot, history=[obs.snapshot],
                             scope={"episode_id": "test", "surface": "nauvis", "force": "player", "server_root": "/fixture"})


def test_all_current_conditions_and_raw_source_survive_export():
    observed = observation()
    exported = packet(observed)
    result = exported["results"]["C1"]
    assert result["predicate_version"] == "c1-v2"
    assert len(result["evidence"]["checks"]) == 6
    assert all(result["evidence"]["checks"].values())
    assert exported["statuses"]["C1"] == "passed"
    assert exported["observation"]["snapshot"]["mall_assemblers"][0]["position"] == {"x": 0, "y": 0}
    exported["observation"]["snapshot"]["starters"]["iron"]["output_count"] = 0
    assert observed.raw["starters"]["iron"]["output_count"] == 8
    assert json.loads(json.dumps(exported)) == exported


def test_stocked_idle_cells_do_not_retract_reach_or_fabricate_current_pass():
    evaluator = CheckpointEvaluator()
    assert packet(observation(), evaluator)["statuses"]["C1"] == "passed"
    later = packet(observation(working=False, tick=200, idle_status="no_power"), evaluator)
    result = later["results"]["C1"]
    assert later["statuses"]["C1"] == result["latched_status"] == "passed"
    assert result["status"] == "failed"
    assert result["evidence"]["checks"]["at_least_four_working_mall_assemblers"] is False
    summary = summary_details(later, "/fixture/sample.json")
    assert summary["tick"] == 200 and summary["tick_end"] == 202
    assert summary["results"]["C1"]["checks"] == result["evidence"]["checks"]
    assert summary["results"]["C1"]["working_mall_count"] == 0


def test_retired_c1_v1_is_reported_beside_the_active_c1_v2():
    # Two cells run, two sit idle on full output: c1-v2 counts four, c1-v1 counts two.
    raw = {"tick": 100, "tick_end": 102, "base_valid": True,
           "starters": {n: {"present": True, "healthy": True, "producing": True, "output_count": 8}
                        for n in ("iron", "copper", "stone")},
           "mall_assemblers": [
               {"recipe": r, "working": i < 2, "healthy": True, "producing": i < 2,
                "craft_count": 7, "status": "working" if i < 2 else "full_output", "position": {"x": i, "y": 0}}
               for i, r in enumerate(("iron-gear-wheel", "copper-cable", "electronic-circuit", "transport-belt"))]}
    obs = StructuredObservation(FactorySnapshot.from_mapping(raw), "2026-10-08T00:00:00Z", "fixture", raw)
    exported = packet(obs)

    active = exported["results"]["C1"]
    assert active["predicate_version"] == "c1-v2" and active["status"] == "passed"
    assert active["evidence"]["working_mall_count"] == 4
    compact = summary_details(exported, "/fixture/sample.json")
    assert compact["results"]["C1"]["working_mall_count"] == 4
    legacy = exported["predicate_comparisons"]["C1"]["c1-v1"]
    assert legacy["predicate_version"] == "c1-v1" and legacy["status"] == "failed"
    assert legacy["evidence"]["working_mall_count"] == 2
    assert legacy["evidence"]["checks"]["at_least_four_working_mall_assemblers"] is False
    # The active milestone list is unchanged: only c1-v2 is registered.
    assert all(d.predicate_version != "c1-v1" for d in CheckpointEvaluator().milestones if d.checkpoint_id == "C1")


def test_missing_evidence_is_null_and_unknown_not_a_missing_factory():
    raw = {"tick": 1}
    obs = StructuredObservation(FactorySnapshot.from_mapping(raw), "fixture", "fixture", raw)
    result = packet(obs)["results"]["C1"]
    assert result["status"] == "unknown"
    assert all(v is None for v in result["evidence"]["checks"].values())
    assert set(result["missing"]) == set(result["evidence"]["checks"])


def session(tmp_path):
    s = DeterministicSession.__new__(DeterministicSession)
    s.client = s.bridge = object()
    s.episode_id = "episode-test"
    s.script_output = tmp_path / "script-output"
    s.reference_point = (0, 0)
    s.mall_targets = {"transport-belt": 10}
    s.economics = lambda: {"loans": [], "projects": {}}
    return s


def test_failed_survey_clears_previous_details(monkeypatch, tmp_path):
    from orchestrator.checkpoint_observer import LiveCheckpointObservationAdapter
    monkeypatch.setattr(LiveCheckpointObservationAdapter, "observe", lambda *_: observation())
    s = session(tmp_path)
    assert s.acceptance()["C1"] == "passed"
    assert s.acceptance_details["mall_targets"] == {"transport-belt": 10}

    def unsupported(*_):
        raise ObservationUnsupported("no new telemetry")

    monkeypatch.setattr(LiveCheckpointObservationAdapter, "observe", unsupported)
    assert s.acceptance() == {"error": "no new telemetry"}
    assert s.acceptance_details is None


def test_tooling_failure_keeps_valid_predicate_evidence(monkeypatch, tmp_path):
    from orchestrator.checkpoint_observer import LiveCheckpointObservationAdapter
    monkeypatch.setattr(LiveCheckpointObservationAdapter, "observe", lambda *_: observation())
    s = session(tmp_path)

    def unavailable():
        raise RuntimeError("loan survey unavailable")

    s.economics = unavailable
    assert s.acceptance()["C1"] == "passed"
    assert "unavailable" in s.acceptance_details["tooling"]["error"]


def test_new_evidence_matches_versioned_contract():
    from jsonschema import Draft202012Validator
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/laya_acceptance.schema.json").read_text())
    Draft202012Validator(schema).validate(packet(observation()))


def test_report_keeps_missing_samples_and_unknown_checks_distinct():
    from tools.laya_experiment import score
    result = {"predicate_version": "c1-v1", "checks": {"stone_starter_ready": None}}
    rows = [{"summary": {"acceptance": {"C1": "unknown"}, "acceptance_details": {"results": {"C1": result}}}},
            {"summary": {"acceptance": {"error": "unavailable"}}},
            {"summary": {}}]
    reported = score(rows, "C1")
    assert reported["acceptance_checked"] == 1
    assert reported["c1_observed"]["conditions"]["stone_starter_ready"] == {
        "passed": 0, "failed": 0, "unknown": 1, "not_sampled": 2}


def test_failed_loan_observation_keeps_its_cause(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from orchestrator import autonomous_builder as builder
    from orchestrator import mall_bootstrap
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", SimpleNamespace(projects={}))
    monkeypatch.setattr(builder, "_BOOTSTRAP_DISTRICT_LEDGER", SimpleNamespace(states=lambda: (
        SimpleNamespace(recipe="copper-plate", lifecycle_state="provisioning", replacement_submitted=True),)))

    def failed(*args):
        raise ValueError("malformed loan group")

    monkeypatch.setattr(mall_bootstrap, "active_bootstrap_loans", failed)
    s = session(tmp_path)
    del s.economics  # Exercise the production observer, rather than the fixture's stub.
    result = s.economics()
    assert result["loans"] is None
    assert result["loan_error"] == "ValueError: malformed loan group"
    assert result["bootstrap_districts"] == {
        "copper-plate": {"lifecycle_state": "provisioning", "replacement_submitted": True}}
    from orchestrator.checkpoint_observer import LiveCheckpointObservationAdapter
    monkeypatch.setattr(LiveCheckpointObservationAdapter, "observe", lambda *_: observation())
    assert s.acceptance()["C1"] == "passed"
    assert s.acceptance_details["tooling"] == result
