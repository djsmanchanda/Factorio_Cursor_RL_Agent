# Path: tests/test_training_power.py
# Purpose: Keep future electricity-source and accumulator roles explicit in training contracts.

from copy import deepcopy

import pytest

from training.candidates import mining_delivery_candidates
from training.canonical import scenario_hash
from training.contracts import validate_scenario
from training.power import POWER_CONSUMER_ENTITIES, POWER_SOURCE_ENTITIES, POWER_STORAGE_ENTITIES, energy_role
from training.scenarios.mining_delivery import generate_mining_delivery_scenario


@pytest.mark.parametrize(
    "entity",
    ["electric-energy-interface", "solar-panel", "steam-engine", "steam-turbine", "fusion-generator"],
)
def test_training_contract_recognizes_every_supported_power_producer(entity: str) -> None:
    scenario = deepcopy(generate_mining_delivery_scenario(24))
    scenario["fixtures"][0]["entity"] = entity
    scenario["scenario_hash"] = scenario_hash(scenario)

    validate_scenario(scenario)
    assert entity in POWER_SOURCE_ENTITIES
    assert energy_role(entity) == "source"


def test_accumulator_is_storage_not_a_power_source() -> None:
    scenario = deepcopy(generate_mining_delivery_scenario(25))
    scenario["fixtures"].append({
        "id": "power-bank", "kind": "power_storage", "entity": "accumulator",
        "position": [6, 0], "protected": True,
    })
    scenario["scenario_hash"] = scenario_hash(scenario)

    validate_scenario(scenario)
    assert "accumulator" in POWER_STORAGE_ENTITIES
    assert energy_role("accumulator") == "storage"
    assert "accumulator" not in POWER_SOURCE_ENTITIES
    assert "fast-inserter" in POWER_CONSUMER_ENTITIES
    assert energy_role("fast-inserter") == "consumer"


def test_contract_rejects_accumulator_as_a_generator() -> None:
    scenario = deepcopy(generate_mining_delivery_scenario(26))
    scenario["fixtures"][0]["entity"] = "accumulator"
    scenario["scenario_hash"] = scenario_hash(scenario)

    with pytest.raises(ValueError, match="electricity producer"):
        validate_scenario(scenario)

def test_candidate_reserves_an_accumulator_fixture_from_placements() -> None:
    scenario = deepcopy(generate_mining_delivery_scenario(27))
    scenario["fixtures"].append({
        "id": "power-bank", "kind": "power_storage", "entity": "accumulator",
        "position": [6, 0], "protected": True,
    })
    scenario["scenario_hash"] = scenario_hash(scenario)

    candidates = mining_delivery_candidates(scenario)

    assert len(candidates) == 2