# Path: tests/test_laya_clean_runner.py | Purpose: Protect the clean Laya runner: need-tagged menus, journal-before-act, never-crash loop and mission data.

import copy
import json

from training.laya_clean_runner import ExplorationPolicy, build_state, compile_menu, need_of, run
from training.laya_factory_status import compile_status
from training.laya_mission import current_phase, phase, phase_reached


def recipe(ingredients, ticks, amount=1):
    return {"ingredients": [{"name": n, "amount": a} for n, a in ingredients], "product_amount": amount, "energy_ticks": ticks}


def facts():
    return {"tick": 100, "pending_ghosts": 0, "machines": [], "production": {}, "consumption": {},
            "ports": [{"position": {"x": 0, "y": 0}, "network": "1", "energy": 100,
                       "construction_radius": 55, "logistic_radius": 25}],
            "poles": [{"name": "substation", "generated": True, "position": {"x": 2, "y": 2}, "wire": 18, "supply": 9,
                       "grid": "1"}],
            "networks": {"1": {"items": {"electric-mining-drill": 5, "inserter": 20}, "reserved": {},
                               "construction_robots": 50}},
            "resources": [{"item": "iron-ore", "position": {"x": 20, "y": 20}}],
            "recipes": {"iron-plate": recipe([("iron-ore", 1)], 192), "copper-plate": recipe([("copper-ore", 1)], 192),
                        "iron-gear-wheel": recipe([("iron-plate", 2)], 30),
                        "copper-cable": recipe([("copper-plate", 1)], 30, 2),
                        "requester-chest": recipe([("iron-plate", 5)], 30)}}


def validator(shortfall_for=()):
    def validate(candidates):
        result = {}
        for c in candidates:
            needs_requester = any(a["entity"] == "requester-chest" for p in c["plan"]["phases"] for a in p["actions"])
            if needs_requester and c.get("product") in shortfall_for:
                result[c["option_id"]] = {"reason": "item_shortfall:requester-chest"}
            else:
                primary = {"expand": "roboport", "mine": "electric-mining-drill", "smelt": "electric-furnace",
                           "assemble": "assembling-machine-1"}[c["family"]]
                result[c["option_id"]] = {"reason": None, "bill": {primary: 1, "inserter": 1}}
        return result
    return validate


def test_menu_tags_unmet_needs_and_turns_item_shortfalls_into_stock_needs():
    board = compile_status(facts(), phase("C1")["targets"])
    menu = compile_menu(facts(), board, validator(shortfall_for={"iron-gear-wheel"}), seed=1)
    needs = {n["need_id"]: n for n in menu["needs"]}
    assert {"item:copper-cable", "coverage", "wait"} <= set(needs)
    # Iron comes from a direct starter, which needs a site plan; generic drill-to-chest and
    # requester-fed furnaces are not offered, so without a plan iron is honestly blocked.
    assert "item:iron-plate" not in needs and menu["blocked"]["item:iron-plate"] == {"no_candidate": 1}
    assert "item:iron-gear-wheel" not in needs  # every gear site lacked a funded requester
    assert needs["stock:requester-chest"]["options"] and menu["shortfalls"] == {"requester-chest": menu["rejected"]["item_shortfall"]}
    assert all(len(n["options"]) <= 2 for n in menu["needs"])
    by_id = {o["option_id"]: o for o in menu["options"]}
    assert all(by_id[o]["need_id"] == n["need_id"] for n in menu["needs"] for o in n["options"])
    assert need_of({"family": "expand"}) == "coverage" and need_of({"family": "wait"}) is None


def test_state_carries_board_mission_guidelines_and_need_tags():
    board = compile_status(facts(), phase("C1")["targets"])
    menu = compile_menu(facts(), board, validator(), seed=2)
    state = build_state(facts(), menu, board, phase("C1"), "test", ExplorationPolicy.model_ref, [], None)
    ids = {g["rule_id"] for g in state["guidelines"]}
    assert {"mission.starter-mall", "mission.serve-bottleneck", "mission.purposeful-coverage"} <= ids
    assert all(o["summary"].startswith("[") for o in state["options"])
    assert state["observation_digest"]["factory_status"]["needs"][0]["item"] == "copper-cable"


