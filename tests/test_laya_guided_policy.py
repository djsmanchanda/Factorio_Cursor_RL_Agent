# Path: tests/test_laya_guided_policy.py | Purpose: Protect the doc-guided policy: only visible rules steer, weights multiply, exploration floor, full record.

import pytest

from training.laya_guided_policy import GuidedPolicy, load_preferences, matches


def menu():
    options = [
        {"option_id": "wait.300", "family": "wait", "need_id": "wait"},
        {"option_id": "mine.a", "family": "mine", "need_id": "item:copper-ore", "product": "copper-ore", "power": "m"},
        {"option_id": "smelt.b", "family": "smelt", "need_id": "item:iron-plate", "product": "iron-plate", "power": "e"},
        {"option_id": "asm.c", "family": "assemble", "need_id": "stock:requester-chest", "product": "requester-chest"},
    ]
    needs = [{"need_id": "wait", "options": ["wait.300"]}, {"need_id": "item:copper-ore", "condition": "short", "options": ["mine.a"]},
             {"need_id": "item:iron-plate", "condition": "satisfied", "options": ["smelt.b"]},
             {"need_id": "stock:requester-chest", "condition": "shortfall", "options": ["asm.c"]}]
    return {"options": options, "needs": needs}


def state(rules):
    return {"guidelines": [{"rule_id": r} for r in rules],
            "observation_digest": {"mission_phase": "C0", "factory_status": {
                "bottleneck": ["copper-plate:unbuilt <- copper-ore:short"]}}}


PREFS = {"serve": [{"when": {"in_bottleneck": True}, "weight": 2.0}, {"when": {"deepest_bottleneck": True}, "weight": 2.0}],
         "wait": [{"when": {"family": "wait", "others_available": True}, "weight": 0.25}],
         "stock": [{"when": {"need": "stock:*"}, "weight": 2.5}]}


def test_only_rules_laya_sees_steer_and_weights_multiply():
    rows = {r["option_id"]: r for r in GuidedPolicy(0, preferences=PREFS).distribution(state(["serve", "wait"]), menu())}
    assert rows["mine.a"]["weight"] == 4.0 and rows["mine.a"]["rules"] == ["serve", "serve"]  # in chain and deepest
    assert rows["wait.300"]["weight"] == 0.25 and rows["asm.c"]["weight"] == 1.0  # 'stock' rule not shown -> no effect
    assert sum(r["probability"] for r in rows.values()) == pytest.approx(1.0)
    assert min(r["probability"] for r in rows.values()) >= 0.15 / 4  # exploration floor
    assert rows["mine.a"]["features"]["deepest_bottleneck"] and not rows["smelt.b"]["features"]["in_bottleneck"]


def test_choice_carries_rules_and_full_distribution_and_is_seeded():
    a = GuidedPolicy(7, preferences=PREFS).choose(state(["serve", "wait", "stock"]), menu())
    b = GuidedPolicy(7, preferences=PREFS).choose(state(["serve", "wait", "stock"]), menu())
    assert a["option_id"] == b["option_id"] and len(a["distribution"]) == 4 and "rules" in a


def test_matcher_and_the_mission_catalog_preferences():
    assert matches({"need": "item:copper-*", "family": ["mine", "smelt"]}, {"need": "item:copper-ore", "family": "mine"})
    assert not matches({"others_available": True}, {"others_available": False})
    prefs = load_preferences()
    assert {"mission.serve-bottleneck", "mission.wait-rule", "mission.make-construction-items"} <= set(prefs)
