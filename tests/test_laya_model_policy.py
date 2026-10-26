# Path: tests/test_laya_model_policy.py | Purpose: Protect learned authority, complete menus, propensities and fail-closed evidence.
import hashlib
import json

import pytest

from training.laya_model_policy import ModelPolicy, QUESTION, RENDERER, render
from tools.serve_laya_model import covered_windows


def make_policy(tmp_path, response, epsilon=0):
    weights = tmp_path / "weights"
    weights.write_bytes(b"frozen")
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"version": "laya-model-policy-v1", "renderer": RENDERER,
        "model": "test-only", "endpoint": "http://127.0.0.1:9999/score", "epsilon": epsilon,
        "files": {str(weights): hashlib.sha256(b"frozen").hexdigest()}}))
    def transport(payload):
        return {**payload, "raw_probabilities": response, "probabilities": response,
                "truncated": False, "usage": {"input_tokens": 100}, "runtime": {"fixture": True}}
    return ModelPolicy(12, bundle, tmp_path / "audit.jsonl", transport=transport)


def menu(n=29):
    return {"options": [{"option_id": f"op.{i}", "family": "fixture", "need_id": f"need.{i}",
                         "params": {"recipe": str(i)}, "features": {"goal_need": True}} for i in range(n)]}


def test_model_scores_control_the_operation_and_preserve_29_choices(tmp_path):
    offered = menu()
    response = {o["option_id"]: float(i == 28) for i, o in enumerate(offered["options"])}
    policy = make_policy(tmp_path, response)
    choice = policy.choose({"brief": "current facts"}, offered)
    assert choice["option_id"] == "op.28"
    assert len(choice["distribution"]) == 29
    response["op.28"], response["op.0"] = 0., 1.
    assert policy.choose({"brief": "current facts"}, offered)["option_id"] == "op.0"


@pytest.mark.parametrize("bad", [{"invented": 1}, {"op.0": float("nan"), "op.1": 0}, {"op.0": .2, "op.1": .2}])
def test_invalid_output_fails_with_preserved_request(tmp_path, bad):
    policy = make_policy(tmp_path, bad)
    with pytest.raises(ValueError):
        policy.choose({"brief": "facts"}, menu(2))
    events = [json.loads(line) for line in policy.audit_path.read_text().splitlines()]
    assert [e["event"] for e in events] == ["model_request", "model_error"]


def test_behavior_probabilities_and_seed_replay(tmp_path):
    policy = make_policy(tmp_path, {"op.0": .8, "op.1": .2}, epsilon=.15)
    state = policy.rng.getstate()
    first = policy.choose({}, menu(2))
    policy.rng.setstate(state)
    again = policy.choose({}, menu(2))
    assert first["option_id"] == again["option_id"]
    assert [r["probability"] for r in first["distribution"]] == pytest.approx([.755, .245])
    assert first["probability"] == next(r["probability"] for r in first["distribution"] if r["option_id"] == first["option_id"])


def test_train_serve_renderer_uses_same_historical_structured_description():
    offered = menu(2)
    before = render({"brief": "facts"}, offered)
    offered["options"][0]["summary"] = "live-only summary"
    assert render({"brief": "facts"}, offered) == before
    assert list(before["questions"][QUESTION]["criteria"]) == ["op.0", "op.1"]


def test_native_windows_require_complete_state_coverage():
    covered_windows([(0, 80), (60, 100)], 100)
    with pytest.raises(ValueError):
        covered_windows([(0, 50), (51, 100)], 100)
    with pytest.raises(ValueError):
        covered_windows([(0, 99)], 100)


def test_compact_option_features_preserve_all_original_values():
    offered = menu(37)
    offered["options"][1]["features"] = {"stuck": False, "shortage": 4, "unknown": None}
    request = render({"brief": "facts"}, offered)
    state = json.loads(request["state"])
    def expand(value):
        if isinstance(value, dict):
            if set(value) == {"$ref"}:
                return expand(state["shared_facts"][value["$ref"]])
            return {k: expand(v) for k, v in value.items()}
        return [expand(v) for v in value] if isinstance(value, list) else value
    criteria = request["questions"][QUESTION]["criteria"]
    assert len(criteria) == 37
    for option in offered["options"]:
        fields = expand(json.loads(criteria[option["option_id"]]))
        assert {state["feature_columns"][int(k)]: v for k, v in fields["features"].items()} == option["features"]
