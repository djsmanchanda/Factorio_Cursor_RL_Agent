# Path: tests/test_laya_mall_slots.py | Purpose: Protect gated, rotating mall slots: cells from receipts, configure-only retargets, phase-aware gates, menu and policy wiring.

import json
from pathlib import Path

import pytest

from training.laya_mall_slots import (craftable, execute_retarget, gate_updates, mall_cells, retarget_candidates)

RECIPES = {"iron-gear-wheel": {"ingredients": [{"name": "iron-plate", "amount": 2}], "product_amount": 1},
           "copper-cable": {"ingredients": [{"name": "copper-plate", "amount": 1}], "product_amount": 2},
           "electric-mining-drill": {"ingredients": [{"name": "electronic-circuit", "amount": 3},
                                                     {"name": "iron-gear-wheel", "amount": 5},
                                                     {"name": "iron-plate", "amount": 10}], "product_amount": 1},
           "iron-plate": {"ingredients": [{"name": "iron-ore", "amount": 1}], "product_amount": 1},
           "copper-plate": {"ingredients": [{"name": "copper-ore", "amount": 1}], "product_amount": 1},
           "electronic-circuit": {"ingredients": [{"name": "iron-plate", "amount": 1},
                                                  {"name": "copper-cable", "amount": 3}], "product_amount": 1},
           "plastic-bar": {"ingredients": [{"name": "petroleum-gas", "amount": 20}, {"name": "coal", "amount": 1}],
                           "product_amount": 2}}


def cell_receipt(root, name, x, recipe, status="observed"):
    plan = {"phases": [{"actions": [
        {"action_type": "place_ghost", "entity": "assembling-machine-1", "position": {"x": x, "y": 0.5}, "recipe": recipe},
        {"action_type": "place_ghost", "entity": "requester-chest", "position": {"x": x - 3, "y": 0.5},
         "logistic_sections": [{"group": f"laya-quad:{x}", "requests": [], "multiplier": 15}]}]}]}
    (root / "evidence").mkdir(exist_ok=True)
    (root / "evidence" / f"transaction-{name}.json").write_text(json.dumps(
        {"status": status, "candidate": {"family": "assemble", "product": recipe, "plan": plan, "network": "1",
                                         "network_anchor": {"x": 0, "y": 0}}}))


def world(*machines):
    return {"recipes": RECIPES, "machines": [{"name": "assembling-machine-1", "position": {"x": x, "y": 0.5},
                                              "recipe": r, "status": s} for x, r, s in machines]}


def test_cells_come_from_observed_assemble_receipts_with_live_recipes(tmp_path):
    cell_receipt(tmp_path, "a", 2.5, "iron-gear-wheel")
    cell_receipt(tmp_path, "b", 8.5, "copper-cable")
    cell_receipt(tmp_path, "c", 14.5, "iron-stick", status="construction_pending")
    cells = mall_cells(tmp_path, world((2.5, "electric-mining-drill", "working"), (8.5, "copper-cable", "item_ingredient_shortage")))
    assert [(c["product"], c["group"]) for c in cells] == [("electric-mining-drill", "laya-quad:2.5"), ("copper-cable", "laya-quad:8.5")]


