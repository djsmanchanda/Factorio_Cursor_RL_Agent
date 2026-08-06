# Path: tests/test_sandbox_contracts.py
# Purpose: Verify behavioral sandbox runtime, topology, authorization, composition, and schemas.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planners.infrastructure import (
    POWER_SOURCE_ENTITY,
    roboport_positions,
    validate_power_connectivity,
    validate_roboport_network,
)


def _actions(plan: dict) -> list:
    return [action for phase in plan["phases"] for action in phase["actions"]]



def test_tick_wait_detects_progress_and_stagnation() -> None:
    from tools.electronics_execution import ElectronicsExecutionError, wait_for_game_ticks

    class TickBridge:
        def __init__(self, ticks: list[int]):
            self.ticks = iter(ticks)

        def command(self, _query: str) -> str:
            return str(next(self.ticks))

    wait_for_game_ticks(
        TickBridge([100, 101, 103]), 3, poll_seconds=0, timeout_seconds=1,
        max_stagnant_polls=2,
    )
    with pytest.raises(ElectronicsExecutionError, match="stalled"):
        wait_for_game_ticks(
            TickBridge([100, 100, 100]), 3, poll_seconds=0, timeout_seconds=1,
            max_stagnant_polls=2,
        )


def test_existing_topology_refuses_by_default_and_reset_is_explicit(monkeypatch) -> None:
    import tools.electronics_execution as execution

    monkeypatch.setattr(execution, "load_json", lambda value: value)

    class TopologyBridge:
        def __init__(self):
            self.reconciles = []
            self.inspections = [
                {"planner_factory_entities": 5, "player_factory_entities": 0},
                {"planner_factory_entities": 0, "player_factory_entities": 0},
            ]

        def inspect_sandbox_topology(self):
            return self.inspections.pop(0)

        def reconcile_sandbox_topology(self, mode: str, *, confirm: bool):
            self.reconciles.append((mode, confirm))
            return {"ok": True}

    refused = TopologyBridge()
    with pytest.raises(RuntimeError, match="incompatible factory topology"):
        execution.prepare_existing_topology(refused, "refuse")
    assert refused.reconciles == []

    reset = TopologyBridge()
    execution.prepare_existing_topology(reset, "reset")
    assert reset.reconciles == [("reset", True)]


def test_reconcile_refuses_a_still_split_topology(monkeypatch) -> None:
    import tools.electronics_execution as execution

    monkeypatch.setattr(execution, "load_json", lambda value: value)

    class SplitBridge:
        reports = iter([
            {"planner_factory_entities": 1, "player_factory_entities": 4},
            {"power_sources": 2, "electric_networks": 2, "logistic_networks": 1},
        ])

        def inspect_sandbox_topology(self):
            return next(self.reports)

        def reconcile_sandbox_topology(self, mode: str, *, confirm: bool):
            assert (mode, confirm) == ("reconcile", True)
            return {"ok": True}

    with pytest.raises(RuntimeError, match="exact canonical"):
        execution.prepare_existing_topology(SplitBridge(), "reconcile")


def _single_action_plan(action_type: str) -> dict:
    action = {
        "action_type": action_type,
        "position": {"x": 0, "y": 0},
    }
    if action_type == "place_tile_ghost":
        action["tile"] = "landfill"
    else:
        action["entity"] = "pipe"
    return {"phases": [{"name": "test", "actions": [action]}]}


def test_layout_authorization_derives_only_required_mutation_contracts() -> None:
    from planners.sandbox_infrastructure import build_layout_authorization

    ghost = build_layout_authorization([_single_action_plan("place_ghost")])
    entity = build_layout_authorization([_single_action_plan("place_entity")])
    tile = build_layout_authorization([_single_action_plan("place_tile_ghost")])
    mixed = build_layout_authorization([
        _single_action_plan("place_entity"), _single_action_plan("remove_entity")
    ])

    assert ghost["approved_actions"] == ["project_more_ghosts"]
    assert entity["approved_actions"] == ["place_core_infrastructure"]
    assert tile["approved_actions"] == ["project_more_ghosts"]
    assert set(mixed["approved_actions"]) == {"place_core_infrastructure", "remove_entities"}
    assert "remove_entities" not in ghost["approved_actions"]
    assert "remove_entities" not in entity["approved_actions"]



