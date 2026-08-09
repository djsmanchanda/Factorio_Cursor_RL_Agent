# Path: tests/test_training_evaluation.py
# Purpose: Verify frozen paired holdout promotion and stable splits.

from __future__ import annotations

from training.evaluation import (
    EvaluationResult, FitnessVector, aggregate_fitness, promotion_decision,
    scenario_split,
)


def fitness(completion: float, ticks: float = 1000, safety: int = 0) -> FitnessVector:
    return FitnessVector(20, safety, completion, completion, ticks, 50, 5, 0)


def test_scenario_split_is_stable() -> None:
    assert scenario_split("mining_delivery", 42) == scenario_split("mining_delivery", 42)
    assert scenario_split("mining_delivery", 42) in {"train", "validation", "holdout"}


def test_aggregate_fitness_is_lexicographic_evidence() -> None:
    result = aggregate_fitness([
        {"status": "completed", "failure_kind": "none", "sustained_rate_ratio": 1,
         "elapsed_ticks": 100, "material_cost": 20, "infrastructure_count": 2},
        {"status": "failed", "failure_kind": "strategy", "sustained_rate_ratio": 0.5,
         "elapsed_ticks": 200, "material_cost": 30, "infrastructure_count": 3,
         "failed_placements": 1},
    ])
    assert result.completion_rate == 0.5
    assert result.mean_completion_ticks == 100
    assert result.failed_placements == 1


def test_promotion_requires_safe_paired_holdout_improvement() -> None:
    incumbent = EvaluationResult("old", "holdout", "same", fitness(0.8, 1000))
    candidate = EvaluationResult("new", "holdout", "same", fitness(0.9, 900))
    assert promotion_decision(candidate, incumbent)[0] is True

    unsafe = EvaluationResult("bad", "holdout", "same", fitness(1.0, safety=1))
    assert promotion_decision(unsafe, incumbent)[0] is False
    unpaired = EvaluationResult("new", "holdout", "different", fitness(0.9))
    assert promotion_decision(unpaired, incumbent)[0] is False