class FakeAdapter:
    def __init__(self, fail=()):
        self.world = facts()
        self.fail = set(fail)
        self.calls = 0
        self.executed = []

    def reconcile(self):
        pass

    def query(self, candidates=None):
        if candidates is not None:
            return {"validations": validator()(candidates)}
        return copy.deepcopy(self.world)

    def execute(self, selected, presented, measure_ticks):
        self.calls += 1
        if self.calls in self.fail:
            raise RuntimeError("ghost submission failed")
        assert any(o["option_id"] == selected for o in presented)
        self.executed.append(selected)
        self.world["tick"] += measure_ticks
        return {"status": "observed", "measurement": {"window_ticks": measure_ticks, "consumption": {},
                "production": {"iron-plate": {"per_second": 0.5}}}}


def test_loop_journals_before_acting_and_survives_isolated_failures(tmp_path):
    journal = tmp_path / "j.jsonl"
    adapter = FakeAdapter(fail={2})
    summary = run(adapter, ExplorationPolicy(0), journal=journal, decisions=4, measure_ticks=300, seed=0, run_id="t")
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    assert summary["decisions"] == 3 and summary["failures"] == 1 and summary["stop"] == "decision_budget"
    # Decision 0 plans the land before anything is built; no world mutation.
    assert rows[0]["status"] == "planned" and rows[0]["choice"]["need_id"] == "plan:site"
    assert (tmp_path / "site-plan.json").is_file() and adapter.calls == 3
    pending = [r for r in rows if r.get("status") == "pending"]
    assert len(pending) == 3 and all(r["authority"] == "exploration" for r in pending)
    assert [r["event"] for r in rows].count("failure") == 1 and rows[-1]["event"] == "summary"


def test_loop_stops_safely_after_repeated_failures(tmp_path):
    summary = run(FakeAdapter(fail={1, 2, 3, 4}), ExplorationPolicy(0), journal=tmp_path / "j.jsonl",
                  decisions=10, measure_ticks=300, seed=0, run_id="t")
    assert summary["stop"] == "safe_stop_after_repeated_failures" and summary["failures"] == 3


def test_mission_ladder_advances_only_on_measured_satisfaction():
    # Iron and copper starters come first (user priority), then the starter mall.
    assert current_phase([])["phase_id"] == "C0" and current_phase(["C0"])["phase_id"] == "C1"
    assert set(current_phase([])["targets"]) == {"iron-plate", "copper-plate"}
    world = facts()
    assert not phase_reached(compile_status(world, {"iron-plate": 0.5}))
    measured = {"window_ticks": 600, "consumption": {}, "production": {"iron-plate": {"per_second": 0.5}}}
    assert phase_reached(compile_status(world, {"iron-plate": 0.5}, measured))


def test_coverage_hop_that_reaches_an_unmet_resource_serves_that_need():
    world = facts()
    world["resources"].append({"item": "copper-ore", "position": {"x": 50, "y": 0}})  # logistic reach 19 from (32,0)
    menu = compile_menu(world, compile_status(world, phase("C1")["targets"]), validator(), seed=3)
    by_id = {o["option_id"]: o for o in menu["options"]}
    copper = next(n for n in menu["needs"] if n["need_id"] == "item:copper-ore")
    # The same hop, relayed over substations or medium poles.
    assert {by_id[o]["family"] for o in copper["options"]} == {"expand"}
    from training.laya_clean_runner import approaches, newly_reached
    assert all("copper-ore" in newly_reached(world, by_id[o]) | approaches(world, by_id[o], {"copper-ore"})
               for o in copper["options"])
    assert any(n["need_id"] == "coverage" for n in menu["needs"])


