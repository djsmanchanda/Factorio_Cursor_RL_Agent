# Path: tests/test_optimizer_blocks_runtime.py | Purpose: Protect the opt-in optimizer-block capability in the clean runner: flag-off identity, site options with facts, verified routing, mutation-time revalidation, in-flight suppression, own-output measurement and funnel journaling.
"""Fakes only: nothing here contacts Factorio, RCON or a real saved world."""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.test_optimizer_block_trial import FakeBridge, chain, world_facts  # noqa: F401  (chain is a fixture)
from tools import optimizer_block_proposals as proposals
from tools import optimizer_block_runtime as rt
from tools import optimizer_saved_world as integration
from training.laya_clean_runner import (ExplorationPolicy, LiveWorld, _option_record, build_state, compile_menu, run)
from training.laya_factory_status import compile_status
from training.laya_mission import phase
from training.laya_saved_world import OPTIMIZER_REQUIRED_CHECKS, SavedWorldAdapter, SavedWorldError

CHECKS = list(OPTIMIZER_REQUIRED_CHECKS)
PACK = proposals.PACK
EVIDENCE = {"fluid_purity": {"fluid_entities": 0}, "safe_infrastructure_attachment": {"anchor_unit": 1234}}


def runner_facts():
    from tests.test_laya_clean_runner import facts
    world = facts()
    world["enabled_recipes"] = {PACK: True}
    return world


def external(option_id="optimizer.a", x=0.5, *, bill=None, evidence=True, facts=None):
    candidate = {"option_id": option_id, "family": "optimizer-block", "product": PACK,
                 "summary": f"Build {option_id}", "bill": bill or {"transport-belt": 4}, "required_checks": CHECKS,
                 "plan": {"surface": "nauvis", "force": "player", "atomic": True, "phases": [
                     {"name": "p", "actions": [{"action_type": "place_ghost", "entity": "requester-chest",
                                                "position": {"x": x, "y": 0.5},
                                                "logistic_requests": [{"name": "copper-plate", "count": 20}]}]}]}}
    if evidence:
        candidate["check_evidence"] = copy.deepcopy(EVIDENCE)
    if facts:
        candidate["optimizer_facts"] = facts
    return candidate


def validate_for(external_bills):
    from tests.test_laya_clean_runner import validator
    base = validator()
    def validate(candidates):
        if candidates[0]["option_id"] in external_bills:
            return {candidates[0]["option_id"]: {"reason": None, "bill": external_bills[candidates[0]["option_id"]]}}
        return base(candidates)
    return validate


def passing(candidate, entry):
    return None, copy.deepcopy(EVIDENCE)


# --------------------------------------------------------------------------- flag off

def test_flag_off_keeps_external_candidates_unselectable_and_the_menu_unchanged():
    candidates = [external("optimizer.a"), external("optimizer.b", x=3.5)]
    board = compile_status(runner_facts(), {"iron-plate": 0.5})
    bills = {c["option_id"]: c["bill"] for c in candidates}
    plain = compile_menu(runner_facts(), board, validate_for(bills), 3, additional_candidates=candidates, observe_unlinked=True)
    explicit = compile_menu(runner_facts(), board, validate_for(bills), 3, additional_candidates=candidates,
                            observe_unlinked=True, external_check=None)
    assert plain == explicit
    assert not any(o["family"] == "optimizer-block" for o in plain["options"])
    assert [r["reason"] for r in plain["additional_candidate_results"]] == ["required_check_unimplemented:fluid_purity"] * 2
    assert all(r["dispatch_reason"] == "external_dispatch_unimplemented" and "selectable" not in r
               for r in plain["additional_candidate_results"])
    state = build_state(runner_facts(), plain, board, phase("C1"), "t", ExplorationPolicy.model_ref, [], None)
    assert "optimizer_blocks" not in state["observation_digest"]
    assert all("optimizer_facts" not in _option_record(o) for o in plain["options"])


