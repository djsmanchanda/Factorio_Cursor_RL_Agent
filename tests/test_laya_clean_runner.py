# Path: tests/test_laya_clean_runner.py | Purpose: Protect the clean Laya runner: need-tagged menus, journal-before-act, never-crash loop and mission data.

import copy
import json

import pytest

from orchestrator.laya_operations import sha256_hash
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


def test_loop_records_blocked_external_candidate_without_changing_its_geometry(tmp_path):
    from training.laya_saved_world import OPTIMIZER_REQUIRED_CHECKS
    adapter = FakeAdapter()
    candidate = {"option_id": "optimizer.test", "family": "optimizer-block", "product": "iron-plate",
                 "plan": {"surface": "nauvis", "force": "player", "atomic": True, "phases": [
                     {"name": "one", "actions": [{"action_type": "place_ghost", "entity": "pipe",
                                                  "position": {"x": 0.5, "y": 0.5}}]},
                     {"name": "two", "actions": [{"action_type": "place_ghost", "entity": "pipe",
                                                  "position": {"x": 1.5, "y": 0.5}}]}]},
                 "required_checks": list(OPTIMIZER_REQUIRED_CHECKS)}
    original = copy.deepcopy(candidate)
    snapshot = {"version": "fluid-topology-snapshot-v1", "tick": 100, "complete": True,
                "entities": [{"name": "pipe", "ghost": True}] * 3}
    normal_query = adapter.query
    def query(candidates=None):
        if candidates and candidates[0]["option_id"] == "optimizer.test":
            assert candidates == [original] and candidates[0] is not candidate
            return {"tick": 100, "validations": {"optimizer.test": {"reason": "surface_pipe_world_contact",
                    "surface_pipe_contacts": {"contacts": 1, "unknown": 0, "complete": True},
                    "fluid_topology_snapshot": copy.deepcopy(snapshot)}}}
        return normal_query(candidates)
    adapter.query = query
    calls = []
    def provider(world, observed, board):
        assert world is adapter and observed["tick"] == 100
        assert any(need["item"] == "iron-plate" for need in board["needs"])
        calls.append(True)
        return [candidate]
    diagnostic_calls = []
    def diagnostic(world, observed, candidates, response):
        assert world is adapter and observed["tick"] == 100
        diagnostic_calls.append(True)
        values = dict(response["validations"])
        if candidates[0]["option_id"] == "optimizer.test":
            values["optimizer.test"] = {**values["optimizer.test"], "fluid_topology_diagnostic": {
                "status": "unverified", "dispatch_authorized": False}}
        return values
    provider.validate = diagnostic
    journal = tmp_path / "j.jsonl"
    summary = run(adapter, ExplorationPolicy(0), journal=journal, decisions=2, measure_ticks=300,
                  seed=0, run_id="t", candidate_provider=provider)
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    pending = next(row for row in rows if row.get("status") == "pending")
    assert summary["failures"] == 0 and calls == [True] and diagnostic_calls
    assert pending["additional_candidate_results"] == [{"option_id": "optimizer.test", "need_id": "item:iron-plate",
        "reason": "surface_pipe_world_contact", "required_check_reason": "required_check_unimplemented:fluid_purity",
        "dispatch_reason": "external_dispatch_unimplemented", "validation": {"reason": "surface_pipe_world_contact",
            "surface_pipe_contacts": {"contacts": 1, "unknown": 0, "complete": True}, "observed_tick": 100,
            "fluid_topology_snapshot": {"sha256": sha256_hash(snapshot), "tick": 100, "complete": True,
                                        "entities": 3},
            "fluid_topology_diagnostic": {"status": "unverified", "dispatch_authorized": False}}}]
    assert "optimizer.test" not in adapter.executed and candidate == original


