# Path: tests/test_laya_ops_decision_facts.py | Purpose: Verify mall capacity, starter blocker and no-progress facts reach Laya's ops decision.

import json
from pathlib import Path
from types import SimpleNamespace

from training.laya_guided_policy import GuidedPolicy
from training.laya_ops_runner import brief, compile_options, mall_capacity, no_progress, run, with_economics

# Shapes from laya-c5d-20261018 next-s77506 decision 5: one anchor gear cell, borrow refused.
GEAR_ANCHOR = {"recipe": "iron-gear-wheel", "anchor": True, "loaned": False, "standing": True}
DEFERRED = {"ok": False, "operation": "produce", "params": {"item": "electronic-circuit", "stock_target": 1},
            "code": "rationed_mall_borrow_deferred", "need_id": "stock:electronic-circuit",
            "details": {"active_loans": [], "item": "electronic-circuit", "target": 1,
                        "slot_claim": {"required": {"copper-cable": 3}, "available": {"copper-cable": 0}}}}
ECONOMICS = {"slots": {"capacity": 8, "occupied": 1, "free": 7, "unlimited": False}, "cells": [GEAR_ANCHOR],
             "loans": [], "projects": {}}


def drill(n):
    return {**DEFERRED, "params": {"item": "electric-mining-drill", "stock_target": 12},
            "need_id": "stock:electric-mining-drill",
            "details": {**DEFERRED["details"], "item": "electric-mining-drill", "target": 12}}


def test_capacity_names_lendable_cells_cover_and_borrow_refusal():
    capacity = mall_capacity(ECONOMICS, [DEFERRED])
    assert capacity["slots_free"] == 7 and capacity["lendable"] == 0 and capacity["anchors"] == 1
    assert capacity["covered_recipes"] == ["iron-gear-wheel"]
    assert capacity["borrow_blocked"] == ("no borrowable assembler for electronic-circuit (0 active loan(s));"
                                          " a new slot is short of copper-cable 3")
    text = brief([DEFERRED], None, None, {}, 0, economics={**ECONOMICS, "capacity": capacity})
    assert "CAPACITY: cells=1 anchors=1 loaned=0 lendable=0 covers iron-gear-wheel | last borrow refused" in text
    unknown = mall_capacity({"slots": {}, "cells": None, "loans": None}, [])
    assert unknown["lendable"] is None and unknown["borrow_blocked"] is None and unknown["active_loans"] is None


def test_produce_and_plate_options_carry_borrow_and_starter_facts():
    menu = {"needs": [], "options": [
        {"option_id": "op.produce.electronic-circuit", "family": "produce", "product": "electronic-circuit",
         "need_id": "stock:electronic-circuit", "params": {"item": "electronic-circuit"}, "features": {}},
        {"option_id": "op.foundation.copper-plate", "family": "foundation", "product": "copper-plate",
         "need_id": "item:copper-plate", "params": {"recipe": "copper-plate"}, "features": {}}]}
    starters = {"iron": {"blocker": "ready"}, "copper": {"blocker": "not_producing", "output_count": 0}}
    economics = {**ECONOMICS, "capacity": mall_capacity(ECONOMICS, [DEFERRED]), "starters": starters}
    produce, foundation = with_economics(menu, [DEFERRED], economics)["options"]
    assert produce["features"]["borrow_blocked"] is True and produce["features"]["lendable_cells"] == 0
    assert foundation["features"]["starter_blocker"] == "not_producing"
    assert "STARTERS [mission.ops-starters-before-foundations]: iron ready out=? | copper not_producing out=0" in brief(
        [], None, None, {}, 0, rules={"mission.ops-starters-before-foundations"}, economics=economics)


def test_no_progress_counts_unchanged_blocked_outcomes_per_option_and_need():
    # next-s77510 decisions 291-293: circuit refused, then the drill refused twice with the same outcome.
    results = [DEFERRED, drill(1), {"ok": True, "operation": "wait", "params": {"ticks": 300}}, drill(2)]
    assert no_progress([r for r in results if r["operation"] == "produce"]) == 3
    assert no_progress([drill(1), {**drill(2), "code": "material_shortage"}]) == 1
    assert no_progress([drill(1), {"ok": True, "operation": "produce", "code": "producing"}]) == 0
    board = {"needs": [{"item": "electric-mining-drill", "condition": "short", "goal_required_per_s": 0}]}
    options = compile_options(board, results, 0)["options"]
    waits = [o for o in options if o["family"] == "wait"]
    assert waits and all("no_progress" not in o["features"] for o in waits)
    assert "BLOCKED [mission.ops-history-no-repeat]: produce electric-mining-drill rationed_mall_borrow_deferred x2" in brief(
        results, None, None, {}, 0, rules={"mission.ops-history-no-repeat"})