def test_flag_off_run_journal_has_no_capability_events(tmp_path):
    from tests.test_laya_clean_runner import FakeAdapter
    journal = tmp_path / "j.jsonl"
    summary = run(FakeAdapter(), ExplorationPolicy(0), journal=journal, decisions=3, measure_ticks=300, seed=0, run_id="t")
    text = journal.read_text()
    assert "optimizer" not in text and "capability" not in text and "optimizer_blocks" not in summary


# --------------------------------------------------------------------------- admission into the menu

def test_up_to_three_verified_sites_become_selectable_options_with_their_facts():
    facts_row = {"project_id": "red-block-1_1", "expected_output_per_s": 0.2, "construction_bill": {"transport-belt": 4},
                 "operating_demand_per_s": {"copper-plate": 0.2, "iron-gear-wheel": 0.2},
                 "network_shortage_vs_bill": {}, "held_reservations": {"transport-belt": 4}}
    candidates = [external(f"optimizer.{i}", x=1.5 * i + 0.5, facts=facts_row) for i in range(3)]
    board = compile_status(runner_facts(), phase("C1")["targets"])  # The pack is not a board need here.
    bills = {c["option_id"]: c["bill"] for c in candidates}
    menu = compile_menu(runner_facts(), board, validate_for(bills), 5, additional_candidates=candidates,
                        per_need_shown=2, external_check=passing)
    shown = [o for o in menu["options"] if o["family"] == "optimizer-block"]
    assert len(shown) == 3  # "Where" is the policy's choice: no sampling down to per_need_shown
    need = next(n for n in menu["needs"] if n["need_id"] == f"optimizer:{PACK}")
    assert len(need["options"]) == 3 and need["condition"] == "available"
    assert all(o["need_id"] == f"optimizer:{PACK}" and o["draws"] == {"copper-plate": 20} for o in shown)
    assert all(r["selectable"] is True and r["reason"] is None for r in menu["additional_candidate_results"])
    state = build_state(runner_facts(), menu, board, phase("C1"), "t", ExplorationPolicy.model_ref, [], None)
    digest = state["observation_digest"]["optimizer_blocks"]
    assert len(digest) == 3 and digest[0]["expected_output_per_s"] == 0.2
    assert digest[0]["operating_demand_per_s"] == {"copper-plate": 0.2, "iron-gear-wheel": 0.2}
    assert _option_record(shown[0])["optimizer_facts"]["construction_bill"] == {"transport-belt": 4}
    assert len(state["options"]) <= 20


def test_a_pack_need_on_the_board_links_the_block_to_that_need():
    world = runner_facts()
    world["recipes"][PACK] = {"ingredients": [{"name": "copper-plate", "amount": 1}], "product_amount": 1, "energy_ticks": 300}
    board = compile_status(world, {PACK: 0.5})
    candidate = external()
    menu = compile_menu(world, board, validate_for({"optimizer.a": candidate["bill"]}), 2,
                        additional_candidates=[candidate], external_check=passing)
    assert [o["need_id"] for o in menu["options"] if o["family"] == "optimizer-block"] == [f"item:{PACK}"]


@pytest.mark.parametrize("check,reason", [
    (lambda c, e: ("attachment_grid_merge", None), "attachment_grid_merge"),
    (lambda c, e: (None, {"fluid_purity": {"fluid_entities": 9}, "safe_infrastructure_attachment": {"anchor_unit": 1234}}),
     "required_check_facts_changed"),
])
def test_blocks_failing_or_changing_a_check_are_not_selectable_and_say_why(check, reason):
    candidate = external()
    board = compile_status(runner_facts(), phase("C1")["targets"])
    menu = compile_menu(runner_facts(), board, validate_for({"optimizer.a": candidate["bill"]}), 5,
                        additional_candidates=[candidate], external_check=check)
    assert not any(o["family"] == "optimizer-block" for o in menu["options"])
    assert menu["additional_candidate_results"][0]["reason"] == reason
    assert menu["blocked"][f"optimizer:{PACK}"] == {reason: 1}


