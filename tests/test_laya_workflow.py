# Path: tests/test_laya_workflow.py
# Purpose: Verify full-workflow decision normalization and coach-data import.

from __future__ import annotations

from training.laya_workflow import LayaWorkflowDirector, LayaWorkflowError, coach_record_to_case


def _record(**overrides):
    record = {
        "version": "1.0.0",
        "record_id": "coach-1",
        "captured_at": "1970-01-01T00:00:00Z",
        "reviewer_id": "player-1",
        "decision_kind": "choose_operation",
        "source": {
            "runtime": "disposable_training",
            "surface": "training/coach",
            "force": "training-coach",
            "tick": 10,
        },
        "guidance": {
            "focus": "throughput",
            "instruction": "Serve the red science goal before expanding the mall.",
            "outcome": "success",
            "feedback_kind": "label",
            "confidence": 0.9,
        },
        "options": [
            {"option_id": "research.red-science", "summary": "Research red science"},
            {"option_id": "expand.iron", "summary": "Expand iron"},
        ],
        "chosen_option_id": "research.red-science",
    }
    record.update(overrides)
    return record


def test_coach_record_becomes_typed_case_with_full_workflow_question() -> None:
    case = coach_record_to_case(_record(observation={
        "tick": 10,
        "surface": "training/coach",
        "force": "training-coach",
        "entity_counts_sampled": {"assembling-machine": 2},
        "entities": [{"name": "assembling-machine-1", "type": "assembling-machine", "position": {"x": 1, "y": 2}}],
        "inventory": {"iron-plate": 12},
        "research": {"current": "automation", "queue": ["logistics"]},
    }))
    assert case["source"]["kind"] == "human_coach"
    assert list(case["questions"]) == ["choose_operation"]
    assert case["gold"]["choose_operation"]["label"] == "research.red-science"
    assert case["state"]["options"][0]["option_id"] == "research.red-science"
    assert case["state"]["observation_digest"]["inventory"]["iron-plate"] == 12.0
    assert case["state"]["observation_digest"]["entity_counts"]["assembling-machine"] == 2


def test_guidance_without_choice_is_not_silently_labelled() -> None:
    record = _record()
    del record["chosen_option_id"]
    try:
        coach_record_to_case(record)
    except LayaWorkflowError as error:
        assert "chosen_option_id" in str(error)
    else:  # pragma: no cover - assertion branch is the contract
        raise AssertionError("guidance-only data must not become a fabricated label")


def test_workflow_director_uses_typed_boundary_without_fallback() -> None:
    case = coach_record_to_case(_record())

    class Predictor:
        def predict(self, state, questions):
            assert state["options"][0]["option_id"] == "research.red-science"
            return {"answers": {"choose_operation": {"choice": "research.red-science", "confidence": 0.95}}}

    result = LayaWorkflowDirector(Predictor()).decide(
        "choose_operation", case["state"], case["state"]["options"]
    )
    assert result["selected_option"]["option_id"] == "research.red-science"
