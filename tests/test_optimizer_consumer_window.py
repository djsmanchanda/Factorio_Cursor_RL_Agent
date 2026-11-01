# Path: tests/test_optimizer_consumer_window.py | Purpose: Preserve the limits of scoped consumer counter observations.
import copy
from dataclasses import replace

import pytest

from core.optimizer_consumer_window import compare_consumer_window
from tests.test_optimizer_saved_world import world


def report(tick, count, request_id):
    return {"version": "optimizer-consumer-observation-v1", "kind": "optimizer_consumer_observation",
            "request_id": request_id, "tick": tick, "ok": True, "ownership_claimed": False,
            "dispatch_authorized": False, "surface": "nauvis", "force": "player",
            "requested": {"entity": "chemical-plant", "position": {"x": 1.5, "y": 2.5}, "unit_number": 42},
            "observation": {"state": "present", "recipe": {"state": "available", "value": "plastic-bar"},
                            "products_finished": {"state": "available", "value": count}}}


def test_matching_consumer_window_reports_only_counter_advance(world):
    _adapter, bound, _ledger = world
    before, after = report(100, 1000, "first"), report(700, 1005, "second")
    result = compare_consumer_window(bound, before, after, expected_recipe="plastic-bar")
    assert result["status"] == "counter_advanced" and result["counter_delta"] == 5
    assert result["source_output"] == bound.plan.throughput_output
    assert result["consumer"]["unit_number"] == 42
    assert result["ownership_claimed"] is result["commissioned"] is result["dispatch_authorized"] is False
    assert "not converted to fluid consumption or delivery" in result["non_claims"][2]
    changed = copy.deepcopy(after)
    changed["observation"]["products_finished"]["value"] += 1
    assert compare_consumer_window(bound, before, changed, expected_recipe="plastic-bar")["evidence_sha256"] != result["evidence_sha256"]


@pytest.mark.parametrize("change,reason", [
    ("missing", "consumer_unavailable"), ("recipe", "recipe_unavailable_or_changed"),
    ("unset", "recipe_unavailable_or_changed"), ("counter", "counter_unavailable"), ("reset", "counter_regressed"),
])
def test_unreliable_observation_stays_unverified(world, change, reason):
    _adapter, bound, _ledger = world
    before, after = report(100, 1000, "first"), report(700, 1005, "second")
    obs = after["observation"]
    if change == "missing": after["observation"] = {"state": "unavailable", "reason": "entity_not_found"}
    elif change == "recipe": obs["recipe"]["value"] = "other"
    elif change == "unset": obs["recipe"] = {"state": "unset"}
    elif change == "counter": obs["products_finished"] = {"state": "unavailable"}
    else: obs["products_finished"]["value"] = 999
    result = compare_consumer_window(bound, before, after, expected_recipe="plastic-bar")
    assert result["status"] == "unverified" and result["reason"] == reason and result["counter_delta"] is None
    assert result["commissioned"] is False


def test_zero_progress_is_explicit_not_a_success(world):
    _adapter, bound, _ledger = world
    result = compare_consumer_window(bound, report(100, 7, "first"), report(700, 7, "second"),
                                     expected_recipe="plastic-bar")
    assert result["status"] == "no_counter_advance" and result["counter_delta"] == 0
    assert result["commissioned"] is False


@pytest.mark.parametrize("change", ["unit", "position", "scope", "same_id", "tick", "float_tick", "float_counter", "claim", "binding"])
def test_wrong_window_identity_or_malformed_report_is_rejected(world, change):
    _adapter, bound, _ledger = world
    before, after = report(100, 1000, "first"), report(700, 1005, "second")
    if change == "unit": after["requested"]["unit_number"] += 1
    elif change == "position": after["requested"]["position"]["x"] += 1
    elif change == "scope": after["force"] = "other"
    elif change == "same_id": after["request_id"] = "first"
    elif change == "tick": after["tick"] = 100
    elif change == "float_tick": after["tick"] = 700.0
    elif change == "float_counter": after["observation"]["products_finished"]["value"] = 1005.0
    elif change == "claim": after["ownership_claimed"] = True
    else: bound = replace(bound, canonical_binding=bound.canonical_binding + b" ")
    with pytest.raises(ValueError):
        compare_consumer_window(bound, before, after, expected_recipe="plastic-bar")