def test_retargets_only_reconfigure_and_carry_what_a_rule_needs(tmp_path):
    cell_receipt(tmp_path, "a", 2.5, "iron-gear-wheel")
    cell_receipt(tmp_path, "b", 8.5, "iron-gear-wheel")
    cell_receipt(tmp_path, "c", 14.5, "copper-cable")
    facts = world((2.5, "iron-gear-wheel", "working"), (8.5, "iron-gear-wheel", "working"), (14.5, "copper-cable", "working"))
    cells = mall_cells(tmp_path, facts)
    options = retarget_candidates(facts, cells, {"electric-mining-drill", "iron-plate", "plastic-bar"},
                                  {"iron-gear-wheel": 300}, goal_items=set())
    assert {o["product"] for o in options} == {"electric-mining-drill"}  # smelting and fluid recipes are not slots
    for option in options:
        acts = option["plan"]["phases"][0]["actions"]
        assert {a["action_type"] for a in acts} == {"configure_entity"} and option["bill"] == {}
        requester, machine = acts  # Requests first, then the machine (a failed machine step keeps the old recipe).
        assert requester["entity"] == "requester-chest" and machine["entity"] == "assembling-machine-1"
        assert machine["logistic_condition"] == {"signal": "electric-mining-drill", "comparator": "<", "constant": 10}
        assert requester["logistic_sections"][0]["multiplier"] == 5 and option["draws"]["iron-plate"] == 50
    by_from = {(o["slot"]["from"], o["slot"]["permanent"]) for o in options}
    assert by_from == {("iron-gear-wheel", False), ("copper-cable", True)}  # two gear slots; the only cable slot
    assert all(o["slot"]["stocked"] for o in options if o["slot"]["from"] == "iron-gear-wheel")


def test_gates_follow_the_phase_and_are_applied_once(tmp_path):
    from training.laya_mall_slots import sync
    cell_receipt(tmp_path, "a", 2.5, "iron-gear-wheel")
    facts = world((2.5, "iron-gear-wheel", "working"))
    live, applied = FakeLive(tmp_path), {}
    c1 = {"needs": [{"item": "iron-gear-wheel", "goal_required_per_s": 0.25, "condition": "short"}]}
    sync(live, facts, c1, {}, applied)  # C1 measures gears: run free
    assert live.configured[-1][0]["clear_logistic_condition"] is True
    sync(live, facts, c1, {}, applied)
    assert len(live.configured) == 1  # Unchanged gates are not re-sent.
    later = {"needs": []}
    sync(live, facts, later, {}, applied)  # Later phases: stop at the stock target
    assert live.configured[-1][0]["logistic_condition"]["constant"] == 100
    sync(live, facts, later, {"iron-gear-wheel": 0.01}, applied)  # A shortage that blocks builds runs ungated
    assert live.configured[-1][0]["clear_logistic_condition"] is True
    failing, cache = FakeLive(tmp_path, ok=False), {}
    sync(failing, facts, later, {}, cache)
    assert cache == {}  # A failed apply is retried next decision.


def test_experimental_gate_and_request_sizes_preserve_defaults_and_refresh_cache(tmp_path):
    from training.laya_mall_slots import sync
    cell_receipt(tmp_path,'a',2.5,'iron-gear-wheel')
    facts=world((2.5,'iron-gear-wheel','working'));live=FakeLive(tmp_path);cache={}
    board={'needs':[{'item':'electric-mining-drill','goal_required_per_s':0,'condition':'unbuilt'}]}
    base=sync(live,facts,board,{},cache)
    assert live.configured[-1][0]['logistic_condition']['constant']==100
    assert base[0]['draws']['iron-plate']==50
    small=sync(live,facts,board,{},cache,gate_scale=.5,buildable_multiplier=1)
    assert live.configured[-1][0]['logistic_condition']['constant']==50
    assert small[0]['draws']['iron-plate']==10 and small[0]['option_id']!=base[0]['option_id']
    n=len(live.configured);sync(live,facts,board,{},cache,gate_scale=.5,buildable_multiplier=1)
    assert len(live.configured)==n


def test_craftable_excludes_furnace_and_fluid_recipes():
    assert craftable(RECIPES, "electric-mining-drill") and not craftable(RECIPES, "iron-plate")
    assert not craftable(RECIPES, "plastic-bar") and not craftable(RECIPES, "nonexistent")


class FakeLive:
    def __init__(self, root, ok=True):
        self.root, self.ok, self.configured, self.tick = Path(root), ok, [], 10

    def query(self):
        return {"tick": self.tick, "production": {}, "consumption": {}, "machines": [], "pending_ghosts": 0}

    def configure(self, actions):
        self.configured.append(actions)
        return {"ok": self.ok, "failed_entities": 0 if self.ok else 1}

    def advance(self, ticks):
        self.tick += ticks


