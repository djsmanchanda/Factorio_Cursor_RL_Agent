# Path: tests/test_rocket_silo_artifact.py | Purpose: Verify offline silo placement and catalog binding.

import copy
import hashlib
import json
from pathlib import Path

import pytest

from core.catalog_demand import mod_set_sha256, plan_catalog_demand
from core.optimizer_artifact_binding import bind_artifact_to_construction_plan
from core.optimizer_construction_state import ConstructionScope
from planners.plan_validation import entity_footprint_tiles, validate_build_plan
from planners.rocket_silo_artifact import build_rocket_silo_artifact
from tests.test_catalog_demand import _catalog as _recipe_catalog, _recipe


def _catalog():
    return {
        "kind": "runtime_catalog", "schema_version": "1.1.0", "ok": True, "tick": 42,
        "provenance": {"export_command": "export_runtime_catalog", "export_tick": 42,
                       "active_mods": {"base": "synthetic"}, "active_mods_count": 1,
                       "scope": {"surface_requested": True, "surface": "test-surface", "force": "test-force",
                                 "entity_scope": "selected_prototype_types", "entity_types": ["rocket-silo"]}},
        "entities": [{"name": "rocket-silo", "type": "rocket-silo", "tile_width": 9, "tile_height": 9,
                      "collision_box": {"min_x": -4.2, "min_y": -4.2, "max_x": 4.2, "max_y": 4.2},
                      "placement": {"items": [{"name": "silo-item", "count": 1}]}}],
    }


def _capture(catalog):
    raw = json.dumps(catalog).encode()
    return {"catalog_bytes": raw, "expected_sha256": hashlib.sha256(raw).hexdigest(),
            "surface": "test-surface", "force": "test-force", "active_mods": {"base": "synthetic"}}


def _feeding_capture():
    catalog = _catalog()
    catalog["provenance"]["scope"]["entity_types"].extend(["transport-belt", "inserter"])
    for name, kind in (("transport-belt", "transport-belt"), ("fast-inserter", "inserter")):
        catalog["entities"].append({"name": name, "type": kind, "tile_width": 1, "tile_height": 1,
                                    "placement": {"items": [{"name": name, "count": 1}]}})
    return _capture(catalog)


def _feed_recipe(*, ingredients=None, force="test-force"):
    if ingredients is None:
        ingredients = [("rocket-fuel", 10), ("processing-unit", 10), ("low-density-structure", 10)]
    catalog = _recipe_catalog(_recipe("rocket-part", ingredients, [("rocket-part", 1)]), force=force)
    raw = json.dumps(catalog).encode()
    return {"recipe_bytes": raw, "recipe_sha256": hashlib.sha256(raw).hexdigest()}


def test_silo_ghost_uses_captured_placement_and_binds_bill():
    capture = _capture(_catalog())
    artifact = build_rocket_silo_artifact(origin=[10, -20], placement_capture=capture)
    action = artifact["plan"]["phases"][0]["actions"][0]
    assert action["position"] == {"x": 14.5, "y": -15.5}
    assert entity_footprint_tiles(action) == {(x, y) for x in range(10, 19) for y in range(-20, -11)}
    assert artifact["declared_item_bill"] == {"silo-item": 1}
    scope = ConstructionScope("test-instance", "a" * 64, "test-surface", "test-force")
    bound = bind_artifact_to_construction_plan(
        artifact, scope=scope, project_id="silo", transaction_id="silo-build",
        throughput_output="rocket-part", throughput_target=1, throughput_unit="items/second",
    )
    assert dict(bound.plan.required_materials) == {"silo-item": 1}
    assert bound.execution_authorized is False
    assert "silo_input_delivery" in artifact["pending_checks"]


def test_silo_footprint_blocks_overlapping_plan():
    artifact = build_rocket_silo_artifact(origin=[0, 0], placement_capture=_capture(_catalog()))
    plan = copy.deepcopy(artifact["plan"])
    plan["phases"][0]["actions"].append({"action_type": "place_ghost", "entity": "pipe",
                                         "position": {"x": 0.5, "y": 0.5}})
    with pytest.raises(ValueError, match="collision"):
        validate_build_plan(plan)


def test_feed_stubs_bind_three_separate_recipe_inputs():
    capture = _feeding_capture()
    recipe = _feed_recipe()
    artifact = build_rocket_silo_artifact(origin=[0, 0], placement_capture=capture, **recipe)
    ports = artifact["planning_context"]["data"]["input_ports"]
    assert [port["item"] for port in ports] == ["low-density-structure", "processing-unit", "rocket-fuel"]
    assert [port["amount_per_part"] for port in ports] == [10, 10, 10]
    assert [port["belt_position"] for port in ports] == [
        {"x": 1.5, "y": -1.5}, {"x": 4.5, "y": -1.5}, {"x": 7.5, "y": -1.5}]
    actions = artifact["plan"]["phases"][0]["actions"]
    assert len(actions) == 7
    for port in ports:
        position = port["belt_position"]
        assert any(action["entity"] == "fast-inserter" and action["direction"] == "north"
                   and action["position"] == {"x": position["x"], "y": position["y"] + 1} for action in actions)
    assert artifact["declared_item_bill"] == {"silo-item": 1, "transport-belt": 3, "fast-inserter": 3}
    scope = ConstructionScope("test-instance", "a" * 64, "test-surface", "test-force")
    bound = bind_artifact_to_construction_plan(
        artifact, scope=scope, project_id="silo", transaction_id="silo-feed",
        throughput_output="rocket-part", throughput_target=1, throughput_unit="items/second",
    )
    assert dict(bound.plan.required_materials) == artifact["declared_item_bill"]
    assert bound.execution_authorized is False
    assert "unconnected" in artifact["unknowns"]["silo_input_delivery"]


