# Path: tests/test_laya_shadow.py
# Purpose: Verify shadow comparisons never replace the incumbent executed action.

from __future__ import annotations

import json

import pytest

from training.candidates import mining_delivery_candidates
from training.laya_shadow import shadow_decision, state_builder_for_scenario
from training.scenarios.mining_delivery import generate_mining_delivery_curriculum
from training.policies import DeterministicBaseline
from tools.run_laya_shadow_batch import (
    _ThrottledPredictor,
    _artifact_paths,
    _assert_artifacts_available,
    _episode_artifact_record,
    _should_continue,
)


class FakeLaya:
    def __init__(self, selected: str):
        self.selected = selected

    def select(self, candidates, observation, seed):
        del observation, seed
        return next(candidate for candidate in candidates if candidate["action_id"] == self.selected)


def test_shadow_records_disagreement_but_returns_baseline_as_execution_authority() -> None:
    candidates = [
        {"action_id": "a", "features": {"predicted_completion": 1.0, "material_cost": 1}},
        {"action_id": "b", "features": {"predicted_completion": 0.5, "material_cost": 1}},
    ]
    record = shadow_decision(FakeLaya("b"), DeterministicBaseline(), candidates, {}, 1)
    assert record["status"] == "selected"
    assert record["baseline_action_id"] == "a"
    assert record["laya_action_id"] == "b"
    assert record["agreement"] is False
    assert record["baseline_action"]["action_id"] == "a"


def test_shadow_state_builder_reuses_validated_case_contract() -> None:
    scenario = generate_mining_delivery_curriculum(1, 0)[0]
    candidates = mining_delivery_candidates(scenario)
    state = state_builder_for_scenario(scenario)({}, candidates)
    assert state["run"]["runtime"] == "disposable_training"
    assert [option["option_id"] for option in state["options"]] == [
        candidate["action_id"] for candidate in candidates
    ]
    source = next(
        candidate for candidate in candidates
        if candidate["action_id"] == state["options"][0]["option_id"]
    )
    projected = state["options"][0]
    assert projected["features"] == source["features"]


def test_count_based_shadow_run_stops_at_requested_episode_limit() -> None:
    assert _should_continue(episodes=9, episode_limit=10, deadline=None) is True
    assert _should_continue(episodes=10, episode_limit=10, deadline=None) is False


def test_throttled_predictor_preserves_request_metadata() -> None:
    class Predictor:
        tokenizer = object()
        config = {"max_len": 1024}
        max_len = 1024
        head_max_len = 256

        def predict(self, state, questions):
            return {"state": state, "questions": questions}

    class Gate:
        def wait(self):
            return None

    wrapped = _ThrottledPredictor(Predictor(), Gate(), 0)
    assert wrapped.tokenizer is wrapped.predictor.tokenizer
    assert wrapped.request_tokenizer is wrapped.tokenizer
    assert wrapped.max_len == 1024
    assert wrapped.head_max_len == 256


def test_shadow_artifacts_are_exclusive_and_preserve_interrupted_evidence(tmp_path) -> None:
    paths = _artifact_paths(tmp_path / "run.jsonl")
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    paths["decisions"].write_text('{"record_type":"decision"}\n', encoding="utf-8")
    with pytest.raises(FileExistsError):
        _assert_artifacts_available(paths)
    assert paths["decisions"].read_text(encoding="utf-8") == '{"record_type":"decision"}\n'


def test_escalation_outcome_record_retains_transition_and_scenario_snapshot() -> None:
    scenario = generate_mining_delivery_curriculum(1, 0)[0]
    transition = {"episode_id": "episode-1", "result": {"status": "completed"}}
    record = _episode_artifact_record(
        scenario=scenario,
        episode_id="episode-1",
        decision_ids=["episode-1:decision:0001"],
        transition=transition,
    )
    assert record["decision_ids"] == ["episode-1:decision:0001"]
    assert record["transition"] == transition
    assert record["scenario_snapshot"]["scenario_hash"] == scenario["scenario_hash"]
    assert json.loads(json.dumps(record))["transition"]["result"]["status"] == "completed"


def test_episode_error_outcome_record_retains_escalation_link() -> None:
    scenario = generate_mining_delivery_curriculum(1, 0)[0]
    record = _episode_artifact_record(
        scenario=scenario,
        episode_id="episode-error",
        decision_ids=["episode-error:decision:0001"],
        error="LayaEscalation: context overflow",
    )
    assert record["record_type"] == "episode_error"
    assert record["transition"] is None
    assert record["decision_ids"] == ["episode-error:decision:0001"]
    assert "context overflow" in record["error"]
