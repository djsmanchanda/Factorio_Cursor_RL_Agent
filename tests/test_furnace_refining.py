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


def test_furnace_inserters_pick_from_source_and_drop_into_sink() -> None:
    # Live 2.1.20 probe: inserters pick up on the side they face; "east" fed the
    # furnace from the plate sink and never produced a plate.
    from training.candidates import furnace_refining_candidates
    from training.scenarios.furnace_refining import generate_furnace_refining_scenario

    scenario = generate_furnace_refining_scenario(201)
    fixtures = {fixture["id"]: fixture["position"] for fixture in scenario["fixtures"]}
    step = {"east": 1, "west": -1}
    for candidate in furnace_refining_candidates(scenario):
        actions = candidate["plan"]["phases"][0]["actions"]
        furnace = next(a for a in actions if a["entity"] == "electric-furnace")["position"]["x"]
        inserters = sorted(
            (a for a in actions if a["entity"] == "fast-inserter"), key=lambda a: a["position"]["x"],
        )
        pickups = [a["position"]["x"] + step[a["direction"]] for a in inserters]
        drops = [a["position"]["x"] - step[a["direction"]] for a in inserters]
        assert pickups[0] == fixtures["ore-source"][0]
        assert abs(drops[0] - furnace) <= 1.5 and abs(pickups[1] - furnace) <= 1.5
        assert drops[1] == fixtures["plate-sink"][0]