def test_land_reserved_for_another_purpose_stays_a_hard_rule_for_blocks():
    plan = {"version": "laya-site-plan-v1", "mall_site": None, "zones": [
        {"zone_id": "z", "purpose": "ore:iron-ore", "rect": [-2, -2, 2, 2]}]}
    candidate = external()
    board = compile_status(runner_facts(), phase("C1")["targets"])
    menu = compile_menu(runner_facts(), board, validate_for({"optimizer.a": candidate["bill"]}), 5, site_plan=plan,
                        additional_candidates=[candidate], external_check=passing)
    assert not any(o["family"] == "optimizer-block" for o in menu["options"])
    assert menu["additional_candidate_results"][0]["reason"].startswith("reserved:")


# --------------------------------------------------------------------------- composition (shared module + manager)

def wf(**kwargs):
    """World facts with the pack recipe enabled."""
    return {**world_facts(**kwargs), "enabled_recipes": {PACK: True}}


def make_world(adapter, root, facts=None, *, options="pass", entities=()):
    world = {"facts": facts or wf()}
    paused = []
    def handler(text):
        if "find_entities_filtered{area" in text:
            return {"tick": world["facts"]["tick"], "entities": list(entities)}
        raise AssertionError("unexpected Lua command")
    adapter.bridge = FakeBridge(root / "server/script-output", handler)
    adapter._check_instance = lambda: None
    adapter.query = lambda candidates=None, inspect=None: copy.deepcopy(world["facts"])
    def stub_options(facts, seed=0, *, additional_candidates=(), check_verifier=None):
        if options == "reject":
            option = additional_candidates[0]["option_id"]
            return [], {"validations": {option: {"reason": "collision", "required_check_reason": "attachment_collision"}}}
        legal = [{**c, "check_evidence": copy.deepcopy(EVIDENCE)} for c in additional_candidates]
        return legal, {"proposed": legal, "validations": {}}
    adapter.options = stub_options
    @contextmanager
    def clock_paused():
        paused.append("enter")
        yield 100
        paused.append("exit")
    adapter.clock_paused = clock_paused
    adapter.paused_log = paused
    return world


@pytest.fixture
def manager(chain, monkeypatch):
    adapter, ctx = chain
    monkeypatch.setattr(proposals, "verify_disposable", lambda root: (Path(root), {"production_authority": False}))
    blocks = rt.OptimizerBlocks(ctx.root, run_id="run-1")
    blocks._base = ctx
    return adapter, ctx, blocks


def test_manager_offers_up_to_three_disjoint_sites_with_stable_identity_and_facts(manager):
    adapter, ctx, blocks = manager
    world = make_world(adapter, ctx.root)
    offered = blocks(adapter, world["facts"], {"needs": []})
    assert 1 < len(offered) <= 3 and adapter.paused_log == ["enter", "exit"]  # composed on a frozen clock
    footprints = [ctx.template.footprint(tuple(c["optimizer_facts"]["origin"])) for c in offered]
    assert all(not (a & b) for i, a in enumerate(footprints) for b in footprints[i + 1:])
    first = offered[0]["optimizer_facts"]
    assert first["expected_output_per_s"] == 0.2 and first["product"] == PACK
    assert first["operating_demand_per_s"] == {"copper-plate": 0.2, "iron-gear-wheel": 0.2}
    assert first["construction_bill"]["assembling-machine-1"] == 2 and first["network_shortage_vs_bill"] == {}
    assert first["held_reservations"]["assembling-machine-1"] == 2 and first["operating_inputs_in_network"]
    # The menu copy carries facts; the held pristine copy does not, and is what verification and execution use.
    held = blocks.offers[offered[0]["option_id"]]
    assert "optimizer_facts" not in held.candidate and held.candidate["family"] == "optimizer-block"
    ids = {c["optimizer_facts"]["project_id"]: blocks.offers[c["option_id"]].transaction_id for c in offered}
    again = blocks(adapter, world["facts"], {"needs": []})
    assert {c["optimizer_facts"]["project_id"]: blocks.offers[c["option_id"]].transaction_id for c in again} == ids


