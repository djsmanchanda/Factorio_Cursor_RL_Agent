# Path: tests/test_training_batch_cli.py
# Purpose: Verify the advertised hundred-task command remains offline and repeatable.

import json

import pytest

from tools.run_training_batch import (
    _checkpoint,
    _learn_policy,
    _load_policy,
    _parse,
    _policy_learning_count,
    _rcon_password,
    main,
)
from training.features import MINING_DELIVERY_FEATURES_V1, MINING_DELIVERY_FEATURES_V2
from training.policies import DiagonalLinUCB


def test_default_batch_validates_one_hundred_scenarios_without_factorio(capsys):
    assert main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "mode": "offline", "scenarios": 100, "attempts": 100,
        "candidates_validated": 200, "factorio_mutated": False,
    }


def test_attempt_multiplier_reports_thousands_of_attempts(capsys):
    assert main(["--count", "100", "--attempts-per-scenario", "10"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["attempts"] == 1000
    assert result["candidates_validated"] == 2000


def test_completed_transitions_train_a_new_generation(tmp_path):
    base = DiagonalLinUCB("policy-g0000-initial", MINING_DELIVERY_FEATURES_V1)
    candidate = {
        "action_id": "a", "plan_hash": "sha256:" + "a" * 64,
        "features": {
            "predicted_completion": 1, "predicted_rate_per_tick": 0.01,
            "drill_count": 1, "route_tiles": 2, "turn_count": 1,
            "pole_count": 1, "material_cost": 1,
        },
    }
    transition = {
        "observation": {
            "delivered_rate_per_tick": 0, "target_rate_per_tick": 0.01,
            "sustained_ticks": 0, "resource_remaining": 100,
        },
        "candidates": [candidate], "chosen_action_id": "a", "reward": {"total": 5},
        "result": {"status": "completed", "failure_kind": "none", "reason": ""},
    }
    learned = _learn_policy(base, [("episode-1", transition, None)])
    assert learned.a_diag != base.a_diag
    assert learned.b != base.b

    path = tmp_path / "policy.json"
    _checkpoint(path, 1, learned)
    generation, restored = _load_policy(path)
    assert generation == 1
    assert restored.policy_id.startswith("policy-g0001-")
    assert restored.a_diag == learned.a_diag


def test_attempts_below_target_are_evidence_but_do_not_train_the_next_policy():
    base = DiagonalLinUCB("policy-g0000-initial", MINING_DELIVERY_FEATURES_V1)
    candidate = {
        "action_id": "a", "plan_hash": "sha256:" + "a" * 64,
        "features": {
            "predicted_completion": 1, "predicted_rate_per_tick": 0.01,
            "drill_count": 1, "route_tiles": 2, "turn_count": 1,
            "pole_count": 1, "material_cost": 1,
        },
    }
    transition = {
        "observation": {
            "delivered_rate_per_tick": 0, "target_rate_per_tick": 0.01,
            "sustained_ticks": 0, "resource_remaining": 100,
        },
        "candidates": [candidate], "chosen_action_id": "a", "reward": {"total": -5},
        "result": {
            "status": "timed_out", "failure_kind": "timeout",
            "reason": "production target was not sustained",
        },
    }
    results = [("episode-failed-target", transition, None)]

    learned = _learn_policy(base, results)

    assert _policy_learning_count(results) == 0
    assert learned.a_diag == base.a_diag
    assert learned.b == base.b


def test_completed_staged_trajectory_credits_each_action_without_overweighting_episode():
    base = DiagonalLinUCB("policy-g0000-initial", MINING_DELIVERY_FEATURES_V1)
    observation = {
        "delivered_rate_per_tick": 0, "target_rate_per_tick": 0.01,
        "sustained_ticks": 0, "resource_remaining": 100,
    }
    candidates = [
        {"action_id": action_id, "plan_hash": "sha256:" + action_id * 64,
         "features": {"predicted_completion": 1, "predicted_rate_per_tick": rate,
                      "drill_count": drills, "route_tiles": 2, "turn_count": 0,
                      "pole_count": 1, "material_cost": 1}}
        for action_id, rate, drills in (("a", 0.01, 1), ("b", 0.02, 2))
    ]
    transition = {
        "observation": observation, "candidates": candidates, "chosen_action_id": "b",
        "reward": {"total": 6},
        "result": {"status": "completed", "failure_kind": "none", "reason": ""},
        "stage_action_trail": [
            {"pre_action_observation": observation, "selected_candidate": candidates[0]},
            {"pre_action_observation": observation, "selected_candidate": candidates[1]},
        ],
    }

    learned = _learn_policy(base, [("episode-staged", transition, None)])

    assert learned.a_diag != base.a_diag
    assert learned.b != base.b
    # Two actions receive 3 reward each, preserving the episode's total credit.
    assert learned.b[0] == pytest.approx(6.0)
def test_rcon_secret_file_overrides_environment(tmp_path, monkeypatch):
    secret = tmp_path / "rcon-password"
    secret.write_text("worker-secret\n", encoding="utf-8")
    monkeypatch.setenv("FACTORIO_TRAINING_RCON_PASSWORD", "environment-secret")

    assert _rcon_password(_parse(["--rcon-secret-file", str(secret)])) == "worker-secret"


def test_rcon_secret_file_rejects_empty_file(tmp_path):
    secret = tmp_path / "rcon-password"
    secret.write_text("\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="RCON secret file is empty"):
        _rcon_password(_parse(["--rcon-secret-file", str(secret)]) )

def test_fresh_checkpoint_uses_mining_efficiency_features(tmp_path) -> None:
    generation, policy = _load_policy(tmp_path / "missing-policy.json")

    assert generation == 0
    assert policy.registry == MINING_DELIVERY_FEATURES_V2
