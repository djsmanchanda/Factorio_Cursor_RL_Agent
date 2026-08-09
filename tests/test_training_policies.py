# Path: tests/test_training_policies.py
# Purpose: Verify deterministic feature and policy learning behavior.

from __future__ import annotations

from training.features import FeatureRegistry, policy_features
from training.policies import DeterministicBaseline, DiagonalLinUCB


REGISTRY = FeatureRegistry(
    "test-v1",
    ("bias", "observation.load", "candidate.value", "candidate.cost"),
    (1.0, 10.0, 5.0, 20.0),
)


def candidates() -> list[dict]:
    return [
        {"action_id": "cheap", "features": {
            "predicted_completion": 1, "material_cost": 10, "route_tiles": 20,
        }},
        {"action_id": "expensive", "features": {
            "predicted_completion": 1, "material_cost": 20, "route_tiles": 10,
        }},
    ]


def test_feature_registry_is_versioned_normalized_and_strict() -> None:
    values = policy_features({"load": 5}, {"value": 2.5, "cost": 10})
    assert REGISTRY.vectorize(values) == (1.0, 0.5, 0.5, 0.5)
    assert FeatureRegistry.from_dict(REGISTRY.to_dict()) == REGISTRY


def test_deterministic_baseline_prefers_lower_material_cost() -> None:
    assert DeterministicBaseline().select(candidates())["action_id"] == "cheap"


def test_linucb_learns_and_round_trips_exactly() -> None:
    policy = DiagonalLinUCB("learned-v1", REGISTRY, alpha=0.0)
    observation = {"load": 5}
    cheap = {"action_id": "cheap", "features": {"value": 1, "cost": 1}}
    costly = {"action_id": "costly", "features": {"value": 4, "cost": 10}}
    policy.update(observation, costly, reward=10)

    assert policy.select([cheap, costly], observation, seed=1) == costly
    restored = DiagonalLinUCB.from_dict(policy.to_dict())
    assert restored.to_dict() == policy.to_dict()
    assert restored.select([cheap, costly], observation, seed=1) == costly


def test_linucb_seeded_ties_are_reproducible() -> None:
    policy = DiagonalLinUCB("tie-v1", REGISTRY)
    options = [
        {"action_id": "a", "features": {"value": 1, "cost": 1}},
        {"action_id": "b", "features": {"value": 1, "cost": 1}},
    ]
    assert policy.select(options, {"load": 0}, 42) == policy.select(options, {"load": 0}, 42)
