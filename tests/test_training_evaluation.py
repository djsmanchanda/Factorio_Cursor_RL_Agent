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
         "elapsed_ticks": 100, "material_cost": 20, "infrastructure_count": 2,
         "productive_mining_drill_ratio": 1, "route_efficiency": 0.9,
         "route_excess_tiles": 2, "occupied_footprint_tiles": 30,
         "electric_pole_count": 2},
        {"status": "failed", "failure_kind": "strategy", "sustained_rate_ratio": 0.5,
         "elapsed_ticks": 200, "material_cost": 30, "infrastructure_count": 3,
         "failed_placements": 1, "productive_mining_drill_ratio": 0.5,
         "route_efficiency": 0.5, "route_excess_tiles": 10,
         "occupied_footprint_tiles": 50, "electric_pole_count": 3},
    ])
    assert result.completion_rate == 0.5
    assert result.mean_completion_ticks == 100
    assert result.failed_placements == 1
    assert result.mean_productive_drill_ratio == 0.75
    assert result.mean_route_efficiency == 0.7
    assert result.mean_route_excess_tiles == 6
    assert result.mean_land_tiles == 40
    assert result.mean_pole_count == 2.5


def test_promotion_requires_safe_paired_holdout_improvement() -> None:
    incumbent = EvaluationResult("old", "holdout", "same", fitness(0.8, 1000))
    candidate = EvaluationResult("new", "holdout", "same", fitness(0.9, 900))
    assert promotion_decision(candidate, incumbent)[0] is True

    unsafe = EvaluationResult("bad", "holdout", "same", fitness(1.0, safety=1))
    assert promotion_decision(unsafe, incumbent)[0] is False
    unpaired = EvaluationResult("new", "holdout", "different", fitness(0.9))
    assert promotion_decision(unpaired, incumbent)[0] is False

    promoted, reason = promotion_decision(
        candidate, incumbent, behaviorally_distinct=False,
    )
    assert promoted is False
    assert "identical held-out action traces" in reason

def test_promotion_prefers_productive_compact_routes_after_output() -> None:
    incumbent = EvaluationResult(
        "old", "holdout", "same",
        FitnessVector(20, 0, 1, 1, 1000, 50, 5, 0, 0.5, 0.6, 20, 80, 8),
    )
    candidate = EvaluationResult(
        "new", "holdout", "same",
        FitnessVector(20, 0, 1, 1, 1000, 50, 5, 0, 0.9, 0.9, 4, 40, 4),
    )

    assert promotion_decision(candidate, incumbent)[0] is True
