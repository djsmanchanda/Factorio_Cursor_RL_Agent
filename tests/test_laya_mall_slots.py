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
