# Path: tests/test_laya_runner.py
# Purpose: Prove Laya owns bounded disposable loop choices without fallback.

from __future__ import annotations

import copy
import json
from pathlib import Path
import pytest

from orchestrator.laya_state import LayaBudgetProfile
from training.canonical import plan_hash
from training.laya_exploration import ExplorationDirector, ExplorationGate
from training.laya_runner import LayaPrimaryRunner, LayaRunnerConfig, LayaRunnerError, LayaRunJournal
from training.laya_workflow import LayaWorkflowDirector
from training.scenarios.mining_delivery import generate_mining_delivery_scenario


MODEL_REF = {
    "model_id": "test-laya", "model_hash": "sha256:" + "1" * 64,
    "request_version": "factorio-decision-v2",
    "manifest_hash": "sha256:" + "2" * 64,
    "manifest": {
        "decision_kinds": [
            "choose_operation", "select_candidate", "wait_or_act", "recovery",
        ],
        "scenario_families": ["mining_delivery", "furnace_refining"],
    },
}
LARGE_BUDGET = LayaBudgetProfile(
    "test-large", max_tokens=20_000, state_target_tokens=15_000,
    question_target_tokens=5_000, max_options=20,
)


class Predictor:
    def __init__(self, chooser):
        self.chooser = chooser
        self.calls = []

    def predict(self, state, questions):
        kind = next(iter(questions))
        ids = list(questions[kind]["criteria"])
        choice = self.chooser(kind, ids, state)
        self.calls.append((kind, ids, choice))
        return {"answers": {kind: {"choice": choice, "confidence": 0.99}}}


class Bridge:
    def __init__(self, scenario, *, fail_first=False):
        self.scenario = scenario
        self.fail_first = fail_first
        self.executions = []
        self.recycle_calls = 0
        self.waited = False
        self.tick = 1
        self.delivered = 0

    def provision(self, _episode_id, _scenario):
        return {"started_tick": self.tick}

    def observe(self, _episode_id):
        completed = bool(self.executions) and self.waited and not (
            self.fail_first and len(self.executions) == 1
        )
        return {
            "status": "completed" if completed else "running",
            "scenario_id": self.scenario["scenario_id"],
            "surface": self.scenario["environment"]["surface_name"],
            "force": self.scenario["environment"]["force_name"],
            "tick": self.tick,
            "elapsed_ticks": self.tick,
            "objective": {
                "target_rate_per_tick": self.scenario["objective"]["target_rate_per_tick"],
                "sustain_ticks": self.scenario["objective"]["sustain_ticks"],
                "stage_index": 1,
            },
            "metrics": {
                "rate_per_tick": self.scenario["objective"]["target_rate_per_tick"] if completed else 0.0,
                "sustained_ticks": self.scenario["objective"]["sustain_ticks"] if completed else 0,
                "delivered_items": self.delivered,
                "resource_remaining": 1000,
                "electric_pole_count": 0,
                "occupied_footprint_tiles": 0,
                "power_connected": True,
            },
            "failure": {"kind": "none", "reason": ""},
        }

    def execute(self, _episode_id, _authorization, plan):
        self.executions.append(copy.deepcopy(plan))
        self.tick += 1
        failed = int(self.fail_first and len(self.executions) == 1)
        return {
            "succeeded_placements": 0 if failed else 1,
            "failed_placements": failed,
        }

    def recycle(self, _episode_id):
        self.recycle_calls += 1
        return {"status": "completed"}


def _runner(tmp_path, bridge, predictor, scenario, **config_overrides):
    config = {
        "worker_id": "worker-test-1", "run_id": "runner-test",
        "evidence_dir": tmp_path, "max_decisions": 12, "max_no_progress": 8,
    }
    config.update(config_overrides)
    director = LayaWorkflowDirector(predictor, budget_profile=LARGE_BUDGET)

    def sleep(_seconds):
        bridge.waited = True
        bridge.tick += 60
        bridge.delivered += 1

    return LayaPrimaryRunner(
        bridge, scenario, director, LayaRunnerConfig(**config),
        model_ref=MODEL_REF, sleeper=sleep,
    )


def test_model_owns_candidate_and_wait_selection_in_multistep_loop(tmp_path) -> None:
    scenario = generate_mining_delivery_scenario(11)
    bridge = Bridge(scenario)

    def choose(kind, ids, _state):
        if kind == "choose_operation":
            return "operation.mining_delivery"
        if kind == "select_candidate":
            return ids[-1]
        if kind == "wait_or_act":
            return "wait.5s"
        raise AssertionError(kind)

    predictor = Predictor(choose)
    result = _runner(tmp_path, bridge, predictor, scenario).run()

    assert result["status"] == "completed"
    assert plan_hash(bridge.executions[0])[7:19] in predictor.calls[1][2]
    assert [call[0] for call in predictor.calls] == [
        "choose_operation", "select_candidate", "wait_or_act",
    ]
    assert bridge.recycle_calls == 1
    events = [json.loads(line) for line in (tmp_path / "runs" / "runner-test.jsonl").read_text().splitlines()]
    assert events[0]["scenario"] == scenario
    assert events[-1]["type"] == "cleanup"
    assert any(event["type"] == "termination" for event in events)


