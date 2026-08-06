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

from orchestrator.stage_services import _power_bridge_hops

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
from planners.sandbox_infrastructure import CANONICAL_ROBOPORT_HUB
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


def _actions(plan: dict) -> list:
    return [action for phase in plan["phases"] for action in phase["actions"]]






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


def test_roboport_validator_rejects_a_split_network() -> None:
    sites = [{"name": "a", "position": (0, 0)}, {"name": "b", "position": (40, 0)}]
    plan = plan_roboport_network(sites)
    broken = copy.deepcopy(plan)
    _actions(broken)[-1]["position"] = {"x": 100, "y": 0}

    with pytest.raises(ValueError, match="separate logistic networks"):
        validate_roboport_network(broken, sites)


def test_composed_processing_bundle_has_one_source_no_local_power_and_no_collisions(
    processing_bundle,
) -> None:
    infrastructure = processing_bundle["infrastructure"]
    plans = processing_bundle["plans"]
    validate_no_collisions(infrastructure + plans)

    infrastructure_actions = _actions(dict(infrastructure)["unified_power"])
    production_actions = [action for _, plan in plans for action in _actions(plan)]
    assert sum(a["entity"] == POWER_SOURCE_ENTITY for a in infrastructure_actions) == 1
    assert all(a["entity"] not in {POWER_SOURCE_ENTITY, "substation"} for a in production_actions)


def test_composed_bundle_validator_detects_cross_plan_collision(processing_bundle) -> None:
    infrastructure = processing_bundle["infrastructure"]
    plans = copy.deepcopy(processing_bundle["plans"])
    collision_position = roboport_positions(dict(infrastructure)["unified_roboports"])[0]
    plans[0][1]["phases"][0]["actions"].append({
        "action_type": "place_entity",
        "entity": "pipe",
        "position": {"x": collision_position[0], "y": collision_position[1]},
    })

    with pytest.raises(ValueError, match="Plan collision"):
        validate_no_collisions(infrastructure + plans)


def test_managed_scaffolding_targets_every_planned_roboport_without_infrastructure(
    processing_bundle,
) -> None:
    robots = dict(processing_bundle["infrastructure"])["unified_roboports"]
    payload = scaffolding_with_materials(processing_bundle["scaffolding"], {"pipe": 12})

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

    tile_ghost = {
        "phases": [{"name": "landfill", "actions": [{
            "action_type": "place_tile_ghost", "tile": "landfill",
            "position": {"x": 4, "y": 7},
        }]}]
    }
    assert not list(Draft7Validator(schema).iter_errors(tile_ghost))

    invalid_tile_ghost = {
        "phases": [{"name": "landfill", "actions": [{
            "action_type": "place_tile_ghost", "position": {"x": 4, "y": 7},
        }]}]
    }
    assert any("tile" in error.message for error in Draft7Validator(schema).iter_errors(invalid_tile_ghost))


def test_shared_composer_strips_local_power() -> None:
    from planners.local_layout_planner import LocalLayoutPlanner
    from planners.sandbox_infrastructure import compose_managed_sandbox

    row = LocalLayoutPlanner().generate_line_layout("iron-gear-wheel", 2, 0, 40)
    composition = compose_managed_sandbox([("row", row)], [10], {"transport-belt": 10})
    row_actions = _actions(dict(composition["plans"])["row"])
    power_actions = _actions(dict(composition["infrastructure"])["unified_power"])
    assert all(a["entity"] not in {"electric-energy-interface", "substation"} for a in row_actions)
    assert sum(a["entity"] == "electric-energy-interface" for a in power_actions) == 1
    assert composition["scaffolding"]["managed_infrastructure"] is True



def test_power_bridge_detours_around_occupied_tiles() -> None:
    occupied_tiles = {(x, 0) for x in range(1, 20)}

    hops = _power_bridge_hops((0.0, 0.0), (24.0, 0.0), 6.5, occupied_tiles)

    assert hops
    assert not {(int(x // 1), int(y // 1)) for x, y in hops} & occupied_tiles
    chain = [(0.0, 0.0), *hops, (24.0, 0.0)]
    assert all(
        ((right[0] - left[0]) ** 2 + (right[1] - left[1]) ** 2) ** 0.5 <= 7.5
        for left, right in zip(chain, chain[1:])
    )
