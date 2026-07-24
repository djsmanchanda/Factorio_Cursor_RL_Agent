# Path: tests/test_autonomy.py
# Purpose: Verify deterministic recipe compilation, immutable phases, restart state, and safe live contracts.

from __future__ import annotations
import json
from pathlib import Path
import pytest

from core.autonomy_program import compile_autonomy_program
from core.autonomy_state import initial_state, reconcile, transition
from core.recipe_dag import compile_recipe_dag

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "tests/fixtures/recipe_catalog.json").read_text())
WORLD = json.loads((ROOT / "tests/fixtures/electronics_world_spec.json").read_text())

def goal(target="processing-unit", rate=0.075):
    return {"version": "1.0.0", "target": target, "delta": {"rate": rate, "unit": "per_second"}}

def test_processing_unit_exact_raw_rates_and_topology():
    graph = compile_recipe_dag(goal(), CATALOG)
    assert graph["topological_order"][-1] == "processing-unit"
    assert graph["raw_leaves_per_second"] == pytest.approx({"coal": 0.15, "copper-ore": 3.0, "crude-oil": 7.916666667, "iron-ore": 1.8075, "water": 1.3125})
    assert next(n for n in graph["nodes"] if n["recipe"] == "processing-unit")["provisioned_craft_seconds"] == pytest.approx(0.9375)

def test_advanced_circuit_exact_raw_rates():
    graph = compile_recipe_dag(goal("advanced-circuit", 0.125), CATALOG)
    assert graph["raw_leaves_per_second"] == pytest.approx({"coal": 0.125, "copper-ore": 0.625, "crude-oil": 5.555555556, "iron-ore": 0.25})

def test_missing_and_ambiguous_recipes_fail_closed():
    with pytest.raises(ValueError, match="Missing enabled"):
        compile_recipe_dag(goal("unknown-widget", 1), CATALOG)
    ambiguous = json.loads(json.dumps(CATALOG))
    duplicate = dict(ambiguous["recipes"][0]); duplicate["name"] = "advanced-circuit-alt"
    ambiguous["recipes"].append(duplicate)
    with pytest.raises(ValueError, match="Ambiguous"):
        compile_recipe_dag(goal("advanced-circuit", 1), ambiguous)

def test_program_determinism_and_concrete_gate():
    first = compile_autonomy_program(goal(), CATALOG, world_spec=WORLD)
    second = compile_autonomy_program(goal(), CATALOG, world_spec=WORLD)
    assert first == second and first["executable"]
    assert [p["kind"] for p in first["phases"]] == ["survey", "allocate", "plan", "authorize", "build", "verify"]
    assert len({p["phase_hash"] for p in first["phases"]}) == 6
    symbolic = compile_autonomy_program(goal("copper-cable", 2), CATALOG)
    assert not symbolic["executable"]
    assert symbolic["phases"][2]["requirements"] == ["NamedPlanner"]

def test_restart_state_transitions_and_reconcile():
    program = compile_autonomy_program(goal(), CATALOG, world_spec=WORLD)
    state = initial_state(program, 10)
    first = program["phases"][0]["id"]
    state = transition(state, first, "running", tick=11)
    assert state["phases"][0]["attempts"] == 1
    resumed = reconcile(program, state, set(), snapshot_tick=20)
    assert resumed["phases"][0]["status"] == "pending"
    resumed = transition(resumed, first, "running", tick=21)
    resumed = transition(resumed, first, "verified", tick=22)
    assert resumed["last_verified_tick"] == 22
    with pytest.raises(ValueError, match="Earlier phases"):
        transition(resumed, program["phases"][2]["id"], "running", tick=23)

