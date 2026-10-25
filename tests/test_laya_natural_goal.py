# Path: tests/test_laya_natural_goal.py | Purpose: Protect the natural goal contract: verified research target_completed ends a run, rate proxies do not, and a stall needs an unchanged useful state across the declared window.

import json

from training.laya_clean_runner import Settings
from training.laya_goal import (
    DEFAULT_TECHNOLOGY, GOAL_VERSION, funded_signature, goal_evidence, loan_signature, research_goal_satisfied, useful_state,
)
from training.laya_ops_runner import run


def test_target_completed_is_the_only_verified_goal():
    assert DEFAULT_TECHNOLOGY == "mining-productivity-4"
    # Proxy evidence: a high science rate, a queued target and running research are all observations.
    proxy = {"current_research": "mining-productivity-4", "research_progress": 0.99,
             "research_queue": ["mining-productivity-4"], "current_target": "mining-productivity-4",
             "technology": {"canonical_name": "mining-productivity-4", "state": "current",
                            "target_completed": False, "science_packs": {"automation-science-pack": 1}}}
    assert research_goal_satisfied(proxy) is False
    assert goal_evidence(proxy)["verified"] is False and goal_evidence(proxy)["research_progress"] == 0.99
    # Verified success: the requested level is completed.
    done = {"technology": {"canonical_name": "mining-productivity-4", "state": "completed",
                           "target_completed": True, "researched": True}}
    assert research_goal_satisfied(done) is True
    assert goal_evidence(done)["verified"] is True
    # Malformed or missing payloads are not success.
    assert research_goal_satisfied(None) is False and research_goal_satisfied({}) is False
    assert research_goal_satisfied({"technology": {"target_completed": "yes"}}) is False


def test_useful_state_counts_relevant_work_not_arbitrary_surplus():
    base = {"pending_ghosts": 5, "production": {"transport-belt": 10, "iron-ore": 900}}
    stock = {"transport-belt": 3}
    economics = {"loans": [{"lender": "transport-belt", "making": "electric-mining-drill", "target": 2}],
                 "projects": {"mine": {"state": "constructing", "reserved": {"transport-belt": 6}}}}
    start = useful_state(base, relevant_items=["transport-belt"], stock=stock, economics=economics)
    # Arbitrary surplus (ores not required by the goal) does not change the signature.
    surplus = {**base, "production": {**base["production"], "iron-ore": 5000}}
    assert useful_state(surplus, relevant_items=["transport-belt"], stock=stock, economics=economics) == start
    # A required item's crafting, delivery, a ghost reduction, a loan or a funded plan does change it.
    assert useful_state({**base, "production": {"transport-belt": 11}}, relevant_items=["transport-belt"],
                        stock=stock, economics=economics) != start
    assert useful_state(base, relevant_items=["transport-belt"], stock={"transport-belt": 4},
                        economics=economics) != start
    assert useful_state({**base, "pending_ghosts": 4}, relevant_items=["transport-belt"], stock=stock,
                        economics=economics) != start
    moved = {"loans": [{"lender": "transport-belt", "making": "splitter", "target": 2}],
             "projects": economics["projects"]}
    assert useful_state(base, relevant_items=["transport-belt"], stock=stock, economics=moved) != start
    grown = {"loans": economics["loans"],
             "projects": {"mine": {"state": "constructing", "reserved": {"transport-belt": 8}}}}
    assert useful_state(base, relevant_items=["transport-belt"], stock=stock, economics=grown) != start
    # An unknown loan survey is None, distinct from a known empty list.
    assert loan_signature(None) is None and loan_signature([]) == ()
    assert funded_signature({"mine": {"state": "supply_wait", "reserved": {"x": 5}}}) == ()


class _World:
    root = None

    def __init__(self, step=100):
        self.tick = 1000
        self.step = step
        self.production = {"transport-belt": 0}

    def query(self):
        return {"tick": self.tick, "production": dict(self.production), "consumption": {},
                "machines": [], "networks": {}, "ports": [], "resources": [], "recipes": {},
                "pending_ghosts": 0}

    def advance(self, ticks):
        self.tick += ticks


class _AdvancingSession:
    """A blocking operation that spends game time while the world builds/loans/researches."""

    def __init__(self, world, *, step=100):
        self.world, self.step, self.calls = world, step, 0

    def run(self, operation, **params):
        self.calls += 1
        self.world.tick += self.step
        self.world.production["transport-belt"] += 1  # crafting/delivery advances
        return {"ok": False, "operation": operation, "params": params, "code": "material_shortage",
                "details": {"required": {"transport-belt": 10}, "available": {}}}

    def goal_status(self, technology):
        return {"current_research": technology, "research_progress": self.calls * 0.1,
                "research_queue": [], "technology": {"canonical_name": technology, "target_completed": False}}

    def close(self):
        pass


class _BlockedSession(_AdvancingSession):
    """A blocked operation that changes nothing in the world."""

    def run(self, operation, **params):
        self.calls += 1
        self.world.tick += self.step
        return {"ok": False, "operation": operation, "params": params, "code": "pending_system_construction"}

    def goal_status(self, technology):
        return {"current_research": technology, "research_progress": 0.5, "research_queue": [],
                "technology": {"canonical_name": technology, "target_completed": False}}


