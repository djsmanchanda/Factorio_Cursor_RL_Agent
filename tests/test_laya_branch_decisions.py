# Path: tests/test_laya_branch_decisions.py | Purpose: Verify bounded model-owned branch choice from measured factory evidence.

from __future__ import annotations

import pytest

from orchestrator.laya_state import measure_request
from tests.test_laya_policy import _candidates, _state_builder
from training.laya_branch_decisions import BranchDecisionError, prepare_serve_goal, resolve_serve_goal
from training.laya_supply_evidence import project_supply_evidence
from training.laya_work_graph import (
    Blocker, CompletionMeasurement, CompletionTarget, WorkBranch, WorkGraph,
    reconcile_work_graph,
)
from training.laya_workflow import LayaWorkflowDirector


def _graph() -> WorkGraph:
    return WorkGraph((
        WorkBranch("belts", "goal.transport", CompletionTarget("belt_per_s", 2, 60), requires=("gears",)),
        WorkBranch("gears", "goal.transport", CompletionTarget("gear_per_s", 1, 60), requires=("iron",)),
        WorkBranch("iron", "goal.transport", CompletionTarget("plate_per_s", 4, 60),
                   blocker=Blocker("mine has no power", "power_restored")),
        WorkBranch("construction", "goal.coverage", CompletionTarget("covered_tiles", 100)),
    ))


def _supply() -> dict:
    return project_supply_evidence(
        {
            "transport-belt": {"stock": 8, "production_rate": 0.5, "draw_rate": 1.0, "capacity_rate": 2.0},
            "iron-gear-wheel": {"stock": 4, "production_rate": 0.2, "draw_rate": 0.8, "capacity_rate": 1.0},
            "iron-plate": {"stock": 50, "production_rate": 1.0, "draw_rate": 3.0, "capacity_rate": 4.0},
        },
        committed_bills=[{"bill_id": "coverage", "requirements": {"transport-belt": 20}}],
        reservations=[{"bill_id": "coverage", "item": "transport-belt", "amount": 5}],
        recipes={
            "belt": {
                "output_item": "transport-belt", "output_amount": 2,
                "ingredients": {"iron-gear-wheel": 1, "iron-plate": 1},
                "provenance": "observed-recipe-catalog",
            },
            "gear": {
                "output_item": "iron-gear-wheel", "output_amount": 1,
                "ingredients": {"iron-plate": 2},
                "provenance": "observed-recipe-catalog",
            },
        },
    )


def _state() -> dict:
    return _state_builder({}, _candidates())


def test_blocked_chain_leaves_independent_peer_for_laya_to_choose() -> None:
    graph, supply = _graph(), _supply()
    state, options, evidence_hash = prepare_serve_goal(_state(), graph, supply)
    assert [option["option_id"] for option in options] == ["serve.construction"]
    assert state["observation_digest"]["item_supply"]["transport-belt"]["committed"] == 15
    assert state["observation_digest"]["item_supply"]["iron-plate"]["future_ingredients"] == 22.5
    assert resolve_serve_goal(graph, supply, evidence_hash=evidence_hash, option_id="serve.construction") == "construction"

    class Predictor:
        def predict(self, _state, questions):
            assert list(questions["serve_goal"]["criteria"]) == ["serve.construction"]
            return {"answers": {"serve_goal": {"choice": "serve.construction", "confidence": 0.95}}}

    selected = LayaWorkflowDirector(Predictor()).decide("serve_goal", state, options)
    assert selected["selected_option"]["option_id"] == "serve.construction"
    # The request is bounded before any real checkpoint gains this decision kind.
    measure_request(state, {"serve_goal": selected["question"]})


def test_measured_progress_opens_next_branch_and_stale_choice_is_rejected() -> None:
    graph, supply = _graph(), _supply()
    _, _, old_hash = prepare_serve_goal(_state(), graph, supply)
    graph = reconcile_work_graph(graph, {}, blockers={"iron": None})
    state, options, fresh_hash = prepare_serve_goal(_state(), graph, supply)
    assert [option["option_id"] for option in options] == ["serve.construction", "serve.iron"]
    assert state["provenance"]["state_snapshot_hash"].startswith("sha256:")
    with pytest.raises(BranchDecisionError, match="changed"):
        resolve_serve_goal(graph, supply, evidence_hash=old_hash, option_id="serve.construction")
    graph = reconcile_work_graph(graph, {"iron": CompletionMeasurement("plate_per_s", 4, 60)})
    _, options, _ = prepare_serve_goal(_state(), graph, supply)
    assert [option["option_id"] for option in options] == ["serve.construction", "serve.gears"]


def test_unbounded_or_non_disposable_projection_fails_closed() -> None:
    graph, supply = _graph(), _supply()
    with pytest.raises(BranchDecisionError, match="bounded"):
        prepare_serve_goal(_state(), graph, supply, max_items=2)
    state = _state()
    state["run"]["runtime"] = "real_nauvis"
    with pytest.raises(BranchDecisionError, match="disposable"):
        prepare_serve_goal(state, graph, supply)