def test_runner_exposes_capacity_and_starters_to_policy(tmp_path):
    class World:
        root, tick = None, 0

        def query(self):
            return {"tick": self.tick, "production": {}, "consumption": {}, "machines": [],
                    "networks": {"n": {"items": {}}}, "recipes": {}}

        def advance(self, ticks):
            self.tick += ticks

    class Session:
        starter_facts = {"stone": {"blocker": "absent", "tick": 0}}

        def economics(self):
            return dict(ECONOMICS)

        def run(self, operation, **params):
            return {**DEFERRED, "operation": operation, "params": params}

        def close(self):
            pass

    seen = []

    class Policy(GuidedPolicy):
        def choose(self, state, menu):
            seen.append((state, menu))
            option = next((o for o in menu["options"] if o["family"] != "wait"), menu["options"][0])
            return {"option_id": option["option_id"], "need_id": option["need_id"], "probability": 1.0,
                    "rules": [], "distribution": {}}

    run(World(), Policy(1), session=Session(), decisions=3, measure_ticks=600, seed=1, run_id="facts",
        journal=tmp_path / "journal.jsonl", emit=lambda s: None)
    state, _ = seen[0]
    digest = state["observation_digest"]["mall_economics"]
    assert digest["capacity"]["lendable"] == 0 and digest["starters"]["stone"]["blocker"] == "absent"
    assert "stone absent out=?" in state["brief"]
    row = json.loads((tmp_path / "decision-states.jsonl").read_text().splitlines()[0])
    assert row["state"]["observation_digest"]["mall_economics"]["capacity"]["covered_recipes"] == ["iron-gear-wheel"]
    # The same blocked option, chosen twice, reports its streak per option and per need on the third menu.
    first = next(o for o in seen[0][1]["options"] if o["family"] != "wait")
    again = next(o for o in seen[2][1]["options"] if o["option_id"] == first["option_id"])
    assert again["features"]["no_progress"] == 2 and again["features"]["need_no_progress"] == 2


def test_acceptance_records_per_starter_facts(monkeypatch):
    import orchestrator.checkpoint_observer as observer
    from orchestrator.checkpoint_milestones import FactorySnapshot
    from training.laya_ops import DeterministicSession

    snapshot = FactorySnapshot.from_mapping({"tick": 9, "base_valid": True, "starters": {
        "iron": {"present": True, "healthy": True, "producing": True, "output_count": 7},
        "copper": {"present": True, "healthy": False, "producing": False}}})

    class Adapter:
        def __init__(self, *args, **kwargs):
            pass

        def observe(self):
            return observer.StructuredObservation(snapshot, "fixture", "fixture", {"tick": snapshot.tick})

    monkeypatch.setattr(observer, "LiveCheckpointObservationAdapter", Adapter)
    session = object.__new__(DeterministicSession)
    session.client = session.bridge = session.reference_point = None
    session.episode_id = "fixture"
    session.script_output = Path("/unused/script-output")
    session.acceptance()
    assert session.starter_facts["iron"] == {"present": True, "healthy": True, "producing": True,
                                             "output_count": 7, "blocker": "ready", "tick": 9}
    assert session.starter_facts["copper"]["blocker"] == "unhealthy"
    assert session.starter_facts["stone"] == {"blocker": "unobserved", "tick": 9}


def test_need_no_progress_variant_loads_with_verified_quotes():
    from training.laya_guided_policy import matches
    from training.laya_variants import load_variant

    variant = load_variant("docs/rl/laya_variants/ops-need-no-progress.json")
    prefer = variant["catalog_patch"]["mission.ops-history-no-repeat"]["prefer"]
    assert [p["weight"] for p in prefer if matches(p["when"], {"need_no_progress": 3})] == [0.2]
    assert not any(matches(p["when"], {"need_no_progress": 1}) for p in prefer)


def test_final_acceptance_is_fresh_while_reached_stays_latched(monkeypatch):
    # Review 2026-10-20: CheckpointEvaluator returns "passed" forever once reached, so a latched final
    # could not show a regression. Fresh statuses use the stateless predicates on the same sample.
    import orchestrator.checkpoint_observer as observer
    from orchestrator import checkpoint_milestones as cm
    from training.laya_ops import DeterministicSession

    outcomes = iter([cm.PredicateStatus.PASSED, cm.PredicateStatus.FAILED, cm.PredicateStatus.UNKNOWN])

    class Definition:
        checkpoint_id, predicate_version = "C9", "test-v1"

        def evaluate(self, current, prior, samples):
            return cm._result(self.checkpoint_id, self.predicate_version, next(outcomes), {})

    class Adapter:
        def __init__(self, *args, **kwargs):
            pass

        def observe(self):
            return observer.StructuredObservation(cm.FactorySnapshot.from_mapping({"tick": 9, "base_valid": True}), "fixture", "fixture", {"tick": 9})

    monkeypatch.setattr(observer, "LiveCheckpointObservationAdapter", Adapter)
    session = object.__new__(DeterministicSession)
    session.client = session.bridge = session.reference_point = None
    session.episode_id = "fixture"
    session.script_output = Path("/unused/script-output")
    session._evaluator, session._baseline, session._history = cm.CheckpointEvaluator((Definition(),)), None, []
    seen = [(session.acceptance()["C9"], session.acceptance_fresh["C9"]) for _ in range(3)]
    assert seen == [("passed", "passed"), ("passed", "failed"), ("passed", "unknown")]
