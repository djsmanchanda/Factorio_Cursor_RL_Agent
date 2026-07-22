# Path: tests/test_resource_survey.py
# Purpose: Verify deterministic observed-resource clustering and electronics site allocation.

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import random

import pytest
from jsonschema import Draft7Validator

from planners.electronics_world import ElectronicsWorldSpec
from planners.resource_survey import (
    allocate_electronics_world,
    cluster_resource_tiles,
    snapshot_from_world_spec,
    _water_site,
)
from planners.world_generation import default_world_payload

_ROOT = Path(__file__).resolve().parents[1]
_MACRO_BLOCK = {"x1": -250, "y1": -250, "x2": 250, "y2": 250}


def _allocate(snapshot: dict, *, validate_bundle: bool = False) -> dict:
    return allocate_electronics_world(
        snapshot, block_bounds=_MACRO_BLOCK, validate_bundle=validate_bundle,
    )


def _snapshot() -> dict:
    return snapshot_from_world_spec(default_world_payload(), tick=600)


def test_snapshot_world_observation_is_schema_valid_and_bounded() -> None:
    snapshot = _snapshot()
    schema = json.loads((_ROOT / "schemas" / "snapshot.schema.json").read_text(encoding="utf-8"))

    assert list(Draft7Validator(schema).iter_errors(snapshot)) == []
    assert snapshot["world_observation"]["bounds"] == {
        "x_min": -250, "y_min": -250,
        "x_max_exclusive": 250, "y_max_exclusive": 250,
    }
    assert snapshot["world_observation"]["seed"] == 17290718


def test_contiguous_tiles_cluster_by_resource_and_not_declared_patch_id() -> None:
    clusters = cluster_resource_tiles(_snapshot())
    counts = {}
    for cluster in clusters:
        counts[cluster.resource] = counts.get(cluster.resource, 0) + 1

    assert counts == {"coal": 1, "copper-ore": 3, "iron-ore": 3, "stone": 1}
    assert all(cluster.total_amount > 0 for cluster in clusters)


def test_shuffled_observation_produces_identical_world_spec() -> None:
    original = _snapshot()
    shuffled = deepcopy(original)
    random.Random(55).shuffle(shuffled["world_observation"]["resource_tiles"])
    random.Random(89).shuffle(shuffled["world_observation"]["water_tiles"])

    expected = _allocate(original)
    actual = _allocate(shuffled)

    assert actual == expected
    assert {patch["id"] for patch in actual["ore_patches"]} == {
        "survey-iron-ore-01", "survey-iron-ore-02", "survey-iron-ore-03",
        "survey-copper-ore-01", "survey-copper-ore-02", "survey-copper-ore-03",
        "survey-coal-01",
    }


def test_canonical_world_allocates_known_source_geometry_and_capacity() -> None:
    result = _allocate(_snapshot())
    lines = {line["stage"]: line for line in result["ore_lines"]}

    assert lines["iron_plate"]["drill_positions"][0] == [1.5, -221.5]
    assert lines["copper_plate_pu"]["drill_positions"][-1] == [12.5, 113.5]
    assert lines["copper_plate_pu"]["capacity_per_second"] == 9.0
    assert result["coal_drill_positions"] == [[20.5, 0.5], [23.5, 0.5], [26.5, 0.5]]
    assert result["pumpjack_sites"][0]["position"] == [20.5, -43.5]
    assert result["pumpjack_sites"][0]["capacity_per_second"] == 1000.0
    assert result["transport_scope"] == "local-block"
    assert result["block_bounds"] == {key: float(value) for key, value in _MACRO_BLOCK.items()}
    assert result["offshore_pump_sites"][0] == {
        "position": [20.5, 83.5], "output": [20, 80],
        "resource": "water", "capacity_per_second": 1200.0, "direction": "north",
    }
    ElectronicsWorldSpec.from_payload(result)


def test_allocation_requires_explicit_valid_macro_block_bounds() -> None:
    with pytest.raises(TypeError, match="block_bounds"):
        allocate_electronics_world(_snapshot())
    for bounds in (
        {"x1": 1, "y1": 0, "x2": 0, "y2": 1},
        {"x1": -251, "y1": -250, "x2": 250, "y2": 250},
        {"x1": -250, "y1": -250, "x2": 250},
    ):
        with pytest.raises(ValueError, match="block_bounds"):
            allocate_electronics_world(
                _snapshot(), block_bounds=bounds, validate_bundle=False,
            )


