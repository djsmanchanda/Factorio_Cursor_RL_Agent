# Path: tests/test_connected_workflow.py
# Purpose: Protect the connected-workflow layout, budget, and branch dependencies verified live on 2.1.20.

from __future__ import annotations

import math

from training.candidates.connected_workflow import BRANCH_IDS, branch_specs, connected_workflow_candidates
from training.scenarios.connected_workflow import BUDGET, generate_connected_workflow_scenario

_SIZE = {"electric-furnace": 3, "assembling-machine-2": 3}
_STEP = {"east": (1, 0), "west": (-1, 0), "north": (0, -1), "south": (0, 1)}


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
    for candidate in connected_workflow_candidates(scenario):
        for action in candidate["plan"]["phases"][0]["actions"]:
            actions.append((candidate["branch_id"], action))
            for tile in _tiles(action["entity"], action["position"]["x"], action["position"]["y"]):
                assert tile not in owner, f"{action} overlaps {owner[tile]}"
                owner[tile] = action["entity"]
    return owner, actions


def test_branch_plans_never_overlap_and_fit_the_budget_exactly() -> None:
    for seed in (0, 7, 311, 65535):
        scenario = generate_connected_workflow_scenario(seed)
        _, actions = _world(scenario)
        counts: dict[str, int] = {}
        for _, action in actions:
            counts[action["entity"]] = counts.get(action["entity"], 0) + 1
        # Every branch owns distinct entities, so no plan relies on idempotent re-placement.
        assert counts == BUDGET


def test_inserters_move_items_along_the_intended_chain() -> None:
    scenario = generate_connected_workflow_scenario(3)
    owner, actions = _world(scenario)
    moves = []
    for _, action in actions:
        if action["entity"] != "fast-inserter":
            continue
        x, y = math.floor(action["position"]["x"]), math.floor(action["position"]["y"])
        dx, dy = _STEP[action["direction"]]  # Inserters pick up on the side they face.
        moves.append((owner.get((x + dx, y + dy)), owner.get((x - dx, y - dy))))
    assert sorted(moves, key=str) == sorted([
        ("ore-source", "electric-furnace"), ("electric-furnace", "iron-chest"),
        ("iron-chest", "assembling-machine-2"), ("assembling-machine-2", "iron-chest"),
        ("iron-chest", "assembling-machine-2"), ("iron-chest", "assembling-machine-2"),
        ("assembling-machine-2", "belt-sink"),
    ], key=str)


def test_branch_specs_gate_production_but_not_construction() -> None:
    specs = {spec["branch_id"]: spec for spec in branch_specs(generate_connected_workflow_scenario(1))}
    assert tuple(specs) == BRANCH_IDS
    assert specs["gears"]["requires"] == ["iron"] and specs["belts"]["requires"] == ["gears"]
    assert "requires" not in specs["construction"] and specs["construction"]["metric"] == "built_count"
