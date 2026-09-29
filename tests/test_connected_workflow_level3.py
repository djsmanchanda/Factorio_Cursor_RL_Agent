# Path: tests/test_connected_workflow_level3.py
# Purpose: Protect the connected-workflow level-3 second-product (circuits) objective without disturbing levels 1-2.

from __future__ import annotations

import copy
import math

import pytest

from orchestrator.laya_state import render_state
from tests.test_laya_policy import _state_builder
from training.canonical import scenario_hash
from training.candidates.connected_workflow import (
    BRANCH_IDS,
    branch_specs,
    connected_workflow_candidates,
)
from training.contracts import validate_scenario
from training.laya_branch_decisions import compact_workflow_digest, prepare_serve_goal
from training.laya_work_graph import project_work_graph
from training.laya_workflow_observation import observe_workflow
from training.scenarios.connected_workflow import (
    BUDGET,
    BUDGET_LEVEL2,
    BUDGET_LEVEL3,
    generate_connected_workflow_scenario,
)

_SIZE = {"electric-furnace": 3, "assembling-machine-2": 3}
_STEP = {"east": (1, 0), "west": (-1, 0), "north": (0, -1), "south": (0, 1)}

# Captured from the pre-existing generator before level-3 work began; any
# change to these values means level 1/2 scenarios stopped being byte-identical.
_LEVEL1_HASHES = {
    0: "sha256:cbbbfdcdd39f556715cfab834f7f356fe99d791db9024366fdd0b11b02360a7e",
    1: "sha256:e2cf6ffc4e7b7b78d247652365f62f5c87e6ea641a50cd1f5f3135bfe79fe3ac",
    3: "sha256:71f47027940935d09f6f4ecb5199a0cedc532431d4f1e1a4783edddca16683e5",
    7: "sha256:3bfbc155c8a862ff5ce19b7e02c8ef09e2d5f1ba3a8c96a76767a20ab1554643",
    311: "sha256:028ebfee624243b171b85a249e40c12b0ea38b4b61f95a31ec96063711131590",
    65535: "sha256:c41053e723e4edb2640f9169d0171cccc8f413632c4b7d6d31747ab9c1679a68",
}
_LEVEL2_HASHES = {
    0: "sha256:fba8d649cd6d9d2d130924bbe40df140f660ff3814c9964030ecef72e197cdc2",
    1: "sha256:8d97895a6e34ccf44eac5d081047115ea12f33b2cbc9470d9e3ba07db311aa90",
    3: "sha256:0570654b23fd965bab08914ad81429e31c8dfc195ae3dac9fa7fc61688a33465",
    7: "sha256:18fad2295374f13e62ec260a94c965eea6610cc259fb3b4a0c05bdb6eb523a98",
    311: "sha256:fb2870d3806927ec2b49734d523a6f73077eb0eb912888b740f15b57c4c7422f",
    65535: "sha256:768f5e31c9dc1b5d31641d4624587d59288455b9fd2443b577ebb9bb59ef56d7",
}


def _tiles(entity: str, x: float, y: float) -> set[tuple[int, int]]:
    radius = _SIZE.get(entity, 1) // 2
    return {(math.floor(x) + dx, math.floor(y) + dy)
            for dx in range(-radius, radius + 1) for dy in range(-radius, radius + 1)}


def _world(scenario):
    owner = {}
    for fixture in scenario["fixtures"]:
        if fixture["entity"] == "infinity-chest":
            owner[tuple(fixture["position"])] = fixture["id"]
    actions = []
    first = {}
    for candidate in connected_workflow_candidates(scenario):
        first.setdefault(candidate["branch_id"], candidate)
    for candidate in first.values():
        for action in candidate["plan"]["phases"][0]["actions"]:
            actions.append((candidate["branch_id"], action))
            for tile in _tiles(action["entity"], action["position"]["x"], action["position"]["y"]):
                assert tile not in owner, f"{candidate['branch_id']}:{action} overlaps {owner[tile]}"
                owner[tile] = action["entity"]
    return owner, actions


def test_level1_and_level2_scenarios_stay_byte_identical() -> None:
    for seed, expected_hash in _LEVEL1_HASHES.items():
        scenario = generate_connected_workflow_scenario(seed)
        assert scenario["scenario_hash"] == expected_hash
        assert scenario["curriculum"]["level"] == 1
        assert scenario["construction_budget"] == BUDGET
    for seed, expected_hash in _LEVEL2_HASHES.items():
        scenario = generate_connected_workflow_scenario(seed, level=2)
        assert scenario["scenario_hash"] == expected_hash
        assert scenario["curriculum"]["level"] == 2
        assert scenario["construction_budget"] == BUDGET_LEVEL2
        assert "requirements" not in scenario["objective"]


