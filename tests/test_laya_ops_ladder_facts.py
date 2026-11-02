# Path: tests/test_laya_ops_ladder_facts.py | Purpose: Protect the C3->C4 fact bundle: chemical-ladder holds, the rung option they enable, and producer status for mall slots.

from training.laya_ops_runner import (WAIT_EXPIRY, compile_options, ladder_holds, named_prerequisites,
                                      producer_status)

RECIPES = {
    "pipe": {"ingredients": [{"name": "iron-plate", "amount": 1}], "enabled": True},
    "iron-gear-wheel": {"ingredients": [{"name": "iron-plate", "amount": 2}], "enabled": True},
    "copper-cable": {"ingredients": [{"name": "copper-plate", "amount": 1}], "enabled": True},
    "electronic-circuit": {"ingredients": [{"name": "iron-plate", "amount": 1},
                                           {"name": "copper-cable", "amount": 3}], "enabled": True},
    "transport-belt": {"ingredients": [{"name": "iron-gear-wheel", "amount": 1},
                                       {"name": "iron-plate", "amount": 1}], "enabled": True},
    "plastic-bar": {"ingredients": [{"name": "coal", "amount": 1},
                                    {"name": "petroleum-gas", "amount": 20}], "enabled": True},
}
STARTED = [{"ok": True, "operation": "starter", "params": {"recipe": plate}} for plate in ("iron-plate", "copper-plate")]
STOCK = {"iron-plate": 500, "copper-plate": 500}
# Shape from laya-c48 reference-s80730 decision at tick 352229: plastic waits at pipe, 100 required.
PARK = {"ok": False, "operation": "produce", "params": {"item": "plastic-bar", "stock_target": 100},
        "code": "chemical_capability_batch", "tick": 1000,
        "details": {"required_stock": 100, "rung": "pipe", "target": "plastic-bar"}}
BOARD = {"needs": [{"item": "plastic-bar", "condition": "unbuilt", "goal_required_per_s": 0.5}]}


def test_a_ladder_park_names_the_rung_as_a_prerequisite_of_the_target():
    demands = named_prerequisites([PARK])
    assert demands == {"pipe": {"target": 100, "capability": False, "parents": ["produce:plastic-bar"]}}


def test_a_ladder_park_holds_the_target_only_until_the_wait_horizon():
    assert ladder_holds([PARK], 1000)["plastic-bar"]["rung"] == "pipe"
    assert ladder_holds([PARK], 1000 + WAIT_EXPIRY - 1)["plastic-bar"]["rung"] == "pipe"
    assert ladder_holds([PARK], 1000 + WAIT_EXPIRY) == {}
    # A later outcome for the same target retires the hold, whatever it says.
    assert ladder_holds([PARK, {"ok": False, "operation": "produce", "params": {"item": "plastic-bar"},
                                "code": "material_shortage", "tick": 1001}], 1001) == {}


def test_the_menu_offers_the_rung_and_holds_the_parked_target():
    options = compile_options(BOARD, STARTED + [PARK], tick=1000, recipes=RECIPES, stock=STOCK)["options"]
    ids = {o["option_id"] for o in options}
    rung = next(o for o in options if o["option_id"] == "op.produce.pipe")
    assert rung["params"]["stock_target"] == 100
    assert rung["features"]["prerequisite_for"] == ["produce:plastic-bar"]
    assert rung["features"]["goal_need"] is True  # the rung serves the goal its parent serves
    assert "op.produce.plastic-bar" not in ids
    # The same decision after the horizon retries the target: the hold is not a permanent ban.
    later = {o["option_id"] for o in compile_options(BOARD, STARTED + [PARK], tick=1000 + WAIT_EXPIRY,
                                                    recipes=RECIPES, stock=STOCK)["options"]}
    assert "op.produce.plastic-bar" in later


def test_producer_status_reports_machines_and_status_buckets():
    board = {"needs": [{"item": "iron-gear-wheel", "condition": "short", "built": 2,
                        "machines": {"assembling-machine-1": 2},
                        "status_counts": {"working": 1, "item_ingredient_shortage": 1}}]}
    assert producer_status(board)["iron-gear-wheel"] == {
        "machines": 2, "built": 2, "statuses": {"working": 1, "starved": 1}, "condition": "short"}


def test_a_mall_slot_is_not_duplicated_for_a_recipe_a_standing_cell_already_makes():
    board = {"needs": [{"item": "iron-gear-wheel", "condition": "short", "goal_required_per_s": 0,
                        "machines": {"assembling-machine-1": 1}, "status_counts": {"working": 1}, "built": 1}]}
    covered = [{"recipe": "iron-gear-wheel", "loaned": False, "standing": True}]
    options = {o["option_id"]: o for o in compile_options(board, STARTED, recipes=RECIPES, stock=STOCK,
                                                          mall_cells=covered)["options"]}
    assert "op.mall_slot.iron-gear-wheel" not in options
    # A lent cell is not a standing producer: the slot is not a duplicate, and it carries the producer fact.
    lent = [{**covered[0], "loaned": True}]
    lent_options = {o["option_id"]: o for o in compile_options(board, STARTED, recipes=RECIPES, stock=STOCK,
                                                               mall_cells=lent)["options"]}
    assert lent_options["op.mall_slot.iron-gear-wheel"]["features"]["producer_exists"] is True
    # A borrow outcome means the slot was given away, so it stays legal even if the survey lags.
    borrow = {"ok": False, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"},
              "code": "temporary_mall_batch"}
    assert "op.mall_slot.iron-gear-wheel" in {
        o["option_id"] for o in compile_options(board, STARTED + [borrow], recipes=RECIPES, stock=STOCK,
                                                mall_cells=covered)["options"]}
