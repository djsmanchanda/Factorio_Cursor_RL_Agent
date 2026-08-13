from training.candidates.furnace_refining import furnace_refining_candidates
from training.scenarios.furnace_refining import generate_furnace_refining_scenario, generate_furnace_refining_curriculum


def test_furnace_contract_and_catalog_are_distinct():
    scenario = generate_furnace_refining_scenario(7)
    candidates = furnace_refining_candidates(scenario)
    assert scenario["family"] == "furnace_refining"
    assert scenario["objective"]["kind"] == "smelt_item_rate"
    assert len(candidates) == 2
    assert candidates[0]["plan_hash"] != candidates[1]["plan_hash"]
    assert all(candidate["features"]["furnace_count"] == 1 for candidate in candidates)


def test_furnace_curriculum_is_seeded_and_unique():
    scenarios = generate_furnace_refining_curriculum(8, 10)
    assert [scenario["seed"] for scenario in scenarios] == list(range(10, 18))
    assert len({scenario["scenario_hash"] for scenario in scenarios}) == 8
