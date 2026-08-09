# Path: tests/test_training_research.py
# Purpose: Verify loopback-only, policy-only autoresearch proposals.

from __future__ import annotations

import json

import pytest

from training.research.controller import AutoresearchController, has_plateau
from training.research.llama_client import LlamaCppClient
from training.research.proposals import apply_proposal, validate_proposal


class Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_llama_client_is_loopback_only_and_parses_json() -> None:
    proposal = {"hypothesis": "explore less", "changes": {"alpha": 0.5},
                "expected_effect": "fewer weak trials", "stop_condition": "score falls"}

    def opener(request, timeout):
        assert request.full_url == "http://127.0.0.1:8080/v1/chat/completions"
        assert timeout == 10
        return Response({"choices": [{"message": {"content": json.dumps(proposal)}}]})

    client = LlamaCppClient("http://127.0.0.1:8080", "qwen", timeout=10, opener=opener)
    assert client.complete_json([{"role": "user", "content": "test"}]) == proposal
    with pytest.raises(ValueError, match="loopback"):
        LlamaCppClient("https://example.com", "qwen")


def test_proposals_can_only_change_bounded_policy_configuration() -> None:
    proposal = validate_proposal({
        "hypothesis": "increase exploration", "changes": {"alpha": 1.5},
        "expected_effect": "discover alternatives", "stop_condition": "no validation gain",
    })
    assert apply_proposal({"alpha": 1.0}, proposal)["alpha"] == 1.5
    with pytest.raises(ValueError, match="cannot modify"):
        validate_proposal({
            "hypothesis": "cheat", "changes": {"reward": 100},
            "expected_effect": "higher score", "stop_condition": "never",
        })


def test_controller_calls_llm_only_after_a_plateau() -> None:
    proposal = {"hypothesis": "change alpha", "changes": {"alpha": 0.8},
                "expected_effect": "improve", "stop_condition": "regression"}

    class Client:
        def __init__(self):
            self.calls = 0

        def complete_json(self, _messages):
            self.calls += 1
            return proposal

    client = Client()
    controller = AutoresearchController(client)
    assert has_plateau([1, 1, 1, 1, 1])
    assert controller.propose([1, 2, 3, 4, 5], {}) is None
    assert controller.propose([1, 1, 1, 1, 1], {}).changes == {"alpha": 0.8}
    assert client.calls == 1