def test_snapshot_identity_uniqueness_amounts_and_bounds_are_strict() -> None:
    wrong_surface = _snapshot()
    wrong_surface["surface"] = "nauvis"
    with pytest.raises(ValueError, match="planner-sandbox"):
        _allocate(wrong_surface)

    duplicate_resource = _snapshot()
    duplicate_resource["world_observation"]["resource_tiles"].append(
        deepcopy(duplicate_resource["world_observation"]["resource_tiles"][0])
    )
    with pytest.raises(ValueError, match="Duplicate observed resource"):
        _allocate(duplicate_resource)

    duplicate_water = _snapshot()
    duplicate_water["world_observation"]["water_tiles"].append(
        deepcopy(duplicate_water["world_observation"]["water_tiles"][0])
    )
    with pytest.raises(ValueError, match="Duplicate observed water"):
        _allocate(duplicate_water)

    negative = _snapshot()
    negative["world_observation"]["resource_tiles"][0]["amount"] = -1
    with pytest.raises(ValueError, match="Snapshot validation failed"):
        _allocate(negative)

    outside = _snapshot()
    outside["world_observation"]["resource_tiles"][0]["position"]["x"] = 300.5
    with pytest.raises(ValueError, match="outside world bounds"):
        _allocate(outside)


def test_observed_oil_amount_derives_capacity_and_must_cover_headroom() -> None:
    weak = _snapshot()
    crude = next(
        entry for entry in weak["world_observation"]["resource_tiles"]
        if entry["resource"] == "crude-oil"
    )
    crude["amount"] = 100_000
    with pytest.raises(ValueError, match="crude-oil capacity.*below required"):
        _allocate(weak)


def test_invalid_shoreline_rejects_water_in_pump_output_path() -> None:
    water = [
        {"x": x, "y": y}
        for y in range(-5, 6)
        for x in range(-5, 6)
    ]
    with pytest.raises(ValueError, match="no valid in-block lake shoreline"):
        _water_site(
            {"water_tiles": water},
            {"x1": -5, "y1": -5, "x2": 5, "y2": 5},
        )

def test_missing_or_fragmented_resources_and_water_fail_closed() -> None:
    missing = _snapshot()
    missing["world_observation"]["resource_tiles"] = [
        entry for entry in missing["world_observation"]["resource_tiles"]
        if entry["resource"] != "coal"
    ]
    with pytest.raises(ValueError, match="coal patches"):
        _allocate(missing)

    fragmented = _snapshot()
    fragmented["world_observation"]["resource_tiles"] = [
        entry for entry in fragmented["world_observation"]["resource_tiles"]
        if not (
            entry["resource"] == "iron-ore"
            and -225 <= entry["position"]["y"] - 0.5 <= -215
            and int(entry["position"]["x"] - 0.5) % 3 == 1
        )
    ]
    with pytest.raises(ValueError, match="no unused iron-ore patch capable|complete deterministic drill row|lacks room"):
        _allocate(fragmented)

    dry = _snapshot()
    dry["world_observation"]["water_tiles"] = []
    with pytest.raises(ValueError, match="no water tiles"):
        _allocate(dry)


def test_out_of_block_patch_requires_cityplanner_rail_handoff() -> None:
    with pytest.raises(ValueError, match="CityPlanner rail interface required"):
        allocate_electronics_world(
            _snapshot(),
            block_bounds={"x1": 0, "y1": -250, "x2": 250, "y2": 250},
            validate_bundle=False,
        )


def test_full_m3_survey_m2_bundle_is_collision_checked() -> None:
    result = _allocate(_snapshot(), validate_bundle=True)

    assert result["version"] == "1.0.0"
    assert len(result["ore_lines"]) == 6


def test_lua_exports_only_persisted_bounded_world_observations() -> None:
    lua = (_ROOT / "factorio_mod" / "snapshot.lua").read_text(encoding="utf-8")

    assert "local function build_world_observation(surface)" in lua
    assert "saved.surface ~= surface.name or not saved.bounds" in lua
    assert "surface.find_entities_filtered({" in lua
    assert 'type = "resource"' in lua
    assert "for y = bounds.y_min, bounds.y_max_exclusive - 1 do" in lua
    assert 'name == "water" or name == "deepwater"' in lua
    assert "world_observation = build_world_observation(surface)" in lua


def test_plan_only_cli_accepts_snapshot_or_raw_observation_contracts() -> None:
    source = (_ROOT / "tools" / "survey_electronics_world.py").read_text(encoding="utf-8")

    assert '"world_observation" in payload' in source
    assert "allocate_electronics_world(" in source
    assert "--block-bounds" in source and "--single-macro-block" in source
    assert "--output" in source


def test_milestone_four_files_have_headers_and_stay_small() -> None:
    files = [
        _ROOT / "planners" / "resource_survey.py",
        _ROOT / "tools" / "survey_electronics_world.py",
        Path(__file__),
    ]
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        assert "Path:" in lines[0] and "Purpose:" in lines[1]
        assert len(lines) <= 500