def test_manager_names_why_nothing_was_offered(manager):
    adapter, ctx, blocks = manager
    world = make_world(adapter, ctx.root, wf(items={k: v for k, v in world_facts()["networks"]["1"]["items"].items()
                                                     if k != "transport-belt"}))
    assert blocks(adapter, world["facts"], {"needs": []}) == [] and blocks.last["reason"] == "unfunded"
    assert blocks.last["detail"]["short"] == {"transport-belt": 14}
    make_world(adapter, ctx.root, options="reject")
    assert blocks(adapter, wf(), {"needs": []}) == [] and blocks.last["reason"] == "check_failed"
    assert blocks.last["detail"]["rejected"][0]["reasons"] == ["collision", "attachment_collision"]
    make_world(adapter, ctx.root, entities=[{"name": "rock", "box": [-80, -80, 80, 80]}])
    assert blocks(adapter, wf(), {"needs": []}) == [] and blocks.last["reason"] == "no_site"
    disabled = wf()
    disabled["enabled_recipes"] = {}
    make_world(adapter, ctx.root, disabled)
    assert blocks(adapter, disabled, {"needs": []}) == [] and blocks.last["reason"] == "recipe_not_enabled"
    satisfied = {"needs": [{"item": PACK, "condition": "satisfied"}]}
    assert blocks(adapter, world_facts(), satisfied) == [] and blocks.last["reason"] == "need_satisfied"


# --------------------------------------------------------------------------- selection and execution

def offer_one(manager):
    adapter, ctx, blocks = manager
    world = make_world(adapter, ctx.root)
    offered = blocks(adapter, world["facts"], {"needs": []})
    return adapter, ctx, blocks, offered[0]


def test_selection_runs_the_bound_candidate_inside_the_clock_boundary_after_a_durable_record(manager, monkeypatch):
    adapter, ctx, blocks, menu_copy = offer_one(manager)
    seen = {}
    def fake_execute(adapter_, bound, ledger, **kwargs):
        seen.update(kwargs, bound=bound, paused=list(adapter.paused_log),
                    registry=json.loads(blocks.path.read_text()))
        return {"before": {"tick": 100}, "submission": {"ok": True, "tick": 100}, "status": "construction_pending"}
    monkeypatch.setattr(rt, "execute_optimizer_candidate", fake_execute)
    tampered = {**menu_copy, "bill": {"transport-belt": 1}, "plan": {"phases": []}}
    receipt = blocks.execute(adapter, tampered, menu_copy["option_id"], 600)
    held = blocks.offers[menu_copy["option_id"]]
    assert seen["candidate"] is held.candidate and seen["bound"] is held.bound  # Not the caller's copy.
    assert seen["paused"] == ["enter", "exit", "enter"] and adapter.paused_log[-1] == "exit"  # Submitted inside the boundary.
    project = seen["registry"]["projects"][held.project_id]
    assert project["state"] == "selected" and project["transaction_id"] == held.transaction_id  # Durable before mutation.
    stored = blocks.registry["projects"][held.project_id]
    assert stored["state"] == "submitted" and stored["boundary"]["ok"] is True and receipt["status"] == "construction_pending"


def test_live_world_routes_optimizer_selection_to_the_capability_and_fails_closed_without_it(manager, monkeypatch):
    adapter, ctx, blocks, menu_copy = offer_one(manager)
    live = object.__new__(LiveWorld)
    live.optimizer_blocks = None
    with pytest.raises(SavedWorldError, match="not enabled"):
        live.execute(menu_copy["option_id"], [menu_copy], 300)
    live.optimizer_blocks = blocks
    calls = []
    monkeypatch.setattr(blocks, "execute", lambda *args: calls.append(args) or {"status": "ok"})
    assert live.execute(menu_copy["option_id"], [menu_copy], 300) == {"status": "ok"}
    assert calls[0][1:] == (menu_copy, menu_copy["option_id"], 300)
    # With a verifier (the capability's own inner call) the adapter's verified path runs instead.
    monkeypatch.setattr(SavedWorldAdapter, "execute", lambda self, *a, **k: ("verified", k.get("check_verifier")))
    assert live.execute(menu_copy["option_id"], [menu_copy], 300, check_verifier="V") == ("verified", "V")
    assert len(calls) == 1