def test_external_candidates_stay_unselectable_even_if_required_checks_are_later_implemented(monkeypatch):
    monkeypatch.setattr("training.laya_clean_runner.required_check_reason", lambda *_args, **_kwargs: None)
    candidate = {"option_id": "optimizer.future", "family": "optimizer-block", "product": "iron-plate",
                 "plan": {"surface": "nauvis", "force": "player", "atomic": True,
                          "phases": [{"name": "test", "actions": [{"action_type": "place_ghost", "entity": "pipe",
                                                                      "position": {"x": 0.5, "y": 0.5}}]}]}}
    board = compile_status(facts(), {"iron-plate": 0.5})
    normal = validator()
    def validate(candidates):
        if candidates[0]["option_id"] == candidate["option_id"]:
            return {candidate["option_id"]: {"reason": None}}
        return normal(candidates)
    menu = compile_menu(facts(), board, validate, 1, additional_candidates=[candidate])
    assert candidate["option_id"] not in {option["option_id"] for option in menu["options"]}
    assert menu["additional_candidate_results"][0]["reason"] == "external_dispatch_unimplemented"


@pytest.mark.parametrize("case,expected", [("invalid", "external_plan_invalid"),
                                         ("invalid_funding", "external_funding_invalid"),
                                         ("missing", "external_validation_missing"),
                                         ("changed_bill", "construction_bill_changed"),
                                         ("shortfall", "item_shortfall:pipe")])
def test_external_candidate_validation_failures_remain_blocked(case, expected):
    from training.laya_saved_world import OPTIMIZER_REQUIRED_CHECKS
    candidate = {"option_id": "optimizer.bad", "family": "optimizer-block", "product": "iron-plate",
                 "bill": {"pipe": 1}, "required_checks": list(OPTIMIZER_REQUIRED_CHECKS),
                 "plan": {"surface": "nauvis", "force": "player", "atomic": True,
                          "phases": [{"name": "test", "actions": [{"action_type": "place_ghost", "entity": "pipe",
                                                                      "position": {"x": 0.5, "y": 0.5}}]}]}}
    if case == "invalid":
        candidate["plan"]["phases"][0]["actions"][0]["action_type"] = "place_entity"
    elif case == "invalid_funding":
        candidate["funding_status"] = {"state": "supply_wait", "reserved": {}, "shortfalls": {"pipe": 0}}
    calls = []
    normal = validator()
    def validate(candidates):
        if candidates[0]["option_id"] == candidate["option_id"]:
            calls.append(True)
            if case == "shortfall":
                return {candidate["option_id"]: {"reason": "item_shortfall:pipe"}}
            return {} if case == "missing" else {candidate["option_id"]: {"bill": {"pipe": 2}}}
        return normal(candidates)
    board = compile_status(facts(), {"iron-plate": 0.5})
    menu = compile_menu(facts(), board, validate, 1, additional_candidates=[candidate])
    assert menu["additional_candidate_results"][0]["reason"] == expected
    assert menu["additional_candidate_results"][0]["dispatch_reason"] == "external_dispatch_unimplemented"
    assert candidate["option_id"] not in {option["option_id"] for option in menu["options"]}
    assert bool(calls) == (case not in {"invalid", "invalid_funding"})
    if case == "invalid_funding":
        assert "pipe" not in menu["shortfalls"]
    if case == "shortfall":
        assert menu["shortfalls"]["pipe"] == 1


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


def test_funding_a_blocked_build_counts_as_progress(tmp_path):
    """Live 2026-11-05 d0: retargets cut the iron line's shortage 6 drills -> 0 but the run stopped as stalled."""
    class WaitOnly(FakeAdapter):
        def query(self, candidates=None):
            if candidates is not None:
                return {"validations": {c["option_id"]: {"reason": "collision"} for c in candidates}}
            return copy.deepcopy(self.world)

    class Funding:  # An unfunded external build whose shortage shrinks every decision.
        short = 40

        def __call__(self, adapter, facts, board):
            self.short -= 1
            self.last = {"reason": "unfunded", "detail": {"short": {"iron-gear-wheel": self.short}}}
            return []

    from training.laya_clean_runner import STALL_DECISIONS
    summary = run(WaitOnly(), ExplorationPolicy(0), journal=tmp_path / "j.jsonl", decisions=STALL_DECISIONS + 6,
                  measure_ticks=300, seed=0, run_id="t", candidate_provider=Funding())
    assert summary["stop"] != "stalled"


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


