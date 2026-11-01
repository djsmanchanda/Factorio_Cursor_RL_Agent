# Path: tests/test_optimizer_build_reconciliation.py | Purpose: Compare scoped event facts without claiming a build or releasing stock.
import copy
from fractions import Fraction

import pytest

from core.optimizer_artifact_binding import bind_artifact_to_construction_plan
from orchestrator.optimizer_build_reconciliation import compare_optimizer_build_evidence
from orchestrator.optimizer_reservations import reserve_optimizer_artifact
from tests.test_optimizer_reservations import _ledger, bound
from tests.test_silo_component_link import bundle_case


def test_diagnostic_comparison_preserves_unknowns_and_does_not_release(bound, tmp_path):
    bound = bind_artifact_to_construction_plan(
        bound.artifact, scope=bound.plan.binding.scope, project_id="silo", transaction_id="a" * 32,
        throughput_output="rocket-part", throughput_target=Fraction(1, 4), throughput_unit="items/second")
    ledger = _ledger(tmp_path)
    reserve_optimizer_artifact(bound, ledger, episode_id="synthetic-episode",
                               stock=dict(bound.plan.required_materials))
    before = ledger.path.read_bytes()
    refs = bound.plan.required_entities
    assert len(refs) >= 5

    def row(ref, unit):
        return {"entity": ref.entity, "position": {"x": float(ref.x), "y": float(ref.y)},
                "unit_number": unit, "tick": 90,
                "current": {"state": "present", "direction": {"state": "available", "value": ref.direction},
                            "quality": {"state": "available", "value": ref.quality},
                            "recipe": ({"state": "available", "value": ref.recipe}
                                       if ref.recipe else {"state": "unset"})}}
    events = [row(ref, i + 1) for i, ref in enumerate(refs[:4])]
    events[1]["current"]["direction"]["value"] = (refs[1].direction + 1) % 16
    events[2]["current"]["quality"] = {"state": "unavailable"}
    events[3]["current"] = {"state": "not_found"}
    foreign = row(refs[0], 9000)
    foreign["entity"] = "unexpected-entity"
    report = {"version": "optimizer-build-evidence-v2", "source": "robot_built_entity_tags",
              "request_id": "req-1", "tick": 100, "ok": True, "ownership_claimed": False,
              "dispatch_authorized": False, "surface": "test-surface", "force": "test-force",
              "tags": {"optimizer_episode": "synthetic-episode", "optimizer_project": "silo",
                       "optimizer_transaction": "a" * 32, "optimizer_plan_hash": bound.binding_sha256},
              "entities": events + [foreign]}
    result = compare_optimizer_build_evidence(bound, ledger, episode_id="synthetic-episode", report=report)
    assert [item["status"] for item in result["entities"][:5]] == [
        "matching_event", "configuration_mismatch", "unknown", "no_current_record", "no_event"]
    assert result["unexpected_event_count"] == 1
    assert result["ownership_claimed"] is result["construction_complete"] is result["dispatch_authorized"] is False
    assert ledger.path.read_bytes() == before
    stale = compare_optimizer_build_evidence(bound, ledger, episode_id="synthetic-episode", report=report,
                                             submission_tick=91)
    assert stale["entities"][0]["status"] == "before_submission"
    assert stale["submission_tick"] == 91 and stale["construction_complete"] is False
    reused = copy.deepcopy(report)
    reused["entities"][-1]["unit_number"] = events[0]["unit_number"]
    result = compare_optimizer_build_evidence(bound, ledger, episode_id="synthetic-episode", report=reused,
                                              submission_tick=90)
    assert result["entities"][0]["status"] == "unknown"
    for invalid_tick in (True, -1, 101):
        with pytest.raises(ValueError, match="submission tick"):
            compare_optimizer_build_evidence(bound, ledger, episode_id="synthetic-episode", report=report,
                                             submission_tick=invalid_tick)
    ambiguous = copy.deepcopy(report)
    duplicate = copy.deepcopy(events[0]); duplicate["unit_number"] = 999
    ambiguous["entities"].append(duplicate)
    result = compare_optimizer_build_evidence(bound, ledger, episode_id="synthetic-episode", report=ambiguous)
    assert result["entities"][0]["status"] == "unknown"
    ambiguous["entities"][-1]["current"] = {"state": "not_found"}
    result = compare_optimizer_build_evidence(bound, ledger, episode_id="synthetic-episode", report=ambiguous)
    assert result["entities"][0]["status"] == "unknown"
    for mutation in ("scope", "hash", "future", "claim"):
        bad = copy.deepcopy(report)
        if mutation == "scope":
            bad["surface"] = "foreign"
        elif mutation == "hash":
            bad["tags"]["optimizer_plan_hash"] = "c" * 64
        elif mutation == "future":
            bad["entities"][0]["tick"] = 101
        else:
            bad["ownership_claimed"] = True
        with pytest.raises(ValueError):
            compare_optimizer_build_evidence(bound, ledger, episode_id="synthetic-episode", report=bad)
    assert ledger.path.read_bytes() == before


def test_unitless_event_remains_unidentified_in_v3(bound, tmp_path):
    bound = bind_artifact_to_construction_plan(
        bound.artifact, scope=bound.plan.binding.scope, project_id="silo", transaction_id="a" * 32,
        throughput_output="rocket-part", throughput_target=Fraction(1, 4), throughput_unit="items/second")
    ledger = _ledger(tmp_path)
    reserve_optimizer_artifact(bound, ledger, episode_id="synthetic-episode", stock=dict(bound.plan.required_materials))
    ref = bound.plan.required_entities[0]
    report = {"version": "optimizer-build-evidence-v3", "source": "robot_built_entity_tags",
              "request_id": "req-1", "tick": 100, "ok": True, "ownership_claimed": False,
              "dispatch_authorized": False, "surface": "test-surface", "force": "test-force",
              "tags": {"optimizer_episode": "synthetic-episode", "optimizer_project": "silo",
                       "optimizer_transaction": "a" * 32, "optimizer_plan_hash": bound.binding_sha256},
              "entities": [{"entity": ref.entity, "position": {"x": float(ref.x), "y": float(ref.y)},
                            "tick": 90, "current": {"state": "identity_unavailable"}}]}
    before = ledger.path.read_bytes()
    result = compare_optimizer_build_evidence(bound, ledger, episode_id="synthetic-episode", report=report,
                                              submission_tick=90)
    assert result["entities"][0]["status"] == "identity_unavailable"
    assert result["entities"][0]["event_unit_numbers"] == [None]
    assert result["construction_complete"] is False and ledger.path.read_bytes() == before
    report["entities"].append(copy.deepcopy(report["entities"][0]))
    result = compare_optimizer_build_evidence(bound, ledger, episode_id="synthetic-episode", report=report)
    assert result["entities"][0]["status"] == "unknown"
