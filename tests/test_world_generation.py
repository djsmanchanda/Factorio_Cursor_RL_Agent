# Path: tests/test_world_generation.py
# Purpose: Verify the bounded deterministic world and starter-kit contracts offline.

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from planners.electronics_block import build_electronics_block
from planners.electronics_world import ElectronicsWorldSpec
from planners.plan_validation import actions, assert_no_production_infinity
from planners.world_generation import PlannerWorldSpec, default_world_payload, validate_world_payload


_ROOT = Path(__file__).resolve().parents[1]


def _overlap(left: dict, right: dict) -> bool:
    return not (
        left["x2"] < right["x1"] or right["x2"] < left["x1"]
        or left["y2"] < right["y1"] or right["y2"] < left["y1"]
    )


def test_world_is_exactly_500_by_500_and_deterministic() -> None:
    first = PlannerWorldSpec.canonical()
    second = PlannerWorldSpec.canonical()

    assert first.to_json() == second.to_json()
    assert first.payload["bounds"] == {
        "x_min": -250, "y_min": -250,
        "x_max_exclusive": 250, "y_max_exclusive": 250,
        "width": 500, "height": 500,
    }
    assert first.payload["seed"] == 17290718
    assert first.payload["map_generation"] == {
        "autoplace_enabled": False, "generate_with_lab_tiles": True,
    }


def test_resources_are_dispersed_non_overlapping_and_complete() -> None:
    world = default_world_payload()
    patches = world["resource_patches"]

    assert {patch["resource"] for patch in patches} == {
        "iron-ore", "copper-ore", "coal", "stone",
    }
    assert len({patch["id"] for patch in patches}) == len(patches)
    assert all(not _overlap(left, right) for index, left in enumerate(patches)
               for right in patches[index + 1:])
    centers = [((patch["x1"] + patch["x2"]) / 2, (patch["y1"] + patch["y2"]) / 2)
               for patch in patches]
    assert max(x for x, _ in centers) - min(x for x, _ in centers) >= 100
    assert max(y for _, y in centers) - min(y for _, y in centers) >= 250


def test_crude_and_water_are_explicit_and_offshore_edge_is_valid() -> None:
    world = default_world_payload()
    lake = world["water_lake"]
    site = lake["offshore_edge_candidates"][0]

    assert world["crude_oil_spots"] == [
        {"id": "crude_primary", "position": [20.5, -43.5], "amount": 30_000_000}
    ]
    assert lake["tile"] == "water"
    assert lake["x1"] <= site["position"][0] <= lake["x2"]
    assert site["position"][1] == lake["y1"] - 0.5
    assert site["direction"] == "north"


def test_starter_kit_is_construction_only_and_contains_no_infinity() -> None:
    kit = default_world_payload()["starter_kit"]
    materials = set(kit["construction_materials"])
    production_items = {
        "iron-ore", "copper-ore", "coal", "stone", "crude-oil", "water",
        "iron-plate", "copper-plate", "copper-cable", "plastic-bar", "sulfur",
        "sulfuric-acid", "electronic-circuit", "advanced-circuit", "processing-unit",
    }

    assert kit["production_ingredients"] == {}
    assert not materials & production_items
    assert not any("infinity" in name for name in materials)
    assert kit["power_source"]["position"] == [-160, -160]
    assert kit["roboport_hub"]["position"] == [-128, -128]
    assert kit["roboport_hub"]["network_policy"] == "single-connected"


def test_generated_electronics_survey_builds_the_complete_milestone_two_block() -> None:
    world = PlannerWorldSpec.canonical()
    electronics = ElectronicsWorldSpec.from_payload(world.electronics_payload())
    bundle = build_electronics_block(include_processing=True, world=electronics)

    assert bundle["world_spec"]["map_bounds"] == {
        "x1": -250, "y1": -250, "x2": 250, "y2": 250,
    }
    assert_no_production_infinity(bundle["production"])
    entities = {action["entity"] for _, plan in bundle["production"] for action in actions(plan)}
    assert {"electric-mining-drill", "pumpjack", "offshore-pump"} <= entities


def test_json_round_trip_is_save_reload_stable() -> None:
    original = PlannerWorldSpec.canonical()
    restored = PlannerWorldSpec.from_json(original.to_json())

    assert restored == original
    assert restored.to_json() == original.to_json()


