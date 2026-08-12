# Path: tests/test_training_scenarios.py
# Purpose: Verify deterministic mini-environment contracts before live surface provisioning.

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.generate_training_scenarios import main
from training.canonical import canonical_sha256, plan_hash, policy_hash, scenario_hash
from training.contracts import validate_scenario, validate_transition
from training.scenarios.mining_delivery import (
    generate_mining_delivery_curriculum,
    generate_mining_delivery_scenario,
)


def _valid_transition(scenario: dict) -> dict:
    return {
        "version": "1.2.0",
        "episode_id": "episode-000001",
        "scenario_id": scenario["scenario_id"],
        "scenario_seed": scenario["seed"],
        "scenario_hash": scenario["scenario_hash"],
        "policy_id": "deterministic-baseline-v1",
        "policy_hash": policy_hash({"name": "deterministic-baseline", "version": 1}),
        "started_tick": 100,
        "ended_tick": 700,
        "observation": {"delivered_rate_per_tick": 0.0},
        "candidates": [{
            "action_id": "direct-belt-east",
            "plan_hash": plan_hash({"phases": []}),
            "features": {"route_tiles": 42.0, "pole_count": 5.0},
        }],
        "chosen_action_id": "direct-belt-east",
        "result": {"status": "completed", "failure_kind": "none", "reason": "target rate sustained"},
        "metrics": {
            "initial_rate_per_tick": 0.0,
            "final_rate_per_tick": 1.0 / 60.0,
            "delivered_items": 600,
            "material_cost": 52,
            "placements_succeeded": 48,
            "placements_failed": 0,
            "electric_pole_count": 5,
            "collection_belt_tiles": 10,
            "actual_delivery_route_tiles": 32,
            "shortest_delivery_route_tiles": 30,
            "route_excess_tiles": 2,
            "route_efficiency": 30.0 / 32.0,
            "occupied_footprint_tiles": 80,
            "placed_mining_drills": 3,
            "productive_mining_drills": 3,
            "productive_mining_drill_ratio": 1.0,
            "mining_drill_capacity_ticks": 1_800,
            "mining_drill_working_ticks": 1_800,
            "mining_drill_blocked_ticks": 0,
            "mining_drill_idle_ticks": 0,
        },
        "reward": {
            "completion": 10.0,
            "throughput": 1.0,
            "elapsed_ticks": -0.1,
            "materials": -0.52,
            "poles": -0.2,
            "route_excess": -0.04,
            "land_usage": -0.08,
            "unproductive_drill_capacity": 0.0,
            "failed_placements": 0.0,
            "total": 10.06,
        },
        "next_observation": {"delivered_rate_per_tick": 1.0 / 60.0},
    }


def test_seeded_mining_delivery_scenario_is_stable_and_valid() -> None:
    first = generate_mining_delivery_scenario(42)
    second = generate_mining_delivery_scenario(42)

    assert first == second
    validate_scenario(first)
    assert first["version"] == "1.2.0"
    assert first["reward_profile"] == "mining-efficiency-v1"
    assert set(first["reward_weights"]) == {
        "completion", "throughput", "elapsed_tick", "material_item",
        "failed_placement", "pole", "route_excess", "land",
        "unproductive_drill_capacity",
    }
    assert first["scenario_id"] == "mining-delivery-002a"
    assert first["scenario_hash"] == scenario_hash(first)
    assert first["family"] == "mining_delivery"
    assert first["environment"]["surface_name"] == "training/mining-delivery-002a"
    assert first["objective"]["item"] == first["resource_patch"]["resource"]
    assert first["objective"]["destination_fixture_id"] == "delivery-sink"
    assert first["fixtures"][0]["position"] == [0, 0]
    assert "target_rate_per_second" not in first["objective"]
    assert first["objective"]["target_rate_per_tick"] > 0


def test_scenario_ids_are_short_and_seed_range_matches_the_identity() -> None:
    assert generate_mining_delivery_scenario(0xFFFF)["scenario_id"] == "mining-delivery-ffff"
    with pytest.raises(ValueError, match="65535"):
        generate_mining_delivery_scenario(0x10000)

def test_curriculum_generates_one_hundred_unique_bounded_scenarios() -> None:
    scenarios = generate_mining_delivery_curriculum(count=100, start_seed=1000)

    assert len(scenarios) == 100
    assert len({scenario["scenario_id"] for scenario in scenarios}) == 100
    assert len({scenario["seed"] for scenario in scenarios}) == 100
    for scenario in scenarios:
        validate_scenario(scenario)
        bounds = scenario["environment"]["bounds"]
        destination = scenario["fixtures"][1]["position"]
        patch = scenario["resource_patch"]["bounds"]
        assert bounds["x_min"] <= destination[0] < bounds["x_max_exclusive"]
        assert bounds["y_min"] <= destination[1] < bounds["y_max_exclusive"]
        assert bounds["x_min"] <= patch["x1"] <= patch["x2"] < bounds["x_max_exclusive"]
        assert bounds["y_min"] <= patch["y1"] <= patch["y2"] < bounds["y_max_exclusive"]