class _StepPolicy:
    authority = "guided_exploration"

    def __init__(self, option_id):
        self.option_id = option_id

    def choose(self, state, menu):
        chosen = next((o for o in menu["options"] if o["option_id"] == self.option_id), None)
        chosen = chosen or next(o for o in menu["options"] if o["family"] != "wait")
        return {"option_id": chosen["option_id"], "need_id": chosen["need_id"], "probability": 1.0, "rules": [],
                "distribution": [{"option_id": chosen["option_id"], "need_id": chosen["need_id"],
                                  "probability": 1.0, "weight": 1.0, "rules": [], "features": {}}]}


def test_goal_run_does_not_end_on_the_rate_ladder(tmp_path, monkeypatch):
    from training import laya_ops_runner as ops
    monkeypatch.setattr(ops, "ops_phase_reached", lambda *_: True)  # Every rate proxy is "reached" (C6 included).
    world = _World()
    summary = run(world, _StepPolicy("op.reserve_steel_seed"), journal=tmp_path / "j.jsonl", decisions=5,
                  measure_ticks=600, seed=1, run_id="proxy", session=_AdvancingSession(world),
                  settings=Settings(goal_technology="mining-productivity-4"))
    assert summary["stop"] == "decision_budget"  # Proxy C6 completion is not the goal.
    assert summary["stop"] != "mission_complete" and summary["stop"] != "proxy_mission_complete_without_acceptance"


def test_twelve_fast_failed_choices_with_progress_do_not_terminate(tmp_path):
    world = _World()
    summary = run(world, _StepPolicy("op.reserve_steel_seed"), journal=tmp_path / "j.jsonl", decisions=12,
                  measure_ticks=600, seed=1, run_id="progress", session=_AdvancingSession(world),
                  settings=Settings(goal_technology="mining-productivity-4"))
    assert summary["decisions"] == 12 and summary["stop"] == "decision_budget"


def test_a_blocked_unchanged_full_window_stalls(tmp_path):
    world = _World()
    summary = run(world, _StepPolicy("op.reserve_steel_seed"), journal=tmp_path / "j.jsonl", decisions=40,
                  measure_ticks=600, seed=1, run_id="blocked", session=_BlockedSession(world),
                  settings=Settings(goal_technology="mining-productivity-4", stall_observation_ticks=3000))
    assert summary["stop"] == "stalled"
    assert summary["stall"]["version"] == "useful-state-window-v1"
    assert summary["stall"]["observation_ticks"] == 3000 and summary["stall"]["idle_ticks"] >= 3000


def test_verified_target_completed_ends_the_run(tmp_path):
    class DoneSession(_AdvancingSession):
        def __init__(self, world):
            super().__init__(world)
            self.status_calls = 0

        def goal_status(self, technology):
            self.status_calls += 1
            return {"current_research": technology,
                    "technology": {"canonical_name": technology, "researched": True, "target_completed": True}}

    session = DoneSession(_World())
    summary = run(session.world, _StepPolicy("op.reserve_steel_seed"), journal=tmp_path / "j.jsonl", decisions=10,
                  measure_ticks=600, seed=1, run_id="done", session=session,
                  settings=Settings(goal_technology="mining-productivity-4"))
    assert summary["stop"] == "goal_completed"
    assert summary["objective_version"] == GOAL_VERSION
    assert summary["goal"]["verified"] is True and summary["goal"]["technology"] == "mining-productivity-4"
    assert session.status_calls == 1


def test_research_bridge_methods_use_the_mod_commands(tmp_path):
    from training.laya_ops import DeterministicSession

    class Bridge:
        def __init__(self):
            self.calls = []

        def set_research(self, technology, force=None):
            self.calls.append(("set", technology, force))
            path = tmp_path / "set.json"
            path.write_text(json.dumps({"ok": True, "canonical_name": technology}))
            return path

        def research_status(self, force=None, technology=None):
            self.calls.append(("status", force, technology))
            path = tmp_path / "status.json"
            path.write_text(json.dumps({"technology": {"canonical_name": technology, "target_completed": True}}))
            return path

        def science_status(self, surface, force):
            self.calls.append(("science", surface, force))
            path = tmp_path / "science.json"
            path.write_text(json.dumps({"lab_count": 2}))
            return path

    session = DeterministicSession.__new__(DeterministicSession)
    session.bridge = Bridge()
    assert session.goal_status("mining-productivity-4")["technology"]["target_completed"] is True
    assert session._set_research("mining-productivity-4")["technology"] == "mining-productivity-4"
    assert session.science_status()["lab_count"] == 2
    assert session.bridge.calls == [("status", "player", "mining-productivity-4"),
                                    ("set", "mining-productivity-4", "player"),
                                    ("science", "nauvis", "player")]


def test_useful_state_counts_loan_crafting_before_final_target_delivery():
    base={'lender':'copper-cable','making':'chemical-science-pack','target':30,
          'step_recipe':'advanced-circuit','counter':10,'remaining_bill':{'chemical-science-pack':30}}
    first=useful_state({},economics={'loans':[base]})
    advanced=useful_state({},economics={'loans':[{**base,'counter':11}]})
    assert first != advanced