def test_level3_scenario_validates_and_randomizes_both_targets() -> None:
    belt_targets, circuit_targets = set(), set()
    for seed in (0, 1, 3, 7, 311, 65535):
        scenario = generate_connected_workflow_scenario(seed, level=3)
        assert scenario["curriculum"]["level"] == 3
        assert scenario["construction_budget"] == BUDGET_LEVEL3
        assert len(scenario["fixtures"]) == 6
        requirements = scenario["objective"]["requirements"]
        assert len(requirements) == 1
        circuit = requirements[0]
        assert circuit["id"] == "circuits" and circuit["item"] == "electronic-circuit"
        assert circuit["destination_fixture_id"] == "circuit-sink"
        belt_targets.add(scenario["objective"]["target_rate_per_tick"])
        circuit_targets.add(circuit["target_rate_per_tick"])
    assert len(belt_targets) > 1
    assert len(circuit_targets) > 1  # genuinely randomized per seed, not a fixed constant


def test_level3_scenario_is_deterministic_per_seed() -> None:
    first = generate_connected_workflow_scenario(42, level=3)
    second = generate_connected_workflow_scenario(42, level=3)
    assert first["scenario_hash"] == second["scenario_hash"]


def test_level3_ore_source_copper_fills_copper_ore() -> None:
    scenario = generate_connected_workflow_scenario(3, level=3)
    copper_source = next(f for f in scenario["fixtures"] if f["id"] == "ore-source-copper")
    assert copper_source["fill_item"] == "copper-ore"
    circuit_sink = next(f for f in scenario["fixtures"] if f["id"] == "circuit-sink")
    assert circuit_sink["kind"] == "item_sink"


def test_level3_branch_plans_never_overlap_across_all_variants() -> None:
    for seed in (0, 1, 3, 7, 311, 65535):
        scenario = generate_connected_workflow_scenario(seed, level=3)
        owner, actions = _world(scenario)
        counts: dict[str, int] = {}
        for _, action in actions:
            counts[action["entity"]] = counts.get(action["entity"], 0) + 1
        assert counts == {entity: count for entity, count in BUDGET_LEVEL3.items()
                          if entity not in {"assembling-machine-1", "inserter"}}


def test_copper_cable_circuits_feed_chain_directions() -> None:
    """Every new inserter picks up from its declared source and drops to its target."""
    scenario = generate_connected_workflow_scenario(3, level=3)
    owner, actions = _world(scenario)

    def moves_for(branch_id: str) -> list[tuple[str | None, str | None]]:
        result = []
        for candidate_branch, action in actions:
            if action["entity"] != "fast-inserter" or candidate_branch != branch_id:
                continue
            x, y = math.floor(action["position"]["x"]), math.floor(action["position"]["y"])
            dx, dy = _STEP[action["direction"]]
            result.append((owner.get((x + dx, y + dy)), owner.get((x - dx, y - dy))))
        return result

    assert sorted(moves_for("copper")) == sorted([
        ("ore-source-copper", "electric-furnace"), ("electric-furnace", "iron-chest"),
    ])
    assert sorted(moves_for("cable")) == sorted([
        ("iron-chest", "assembling-machine-2"), ("assembling-machine-2", "iron-chest"),
    ])
    assert sorted(moves_for("circuits")) == sorted([
        ("electric-furnace", "iron-chest"), ("iron-chest", "assembling-machine-2"),
        ("iron-chest", "assembling-machine-2"), ("assembling-machine-2", "circuit-sink"),
    ])


def test_level1_branch_ids_and_variants_are_unaffected_by_level3() -> None:
    assert set(BRANCH_IDS) == {"iron", "gears", "belts", "construction"}
    scenario = generate_connected_workflow_scenario(5)
    by_branch = {}
    for candidate in connected_workflow_candidates(scenario):
        by_branch.setdefault(candidate["branch_id"], []).append(candidate)
    assert set(by_branch) == set(BRANCH_IDS)