class StubVerifier:
    outcome = None

    def __init__(self, *args, **kwargs):
        pass

    def prepare(self, candidate):
        return candidate

    def __call__(self, adapter, candidate, entry, response):
        return copy.deepcopy(StubVerifier.outcome)


def test_a_fact_changed_by_mutation_time_rejects_before_any_ghost_is_submitted(manager, monkeypatch):
    adapter, ctx, blocks, menu_copy = offer_one(manager)
    live = object.__new__(LiveWorld)
    live.__dict__.update(adapter.__dict__)
    live.optimizer_blocks = blocks
    live.validate_plan = lambda plan: None
    live.surface, live.force = "nauvis", "player"
    submitted = []
    class Bridge:
        def build_layout_correlated(self, *args, **kwargs):
            submitted.append(kwargs)
            return {"ok": True}
        def command(self, text):
            return "live"
    live.bridge = Bridge()
    live.advance = lambda ticks: None
    held = blocks.offers[menu_copy["option_id"]]
    live.query = lambda candidates=None, inspect=None: (
        {"tick": 100, "validations": {held.candidate["option_id"]: {"bill": held.candidate["bill"]}}}
        if candidates else wf())
    monkeypatch.setattr(integration, "OptimizerCheckVerifier", StubVerifier)
    monkeypatch.setattr(integration, "_revalidate_stock", lambda *a, **k: None)
    # The world's attachment grid moved between the option and the mutation.
    StubVerifier.outcome = {"fluid_purity": {"reason": None, "facts": EVIDENCE["fluid_purity"]},
                            "safe_infrastructure_attachment": {"reason": None, "facts": {"anchor_unit": 9999}}}
    with pytest.raises(SavedWorldError, match="required_check_facts_changed:safe_infrastructure_attachment"):
        live.execute(menu_copy["option_id"], [menu_copy], 300)
    assert submitted == []
    receipts = [json.loads(p.read_text()) for p in (ctx.root / "evidence").glob("transaction-*.json")]
    assert [r["status"] for r in receipts] == ["rejected"]
    project = blocks.registry["projects"][held.project_id]
    assert project["state"] == "submitted" and blocks.projects("selected", "submitted")  # Receipt exists: reconcile decides.
    blocks.reconcile(live, {"tick": 200})
    assert blocks.registry["projects"][held.project_id]["state"] == "dispatch_failed"
    assert blocks.drain_events()[-1]["stage"] == "dispatch_failed"
    assert blocks.blocking_reason() is None  # Resolved by a pre-placement rejection; the origin is not re-offered.


def test_a_boundary_mismatch_halts_all_further_optimizer_submissions(manager, monkeypatch):
    adapter, ctx, blocks, menu_copy = offer_one(manager)
    monkeypatch.setattr(rt, "execute_optimizer_candidate", lambda *a, **k: {
        "before": {"tick": 100}, "submission": {"ok": True, "tick": 101}, "status": "construction_pending"})
    blocks.execute(adapter, menu_copy, menu_copy["option_id"], 600)
    assert blocks.halted == "boundary_violation"
    assert blocks(adapter, wf(), {"needs": []}) == [] and blocks.last["reason"] == "boundary_violation"


# --------------------------------------------------------------------------- double dispatch

def test_an_in_flight_block_is_never_reoffered_or_resubmitted(manager, monkeypatch):
    adapter, ctx, blocks, menu_copy = offer_one(manager)
    calls = []
    monkeypatch.setattr(rt, "execute_optimizer_candidate", lambda *a, **k: calls.append(1) or {
        "before": {"tick": 100}, "submission": {"ok": True, "tick": 100}, "status": "construction_pending"})
    blocks.execute(adapter, menu_copy, menu_copy["option_id"], 600)
    assert blocks(adapter, wf(), {"needs": []}) == [] and blocks.last["reason"] == "in_flight"
    with pytest.raises(SavedWorldError, match="blocked: in_flight"):
        blocks.execute(adapter, menu_copy, menu_copy["option_id"], 600)
    assert calls == [1]


