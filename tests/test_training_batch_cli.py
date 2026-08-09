# Path: tests/test_training_batch_cli.py
# Purpose: Verify the advertised hundred-task command remains offline and repeatable.

import json

from tools.run_training_batch import _checkpoint, _learn_policy, _load_policy, main
from training.features import MINING_DELIVERY_FEATURES_V1
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