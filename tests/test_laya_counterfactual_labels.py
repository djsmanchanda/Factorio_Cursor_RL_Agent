# Path: tests/test_laya_counterfactual_labels.py
# Purpose: Protect counterfactual replay forcing, divergence detection, replicas, and outcome targets.

from __future__ import annotations

import pytest

from tools.laya_counterfactual_labels import (
    ReplayDiverged, ScriptedPredictor, outcome_cost, replica, rollout_choice, soft_target,
)
from training.scenarios.connected_workflow import generate_connected_workflow_scenario
from training.laya_counterfactual import LABEL_VERSION, journal_outcome, preference_target


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


def test_policy_continuation_follows_model_but_never_escalates() -> None:
    class Model:
        def predict(self, state, questions):
            return {"answers": {"serve_goal": {"probabilities": {"escalate.stop": 0.9, "wait.1s": 0.02, "wait.5s": 0.08}}}}

    predictor = ScriptedPredictor(["serve.iron"], continuation=Model())
    predictor.predict({}, _question(["serve.iron", "wait.5s"]))
    answer = predictor.predict({}, _question(["serve.gears", "wait.1s", "wait.5s", "escalate.stop"]))
    # After the prefix the policy, not the scripted rollout, picks: its best non-escalation option.
    assert answer["answers"]["serve_goal"]["choice"] == "wait.5s"


def test_replica_keeps_world_but_isolates_names() -> None:
    scenario = generate_connected_workflow_scenario(801)
    copy = replica(scenario, "k0o2")
    assert copy["environment"]["surface_name"].endswith("-k0o2")
    assert copy["fixtures"] == scenario["fixtures"] and copy["objective"] == scenario["objective"]
    assert copy["scenario_hash"] != scenario["scenario_hash"]


def test_unfinished_runs_cost_the_budget_and_targets_prefer_faster() -> None:
    assert outcome_cost({"status": "timed_out", "elapsed_ticks": 900}, 36_000) == 36_000.0
    assert outcome_cost({"status": "completed", "elapsed_ticks": 3000, "material_cost": 50.0}, 36_000, 10.0) == 3500.0
    # Each decision is priced, so twenty 1 s waits cost more than four 5 s waits.
    assert outcome_cost({"status": "completed", "elapsed_ticks": 3000, "decisions": 20}, 36_000, 0.0, 60.0) == 4200.0
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
    outcome = {"status": "completed", "failure_cause": "none", "failure_kind": "none", "reason": "",
               "elapsed_ticks": 3000, "safety_violations": 0, "progress": {}, "journal": result["journal"]}
    label = {"version": LABEL_VERSION, "rollout": "r", "seed": 7, "scenario_id": scenario["scenario_id"],
             "decision_index": 0, "decision_kind": "serve_goal", "options": options,
             "base_journal": result["journal"], "cost_ticks": costs, "all_alternatives_faithful": True,
             "noise_ticks": 50.0, "soft_target": soft_target(costs, 600.0),
             "outcomes": {option: dict(outcome) for option in costs}, "base_repeat_outcomes": [outcome],
             "temperature_ticks": 600.0, "preference_weight": 0.3, "preference_exclusion": ""}

    case, reason = label_to_case(label)
    assert reason == "" and abs(sum(case["gold"]["serve_goal"]["probabilities"].values()) - 1) < 1e-9
    assert case["gold"]["serve_goal"]["probabilities"]["escalate.stop"] == 0.0
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(case) + "\n")
    assert len(list(iter_cases_from_jsonl(path))) == 1
    assert label_to_case({**label, "all_alternatives_faithful": False}) == (None, "unfaithful alternative")
    assert label_to_case({**label, "outcomes": {}})[0] is None
    failed = {**outcome, "status": "timed_out", "failure_cause": "policy", "failure_kind": "timeout"}
    unresolved = {**label, "outcomes": {option: failed for option in costs}, "base_repeat_outcomes": [failed]}
    assert label_to_case(unresolved) == (None, "unresolved all-failed continuation")
    mixed = {**label, "outcomes": {option: failed for option in costs}}
    completed_option = next(iter(costs))
    mixed["outcomes"][completed_option] = outcome
    mixed["cost_ticks"] = {**costs, completed_option: 50000.0}
    case, reason = label_to_case(mixed)
    assert reason == "" and case["gold"]["serve_goal"]["probabilities"][completed_option] == 1.0
    # Previously exported costs-only cases cannot slip back into accumulated training.
    stale = {**case, "label_provenance": {**case["label_provenance"], "notes": "old costs-only label"}}
    path.write_text(json.dumps(stale) + "\n")
    report = {}
    assert list(iter_cases_from_jsonl(path, quality_report=report)) == []
    assert report["excluded"] == 1


