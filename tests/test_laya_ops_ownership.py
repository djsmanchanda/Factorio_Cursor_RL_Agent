# Path: tests/test_laya_ops_ownership.py | Purpose: Protect the ownership link from a material shortage to the blocker's produce option (C50 stall seed 80904).

from training.laya_ops_runner import compile_options

RECIPES = {
    "oil-refinery": {"ingredients": [{"name": "iron-plate", "amount": 1}], "enabled": True},
    "plastic-bar": {"ingredients": [{"name": "coal", "amount": 1}], "enabled": True},
    "transport-belt": {"ingredients": [{"name": "iron-plate", "amount": 1}], "enabled": True},
}
STARTED = [{"ok": True, "operation": "starter", "params": {"recipe": plate}}
           for plate in ("iron-plate", "copper-plate", "stone-brick")]
SLOT = {"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}}
STOCK = {"iron-plate": 500, "copper-plate": 500, "stone": 500}
BOARD = {"needs": [{"item": "plastic-bar", "condition": "unbuilt", "goal_required_per_s": 0.5}]}
# Shape from C50 facts seed 80904: plastic-bar's bill is short 2 oil-refineries.
SHORT = {"ok": False, "operation": "produce", "params": {"item": "plastic-bar", "stock_target": 100},
         "code": "material_shortage", "need_id": "stock:plastic-bar",
         "details": {"required": {"oil-refinery": 4}, "available": {"oil-refinery": 2}}}


def test_a_material_shortage_keeps_the_ownership_link_to_the_blocked_item():
    options = {o["option_id"]: o for o in compile_options(BOARD, STARTED + [SLOT, SHORT], tick=0,
                                                          recipes=RECIPES, stock=STOCK)["options"]}
    blocker = options["op.produce.oil-refinery"]
    assert blocker["features"]["prerequisite_for"] == ["produce:plastic-bar"]
    assert blocker["features"]["goal_need"] is True


def test_an_unrelated_shortage_keeps_an_empty_parent_list():
    board = {"needs": [{"item": "plastic-bar", "condition": "unbuilt", "goal_required_per_s": 0.5}]}
    # A foundation blocked on transport belts: the ownership fact names the real failing operation.
    short = {**SHORT, "operation": "foundation", "params": {"recipe": "iron-plate"},
             "details": {"required": {"transport-belt": 10}, "available": {"transport-belt": 4}}}
    options = {o["option_id"]: o for o in compile_options(board, STARTED + [SLOT, short], tick=0,
                                                          recipes=RECIPES, stock=STOCK)["options"]}
    assert options["op.produce.transport-belt"]["features"]["prerequisite_for"] == ["foundation:iron-plate"]
