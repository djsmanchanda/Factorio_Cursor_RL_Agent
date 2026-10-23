# Path: tests/test_mall_reconciliation.py | Purpose: Protect selected loan identity, measured readiness, shared execution and truthful utilization.
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as ab
from orchestrator import mall_bootstrap as bootstrap
from orchestrator import mall_reconciliation as reconciliation
from orchestrator.operation_observation import assembler_utilization
from orchestrator.operation_session import DeterministicSession, receipt
from orchestrator.work_state import WorkStateSignal
from training.laya_ops_runner import with_economics

REAL_SUBMIT = ab._submit_bootstrap_loan


@pytest.fixture
def rig(monkeypatch):
    loan = bootstrap.MallBootstrapLoan(
        original_recipe="inserter", target_item="chemical-plant", target_count=2,
        side="right", requester_position=(36.5, 33.5), current_recipe="electronic-circuit",
        step_recipe="electronic-circuit", step_target_count=10, step_baseline_finished=50,
        step_required_crafts=10, step_minimum_crafts=2)
    active = [loan]
    health = {"counter": 164000, "status": "disabled_by_control_behavior"}
    monkeypatch.setattr(bootstrap, "active_bootstrap_loans", lambda *a: tuple(active))
    monkeypatch.setattr(ab.live_base, "machine_health", lambda *a: (
        {loan.machine_position: health["status"]}, {loan.machine_position: health["counter"]}))
    session = SimpleNamespace(client=object(), bridge=object(), emit=lambda *a: None, reference_point=(0, 0))
    calls = []

    def submit(*args, **kwargs):
        calls.append((args, kwargs))
        active[0] = replace(active[0], current_recipe="chemical-plant", step_recipe="chemical-plant",
                            step_baseline_finished=164, step_required_crafts=2)
        return "advanced existing loan"

    monkeypatch.setattr(ab, "_submit_bootstrap_loan", submit)
    return session, loan, active, health, calls


def test_recorded_gated_prerequisite_becomes_selected_work_without_a_new_bill(rig):
    session, loan, active, health, calls = rig
    economics = {"loans": reconciliation.observe_loans(session.client, "nauvis", "player")}
    fact = economics["loans"][0]
    assert fact["step_completed_crafts"] == 114 and fact["ready_reason"] == "prerequisite_finished"
    option, = reconciliation.options(economics)
    assert option["features"]["idle_ready_loan"] and option["bill"] == {}
    assert not calls  # Observation and compiling a menu never execute the transaction.
    menu = {"options": [], "needs": []}
    assert with_economics(menu, [], economics)["options"] == []
    menu = with_economics({"options": [], "needs": []}, [], economics, reconcile_loans=True)
    selected, = menu["options"]
    result = reconciliation.reconcile(session, **selected["params"])
    assert result["ok"] and result["code"] == "loan_reconciled" and result["configuration_changed"]
    assert len(calls) == 1 and calls[0][0][4] == loan
    assert active[0].current_recipe == "chemical-plant"
    assert receipt("reconcile_mall_loan", selected["params"], result)["code"] == "loan_reconciled"
    # It is not a producing receipt and never grants output credit.
    assert result.get("produced") is None and result.get("delivered") is None


@pytest.mark.parametrize("change", ["group", "recipe", "item", "position", "no_counter", "regressed_counter"])
def test_stale_or_unknown_loan_cannot_be_reconciled(rig, change):
    session, loan, active, health, calls = rig
    expected = reconciliation.identity(loan)
    if change == "group": active[0] = replace(loan, target_count=3)
    if change == "recipe": active[0] = replace(loan, current_recipe="iron-stick")
    if change == "item": active[0] = replace(loan, target_item="big-electric-pole")
    if change == "position": active[0] = replace(loan, requester_position=(40.5, 33.5))
    if change == "no_counter": health["counter"] = None
    if change == "regressed_counter": health["counter"] = 1000
    result = reconciliation.reconcile(session, item=loan.target_item, expected=expected)
    assert not result["ok"] and not result["attempted"] and not calls