def test_failure_after_placement_begins_is_not_treated_as_atomic_rejection(tmp_path):
    import json
    from training.laya_clean_runner import LiveWorld
    (tmp_path / "evidence").mkdir()
    path = tmp_path / "evidence/transaction-x.json"
    receipt = {"status": "submission_failed", "submission": {"ok": False,
               "error": "1 placement(s) failed", "succeeded_placements": 1, "placement_failures": []}}
    path.write_text(json.dumps(receipt))
    world = LiveWorld.__new__(LiveWorld)
    world.root = tmp_path
    world._close_failed_submissions()
    assert json.loads(path.read_text())["status"] == "submission_failed"


def test_known_preflight_failure_in_correlated_receipt_is_closed(tmp_path):
    import json
    from training.laya_clean_runner import LiveWorld
    (tmp_path / "evidence").mkdir()
    path = tmp_path / "evidence/transaction-x.json"
    path.write_text(json.dumps({"status": "submission_failed", "submission": {
        "ok": False, "error": "atomic_footprint_blocked", "placement_failures": []}}))
    world = LiveWorld.__new__(LiveWorld)
    world.root = tmp_path
    world._close_failed_submissions()
    assert json.loads(path.read_text())["status"] == "rejected"


def test_unlinked_external_proposal_is_observed_without_inventing_demand():
    from training.laya_saved_world import OPTIMIZER_REQUIRED_CHECKS
    candidate = {"option_id": "optimizer.unlinked", "family": "optimizer-block", "product": "petroleum-gas",
                 "bill": {"pipe": 4}, "funding_status": {"state": "supply_wait", "reserved": {},
                                                          "shortfalls": {"pipe": 4}},
                 "required_checks": list(OPTIMIZER_REQUIRED_CHECKS),
                 "plan": {"surface": "nauvis", "force": "player", "atomic": True,
                          "phases": [{"name": "test", "actions": [{"action_type": "place_ghost", "entity": "pipe",
                                                                     "position": {"x": 0.5, "y": 0.5}}]}]}}
    board = compile_status(facts(), {"iron-plate": 0.5})
    normal = validator()
    seen = []
    def validate(candidates):
        if candidates[0]["option_id"] == candidate["option_id"]:
            seen.append(copy.deepcopy(candidates))
            return {candidate["option_id"]: {"reason": "item_shortfall:pipe", "bill": candidate["bill"]}}
        return normal(candidates)
    baseline = compile_menu(facts(), board, validate, 3, additional_candidates=[candidate])
    assert baseline["additional_candidate_results"] == [] and not seen
    observed = compile_menu(facts(), board, validate, 3, additional_candidates=[candidate], observe_unlinked=True)
    assert seen == [[candidate]]
    result, = observed["additional_candidate_results"]
    assert result["need_id"] is None and result["demand_status"] == "not_in_status_board"
    assert result["product"] == "petroleum-gas"
    assert result["reason"] == "item_shortfall:pipe"
    assert result["dispatch_reason"] == "external_dispatch_unimplemented"
    assert all(option["option_id"] != candidate["option_id"] for option in observed["options"])
    for key in ("needs", "options", "blocked", "rejected", "shortfalls"):
        assert observed[key] == baseline[key]
    satisfied = copy.deepcopy(board)
    satisfied["needs"].append({"item": "petroleum-gas", "condition": "satisfied"})
    seen.clear()
    menu = compile_menu(facts(), satisfied, validate, 3, additional_candidates=[candidate], observe_unlinked=True)
    assert menu["additional_candidate_results"] == [] and not seen


