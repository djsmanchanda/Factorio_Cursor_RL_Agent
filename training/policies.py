# Path: training/policies.py
# Purpose: Provide deterministic and inspectable policies over planner candidates.

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Mapping, Sequence

from training.features import FeatureRegistry, policy_features


def policy_snapshot(policy) -> dict:
    """Return the exact immutable payload used to bind stored transitions."""
    if hasattr(policy, "to_dict"):
        return policy.to_dict()
    return {"policy_id": str(policy.policy_id), "algorithm": type(policy).__name__}


def transition_can_train_policy(transition: Mapping) -> bool:
    """Return whether an attempt sustained its objective and may shape a successor."""
    result = transition.get("result") or {}
    return result.get("status") == "completed" and result.get("failure_kind") == "none"


def _candidate_id(candidate: Mapping) -> str:
    identifier = str(candidate.get("action_id", ""))
    if not identifier:
        raise ValueError("every candidate needs a non-empty action_id")
    return identifier


class DeterministicBaseline:
    """Choose predicted completion, then the least costly deterministic plan."""

    policy_id = "deterministic-baseline-v1"

    def select(
        self, candidates: Sequence[Mapping], observation: Mapping | None = None,
        seed: int | None = None,
    ) -> Mapping:
        del observation, seed
        if not candidates:
            raise ValueError("cannot select from an empty candidate catalog")

        def rank(candidate: Mapping) -> tuple:
            features = candidate.get("features") or {}
            return (
                -float(features.get("predicted_completion", 0.0)),
                float(features.get("material_cost", math.inf)),
                float(features.get("route_tiles", math.inf)),
                _candidate_id(candidate),
            )

        return min(candidates, key=rank)


@dataclass
class DiagonalLinUCB:
    """A stdlib-only diagonal LinUCB policy with JSON-safe state."""

    policy_id: str
    registry: FeatureRegistry
    alpha: float = 1.0
    regularization: float = 1.0
    exploration_rate: float = 0.0
    a_diag: list[float] | None = None
    b: list[float] | None = None

    def __post_init__(self) -> None:
        if (
            not self.policy_id or not math.isfinite(self.alpha)
            or not math.isfinite(self.regularization)
            or not math.isfinite(self.exploration_rate)
            or self.alpha < 0 or self.regularization <= 0
            or not 0 <= self.exploration_rate <= 1
        ):
            raise ValueError("invalid LinUCB identity or hyperparameters")
        width = len(self.registry.names)
        if self.a_diag is None:
            self.a_diag = [self.regularization] * width
        if self.b is None:
            self.b = [0.0] * width
        if len(self.a_diag) != width or len(self.b) != width:
            raise ValueError("LinUCB state width does not match its feature registry")
        if any(not math.isfinite(value) or value <= 0 for value in self.a_diag):
            raise ValueError("LinUCB diagonal covariance must remain positive")
        if any(not math.isfinite(value) for value in self.b):
            raise ValueError("LinUCB reward state must remain finite")

    def _vector(self, observation: Mapping, candidate: Mapping) -> tuple[float, ...]:
        values = policy_features(observation, candidate.get("features") or {})
        # A checkpoint owns its feature contract. New audit-only candidate fields
        # must not invalidate an older immutable policy during replay or migration.
        return self.registry.vectorize({
            name: values[name] for name in self.registry.names if name in values
        })

    def score(self, observation: Mapping, candidate: Mapping) -> float:
        vector = self._vector(observation, candidate)
        mean = sum((b / a) * x for a, b, x in zip(self.a_diag, self.b, vector))
        uncertainty = math.sqrt(sum((x * x) / a for a, x in zip(self.a_diag, vector)))
        return mean + self.alpha * uncertainty

    def select(self, candidates: Sequence[Mapping], observation: Mapping, seed: int) -> Mapping:
        if not candidates:
            raise ValueError("cannot select from an empty candidate catalog")
        rng = random.Random(seed)
        ordered = sorted(candidates, key=_candidate_id)
        if self.exploration_rate and rng.random() < self.exploration_rate:
            return rng.choice(ordered)
        scored = [(self.score(observation, candidate), candidate) for candidate in candidates]
        best = max(score for score, _candidate in scored)
        tied = sorted(
            (candidate for score, candidate in scored if math.isclose(score, best, abs_tol=1e-12)),
            key=_candidate_id,
        )
        return rng.choice(tied)

    def update(self, observation: Mapping, candidate: Mapping, reward: float) -> None:
        vector = self._vector(observation, candidate)
        for index, value in enumerate(vector):
            self.a_diag[index] += value * value
            self.b[index] += float(reward) * value

    def to_dict(self) -> dict:
        return {
            "version": "1.1.0",
            "algorithm": "diagonal_linucb",
            "policy_id": self.policy_id,
            "registry": self.registry.to_dict(),
            "alpha": self.alpha,
            "regularization": self.regularization,
            "exploration_rate": self.exploration_rate,
            "a_diag": list(self.a_diag),
            "b": list(self.b),
        }

    @classmethod
    def from_dict(cls, payload: Mapping) -> "DiagonalLinUCB":
        if payload.get("version") not in {"1.0.0", "1.1.0"} or payload.get("algorithm") != "diagonal_linucb":
            raise ValueError("unsupported LinUCB checkpoint")
        return cls(
            policy_id=str(payload["policy_id"]),
            registry=FeatureRegistry.from_dict(payload["registry"]),
            alpha=float(payload["alpha"]),
            regularization=float(payload["regularization"]),
            exploration_rate=float(payload.get("exploration_rate", 0.0)),
            a_diag=[float(value) for value in payload["a_diag"]],
            b=[float(value) for value in payload["b"]],
        )
