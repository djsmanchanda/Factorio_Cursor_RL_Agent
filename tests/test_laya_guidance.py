# Path: tests/test_laya_guidance.py
# Purpose: Verify high-level human guidance is injected without action fallback.

from __future__ import annotations

import json

from training.laya_guidance import LayaGuidanceStore


def _state() -> dict:
    return {
        "run": {"surface": "training/coach", "runtime": "disposable_training"},
        "guidelines": [{"rule_id": "rule.hard", "kind": "hard", "statement": "Stay isolated."}],
        "goal_stack": [{"goal_id": "goal.factory", "kind": "objective", "objective": "Produce iron", "status": "active", "gap": 1.0, "age_ticks": 0, "retry_count": 0, "last_failure_kind": "none"}],
    }


def test_latest_guidance_per_focus_becomes_soft_priority(tmp_path) -> None:
    path = tmp_path / "coach.json"
    path.write_text(json.dumps({
        "record_id": "coach-1",
        "source": {"surface": "training/coach", "runtime": "disposable_training"},
        "guidance": {"focus": "throughput", "goal": "Reach red science", "instruction": "Repair the starving input before adding more assemblers.", "outcome": "unknown"},
    }) + "\n", encoding="utf-8")
    state = LayaGuidanceStore(path).apply(_state())
    assert state["guidelines"][0]["rule_id"] == "rule.hard"
    assert any("starving input" in item["statement"] for item in state["guidelines"])
    assert state["goal_stack"][0]["goal_id"] == "goal.human.throughput"


def test_guidance_is_scoped_to_runtime_and_surface(tmp_path) -> None:
    path = tmp_path / "coach.json"
    path.write_text(json.dumps({
        "record_id": "coach-foreign",
        "source": {"surface": "nauvis", "runtime": "real_nauvis"},
        "guidance": {"focus": "general", "instruction": "Do not apply here.", "outcome": "unknown"},
    }) + "\n", encoding="utf-8")
    state = LayaGuidanceStore(path).apply(_state())
    assert not any("Do not apply here" in item.get("statement", "") for item in state["guidelines"])