def test_world_contract_rejects_autoplace_overlap_and_production_starter_items() -> None:
    autoplace = deepcopy(default_world_payload())
    autoplace["map_generation"]["autoplace_enabled"] = True
    with pytest.raises(ValueError, match="autoplace_enabled"):
        validate_world_payload(autoplace)

    overlap = deepcopy(default_world_payload())
    overlap["resource_patches"][1].update(overlap["resource_patches"][0])
    overlap["resource_patches"][1]["id"] = "overlap"
    with pytest.raises(ValueError, match="overlaps"):
        validate_world_payload(overlap)

    production = deepcopy(default_world_payload())
    production["starter_kit"]["production_ingredients"] = {"iron-plate": 100}
    with pytest.raises(ValueError, match="production_ingredients"):
        validate_world_payload(production)


def test_lua_world_creation_is_confirmation_gated_and_persistent() -> None:
    lua = (_ROOT / "factorio_mod" / "world_generation.lua").read_text(encoding="utf-8")
    shared = (_ROOT / "factorio_mod" / "sandbox_shared.lua").read_text(encoding="utf-8")
    control = (_ROOT / "factorio_mod" / "control.lua").read_text(encoding="utf-8")

    assert 'require("world_generation")' in control
    assert 'payload.confirm ~= true' in lua
    assert 'CREATE_TOKEN = "CREATE_PLANNER_WORLD"' in lua
    assert 'RESET_TOKEN = "RESET_PLANNER_WORLD"' in lua
    assert 'game.create_surface(SURFACE_NAME, map_gen_settings(spec))' in lua
    assert 'game.delete_surface(existing)' in lua
    assert 'storage.planner_world = {' in lua
    assert 'default_enable_all_autoplace_controls = false' in lua
    assert 'treat_missing_as_default = false' in lua
    assert 'name = "crude-oil"' in lua
    assert 'surface.set_tiles(tiles, true)' in lua
    assert "infinity-chest" not in lua and "infinity-pipe" not in lua
    assert "run the confirmed WorldSpec command first" in shared


def test_world_rejects_cross_contract_drift_before_execution() -> None:
    canonical = deepcopy(default_world_payload())
    canonical["starter_kit"]["power_source"]["position"] = [-159, -160]
    with pytest.raises(ValueError, match="canonical source"):
        validate_world_payload(canonical)

    electronics_patch = deepcopy(default_world_payload())
    electronics_patch["electronics_world"]["ore_patches"][0]["x1"] += 1
    with pytest.raises(ValueError, match="differs from generated world"):
        validate_world_payload(electronics_patch)

    pumpjack = deepcopy(default_world_payload())
    pumpjack["electronics_world"]["pumpjack_sites"][0]["position"] = [21.5, -43.5]
    with pytest.raises(ValueError, match="no generated crude-oil spot"):
        validate_world_payload(pumpjack)

    lake = deepcopy(default_world_payload())
    lake["water_lake"].update({"x1": 18, "y1": -2, "x2": 30, "y2": 4})
    lake["water_lake"]["offshore_edge_candidates"][0]["position"] = [20.5, -2.5]
    with pytest.raises(ValueError, match="overlaps a resource patch"):
        validate_world_payload(lake)


def test_lua_validates_every_contract_before_mutation_and_guards_reset_ownership() -> None:
    lua = (_ROOT / "factorio_mod" / "world_generation.lua").read_text(encoding="utf-8")

    assert "local function is_integer(value)" in lua
    assert "local function validate_resources(spec)" in lua
    assert "local function validate_crude(spec)" in lua
    assert "local function validate_lake(spec, resource_patches)" in lua
    assert "local function validate_starter_kit(starter)" in lua
    assert "local function validate_electronics_world(world, patches, crude_spots, lake)" in lua
    assert "local function owns_existing_surface(surface)" in lua
    assert "reset refused: existing planner-sandbox is not owned" in lua
    assert "saved.owner == OWNER and saved.surface == SURFACE_NAME" in lua
    assert "Starter kit item is not construction-only" in lua
    assert "electronics pumpjack has no generated crude-oil spot" in lua
    assert lua.index("local spec = validate_spec(payload.world_spec)") < lua.index("game.delete_surface(existing)")
    assert lua.index("local spec = validate_spec(payload.world_spec)") < lua.index("return create_world(spec)")

def test_new_files_obey_headers_and_line_limit() -> None:
    files = [
        _ROOT / "planners" / "world_generation.py",
        _ROOT / "factorio_mod" / "world_generation.lua",
        _ROOT / "tools" / "build_world_spec.py",
        Path(__file__),
    ]
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        assert "Path:" in lines[0]
        assert "Purpose:" in lines[1]
        assert len(lines) <= 500