def test_execute_retarget_configures_measures_and_never_leaves_pending(tmp_path):
    from training.laya_saved_world import SavedWorldError
    (tmp_path / "evidence").mkdir()
    cell_receipt(tmp_path, "a", 2.5, "iron-gear-wheel")
    facts = world((2.5, "iron-gear-wheel", "working"))
    [option] = retarget_candidates(facts, mall_cells(tmp_path, facts), {"electric-mining-drill"}, {}, set())
    receipt = execute_retarget(FakeLive(tmp_path), option, 600)
    assert receipt["status"] == "observed" and "measurement" in receipt
    with pytest.raises(SavedWorldError):
        execute_retarget(FakeLive(tmp_path, ok=False), option, 600)
    statuses = {json.loads(p.read_text()).get("status") for p in (tmp_path / "evidence").glob("transaction-*.json")}
    assert statuses <= {"observed", "rejected"}
    with pytest.raises(SavedWorldError):
        execute_retarget(FakeLive(tmp_path), {**option, "plan": {"phases": [{"actions": [{"action_type": "place_ghost"}]}]}}, 600)


def test_retargets_join_the_menu_under_the_need_they_serve():
    from tests.test_laya_clean_runner import facts as runner_facts, validator
    from training.laya_clean_runner import compile_menu
    from training.laya_factory_status import compile_status
    from training.laya_mission import phase
    world_ = runner_facts()
    retarget = {"option_id": "retarget.x", "family": "retarget", "product": "iron-gear-wheel", "summary": "r",
                "plan": {"phases": [{"actions": []}]}, "bill": {}, "draws": {},
                "slot": {"from": "copper-cable", "idle": True, "stocked": False, "permanent": False, "from_goal": False}}
    menu = compile_menu(world_, compile_status(world_, phase("C1")["targets"]), validator(), seed=3, retargets=[retarget])
    legal = {o["option_id"]: o for o in menu["options"]}
    gear = next(n for n in menu["needs"] if n["need_id"] == "item:iron-gear-wheel")
    assert "retarget.x" in gear["options"] or len(gear["options"]) == 2  # sampled with that need's builds
    if "retarget.x" in legal:
        assert legal["retarget.x"]["need_id"] == "item:iron-gear-wheel"


def test_slot_features_for_the_rotating_mall_rule():
    from training.laya_guided_policy import option_features
    menu = {"needs": [], "options": [{"option_id": "r", "family": "retarget", "need_id": "stock:x", "bill": {},
                                      "slot": {"from": "copper-cable", "idle": False, "stocked": True,
                                               "permanent": True, "from_goal": False}},
                                     {"option_id": "a", "family": "assemble", "need_id": "stock:x", "bill": {}}]}
    state = {"observation_digest": {"factory_status": {"needs": []}}}
    f = option_features(state, menu)
    assert f["r"]["slot_free"] and f["r"]["slot_permanent"] and not f["r"]["slot_from_goal"]
    assert not f["a"]["slot_free"] and not f["a"]["slot_permanent"]


def test_a_slot_waiting_for_new_ingredients_is_busy_not_idle(tmp_path):
    cell_receipt(tmp_path, "a", 2.5, "iron-gear-wheel")
    plan = {"phases": [{"actions": [{"action_type": "configure_entity", "entity": "assembling-machine-1",
                                      "position": {"x": 2.5, "y": 0.5}}]}]}
    (tmp_path / "evidence" / "transaction-r.json").write_text(json.dumps(
        {"status": "observed", "before": {"tick": 1000}, "candidate": {"family": "retarget", "plan": plan}}))
    facts = {**world((2.5, "electric-mining-drill", "item_ingredient_shortage")), "tick": 3000}
    [option] = retarget_candidates(facts, mall_cells(tmp_path, facts), {"iron-gear-wheel"}, {}, set())
    assert not option["slot"]["idle"] and option["slot"]["recent"]
    later = {**world((2.5, "electric-mining-drill", "disabled_by_control_behavior")), "tick": 20000}
    [option] = retarget_candidates(later, mall_cells(tmp_path, later), {"iron-gear-wheel"}, {}, set())
    assert option["slot"]["idle"] and not option["slot"]["recent"]


