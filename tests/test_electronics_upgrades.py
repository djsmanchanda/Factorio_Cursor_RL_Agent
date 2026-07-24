# Path: tests/test_electronics_upgrades.py
# Purpose: Verify native initial logistics and deterministic premium upgrade plans.

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft7Validator

from planners.electronics_block import BELT, INSERTER
from planners.electronics_upgrades import INITIAL_TO_PREMIUM, build_premium_upgrade_plan

ROOT = Path(__file__).resolve().parents[1]


def test_initial_electronics_tiers_are_nauvis_native_fast_logistics():
    assert (BELT, INSERTER) == ("fast-transport-belt", "fast-inserter")


def test_premium_upgrade_plan_is_sorted_deduplicated_and_schema_valid():
    bundle = {
        "plans": [
            ("second", {"phases": [{"actions": [
                {"action_type": "place_ghost", "entity": "fast-inserter", "position": {"x": 3.5, "y": 1.5}},
            ]}]}),
            ("first", {"phases": [{"actions": [
                {"action_type": "place_ghost", "entity": "fast-transport-belt", "position": {"x": 2.5, "y": 1.5}},
                {"action_type": "place_entity", "entity": "fast-transport-belt", "position": {"x": 2.5, "y": 1.5}},
            ]}]}),
        ]
    }
    plan = build_premium_upgrade_plan(bundle)
    assert plan == {
        "actions": [
            {"action": "entity_tier_upgrade", "block": "first", "from_name": "fast-transport-belt",
             "to_name": "express-transport-belt", "position": {"x": 2.5, "y": 1.5}},
            {"action": "entity_tier_upgrade", "block": "second", "from_name": "fast-inserter",
             "to_name": "stack-inserter", "position": {"x": 3.5, "y": 1.5}},
        ]
    }
    schema = json.loads((ROOT / "schemas" / "upgrade_plan.schema.json").read_text(encoding="utf-8"))
    assert not list(Draft7Validator(schema).iter_errors(plan))


def test_upgrade_executor_supports_generic_entity_tier_upgrades():
    lua = (ROOT / "factorio_mod" / "upgrades.lua").read_text(encoding="utf-8")
    assert 'entry.action == "entity_tier_upgrade"' in lua
    assert INITIAL_TO_PREMIUM["fast-inserter"] == "stack-inserter"