def test_runner_journals_unlinked_candidate_without_promoting_it(tmp_path):
    from training.laya_saved_world import OPTIMIZER_REQUIRED_CHECKS
    adapter = FakeAdapter()
    candidate = {"option_id": "optimizer.oil", "family": "optimizer-block", "product": "petroleum-gas",
                 "plan": {"surface": "nauvis", "force": "player", "atomic": True,
                          "phases": [{"name": "test", "actions": [{"action_type": "place_ghost", "entity": "pipe",
                                                                     "position": {"x": 0.5, "y": 0.5}}]}]},
                 "required_checks": list(OPTIMIZER_REQUIRED_CHECKS)}
    normal_query = adapter.query
    def query(candidates=None):
        if candidates and candidates[0]["option_id"] == candidate["option_id"]:
            assert candidates[0].get("inspect_underground_tiles") is True
            return {"tick": 100, "validations": {candidate["option_id"]: {"reason": "fluid_unknown"}}}
        return normal_query(candidates)
    adapter.query = query
    def provider(_world, _observed, _board):
        return [candidate]
    provider.observe_unlinked = True
    provider.prepare_validation = lambda candidates: [
        {**c, "inspect_underground_tiles": True} if c["option_id"] == candidate["option_id"] else c
        for c in candidates]
    def describe(_facts, _board, proposals):
        assert proposals == [candidate]
        return {candidate["option_id"]: {"status": "unavailable", "reason": "test", "dispatch_authorized": False}}
    provider.describe = describe
    journal = tmp_path / "j.jsonl"
    result = run(adapter, ExplorationPolicy(0), journal=journal, decisions=2, measure_ticks=300, seed=0,
                 run_id="unlinked", candidate_provider=provider)
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    decision = next(row for row in rows if row.get("status") == "pending")
    assert result["failures"] == 0
    observed, = decision["additional_candidate_results"]
    assert observed["demand_status"] == "not_in_status_board" and observed["need_id"] is None
    assert observed["reason"] == "fluid_unknown" and observed["dispatch_reason"] == "external_dispatch_unimplemented"
    assert observed["model_demand"]["status"] == "unavailable"
    records = [json.loads(line) for line in (tmp_path / "decision-states.jsonl").read_text().splitlines()]
    state_evidence = records[-1]["state"]["observation_digest"]["optimizer_demand"]
    assert state_evidence == [{"option_id": candidate["option_id"], "status": "unavailable",
                               "reason": "test", "dispatch_authorized": False}]
    assert candidate["option_id"] not in adapter.executed


def test_fluid_facts_leave_solid_menus_alone_and_plastic_chain_blocked_without_an_operation():
    from tests.test_laya_factory_status import FLUID_RECIPES
    plain, fluid = facts(), {**facts(), "fluid_recipes": FLUID_RECIPES}
    targets = phase("C1")["targets"]
    assert (compile_menu(fluid, compile_status(fluid, targets), validator(), seed=4)
            == compile_menu(plain, compile_status(plain, targets), validator(), seed=4))
    board = compile_status(fluid, {"plastic-bar": 0.5})
    menu = compile_menu(fluid, board, validator(), seed=4)
    for item in ("plastic-bar", "petroleum-gas", "crude-oil"):
        assert menu["blocked"][f"item:{item}"] == {"no_candidate": 1}
        assert not any(o.get("need_id") == f"item:{item}" for o in menu["options"])
    state = build_state({**fluid, "oil_field": {"anchor": {"x": 0, "y": 0}, "anchor_source": "roboport",
                                               "search_radius": 512, "found": True, "wells": 3,
                                               "center": {"x": 62.04, "y": 0.33}, "distance": 62.04,
                                               "yield_percent": 350.0, "max_crude_per_s": 35.0,
                                               "water": {"found": False}}},
                        menu, board, phase("C4"), "test", ExplorationPolicy.model_ref, [], None)
    assert state["observation_digest"]["oil_field"] == {
        "found": True, "wells": 3, "search_radius": 512, "anchor": {"x": 0, "y": 0}, "distance": 62.0,
        "yield_percent": 350.0, "max_crude_per_s": 35.0, "center": {"x": 62.0, "y": 0.3}, "water_distance": None}


