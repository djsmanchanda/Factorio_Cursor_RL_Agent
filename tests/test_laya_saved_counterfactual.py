# Path: tests/test_laya_saved_counterfactual.py | Purpose: Protect starter budgets, quad geometry, replay matching and completion-first saved-world labels.

import copy

import pytest

from planners.laya_quad_cell import generate_quad_slot, quad_centers_from_plan, quad_reservation_reason
from planners.plan_validation import validate_build_plan
from training.laya_starter_kit import STARTER_ITEMS, verify_starter_kit
from training.laya_saved_counterfactual import assess, label_to_case, replay_fingerprint
from training.laya_saved_world import SavedWorldError
from tests.test_laya_saved_world import facts


def kit_facts():
    f = facts()
    f["networks"]["1"].update(items=dict(STARTER_ITEMS), construction_robots=50, logistic_robots=50)
    f["infrastructure_counts"] = {"substation": 4, "electric-energy-interface": 1, "roboport": 1, "storage-chest": 1}
    f["player_stock"] = {"main": {"assembling-machine-1": 1000}}
    f["mods"] = {"base": "2.1.20"}
    return f


def test_kit_checks_real_provider_stock_and_does_not_borrow_player_items():
    f = kit_facts()
    assert verify_starter_kit(f)["matches"]
    f["networks"]["1"]["items"]["assembling-machine-1"] = 5
    report = verify_starter_kit(f)
    assert not report["matches"]
    assert report["stock_differences"]["assembling-machine-1"] == {"expected": 6, "observed": 5}
    assert report["additional_assemblers_to_manufacture"] == 6
    assert report["extra_items_granted"] == 0


@pytest.mark.parametrize("side", ["west", "east", "north", "south"])
def test_quad_slot_is_one_machine_with_shared_geometry_and_required_arm_reach(side):
    spec = facts()["recipes"]["iron-gear-wheel"]
    actions = generate_quad_slot("iron-gear-wheel", spec, (10.5, 10.5), side)
    validate_build_plan({"phases": [{"name": "quad-slot", "actions": actions}]})
    assert sum(a["entity"] == "assembling-machine-1" for a in actions) == 1
    assert sum(a["entity"] == "requester-chest" for a in actions) == 1
    assert sum(a["entity"] == "passive-provider-chest" for a in actions) == 1
    arms = [a["entity"] for a in actions if "inserter" in a["entity"]]
    assert arms == (["long-handed-inserter"] * 2 if side in {"north", "south"} else ["inserter"] * 2)
    assert all(a["action_type"] == "place_ghost" for a in actions)
    if side in {"north", "south"}:
        sign = -1 if side == "north" else 1
        arms_by_y = {a["position"]["y"]: a for a in actions if a["entity"] == "long-handed-inserter"}
        input_arm = arms_by_y[10.5 + sign * 2]
        output_arm = arms_by_y[10.5 + sign * 3]
        # Factorio faces toward pickup, with two-tile long-arm reach.
        pickup_sign = -1 if input_arm["direction"] == "north" else 1
        assert input_arm["position"]["y"] + pickup_sign * 2 == 10.5
        output_pickup_sign = -1 if output_arm["direction"] == "north" else 1
        assert output_arm["position"]["y"] + output_pickup_sign * 2 == 10.5 + sign * 5
        assert output_arm["position"]["y"] - output_pickup_sign * 2 == 10.5 + sign


def test_fingerprint_normalizes_network_ids_but_rejects_tick_stock_or_machine_drift():
    f = kit_facts()
    changed = copy.deepcopy(f)
    changed["networks"]["different-id"] = changed["networks"].pop("1")
    changed["ports"][0]["network"] = "different-id"
    assert replay_fingerprint(f) == replay_fingerprint(changed)
    changed["tick"] += 1
    assert replay_fingerprint(f) != replay_fingerprint(changed)
    changed = copy.deepcopy(f)
    changed["networks"]["1"]["items"]["electric-furnace"] -= 1
    assert replay_fingerprint(f) != replay_fingerprint(changed)


def test_recipe_changes_reuse_only_the_selected_slots_request_section():
    def group(recipe, center, side):
        plan = generate_quad_slot(recipe, facts()["recipes"]["iron-gear-wheel"], center, side)
        chest = next(a for a in plan if a["entity"] == "requester-chest")
        return chest["logistic_sections"][0]["group"]
    assert group("iron-gear-wheel", (10.5, 10.5), "west") == group("transport-belt", (10.5, 10.5), "west")
    assert group("iron-gear-wheel", (10.5, 10.5), "west") != group("iron-gear-wheel", (10.5, 10.5), "north")
    assert group("iron-gear-wheel", (10.5, 10.5), "west") != group("iron-gear-wheel", (16.5, 15.5), "west")


@pytest.mark.parametrize("side", ["north", "south"])
def test_reserved_vertical_space_accepts_only_complete_long_arm_slots(side):
    center = {"x": 10.5, "y": 10.5}
    plan = {"phases": [{"actions": generate_quad_slot("iron-gear-wheel", facts()["recipes"]["iron-gear-wheel"], (10.5, 10.5), side)}]}
    assert quad_reservation_reason(plan, [center]) is None
    regular = copy.deepcopy(plan)
    for a in regular["phases"][0]["actions"]:
        if a["entity"] == "long-handed-inserter":
            a["entity"] = "inserter"
    assert quad_reservation_reason(regular, [center]) == "reserved_quad_expansion"
    no_arms = copy.deepcopy(plan)
    no_arms["phases"][0]["actions"] = [a for a in no_arms["phases"][0]["actions"] if a["entity"] != "long-handed-inserter"]
    assert quad_reservation_reason(no_arms, [center]) == "quad_vertical_requires_long_handed_inserters"
    intruder = {"phases": [{"actions": [{"entity": "substation", "position": {"x": 10, "y": 5 if side == "north" else 16}}]}]}
    assert quad_reservation_reason(intruder, [center]) == "reserved_quad_expansion"


