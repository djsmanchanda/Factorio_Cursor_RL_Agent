# Path: tests/test_laya_policy.py
# Purpose: Verify Laya selection, dynamic questions, and explicit escalation behavior.

from __future__ import annotations

import pytest
import json

from training.laya_guidance import LayaGuidanceStore
from training.laya_policy import LayaEscalation, LayaPolicy, LayaPolicyError


def _state_builder(observation, candidates):
    state = {
        "version": "1.0.0",
        "run": {"run_id": "run-1", "episode_id": "episode-1", "tick": 0, "elapsed_ticks": 0, "bootstrap_profile": "training-v1", "surface": "training/1", "force": "training-1", "runtime": "disposable_training"},
        "objective": {"family": "mining", "description": "Deliver ore", "target": 1.0, "unit": "per_tick"},
        "goal_stack": [{"goal_id": "goal-1", "kind": "objective", "objective": "Deliver ore", "status": "active", "gap": 1.0, "age_ticks": 0, "retry_count": 0, "last_failure_kind": "none"}],
        "acceptance_gap": {"metric": "rate", "current": 0.0, "target": 1.0, "remaining": 1.0, "unit": "per_tick", "sustained_ticks": 0, "required_sustained_ticks": 10},
        "current_transaction": None,
        "state_digest": {
            "power": {"generation_kw": None, "firm_generation_kw": None, "accumulator_mj": None, "unpowered_consumers": None, "poles": None},
            "logistics": {"robots": None, "providers_available": None, "pending_ghosts": None, "ghost_blockages_top3": []},
            "stock": {"total": None, "accessible": None, "allocated": None, "top_shortages": []},
            "production": {"rate_now": 0.0, "rate_target": 1.0, "sustained_ticks": 0, "bottleneck_hint": None},
            "resource": {"remaining": None, "patch_bounds_hint": None},
            "space": {"free_area_tiles": None, "reserved_area_tiles": None, "occupied_footprint_tiles": None, "candidate_site_count": len(candidates)},
        },
        "recent_history": [], "active_failures": [],
        "guidelines": [{"rule_id": "rule-1", "kind": "hard", "statement": "Stay isolated."}],
        "options": [
            {"option_id": candidate["action_id"], "operation_family": "mine", "tool_id": "training.mine", "signature": "sha256:" + "0" * 64, "plan_hash": None, "summary": candidate["action_id"], "features": candidate["features"], "scope": {"surface": "training/1", "force": "training-1", "bounds": None}, "risk_tier": "ghost_submit"}
            for candidate in candidates
        ],
        "provenance": {"renderer_version": "1.0.0", "state_snapshot_hash": "sha256:" + "0" * 64, "guideline_pack": {"id": "pack-1", "version": "1.0.0", "hash": "sha256:" + "0" * 64}, "tool_registry": {"id": "registry-1", "version": "1.0.0", "hash": "sha256:" + "0" * 64}, "model": {"model_id": "model-1", "model_hash": "sha256:" + "0" * 64}},
        "budget": {"materials_remaining": None, "construction_budget_remaining": None, "action_count_remaining": None, "elapsed_ticks_remaining": None},
    }
    return state


class FakeAgent:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def predict(self, state, questions):
        self.calls.append((state, questions))
        return {"answers": {"select_candidate": self.answer}}


def _candidates():
    return [{"action_id": "a", "features": {"rate": 1.0}}, {"action_id": "b", "features": {"rate": 2.0}}]


def test_policy_uses_dynamic_candidates_and_returns_exact_candidate() -> None:
    agent = FakeAgent({"choice": "b", "confidence": 0.95})
    policy = LayaPolicy(agent, _state_builder)
    selected = policy.select(_candidates(), {"rate": 0.0}, 1)
    assert selected["action_id"] == "b"
    assert list(agent.calls[0][1]["select_candidate"]["criteria"]) == ["a", "b"]


def test_policy_question_uses_state_option_summary_as_canonical_description() -> None:
    agent = FakeAgent({"choice": "a", "confidence": 0.95})
    policy = LayaPolicy(agent, _state_builder)
    policy.select(_candidates(), {}, 1)
    criteria = agent.calls[0][1]["select_candidate"]["criteria"]
    assert criteria == {"a": "a", "b": "b"}


def test_policy_rejects_state_candidate_identity_drift() -> None:
    def drifting_state(observation, candidates):
        state = _state_builder(observation, candidates)
        state["options"].reverse()
        return state

    with pytest.raises(LayaPolicyError, match="match candidates in order"):
        LayaPolicy(FakeAgent({"choice": "a", "confidence": 0.95}), drifting_state).select(
            _candidates(), {}, 1,
        )


@pytest.mark.parametrize("answer", [{"choice": "unknown", "confidence": 1.0}, {"choice": "a", "confidence": 0.1}, {"choice": "a", "confidence": float("nan")}, {"choice": "a", "confidence": float("inf")}])
def test_invalid_or_low_confidence_answers_escalate(answer) -> None:
    with pytest.raises(LayaEscalation):
        LayaPolicy(FakeAgent(answer), _state_builder).select(_candidates(), {}, 1)


def test_policy_injects_current_human_guidance_before_inference(tmp_path) -> None:
    path = tmp_path / "coach.json"
    path.write_text(json.dumps({
        "record_id": "coach-1",
        "source": {"surface": "training/1", "runtime": "disposable_training"},
        "guidance": {"focus": "reliability", "instruction": "Repair the blocked input first.", "outcome": "unknown"},
    }) + "\n", encoding="utf-8")
    agent = FakeAgent({"choice": "a", "confidence": 0.95})
    policy = LayaPolicy(agent, _state_builder, guidance_store=LayaGuidanceStore(path))
    policy.select(_candidates(), {}, 1)
    state = agent.calls[0][0]
    assert any("blocked input" in item["statement"] for item in state["guidelines"])