def test_mining_scenario_budget_contains_construction_items_only() -> None:
    scenario = generate_mining_delivery_scenario(7)
    forbidden = {
        "iron-ore", "copper-ore", "coal", "stone",
        "iron-plate", "copper-plate", "steel-plate",
    }

    assert not forbidden & set(scenario["construction_budget"])
    assert scenario["construction_budget"]["electric-mining-drill"] >= 1
    assert scenario["construction_budget"]["transport-belt"] >= 1
    assert scenario["constraints"]["allow_fixture_deconstruction"] is False


def test_contract_rejects_objective_and_resource_drift() -> None:
    scenario = deepcopy(generate_mining_delivery_scenario(9))
    scenario["objective"]["item"] = "copper-ore"
    scenario["scenario_hash"] = scenario_hash(scenario)

    with pytest.raises(ValueError, match="objective item"):
        validate_scenario(scenario)


def test_contract_rejects_fixture_and_budget_cheats() -> None:
    fixture = deepcopy(generate_mining_delivery_scenario(10))
    fixture["fixtures"][1]["entity"] = "steel-chest"
    fixture["scenario_hash"] = scenario_hash(fixture)
    with pytest.raises(ValueError, match="instrumentation entity"):
        validate_scenario(fixture)

    budget = deepcopy(generate_mining_delivery_scenario(10))
    budget["construction_budget"]["iron-plate"] = 100
    budget["constraints"]["allowed_entities"].append("iron-plate")
    budget["scenario_hash"] = scenario_hash(budget)
    with pytest.raises(ValueError, match="construction_budget"):
        validate_scenario(budget)


def test_contract_rejects_nonintegral_power_fixture() -> None:
    scenario = deepcopy(generate_mining_delivery_scenario(11))
    scenario["fixtures"][0]["position"] = [0.5, 0.5]
    scenario["scenario_hash"] = scenario_hash(scenario)

    with pytest.raises(ValueError, match="integral"):
        validate_scenario(scenario)


def test_contract_schemas_are_pinned_for_the_controller_lifetime(monkeypatch) -> None:
    scenario = generate_mining_delivery_scenario(16)
    transition = _valid_transition(scenario)

    def reject_runtime_schema_read(*_args, **_kwargs):
        raise AssertionError("running controllers must not reload schemas from disk")

    monkeypatch.setattr(Path, "read_text", reject_runtime_schema_read)
    validate_scenario(scenario)
    validate_transition(transition)

def test_contract_version_is_explicit_and_old_versions_fail_closed() -> None:
    scenario = deepcopy(generate_mining_delivery_scenario(11))
    scenario["version"] = "1.0.0"
    scenario["scenario_hash"] = scenario_hash(scenario)

    with pytest.raises(ValueError, match="1.2.0"):
        validate_scenario(scenario)
def test_transition_contract_records_choice_outcome_and_reward() -> None:
    transition = _valid_transition(generate_mining_delivery_scenario(12))

    validate_transition(transition)
    invalid = deepcopy(transition)
    invalid["chosen_action_id"] = "missing-candidate"
    with pytest.raises(ValueError, match="chosen_action_id"):
        validate_transition(invalid)


def test_canonical_hashes_ignore_mapping_order_and_bind_semantic_changes() -> None:
    left = {"b": [2, 3], "a": 1}
    right = {"a": 1, "b": [2, 3]}

    assert canonical_sha256(left) == canonical_sha256(right)
    assert plan_hash(left) == plan_hash(right)
    assert policy_hash(left) == policy_hash(right)
    assert plan_hash(left) != plan_hash({"a": 1, "b": [2, 4]})


def test_scenario_hash_excludes_only_its_own_hash_field() -> None:
    scenario = generate_mining_delivery_scenario(13)
    changed_hash = deepcopy(scenario)
    changed_hash["scenario_hash"] = "sha256:" + "f" * 64
    changed_payload = deepcopy(scenario)
    changed_payload["objective"]["sustain_ticks"] += 1

    assert scenario_hash(changed_hash) == scenario["scenario_hash"]
    assert scenario_hash(changed_payload) != scenario["scenario_hash"]


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_contracts_reject_non_finite_numbers_recursively(bad_value: float) -> None:
    scenario = deepcopy(generate_mining_delivery_scenario(14))
    scenario["objective"]["target_rate_per_tick"] = bad_value
    with pytest.raises(ValueError, match="finite"):
        validate_scenario(scenario)

    transition = _valid_transition(generate_mining_delivery_scenario(14))
    transition["candidates"][0]["features"]["route_tiles"] = bad_value
    with pytest.raises(ValueError, match="finite"):
        validate_transition(transition)


def test_contract_rejects_scenario_hash_drift() -> None:
    scenario = deepcopy(generate_mining_delivery_scenario(15))
    scenario["resource_patch"]["amount_per_tile"] += 1

    with pytest.raises(ValueError, match="scenario_hash"):
        validate_scenario(scenario)


def test_cli_prints_a_deterministic_scenario_batch(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--count", "3", "--start-seed", "11"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert [scenario["seed"] for scenario in payload] == [11, 12, 13]
    assert payload == generate_mining_delivery_curriculum(count=3, start_seed=11)
