# Path: training/features.py
# Purpose: Define versioned, bounded numeric feature vectors for learned policies.

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class FeatureRegistry:
    """An ordered feature contract with deterministic normalization."""

    version: str
    names: tuple[str, ...]
    scales: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("feature registry version cannot be empty")
        if not self.names or len(self.names) != len(self.scales):
            raise ValueError("feature names and scales must have equal non-zero length")
        if len(set(self.names)) != len(self.names):
            raise ValueError("feature names must be unique")
        if any(scale <= 0 for scale in self.scales):
            raise ValueError("feature scales must be positive")

    def vectorize(self, values: Mapping[str, float]) -> tuple[float, ...]:
        unknown = set(values).difference(self.names)
        if unknown:
            raise ValueError(f"unknown features for {self.version}: {sorted(unknown)}")
        normalized = []
        for name, scale in zip(self.names, self.scales):
            value = float(values.get(name, 0.0))
            if not math.isfinite(value):
                raise ValueError(f"feature {name} must be finite")
            normalized.append(value / scale)
        return tuple(normalized)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "names": list(self.names),
            "scales": list(self.scales),
        }

    @classmethod
    def from_dict(cls, payload: Mapping) -> "FeatureRegistry":
        return cls(
            version=str(payload["version"]),
            names=tuple(str(name) for name in payload["names"]),
            scales=tuple(float(scale) for scale in payload["scales"]),
        )


def policy_features(
    observation: Mapping[str, float], candidate: Mapping[str, float],
) -> dict[str, float]:
    """Namespace observation and candidate values before vectorization."""
    values = {"bias": 1.0}
    values.update({f"observation.{key}": float(value) for key, value in observation.items()})
    values.update({f"candidate.{key}": float(value) for key, value in candidate.items()})
    return values


MINING_DELIVERY_FEATURES_V1 = FeatureRegistry(
    version="mining-delivery-v1",
    names=(
        "bias",
        "observation.delivered_rate_per_tick",
        "observation.target_rate_per_tick",
        "observation.sustained_ticks",
        "observation.resource_remaining",
        "candidate.predicted_completion",
        "candidate.predicted_rate_per_tick",
        "candidate.drill_count",
        "candidate.route_tiles",
        "candidate.turn_count",
        "candidate.pole_count",
        "candidate.material_cost",
    ),
    scales=(1.0, 0.05, 0.05, 1_800.0, 100_000_000.0, 1.0, 0.05, 8.0, 160.0, 4.0, 24.0, 256.0),
)

MINING_DELIVERY_FEATURES_V2 = FeatureRegistry(
    version="mining-efficiency-v1",
    names=(
        "bias",
        "observation.delivered_rate_per_tick",
        "observation.target_rate_per_tick",
        "observation.sustained_ticks",
        "observation.resource_remaining",
        "candidate.predicted_completion",
        "candidate.predicted_rate_per_tick",
        "candidate.drill_count",
        "candidate.collection_belt_tiles",
        "candidate.actual_delivery_route_tiles",
        "candidate.shortest_delivery_route_tiles",
        "candidate.route_excess_tiles",
        "candidate.route_efficiency",
        "candidate.occupied_land_tiles",
        "candidate.turn_count",
        "candidate.pole_count",
        "candidate.material_cost",
    ),
    scales=(
        1.0, 0.05, 0.05, 1_800.0, 100_000_000.0, 1.0, 0.05, 8.0,
        64.0, 160.0, 160.0, 80.0, 1.0, 512.0, 4.0, 24.0, 256.0,
    ),
)
