# Path: tests/test_training_scenarios.py
# Purpose: Verify deterministic mini-environment contracts before live surface provisioning.

from __future__ import annotations

from copy import deepcopy
import json

import pytest

from tools.generate_training_scenarios import main
from training.contracts import validate_scenario, validate_transition
from training.scenarios.mining_delivery import (
    generate_mining_delivery_curriculum,
    generate_mining_delivery_scenario,
)


def test_seeded_mining_delivery_scenario_is_stable_and_valid() -> None:
    first = generate_mining_delivery_scenario(42)
    second = generate_mining_delivery_scenario(42)

    assert first == second
    validate_scenario(first)
    assert first["scenario_id"] == "mining-delivery-0000002a"
    assert first["family"] == "mining_delivery"
    assert first["environment"]["surface_name"] == "training/mining-delivery-0000002a"
    assert first["objective"]["item"] == first["resource_patch"]["resource"]
    assert first["objective"]["destination_fixture_id"] == "delivery-sink"


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

    with pytest.raises(ValueError, match="objective item"):
        validate_scenario(scenario)


def test_contract_rejects_fixture_and_budget_cheats() -> None:
    fixture = deepcopy(generate_mining_delivery_scenario(10))
    fixture["fixtures"][1]["entity"] = "steel-chest"
    with pytest.raises(ValueError, match="instrumentation entity"):
        validate_scenario(fixture)

    budget = deepcopy(generate_mining_delivery_scenario(10))
    budget["construction_budget"]["iron-plate"] = 100
    budget["constraints"]["allowed_entities"].append("iron-plate")
    with pytest.raises(ValueError, match="construction_budget"):
        validate_scenario(budget)


def test_transition_contract_records_choice_outcome_and_reward() -> None:
    scenario = generate_mining_delivery_scenario(12)
    transition = {
        "version": "1.0.0",
        "episode_id": "episode-000001",
        "scenario_id": scenario["scenario_id"],
        "scenario_seed": scenario["seed"],
        "policy_id": "deterministic-baseline-v1",
        "started_tick": 100,
        "ended_tick": 700,
        "observation": {"delivered_rate_per_second": 0.0},
        "candidates": [
            {
                "action_id": "direct-belt-east",
                "plan_hash": "sha256:" + "a" * 64,
                "features": {"route_tiles": 42.0, "pole_count": 5.0},
            }
        ],
        "chosen_action_id": "direct-belt-east",
        "result": {
            "status": "completed",
            "failure_kind": "none",
            "reason": "target rate sustained",
        },
        "metrics": {
            "initial_rate_per_second": 0.0,
            "final_rate_per_second": 1.0,
            "delivered_items": 600,
            "material_cost": 52,
            "placements_succeeded": 48,
            "placements_failed": 0,
        },
        "reward": {
            "completion": 10.0,
            "throughput": 1.0,
            "elapsed_ticks": -0.1,
            "materials": -0.52,
            "infrastructure": -0.2,
            "failed_placements": 0.0,
            "total": 10.18,
        },
        "next_observation": {"delivered_rate_per_second": 1.0},
    }

    validate_transition(transition)
    invalid = deepcopy(transition)
    invalid["chosen_action_id"] = "missing-candidate"
    with pytest.raises(ValueError, match="chosen_action_id"):
        validate_transition(invalid)


def test_cli_prints_a_deterministic_scenario_batch(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--count", "3", "--start-seed", "11"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert [scenario["seed"] for scenario in payload] == [11, 12, 13]
    assert payload == generate_mining_delivery_curriculum(count=3, start_seed=11)
