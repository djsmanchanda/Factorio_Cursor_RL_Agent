# Path: tests/test_infrastructure.py
# Purpose: Deterministic tests for unified power, roboports, scaffolding, and placement reporting.

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planners.electronics_block import _block_anchors, build_electronics_block
from planners.electronics_world import load_electronics_world_spec
from planners.infrastructure import (
    POWER_SOURCE_ENTITY,
    plan_power_network,
    plan_roboport_network,
    roboport_positions,
    validate_power_connectivity,
    validate_roboport_network,
)
from planners.infrastructure_geometry import choose_clear_l_route
from planners.plan_validation import validate_no_collisions
from planners.sandbox_infrastructure import CANONICAL_POWER_SOURCE, CANONICAL_ROBOPORT_HUB
from tools.electronics_execution import scaffolding_with_materials

# NOTE ON THE MOVED SYMBOLS THIS TEST USED TO IMPORT FROM tools.build_processing_units:
#   POWER_SOURCE                -> planners.sandbox_infrastructure.CANONICAL_POWER_SOURCE
#   ROBOPORT_SITES              -> no longer a standalone constant; the real production
#                                   anchors now live in planners.electronics_block._block_anchors
#   build_infrastructure_plans  -> planners.electronics_block.build_electronics_block(...)["infrastructure"]
#   build_plans/build_link_plans-> build_electronics_block(...)["plans"]
#   validate_processing_bundle  -> planners.plan_validation.validate_no_collisions(infrastructure + plans)
#   managed_scaffolding_payload -> tools.electronics_execution.scaffolding_with_materials(scaffolding, materials)
# tools/build_processing_units.py is now a thin CLI over build_electronics_block; it does not
# re-export any of the planning helpers any more.

_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "electronics_world_spec.json"


def _actions(plan: dict) -> list:
    return [action for phase in plan["phases"] for action in phase["actions"]]


def _lua_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "factorio_mod").glob("*.lua"))
    )


def _processing_bundle() -> dict:
    world = load_electronics_world_spec(_FIXTURE)
    return build_electronics_block(include_processing=True, world=world)


def _robot_sites_for(include_processing: bool) -> list[dict]:
    """Rebuild the real site list compose_managed_sandbox validates the network
    against, from the same anchors build_electronics_block feeds it, so the
    coverage check below exercises actual production geometry rather than a
    trivial self-covering proxy."""
    sites = [
        {
            "name": anchor["name"],
            "position": (anchor["x"], anchor["y"]),
            **({"extent": anchor["extent"]} if anchor.get("extent") else {}),
        }
        for anchor in _block_anchors(include_processing)
    ]
    if not any(site["position"] == CANONICAL_ROBOPORT_HUB for site in sites):
        sites.insert(0, {"name": "canonical_hub", "position": CANONICAL_ROBOPORT_HUB})
    return sites


def test_power_plan_has_one_source_and_every_pole_reaches_it() -> None:
    bundle = _processing_bundle()
    power = dict(bundle["infrastructure"])["unified_power"]

    sources = [action for action in _actions(power) if action["entity"] == POWER_SOURCE_ENTITY]
    assert [action["position"] for action in sources] == [
        {"x": round(CANONICAL_POWER_SOURCE[0]), "y": round(CANONICAL_POWER_SOURCE[1])}
    ]
    validate_power_connectivity(power)


def test_power_spine_uses_an_mst_instead_of_hub_spokes() -> None:
    plan = plan_power_network(
        [
            {"name": "north", "substation": (5, 80), "pole_anchor": (0, 80)},
            {"name": "north_east", "substation": (25, 100), "pole_anchor": (20, 100)},
        ],
        source=(0, 0),
    )
    spine = {
        (action["position"]["x"], action["position"]["y"])
        for action in _actions(plan)
        if action["entity"] in {"substation", "big-electric-pole"}
    }

    assert (20, 80) in spine
    assert (20, 0) not in spine


def test_power_spine_keeps_120_tile_legs_on_substations_by_default() -> None:
    plan = plan_power_network(
        [{"name": "near", "substation": (125, 0), "pole_anchor": (120, 0)}],
        source=(0, 0),
    )

    assert not any(action["entity"] == "big-electric-pole" for action in _actions(plan))
def test_power_route_uses_shortest_clear_detour_when_direct_elbows_are_blocked() -> None:
    blocked = {(x, y) for x in (6, 7) for y in (-99, -98, -96, -95)}

    route = choose_clear_l_route((-9, -98), (28, -95), 16, 2, blocked)

    assert route == [(-9, -98), (-1, -98), (-1, -95), (28, -95)]

