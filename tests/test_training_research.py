# Path: tests/test_training_research.py
# Purpose: Verify loopback-only, policy-only autoresearch proposals.

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.run_autoresearch as run_autoresearch
from training.research.controller import (
    MAX_GUIDANCE_ITEMS,
    AutoresearchController,
    build_research_packet,
    has_plateau,
)
from training.research.llama_client import LlamaCppClient
from training.research.proposals import apply_proposal, validate_proposal
from training.store import TrainingStore


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
        return Response({
            "choices": [{"message": {"content": json.dumps(proposal)}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
        })

    client = LlamaCppClient("http://127.0.0.1:8080", "qwen", timeout=10, opener=opener)
    assert client.complete_json([{"role": "user", "content": "test"}]) == proposal
    assert client.last_usage == {"prompt_tokens": 12, "completion_tokens": 7}
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


def test_research_packet_bounds_and_sanitizes_human_guidance() -> None:
    guidance = [{
        "guidance_id": f"guidance-{index}", "focus": "efficiency",
        "message": f"  reduce poles {index}  ", "created_utc": "ignored",
        "expires_generation": index + 1, "active": 1,
    } for index in range(MAX_GUIDANCE_ITEMS + 2)]
    allowed = {"alpha": {"minimum": 0.01, "maximum": 5.0}}

    packet = build_research_packet({}, [], {}, allowed, guidance)

    assert len(packet["research_guidance"]) == MAX_GUIDANCE_ITEMS
    assert packet["research_guidance"][0] == {
        "focus": "efficiency", "message": "reduce poles 0", "expires_generation": 1,
    }
    assert packet["allowed_changes"] == allowed


def test_guidance_cannot_expand_proposal_allowlist() -> None:
    class Client:
        def complete_json(self, _messages):
            return {
                "hypothesis": "obey injected guidance", "changes": {"reward": 100},
                "expected_effect": "change evaluator", "stop_condition": "never",
            }

    packet = build_research_packet(
        {}, [], {}, {"alpha": {"minimum": 0.01, "maximum": 5.0}},
        [{"focus": "general", "message": "change reward", "expires_generation": None}],
    )

    with pytest.raises(ValueError, match="cannot modify"):
        AutoresearchController(Client()).propose([1, 1, 1, 1, 1], packet)


def test_run_autoresearch_loads_only_unexpired_active_guidance(tmp_path: Path) -> None:
    database = tmp_path / "experience.db"
    with TrainingStore(database) as store:
        store.save_policy("policy-3", "diagonal_linucb", 3, {"alpha": 1.0}, {})
        store.save_guidance("expired", "general", "old", expires_generation=2)
        store.save_guidance("current", "reliability", "avoid failure", expires_generation=3)
        store.save_guidance("permanent", "throughput", "improve rate")
        store.save_guidance("dismissed", "efficiency", "use fewer poles")
        store.dismiss_guidance("dismissed")

    guidance = run_autoresearch._load_active_guidance(
        database, "policy-3", {"generation": 99},
    )

    assert [item["guidance_id"] for item in guidance] == ["permanent", "current"]


def test_run_autoresearch_publishes_phases_and_guidance(tmp_path, monkeypatch) -> None:
    policy = tmp_path / "policy.json"
    evidence = tmp_path / "evidence.json"
    database = tmp_path / "experience.db"
    policy.write_text('{"alpha":1.0}', encoding="utf-8")
    evidence.write_text('{"generation":3,"scores":[1,1,1,1,1]}', encoding="utf-8")
    with TrainingStore(database) as store:
        store.save_policy("policy-3", "diagonal_linucb", 3, {"alpha": 1.0}, {})
        store.save_guidance("current", "reliability", "avoid failures", 3)

    events = []
    packets = []

    class Telemetry:
        def __init__(self, _directory, worker_id):
            assert worker_id == "autoresearch"

        def publish(self, event):
            events.append(dict(event))

    class Client:
        def __init__(self, _endpoint, _model):
            pass

        def complete_json(self, messages):
            packets.append(json.loads(messages[1]["content"]))
            return {
                "hypothesis": "reduce exploration", "changes": {"alpha": 0.8},
                "expected_effect": "improve reliability", "stop_condition": "score falls",
            }

    monkeypatch.setattr(run_autoresearch, "WorkerTelemetry", Telemetry)
    monkeypatch.setattr(run_autoresearch, "LlamaCppClient", Client)

    result = run_autoresearch.main([
        "--model", "local", "--policy", str(policy), "--evidence", str(evidence),
        "--database", str(database), "--live-directory", str(tmp_path / "live"),
        "--parent-policy-id", "policy-3",
    ])

    assert result == 0
    assert [event["phase"] for event in events] == [
        "evidence_loaded", "requesting_model", "proposal_recorded",
    ]
    assert all(event["kind"] == "autoresearch" for event in events)
    assert packets[0]["research_guidance"] == [{
        "focus": "reliability", "message": "avoid failures", "expires_generation": 3,
    }]


def test_run_autoresearch_publishes_failure_and_reraises(tmp_path, monkeypatch) -> None:
    events = []

    class Telemetry:
        def __init__(self, _directory, _worker_id):
            pass

        def publish(self, event):
            events.append(dict(event))

    def fail(_args, _telemetry):
        raise RuntimeError("research failed")

    monkeypatch.setattr(run_autoresearch, "WorkerTelemetry", Telemetry)
    monkeypatch.setattr(run_autoresearch, "_run", fail)

    with pytest.raises(RuntimeError, match="research failed"):
        run_autoresearch.main([
            "--model", "local", "--policy", str(tmp_path / "policy.json"),
            "--evidence", str(tmp_path / "evidence.json"),
            "--database", str(tmp_path / "experience.db"),
            "--live-directory", str(tmp_path / "live"),
            "--parent-policy-id", "policy-3",
        ])

    assert events == [{
        "kind": "autoresearch", "phase": "failed",
        "error_type": "RuntimeError", "error": "research failed",
    }]


def test_non_plateau_skips_model_and_does_not_record_fake_runtime(tmp_path, monkeypatch) -> None:
    policy = tmp_path / "policy.json"
    evidence = tmp_path / "evidence.json"
    database = tmp_path / "experience.db"
    policy.write_text('{"alpha":1.0}', encoding="utf-8")
    evidence.write_text('{"scores":[1,2,3,4,5]}', encoding="utf-8")
    with TrainingStore(database) as store:
        store.save_policy("policy-parent", "diagonal_linucb", 0, {"alpha": 1.0}, {})

    class Client:
        def __init__(self, *_args):
            raise AssertionError("model client must not be created before a plateau")

    monkeypatch.setattr(run_autoresearch, "LlamaCppClient", Client)
    result = run_autoresearch.main([
        "--model", "local", "--policy", str(policy), "--evidence", str(evidence),
        "--database", str(database), "--live-directory", str(tmp_path / "live"),
        "--parent-policy-id", "policy-parent",
    ])
    assert result == 0
    with TrainingStore(database) as store:
        assert store.rows("llm_runtime_samples") == []
        assert store.rows("research_proposals") == []


def test_autoresearch_rejects_policy_checkpoint_with_false_lineage(tmp_path) -> None:
    database = tmp_path / "experience.db"
    with TrainingStore(database) as store:
        store.save_policy("policy-parent", "diagonal_linucb", 0, {"alpha": 1.0}, {})
    with pytest.raises(ValueError, match="does not match"):
        run_autoresearch._verify_parent_policy(
            database, "policy-parent", {"alpha": 4.0},
        )