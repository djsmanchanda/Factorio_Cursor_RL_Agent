# Path: tests/test_training_policies.py
# Purpose: Verify deterministic feature and policy learning behavior.

from __future__ import annotations

from training.features import (
    MINING_DELIVERY_FEATURES_V1, MINING_DELIVERY_FEATURES_V2, MINING_DELIVERY_FEATURES_V3,
    FeatureRegistry, policy_features,
)
from training.policies import (
    DeterministicBaseline, DiagonalLinUCB,
    transition_can_train_policy, transition_can_update_policy,
)


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


def test_linucb_seeded_exploration_visits_multiple_candidates_and_round_trips() -> None:
    policy = DiagonalLinUCB("exploring-v1", REGISTRY, exploration_rate=1.0)
    options = [
        {"action_id": "a", "features": {"value": 1, "cost": 1}},
        {"action_id": "b", "features": {"value": 4, "cost": 10}},
    ]
    selected = [policy.select(options, {"load": 0}, seed)["action_id"] for seed in range(20)]

    assert set(selected) == {"a", "b"}
    assert selected == [policy.select(options, {"load": 0}, seed)["action_id"] for seed in range(20)]
    assert DiagonalLinUCB.from_dict(policy.to_dict()).exploration_rate == 1.0

def test_mining_efficiency_registry_exposes_route_quality() -> None:
    names = set(MINING_DELIVERY_FEATURES_V2.names)
    assert {
        "candidate.collection_belt_tiles", "candidate.actual_delivery_route_tiles",
        "candidate.shortest_delivery_route_tiles", "candidate.route_excess_tiles",
        "candidate.route_efficiency",
    } <= names


def test_fresh_registry_exposes_measured_capacity_and_bottleneck_evidence() -> None:
    names = set(MINING_DELIVERY_FEATURES_V3.names)
    assert {
        "observation.placed_mining_drills",
        "observation.capacity_audit_available",
        "observation.productive_mining_drill_ratio",
        "observation.mining_drill_blocked_ticks",
        "candidate.capacity_margin_per_tick",
    } <= names


def test_safe_partial_outcome_can_learn_but_cannot_support_promotion() -> None:
    transition = {"result": {"status": "timed_out", "failure_kind": "timeout"}}
    assert transition_can_update_policy(transition)
    assert not transition_can_train_policy(transition)


def test_infrastructure_and_hard_failures_never_become_policy_evidence() -> None:
    for status, kind in (
        ("failed", "safety"), ("failed", "identity"), ("failed", "budget"),
        ("failed", "fixture"), ("failed", "capability"), ("failed", "execution"),
        ("failed", "power_unconnected"),
    ):
        assert not transition_can_update_policy({"result": {"status": status, "failure_kind": kind}})


def test_older_checkpoint_ignores_new_candidate_evidence() -> None:
    policy = DiagonalLinUCB("legacy-v1", MINING_DELIVERY_FEATURES_V1)
    candidate = candidates()[0] | {"features": {
        **candidates()[0]["features"], "route_excess_tiles": 12,
        "route_efficiency": 0.6,
    }}

    assert policy.score({"delivered_rate_per_tick": 0}, candidate) >= 0
