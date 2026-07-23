# Path: tests/test_sandbox_contracts.py
# Purpose: Verify managed sandbox runtime, topology, authorization, and Lua contracts.

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


def _lua_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "factorio_mod").glob("*.lua"))
    )

def _verification_payload() -> dict:
    machines = []
    fluids = {
        "basic-oil-processing": ["crude-oil"],
        "sulfur": ["petroleum-gas", "water"],
        "plastic-bar": ["petroleum-gas"],
        "sulfuric-acid": ["water"],
    }
    for recipe, count in {
        "basic-oil-processing": 2,
        "sulfur": 2,
        "plastic-bar": 2,
        "sulfuric-acid": 2,
    }.items():
        machines.extend({"recipe": recipe, "status": "working", "fluids": fluids[recipe]}
                        for _ in range(count))
    return {
        "machines": machines,
        "power_sources": 1,
        "electric_networks": 1,
        "logistic_networks": 1,
    }


@pytest.mark.skip(
    reason=(
        "verify_processing_snapshot parsed a legacy scripted-source RCON/Lua "
        "snapshot (flat machines list with recipe/status/fluids, plus "
        "power_sources/electric_networks/logistic_networks counters) that "
        "PROCESSING_VERIFICATION_QUERY used to embed literally in "
        "tools/build_processing_units.py. Both the query string and the "
        "parser were removed with the M6 refactor and have no drop-in "
        "replacement: tools.electronics_execution.validate_live_report "
        "validates a structurally different, mod-native live-execution "
        "report (electric_samples/fluid_machines/ghosts/logistic_roboports) "
        "against schemas/live_execution_report.schema.json, which does not "
        "exist in this checkout -- that gap is tracked separately as the M6 "
        "execution gap and is out of this test file's scope to fix."
    )
)
def test_processing_live_report_parser_asserts_counts_fluids_status_and_topology() -> None:
    import json

    from tools.build_processing_units import verify_processing_snapshot

    payload = _verification_payload()
    assert verify_processing_snapshot(json.dumps(payload))["power_sources"] == 1

    payload["machines"][0]["status"] = "no_power"
    with pytest.raises(ValueError, match="invalid live status"):
        verify_processing_snapshot(json.dumps(payload))
    payload = _verification_payload()
    payload["machines"][2]["fluids"] = ["water"]
    with pytest.raises(ValueError, match="missing live input fluids"):
        verify_processing_snapshot(json.dumps(payload))
    payload = _verification_payload()
    payload["logistic_networks"] = 2
    with pytest.raises(ValueError, match="Expected one logistic_networks"):
        verify_processing_snapshot(json.dumps(payload))


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
    return {"phases": [{"name": "test", "actions": [{
        "action_type": action_type,
        "entity": "pipe",
        "position": {"x": 0, "y": 0},
    }]}]}


def test_layout_authorization_derives_only_required_mutation_contracts() -> None:
    from planners.sandbox_infrastructure import build_layout_authorization

    ghost = build_layout_authorization([_single_action_plan("place_ghost")])
    entity = build_layout_authorization([_single_action_plan("place_entity")])
    mixed = build_layout_authorization([
        _single_action_plan("place_entity"), _single_action_plan("remove_entity")
    ])

    assert ghost["approved_actions"] == ["project_more_ghosts"]
    assert entity["approved_actions"] == ["place_core_infrastructure"]
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

    topology_lua = (
        REPO_ROOT / "factorio_mod" / "sandbox_topology.lua"
    ).read_text(encoding="utf-8")
    assert "canonical_power_source = canonical_power_source" in topology_lua
    assert "canonical_roboport_hub = canonical_roboport_hub" in topology_lua
    assert "canonical_power_source = false" in topology_lua
    assert "canonical_roboport_hub = false" in topology_lua

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



def test_lua_modules_are_bounded_headered_and_register_each_command_once() -> None:
    import re

    mod_root = REPO_ROOT / "factorio_mod"
    lua_files = sorted(mod_root.glob("*.lua"))
    for path in lua_files:
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[0] == f"-- Path: factorio_mod/{path.name}"
        assert lines[1].startswith("-- Purpose: ")
        assert len(lines) <= 500, f"{path.name} has {len(lines)} lines"

    control = (mod_root / "control.lua").read_text(encoding="utf-8")
    modules = {
        "sandbox_shared", "world_generation", "snapshot", "recipe_catalog", "ghost_plans", "construction", "upgrades",
        "deconstruction", "sandbox_topology", "scaffolding", "layout_executor", "research",
    }
    for module in modules:
        assert f'require("{module}")' in control

    expected_commands = {
        "snapshot", "export_recipe_catalog", "apply_ghost_plan", "export_ghost_observation", "execute_ghost_plan",
        "execute_construction", "execute_upgrade_plan", "execute_deconstruction_plan",
        "inspect_sandbox_topology", "reconcile_sandbox_topology",
        "ensure_sandbox_scaffolding", "seed_ore_patches", "build_layout_plan", "set_research", "research_status",
            "create_planner_world",
    }
    registered = re.findall(r'commands\.add_command\("([^"]+)"', _lua_source())
    assert set(registered) == expected_commands
    assert len(registered) == len(expected_commands)

def test_lua_topology_and_infinity_idempotency_contracts_are_exact() -> None:
    lua = _lua_source()
    assert "roboport.logistic_network.network_id" in lua

    # The old contract checked that tools/build_processing_units.py embedded a
    # literal legacy RCON/Lua verification string ("r.logistic_network.network_id")
    # inline. That scripted-verification path is gone on purpose: the CLI is now
    # a thin wrapper with no stage geometry or inline Lua of its own, and live
    # report validation is owned exclusively by tools/electronics_execution.py.
    cli_source = (REPO_ROOT / "tools" / "build_processing_units.py").read_text(encoding="utf-8")
    assert "r.logistic_network.network_id" not in cli_source
    for hardcoded_marker in ("STAGES = [", "LINKS = [", "PROCESSING_VERIFICATION_QUERY"):
        assert hardcoded_marker not in cli_source, (
            f"build_processing_units.py should not own {hardcoded_marker!r} directly"
        )

    execution_source = (
        REPO_ROOT / "tools" / "electronics_execution.py"
    ).read_text(encoding="utf-8")
    assert "def validate_live_report" in execution_source
    assert "def prepare_existing_topology" in execution_source

    for marker in (
        'filter.mode ~= "at-least"',
        "tonumber(action.fill_percentage) or 1.0",
        'filter.mode ~= "exactly"',
        "tonumber(filter.count) ~= 1000",
        "entity.remove_unfiltered_items ~= true",
    ):
        assert marker in lua

def test_legacy_scaffolding_requires_explicit_opt_in() -> None:
    control = _lua_source()
    assert "payload.legacy_infrastructure ~= true" in control
    assert "Scaffolding mode must be explicit" in control


def test_underground_belt_type_is_schema_validated_and_exactly_idempotent_in_lua() -> None:
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

    lua = (REPO_ROOT / "factorio_mod" / "layout_executor.lua").read_text(encoding="utf-8")
    assert "entity.belt_to_ground_type" in lua
    assert '"underground_type_mismatch:expected="' in lua
    assert lua.count("type = action.underground_type") == 2
    assert "configuration_error(existing, action, direction)" in lua