@pytest.mark.parametrize("recipe", [
    _feed_recipe(force="other"),
    _feed_recipe(ingredients=[("a", 1), ("a", 2)]),
    _feed_recipe(ingredients=[("a", 1), ("b", 1), ("c", 1), ("d", 1)]),
    _feed_recipe(ingredients=[("a", 1, "fluid")]),
    {"recipe_bytes": b"{}", "recipe_sha256": "0" * 64},
])
def test_invalid_feed_recipes_fail_closed(recipe):
    with pytest.raises(ValueError):
        build_rocket_silo_artifact(origin=[0, 0], placement_capture=_feeding_capture(), **recipe)


def test_feeder_needs_captured_one_tile_inserter():
    capture = _feeding_capture()
    catalog = json.loads(capture["catalog_bytes"])
    catalog["entities"][-1]["tile_width"] = 2
    with pytest.raises(ValueError, match="1x1"):
        build_rocket_silo_artifact(origin=[0, 0], placement_capture=_capture(catalog), **_feed_recipe())


def test_historical_rocket_part_recipe_drives_dedicated_feeds():
    path = Path("data/factorio-2.1.21/probes/deterministic/server/script-output/factorio_mod/recipe_catalogs/recipe_catalog_243688.json")
    if not path.is_file():
        pytest.skip("historical catalog unavailable")
    data = path.read_bytes()
    capture = _feeding_capture()
    catalog = json.loads(capture["catalog_bytes"])
    catalog["provenance"]["scope"]["force"] = "player"
    capture = _capture(catalog)
    capture["force"] = "player"
    artifact = build_rocket_silo_artifact(origin=[0, 0], placement_capture=capture,
                                         recipe_bytes=data, recipe_sha256=hashlib.sha256(data).hexdigest())
    ports = artifact["planning_context"]["data"]["input_ports"]
    assert {port["item"]: port["amount_per_part"] for port in ports} == {
        "low-density-structure": 1, "processing-unit": 1, "rocket-fuel": 1}
    assert "silo_recipe_provenance" in artifact["pending_checks"]


@pytest.mark.parametrize("origin", [[True, 0], [0, 0.5], [0], [1_000_001, 0], "0,0"])
def test_invalid_silo_origin_fails_closed(origin):
    with pytest.raises(ValueError, match="origin"):
        build_rocket_silo_artifact(origin=origin, placement_capture=_capture(_catalog()))


@pytest.mark.parametrize("change", [
    lambda entity: entity.pop("tile_width"),
    lambda entity: entity.update(tile_height=10),
    lambda entity: entity.update(type="other"),
    lambda entity: entity.pop("collision_box"),
    lambda entity: entity["collision_box"].update(max_x=4.6),
    lambda entity: entity["collision_box"].update(min_x=True),
    lambda entity: entity["placement"].update(items=[]),
])
def test_unsupported_prototype_fails_closed(change):
    catalog = _catalog()
    change(catalog["entities"][0])
    with pytest.raises(ValueError):
        build_rocket_silo_artifact(origin=[0, 0], placement_capture=_capture(catalog))


def test_wrong_capture_hash_fails_closed():
    capture = _capture(_catalog())
    capture["expected_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        build_rocket_silo_artifact(origin=[0, 0], placement_capture=capture)


def test_silo_bill_drives_historical_recipe_demand():
    path = Path("data/factorio-2.1.21/probes/deterministic/server/script-output/factorio_mod/recipe_catalogs/recipe_catalog_243688.json")
    if not path.is_file():
        pytest.skip("historical catalog unavailable")
    data = path.read_bytes()
    recipes = json.loads(data)
    catalog = _catalog()
    catalog["entities"][0]["placement"]["items"][0]["name"] = "rocket-silo"
    artifact = build_rocket_silo_artifact(origin=[0, 0], placement_capture=_capture(catalog))
    mods = [{"name": "synthetic", "version": "0.0.0"}]
    provenance = {"catalog_sha256": hashlib.sha256(data).hexdigest(), "game_version": "0.0.0",
                  "force": recipes["force"], "surface": "synthetic", "export_tick": recipes["tick"],
                  "export_command": "/synthetic", "mods": mods, "mod_set_sha256": mod_set_sha256(mods)}
    names = ("concrete", "electric-engine-unit", "pipe", "processing-unit", "steel-plate")
    result = plan_catalog_demand(data, provenance=provenance, demand=artifact["declared_item_bill"],
                                 boundaries=[{"name": name, "cap": 1000} for name in names])
    assert result.balance.feasible is True
    assert dict(zip(result.balance.source_names, result.balance.source_rates)) == {
        "concrete": 1000, "electric-engine-unit": 200, "pipe": 100,
        "processing-unit": 200, "steel-plate": 1000,
    }