def test_already_built_options_are_pruned_and_blocked_needs_say_why():
    def validate(candidates):
        # Expansions are already built (empty bill); gear slots lack requesters.
        # Expansions only need new relay substations: their roboport already exists.
        return {c["option_id"]: {"reason": None, "bill": {"substation": 2}} if c["family"] == "expand" else
                ({"reason": "item_shortfall:requester-chest"} if c.get("product") == "iron-gear-wheel"
                 else {"reason": None, "bill": {"electric-furnace": 1, "electric-mining-drill": 1,
                                                "assembling-machine-1": 1}}) for c in candidates}
    world = facts()
    menu = compile_menu(world, compile_status(world, phase("C1")["targets"]), validate, seed=4)
    assert "coverage" not in {n["need_id"] for n in menu["needs"]}
    assert set(menu["blocked"]["coverage"]) == {"no_op"}
    assert set(menu["blocked"]["item:iron-gear-wheel"]) == {"item_shortfall:requester-chest"}
    assert menu["blocked"]["item:stone-brick"] == {"no_candidate": 1}


def test_live_world_waits_on_the_running_clock_without_pausing():
    from training.laya_clean_runner import LiveWorld

    class Bridge:
        def __init__(self):
            self.now, self.commands = 1000, []

        def command(self, text):
            self.commands.append(text)
            if "rcon.print(game.tick)" in text:
                self.now += 120  # The server keeps running between polls.
                return str(self.now)
            return "live"

    world = object.__new__(LiveWorld)
    world.bridge, world.speed = Bridge(), 1.0
    world.advance(300)
    assert world.bridge.now >= 1300
    assert not any("tick_paused=true" in c for c in world.bridge.commands)
    assert any("tick_paused=false" in c and "game.speed=1.0" in c for c in world.bridge.commands)


def test_live_world_observes_with_the_shared_helper_minus_only_the_pause_assertion():
    from pathlib import Path
    from training.laya_clean_runner import PAUSED_ASSERTION
    source = Path("training/laya_saved_world.lua").read_text()
    assert source.count(PAUSED_ASSERTION) == 1  # Live mode fails closed if this changes.
    from training.laya_clean_runner import COLLISION_RETURN, RECIPE_LINE, RECIPE_LIST_END
    assert source.count(RECIPE_LINE) == source.count(COLLISION_RETURN) == source.count(RECIPE_LIST_END) == 1


def test_loop_stops_as_stalled_when_only_waiting_without_progress(tmp_path):
    class WaitOnly(FakeAdapter):
        def query(self, candidates=None):
            if candidates is not None:  # Nothing is buildable: every check fails.
                return {"validations": {c["option_id"]: {"reason": "collision"} for c in candidates}}
            return copy.deepcopy(self.world)

    from training.laya_clean_runner import STALL_DECISIONS
    summary = run(WaitOnly(), ExplorationPolicy(0), journal=tmp_path / "j.jsonl", decisions=None,
                  measure_ticks=300, seed=0, run_id="t")
    # Plan, one first measurement (gap unknown -> known counts as progress), then the stalled waits.
    assert summary["stop"] == "stalled" and summary["decisions"] == STALL_DECISIONS + 2


def test_every_decision_is_recorded_for_fine_tuning(tmp_path):
    from training.laya_guided_policy import GuidedPolicy
    journal = tmp_path / "j.jsonl"
    run(FakeAdapter(fail={2}), GuidedPolicy(0), journal=journal, decisions=3, measure_ticks=300, seed=0, run_id="t")
    rows = [json.loads(l) for l in (tmp_path / "decision-states.jsonl").read_text().splitlines()]
    assert [r["outcome"]["status"] for r in rows] == ["planned", "observed", "failed"]
    built = rows[1]
    assert built["state"]["options"] and built["distribution"] and built["choice"]["option_id"]
    assert all({"family", "need_id", "plan_hash"} <= set(o) for o in built["options"])
    assert built["state"]["observation_digest"]["mission_phase"] == "C0" and built["recorded_at"]


def test_board_rates_use_a_rolling_window_not_one_bursty_sample():
    from training.laya_clean_runner import rolling_measurement
    samples = [(0, {"iron-plate": 0}, {}), (1800, {"iron-plate": 60}, {}), (3600, {"iron-plate": 60}, {}),
               (5400, {"iron-plate": 120}, {}), (7200, {"iron-plate": 120}, {}), (9000, {"iron-plate": 180}, {})]
    rolled = rolling_measurement(samples)
    assert rolled["window_ticks"] == 7200 and rolled["production"]["iron-plate"]["per_second"] == 1.0
    assert rolling_measurement(samples[:1]) is None
    assert rolling_measurement(samples[:2])["window_ticks"] == 1800  # Shorter history uses what exists.