def test_incomplete_final_and_unknown_steps_do_not_invent_completed_prerequisites(rig):
    session, loan, active, health, calls = rig
    health["counter"] = 59000
    assert reconciliation.options({"loans": reconciliation.observe_loans(session.client, "nauvis", "player")}) == []
    health["counter"] = 164000
    active[0] = replace(loan, target_item="electronic-circuit")
    assert reconciliation.options({"loans": reconciliation.observe_loans(session.client, "nauvis", "player")}) == []
    assert reconciliation.options({"loans": None}) == []


def test_only_selected_existing_loan_advances_and_pending_release_is_visible(rig):
    session, loan, active, health, calls = rig
    other = replace(loan, target_item="big-electric-pole", requester_position=(42.5, 38.5))
    active.append(other)
    result = reconciliation.reconcile(session, item=loan.target_item, expected=reconciliation.identity(loan))
    assert result["ok"] and active[1] == other and len(calls) == 1
    assert reconciliation.ready_reason({"releasing": True}) == "release_pending"


def test_shared_funding_signal_is_preserved_and_unknown_readback_is_not_success(rig, monkeypatch):
    session, loan, active, health, calls = rig

    def blocked(*a, **kw):
        raise WorkStateSignal("missing material", code="material_shortage", state="supply_wait",
                              classification="intended_difficulty", details={"missing": {"iron-plate": 2}})

    monkeypatch.setattr(ab, "_submit_bootstrap_loan", blocked)
    result = reconciliation.reconcile(session, item=loan.target_item, expected=reconciliation.identity(loan))
    assert result["code"] == "material_shortage" and result["attempted"] and not result["ok"]
    assert result["configuration_changed"] is None and result["details"]["missing"] == {"iron-plate": 2}

    lost = [False]

    def read_active(*a):
        if lost[0]:
            raise ValueError("lost")
        return tuple(active)

    monkeypatch.setattr(bootstrap, "active_bootstrap_loans", read_active)

    def readback_lost(*a, **kw):
        lost[0] = True

    monkeypatch.setattr(ab, "_submit_bootstrap_loan", readback_lost)
    result = reconciliation.reconcile(session, item=loan.target_item, expected=reconciliation.identity(loan))
    assert result["code"] == "loan_reconciliation_unverified" and result["attempted"] and not result["ok"]
    assert result["configuration_changed"] is None


def test_session_dispatch_preserves_selected_scope_and_skips_other_loan_upkeep(rig, monkeypatch):
    session, loan, active, health, calls = rig
    real = object.__new__(DeterministicSession)
    real.__dict__.update(session.__dict__)
    monkeypatch.setattr(real, "_maintain_mall", lambda: pytest.fail("unselected upkeep"))
    result = real.run("reconcile_mall_loan", item=loan.target_item, expected=reconciliation.identity(loan))
    assert result["ok"] and result["operation"] == "reconcile_mall_loan"


def test_real_shared_loan_planner_advances_completed_step_using_the_same_entities(rig, monkeypatch):
    session, loan, active, health, calls = rig
    from planners.recipe_data import LINE_RECIPES
    monkeypatch.setitem(LINE_RECIPES, "chemical-plant", {
        "machine": "assembling-machine-1", "ingredients": ["electronic-circuit", "iron-plate"],
        "amounts": [5, 5], "product_amount": 1, "craft_time": 1})
    stock = {"electronic-circuit": 0, "iron-plate": 100}
    monkeypatch.setattr(ab, "_bootstrap_loan_stock", lambda *a: (dict(stock), dict(stock)))
    monkeypatch.setattr(ab, "_bootstrap_loan_products_finished", lambda *a: 164)
    monkeypatch.setattr(ab, "_missing_chemical_ladder_predecessor", lambda *a: None)
    monkeypatch.setattr(ab, "_BOOTSTRAP_LOAN_PROGRESS_REVISION", 0)
    monkeypatch.setattr(ab, "_BOOTSTRAP_LOAN_CONFIGURATION_REVISION", 0)
    plans = []

    def apply_plan(client, bridge, surface, plan, name, emit):
        plans.append(plan)
        actions = plan["phases"][0]["actions"]
        requester = next(a for a in actions if a["entity"] == "requester-chest")
        group = requester["logistic_sections"][0]["group"]
        original, target, count, spare, side, step, step_target, baseline, required, minimum, completed = (
            bootstrap.parse_bootstrap_loan_group(group))
        active[0] = replace(loan, current_recipe=step, step_recipe=step, step_target_count=step_target,
                            step_baseline_finished=baseline, step_required_crafts=required,
                            step_minimum_crafts=minimum, completed_step_targets=completed, observed_group=group)

    monkeypatch.setattr(ab, "_submit", apply_plan)
    monkeypatch.setattr(ab, "_submit_bootstrap_loan", REAL_SUBMIT)
    result = reconciliation.reconcile(session, item=loan.target_item, expected=reconciliation.identity(loan))
    assert result["ok"] and active[0].current_recipe == "chemical-plant"
    assert active[0].completed_step_targets == (("electronic-circuit", 10),)
    assert len(plans) == 1
    assert {a["action_type"] for a in plans[0]["phases"][0]["actions"]} == {"configure_entity"}
    assert active[0].machine_position == loan.machine_position


