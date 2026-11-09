# Path: tests/test_laya_chest_free_feed.py | Purpose: Protect the chest-free feed contract for forced-belt lines.

from orchestrator.stage_transport import _swap_infinity_chests


def _plan():
    return {"phases": [{"actions": [
        {"entity": "infinity-chest", "infinity_filter": "iron-plate", "position": {"x": 0, "y": 0}},
        {"entity": "infinity-chest", "infinity_filter": "copper-plate", "position": {"x": 1, "y": 0}},
    ]}]}


def test_a_forced_belt_feed_never_becomes_a_requester():
    plan = _plan()
    _swap_infinity_chests(plan, {"iron-plate": "belt", "copper-plate": "belt"})
    entities = [a["entity"] for a in plan["phases"][0]["actions"]]
    assert entities == ["steel-chest", "steel-chest"]
    assert not any("logistic_request" in a for a in plan["phases"][0]["actions"])


def test_a_logistic_feed_still_requests():
    plan = _plan()
    _swap_infinity_chests(plan, {"iron-plate": "logistic", "copper-plate": "belt"})
    assert plan["phases"][0]["actions"][0]["entity"] == "requester-chest"
    assert plan["phases"][0]["actions"][1]["entity"] == "steel-chest"


def test_the_pm_consumer_plan_bills_no_requester_chests():
    from orchestrator.material_reservations import plan_material_bill
    from orchestrator.stage_transport import _publish_output_chest
    from planners.local_layout_planner import LocalLayoutPlanner
    from planners.recipe_data import LINE_RECIPES

    plan = LocalLayoutPlanner().generate_line_layout(
        "productivity-module", 1, 0, 0, belt_type="transport-belt",
        inserter_type="inserter", feed_style="chest", terminal_collector=True,
    )
    _publish_output_chest(plan, limit=1)
    ingredients = LINE_RECIPES["productivity-module"]["ingredients"]
    _swap_infinity_chests(plan, {item: "belt" for item in ingredients})
    bill = plan_material_bill(plan)
    assert bill.get("requester-chest", 0) == 0
    assert bill.get("passive-provider-chest", 0) == 1
    assert bill.get("steel-chest", 0) >= 2
