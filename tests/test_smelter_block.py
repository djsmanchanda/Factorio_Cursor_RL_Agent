# Path: tests/test_smelter_block.py
# Purpose: Prove modular refinery sizing and End-to-Middle expansion preserve the approved structure.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planners.plan_validation import actions  # noqa: E402
from planners.smelter_block import (  # noqa: E402
    FURNACES_PER_MODULE,
    block_shape,
    generate_refinery_extension_plan,
    generate_refinery_plan,
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


def test_invalid_shapes_are_rejected() -> None:
    with pytest.raises(ValueError, match="at least one furnace"):
        block_shape(0)
    with pytest.raises(ValueError, match="must increase"):
        generate_refinery_extension_plan("iron-plate", 6, 6)