def test_lua_catalog_and_no_ore_seeding_contract():
    lua = (ROOT / "factorio_mod/recipe_catalog.lua").read_text()
    daemon = (ROOT / "orchestrator/expansion_daemon.py").read_text()
    assert "table.sort(names)" in lua and "probabilistic product" in lua
    assert "command.parameter" in lua and "game.forces[name]" in lua
    assert "force does not exist" in lua
    assert "ore_patches=" not in daemon
    assert "CityPlanner rail handoff" in daemon


def test_cycle_coproduct_rate_and_recipe_contract_fail_closed():
    cycle = {"version": "1.0.0", "force": "planner", "tick": 0, "raw_resources": [], "recipes": [
        {"name": "make-a", "enabled": True, "category": "crafting", "energy_ticks": 60, "ingredients": [{"name": "b", "type": "item", "amount": 1}], "products": [{"name": "a", "type": "item", "amount": 1}], "supported": True},
        {"name": "make-b", "enabled": True, "category": "crafting", "energy_ticks": 60, "ingredients": [{"name": "a", "type": "item", "amount": 1}], "products": [{"name": "b", "type": "item", "amount": 1}], "supported": True},
    ]}
    with pytest.raises(ValueError, match="cycle"):
        compile_recipe_dag(goal("a", 1), cycle)
    assert not compile_autonomy_program(goal("processing-unit", 0.101), CATALOG, world_spec=WORLD)["executable"]
    changed = json.loads(json.dumps(CATALOG))
    next(recipe for recipe in changed["recipes"] if recipe["name"] == "processing-unit")["energy_ticks"] = 601
    assert not compile_autonomy_program(goal(), changed, world_spec=WORLD)["executable"]

def test_reconcile_requires_exact_phase_identity_and_verified_prefix():
    program = compile_autonomy_program(goal(), CATALOG, world_spec=WORLD)
    state = initial_state(program, 0)
    with pytest.raises(ValueError, match="prefix"):
        reconcile(program, state, {program["phases"][1]["id"]}, snapshot_tick=1)
    state["phases"][0]["phase_hash"] = "0" * 64
    with pytest.raises(ValueError, match="identities"):
        reconcile(program, state, set(), snapshot_tick=1)

def test_daemon_filters_unsupported_actions_before_policy_selection():
    from orchestrator.expansion_daemon import _is_executable_action
    chain = {"lines": [{"name": "mine", "mining_feed": True}, {"name": "line"}, {"name": "science", "consumers": ["lab_row"]}]}
    assert not _is_executable_action({"action": "open_new_mine", "target_line": "mine"}, chain)
    assert not _is_executable_action({"action": "upgrade_belt_tier", "target_line": "line"}, chain)
    assert _is_executable_action({"action": "extend_line_x", "target_line": "line"}, chain)
    assert _is_executable_action({"action": "add_collectors", "target_line": "science"}, chain)
    cli = (ROOT / "tools/compile_autonomy_goal.py").read_text()
    assert "compiled_from_snapshot_tick" not in cli


def test_worldspec_provenance_and_upgrade_idempotency_are_fail_closed():
    with pytest.raises(ValueError, match="block_bounds"):
        compile_autonomy_program(goal(), CATALOG, world_spec=WORLD, snapshot={"surface": "planner-sandbox", "tick": 0})
    lua = (ROOT / "factorio_mod/upgrades.lua").read_text()
    assert lua.index("local upgraded") < lua.index("local target")


def test_declared_raw_resource_wins_over_enabled_recipe_producer():
    catalog = json.loads(json.dumps(CATALOG))
    catalog["recipes"].append({
        "name": "empty-water-barrel", "enabled": True, "category": "crafting-with-fluid",
        "energy_ticks": 12,
        "ingredients": [{"name": "water-barrel", "type": "item", "amount": 1}],
        "products": [{"name": "water", "type": "fluid", "amount": 50}],
        "supported": True,
    })
    graph = compile_recipe_dag(goal("sulfur", 2), catalog)
    assert "empty-water-barrel" not in graph["topological_order"]
    assert graph["raw_leaves_per_second"]["water"] == pytest.approx(30)