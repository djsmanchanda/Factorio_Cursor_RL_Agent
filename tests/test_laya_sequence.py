# Path: tests/test_laya_sequence.py
# Purpose: Prove Laya's compact-v2 sequence preserves decision evidence or fails before truncation.

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tests.test_laya_policy import _candidates, _state_builder
from training.laya_sequence import (
    SEQUENCE_VERSION,
    LayaSequenceError,
    build_decision_sequence,
    project_decision_state,
)


class WordTokenizer:
    cls_token_id = 1
    sep_token_id = 2
    mask_token_id = 3
    mask_token = "[MASK]"

    def __call__(self, text: str, **_kwargs):
        return {"input_ids": list(range(10, 10 + len(text.split())))}


def _question(state):
    return {
        "type": "choice",
        "instructions": "Choose the next validated operation candidate.",
        "criteria": {
            option["option_id"]: option["summary"]
            for option in state["options"]
        },
    }


def test_projection_preserves_decision_facts_and_excludes_external_payloads() -> None:
    state = _state_builder({}, _candidates())
    state["active_failures"] = [{
        "failure_id": "failure-1",
        "kind": "strategy",
        "state": "active",
        "message": "The selected route is not producing.",
        "first_seen_tick": 2,
        "active": True,
    }]
    state["guidelines"].append({
        "rule_id": "rule-2",
        "kind": "hard",
        "statement": "Never cross the declared force boundary.",
    })
    projected = project_decision_state(state)

    assert projected["sequence_version"] == SEQUENCE_VERSION
    assert projected["objective"] == state["objective"]
    assert projected["failures"] == state["active_failures"]
    assert [item["statement"] for item in projected["guidelines"]] == [
        "Stay isolated.",
        "Never cross the declared force boundary.",
    ]
    assert projected["options"][1]["features"] == {"rate": 2.0}
    assert "provenance" not in projected
    assert "signature" not in projected["options"][0]
    assert "plan_hash" not in projected["options"][0]


def test_sequence_builder_rejects_head_and_state_overflow_without_truncation() -> None:
    state = _state_builder({}, _candidates())
    question = _question(state)
    long_question = copy.deepcopy(question)
    long_question["criteria"] = {
        key: "description " * 100 for key in long_question["criteria"]
    }
    with pytest.raises(LayaSequenceError, match="decision head requires"):
        build_decision_sequence(WordTokenizer(), state, long_question, 1024, 32)

    overloaded = copy.deepcopy(state)
    overloaded["active_failures"] = [
        {
            "failure_id": f"failure-{index}",
            "kind": "strategy",
            "state": "active",
            "message": ("measured unresolved production failure " * 12)[:512],
            "first_seen_tick": index,
            "active": True,
        }
        for index in range(16)
    ]
    with pytest.raises(LayaSequenceError, match="sequence requires"):
        build_decision_sequence(WordTokenizer(), overloaded, question, 128, 64)
    with pytest.raises(LayaSequenceError, match="max_len must be an integer"):
        build_decision_sequence(WordTokenizer(), state, question, float("inf"), 64)


def test_audited_shadow_states_fit_real_local_tokenizer_without_losing_features() -> None:
    transformers = pytest.importorskip("transformers")
    root = Path(__file__).resolve().parents[1]
    tokenizer_path = root / "data" / "laya-base" / "tokenizer"
    if not tokenizer_path.is_dir():
        pytest.skip("local Laya tokenizer is unavailable")

    from training.candidates import mining_delivery_candidates
    from training.laya_shadow import state_builder_for_scenario
    from training.scenarios.mining_delivery import generate_mining_delivery_curriculum

    tokenizer = transformers.AutoTokenizer.from_pretrained(tokenizer_path)
    for scenario in generate_mining_delivery_curriculum(10):
        candidates = mining_delivery_candidates(scenario)
        state = state_builder_for_scenario(scenario)({
            "tick": 0,
            "delivered_rate_per_tick": 0.0,
            "sustained_ticks": 0,
            "resource_remaining": 1000,
        }, candidates)
        question = _question(state)
        ids, markers = build_decision_sequence(tokenizer, state, question, 1024, 256)

        assert len(ids) <= 1024
        assert len(markers) == len(candidates)
        projected = project_decision_state(state)
        for candidate, option in zip(candidates, projected["options"]):
            assert option["features"] == candidate["features"]