def test_branch_gating_copper_cable_circuits() -> None:
    scenario = generate_connected_workflow_scenario(3, level=3)
    specs = branch_specs(scenario)
    ids = {spec["branch_id"] for spec in specs}
    assert ids == {"iron", "gears", "belts", "construction", "iron2", "copper", "cable", "circuits"}
    by_id = {spec["branch_id"]: spec for spec in specs}
    assert by_id["cable"]["requires"] == ["copper"]
    assert by_id["circuits"]["requires"] == ["iron", "cable"]
    assert by_id["copper"].get("requires") in (None, [])
    # Level 2's iron2 gate (a raw electric-furnace count of 2) is unambiguous
    # there, but level 3 also has a "copper" branch that builds its own
    # furnace, so counting to 2 there would falsely close iron2 the instant
    # copper exists -- measured live, before this fix, iron2 was never built
    # and iron-plate output stayed pinned at one furnace's ~0.6/s. Level 3
    # must count to 3 (iron + copper + iron2).
    assert by_id["iron2"]["target_minimum"] == 3

    surface, force = scenario["environment"]["surface_name"], scenario["environment"]["force_name"]
    base_report = {
        "runtime": "disposable_training", "surface": surface, "force": force,
        "items": {},
    }
    # Nothing measured yet: copper is open (no prerequisite), cable/circuits blocked.
    graph, _ = observe_workflow(base_report, specs)
    statuses = {branch.branch_id: branch.status for branch in graph.branches}
    assert statuses["copper"] == "open"
    assert statuses["cable"] == "blocked"
    assert statuses["circuits"] == "blocked"

    # Copper closes; cable becomes runnable, circuits still needs iron too.
    copper_closed_report = dict(base_report)
    copper_closed_report["items"] = {
        "copper-plate": {"production_rate": 0.4 / 60.0 + 0.001, "production_sustained_ticks": 600},
    }
    graph, _ = observe_workflow(copper_closed_report, specs)
    statuses = {branch.branch_id: branch.status for branch in graph.branches}
    assert statuses["copper"] == "closed"
    assert statuses["cable"] == "open"
    assert statuses["circuits"] == "blocked"

    # Iron and cable both close: circuits becomes runnable.
    ready_report = dict(base_report)
    ready_report["items"] = {
        "iron-plate": {"production_rate": 0.4 / 60.0 + 0.001, "production_sustained_ticks": 600},
        "copper-plate": {"production_rate": 0.4 / 60.0 + 0.001, "production_sustained_ticks": 600},
        "copper-cable": {"production_rate": 0.05 / 60.0 + 0.001, "production_sustained_ticks": 600},
    }
    graph, _ = observe_workflow(ready_report, specs)
    statuses = {branch.branch_id: branch.status for branch in graph.branches}
    assert statuses["iron"] == "closed"
    assert statuses["cable"] == "closed"
    assert statuses["circuits"] == "open"


def test_iron2_does_not_falsely_close_when_only_copper_built() -> None:
    """Regression: measured live, iron+copper alone used to satisfy iron2's

    old target_minimum=2 furnace count, closing iron2 before its own furnace
    ever existed -- the branch then vanished from "runnable" forever (a
    closed branch is never offered again), and iron-plate output stayed
    pinned at one furnace's ~0.6/s for the rest of the episode.
    """
    scenario = generate_connected_workflow_scenario(3, level=3)
    specs = branch_specs(scenario)
    surface, force = scenario["environment"]["surface_name"], scenario["environment"]["force_name"]

    # "iron" itself already closed (so only the furnace-count gate remains)
    # plus iron + copper built (2 electric-furnaces total): iron2 must stay
    # open, not closed by copper's furnace standing in for its own.
    two_furnaces_report = {
        "runtime": "disposable_training", "surface": surface, "force": force,
        "items": {
            "iron-plate": {"production_rate": 0.4 / 60.0 + 0.001, "production_sustained_ticks": 600},
            "electric-furnace": {"metrics": {"built_count": {"value": 2, "sustained_ticks": 0}}},
        },
    }
    graph, _ = observe_workflow(two_furnaces_report, specs)
    statuses = {branch.branch_id: branch.status for branch in graph.branches}
    assert statuses["iron"] == "closed"
    assert statuses["iron2"] != "closed"

    # iron + copper + iron2's own furnace (3 total): now it may close.
    three_furnaces_report = copy.deepcopy(two_furnaces_report)
    three_furnaces_report["items"]["electric-furnace"]["metrics"]["built_count"]["value"] = 3
    graph, _ = observe_workflow(three_furnaces_report, specs)
    statuses = {branch.branch_id: branch.status for branch in graph.branches}
    assert statuses["iron2"] == "closed"


