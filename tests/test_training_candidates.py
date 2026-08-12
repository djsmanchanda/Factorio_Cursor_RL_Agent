# Path: tests/test_training_candidates.py
# Purpose: Prove seeded training catalogs remain safe, distinct, and budget bounded.

import math

from planners.plan_validation import actions, validate_build_plan
from training.candidates import mining_delivery_candidates
from training.canonical import plan_hash
from training.power import POWER_CONSUMER_ENTITIES
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
            assert {action["action_type"] for action in actions(candidate["plan"])} == {"place_entity"}


def test_collection_row_never_overlaps_an_underground_bridge_endpoint():
    scenario = generate_mining_delivery_scenario(47)

    for candidate in mining_delivery_candidates(scenario):
        placed = [
            (action["entity"], action["position"]["x"], action["position"]["y"])
            for action in actions(candidate["plan"])
        ]
        underground_positions = {
            (x, y) for entity, x, y in placed if entity == "underground-belt"
        }
        transport_positions = {
            (x, y) for entity, x, y in placed if entity == "transport-belt"
        }
        assert not underground_positions & transport_positions

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


def test_candidates_anchor_the_first_pole_inside_power_source_coverage():
    for seed in range(100):
        scenario = generate_mining_delivery_scenario(seed)
        source = next(item for item in scenario["fixtures"] if item["kind"] == "power_source")
        for candidate in mining_delivery_candidates(scenario):
            first = next(
                action["position"] for action in actions(candidate["plan"])
                if action["entity"] == "medium-electric-pole"
            )
            assert math.dist(source["position"], (first["x"], first["y"])) <= 3.5


def test_candidates_supply_the_delivery_inserter_as_well_as_drills():
    for seed in range(100):
        scenario = generate_mining_delivery_scenario(seed)
        for candidate in mining_delivery_candidates(scenario):
            planned = list(actions(candidate["plan"]))
            poles = [action["position"] for action in planned if action["entity"] == "medium-electric-pole"]
            consumers = [
                action["position"] for action in planned
                if action["entity"] in POWER_CONSUMER_ENTITIES
            ]
            assert consumers
            for consumer in consumers:
                assert any(
                    abs(consumer["x"] - pole["x"]) <= 3.5
                    and abs(consumer["y"] - pole["y"]) <= 3.5
                    for pole in poles
                )

def test_high_demand_stress_scenarios_remain_legal_when_compact_capacity_is_exceeded():
    for rate in (10.0, 30.0):
        scenario = generate_mining_delivery_scenario(3000 + int(rate), rate)
        candidates = mining_delivery_candidates(scenario)
        assert len(candidates) == 2
        assert all(candidate["features"]["predicted_rate_per_tick"] * 60 < rate for candidate in candidates)
        for candidate in candidates:
            validate_build_plan(candidate["plan"])
