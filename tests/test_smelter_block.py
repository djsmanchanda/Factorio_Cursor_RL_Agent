# Path: tests/test_smelter_block.py
# Purpose: Prove modular refinery sizing and End-to-Middle expansion preserve the approved structure.

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from planners.plan_validation import actions  # noqa: E402
from planners.smelter_block import (  # noqa: E402
    FURNACES_PER_MODULE,
    block_shape,
    generate_managed_refinery_extension_plan,
    generate_managed_refinery_plan,
    generate_refinery_extension_plan,
    generate_refinery_plan,
    refinery_interfaces,
    REFINERY_CAPACITY_SCHEDULES,
    scheduled_refinery_target,
    split_managed_refinery_extension_plan,
)


def _placements(plan: dict) -> list[dict]:
    return [action for action in actions(plan) if action["action_type"] == "place_ghost"]


def _at(plan: dict, x: float, y: float) -> list[dict]:
    return [
        action
        for action in _placements(plan)
        if action["position"] == {"x": x, "y": y}
    ]


def test_the_smallest_refinery_is_start_plus_end_with_six_furnaces() -> None:
    shape = block_shape(1)
    plan = generate_refinery_plan("iron-plate", 1)

    assert shape.columns == 1
    assert shape.middle_rows == 0
    assert shape.capacity == FURNACES_PER_MODULE
    assert [phase["name"] for phase in plan["phases"]] == [
        "refinery_start_iron-plate", "refinery_end_iron-plate",
    ]
    assert sum(action["entity"] == "electric-furnace" for action in _placements(plan)) == 6


def test_the_180_furnace_example_is_five_columns_and_five_middle_rows() -> None:
    shape = block_shape(180)
    plan = generate_refinery_plan("iron-plate", 180)

    assert (shape.columns, shape.middle_rows, shape.capacity) == (5, 5, 180)
    assert sum(action["entity"] == "electric-furnace" for action in _placements(plan)) == 180


def test_the_refinery_widens_before_it_moves_the_end() -> None:
    assert (block_shape(6).columns, block_shape(6).middle_rows) == (1, 0)
    assert (block_shape(30).columns, block_shape(30).middle_rows) == (5, 0)
    assert (block_shape(31).columns, block_shape(31).middle_rows) == (5, 1)


def test_scheduled_capacities_keep_exact_lattice_counts() -> None:
    for capacity in {value for schedule in REFINERY_CAPACITY_SCHEDULES for value in schedule}:
        assert block_shape(capacity).capacity == capacity


def test_first_generation_stops_at_its_cap_instead_of_rounding_to_sixty() -> None:
    assert scheduled_refinery_target(6, 7) == 12
    assert scheduled_refinery_target(12, 13) == 18
    assert scheduled_refinery_target(24, 25) == 30
    assert scheduled_refinery_target(48, 49) is None


def test_middle_topology_is_plate_furnace_ore_furnace_plate() -> None:
    plan = generate_refinery_plan("iron-plate", 31)

    assert _at(plan, 0.5, 3.5)[0]["entity"] == "fast-transport-belt"
    assert _at(plan, 3.5, 4.5)[0]["entity"] == "electric-furnace"
    assert _at(plan, 6.5, 3.5)[0]["entity"] == "fast-transport-belt"
    assert _at(plan, 9.5, 4.5)[0]["entity"] == "electric-furnace"
    assert _at(plan, 12.5, 3.5)[0]["entity"] == "fast-transport-belt"


def test_x_repeats_share_belts_and_poles_without_duplicate_actions() -> None:
    plan = generate_refinery_plan("copper-plate", 30)
    slots = [
        (action["position"]["x"], action["position"]["y"])
        for action in _placements(plan)
    ]

    assert len(slots) == len(set(slots))


def test_adding_a_middle_row_retires_only_end_belts_and_splitters() -> None:
    old = generate_refinery_plan("iron-plate", 30)
    plan = generate_refinery_extension_plan("iron-plate", 30, 31)
    retire = plan["phases"][0]
    old_end_slots = {
        (action["entity"], action["position"]["x"], action["position"]["y"])
        for action in old["phases"][-1]["actions"]
    }
    removed_slots = {
        (action["entity"], action["position"]["x"], action["position"]["y"])
        for action in retire["actions"]
    }

    assert retire["name"] == "retire_refinery_end_iron-plate"
    assert {action["entity"] for action in retire["actions"]} <= {
        "fast-transport-belt", "fast-splitter",
    }
    assert all(action["action_type"] == "remove_entity" for action in retire["actions"])
    assert removed_slots <= old_end_slots


def test_old_end_furnaces_become_the_first_middle_row() -> None:
    old = generate_refinery_plan("iron-plate", 30)
    extension = generate_refinery_extension_plan("iron-plate", 30, 31)
    old_furnaces = {
        (action["position"]["x"], action["position"]["y"])
        for action in _placements(old)
        if action["entity"] == "electric-furnace"
    }
    removals = {
        (action["position"]["x"], action["position"]["y"])
        for action in actions(extension)
        if action["action_type"] == "remove_entity"
    }

    assert old_furnaces.isdisjoint(removals)
    assert sum(
        action["entity"] == "electric-furnace"
        for action in extension["phases"][-1]["actions"]
    ) == 30


def test_extension_order_is_retire_then_repeat_then_end() -> None:
    plan = generate_refinery_extension_plan("iron-plate", 30, 31)

    assert [phase["name"] for phase in plan["phases"]] == [
        "retire_refinery_end_iron-plate",
        "extend_refinery_iron-plate",
        "finish_refinery_end_iron-plate",
    ]


