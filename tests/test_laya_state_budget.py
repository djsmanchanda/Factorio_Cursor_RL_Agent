# Path: tests/test_laya_state_budget.py
# Purpose: Verify exact state-plus-question context budgeting for Laya requests.

from __future__ import annotations

import pytest

from orchestrator.laya_state import (
    LayaStateError,
    get_budget_profile,
    measure_request,
    render_request,
)
from tests.test_laya_policy import _candidates, _state_builder


def _request():
    state = _state_builder({}, _candidates())
    questions = {
        "select_candidate": {
            "type": "choice",
            "instructions": "Choose one.",
            "criteria": {"a": "small", "b": "large"},
        }
    }
    return state, questions


class CountingTokenizer:
    def __init__(self, count: int):
        self.count = count
        self.calls: list[str] = []

    def encode(self, text: str, **_kwargs):
        self.calls.append(text)
        return list(range(self.count))


def test_request_budget_counts_questions_and_options_with_tokenizer() -> None:
    state, questions = _request()
    tokenizer = CountingTokenizer(37)
    rendered, usage = measure_request(state, questions, tokenizer=tokenizer)
    assert '"questions"' in rendered
    assert usage.token_count == 37
    assert usage.tokenizer == "tokenizer"
    assert usage.remaining_tokens == 987
    assert len(tokenizer.calls) == 3  # envelope, state, and questions


def test_request_budget_rejects_question_even_when_state_fits() -> None:
    state, questions = _request()
    questions["select_candidate"]["instructions"] = "x" * 5000
    with pytest.raises(LayaStateError, match="request requires"):
        render_request(state, questions)


def test_compact_profiles_are_named_and_bound_option_count() -> None:
    profile = get_budget_profile("compact-768")
    assert profile.max_tokens == 768
    assert profile.state_target_tokens + profile.question_target_tokens == 768
    assert get_budget_profile("compact-512").max_options == 12

    state, questions = _request()
    questions["select_candidate"]["criteria"] = {
        str(index): "option" for index in range(13)
    }
    with pytest.raises(LayaStateError, match="allows 12"):
        render_request(state, questions, budget_profile="compact-512")
