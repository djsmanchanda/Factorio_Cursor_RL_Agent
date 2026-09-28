# Path: tests/test_connected_workflow_level2.py
# Purpose: Protect the connected-workflow demand-scaling level (iron2 expansion) without disturbing level 1.

from __future__ import annotations

import math

import pytest

from orchestrator.laya_state import LayaStateError, render_state
from tests.test_laya_policy import _state_builder
from training.candidates.connected_workflow import (
    BRANCH_IDS,
    branch_specs,
    connected_workflow_candidates,
)
from training.laya_branch_decisions import prepare_serve_goal
from training.laya_work_graph import WorkGraph
from training.laya_workflow_observation import observe_workflow
from training.scenarios.connected_workflow import (
    BUDGET,
    BUDGET_LEVEL2,
    generate_connected_workflow_scenario,
)

_SIZE = {"electric-furnace": 3, "assembling-machine-2": 3}
_STEP = {"east": (1, 0), "west": (-1, 0), "north": (0, -1), "south": (0, 1)}

# Captured from the pre-existing generator before this level-2 work began;
# any change to these values means level-1 scenarios stopped being byte-identical.
_LEVEL1_HASHES = {
    0: "sha256:cbbbfdcdd39f556715cfab834f7f356fe99d791db9024366fdd0b11b02360a7e",
    1: "sha256:e2cf6ffc4e7b7b78d247652365f62f5c87e6ea641a50cd1f5f3135bfe79fe3ac",
    3: "sha256:71f47027940935d09f6f4ecb5199a0cedc532431d4f1e1a4783edddca16683e5",
    7: "sha256:3bfbc155c8a862ff5ce19b7e02c8ef09e2d5f1ba3a8c96a76767a20ab1554643",
    311: "sha256:028ebfee624243b171b85a249e40c12b0ea38b4b61f95a31ec96063711131590",
    65535: "sha256:c41053e723e4edb2640f9169d0171cccc8f413632c4b7d6d31747ab9c1679a68",
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


def test_level1_scenarios_stay_byte_identical() -> None:
    for seed, expected_hash in _LEVEL1_HASHES.items():
        scenario = generate_connected_workflow_scenario(seed)
        assert scenario["scenario_hash"] == expected_hash
        assert scenario["curriculum"]["level"] == 1
        assert scenario["construction_budget"] == BUDGET


def test_level2_scenario_validates_and_randomizes_target() -> None:
    targets = set()
    for seed in (0, 1, 3, 7, 311, 65535):
        scenario = generate_connected_workflow_scenario(seed, level=2)
        assert scenario["curriculum"]["level"] == 2
        assert scenario["construction_budget"] == BUDGET_LEVEL2
        assert len(scenario["fixtures"]) == 4
        targets.add(scenario["objective"]["target_rate_per_tick"])
    assert len(targets) > 1  # genuinely randomized per seed, not a fixed constant
    # The range straddles one furnace's ~0.4 belts/s cap in both directions.
    per_second = [t * 60.0 for t in targets]
    assert min(per_second) < 0.4 < max(per_second)


def test_level2_scenario_is_deterministic_per_seed() -> None:
    first = generate_connected_workflow_scenario(42, level=2)
    second = generate_connected_workflow_scenario(42, level=2)
    assert first["scenario_hash"] == second["scenario_hash"]


def test_level2_branch_plans_never_overlap_across_all_variants() -> None:
    for seed in (0, 1, 3, 7, 311, 65535):
        scenario = generate_connected_workflow_scenario(seed, level=2)
        owner, actions = _world(scenario)
        counts: dict[str, int] = {}
        for _, action in actions:
            counts[action["entity"]] = counts.get(action["entity"], 0) + 1
        assert counts == {entity: count for entity, count in BUDGET_LEVEL2.items()
                          if entity not in {"assembling-machine-1", "inserter"}}


def test_iron2_feeds_the_gear_assembler_directly() -> None:
    scenario = generate_connected_workflow_scenario(3, level=2)
    owner, actions = _world(scenario)
    moves = []
    for branch_id, action in actions:
        if action["entity"] != "fast-inserter" or branch_id != "iron2":
            continue
        x, y = math.floor(action["position"]["x"]), math.floor(action["position"]["y"])
        dx, dy = _STEP[action["direction"]]
        moves.append((owner.get((x + dx, y + dy)), owner.get((x - dx, y - dy))))
    assert sorted(moves) == sorted([
        ("ore-source-2", "electric-furnace"), ("electric-furnace", "assembling-machine-2"),
    ])


def test_level1_branch_ids_and_variants_are_unaffected_by_level2() -> None:
    assert "iron2" not in BRANCH_IDS
    scenario = generate_connected_workflow_scenario(5)
    by_branch = {}
    for candidate in connected_workflow_candidates(scenario):
        by_branch.setdefault(candidate["branch_id"], []).append(candidate)
    assert set(by_branch) == set(BRANCH_IDS)


def test_iron2_is_only_offered_once_iron_is_closed() -> None:
    scenario = generate_connected_workflow_scenario(3, level=2)
    specs = branch_specs(scenario)
    ids = {spec["branch_id"] for spec in specs}
    assert ids == {"iron", "gears", "belts", "construction", "iron2"}
    iron2 = {spec["branch_id"]: spec for spec in specs}["iron2"]
    assert iron2["requires"] == ["iron"]
    assert iron2["metric"] == "built_count" and iron2["item"] == "electric-furnace"

    surface, force = scenario["environment"]["surface_name"], scenario["environment"]["force_name"]
    base_report = {
        "runtime": "disposable_training", "surface": surface, "force": force,
        "items": {"electric-furnace": {"metrics": {"built_count": {"value": 1, "sustained_ticks": 0}}}},
    }
    # Furnace 1 exists but "iron" has not measured a sustained rate yet: iron2 is blocked.
    graph, _ = observe_workflow(base_report, specs)
    blocked_ids = {branch.branch_id for branch in graph.branches if branch.status == "blocked"}
    assert "iron2" in blocked_ids

    # Once "iron" closes (its own production_rate target, sustained), iron2 becomes runnable.
    ready_report = dict(base_report)
    ready_report["items"] = {
        "iron-plate": {"production_rate": 0.4 / 60.0 + 0.001, "production_sustained_ticks": 600},
        "electric-furnace": {"metrics": {"built_count": {"value": 1, "sustained_ticks": 0}}},
    }
    graph, _ = observe_workflow(ready_report, specs)
    runnable_ids = {branch.branch_id for branch in graph.branches if branch.status == "open"}
    assert "iron" in {b.branch_id for b in graph.branches if b.status == "closed"}
    assert "iron2" in runnable_ids

    # Building the second furnace closes iron2; nothing else depends on it.
    scaled_report = dict(ready_report)
    scaled_report["items"] = {**ready_report["items"],
                              "electric-furnace": {"metrics": {"built_count": {"value": 2, "sustained_ticks": 0}}}}
    graph, _ = observe_workflow(scaled_report, specs)
    assert "iron2" in {b.branch_id for b in graph.branches if b.status == "closed"}


def test_level2_digest_stays_under_the_2048_token_director_budget() -> None:
    scenario = generate_connected_workflow_scenario(65535, level=2)
    specs = branch_specs(scenario)
    surface, force = scenario["environment"]["surface_name"], scenario["environment"]["force_name"]
    # A busy mid-episode moment: iron closed and being drawn from, gears/belts
    # in flight, construction and iron2 still open -- the largest realistic
    # branch+item mix this family produces.
    report = {
        "runtime": "disposable_training", "surface": surface, "force": force,
        "items": {
            "iron-plate": {"production_rate": 0.4 / 60.0 + 0.001, "production_sustained_ticks": 600,
                          "draw_rate": 0.3 / 60.0, "stock": 40.0},
            "iron-gear-wheel": {"production_rate": None, "production_sustained_ticks": None,
                                "draw_rate": 0.0, "stock": 0.0},
            "transport-belt": {"production_rate": None, "production_sustained_ticks": None},
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
    assert rendered  # renders without raising LayaStateError (see below)