def test_model_escalation_stops_without_policy_fallback(tmp_path) -> None:
    scenario = generate_mining_delivery_scenario(12)
    bridge = Bridge(scenario)
    predictor = Predictor(lambda _kind, ids, _state: next(item for item in ids if item == "escalate.stop"))

    result = _runner(tmp_path, bridge, predictor, scenario).run()

    assert result["status"] == "escalated"
    assert bridge.executions == []
    assert len(predictor.calls) == 1


def test_failed_execution_requires_explicit_model_recovery_retry(tmp_path) -> None:
    scenario = generate_mining_delivery_scenario(13)
    bridge = Bridge(scenario, fail_first=True)

    def choose(kind, ids, _state):
        if kind == "choose_operation":
            return "operation.mining_delivery"
        if kind == "select_candidate":
            return ids[0]
        if kind == "recovery":
            return next(item for item in ids if item.startswith("recovery.retry."))
        if kind == "wait_or_act":
            return "wait.1s"
        raise AssertionError(kind)

    predictor = Predictor(choose)
    result = _runner(tmp_path, bridge, predictor, scenario).run()

    assert result["status"] == "completed"
    assert len(bridge.executions) == 2
    assert "recovery" in [call[0] for call in predictor.calls]
    failure_files = list((tmp_path / "failures").glob("*.json"))
    assert len(failure_files) == 1
    assert json.loads(failure_files[0].read_text())["cause"] == "tool"


