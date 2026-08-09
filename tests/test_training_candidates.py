# Path: tests/test_training_candidates.py
# Purpose: Prove seeded training catalogs remain safe, distinct, and budget bounded.

from planners.plan_validation import actions, validate_build_plan
from training.candidates import mining_delivery_candidates
from training.canonical import plan_hash
from training.scenarios.mining_delivery import generate_mining_delivery_scenario


def test_one_hundred_scenarios_compile_two_valid_candidates_each():
    for seed in range(100):
        scenario = generate_mining_delivery_scenario(seed)
        candidates = mining_delivery_candidates(scenario)
        assert len(candidates) == 2
        assert len({candidate["plan_hash"] for candidate in candidates}) == 2
        for candidate in candidates:
            validate_build_plan(candidate["plan"])
            assert candidate["plan_hash"] == plan_hash(candidate["plan"])
            assert candidate["plan"]["surface"].startswith("training/")
            assert candidate["plan"]["force"].startswith("training-")


def test_candidates_only_place_budgeted_entities():
    scenario = generate_mining_delivery_scenario(7)
    for candidate in mining_delivery_candidates(scenario):
        counts = {}
        for action in actions(candidate["plan"]):
            counts[action["entity"]] = counts.get(action["entity"], 0) + 1
        assert set(counts) <= set(scenario["construction_budget"])
        assert all(count <= scenario["construction_budget"][name] for name, count in counts.items())


def test_candidate_catalog_is_seed_deterministic():
    scenario = generate_mining_delivery_scenario(42)
    assert mining_delivery_candidates(scenario) == mining_delivery_candidates(scenario)