def test_managed_output_taps_the_end_without_blocking_either_plate_belt() -> None:
    plan = generate_managed_refinery_plan("iron-plate", 6)
    interface = refinery_interfaces(6)
    output = plan["phases"][-1]

    assert output["name"] == "refinery_output_iron-plate"
    assert all(
        _at(plan, x, y)[0]["entity"] == "fast-transport-belt"
        for x, y in interface.plate_outputs
    )
    assert _at(plan, *interface.provider)[0]["entity"] == "passive-provider-chest"
    assert interface.provider not in interface.plate_outputs


def test_managed_extension_keeps_old_provider_until_growth_is_complete() -> None:
    delta = generate_managed_refinery_extension_plan(
        "iron-plate", 6, 12, current_variant="basic", target_variant="basic",
    )
    growth, cutover = split_managed_refinery_extension_plan(delta, "iron-plate")
    old_provider = refinery_interfaces(6, variant="basic").provider
    new_provider = refinery_interfaces(12, variant="basic").provider

    growth_actions = list(actions(growth))
    cutover_actions = list(actions(cutover))
    assert growth_actions
    assert not any(action["action_type"] == "remove_entity" for action in growth_actions)
    assert any(
        action["action_type"] == "remove_entity"
        and action["entity"] == "passive-provider-chest"
        and (action["position"]["x"], action["position"]["y"]) == old_provider
        for action in cutover_actions
    )
    assert any(
        action["action_type"] == "place_ghost"
        and action["entity"] == "passive-provider-chest"
        and (action["position"]["x"], action["position"]["y"]) == new_provider
        for action in growth_actions
    )


def test_managed_expansion_moves_the_output_tap_to_the_new_end() -> None:
    old_interface = refinery_interfaces(30)
    new_interface = refinery_interfaces(31)
    extension = generate_managed_refinery_extension_plan("iron-plate", 30, 31)
    removals = {
        (action["entity"], action["position"]["x"], action["position"]["y"])
        for action in actions(extension)
        if action["action_type"] == "remove_entity"
    }
    additions = {
        (action["entity"], action["position"]["x"], action["position"]["y"])
        for action in actions(extension)
        if action["action_type"] == "place_ghost"
    }

    assert ("passive-provider-chest", *old_interface.provider) in removals
    assert ("passive-provider-chest", *new_interface.provider) in additions
    assert new_interface.provider[1] > old_interface.provider[1]


def test_invalid_shapes_are_rejected() -> None:
    with pytest.raises(ValueError, match="at least one furnace"):
        block_shape(0)
    with pytest.raises(ValueError, match="must increase"):
        generate_refinery_extension_plan("iron-plate", 6, 6)


def test_basic_variant_bootstraps_one_six_furnace_regular_belt_module() -> None:
    plan = generate_managed_refinery_plan("iron-plate", 6, variant="basic")
    placements = _placements(plan)
    assert [phase["name"] for phase in plan["phases"][:2]] == [
        "refinery_start_iron-plate", "refinery_end_iron-plate",
    ]
    assert sum(action["entity"] == "electric-furnace" for action in placements) == 6
    assert any(
        action["entity"] == "fast-inserter"
        and action["position"] == {"x": 15.5, "y": 13.5}
        for action in placements
    )
    assert sum(action["entity"] == "transport-belt" for action in placements) == 53
    interface = refinery_interfaces(6, variant="basic")
    positions = {(a["position"]["x"], a["position"]["y"]): a for a in placements}
    assert interface.plate_outputs == ((14.5, 12.5),)
    assert positions[(14.5, 12.5)]["entity"] == "transport-belt"
    assert positions[(15.5, 12.5)]["entity"] == "transport-belt"
    assert positions[(15.5, 13.5)]["entity"] == "fast-inserter"
    assert positions[interface.provider]["entity"] == "passive-provider-chest"
    assert interface.provider == (15.5, 14.5)


def test_basic_to_standard_expansion_replaces_bootstrap_before_growth() -> None:
    plan = generate_managed_refinery_extension_plan(
        "iron-plate", 6, 12, current_variant="basic", target_variant="standard",
    )
    assert [phase["name"] for phase in plan["phases"][:2]] == [
        "retire_refinery_output_iron-plate",
        "retire_refinery_basic_iron-plate",
    ]
    assert plan["phases"][2]["name"] == "replace_refinery_iron-plate"
    core_removed = plan["phases"][1]["actions"]
    assert all(
        action["entity"] not in {"electric-furnace", "inserter", "medium-electric-pole"}
        for action in core_removed
    )
    assert any(
        action["entity"] == "fast-transport-belt"
        for action in plan["phases"][2]["actions"]
    )


def test_vertical_mirror_puts_the_ore_input_on_the_opposite_side() -> None:
    normal = refinery_interfaces(6, variant="basic")
    mirrored = refinery_interfaces(6, variant="basic", vertical_mirror=True)
    plan = generate_managed_refinery_plan(
        "stone-brick", 6, variant="basic", vertical_mirror=True,
    )

    assert mirrored.ore_inputs == tuple(
        (x, -y) for x, y in normal.ore_inputs
    )
    assert mirrored.provider == (normal.provider[0], -normal.provider[1])
    assert any(
        action.get("direction") == "south"
        for action in _placements(plan)
        if action.get("direction") in {"north", "south"}
    )


def test_mirrored_expansion_grows_north_without_flipping_back() -> None:
    old = refinery_interfaces(30, vertical_mirror=True)
    new = refinery_interfaces(31, vertical_mirror=True)
    extension = generate_managed_refinery_extension_plan(
        "iron-plate", 30, 31, vertical_mirror=True,
    )

    assert new.provider[1] < old.provider[1]
    assert any(
        action.get("entity") == "passive-provider-chest"
        and action.get("position") == {"x": new.provider[0], "y": new.provider[1]}
        for action in actions(extension)
    )