@pytest.mark.parametrize("chests, kept", [(1, True), (2, False)])
def test_the_last_kit_chest_is_kept_for_the_plastic_cell(chests, kept):
    """User contract 2026-10-17; live 2026-11-08: mall slots took every kit chest and the oil cell stalled."""
    f = facts()
    f["networks"]["1"]["items"]["passive-provider-chest"] = chests

    def validate(candidates):
        return {c["option_id"]: {"reason": None, "bill": {
            {"expand": "roboport", "mine": "electric-mining-drill", "smelt": "electric-furnace",
             "assemble": "assembling-machine-1"}[c["family"]]: 1, "passive-provider-chest": 1}} for c in candidates}

    menu = compile_menu(f, compile_status(f, phase("C1")["targets"]), validate, seed=1)
    reserved = any("reserved:plastic_cell:passive-provider-chest" in r for r in menu["blocked_alternatives"].values()) \
        or any("reserved:plastic_cell:passive-provider-chest" in r for r in menu["blocked"].values())
    assert reserved is kept
    assert all(o["family"] == "wait" for o in menu["options"]) is kept


def test_a_chest_shortfall_before_plastic_is_a_blocked_fact_not_a_stock_need():
    f = facts()
    f["recipes"].update({
        "passive-provider-chest": recipe([("steel-chest", 1), ("electronic-circuit", 3), ("advanced-circuit", 1)], 30),
        "advanced-circuit": recipe([("plastic-bar", 2), ("electronic-circuit", 2)], 360)})

    def validate(candidates):
        return {c["option_id"]: {"reason": "item_shortfall:passive-provider-chest", "bill": {}} for c in candidates}

    menu = compile_menu(f, compile_status(f, phase("C1")["targets"]), validate, seed=1)
    assert "passive-provider-chest" not in menu["shortfalls"]
    assert menu["unit_shortfalls"].get("passive-provider-chest")  # Still visible as a fact.
    assert [r.split(":")[0] for r in menu["blocked"]["stock:passive-provider-chest"]] == ["unobtainable"]
    assert not any(o.get("product") == "passive-provider-chest" for o in menu["options"])


def test_cadence_is_flat_then_shrinks_like_a_log_and_floors():
    """User 2026-11-08: the decision rate rises like a delayed logarithmic function of game time."""
    from training.laya_clean_runner import Settings, cadence_ticks
    s = Settings()
    early = [cadence_ticks(t, s) for t in (0, s.cadence_delay_ticks)]
    later = [cadence_ticks(s.cadence_delay_ticks + k * s.cadence_scale_ticks, s) for k in (1, 3, 10, 100)]
    assert early == [s.cadence_start_ticks] * 2
    assert later == sorted(later, reverse=True) and later[0] < s.cadence_start_ticks
    assert cadence_ticks(10**9, s) == s.cadence_min_ticks
    rates = [1 / cadence_ticks(s.cadence_delay_ticks + k * s.cadence_scale_ticks, s) for k in (1, 2, 4, 8)]
    gains = [b - a for a, b in zip(rates, rates[1:])]
    assert all(g > 0 for g in gains)  # rising, by roughly constant steps per doubling (log-like)


class DeferredAdapter(FakeAdapter):
    deferred_execution = True

    def __init__(self):
        super().__init__()
        self.world["networks"]["1"]["items"].update({"assembling-machine-1": 10, "roboport": 4, "electric-furnace": 6})
        self.advanced = []
        self.waits = []

    def execute(self, selected, presented, measure_ticks, wait=True):
        self.waits.append(wait)
        self.executed.append(selected)
        option = next(o for o in presented if o["option_id"] == selected)
        return {"status": "construction_pending" if option.get("plan") else "observed"}

    def advance(self, ticks):
        self.advanced.append(ticks)
        self.world["tick"] += ticks


def test_live_cycles_submit_without_waiting_and_advance_by_the_cadence(tmp_path):
    from training.laya_clean_runner import Settings, cadence_ticks
    adapter = DeferredAdapter()
    run(adapter, ExplorationPolicy(3), journal=tmp_path / "j.jsonl", decisions=6, measure_ticks=300, seed=0, run_id="t")
    assert adapter.waits and not any(adapter.waits)  # Every submission returned without waiting.
    assert adapter.advanced and all(t >= cadence_ticks(0, Settings()) for t in adapter.advanced)