def test_stock_items_are_sampled_from_the_mall_zone_only():
    from training.laya_site_plan import plan_options, reservation_reason
    world = facts()
    plan = plan_options(world)[0]["site_plan"]
    def validate(candidates):  # Gear builds lack requesters, so requester chests become a stock need.
        return {c["option_id"]: ({"reason": "item_shortfall:requester-chest"} if c.get("product") == "iron-gear-wheel"
                                 else {"reason": None, "bill": {"assembling-machine-1": 1, "electric-furnace": 1,
                                                                "electric-mining-drill": 1, "roboport": 1}})
                for c in candidates}
    menu = compile_menu(world, compile_status(world, phase("C1")["targets"]), validate, seed=5, site_plan=plan)
    by_id = {o["option_id"]: o for o in menu["options"]}
    stock = next(n for n in menu["needs"] if n["need_id"] == "stock:requester-chest")
    assert stock["options"] and all(reservation_reason(plan, by_id[o]) is None for o in stock["options"])


def test_coverage_that_serves_no_need_does_not_count_as_progress(tmp_path):
    class CoverageOnly(FakeAdapter):
        def query(self, candidates=None):
            if candidates is not None:  # Only roboport hops are buildable; nothing serves a need.
                return {"validations": {c["option_id"]: ({"reason": None, "bill": {"roboport": 1, "medium-electric-pole": 1}}
                                                         if c["family"] == "expand" else {"reason": "collision"})
                                        for c in candidates}}
            return copy.deepcopy(self.world)

    from training.laya_guided_policy import GuidedPolicy
    world_without_ore = CoverageOnly()
    world_without_ore.world["resources"] = []
    summary = run(world_without_ore, GuidedPolicy(1, epsilon=1.0), journal=tmp_path / "j.jsonl", decisions=60,
                  measure_ticks=300, seed=0, run_id="t")
    assert summary["stop"] == "stalled"


def test_records_mark_waits_with_no_legal_build_and_coverage_reach(tmp_path):
    from training.laya_guided_policy import GuidedPolicy

    class WaitOnly(FakeAdapter):
        def query(self, candidates=None):
            if candidates is not None:
                return {"validations": {c["option_id"]: {"reason": "collision"} for c in candidates}}
            return copy.deepcopy(self.world)

    run(WaitOnly(), GuidedPolicy(0), journal=tmp_path / "j.jsonl", decisions=3, measure_ticks=300, seed=0, run_id="t")
    rows = [json.loads(l) for l in (tmp_path / "decision-states.jsonl").read_text().splitlines()]
    assert [r.get("no_legal_build") for r in rows[1:]] == [True, True]
    world = facts()
    world["resources"].append({"item": "copper-ore", "position": {"x": 50, "y": 0}})
    menu = compile_menu(world, compile_status(world, phase("C1")["targets"]), validator(), seed=3)
    hops = [o for o in menu["options"] if o["family"] == "expand" and o["need_id"] == "item:copper-ore"]
    assert hops and all(set(h["coverage_reach"]) == {"reaches", "approaches"} for h in hops)


def test_menu_fits_the_state_limit_with_unique_options_and_journals_what_did_not_fit():
    from training.laya_clean_runner import MAX_MENU
    world = facts()
    world["recipes"].update({f"part-{i}": recipe([("iron-plate", 1)], 30) for i in range(25)})
    targets = {f"part-{i}": 0.1 for i in range(25)}
    menu = compile_menu(world, compile_status(world, targets), validator(), seed=6)
    ids = [o["option_id"] for o in menu["options"]]
    assert len(ids) <= MAX_MENU and len(ids) == len(set(ids)) and menu["omitted_needs"]
    assert all(len(n["options"]) == 1 for n in menu["needs"] if n["need_id"] != "wait")


