# Path: tests/test_laya_small_stone_line.py | Purpose: The small stone-brick line option is sized to the furnaces on hand and is legal at every unit count.
from __future__ import annotations

from planners.plan_validation import validate_build_plan
from training import laya_starters


def _fixture():
    samples = [{"item": "stone", "position": {"x": x, "y": y}}
               for x in range(-8, 9, 4) for y in range(-8, 9, 4)]
    facts = {
        "ports": [{"energy": 1, "network": 1, "construction_radius": 40, "logistic_radius": 40,
                   "position": {"x": 0, "y": 0}}],
        "resources": samples,
    }
    plan = {"zones": [{"purpose": "ore:stone", "rect": [-10, -10, 10, 10]}]}
    return facts, plan


def test_small_line_takes_the_furnaces_on_hand():
    facts, plan = _fixture()
    candidates = laya_starters.small_line_candidates(facts, plan, "stone-brick", "stone", 3, per_plate=1)
    assert candidates, "a small stone line should be offered when at least two furnaces are on hand"
    option = candidates[0]
    assert option["features"]["furnaces"] == 3
    assert option["features"]["small_line"] is True
    validate_build_plan(option["plan"])
    furnaces = [a for a in option["plan"]["phases"][0]["actions"] if a["entity"] == "electric-furnace"]
    assert len(furnaces) == 3
    assert all(a["entity"] != "stone-furnace" for a in option["plan"]["phases"][0]["actions"])


def test_small_line_needs_two_furnaces_and_stone():
    facts, plan = _fixture()
    assert laya_starters.small_line_candidates(facts, plan, "stone-brick", "stone", 1) == []
    assert laya_starters.small_line_candidates(facts, plan, "iron-plate", "iron-ore", 4) == []
    assert laya_starters.small_line_candidates(facts, None, "stone-brick", "stone", 4) == []