def test_utilization_is_an_instantaneous_known_status_share_not_a_time_fraction():
    machines = [{"name": "assembling-machine-1", "status": "working"},
                {"name": "assembling-machine-2", "status": "disabled_by_control_behavior"},
                {"name": "assembling-machine-3", "status": "no_recipe"},
                {"name": "assembling-machine-1"}, {"name": "electric-furnace", "status": "working"}]
    result = assembler_utilization({"tick": 50, "machines": machines}, {"loans": None})
    assert result["assemblers"] == 4 and result["known_statuses"] == 3
    assert result["working_sample_share"] == 1 / 3 and result["ready_loans"] is None
    assert result["basis"] == "instantaneous_status_sample"
    assert assembler_utilization({}, {})["working_sample_share"] is None


def test_selected_reconciliation_receipt_survives_runner_journal_and_outcome_export(rig, tmp_path):
    from tools.export_laya_ops_data import export
    from training.laya_clean_runner import Settings
    from training.laya_guided_policy import GuidedPolicy
    from training.laya_ops_runner import run

    session, loan, active, health, calls = rig
    session.economics = lambda: {"loans": reconciliation.observe_loans(session.client, "nauvis", "player")}
    session.run = lambda operation, **params: receipt(operation, params, reconciliation.reconcile(session, **params))
    session.close = lambda: None

    class World:
        root = None
        extra_recipes = ()

        def query(self):
            return {"tick": 1000, "production": {}, "consumption": {}, "machines": [],
                    "networks": {}, "ports": [], "resources": [], "recipes": {}}

    class ReconcilePolicy(GuidedPolicy):
        def choose(self, state, menu):
            return super().choose(state, {**menu, "options": [o for o in menu["options"]
                                                              if o["family"] == "reconcile_mall_loan"]})

    world = tmp_path / "world"
    run(World(), ReconcilePolicy(2), session=session, journal=world / "evidence/clean-decisions.jsonl",
        decisions=1, measure_ticks=600, seed=2, run_id="reconciliation",
        settings=Settings(mall_loan_reconciliation=True))
    batch = tmp_path / "batch"
    batch.mkdir()
    arms = [{"id": "ready", "revision": "a" * 40}]
    (batch / "batch.json").write_text(json.dumps({"name": "reconciliation", "arms": arms, "seeds": [2]}))
    (batch / "freeze.json").write_text(json.dumps({"source_sha256": "c" * 64, "planned_jobs": 1, "arms": arms}))
    (batch / "job-status.json").write_text(json.dumps({"jobs": [
        {"id": "ready-2", "arm": "ready", "seed": 2, "world": str(world), "status": "completed"}]}))
    manifest = export([batch], tmp_path / "out")
    rows = [json.loads(line) for path in (tmp_path / "out").glob("*.jsonl") for line in path.read_text().splitlines()]
    assert not manifest["excluded"] and len(rows) == 1
    outcome = rows[0]["observed_outcome"]
    assert outcome["code"] == "loan_reconciled"
    assert outcome["reconciliation_receipt"]["attempted"] is True
    assert outcome["reconciliation_receipt"]["configuration_changed"] is True
    selected = next(o for o in rows[0]["options"] if o["option_id"] == rows[0]["choice"]["option_id"])
    assert selected["params"]["expected"] == reconciliation.identity(loan)
    assert len(calls) == 1