def test_power_validator_rejects_a_disconnected_site() -> None:
    plan = plan_power_network(
        [{"name": "site", "substation": (20, 0), "pole_anchor": (15, 0)}],
        source=(0, 0),
    )
    broken = copy.deepcopy(plan)
    substation = next(
        a for a in _actions(broken)
        if a["entity"] == "substation" and (a["position"]["x"], a["position"]["y"]) == (20, 0)
    )
    substation["position"] = {"x": 200, "y": 200}

    with pytest.raises(ValueError, match="unreachable from the power source"):
        validate_power_connectivity(broken)


def test_robot_plan_is_one_connected_canonical_network_covering_every_stage() -> None:
    bundle = _processing_bundle()
    robots = dict(bundle["infrastructure"])["unified_roboports"]

    positions = roboport_positions(robots)
    assert positions[0] == CANONICAL_ROBOPORT_HUB
    validate_roboport_network(robots, _robot_sites_for(True))


def test_roboport_validator_rejects_a_split_network() -> None:
    sites = [{"name": "a", "position": (0, 0)}, {"name": "b", "position": (40, 0)}]
    plan = plan_roboport_network(sites)
    broken = copy.deepcopy(plan)
    _actions(broken)[-1]["position"] = {"x": 100, "y": 0}

    with pytest.raises(ValueError, match="separate logistic networks"):
        validate_roboport_network(broken, sites)


def test_composed_processing_bundle_has_one_source_no_local_power_and_no_collisions() -> None:
    bundle = _processing_bundle()
    infrastructure, plans = bundle["infrastructure"], bundle["plans"]
    validate_no_collisions(infrastructure + plans)

    infrastructure_actions = _actions(dict(infrastructure)["unified_power"])
    production_actions = [action for _, plan in plans for action in _actions(plan)]
    assert sum(a["entity"] == POWER_SOURCE_ENTITY for a in infrastructure_actions) == 1
    assert all(a["entity"] not in {POWER_SOURCE_ENTITY, "substation"} for a in production_actions)


def test_composed_bundle_validator_detects_cross_plan_collision() -> None:
    bundle = _processing_bundle()
    infrastructure = bundle["infrastructure"]
    plans = copy.deepcopy(bundle["plans"])
    collision_position = roboport_positions(dict(infrastructure)["unified_roboports"])[0]
    plans[0][1]["phases"][0]["actions"].append({
        "action_type": "place_entity",
        "entity": "pipe",
        "position": {"x": collision_position[0], "y": collision_position[1]},
    })

    with pytest.raises(ValueError, match="Plan collision"):
        validate_no_collisions(infrastructure + plans)


def test_managed_scaffolding_targets_every_planned_roboport_without_infrastructure() -> None:
    bundle = _processing_bundle()
    robots = dict(bundle["infrastructure"])["unified_roboports"]
    payload = scaffolding_with_materials(bundle["scaffolding"], {"pipe": 12})

    assert payload["managed_infrastructure"] is True
    positions = [(anchor["x"], anchor["y"]) for anchor in payload["anchors"]]
    assert positions == roboport_positions(robots)
    assert positions[0] == CANONICAL_ROBOPORT_HUB
    assert payload["anchors"][0]["materials"] == {"pipe": 12}
    assert payload["anchors"][0]["provider_position"] == {"x": -124.0, "y": -133.0}
    assert payload["bots_per_roboport"] == 50
    assert not any(
        key in anchor
        for anchor in payload["anchors"]
        for key in ("electric-energy-interface", "substation", "roboport")
    )


def test_lua_contract_uses_planner_force_exact_idempotency_and_honest_counters() -> None:
    control = _lua_source()

    assert 'game.forces["player"]' not in control
    # The helper gained an OPTIONAL force_name so the real-base builder can
    # target an existing force (e.g. "player"); the sandbox contract this test
    # guards is that calling it with NO argument still resolves to the isolated
    # planner force, creating it when absent.
    assert "local function get_or_create_planner_force(force_name)" in control
    assert "local force = game.forces.planner" in control
    assert 'force = game.create_force("planner")' in control
    assert "local function find_exact_entity(surface, force, name, position)" in control
    assert "force = force" in control
    assert "Managed infrastructure requires planner roboport at exact anchor" in control
    assert 'mode = managed and "managed_infrastructure" or "legacy"' in control
    for field in (
        "attempted_placements",
        "succeeded_placements",
        "already_present_placements",
        "failed_placements",
        "placement_failures",
    ):
        assert field in control
    assert 'reason = "create_entity_returned_nil"' not in control
    assert 'record_failure(phase, action, "create_entity_returned_nil")' in control


