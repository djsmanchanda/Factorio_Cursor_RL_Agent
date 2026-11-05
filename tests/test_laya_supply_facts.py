# Path: tests/test_laya_supply_facts.py | Purpose: Protect the supply facts Laya sees: unit shortfalls, blocked alternatives, slot stocked threshold, stock-covered needs and consumption in the board.

from training.laya_clean_runner import _placement_bill, compile_menu
from training.laya_factory_status import _diagnose, compact_status, compile_status
from training.laya_mall_slots import slot_facts
from training.laya_mission import phase
from tests.test_laya_clean_runner import facts


def cell(product="electric-mining-drill", retargeted_tick=100, status="working"):
    return {"product": product, "status": status, "retargeted_tick": retargeted_tick}


def test_slot_with_five_drills_is_not_stocked_while_a_line_needs_six():
    stock = {"electric-mining-drill": 5}
    base = slot_facts(cell(), [cell()], stock, set(), tick=200)
    assert base["stocked"] is True and base["recent"] is False  # Today's behaviour: 5 >= 10 // 2.
    pending = slot_facts(cell(), [cell()], stock, set(), tick=200, pending_units={"electric-mining-drill": 6})
    assert pending["stocked"] is False and pending["recent"] is True  # Recent protection holds.
    covered = slot_facts(cell(), [cell()], {"electric-mining-drill": 6}, set(), tick=200,
                         pending_units={"electric-mining-drill": 6})
    assert covered["stocked"] is True
    # A need larger than the gate cannot make a gated slot unstockable.
    capped = slot_facts(cell(), [cell()], {"electric-mining-drill": 10}, set(), tick=200,
                        pending_units={"electric-mining-drill": 99})
    assert capped["stocked"] is True


def mixed_validator(blocked):
    """Every assemble candidate with an odd index is short of inserters; the rest are legal."""
    def validate(candidates):
        result = {}
        for i, c in enumerate(candidates):
            if c["family"] == "assemble" and i % 2:
                blocked.append(c)
                result[c["option_id"]] = {"reason": "item_shortfall:inserter"}
            else:
                result[c["option_id"]] = {"reason": None, "bill": {"assembling-machine-1": 1}}
        return result
    return validate


def test_unit_shortfalls_and_blocked_alternatives_for_a_need_with_options():
    world = facts()
    world["networks"]["1"]["items"]["inserter"] = 1
    blocked = []
    board = compile_status(world, phase("C1")["targets"])
    menu = compile_menu(world, board, mixed_validator(blocked), seed=1, per_need_checked=12)
    assert blocked, "the scenario must block some candidates"
    needed = max(_placement_bill(c).get("inserter", 0) for c in blocked)
    assert menu["shortfalls"]["inserter"] == len(blocked)  # Unchanged meaning: blocked candidates.
    assert menu["unit_shortfalls"]["inserter"] == max(1, needed - 1)  # Units: bill - stock.
    assert menu["pending_units"]["inserter"] == needed
    alternatives = menu["blocked_alternatives"]
    assert alternatives, "needs with legal options still show their blocked candidates"
    for need, reasons in alternatives.items():
        assert need not in menu["blocked"] and reasons.get("item_shortfall:inserter")
        assert any(n["need_id"] == need for n in menu["needs"])


def test_no_shortfall_keeps_the_new_keys_empty():
    world = facts()
    board = compile_status(world, phase("C1")["targets"])
    legal = lambda cs: {c["option_id"]: {"reason": None, "bill": {"assembling-machine-1": 1}} for c in cs}  # noqa: E731
    menu = compile_menu(world, board, legal, seed=1)
    assert menu["unit_shortfalls"] == {} and menu["pending_units"] == {}


def test_enough_stock_satisfies_a_need_with_no_machines():
    recipe = {"ingredients": [{"name": "iron-plate", "amount": 2}], "product_amount": 1, "energy_ticks": 30}
    assert _diagnose("iron-gear-wheel", recipe, {}, 0, 0.5, None, None, stock=331) == "satisfied"  # 331 >= 0.5 * 600
    assert _diagnose("iron-gear-wheel", recipe, {}, 0, 0.5, None, None, stock=10) == "unbuilt"
    assert _diagnose("iron-gear-wheel", None, {}, 0, 0.5, None, None, stock=331) == "recipe_unavailable"


def test_compact_status_carries_consumption():
    world = facts()
    measurement = {"window_ticks": 600, "production": {}, "consumption": {"iron-plate": {"per_second": 0.25}}}
    board = compile_status(world, phase("C1")["targets"], measurement)
    rows = {row["item"]: row for row in compact_status(board, limit=50)["needs"]}
    assert rows["iron-plate"]["consumed"] == 0.25
    assert all("consumed" in row for row in rows.values())
    unmeasured = {row["item"]: row for row in compact_status(compile_status(world, phase("C1")["targets"]))["needs"]}
    assert all(row["consumed"] is None for row in unmeasured.values())


def test_an_unfunded_external_build_becomes_stock_demand():
    """An optimizer block the network cannot fund counts as a blocked build: its short items get stock needs."""
    world = facts()
    world["networks"]["1"]["items"]["requester-chest"] = 1
    board = compile_status(world, phase("C1")["targets"])
    legal = lambda cs: {c["option_id"]: {"reason": None, "bill": {"assembling-machine-1": 1}} for c in cs}  # noqa: E731
    menu = compile_menu(world, board, legal, seed=1, external_shortfalls={"requester-chest": 2})
    assert menu["shortfalls"]["requester-chest"] == 1
    assert menu["unit_shortfalls"]["requester-chest"] == 2
    assert menu["pending_units"]["requester-chest"] == 3  # Stock 1 + 2 lacking.