def test_coverage_approaches_only_planned_ore_sites():
    from training.laya_clean_runner import _need_key, planned_sites
    facts = {"ports": [{"position": {"x": 0, "y": 0}, "energy": 1, "logistic_radius": 25, "construction_radius": 55}],
             "resources": [{"item": "copper-ore", "position": {"x": -60, "y": 0}},   # small patch west, unplanned
                           {"item": "copper-ore", "position": {"x": 80, "y": 0}}]}   # planned zone east
    plan = {"zones": [{"purpose": "ore:copper-ore", "rect": [70, -10, 90, 10]}]}
    hop = lambda x: {"family": "expand", "plan": {"phases": [{"actions": [{"entity": "roboport", "position": {"x": x, "y": 0}}]}]}}
    reach = planned_sites(facts, plan)
    assert _need_key(reach, hop(30), {"copper-ore"}) == "item:copper-ore"
    assert _need_key(reach, hop(-30), {"copper-ore"}) == "coverage"


def test_requested_items_count_sections_with_their_multiplier():
    from training.laya_clean_runner import requested_items
    chest = {"entity": "requester-chest", "logistic_sections": [
        {"group": "g", "requests": [{"name": "inserter", "count": 1}, {"name": "iron-plate", "count": 2}], "multiplier": 15}]}
    assert requested_items({"plan": {"phases": [{"actions": [chest]}]}}) == {"inserter": 15, "iron-plate": 30}


def test_a_need_never_presents_the_same_option_twice(monkeypatch):
    import training.laya_clean_runner as runner
    # Two builds that differ only in relay poles become one option once re-powered.
    monkeypatch.setattr(runner, "power_variants", lambda c, facts: [{**c, "option_id": "same." + c["family"]}] * 2)
    world = facts()
    menu = compile_menu(world, compile_status(world, phase("C1")["targets"]), validator(), seed=1)
    ids = [o["option_id"] for o in menu["options"]]
    assert len(ids) == len(set(ids))


def test_a_failed_atomic_submission_is_closed_and_its_footprint_avoided(tmp_path):
    import json
    from training.laya_clean_runner import LiveWorld, _submitted_entities
    (tmp_path / "evidence").mkdir()
    receipt = {"status": "pending", "candidate": {"family": "line"}, "submission": {
        "ok": False, "error": "atomic_footprint_blocked",
        "placement_failures": [{"entity": "electric-mining-drill", "position": {"x": 60.5, "y": 5.5}}]}}
    (tmp_path / "evidence" / "transaction-x.json").write_text(json.dumps(receipt))
    world = LiveWorld.__new__(LiveWorld)  # Only the evidence directory is needed.
    world.root = tmp_path
    world._close_failed_submissions()
    assert json.loads((tmp_path / "evidence" / "transaction-x.json").read_text())["status"] == "rejected"
    adapter = type("A", (), {"root": tmp_path})()
    assert {"entity": "electric-mining-drill", "position": {"x": 60.5, "y": 5.5}} in _submitted_entities(adapter)


def test_live_measurement_exports_mission_items_including_fluid_recipes(tmp_path, monkeypatch):
    import shutil, subprocess
    from pathlib import Path
    import pytest
    from training.laya_clean_runner import LiveWorld
    from training.laya_saved_world import SavedWorldAdapter
    lua=shutil.which('lua')
    if not lua:
        pytest.skip('Lua interpreter required for saved-world counter replay')
    source=Path('training/laya_saved_world.lua').read_text()
    monkeypatch.setattr(SavedWorldAdapter,'__init__',lambda self,root:setattr(self,'lua_source',source))
    world=LiveWorld(tmp_path,2)
    # Run the actual generated recipe/counter export block; a fluid recipe is
    # excluded from geometry recipes but its production must still be measured.
    block=world.lua_source.split('local recipe_names=',1)[1].split('local bounds=',1)[0]
    script = """local force={recipes={}}; local facts={recipes={},production={},consumption={}}
local counts={['plastic-bar']=20,['advanced-circuit']=9,['automation-science-pack']=7,['production-science-pack']=5}
local stats={get_input_count=function(name) return counts[name] or 0 end,get_output_count=function(name) return 0 end}
local recipe_names="""+block+"""
assert(facts.production['plastic-bar']==20,'missing plastic counter')
assert(facts.production['advanced-circuit']==9,'missing circuit counter')
assert(facts.production['automation-science-pack']==7,'missing science counter')
assert(facts.production['production-science-pack']==5,'missing production science counter')
"""
    result=subprocess.run([lua,'-'],input=script,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
