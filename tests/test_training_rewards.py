# Path: tests/test_training_rewards.py
# Purpose: Verify reward accounting and the non-negotiable safety gate.

import pytest

from training.rewards import reward_components, safety_violation
from training.scenarios.mining_delivery import generate_mining_delivery_scenario


def test_reward_total_is_exact_sum_of_components():
    scenario = generate_mining_delivery_scenario(1)
    report = {
        "status": "completed", "delivered_items": 100, "elapsed_ticks": 600,
        "material_items": 20, "failed_placements": 0, "extra_poles": 1,
    }
    reward = reward_components(scenario, report)
    assert reward["total"] == pytest.approx(sum(value for key, value in reward.items() if key != "total"))


def test_safety_is_not_a_reward_tradeoff():
    assert safety_violation({"failure_kind": "fixture"})
    assert safety_violation({"failure_kind": "safety"})
    assert not safety_violation({"failure_kind": "strategy"})


def test_non_finite_report_value_is_rejected():
    scenario = generate_mining_delivery_scenario(2)
    with pytest.raises(ValueError, match="must be finite"):
        reward_components(scenario, {"elapsed_ticks": float("nan")})