def test_coverage_toward_a_line_site_serves_the_plate():
    from training.laya_clean_runner import _need_key, planned_sites
    plan = {"zones": [{"purpose": "smelt:iron-plate", "rect": [16, 30, 60, 64]}]}
    facts = {"ports": [{"position": {"x": 3, "y": -1}, "energy": 1, "logistic_radius": 25, "construction_radius": 55}],
             "resources": []}
    hop = lambda x, y: {"family": "expand", "plan": {"phases": [{"actions": [{"entity": "roboport", "position": {"x": x, "y": y}}]}]}}
    reach = planned_sites(facts, plan, {"iron-plate"})
    assert _need_key(reach, hop(30, 40), {"iron-plate"}) == "item:iron-plate"
    assert _need_key(planned_sites(facts, plan), hop(30, 40), {"iron-plate"}) == "coverage"  # no starter yet


def test_no_retarget_to_an_item_whose_inputs_nothing_can_make(tmp_path):
    from training.laya_mall_slots import obtainable
    recipes = {**RECIPES, "copper-plate": {"ingredients": [{"name": "copper-ore", "amount": 1}]},
               "passive-provider-chest": {"ingredients": [{"name": "electronic-circuit", "amount": 3},
                                                                     {"name": "advanced-circuit", "amount": 1}]},
               "electronic-circuit": {"ingredients": [{"name": "iron-plate", "amount": 1},
                                                      {"name": "copper-cable", "amount": 3}]}}
    assert obtainable(recipes, "electronic-circuit", {}) and not obtainable(recipes, "passive-provider-chest", {})
    assert obtainable(recipes, "passive-provider-chest", {"advanced-circuit": 2})
    cell_receipt(tmp_path, "a", 2.5, "iron-gear-wheel")
    facts = {**world((2.5, "iron-gear-wheel", "working")), "recipes": recipes}
    assert retarget_candidates(facts, mall_cells(tmp_path, facts), {"passive-provider-chest"}, {}, set()) == []


def need(item, condition, depth, gap=None, required=0.5, short=()):
    return {"item": item, "condition": condition, "depth": depth, "gap_per_s": gap, "required_per_s": required,
            "goal_required_per_s": 0.0, "short_inputs": list(short)}


def manager_board():
    # Drills (goal) are starved of circuits, circuits of cable: the chain's deepest link comes first.
    return {"targets": {"electric-mining-drill": 0.1}, "needs": [
        need("electric-mining-drill", "unbuilt", 0, short=["electronic-circuit"]),
        need("electronic-circuit", "unbuilt", 1, short=["copper-cable"]),
        need("copper-cable", "unbuilt", 2),
        need("iron-gear-wheel", "satisfied", 1)]}


def test_demand_order_puts_precursors_first_and_drops_what_cannot_be_made_now():
    from training.laya_mall_slots import demand_order
    have = {"iron-plate", "copper-plate"}
    order = demand_order(manager_board(), {"plastic-bar": 0.01}, RECIPES, have)
    assert order == ["copper-cable", "electronic-circuit", "electric-mining-drill"]  # plastic-bar takes fluids


