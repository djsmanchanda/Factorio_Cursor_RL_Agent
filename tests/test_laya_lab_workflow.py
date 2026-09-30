# Path: tests/test_laya_lab_workflow.py | Purpose: Verify real disposable telemetry feeds branch choices without invented supply.

from __future__ import annotations

import pytest
import json

from orchestrator.laya_state import measure_request
from tests.test_laya_policy import _candidates, _state_builder
from training.laya_branch_decisions import prepare_serve_goal
from training.laya_branch_decisions import BranchDecisionError, resolve_serve_goal
from training.laya_lab_workflow import observe_lab_workflow
from training.laya_workflow_observation import WorkflowObservationError
from training.laya_workflow import LayaWorkflowDirector
from tools.inspect_laya_workflow_report import main as inspect_main


BRANCHES = (
    {"branch_id": "iron", "parent_goal_id": "goal.belts", "item": "iron-plate",
     "metric": "production_rate", "target_minimum": 0.04,
     "required_sustained_ticks": 60},
    {"branch_id": "gears", "parent_goal_id": "goal.belts", "item": "iron-gear-wheel",
     "metric": "production_rate", "target_minimum": 0.02,
     "required_sustained_ticks": 60, "requires": ["iron"]},
    {"branch_id": "belts", "parent_goal_id": "goal.belts", "item": "transport-belt",
     "metric": "production_rate", "target_minimum": 0.02,
     "required_sustained_ticks": 60, "requires": ["gears"]},
    {"branch_id": "construction", "parent_goal_id": "goal.coverage", "item": "medium-electric-pole",
     "metric": "built_count", "target_minimum": 1},
)


def _report(*, iron_rate=0.0, iron_ticks=0, pole_count=0):
    return {
        "surface": "training/workflow-001", "force": "training-workflow-001",
        "status": "running",
        "started_tick": 100, "elapsed_ticks": 120,
        "metrics": {
            "item_production": {"available": True, "items": {
                "iron-plate": {"available": True, "window_rate_per_tick": iron_rate,
                               "window_ticks": iron_ticks, "sustained_ticks": 900},
                "iron-gear-wheel": {"available": True, "window_rate_per_tick": 0,
                                    "window_ticks": 0, "sustained_ticks": 0},
                "transport-belt": {"available": False},
            }},
            "electric_pole_count": pole_count,
            "built_entities": {"medium-electric-pole": 4},  # May include ghosts.
        },
    }


def _state():
    state = _state_builder({}, _candidates())
    state["run"]["surface"] = "training/workflow-001"
    state["run"]["force"] = "training-workflow-001"
    return state


def test_lab_report_keeps_independent_construction_available() -> None:
    graph, supply = observe_lab_workflow(_report(), BRANCHES)
    state = _state()
    prepared, options, _ = prepare_serve_goal(state, graph, supply)
    assert [option["option_id"] for option in options] == ["serve.construction", "serve.iron"]
    assert prepared["observation_digest"]["item_supply"]["transport-belt"]["output_rate"] is None
    assert supply["items"]["iron-plate"]["stock"]["accessible"] is None


def test_ghost_count_cannot_close_construction_and_measured_iron_opens_gears() -> None:
    first, _ = observe_lab_workflow(_report(), BRANCHES)
    assert [branch["branch_id"] for branch in first.branches if branch.status == "closed"] == []
    next_graph, _ = observe_lab_workflow(
        _report(iron_rate=0.05, iron_ticks=120, pole_count=1), BRANCHES, first,
    )
    assert {branch.branch_id for branch in next_graph.branches if branch.status == "closed"} == {
        "iron", "construction",
    }
    assert {branch.branch_id for branch in next_graph.branches if branch.status == "open"} == {"gears"}


def test_lab_evidence_flows_through_model_goal_choice_and_rejects_stale_selection() -> None:
    graph, supply = observe_lab_workflow(_report(), BRANCHES)
    state, options, evidence_hash = prepare_serve_goal(_state(), graph, supply)

    class Predictor:
        def predict(self, _state, questions):
            assert set(questions["serve_goal"]["option_ids"]) == {"serve.construction", "serve.iron"}
            return {"answers": {"serve_goal": {"choice": "serve.construction", "confidence": 0.98}}}

    decision = LayaWorkflowDirector(Predictor()).decide("serve_goal", state, options)
    _, usage = measure_request(decision["state"], {"serve_goal": decision["question"]})
    assert usage.remaining_tokens >= 0
    assert resolve_serve_goal(
        graph, supply, evidence_hash=evidence_hash,
        option_id=decision["selected_option"]["option_id"],
    ) == "construction"
    changed_graph, changed_supply = observe_lab_workflow(_report(pole_count=1), BRANCHES, graph)
    with pytest.raises(BranchDecisionError, match="changed"):
        resolve_serve_goal(changed_graph, changed_supply, evidence_hash=evidence_hash,
                           option_id="serve.construction")

    with pytest.raises(BranchDecisionError, match="another training scope"):
        prepare_serve_goal(_state_builder({}, _candidates()), graph, supply)


def test_lab_bridge_requires_scoped_current_telemetry() -> None:
    report = _report()
    report["surface"] = "nauvis"
    with pytest.raises(WorkflowObservationError):
        observe_lab_workflow(report, BRANCHES)
    report = _report()
    del report["metrics"]["item_production"]
    with pytest.raises(WorkflowObservationError, match="telemetry"):
        observe_lab_workflow(report, BRANCHES)
    report = _report()
    report["status"] = "failed"
    with pytest.raises(WorkflowObservationError, match="active or completed"):
        observe_lab_workflow(report, BRANCHES)


def test_phase_owned_completion_never_uses_total_furnace_count():
    spec = {"branch_id": "iron2", "parent_goal_id": "goal.expand", "item": "electric-furnace",
            "metric": "built_count", "target_minimum": 1, "measurement_scope": "connected_workflow.iron2"}
    report = _report()
    report["metrics"]["live_entities"] = {"electric-furnace": 3}
    graph, _ = observe_lab_workflow(report, [spec])
    assert graph.branches[0].status == "open"  # Old mod: owned evidence is unknown.
    report["metrics"]["live_entities_by_phase"] = {"connected_workflow.copper": {"electric-furnace": 1}}
    graph, _ = observe_lab_workflow(report, [spec])
    assert graph.branches[0].status == "open"
    report["metrics"]["live_entities_by_phase"]["connected_workflow.iron2"] = {"electric-furnace": 1}
    graph, _ = observe_lab_workflow(report, [spec])
    assert graph.branches[0].status == "closed"
    report["metrics"]["live_entities_by_phase"]["connected_workflow.iron2"] = {}
    graph, _ = observe_lab_workflow(report, [spec], graph)
    assert graph.branches[0].status == "open"


def test_report_inspector_exposes_current_branch_evidence(tmp_path, capsys) -> None:
    report_path, branches_path = tmp_path / "report.json", tmp_path / "branches.json"
    report_path.write_text(json.dumps(_report()), encoding="utf-8")
    branches_path.write_text(json.dumps(BRANCHES), encoding="utf-8")
    assert inspect_main(["--report", str(report_path), "--branches", str(branches_path)]) == 0
    projection = json.loads(capsys.readouterr().out)
    assert [branch["branch_id"] for branch in projection["work_graph"]["runnable"]] == [
        "construction", "iron",
    ]
    assert projection["supply_evidence"]["items"]["transport-belt"]["production"]["observed_rate"] is None
