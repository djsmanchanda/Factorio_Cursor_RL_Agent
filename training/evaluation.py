# Path: training/evaluation.py
# Purpose: Aggregate frozen evaluations and gate champion promotion lexicographically.

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Mapping


def scenario_split(family: str, seed: int) -> str:
    """Assign a stable 70/15/15 split without relying on seed ranges."""
    digest = hashlib.sha256(f"{family}:{seed}".encode()).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100
    if bucket < 70:
        return "train"
    return "validation" if bucket < 85 else "holdout"


@dataclass(frozen=True)
class FitnessVector:
    episodes: int
    safety_violations: int
    completion_rate: float
    sustained_rate_ratio: float
    mean_completion_ticks: float
    mean_material_cost: float
    mean_infrastructure: float
    failed_placements: int
    mean_productive_drill_ratio: float = 0.0
    mean_route_efficiency: float = 0.0
    mean_route_excess_tiles: float = 0.0
    mean_land_tiles: float = 0.0
    mean_pole_count: float = 0.0

    def rank_key(self) -> tuple:
        return (
            -self.safety_violations,
            self.completion_rate,
            self.sustained_rate_ratio,
            self.mean_productive_drill_ratio,
            self.mean_route_efficiency,
            -self.mean_route_excess_tiles,
            -self.mean_completion_ticks,
            -self.mean_material_cost,
            -self.mean_land_tiles,
            -self.mean_pole_count,
            -self.mean_infrastructure,
            -self.failed_placements,
        )

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass(frozen=True)
class EvaluationResult:
    policy_id: str
    split: str
    scenario_set_hash: str
    fitness: FitnessVector
    frozen: bool = True


def aggregate_fitness(episodes: Iterable[Mapping]) -> FitnessVector:
    records = list(episodes)
    if not records:
        raise ValueError("cannot evaluate an empty episode set")
    completed = [record for record in records if record["status"] == "completed"]
    denominator = len(records)

    def mean(name: str, source: list[Mapping] = records) -> float:
        return sum(float(record.get(name, 0.0)) for record in source) / max(1, len(source))

    return FitnessVector(
        episodes=denominator,
        safety_violations=sum(record.get("failure_kind") == "safety" for record in records),
        completion_rate=len(completed) / denominator,
        sustained_rate_ratio=mean("sustained_rate_ratio"),
        mean_completion_ticks=mean("elapsed_ticks", completed),
        mean_material_cost=mean("material_cost"),
        mean_infrastructure=mean("infrastructure_count"),
        failed_placements=sum(int(record.get("failed_placements", 0)) for record in records),
        mean_productive_drill_ratio=mean("productive_mining_drill_ratio"),
        mean_route_efficiency=mean("route_efficiency"),
        mean_route_excess_tiles=mean("route_excess_tiles"),
        mean_land_tiles=mean("occupied_footprint_tiles"),
        mean_pole_count=mean("electric_pole_count"),
    )


def promotion_decision(
    candidate: EvaluationResult,
    incumbent: EvaluationResult | None,
    *,
    minimum_episodes: int = 20,
    prior_family_regressions: int = 0,
    behaviorally_distinct: bool = True,
) -> tuple[bool, str]:
    """Promote only a frozen, safe, paired holdout improvement."""
    if candidate.split != "holdout" or not candidate.frozen:
        return False, "candidate evaluation is not frozen holdout evidence"
    if candidate.fitness.episodes < minimum_episodes:
        return False, "candidate has insufficient holdout episodes"
    if candidate.fitness.safety_violations:
        return False, "candidate has safety violations"
    if candidate.fitness.completion_rate <= 0:
        return False, "candidate has no completed held-out objective"
    if prior_family_regressions:
        return False, "candidate regresses an earlier curriculum family"
    if incumbent is not None and not behaviorally_distinct:
        return False, "candidate and incumbent selected identical held-out action traces"
    if incumbent is None:
        return True, "first safe holdout champion"
    if candidate.scenario_set_hash != incumbent.scenario_set_hash:
        return False, "candidate and incumbent were not evaluated on paired scenarios"
    if candidate.fitness.completion_rate < incumbent.fitness.completion_rate:
        return False, "candidate completion rate regresses"
    if candidate.fitness.rank_key() <= incumbent.fitness.rank_key():
        return False, "candidate does not improve the lexicographic fitness vector"
    return True, "candidate improves paired frozen holdout fitness"