def test_free_cells_go_to_urgent_items_working_board_cells_and_recent_switches_stay():
    from training.laya_mall_slots import assign
    def cell(x, product, status, side="horizontal", since=None):
        return {"entity": "assembling-machine-1", "position": {"x": x, "y": 0.5},
                "requester": {"x": x - 3, "y": 0.5 if side == "horizontal" else 5.5},
                "product": product, "status": status, "retargeted_tick": since}
    cells = [cell(1, "iron-gear-wheel", "working"),                      # meets a board need: kept
             cell(2, "iron-stick", "disabled_by_control_behavior"),      # gated (stocked): free
             cell(3, "passive-provider-chest", "item_ingredient_shortage", "vertical"),  # impossible now: free
             cell(4, "splitter", "working", since=9_000),                # off-board but switched recently: kept
             cell(5, None, "no_recipe", "vertical")]
    recipes = {**RECIPES, "passive-provider-chest": {"ingredients": [{"name": "advanced-circuit", "amount": 1}],
                                                     "product_amount": 1}}
    order = ["copper-cable", "electronic-circuit", "electric-mining-drill"]
    moves = assign(cells, order, manager_board(), recipes, {"iron-plate", "copper-plate"}, tick=10_000)
    assert [(c["position"]["x"], item) for c, item in moves] == [(2, "copper-cable"), (3, "electronic-circuit"),
                                                                 (5, "electric-mining-drill")]


def test_manage_switches_in_one_configure_and_leaves_receipts_slot_ages_read(tmp_path):
    from training.laya_mall_slots import manage
    cell_receipt(tmp_path, "a", 3.5, "iron-stick")
    calls = []
    adapter = type("A", (), {"root": tmp_path, "configure": lambda self, acts: calls.append(acts) or {"ok": True}})()
    facts = {"tick": 500, "recipes": RECIPES, "networks": {"1": {"items": {"iron-plate": 50, "copper-plate": 50}}},
             "machines": [{"name": "assembling-machine-1", "position": {"x": 3.5, "y": 0.5}, "recipe": "iron-stick",
                           "status": "disabled_by_control_behavior"}]}
    made = manage(adapter, facts, manager_board(), {}, {})
    assert [m["to"] for m in made] == ["copper-cable"] and len(calls) == 1
    [cell] = mall_cells(tmp_path, {**facts, "machines": [{**facts["machines"][0], "recipe": "copper-cable", "status": "working"}]})
    assert cell["product"] == "copper-cable" and cell["retargeted_tick"] == 500


def test_build_blocking_shortfall_takes_the_stocked_goal_cell_precursor_first():
    """eval-fixes-20261108: every seed's only cell stayed on goal copper cable (4,262 stocked) while builds
    were short of medium poles, so nothing could be built - not even a cell to make poles."""
    from training.laya_mall_slots import assign, demand_order
    recipes = {**RECIPES, "iron-stick": {"ingredients": [{"name": "iron-plate", "amount": 1}], "product_amount": 2},
               "medium-electric-pole": {"ingredients": [{"name": "iron-stick", "amount": 4},
                                                        {"name": "copper-plate", "amount": 2}], "product_amount": 1}}
    board = {"targets": {"copper-cable": 0.56}, "needs": [need("copper-cable", "satisfied", 0, required=0.56)]}
    stock = {"copper-cable": 4262, "iron-plate": 2354, "copper-plate": 900}
    have = set(stock)
    order = demand_order(board, {"medium-electric-pole": 0.01}, recipes, have, stock)
    assert order[:2] == ["iron-stick", "medium-electric-pole"]
    cable = {"entity": "assembling-machine-1", "position": {"x": -9.5, "y": -20.5}, "requester": {"x": -12.5, "y": -20.5},
             "product": "copper-cable", "status": "item_ingredient_shortage", "retargeted_tick": None}
    assert [(c["product"], item) for c, item in assign([cable], order, board, recipes, have, 10_000, stock=stock)] \
        == [("copper-cable", "iron-stick")]
    # Unstocked, the goal cell keeps its job.
    assert assign([cable], order, board, recipes, have, 10_000, stock={**stock, "copper-cable": 10}) == []


def test_an_input_starved_item_gets_no_extra_cells():
    """eval-fixes-20261109 s1: all three cells went to iron-starved gears (722 stocked), leaving cable unbuilt."""
    from training.laya_mall_slots import assign
    recipes = {name: {**r, "energy_ticks": 30} for name, r in RECIPES.items()}
    board = {"targets": {}, "needs": [need("iron-gear-wheel", "input_starved", 0, gap=0.25, short=["iron-plate"])]}
    cells = [{"entity": "assembling-machine-1", "position": {"x": x, "y": 0.5}, "requester": {"x": x - 3, "y": 0.5},
              "product": None, "status": "no_recipe", "retargeted_tick": None} for x in (1, 2, 3)]
    moves = assign(cells, ["iron-gear-wheel"], board, recipes, {"iron-plate"}, 10_000)
    assert [item for _, item in moves] == ["iron-gear-wheel"]