def test_horizontal_seed_reserves_expansion_before_construction_finishes(tmp_path):
    import json
    from training.laya_saved_world import SavedWorldAdapter
    plan = {"phases": [{"actions": generate_quad_slot("iron-gear-wheel", facts()["recipes"]["iron-gear-wheel"], (10.5, 10.5), "west")} ]}
    center = {"x": 10.5, "y": 10.5}
    assert quad_centers_from_plan(plan) == [center]
    assert quad_reservation_reason(plan, [center]) is None
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    adapter.manifest = {"quad_centers": [{"x": -10.5, "y": -10.5}]}
    (tmp_path / "evidence").mkdir()
    receipt = {"status": "pending", "candidate": {"plan": plan}, "submission": {"ok": True}}
    path = tmp_path / "evidence/transaction-selected.json"
    path.write_text(json.dumps(receipt))
    assert adapter.quad_centers() == [{"x": -10.5, "y": -10.5}, center]
    receipt["submission"]["ok"] = False
    path.write_text(json.dumps(receipt))
    assert adapter.quad_centers() == [{"x": -10.5, "y": -10.5}]


def test_prefix_fingerprint_includes_reserved_expansion_space():
    f = kit_facts()
    changed = copy.deepcopy(f)
    changed["quad_centers"] = [{"x": 10.5, "y": 10.5}]
    assert replay_fingerprint(f) != replay_fingerprint(changed)


@pytest.mark.parametrize("field", ["recipes", "resources", "researched"])
def test_replay_fingerprint_rejects_changed_recipe_research_or_resource_facts(field):
    f = kit_facts()
    changed = copy.deepcopy(f)
    if field == "recipes":
        changed[field]["iron-gear-wheel"]["energy_ticks"] += 1
    elif field == "resources":
        changed[field] = [{"item": "iron-ore", "position": {"x": 1, "y": 1}, "remaining": 100}]
    else:
        changed[field] = {"steel-processing": True}
    assert replay_fingerprint(f) != replay_fingerprint(changed)


def label():
    f = kit_facts()
    fingerprint = replay_fingerprint(f)
    candidates = [{"option_id": "wait.300", "family": "wait", "summary": "Wait 5 seconds", "wait_ticks": 300},
                  {"option_id": "wait.1800", "family": "wait", "summary": "Wait 30 seconds", "wait_ticks": 1800}]
    return {"run_id": "cf-test", "ancestor_source_sha256": "1" * 64, "split": "train", "facts": f,
            "candidates": candidates, "start_fingerprint": fingerprint, "horizon_ticks": 7200,
            "goal": "iron-plate", "rate_per_second": .1, "sustain_ticks": 1200,
            "costs": {"wait.300": 1000, "wait.1800": 9000},
            "outcomes": {"wait.300": {"status": "timed_out", "failure_cause": "policy", "failure_kind": "timeout",
                                     "safety_violations": 0, "replay_fingerprint": fingerprint, "horizon_ticks": 7200},
                         "wait.1800": {"status": "completed", "failure_cause": "none", "failure_kind": "none",
                                      "safety_violations": 0, "replay_fingerprint": fingerprint, "horizon_ticks": 7200}}}


def test_cheap_failure_cannot_outrank_completion_and_all_failed_labels_stay_unresolved():
    l = label()
    case, reason = label_to_case(l)
    assert not reason
    assert case["gold"]["select_candidate"]["probabilities"] == {"wait.300": 0, "wait.1800": 1}
    assert case["split"] == "train"
    sibling = copy.deepcopy(l)
    sibling["run_id"] = "different-menu-seed"
    assert label_to_case(sibling)[0]["source"]["scenario_seed"] == case["source"]["scenario_seed"]
    l["outcomes"]["wait.1800"].update(status="timed_out", failure_cause="policy", failure_kind="timeout")
    assert label_to_case(l) == (None, "unresolved all-failed continuation")


@pytest.mark.parametrize("mismatch", ["fingerprint", "horizon", "tool"])
def test_mismatched_or_tool_failed_replays_never_become_preferences(mismatch):
    l = label()
    result = l["outcomes"]["wait.300"]
    if mismatch == "fingerprint":
        result["replay_fingerprint"] = "sha256:" + "2" * 64
    elif mismatch == "horizon":
        result["horizon_ticks"] = 7000
    else:
        result.update(failure_cause="tool", failure_kind="execution")
    assert label_to_case(l)[0] is None


def test_sustain_uses_every_common_engine_window_and_rejects_missing_samples():
    sample = {"window_ticks": 300, "production": {"iron-ore": {"per_second": .2}}, "pending_ghosts": 0}
    assert assess([sample] * 4, "iron-ore", .1, 4)["status"] == "completed"
    blocked = {**sample, "pending_ghosts": 1}
    assert assess([sample, sample, blocked, sample], "iron-ore", .1, 4)["status"] == "timed_out"
    with pytest.raises(SavedWorldError, match="incomplete"):
        assess([sample], "iron-ore", .1, 4)
