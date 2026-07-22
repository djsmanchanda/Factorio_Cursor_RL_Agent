# Path: tests/test_electronics_block.py
# Purpose: Verify immutable, surveyed raw-resource electronics phases and contracts.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from planners.electronics_block import DEPENDENCIES_2A, DEPENDENCIES_2B, build_electronics_block
from planners.electronics_world import ElectronicsWorldSpec, load_electronics_world_spec
from planners.infrastructure import validate_power_connectivity
from planners.plan_validation import actions, validate_no_collisions, validate_placement_subset

_FIXTURE = Path(__file__).parent / "fixtures" / "electronics_world_spec.json"


@pytest.fixture(scope="module")
def world():
    return load_electronics_world_spec(_FIXTURE)


@pytest.fixture(scope="module")
def advanced(world):
    return build_electronics_block(include_processing=False, world=world)


@pytest.fixture(scope="module")
def processing(world):
    return build_electronics_block(include_processing=True, world=world)


def _all_actions(bundle):
    return [
        action
        for _, plan in bundle["infrastructure"] + bundle["plans"]
        for action in actions(plan)
    ]


def test_advanced_circuit_chain_is_raw_resource_complete_and_immutable(advanced):
    assert set(map(tuple, advanced["dependencies"])) == DEPENDENCIES_2A
    emitted = _all_actions(advanced)
    assert not {"infinity-chest", "infinity-pipe"} & {a.get("entity") for a in emitted}
    assert not any(a["action_type"] == "remove_entity" for a in emitted)
    assert sum(a.get("entity") == "electric-energy-interface" for a in emitted) == 1
    assert "reserve_ac_output" in dict(advanced["plans"])
    assert advanced["interfaces_reserved"] is True
    assert "ore_patches" not in advanced["scaffolding"]


def test_processing_chain_consumes_sulfur_and_uses_reserved_interface(processing):
    assert set(map(tuple, processing["dependencies"])) == DEPENDENCIES_2B
    plans = dict(processing["plans"])
    assert {"sulfur", "sulfuric_acid", "processing_unit", "sulfur_to_acid"} <= plans.keys()
    assert {"reserve_ac_output", "ac_to_pu"} <= plans.keys()
    emitted = _all_actions(processing)
    assert not {"infinity-chest", "infinity-pipe"} & {a.get("entity") for a in emitted}
    assert not any(a["action_type"] == "remove_entity" for a in emitted)
    assert sum(a.get("entity") == "electric-energy-interface" for a in emitted) == 1


def test_both_blocks_fit_the_surveyed_bounded_surface(advanced, processing):
    expected = {"x1": -250, "y1": -250, "x2": 250, "y2": 250}
    assert advanced["block_footprint"] == expected
    assert processing["block_footprint"] == expected
    assert advanced["world_spec"]["map_bounds"] == expected


def test_2a_placements_are_a_strict_subset_of_2b(advanced, processing):
    early = advanced["infrastructure"] + advanced["plans"]
    ultimate = processing["infrastructure"] + processing["plans"]
    validate_placement_subset(early, ultimate)
    assert len(early) < len(ultimate)


def test_versioned_throughput_contract_declares_headroom_and_endpoints(processing):
    contract = processing["throughput_contract"]
    assert contract["version"] == "1.0.0"
    assert contract["headroom_factor"] == 1.25
    assert contract["targets"] == {"advanced-circuit": 0.6, "processing-unit": 0.1}
    assert {entry["kind"] for entry in contract["interfaces"]} == {"item", "fluid"}
    for entry in contract["interfaces"]:
        assert entry["producer"]["position"]
        assert entry["consumer"]["position"]
        assert entry["capacity_per_second"] >= entry["required_with_headroom_per_second"]
    crude = next(entry for entry in contract["interfaces"] if entry["name"] == "crude_to_refinery")
    assert crude["required_rate_per_second"] == pytest.approx(85 / 3)
    assert crude["required_with_headroom_per_second"] == pytest.approx(425 / 12)


def test_world_spec_is_schema_loaded_and_fails_closed_without_observed_resources():
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    payload["ore_lines"][0]["patch_id"] = "coal"
    with pytest.raises(ValueError, match="matching surveyed patch"):
        ElectronicsWorldSpec.from_payload(payload)

    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="Additional properties"):
        ElectronicsWorldSpec.from_payload(payload)


def test_every_emitted_drill_is_declared_by_the_world_spec(processing, world):
    emitted = {
        (action["position"]["x"], action["position"]["y"])
        for action in _all_actions(processing)
        if action.get("entity") == "electric-mining-drill"
    }
    declared = set(world.coal_drill_positions)
    for line in world.ore_lines:
        declared.update(line["drill_positions"])
    assert emitted == declared

def test_relocated_power_spine_stays_connected_and_nonoverlapping(processing):
    complete = processing["infrastructure"] + processing["plans"]
    combined = {
        "phases": [
            {"name": f"{name}/{phase['name']}", "actions": phase["actions"]}
            for name, plan in complete
            for phase in plan["phases"]
        ]
    }

    validate_power_connectivity(combined)
    validate_no_collisions(complete)