def test_restart_memory_rejects_terminal_and_preserves_stable_trajectory(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    journal = LayaRunJournal(path)
    journal.append(
        "run_started", scenario_hash="sha256:" + "2" * 64, worker_id="worker-1",
    )
    journal.append("provisioned", report={})
    journal.append("decision", decision_id="decision-0001")
    resumed = LayaRunJournal(path, resume=True)
    assert len(resumed.events) == 3
    resumed.append("termination", status="escalated")

    try:
        LayaRunJournal(path, resume=True)
    except Exception as error:
        assert "terminated" in str(error)
    else:
        raise AssertionError("terminated trajectory must not resume")


def test_provision_failure_is_recorded_as_environment_termination(tmp_path) -> None:
    scenario = generate_mining_delivery_scenario(14)
    bridge = Bridge(scenario)

    def fail_provision(_episode_id, _scenario):
        raise RuntimeError("worker refused provisioning")

    bridge.provision = fail_provision
    predictor = Predictor(lambda _kind, ids, _state: ids[0])

    result = _runner(tmp_path, bridge, predictor, scenario).run()

    assert result["status"] == "failed"
    assert bridge.recycle_calls == 0
    packet = json.loads(next((tmp_path / "failures").glob("*.json")).read_text())
    assert packet["cause"] == "environment"
    assert packet["evidence"]["outcome"]["exception_type"] == "RuntimeError"


def test_interrupted_transaction_is_not_repeated_on_resume(tmp_path):
    from training.laya_runner import LayaRunnerError
    path = tmp_path / "interrupted.jsonl"
    journal = LayaRunJournal(path)
    journal.append("run_started")
    journal.append("provisioned", report={})
    journal.append("transaction_started", transaction_id="uncertain-build")
    with pytest.raises(LayaRunnerError, match="unresolved transaction"):
        LayaRunJournal(path, resume=True)


def test_director_with_document_guidance_fits_actual_tokenizer(tmp_path):
    transformers = pytest.importorskip("transformers")
    from orchestrator.laya_knowledge import load_guideline_context
    from training.laya_sequence import build_decision_sequence
    root = Path(__file__).resolve().parents[1]
    token_path = root / "data/laya-base/tokenizer"
    if not token_path.is_dir():
        pytest.skip("local tokenizer unavailable")
    tokenizer = transformers.AutoTokenizer.from_pretrained(token_path, local_files_only=True)
    sizes = []
    class TokenizedPredictor(Predictor):
        def predict(self, state, questions):
            for question in questions.values():
                sizes.append(len(build_decision_sequence(tokenizer, state, question, 1024, 256)[0]))
            return super().predict(state, questions)
    def choose(kind, ids, _state):
        return "operation.mining_delivery" if kind == "choose_operation" else ("wait.5s" if kind == "wait_or_act" else ids[-1])
    scenario = generate_mining_delivery_scenario(11)
    bridge = Bridge(scenario)
    runner = _runner(tmp_path, bridge, TokenizedPredictor(choose), scenario)
    runner.knowledge = load_guideline_context(root, [scenario["family"], "choose_operation", "select_candidate", "recovery"])
    result = runner.run()
    assert result["status"] == "completed", runner.journal.events[-4:]
    assert len(sizes) == 3 and max(sizes) <= 1024
    events = runner.journal.events
    starts = [row for row in events if row["type"] == "transaction_started"]
    assert len(starts) == 3
    assert all(any(other["type"] == "tool_result" and other["transaction_id"] == row["transaction_id"] and other["seq"] > row["seq"] for other in events) for row in starts)


MODEL_ONLY_REF = {"model_id": "laya-base-typed-decisions", "model_hash": "sha256:" + "3" * 64}


def _exploration_runner(tmp_path, bridge, predictor, scenario, gate, *, run_id="explore-test"):
    director = ExplorationDirector(predictor, gate, budget_profile=LARGE_BUDGET)

    def sleep(_seconds):
        bridge.waited = True
        bridge.tick += 60
        bridge.delivered += 1

    return LayaPrimaryRunner(
        bridge, scenario, director,
        LayaRunnerConfig(
            worker_id="worker-test-1", run_id=run_id, evidence_dir=tmp_path,
            max_decisions=12, max_no_progress=8,
        ),
        model_ref=MODEL_ONLY_REF, exploration=gate, sleeper=sleep,
    )


def _events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_exploration_gate_drives_without_manifest_and_labels_every_artifact(tmp_path) -> None:
    scenario = generate_mining_delivery_scenario(11)
    bridge = Bridge(scenario)

    def choose(kind, ids, _state):
        return {"choose_operation": "operation.mining_delivery", "wait_or_act": "wait.5s"}.get(kind, ids[-1])

    class Uncertain(Predictor):
        def predict(self, state, questions):
            response = super().predict(state, questions)
            next(iter(response["answers"].values()))["confidence"] = 0.2
            return response

    gate = ExplorationGate(seed=7, epsilon=0.0)
    result = _exploration_runner(tmp_path, bridge, Uncertain(choose), scenario, gate).run()

    # Low confidence does not escalate under exploration; the model still acts.
    assert result["status"] == "completed"
    assert result["execution_authority"] == "exploration"
    events = _events(tmp_path / "runs" / "explore-test.jsonl")
    started = events[0]
    assert started["authority"]["kind"] == "exploration"
    assert started["authority"]["comparison_eligible"] is False
    assert started["checkpoint_capabilities"] is None
    assert started["capability_scope"]["trained"] is False
    selections = [event["selection"] for event in events if event["type"] == "decision_result"]
    assert selections and all(item["source"] == "model" for item in selections)


def test_exploration_and_trained_capability_gates_cannot_be_mixed(tmp_path) -> None:
    scenario = generate_mining_delivery_scenario(11)
    gate = ExplorationGate(seed=1, epsilon=0.5)
    predictor = Predictor(lambda _kind, ids, _state: ids[0])
    config = LayaRunnerConfig(worker_id="worker-test-1", run_id="mixed", evidence_dir=tmp_path)
    explorer = ExplorationDirector(predictor, gate, budget_profile=LARGE_BUDGET)
    plain = LayaWorkflowDirector(predictor, budget_profile=LARGE_BUDGET)

    with pytest.raises(LayaRunnerError, match="must not carry"):
        LayaPrimaryRunner(Bridge(scenario), scenario, explorer, config, model_ref=MODEL_REF, exploration=gate)
    with pytest.raises(LayaRunnerError, match="requires the explicit exploration gate"):
        LayaPrimaryRunner(Bridge(scenario), scenario, explorer, config, model_ref=MODEL_REF)
    with pytest.raises(LayaRunnerError, match="matching ExplorationDirector"):
        LayaPrimaryRunner(Bridge(scenario), scenario, plain, config, model_ref=MODEL_ONLY_REF, exploration=gate)
    with pytest.raises(LayaRunnerError, match="capability manifest"):
        LayaPrimaryRunner(Bridge(scenario), scenario, plain, config, model_ref=MODEL_ONLY_REF)


def test_seeded_exploration_is_reproducible(tmp_path) -> None:
    def selections(run_id):
        scenario = generate_mining_delivery_scenario(13)
        predictor = Predictor(lambda _kind, ids, _state: ids[0])
        _exploration_runner(
            tmp_path, Bridge(scenario), predictor, scenario,
            ExplorationGate(seed=3, epsilon=1.0), run_id=run_id,
        ).run()
        return [
            (event["selected_option_id"], event["selection"]["source"])
            for event in _events(tmp_path / "runs" / f"{run_id}.jsonl")
            if event["type"] == "decision_result"
        ]

    first = selections("explore-a")
    assert first == selections("explore-b")
    assert {source for _option, source in first} == {"exploration_random"}