def test_infrastructure_python_modules_respect_file_size_limit() -> None:
    for relative in ("planners/infrastructure.py", "planners/infrastructure_geometry.py"):
        assert len((REPO_ROOT / relative).read_text(encoding="utf-8").splitlines()) <= 500

def test_snapshot_contract_includes_planner_and_neutral_resources_only() -> None:
    control = _lua_source()
    start = control.index("local function build_snapshot")
    end = control.index("local function write_snapshot", start)
    snapshot = control[start:end]

    assert "surface.find_entities_filtered({ force = force })" in snapshot
    assert 'surface.find_entities_filtered({ type = "resource", force = "neutral" })' in snapshot
    assert "game.forces.player" not in snapshot
    assert 'entity.type ~= "character"' in snapshot


def test_topology_contract_is_read_only_until_explicit_confirmed_command() -> None:
    control = _lua_source()
    helper_start = control.index("local function get_or_create_planner_force")
    helper_end = control.index("local function find_exact_entity", helper_start)
    helper = control[helper_start:helper_end]
    inspect_start = control.index("local function inspect_sandbox_topology")
    inspect_end = control.index('commands.add_command("inspect_sandbox_topology"', inspect_start)
    inspection = control[inspect_start:inspect_end]

    assert "entity.force = force" not in helper
    assert ".destroy()" not in inspection
    assert 'payload.confirm ~= true' in control
    assert 'payload.mode ~= "reset" and payload.mode ~= "reconcile"' in control
    assert "entity.force = planner" in control
    assert "entity.destroy()" in control


def test_layout_contract_verifies_configuration_and_separate_authorizations() -> None:
    control = _lua_source()

    for marker in (
        "configuration_error(existing, action, direction)",
        "configure_created_entity(ghost, action)",
        "configure_created_entity(entity, action)",
        'place_ghost = "project_more_ghosts"',
        'place_entity = "place_core_infrastructure"',
        'remove_entity = "remove_entities"',
        '"direction_mismatch"',
        '"recipe_mismatch:expected="',
        '"infinity_filter_mismatch:expected="',
    ):
        assert marker in control


def test_build_plan_schema_requires_entity_and_complete_position() -> None:
    import json

    from jsonschema import Draft7Validator

    schema = json.loads((REPO_ROOT / "schemas" / "build_plan.schema.json").read_text(encoding="utf-8"))
    invalid = {"phases": [{"name": "bad", "actions": [{"action_type": "place_ghost"}]}]}
    errors = list(Draft7Validator(schema).iter_errors(invalid))
    messages = " ".join(error.message for error in errors)
    assert "entity" in messages
    assert "position" in messages

    incomplete = {
        "phases": [{"name": "bad", "actions": [{
            "action_type": "place_entity", "entity": "pipe", "position": {"x": 1}
        }]}]
    }
    assert any("y" in error.message for error in Draft7Validator(schema).iter_errors(incomplete))


def test_shared_composer_strips_local_power_and_active_callers_use_managed_mode() -> None:
    from planners.local_layout_planner import LocalLayoutPlanner
    from planners.sandbox_infrastructure import compose_managed_sandbox

    row = LocalLayoutPlanner().generate_line_layout("iron-gear-wheel", 2, 0, 40)
    composition = compose_managed_sandbox([("row", row)], [10], {"transport-belt": 10})
    row_actions = _actions(dict(composition["plans"])["row"])
    power_actions = _actions(dict(composition["infrastructure"])["unified_power"])
    assert all(a["entity"] not in {"electric-energy-interface", "substation"} for a in row_actions)
    assert sum(a["entity"] == "electric-energy-interface" for a in power_actions) == 1
    assert composition["scaffolding"]["managed_infrastructure"] is True

    for relative in (
        "tools/build_line.py",
        "tools/build_science_chain.py",
        "orchestrator/loop_daemon.py",
        "orchestrator/expansion_daemon.py",
    ):
        caller = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "compose_managed_sandbox" in caller
        assert "legacy_infrastructure" not in caller


def test_fluid_routing_is_split_and_layout_module_respects_file_limit() -> None:
    layouts = REPO_ROOT / "planners" / "fluid_layouts.py"
    routing = REPO_ROOT / "planners" / "fluid_routing.py"
    assert routing.exists()
    assert "def generate_fluid_chain_link" in routing.read_text(encoding="utf-8")
    assert len(layouts.read_text(encoding="utf-8").splitlines()) <= 500