def test_sequential_compositions_share_one_canonical_connected_backbone() -> None:
    from planners.local_layout_planner import LocalLayoutPlanner
    from planners.sandbox_infrastructure import (
        CANONICAL_POWER_SOURCE,
        CANONICAL_ROBOPORT_HUB,
        compose_managed_sandbox,
    )

    planner = LocalLayoutPlanner()
    left = compose_managed_sandbox(
        [("left", planner.generate_line_layout("iron-gear-wheel", 2, 0, 40))],
        [(10, -3)],
        {},
    )
    right = compose_managed_sandbox(
        [("right", planner.generate_line_layout("copper-cable", 2, 80, 40))],
        [(90, -3)],
        {},
    )

    unique_power = {}
    unique_robots = {}
    for composition in (left, right):
        infrastructure = dict(composition["infrastructure"])
        assert roboport_positions(infrastructure["unified_roboports"])[0] == CANONICAL_ROBOPORT_HUB
        for action in _actions(infrastructure["unified_power"]):
            key = (action["entity"], action["position"]["x"], action["position"]["y"])
            unique_power[key] = action
        for action in _actions(infrastructure["unified_roboports"]):
            key = (action["entity"], action["position"]["x"], action["position"]["y"])
            unique_robots[key] = action

    combined_power = {"phases": [{"name": "union", "actions": list(unique_power.values())}]}
    combined_robots = {"phases": [{"name": "union", "actions": list(unique_robots.values())}]}
    sources = [key for key in unique_power if key[0] == POWER_SOURCE_ENTITY]
    assert sources == [(POWER_SOURCE_ENTITY, *CANONICAL_POWER_SOURCE)]
    validate_power_connectivity(combined_power)
    validate_roboport_network(combined_robots, [
        {"name": "canonical", "position": CANONICAL_ROBOPORT_HUB},
        {"name": "left", "position": (10, -3)},
        {"name": "right", "position": (90, -3)},
    ])


def test_topology_compatibility_requires_emitted_canonical_keys_on_repeat_invocation() -> None:
    from planners.sandbox_infrastructure import (
        require_compatible_topology,
        topology_is_compatible,
    )


    assert topology_is_compatible({"planner_factory_entities": 0, "player_factory_entities": 0})
    emitted = {
        "planner_factory_entities": 5,
        "player_factory_entities": 0,
        "unified": True,
        "canonical_power_source": True,
        "canonical_roboport_hub": True,
    }

    class RepeatBridge:
        def __init__(self):
            self.calls = 0

        def inspect_sandbox_topology(self):
            self.calls += 1
            return dict(emitted)

    bridge = RepeatBridge()
    assert require_compatible_topology(bridge) == emitted
    assert require_compatible_topology(bridge) == emitted
    assert bridge.calls == 2

    incompatible = {**emitted, "canonical_power_source": False}
    assert not topology_is_compatible(incompatible)



def test_underground_belt_type_is_schema_validated() -> None:
    import json

    from jsonschema import Draft7Validator

    schema = json.loads(
        (REPO_ROOT / "schemas" / "build_plan.schema.json").read_text(encoding="utf-8")
    )
    action = {
        "action_type": "place_ghost",
        "entity": "express-underground-belt",
        "position": {"x": 1.5, "y": 2.5},
        "direction": "east",
        "underground_type": "input",
    }
    plan = {"phases": [{"name": "route", "actions": [action]}]}
    validator = Draft7Validator(schema)
    assert not list(validator.iter_errors(plan))

    del action["underground_type"]
    assert list(validator.iter_errors(plan))
    action["underground_type"] = "sideways"
    assert list(validator.iter_errors(plan))
