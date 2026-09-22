# Path: tests/test_laya_dataset.py
# Purpose: Verify auditable per-decision Laya case decomposition.

from __future__ import annotations

import pytest

from training.canonical import canonical_sha256
from training.laya_dataset import LayaDatasetError, transition_to_case, transition_to_step_cases


HASH = "sha256:" + ("0" * 64)


def _scenario() -> dict:
    return {
        "scenario_id": "scenario-1",
        "family": "mining_delivery",
        "seed": 7,
        "environment": {"surface_name": "training/1", "force_name": "training-1"},
        "objective": {
            "kind": "delivery",
            "item": "iron-ore",
            "target_rate_per_tick": 1.0,
            "sustain_ticks": 10,
        },
    }


def _transition(**overrides) -> dict:
    candidate = {"action_id": "build.a", "plan_hash": HASH, "features": {"expected_rate": 1.0}}
    value = {
        "version": "1.2.0",
        "episode_id": "episode-1",
        "scenario_id": "scenario-1",
        "scenario_seed": 7,
        "scenario_hash": HASH,
        "started_tick": 10,
        "observation": {
            "delivered_rate_per_tick": 0.0,
            "target_rate_per_tick": 1.0,
            "sustained_ticks": 0,
            "resource_remaining": 100.0,
            "stage_index": 1,
            "stage_count": 2,
        },
        "candidates": [candidate],
        "chosen_action_id": "build.a",
        "result": {"status": "completed", "failure_kind": "none", "reason": ""},
        "next_observation": {
            "delivered_rate_per_tick": 1.0,
            "target_rate_per_tick": 1.0,
            "sustained_ticks": 10,
            "resource_remaining": 99.0,
            "stage_index": 2,
            "stage_count": 2,
        },
    }
    value.update(overrides)
    return value


def test_step_cases_split_typed_decisions_and_link_parent_trajectory() -> None:
    transition = _transition(workflow_decisions=[{
        "decision_kind": "choose_operation",
        "instructions": "Choose the operation family.",
        "options": [{"option_id": "mine", "summary": "Mine ore"}],
        "chosen_option_id": "mine",
        "label_source": "outcome",
    }])

    cases = transition_to_step_cases(transition, _scenario())

    assert [case["source"]["step_index"] for case in cases] == [0, 1]
    assert [next(iter(case["questions"])) for case in cases] == ["select_candidate", "choose_operation"]
    assert all(case["source"]["trajectory_id"] == "episode-1" for case in cases)
    assert cases[1]["decision_trace"]["pre_observation"]["delivered_rate_per_tick"] == 0.0
    assert cases[1]["decision_trace"]["post_observation"]["delivered_rate_per_tick"] == 1.0
    assert cases[1]["decision_trace"]["outcome_ref"]["trajectory_id"] == "episode-1"
    assert cases[1]["label_provenance"]["evidence_hash"].startswith("sha256:")


def test_stage_trace_is_one_case_and_not_duplicated_by_typed_stage_label() -> None:
    stage_two = {
        "stage_index": 2,
        "observed_tick": 30,
        "pre_action_observation": {"delivered_rate_per_tick": 1.0, "stage_index": 2},
        "selected_candidate": {"action_id": "build.b", "plan_hash": HASH, "features": {"expected_rate": 2.0}},
        "available_candidates": [{"action_id": "build.b", "plan_hash": HASH, "features": {"expected_rate": 2.0}}],
        "execution": {"status": "submitted", "succeeded_placements": 1},
        "post_execution_tick": 31,
        "post_action_observation": {"delivered_rate_per_tick": 2.0, "stage_index": 2},
    }
    transition = _transition(
        stage_action_trail=[{
            "stage_index": 1,
            "observed_tick": 10,
            "pre_action_observation": _transition()["observation"],
            "selected_candidate": _transition()["candidates"][0],
            "available_candidates": _transition()["candidates"],
            "execution": {"status": "submitted"},
            "post_execution_tick": 11,
            "post_action_observation": _transition()["next_observation"],
        }, stage_two],
        workflow_decisions=[{
            "decision_kind": "select_candidate_stage_2",
            "options": [{"option_id": "build.b", "summary": "Build stage two"}],
            "chosen_option_id": "build.b",
            "label_source": "counterfactual",
        }],
    )

    cases = transition_to_step_cases(transition, _scenario())

    assert [next(iter(case["questions"])) for case in cases] == ["select_candidate", "select_candidate_stage_2"]
    assert len(cases) == 2
    assert cases[1]["decision_trace"]["executed_action"]["option_id"] == "build.b"
    assert cases[1]["decision_trace"]["outcome"]["execution"]["succeeded_placements"] == 1
    assert cases[1]["label_provenance"]["method"] == "counterfactual"


def test_legacy_transition_case_remains_grouped() -> None:
    transition = _transition(workflow_decisions=[{
        "decision_kind": "choose_operation",
        "options": [{"option_id": "mine", "summary": "Mine ore"}],
        "chosen_option_id": "mine",
    }])

    case = transition_to_case(transition, _scenario())

    assert set(case["questions"]) == {"select_candidate", "choose_operation"}
    assert "decision_trace" not in case


def test_failed_action_is_not_promoted_without_causal_evidence() -> None:
    transition = _transition(result={"status": "timed_out", "failure_kind": "timeout", "reason": "no output"})
    with pytest.raises(LayaDatasetError, match="requires reviewer or evaluated counterfactual"):
        transition_to_case(transition, _scenario())


def test_state_snapshot_uses_pre_action_observation_not_scenario_hash() -> None:
    transition = _transition()
    case = transition_to_case(transition, _scenario())
    expected = canonical_sha256(transition["observation"])
    assert case["state"]["provenance"]["state_snapshot_hash"] == expected
    assert case["source"]["provenance_hash"] == expected


def test_terminal_failure_does_not_leak_into_prompt_state() -> None:
    transition = _transition(result={"status": "timed_out", "failure_kind": "timeout", "reason": "no output"})
    transition["counterfactual_evidence"] = {"evaluations": [
        {"option_id": "build.a", "preferred": True, "metrics": {"completed": True}, "evidence_hash": "sha256:" + "1" * 64, "initial_state_hash": "sha256:" + "3" * 64},
        {"option_id": "build.b", "preferred": False, "metrics": {"completed": False}, "evidence_hash": "sha256:" + "2" * 64, "initial_state_hash": "sha256:" + "3" * 64},
    ]}
    transition["label_source"] = "counterfactual"
    case = transition_to_case(transition, _scenario())
    assert case["state"]["goal_stack"][0]["last_failure_kind"] == "none"
