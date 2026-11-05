# Path: tests/test_laya_ops_c6_consumer.py | Purpose: Protect the C6 production-consumer option that c6-v2 requires.

from training.laya_ops_runner import compile_options

RECIPES = {
    "advanced-circuit": {"ingredients": [{"name": "iron-plate", "amount": 1}], "enabled": True},
    "productivity-module": {"ingredients": [{"name": "advanced-circuit", "amount": 1}], "enabled": True},
}
STARTED = [{"ok": True, "operation": "starter", "params": {"recipe": p}} for p in ("iron-plate", "copper-plate")]
BOARD = {"needs": [{"item": "advanced-circuit", "condition": "short", "goal_required_per_s": 0.25}]}


def test_c6_offers_a_production_consumer_for_the_advanced_circuit_goal():
    options = {o["option_id"]: o for o in compile_options(
        BOARD, STARTED, tick=0, recipes=RECIPES, stock={"advanced-circuit": 50})["options"]}
    consumer = options["op.produce.productivity-module"]
    assert consumer["features"]["goal_need"] is True
    assert consumer["features"]["prerequisite_for"] == ["produce:advanced-circuit"]
    assert consumer["params"]["finite"] is False


def test_consumer_option_is_not_offered_once_stocked():
    options = {o["option_id"] for o in compile_options(
        BOARD, STARTED, tick=0, recipes=RECIPES,
        stock={"advanced-circuit": 50, "productivity-module": 1})["options"]}
    assert "op.produce.productivity-module" not in options
