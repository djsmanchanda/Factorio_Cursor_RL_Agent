# Path: tests/test_laya_decision_flow.py
# Purpose: Protect the bounded Laya Guided Runner decision protocol.

from __future__ import annotations

import pytest

from orchestrator.laya_decision_flow import (
    build_decision_frame,
    next_decision_kind,
    normalize_options,
)


def test_workflow_advances_one_strategic_seam_at_a_time() -> None:
    assert next_decision_kind("serve_goal") == "choose_operation"
    assert next_decision_kind("choose_operation", operation_family="mine") == "select_site"
    assert next_decision_kind("choose_operation", operation_family="research") == "select_research"
    assert next_decision_kind("select_site") == "select_scale"
    assert next_decision_kind("select_scale") == "select_route"
    assert next_decision_kind("select_candidate") == "wait_or_act"
    assert next_decision_kind("wait_or_act") == "serve_goal"
    assert next_decision_kind("select_candidate", outcome="failed") == "recovery"


def test_decision_frame_keeps_full_state_external_and_options_compact() -> None:
    frame = build_decision_frame(
        {"provenance": {"state_snapshot_hash": "sha256:" + "1" * 64}, "large": "outside the prompt"},
        "choose_operation",
        [{"option_id": "mine", "summary": "Mine iron", "features": {"rate": 1, "cost": 5, "extra": 8, "ignored": 9}}],
        trajectory_id="trajectory-1",
        step_index=2,
    )
    assert frame.decision_id == "trajectory-1.s0002.choose_operation"
    assert frame.state_hash == "sha256:" + "1" * 64
    assert frame.question["option_ids"] == ["mine"]
    assert set(frame.options[0]["features"]) == {"rate", "cost", "extra", "ignored"}


def test_option_overflow_and_duplicates_fail_closed() -> None:
    with pytest.raises(ValueError):
        normalize_options(["a", "a"])
    with pytest.raises(ValueError):
        normalize_options([str(index) for index in range(13)])