def test_parallel_picks_take_other_aspects_that_fit_footprint_and_stock():
    from training.laya_clean_runner import parallel_picks

    def option(oid, family, need, x, bill):
        plan = {"phases": [{"actions": [{"action_type": "place_ghost", "entity": "assembling-machine-1",
                                         "position": {"x": x, "y": 0.5}}]}]}
        return {"option_id": oid, "family": family, "need_id": need, "plan": plan, "bill": bill, "summary": oid}
    first = option("a", "assemble", "item:gear", 0.5, {"assembling-machine-1": 1})
    menu = {"options": [first,
                        option("b", "assemble", "item:cable", 10.5, {"assembling-machine-1": 1}),  # same aspect
                        option("c", "line", "item:iron-plate", 1.5, {"assembling-machine-1": 1}),  # overlaps a
                        option("d", "expand", "coverage", 20.5, {"assembling-machine-1": 2}),      # stock left: 1
                        option("e", "smelt", "item:steel-plate", 30.5, {"assembling-machine-1": 1}),
                        {"option_id": "wait.300", "family": "wait", "need_id": "wait", "summary": "wait"}],
            "needs": [{"need_id": n, "options": [i]} for n, i in
                      (("item:gear", "a"), ("item:cable", "b"), ("item:iron-plate", "c"), ("coverage", "d"),
                       ("item:steel-plate", "e"), ("wait", "wait.300"))]}
    facts = {"networks": {"1": {"items": {"assembling-machine-1": 2}}}}

    class First:  # Always the first non-wait option offered.
        def choose(self, state, menu):
            o = next(o for o in menu["options"] if o["family"] != "wait")
            return {"option_id": o["option_id"], "need_id": o["need_id"], "probability": 1.0}
    extra = parallel_picks(First(), facts, menu, first, 4, lambda sub: {})
    assert [o["option_id"] for _, o, _, _ in extra] == ["e"]  # b same aspect, c overlaps, d unaffordable


def test_parallel_picks_never_spend_irreplaceable_kit_items_or_take_untagged_coverage():
    from training.laya_clean_runner import parallel_picks
    first = {"option_id": "a", "family": "assemble", "need_id": "item:gear", "bill": {}, "summary": "a"}
    port_hop = {"option_id": "b", "family": "expand", "need_id": "item:copper-ore", "bill": {"roboport": 1}, "summary": "b"}
    loose = {"option_id": "c", "family": "starter", "need_id": "coverage", "bill": {}, "summary": "c"}
    menu = {"options": [first, port_hop, loose, {"option_id": "wait.300", "family": "wait", "need_id": "wait"}],
            "needs": [{"need_id": n, "options": [i]} for n, i in (("item:gear", "a"), ("item:copper-ore", "b"),
                                                                   ("coverage", "c"), ("wait", "wait.300"))]}
    facts = {"recipes": {"roboport": {"ingredients": [{"name": "advanced-circuit", "amount": 45}]}},
             "networks": {"1": {"items": {"roboport": 9}}}}

    class Any:
        def choose(self, state, menu):
            o = next(o for o in menu["options"] if o["family"] != "wait")
            return {"option_id": o["option_id"], "need_id": o["need_id"], "probability": 1.0}
    assert parallel_picks(Any(), facts, menu, first, 4, lambda sub: {}) == []


def test_untagged_coverage_may_not_spend_a_roboport_nothing_can_make():
    f = facts()
    f["recipes"]["roboport"] = recipe([("advanced-circuit", 45)], 300)

    def validate(candidates):
        return {c["option_id"]: {"reason": None, "bill": {"roboport": 1} if c["family"] == "expand" else
                {"assembling-machine-1": 1}} for c in candidates}
    menu = compile_menu(f, compile_status(f, phase("C1")["targets"]), validate, seed=1)
    assert not any(o["family"] == "expand" and o["need_id"] == "coverage" for o in menu["options"])
    reasons = {**menu["blocked"].get("coverage", {}), **menu["blocked_alternatives"].get("coverage", {})}
    assert "irreplaceable:roboport" in reasons