def test_all_failed_waits_are_unresolved_and_tool_failures_are_quarantined():
    failed = {"status": "timed_out", "failure_cause": "policy", "safety_violations": 0}
    costs = {"build": 39460.0, "wait": 38460.0}
    assert preference_target(costs, {"build": failed, "wait": failed}, 100, 0) == (
        None, 0.0, "unresolved all-failed continuation")
    completed = {"status": "completed", "failure_cause": "none", "failure_kind": "none", "safety_violations": 0}
    for cause in ("tool", "environment", "validator", "unknown"):
        assert preference_target(costs, {"build": completed, "wait": {**failed, "failure_cause": cause}},
                                 100, 0)[2] == "non-policy alternative outcome"


def test_confidence_uses_best_two_completions_not_an_expensive_third():
    completed = {"status": "completed", "failure_cause": "none", "failure_kind": "none", "safety_violations": 0}
    costs = {"a": 6000.0, "b": 6010.0, "c": 30000.0}
    target, weight, reason = preference_target(costs, {o: completed for o in costs}, 100, 20)
    assert reason == "" and weight == 0.3 and target["a"] > target["b"]


@pytest.mark.parametrize("temperature", [0, -1, float("nan"), float("inf")])
def test_invalid_temperature_rejected(temperature):
    with pytest.raises(ValueError):
        soft_target({"a": 1.0}, temperature)


def test_legacy_outcomes_are_recovered_from_journals_and_missing_evidence_fails(tmp_path):
    import json
    from tools.laya_labels_to_cases import label_to_case
    from training.laya_counterfactual import label_outcomes

    def write(name, status, kind, cause):
        path = tmp_path / name
        rows = [{"type": "failure_packet", "cause": cause},
                {"type": "termination", "status": status, "final_observation": {
                    "status": "timed_out", "failure": {"kind": kind}, "metrics": {}, "elapsed_ticks": 36000}}]
        path.write_text("\n".join(json.dumps(r) for r in rows))
        return path

    base = write("round-7-base0.jsonl", "failed", "timeout", "environment")
    alternate = write("round-7-k0-wait.5s.jsonl", "failed", "timeout", "environment")
    label = {"version": "laya-counterfactual-v3", "base_journal": str(base), "base_choice": "serve.iron",
             "seed": 7, "decision_index": 0, "cost_ticks": {"serve.iron": 39000.0, "wait.5s": 38000.0},
             "options": ["serve.iron", "wait.5s", "escalate.stop"], "all_alternatives_faithful": True}
    outcomes = label_outcomes(label)
    assert {r["failure_cause"] for r in outcomes.values()} == {"policy"}
    assert label_to_case(label) == (None, "unresolved all-failed continuation")
    journal_outcome.cache_clear()
    alternate.unlink()
    assert label_to_case(label) == (None, "unavailable or invalid outcome evidence")


def test_cleanup_failure_overrides_objective_success(tmp_path):
    import json
    path = tmp_path / "cleanup.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in [
        {"type": "termination", "status": "completed", "final_observation": {"metrics": {}}},
        {"type": "cleanup", "status": "failed"},
    ]))
    assert journal_outcome(str(path))["status"] == "cleanup_failed"