def test_a_stocked_bottleneck_item_releases_its_cell_to_a_build_shortfall():
    """eval-fixes-20261109b s4: the only cell stayed on iron-starved gears (468 stocked) while every build lacked
    long-handed inserters."""
    from training.laya_mall_slots import assign, demand_order
    recipes = {**RECIPES, "long-handed-inserter": {"ingredients": [{"name": "iron-gear-wheel", "amount": 1},
                                                                   {"name": "iron-plate", "amount": 1}], "product_amount": 1}}
    board = {"targets": {"iron-gear-wheel": 0.27},
             "needs": [need("iron-gear-wheel", "input_starved", 0, gap=0.12, required=0.27, short=["iron-plate"]),
                       need("iron-plate", "under_capacity", 1, gap=0.2, required=0.6)]}
    stock = {"iron-gear-wheel": 468, "iron-plate": 3}
    order = demand_order(board, {"long-handed-inserter": 0.01}, recipes, set(stock), stock)
    gear = {"entity": "assembling-machine-1", "position": {"x": -16.5, "y": -2.5}, "requester": {"x": -19.5, "y": -2.5},
            "product": "iron-gear-wheel", "status": "item_ingredient_shortage", "retargeted_tick": None}
    moves = assign([gear], order, board, recipes, set(stock), 10_000, stock=stock, shortfalls={"long-handed-inserter"})
    assert [item for _, item in moves] == ["long-handed-inserter"]


def test_an_unserved_precursor_preempts_the_lowest_ranked_demanded_cell():
    """eval-fixes-20261109e s0: circuit and inserter cells waited for cable; cable had no cell and none was free."""
    from training.laya_mall_slots import assign
    def cell(x, product):
        return {"entity": "assembling-machine-1", "position": {"x": x, "y": 0.5}, "requester": {"x": x - 3, "y": 0.5},
                "product": product, "status": "item_ingredient_shortage", "retargeted_tick": None}
    board = {"targets": {}, "needs": [need("inserter", "input_starved", 0, short=["electronic-circuit"]),
                                      need("electronic-circuit", "input_starved", 1, short=["copper-cable"]),
                                      need("copper-cable", "unbuilt", 2)]}
    order = ["copper-cable", "electronic-circuit", "inserter"]
    recipes = {**RECIPES, "inserter": {"ingredients": [{"name": "electronic-circuit", "amount": 1},
                                                       {"name": "iron-plate", "amount": 1}], "product_amount": 1}}
    have = {"copper-plate", "iron-plate"}
    moves = assign([cell(1, "electronic-circuit"), cell(2, "inserter")], order, board, recipes, have, 10_000)
    assert [(c["product"], item) for c, item in moves] == [("inserter", "copper-cable")]
    # A recently switched cell is not taken; the next lowest-ranked settled one is, and none when all are recent.
    fresh = [cell(1, "electronic-circuit"), {**cell(2, "inserter"), "retargeted_tick": 9_000}]
    assert [(c["product"], item) for c, item in assign(fresh, order, board, recipes, have, 10_000)] \
        == [("electronic-circuit", "copper-cable")]
    both = [{**c, "retargeted_tick": 9_000} for c in fresh]
    assert assign(both, order, board, recipes, have, 10_000) == []


