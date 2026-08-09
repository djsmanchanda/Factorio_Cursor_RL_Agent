# Path: training/population.py
# Purpose: Evolve policy configurations through seeded elitist mutation.

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from typing import Mapping, Sequence


MUTABLE_RANGES = {
    "alpha": (0.01, 5.0),
    "regularization": (0.01, 100.0),
    "mutation_scale": (0.001, 1.0),
}


@dataclass(frozen=True)
class PolicyGenome:
    policy_id: str
    generation: int
    config: dict[str, float]
    parent_policy_id: str | None = None

    def __post_init__(self) -> None:
        if not self.policy_id or self.generation < 0:
            raise ValueError("invalid policy genome identity")
        _validate_config(self.config)


def _validate_config(config: Mapping[str, float]) -> None:
    unknown = set(config).difference(MUTABLE_RANGES)
    if unknown:
        raise ValueError(f"unknown mutable policy settings: {sorted(unknown)}")
    for name, value in config.items():
        low, high = MUTABLE_RANGES[name]
        if isinstance(value, bool) or not low <= float(value) <= high:
            raise ValueError(f"{name} must be between {low} and {high}")


def _mutate(config: Mapping[str, float], rng: random.Random, scale: float) -> dict[str, float]:
    child = dict(config)
    mutable = sorted(child)
    if not mutable:
        raise ValueError("a policy genome needs at least one mutable setting")
    changed = rng.choice(mutable)
    for name in mutable:
        if name != changed and rng.random() >= 0.35:
            continue
        low, high = MUTABLE_RANGES[name]
        child[name] = min(high, max(low, float(child[name]) * math.exp(rng.gauss(0, scale))))
    return child


def _child_id(parent_id: str, generation: int, index: int, config: Mapping) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{parent_id}|{generation}|{index}|{payload}".encode()).hexdigest()[:12]
    return f"policy-g{generation:04d}-{digest}"


def evolve_population(
    population: Sequence[PolicyGenome],
    fitness_by_id: Mapping[str, tuple],
    *,
    generation: int,
    seed: int,
    size: int,
    elite_count: int,
    mutation_scale: float,
) -> list[PolicyGenome]:
    """Retain elites and fill the population with seeded mutations."""
    if not population or not 1 <= elite_count <= size:
        raise ValueError("population and elite count must be non-empty and bounded")
    missing = {genome.policy_id for genome in population}.difference(fitness_by_id)
    if missing:
        raise ValueError(f"missing fitness for policies: {sorted(missing)}")
    ranked = sorted(
        population,
        key=lambda genome: (fitness_by_id[genome.policy_id], genome.policy_id),
        reverse=True,
    )
    elites = ranked[:elite_count]
    result = list(elites)
    rng = random.Random(seed)
    while len(result) < size:
        parent = elites[(len(result) - elite_count) % len(elites)]
        config = _mutate(parent.config, rng, mutation_scale)
        result.append(PolicyGenome(
            policy_id=_child_id(parent.policy_id, generation, len(result), config),
            generation=generation,
            parent_policy_id=parent.policy_id,
            config=config,
        ))
    return result