def write_receipt(root, option_id, status, **extra):
    path = root / "evidence" / "transaction-r.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"status": status, "candidate": {"option_id": option_id, "plan": extra.pop("plan", {})}, **extra}))
    return path


def test_recovery_reads_receipts_and_never_resends(manager, monkeypatch):
    adapter, ctx, blocks, menu_copy = offer_one(manager)
    monkeypatch.setattr(rt, "execute_optimizer_candidate", lambda *a, **k: pytest.fail("recovery must not resend"))
    held = blocks.offers[menu_copy["option_id"]]
    blocks.registry["projects"][held.project_id] = {"project_id": held.project_id, "origin": [0, 0], "state": "selected",
                                                    "option_id": menu_copy["option_id"]}
    blocks._save()
    sent = len(adapter.bridge.commands)
    # Restart with no receipt: ambiguous, so it is unresolved and blocks everything; nothing is sent.
    restarted = rt.OptimizerBlocks(ctx.root, run_id="run-2")
    restarted.reconcile(adapter, {"tick": 500})
    assert restarted.registry["projects"][held.project_id]["state"] == "unresolved"
    assert restarted(adapter, wf(), {"needs": []}) == [] and restarted.last["reason"] == "unresolved_transaction"
    assert len(adapter.bridge.commands) == sent
    # A receipt that is still pending construction keeps the project submitted (in flight), unresolved receipts block too.
    restarted.registry["projects"][held.project_id]["state"] = "selected"
    write_receipt(ctx.root, menu_copy["option_id"], "construction_pending")
    restarted.reconcile(adapter, {"tick": 600})
    assert restarted.registry["projects"][held.project_id]["state"] == "submitted"
    assert restarted.blocking_reason() == "in_flight"
    # A pre-placement rejection resolves the transaction; the origin is never offered again.
    write_receipt(ctx.root, menu_copy["option_id"], "rejected", reason="atomic_footprint_blocked")
    restarted.reconcile(adapter, {"tick": 700})
    assert restarted.registry["projects"][held.project_id]["state"] == "dispatch_failed"
    assert restarted.blocking_reason() is None
    reoffered = restarted(adapter, wf(), {"needs": []})
    assert all(c["optimizer_facts"]["origin"] != [0, 0] for c in reoffered)
    assert not any("build_layout" in c for c in adapter.bridge.commands)  # Only read-only survey commands.


# --------------------------------------------------------------------------- construction, measurement, funnel

def block_rows(plan, crafts, *, requests=True, found=True):
    rows = []
    for spec in proposals.state_specs(plan):
        row = {"name": spec["name"], "x": spec["x"], "y": spec["y"], "found": found, "status": "working"}
        if spec["name"] == "assembling-machine-1":
            row.update(recipe=PACK, products_finished=crafts)
        if spec["name"] == "requester-chest" and requests:
            row["requests"] = [{"name": n, "count": c} for n, c in proposals.requested_items(plan)[(spec["x"], spec["y"])].items()]
        rows.append(row)
    return rows