def test_multi_product_objective_schema_and_semantics() -> None:
    scenario = generate_connected_workflow_scenario(3, level=3)
    validate_scenario(scenario)  # sanity: a real level-3 scenario is valid

    bad_destination = copy.deepcopy(scenario)
    bad_destination["objective"]["requirements"][0]["destination_fixture_id"] = "ore-source"
    bad_destination["scenario_hash"] = scenario_hash(bad_destination)
    with pytest.raises(ValueError):
        validate_scenario(bad_destination)

    duplicate_ids = copy.deepcopy(scenario)
    duplicate_ids["objective"]["requirements"].append(dict(duplicate_ids["objective"]["requirements"][0]))
    duplicate_ids["scenario_hash"] = scenario_hash(duplicate_ids)
    with pytest.raises(ValueError):
        validate_scenario(duplicate_ids)

    misplaced_fill_item = copy.deepcopy(scenario)
    misplaced_fill_item["fixtures"][0]["fill_item"] = "copper-ore"
    misplaced_fill_item["scenario_hash"] = scenario_hash(misplaced_fill_item)
    with pytest.raises(ValueError):
        validate_scenario(misplaced_fill_item)

    too_many_fixtures = copy.deepcopy(scenario)
    for index in range(3):
        extra = dict(too_many_fixtures["fixtures"][-1])
        extra["id"] = f"extra-sink-{index}"
        too_many_fixtures["fixtures"].append(extra)
    too_many_fixtures["scenario_hash"] = scenario_hash(too_many_fixtures)
    with pytest.raises(ValueError):
        validate_scenario(too_many_fixtures)


def test_level3_digest_exposes_the_secondary_circuit_target() -> None:
    scenario = generate_connected_workflow_scenario(65535, level=3)
    specs = branch_specs(scenario)
    requirements = scenario["objective"]["requirements"]
    report = {
        "runtime": "disposable_training",
        "surface": scenario["environment"]["surface_name"], "force": scenario["environment"]["force_name"],
        "items": {
            "iron-plate": {"production_rate": 0.4 / 60.0 + 0.001, "production_sustained_ticks": 600},
            "copper-plate": {"production_rate": 0.4 / 60.0 + 0.001, "production_sustained_ticks": 600},
            "copper-cable": {"production_rate": 0.05 / 60.0 + 0.001, "production_sustained_ticks": 600},
            "electronic-circuit": {"production_rate": 0.03, "production_sustained_ticks": 300},
        },
    }
    graph, supply = observe_workflow(report, specs)
    projection = project_work_graph(graph)
    digest = compact_workflow_digest(projection, supply, requirements)
    assert "circuits" in digest["secondary_targets"]
    circuits_target = digest["secondary_targets"]["circuits"]
    assert circuits_target["item"] == "electronic-circuit"
    assert circuits_target["target_rate_per_tick"] == pytest.approx(requirements[0]["target_rate_per_tick"])
    assert circuits_target["output_rate"] == pytest.approx(0.03)


def test_level3_digest_stays_under_the_2048_token_director_budget() -> None:
    scenario = generate_connected_workflow_scenario(65535, level=3)
    specs = branch_specs(scenario)
    surface, force = scenario["environment"]["surface_name"], scenario["environment"]["force_name"]
    report = {
        "runtime": "disposable_training", "surface": surface, "force": force,
        "items": {
            "iron-plate": {"production_rate": 0.4 / 60.0 + 0.001, "production_sustained_ticks": 600,
                          "draw_rate": 0.3 / 60.0, "stock": 40.0},
            "iron-gear-wheel": {"production_rate": None, "production_sustained_ticks": None},
            "transport-belt": {"production_rate": None, "production_sustained_ticks": None},
            "copper-plate": {"production_rate": 0.4 / 60.0 + 0.001, "production_sustained_ticks": 600},
            "copper-cable": {"production_rate": 0.05 / 60.0 + 0.001, "production_sustained_ticks": 600},
            "electronic-circuit": {"production_rate": None, "production_sustained_ticks": None},
            "medium-electric-pole": {"metrics": {"built_count": {"value": 2, "sustained_ticks": 0}}},
            "electric-furnace": {"metrics": {"built_count": {"value": 1, "sustained_ticks": 0}}},
        },
    }
    graph, supply = observe_workflow(report, specs)
    base_state = _state_builder({}, [])
    base_state["run"]["surface"], base_state["run"]["force"] = surface, force
    prepared, options, _ = prepare_serve_goal(base_state, graph, supply, max_options=12, max_items=8)
    assert options  # runnable branches were actually offered
    rendered = render_state(prepared, budget_profile="director-2048")
    assert rendered  # renders without raising LayaStateError
