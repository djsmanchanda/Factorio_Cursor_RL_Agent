# Path: tests/test_cohesive_smelter_expansion.py
# Purpose: Prove mining expansion grows one direct-belt plate refinery instead of planning another independent site.

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator.stage_services import StuckError  # noqa: E402


def _actions(plan: dict) -> list[dict]:
    return [
        action
        for phase in plan["phases"]
        for action in phase["actions"]
    ]


def test_plate_expansion_is_addition_only_and_keeps_one_origin() -> None:
    plan, _full, output = builder._plate_line_extension_plan(
        "iron-plate", 5, 12, (-10, -32),
        "transport-belt", "inserter", "east",
    )
    actions = _actions(plan)
    furnaces = [
        action for action in actions
        if action["entity"] == "electric-furnace"
    ]

    assert len(furnaces) == 7
    assert {action["position"]["y"] for action in furnaces} == {-28.5}
    assert min(action["position"]["x"] for action in furnaces) == 6.5
    assert output == (27.5, -23.5)
    assert not [action for action in actions if action["action_type"] == "remove_entity"]


def test_plate_expansion_preserves_direct_belts_and_old_side_tap() -> None:
    plan, full, _output = builder._plate_line_extension_plan(
        "copper-plate", 5, 12, (80, -19),
        "transport-belt", "inserter", "east",
    )
    actions = _actions(plan)

    assert not [action for action in actions if action["entity"] == "infinity-chest"]
    assert not [
        action for action in actions
        if action["position"] == {"x": 96.5, "y": -10.5}
    ], "the old provider remains live and connected"
    assert any(
        action["entity"] == "transport-belt"
        and action["position"] == {"x": 96.5, "y": -12.5}
        for action in _actions(full)
    ), "the complete output trunk still passes the old side tap"


def test_ambiguous_multi_site_smelters_fail_before_another_site_is_added() -> None:
    line = SimpleNamespace(
        recipe="iron-plate",
        machine_count=4,
        machine_positions=((1.5, 3.5), (4.5, 3.5), (1.5, 20.5), (4.5, 20.5)),
    )

    with pytest.raises(StuckError, match="separate row"):
        builder._existing_plate_smelter(object(), "nauvis", line)


def test_mining_expansion_validates_cohesion_before_submitting_drills() -> None:
    target_source = inspect.getsource(builder._cohesive_smelter_target)
    build_source = inspect.getsource(builder.build_mining_stage)

    assert "_existing_plate_smelter(" in target_source
    assert build_source.index("_cohesive_smelter_target(") < build_source.index(
        "_submit_mining_plan("
    )
    assert build_source.index("_extend_plate_smelter(") < build_source.index(
        "_build_initial_plate_smelter("
    )
    assert "system_drill_count_before + extraction.drill_count" in target_source



def test_westbound_expansion_keeps_the_existing_ore_feed_fixed() -> None:
    old, old_feed, _old_output = builder._plate_line_layout(
        "iron-plate", 5, (80, -19), "transport-belt", "inserter", "west",
    )
    plan, full, output = builder._plate_line_extension_plan(
        "iron-plate", 5, 12, (80, -19),
        "transport-belt", "inserter", "west",
    )
    _new, new_feed, _new_output = builder._plate_line_layout(
        "iron-plate", 12, (59, -19), "transport-belt", "inserter", "west",
    )
    old_furnaces = {
        (action["position"]["x"], action["position"]["y"])
        for action in _actions(old) if action["entity"] == "electric-furnace"
    }
    added_furnaces = [
        action for action in _actions(plan)
        if action["entity"] == "electric-furnace"
    ]

    assert any(
        action["entity"] == "transport-belt"
        and action["position"] == {"x": old_feed[0], "y": old_feed[1]}
        for action in _actions(full)
    ), "the original ore handoff remains a live tile on the expanded input trunk"
    assert not [
        action for action in _actions(plan)
        if action["position"] == {"x": old_feed[0], "y": old_feed[1]}
        and action["entity"] != "transport-belt"
    ]
    assert new_feed[0] >= old_feed[0]
    assert all(action["position"]["x"] < min(x for x, _y in old_furnaces)
               for action in added_furnaces)
    assert output == (57.5, -10.5)
    assert not [action for action in _actions(plan)
                if action["action_type"] == "remove_entity"]
    assert len([action for action in _actions(full)
                if action["entity"] == "electric-furnace"]) == 12

