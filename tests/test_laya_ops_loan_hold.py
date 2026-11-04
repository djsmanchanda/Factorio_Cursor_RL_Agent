# Path: tests/test_laya_ops_loan_hold.py | Purpose: Protect the loan-horizon hold: an option is suppressed only while its seed loan is active.

from training.laya_ops_runner import compile_options, loan_holds

RECIPES = {"assembling-machine-1": {"ingredients": [{"name": "iron-plate", "amount": 1}], "enabled": True}}
STARTED = [{"ok": True, "operation": "starter", "params": {"recipe": p}} for p in ("iron-plate", "copper-plate")]
STOCK = {"iron-plate": 500, "copper-plate": 500}
BOARD = {"needs": [{"item": "assembling-machine-1", "condition": "unbuilt", "goal_required_per_s": 0.5}]}
DEFER = {"ok": False, "operation": "produce", "params": {"item": "assembling-machine-1", "stock_target": 2},
         "code": "production_prerequisite_deferred",
         "message": "borrowed copper-cable cell is producing temporary assembling-machine-1 for the assembling-machine-1 seed"}
LOAN = {"target_item": "assembling-machine-1", "original_recipe": "copper-cable"}


def test_a_deferral_holds_the_item_only_while_its_seed_loan_is_active():
    assert loan_holds([DEFER], [LOAN])["assembling-machine-1"]
    assert loan_holds([DEFER], []) == {}
    assert loan_holds([DEFER], [{**LOAN, "target_item": "electronic-circuit"}]) == {}


def test_the_menu_drops_the_option_while_the_loan_runs_and_restores_it_after():
    held = {o["option_id"] for o in compile_options(BOARD, STARTED + [DEFER], tick=0, recipes=RECIPES,
                                                    stock=STOCK, loans=[LOAN])["options"]}
    assert "op.produce.assembling-machine-1" not in held
    released = {o["option_id"] for o in compile_options(BOARD, STARTED + [DEFER], tick=0, recipes=RECIPES,
                                                        stock=STOCK, loans=[])["options"]}
    assert "op.produce.assembling-machine-1" in released
