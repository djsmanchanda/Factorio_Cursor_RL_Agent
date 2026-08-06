# Path: tests/test_refinery_blueprints.py
# Purpose: Pin the supplied refinery blueprints and preserve splitter priorities through BuildPlan and Lua execution.

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from jsonschema import Draft7Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planners.refinery_blueprints import (  # noqa: E402
    decoded_template, template_actions, template_name,
)


def test_supplied_templates_decode_with_their_declared_snap_grids() -> None:
    assert decoded_template("start")["snap-to-grid"] == {"x": 12, "y": 3}
    assert decoded_template("middle")["snap-to-grid"] == {"x": 12, "y": 9}
    assert decoded_template("end")["snap-to-grid"] == {"x": 12, "y": 11}


def test_supplied_templates_keep_their_entity_inventories() -> None:
    assert Counter(entity["name"] for entity in decoded_template("start")["entities"]) == {
        "fast-transport-belt": 20, "fast-splitter": 3,
    }
    assert Counter(entity["name"] for entity in decoded_template("middle")["entities"]) == {
        "electric-furnace": 6, "fast-transport-belt": 27,
        "inserter": 12, "medium-electric-pole": 3,
    }


def test_splitter_priorities_survive_translation() -> None:
    start_splitters = [
        action for action in template_actions("start") if action["entity"] == "fast-splitter"
    ]
    end_splitters = [
        action
        for action in template_actions("end", recipe="iron-plate")
        if action["entity"] == "fast-splitter"
    ]

    assert {
        (action["input_priority"], action["output_priority"])
        for action in start_splitters
    } == {("right", "right")}
    assert {
        (action["input_priority"], action["output_priority"])
        for action in end_splitters
    } == {("left", "left")}


def test_priority_actions_validate_against_build_plan_1_4() -> None:
    schema = json.loads((REPO_ROOT / "schemas" / "build_plan.schema.json").read_text())
    plan = {"phases": [{"name": "refinery_start", "actions": template_actions("start")}]}

    assert schema["title"] == "BuildPlan 1.4"
    assert not list(Draft7Validator(schema).iter_errors(plan))


def test_lua_executor_applies_and_verifies_both_splitter_priorities() -> None:
    executor = (REPO_ROOT / "factorio_mod" / "layout_executor.lua").read_text()

    for field in ("splitter_input_priority", "splitter_output_priority"):
        assert f"entity.{field} = action." in executor
        assert f"return entity.{field}" in executor


def test_basic_templates_are_regular_belt_bootstrap_variants() -> None:
    assert decoded_template("basic_start")["snap-to-grid"] == {"x": 12, "y": 3}
    assert decoded_template("basic_middle")["snap-to-grid"] == {"x": 12, "y": 9}
    assert decoded_template("basic_end")["snap-to-grid"] == {"x": 12, "y": 11}
    assert Counter(entity["name"] for entity in decoded_template("basic_start")["entities"]) == {
        "transport-belt": 13, "splitter": 1,
    }
    assert Counter(entity["name"] for entity in decoded_template("basic_end")["entities"]) == {
        "transport-belt": 38, "splitter": 2, "electric-furnace": 6,
        "inserter": 12, "medium-electric-pole": 3,
    }
    assert template_name("start", "basic") == "basic_start"
    assert template_name("middle", "basic") == "basic_middle"
    assert template_name("end", "basic") == "basic_end"


def test_basic_splitter_priorities_survive_translation() -> None:
    splitters = [
        action for action in template_actions("basic_start")
        if action["entity"] == "splitter"
    ]
    assert {(action["input_priority"], action["output_priority"]) for action in splitters} == {
        ("right", "right"),
    }
