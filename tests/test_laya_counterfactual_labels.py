# Path: tests/test_laya_counterfactual_labels.py
# Purpose: Protect counterfactual replay forcing, divergence detection, replicas, and outcome targets.

from __future__ import annotations

import pytest

from tools.laya_counterfactual_labels import (
    ReplayDiverged, ScriptedPredictor, outcome_cost, replica, rollout_choice, soft_target,
)
from training.scenarios.connected_workflow import generate_connected_workflow_scenario


def _question(ids):
    return {"serve_goal": {"criteria": {option: option for option in ids}}}


def test_forced_prefix_then_rollout_and_divergence() -> None:
    predictor = ScriptedPredictor(["wait.5s"])
    first = predictor.predict({}, _question(["serve.iron", "wait.5s", "escalate.stop"]))
    second = predictor.predict({}, _question(["serve.construction", "serve.iron", "wait.5s"]))
    assert first["answers"]["serve_goal"]["choice"] == "wait.5s"
    # Rollout builds in branch order, never escalates.
    assert second["answers"]["serve_goal"]["choice"] == "serve.iron"
    assert rollout_choice(["wait.1s", "wait.5s", "escalate.stop"]) == "wait.5s"
    with pytest.raises(ReplayDiverged):
        ScriptedPredictor(["serve.gears"]).predict({}, _question(["serve.iron", "wait.5s"]))


def test_replica_keeps_world_but_isolates_names() -> None:
    scenario = generate_connected_workflow_scenario(801)
    copy = replica(scenario, "k0o2")
    assert copy["environment"]["surface_name"].endswith("-k0o2")
    assert copy["fixtures"] == scenario["fixtures"] and copy["objective"] == scenario["objective"]
    assert copy["scenario_hash"] != scenario["scenario_hash"]


def test_unfinished_runs_cost_the_budget_and_targets_prefer_faster() -> None:
    assert outcome_cost({"status": "timed_out", "elapsed_ticks": 900}, 36_000) == 36_000.0
    target = soft_target({"serve.iron": 3197.0, "wait.15s": 4066.0}, 600.0)
    assert target["serve.iron"] > target["wait.15s"] and abs(sum(target.values()) - 1) < 1e-9