def test_measurement_is_the_blocks_own_products_finished_over_a_complete_window(manager):
    adapter, ctx, blocks, menu_copy = offer_one(manager)
    held = blocks.offers[menu_copy["option_id"]]
    plan = held.candidate["plan"]
    clock, crafts = {"tick": 1000}, {"n": 5}
    adapter.bridge.handler = lambda text: {"tick": clock["tick"], "rows": block_rows(plan, crafts["n"])}
    receipt = write_receipt(ctx.root, held.candidate["option_id"], "observed", plan=plan,
                            configuration={"ok": True}, construction={"pending": 0, "missing": 0})
    blocks.registry["projects"][held.project_id] = {"project_id": held.project_id, "origin": [0, 0], "state": "submitted",
                                                    "option_id": held.candidate["option_id"], "receipt": receipt.name,
                                                    "submitted_tick": 900}
    huge_forcewide = {"tick": 1000, "production": {PACK: 99999}}
    blocks.reconcile(adapter, huge_forcewide)
    project = blocks.registry["projects"][held.project_id]
    assert project["state"] == "configured" and project["configured_tick"] == 1000
    clock["tick"] = 1000 + 1799  # Warm-up incomplete: no baseline yet.
    blocks.reconcile(adapter, {"tick": clock["tick"]})
    assert project["state"] == "configured"
    clock["tick"] = 1000 + 1800
    crafts["n"] = 20
    blocks.reconcile(adapter, {"tick": clock["tick"]})
    assert project["state"] == "measuring" and project["baseline"] == {"tick": 2800, "crafts": 40}  # 2 assemblers x 20
    clock["tick"] = 2800 + 3599
    blocks.reconcile(adapter, {"tick": clock["tick"]})
    assert project["state"] == "measuring"  # The window is not complete yet.
    clock["tick"] = 2800 + 3600
    crafts["n"] = 56  # 36 more crafts per assembler -> 72 packs in 3600 ticks = 1.2/s? (counter, not rate)
    blocks.reconcile(adapter, {"tick": clock["tick"], "production": {PACK: 99999}})
    measurement = project["measurement"]
    assert project["state"] == "measured" and measurement["packs"] == 72
    assert measurement["per_second"] == pytest.approx(72 / 60) and measurement["window_ticks"] == 3600
    assert measurement["ratio"] == pytest.approx(72 / 60 / 0.2) and measurement["source"].startswith("products_finished")
    stages = [e["stage"] for e in blocks.drain_events()]
    assert stages == ["configured", "measured"] and blocks.registry["funnel"]["measured"] == 1


def test_unconfirmed_configuration_or_missing_assemblers_never_produce_a_measurement(manager):
    adapter, ctx, blocks, menu_copy = offer_one(manager)
    held = blocks.offers[menu_copy["option_id"]]
    plan = held.candidate["plan"]
    receipt = write_receipt(ctx.root, held.candidate["option_id"], "observed", plan=plan, configuration={"ok": True})
    project = {"project_id": held.project_id, "origin": [0, 0], "state": "submitted", "option_id": held.candidate["option_id"],
               "receipt": receipt.name, "submitted_tick": 0}
    blocks.registry["projects"][held.project_id] = project
    adapter.bridge.handler = lambda text: {"tick": 10, "rows": block_rows(plan, 0, requests=False)}
    blocks.reconcile(adapter, {"tick": 10})
    assert project["state"] == "submitted"  # Unreadable read-back is unknown, not configured.
    adapter.bridge.handler = lambda text: {"tick": 10, "rows": block_rows(plan, 0, found=False)}
    project["state"], project["configured_tick"] = "configured", -5000
    blocks.reconcile(adapter, {"tick": 10})
    assert project["state"] == "measured" and project["measurement"]["status"] == "invalid"
    assert blocks.registry["funnel"].get("measured", 0) == 0


def test_construction_incomplete_is_journaled_once_while_the_block_stays_in_flight(manager):
    adapter, ctx, blocks, menu_copy = offer_one(manager)
    held = blocks.offers[menu_copy["option_id"]]
    write_receipt(ctx.root, held.candidate["option_id"], "construction_pending")
    blocks.registry["projects"][held.project_id] = {"project_id": held.project_id, "origin": [0, 0], "state": "submitted",
                                                    "option_id": held.candidate["option_id"], "submitted_tick": 100}
    blocks.reconcile(adapter, {"tick": 100 + 7201})
    blocks.reconcile(adapter, {"tick": 100 + 9000})
    events = blocks.drain_events()
    assert [e["stage"] for e in events] == ["construction_incomplete"] and events[0]["elapsed_ticks"] == 7201
    assert blocks.blocking_reason() == "in_flight"