def test_pending_pipe_bill_reclaims_underground_slot_and_lifts_gate(tmp_path):
    from training.laya_mall_slots import manage
    cell_receipt(tmp_path, "pipes", 3.5, "pipe-to-ground")
    recipes = {**RECIPES,
               "pipe": {"ingredients": [{"name": "iron-plate", "amount": 1}], "product_amount": 1},
               "pipe-to-ground": {"ingredients": [{"name": "pipe", "amount": 10},
                                                   {"name": "iron-plate", "amount": 5}], "product_amount": 2}}
    facts = {"tick": 20000, "recipes": recipes,
             "networks": {"1": {"items": {"iron-plate": 500, "pipe-to-ground": 20}, "reserved": {"pipe": 81}}},
             "machines": [{"name": "assembling-machine-1", "position": {"x": 3.5, "y": 0.5},
                           "recipe": "pipe-to-ground", "status": "disabled_by_control_behavior"}]}
    calls = []
    adapter = type("A", (), {"root": tmp_path, "configure": lambda self, acts: calls.append(acts) or {"ok": True}})()
    board = {"targets": {}, "needs": [], "construction": {"shortfall": {"pipe": 81}}}
    made = manage(adapter, facts, board, {}, {})
    assert [m["to"] for m in made] == ["pipe"]
    machine = next(a for a in calls[0] if a["entity"] == "assembling-machine-1")
    assert machine["recipe"] == "pipe" and machine["clear_logistic_condition"]
    # Once the bill is funded, normal stock gating returns.
    calls.clear()
    facts["machines"][0].update(recipe="pipe", status="working")
    facts["networks"]["1"]["items"]["pipe"] = 81
    manage(adapter, facts, {"targets": {}, "needs": [], "construction": {"shortfall": {}}}, {}, {})
    assert any(a.get("logistic_condition", {}).get("signal") == "pipe" for a in calls[0])


def test_overdue_request_automatically_preempts_recent_lower_priority_slot(tmp_path):
    from training.laya_mall_slots import manage
    cell_receipt(tmp_path, "a", 2.5, "electronic-circuit")
    facts = world((2.5, "electronic-circuit", "working"))
    facts.update(tick=100, networks={"1": {"items": {"iron-plate": 100, "copper-plate": 100,
                                                       "copper-cable": 100}, "reserved": {}}},
                 pending_requests=[{"request_id": "chest:pipe", "item": "pipe", "missing": 81, "network": "1"}])
    facts["recipes"] = {**RECIPES, "pipe": {"ingredients": [{"name": "iron-plate", "amount": 1}],
                                              "product_amount": 1, "energy_ticks": 30}}
    # This machine was switched only 100 ticks ago; ordinary batch settling must not hide overdue work.
    (tmp_path / "evidence" / "transaction-z.json").write_text(json.dumps({
        "status": "applied", "before_tick": 100, "candidate": {"family": "retarget", "plan": {"phases": [
            {"actions": [{"entity": "assembling-machine-1", "position": {"x": 2.5, "y": .5}}]}]}}}))
    board = {"targets": {}, "needs": [need("electronic-circuit", "under_capacity", 0, required=.1)]}
    live, applied = FakeLive(tmp_path), {}
    assert manage(live, facts, board, {}, applied) == []
    facts["tick"] = 1300
    moves = manage(live, facts, board, {}, applied)
    assert [m["to"] for m in moves] == ["pipe"]
    assert live.configured[-1][-1]["clear_logistic_condition"] is True
    facts["machines"][0]["recipe"] = "pipe"
    facts["tick"] += 100
    assert manage(live, facts, board, {}, applied) == []  # The producer is kept, without duplication.


def test_overdue_work_preserves_missing_precursor_and_rejects_nonassembler_work():
    from training.laya_mall_slots import demand_order
    recipes = {**RECIPES,
               "pipe": {"ingredients": [{"name": "iron-plate", "amount": 1}], "product_amount": 1},
               "pipe-to-ground": {"ingredients": [{"name": "pipe", "amount": 10}], "product_amount": 2}}
    assert demand_order({"needs": [], "targets": {}}, {"pipe-to-ground": 2, "iron-plate": 81, "plastic-bar": 10},
                        recipes, {"iron-plate"}, {"iron-plate": 100}, ["pipe-to-ground"]) == ["pipe", "pipe-to-ground"]
