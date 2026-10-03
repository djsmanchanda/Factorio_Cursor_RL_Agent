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


def test_scarce_flags_bills_that_spend_most_of_what_is_left():
    from training.laya_guided_policy import option_features
    m = menu()
    m["options"][1]["bill"] = {"medium-electric-pole": 8}
    m["options"][2]["bill"] = {"medium-electric-pole": 2}
    s = state([])
    s["observation_digest"]["saved_world"] = {"provider_stock": {"1": {"medium-electric-pole": 10}}}
    f = option_features(s, m)
    assert f["mine.a"]["scarce"] and not f["smelt.b"]["scarce"] and f["mine.a"]["bill_items"] == 8


def test_context_keeps_rules_relevant_to_this_menu_when_too_many_apply(monkeypatch):
    import training.laya_clean_runner as runner
    monkeypatch.setattr(runner, "load_preferences", lambda: {"r.match": [{"when": {"need": "stock:*"}, "weight": 2}],
                                                             "r.nomatch": [{"when": {"need": "plan:*"}, "weight": 2}]})
    rules = [{"rule_id": "r.nomatch", "kind": "soft"}, {"rule_id": "r.text", "kind": "soft"},
             {"rule_id": "r.match", "kind": "soft"}, {"rule_id": "rule.hard", "kind": "hard"}]
    kept, omitted = runner.select_guidelines(rules, state([]), menu(), limit=3)
    assert [r["rule_id"] for r in kept] == ["rule.hard", "r.match", "r.text"] and omitted == ["r.nomatch"]


def test_phase_goal_items_outweigh_stock_chain_items():
    s = state(["mission.phase-goals-first"])
    s["observation_digest"]["factory_status"]["needs"] = [
        {"item": "copper-ore", "required": 0.5}, {"item": "requester-chest", "required": 0.01, "goal": 0.0}]
    rows = {r["option_id"]: r for r in GuidedPolicy(0, preferences=load_preferences()).distribution(s, menu())}
    assert rows["mine.a"]["features"]["goal_need"] and rows["mine.a"]["weight"] == 3.0
    assert not rows["asm.c"]["features"]["goal_need"]


def test_short_construction_items_on_the_board_get_the_construction_item_preference():
    s = state(["mission.make-construction-items"])
    s["observation_digest"]["factory_status"]["stock_targets"] = ["iron-plate"]
    rows = {r["option_id"]: r for r in GuidedPolicy(0, preferences=load_preferences()).distribution(s, menu())}
    assert rows["smelt.b"]["features"]["stock_target"] and rows["smelt.b"]["weight"] == 2.5


def test_non_goal_options_wait_while_a_goal_action_is_possible():
    s = state(["mission.phase-goals-first"])
    s["observation_digest"]["factory_status"]["needs"] = [
        {"item": "copper-ore", "required": 0.5}, {"item": "requester-chest", "required": 0.01, "goal": 0.0}]
    rows = {r["option_id"]: r for r in GuidedPolicy(0, preferences=load_preferences()).distribution(s, menu())}
    assert rows["asm.c"]["weight"] == pytest.approx(0.2) and rows["mine.a"]["weight"] == 3.0


def test_side_builds_that_would_leave_no_goal_build_affordable_are_flagged():
    from training.laya_guided_policy import option_features
    m = menu()
    m["options"][1]["bill"] = {"inserter": 2}       # goal build (copper-ore mine)
    m["options"][3]["bill"] = {"inserter": 3}       # stock cell
    s = state([])
    s["observation_digest"]["factory_status"]["needs"] = [{"item": "copper-ore", "required": 0.5},
                                                          {"item": "requester-chest", "required": 0.01, "goal": 0.0}]
    s["observation_digest"]["saved_world"] = {"provider_stock": {"1": {"inserter": 4}}}
    assert option_features(s, m)["asm.c"]["starves_goal"]          # 4 - 3 leaves 1 < 2
    s["observation_digest"]["saved_world"] = {"provider_stock": {"1": {"inserter": 6}}}
    assert not option_features(s, m)["asm.c"]["starves_goal"]      # 6 - 3 leaves 3 >= 2


def test_side_builds_dipping_below_the_unbuilt_starters_kit_are_flagged():
    from training.laya_guided_policy import option_features
    m = menu()
    m["goal_reserve"] = {"inserter": 2}
    m["options"][3]["bill"] = {"inserter": 3}       # stock cell (side build)
    s = state([])
    s["observation_digest"]["factory_status"]["needs"] = [{"item": "copper-ore", "required": 0.5},
                                                          {"item": "requester-chest", "required": 0.01, "goal": 0.0}]
    s["observation_digest"]["saved_world"] = {"provider_stock": {"1": {"inserter": 4}}}
    assert option_features(s, m)["asm.c"]["dips_goal_reserve"]       # 4 - 3 < 2
    s["observation_digest"]["saved_world"] = {"provider_stock": {"1": {"inserter": 5}}}
    assert not option_features(s, m)["asm.c"]["dips_goal_reserve"]


def test_requester_draws_count_as_spending():
    from training.laya_guided_policy import option_features
    m = menu()
    m["goal_reserve"] = {"inserter": 2}
    m["options"][3]["bill"] = {"assembling-machine-1": 1}
    m["options"][3]["draws"] = {"inserter": 10}     # a long-handed-inserter cell requests inserters
    s = state([])
    s["observation_digest"]["factory_status"]["needs"] = [{"item": "copper-ore", "required": 0.5},
                                                          {"item": "requester-chest", "required": 0.01, "goal": 0.0}]
    s["observation_digest"]["saved_world"] = {"provider_stock": {"1": {"inserter": 11, "assembling-machine-1": 5}}}
    features = option_features(s, m)["asm.c"]
    assert features["dips_goal_reserve"] and features["scarce"]