def test_observe_decision_records_each_funnel_stage(manager):
    adapter, ctx, blocks = manager
    option = {"option_id": "optimizer.x", "family": "optimizer-block"}
    wait = {"option_id": "wait.300", "family": "wait"}
    blocks.last = {"reason": "unfunded", "detail": {"short": {"transport-belt": 3}}}
    blocks.observe_decision(index=1, tick=10, menu={"options": [wait]}, chosen_id="wait.300")
    blocks.observe_decision(index=2, tick=20, menu={"options": [option, wait]}, chosen_id="wait.300")
    blocks.observe_decision(index=3, tick=30, menu={"options": [option, wait]}, chosen_id="optimizer.x")
    stages = blocks.drain_events()
    assert [(e["stage"], e.get("reason")) for e in stages] == [
        ("never_offered", "unfunded"), ("offered_not_selected", None), ("selected", None)]
    assert stages[0]["detail"] == {"short": {"transport-belt": 3}}
    assert blocks.registry["funnel"]["never_offered"] == 1 and blocks.registry["never_offered_reasons"] == {"unfunded": 1}
    assert json.loads(blocks.path.read_text())["funnel"]["selected"] == 1


# --------------------------------------------------------------------------- the runner loop wiring

class Provider:
    """The capability's runner-facing surface, with canned offers; proves loop wiring and journaling."""
    observe_unlinked = True

    def __init__(self, candidate):
        self.candidate, self.events, self.executed = candidate, [], []
        self.registry = {"funnel": {"selected": 0}, "never_offered_reasons": {}, "projects": {}}

    def __call__(self, adapter, facts, board):
        return [self.candidate]

    def set_land_check(self, check):
        self.land = check

    def prepare_validation(self, candidates):
        return list(candidates)

    def verify(self, adapter, candidate, entry, response):
        return {name: {"reason": None, "facts": EVIDENCE[name]} for name in CHECKS}

    def reconcile(self, adapter, facts):
        self.events.append({"event": "optimizer_funnel", "stage": "configured", "tick": facts["tick"]})

    def observe_decision(self, *, index, tick, menu, chosen_id):
        shown = [o["option_id"] for o in menu["options"] if o["family"] == "optimizer-block"]
        self.events.append({"event": "optimizer_funnel", "index": index, "stage": "selected" if chosen_id in shown else "offered_not_selected"})
        self.registry["funnel"]["selected"] += chosen_id in shown

    def drain_events(self):
        events, self.events = self.events, []
        return events

    def execute(self, *args):
        raise AssertionError("the loop calls adapter.execute; the adapter routes to the capability")


def test_the_loop_offers_verified_blocks_and_journals_the_funnel(tmp_path):
    from tests.test_laya_clean_runner import FakeAdapter
    candidate = external("optimizer.loop", x=30.5, evidence=False)
    class Picks:
        authority, model_ref = "test", ExplorationPolicy.model_ref
        def choose(self, state, menu):
            options = [o["option_id"] for o in menu["options"]]
            pick = next((o for o in options if o.startswith("optimizer.")), options[0])
            need = next(n["need_id"] for n in menu["needs"] if pick in n["options"])
            return {"option_id": pick, "need_id": need, "probability": 1.0, "rules": []}
    class Adapter(FakeAdapter):
        def query(self, candidates=None):
            if candidates is not None:
                return {"tick": 100, "validations": {c["option_id"]: (
                    {"reason": None, "bill": candidate["bill"]} if c["option_id"] == "optimizer.loop"
                    else validate_for({})(  [c])[c["option_id"]]) for c in candidates}}
            return super().query(candidates)
    provider = Provider(candidate)
    journal = tmp_path / "j.jsonl"
    summary = run(Adapter(), Picks(), journal=journal, decisions=3, measure_ticks=300, seed=0, run_id="t",
                  candidate_provider=provider)
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    assert {"run_id": "t", "event": "capability", "optimizer_blocks": True} in rows
    stages = [r["stage"] for r in rows if r.get("event") == "optimizer_funnel"]
    assert "selected" in stages and "configured" in stages
    decision = next(r for r in rows if r.get("status") == "pending" and r["choice"]["option_id"] == "optimizer.loop")
    assert decision["additional_candidate_results"][0]["selectable"] is True
    assert summary["optimizer_blocks"]["funnel"]["selected"] >= 1
