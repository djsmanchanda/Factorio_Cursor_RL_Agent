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
    assert outcome_cost({"status": "completed", "elapsed_ticks": 3000, "material_cost": 50.0}, 36_000, 10.0) == 3500.0
    target = soft_target({"serve.iron": 3197.0, "wait.15s": 4066.0}, 600.0)
    assert target["serve.iron"] > target["wait.15s"] and abs(sum(target.values()) - 1) < 1e-9


def test_faithful_label_becomes_schema_valid_soft_target_case(tmp_path) -> None:
    import json
    from tests.test_laya_runner import LARGE_BUDGET, MODEL_ONLY_REF
    from tests.test_laya_workflow_runner import WorkflowBridge
    from tools.laya_labels_to_cases import label_to_case
    from training.laya_dataset import iter_cases_from_jsonl
    from training.laya_exploration import ExplorationDirector, ExplorationGate
    from training.laya_runner import LayaPrimaryRunner, LayaRunnerConfig

    scenario = generate_connected_workflow_scenario(7)
    bridge = WorkflowBridge(scenario)
    gate = ExplorationGate(seed=7, epsilon=0.0, allow_model_escalation=False)
    result = LayaPrimaryRunner(
        bridge, scenario, ExplorationDirector(ScriptedPredictor([]), gate, budget_profile=LARGE_BUDGET),
        LayaRunnerConfig(worker_id="w", run_id="cf-base", evidence_dir=tmp_path, max_decisions=40),
        model_ref=MODEL_ONLY_REF, exploration=gate, sleeper=lambda s: setattr(bridge, "tick", bridge.tick + int(s * 60)),
    ).run()
    first = next(json.loads(line) for line in open(result["journal"]) if '"type":"decision"' in line)
    options = [option["option_id"] for option in first["options"]]
    costs = {option: 3000.0 + 100 * index for index, option in enumerate(options) if "escalate" not in option}
    label = {"version": "laya-counterfactual-v1", "rollout": "r", "seed": 7, "scenario_id": scenario["scenario_id"],
             "decision_index": 0, "decision_kind": "serve_goal", "options": options,
             "base_journal": result["journal"], "cost_ticks": costs, "all_alternatives_faithful": True,
             "noise_ticks": 50.0, "soft_target": soft_target(costs, 600.0)}

    case, reason = label_to_case(label)
    assert reason == "" and abs(sum(case["gold"]["serve_goal"]["probabilities"].values()) - 1) < 1e-9
    assert case["gold"]["serve_goal"]["probabilities"]["escalate.stop"] == 0.0
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(case) + "\n")
    assert len(list(iter_cases_from_jsonl(path))) == 1
    assert label_to_case({**label, "all_alternatives_faithful": False}) == (None, "unfaithful alternative")
