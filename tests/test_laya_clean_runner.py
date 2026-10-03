# Path: tests/test_laya_clean_runner.py | Purpose: Protect the clean Laya runner: need-tagged menus, journal-before-act, never-crash loop and mission data.

import copy
import json

from training.laya_clean_runner import ExplorationPolicy, build_state, compile_menu, need_of, run
from training.laya_factory_status import compile_status
from training.laya_mission import PHASES, current_phase, phase_reached


def recipe(ingredients, ticks, amount=1):
    return {"ingredients": [{"name": n, "amount": a} for n, a in ingredients], "product_amount": amount, "energy_ticks": ticks}


def facts():
    return {"tick": 100, "pending_ghosts": 0, "machines": [], "production": {}, "consumption": {},
            "ports": [{"position": {"x": 0, "y": 0}, "network": "1", "energy": 100,
                       "construction_radius": 55, "logistic_radius": 25}],
            "poles": [{"generated": True, "position": {"x": 2, "y": 2}, "wire": 18}],
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
    board = compile_status(facts(), PHASES[0]["targets"])
    menu = compile_menu(facts(), board, validator(shortfall_for={"iron-gear-wheel"}), seed=1)
    needs = {n["need_id"]: n for n in menu["needs"]}
    assert {"item:iron-ore", "item:iron-plate", "item:copper-cable", "coverage", "wait"} <= set(needs)
    assert "item:iron-gear-wheel" not in needs  # every gear site lacked a funded requester
    assert needs["stock:requester-chest"]["options"] and menu["shortfalls"] == {"requester-chest": menu["rejected"]["item_shortfall"]}
    assert all(len(n["options"]) <= 2 for n in menu["needs"])
    by_id = {o["option_id"]: o for o in menu["options"]}
    assert all(by_id[o]["need_id"] == n["need_id"] for n in menu["needs"] for o in n["options"])
    assert need_of({"family": "expand"}) == "coverage" and need_of({"family": "wait"}) is None


def test_state_carries_board_mission_guidelines_and_need_tags():
    board = compile_status(facts(), PHASES[0]["targets"])
    menu = compile_menu(facts(), board, validator(), seed=2)
    state = build_state(facts(), menu, board, PHASES[0], "test", ExplorationPolicy.model_ref, [], None)
    ids = {g["rule_id"] for g in state["guidelines"]}
    assert {"mission.starter-mall", "mission.bot-supply", "mission.belt-transfer"} <= ids
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
    pending = [r for r in rows if r.get("status") == "pending"]
    assert len(pending) == 4 and all(r["authority"] == "exploration" for r in pending)
    assert [r["event"] for r in rows].count("failure") == 1 and rows[-1]["event"] == "summary"


def test_loop_stops_safely_after_repeated_failures(tmp_path):
    summary = run(FakeAdapter(fail={1, 2, 3, 4}), ExplorationPolicy(0), journal=tmp_path / "j.jsonl",
                  decisions=10, measure_ticks=300, seed=0, run_id="t")
    assert summary["stop"] == "safe_stop_after_repeated_failures" and summary["failures"] == 3


def test_mission_ladder_advances_only_on_measured_satisfaction():
    assert current_phase([])["phase_id"] == "C1" and current_phase(["C1"])["phase_id"] == "C2"
    world = facts()
    assert not phase_reached(compile_status(world, {"iron-plate": 0.5}))
    measured = {"window_ticks": 600, "consumption": {}, "production": {"iron-plate": {"per_second": 0.5}}}
    assert phase_reached(compile_status(world, {"iron-plate": 0.5}, measured))


def test_coverage_hop_that_reaches_an_unmet_resource_serves_that_need():
    world = facts()
    world["resources"].append({"item": "copper-ore", "position": {"x": 50, "y": 0}})  # logistic reach 19 from (32,0)
    menu = compile_menu(world, compile_status(world, PHASES[0]["targets"]), validator(), seed=3)
    by_id = {o["option_id"]: o for o in menu["options"]}
    copper = next(n for n in menu["needs"] if n["need_id"] == "item:copper-ore")
    # The same hop, relayed over substations or medium poles.
    assert {by_id[o]["family"] for o in copper["options"]} == {"expand"}
    assert all("Extend bot coverage to (32,0)" in by_id[o]["summary"] for o in copper["options"])
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
    menu = compile_menu(world, compile_status(world, PHASES[0]["targets"]), validate, seed=4)
    assert "coverage" not in {n["need_id"] for n in menu["needs"]}
    assert menu["blocked"]["coverage"] == {"no_op": menu["rejected"]["no_op"]}
    assert set(menu["blocked"]["item:iron-gear-wheel"]) == {"item_shortfall:requester-chest"}
    assert menu["blocked"]["item:stone"] == {"no_candidate": 1}


def test_medium_pole_relay_variant_keeps_the_build_and_one_substation():
    import math
    from training.laya_clean_runner import pole_relay_variant
    from training.laya_saved_world import compile_candidates
    world = facts()
    world["resources"] = [{"item": "iron-ore", "position": {"x": 40.5, "y": 30.5}}]
    mine = next(c for c in compile_candidates(world) if c["family"] == "mine")
    variant = pole_relay_variant(mine, world)
    before, after = mine["plan"]["phases"][0]["actions"], variant["plan"]["phases"][0]["actions"]
    assert sum(a["entity"] == "substation" for a in before) >= 2
    assert sum(a["entity"] == "substation" for a in after) == 1
    assert [a for a in after if a["entity"] not in ("substation", "medium-electric-pole")] == \
           [a for a in before if a["entity"] != "substation"]
    points = [(2, 2)] + [(a["position"]["x"], a["position"]["y"]) for a in after
                         if a["entity"] in ("substation", "medium-electric-pole")]
    assert all(math.dist(a, b) <= 9 for a, b in zip(points, points[1:]))
    assert variant["option_id"] != mine["option_id"] and variant["option_id"].startswith("mine.p")


def test_pole_variant_that_overlaps_its_own_build_is_not_offered():
    # Live failure 2026-10-03: a relay hop landed on the plan's own roboport.
    from training.laya_clean_runner import pole_relay_variant
    from training.laya_saved_world import ghost
    world = facts()
    plan_actions = [ghost("substation", 12, -10), ghost("substation", 24, -21), ghost("substation", 35, -32),
                    ghost("roboport", 35, -33)]
    candidate = {"family": "expand", "option_id": "expand.x", "summary": "Extend", "network_anchor": {"x": 0, "y": 0},
                 "plan": {"surface": "nauvis", "force": "player", "atomic": True,
                          "phases": [{"name": "saved-world-expand", "actions": plan_actions}]}}
    variant = pole_relay_variant(candidate, world)
    if variant is not None:
        from planners.plan_validation import validate_build_plan
        validate_build_plan(variant["plan"])  # Offered variants never overlap their own build.


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
