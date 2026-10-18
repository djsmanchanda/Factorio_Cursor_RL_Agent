# Path: tests/test_laya_ops_economics.py | Purpose: Verify read-only mall economics, evidence gaps, ownership and bounded decision context.

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as ab
from orchestrator import mall_bootstrap
from orchestrator.mall_bootstrap import MallBootstrapLoan
from training.laya_ops import DeterministicSession
from training.laya_ops_runner import brief, economics_observation, run
from training.laya_guided_policy import GuidedPolicy


def unavailable(*args, **kwargs):
    raise RuntimeError("survey unavailable")


@pytest.fixture
def session(monkeypatch):
    session = DeterministicSession.__new__(DeterministicSession)
    session.client = object()
    session.reference_point = (0, 0)
    monkeypatch.setattr(ab, "_MATERIAL_RESERVATION_LEDGER", SimpleNamespace(projects={}, _active=lambda p: p.state == "ready"))
    monkeypatch.setattr(ab, "_independent_mall_ready", lambda *a: False)
    monkeypatch.setattr(ab, "_production_started", lambda *a: False)
    monkeypatch.setattr(ab, "_dense_bootstrap_mall_enabled", lambda *a: False)
    monkeypatch.setattr(ab, "mall_slot_count", lambda *a: 4)
    monkeypatch.setattr(mall_bootstrap, "active_bootstrap_loans", lambda *a: ())
    monkeypatch.setattr(ab.live_base, "machine_health", lambda *a: ({}, {}))
    return session


def test_free_capacity_is_not_material_or_borrower_capacity(session):
    facts = economics_observation(session.economics(), {"n": {"items": {}}})
    assert facts["slots"] == {"capacity": 8, "occupied": 4, "free": 4, "unlimited": False,
                              "scope": "bootstrap", "includes_ghosts": True}
    assert facts["stock"]["unreserved"] == {}
    assert facts["loans"] == [] and facts["projects"] == {}
    text = brief([], None, None, {}, 0, economics=facts)
    assert "capacity=8 occupied=4 free=4" in text
    assert "materials/borrowability separate" in text
    assert "iron-plate network=0 reserved=0 unreserved=0" in text
    assert "borrowable" not in facts["slots"]


def test_failed_components_stay_unknown_and_do_not_erase_other_facts(session, monkeypatch):
    monkeypatch.setattr(ab, "mall_slot_count", unavailable)
    monkeypatch.setattr(mall_bootstrap, "active_bootstrap_loans", unavailable)
    monkeypatch.setattr(ab, "_MATERIAL_RESERVATION_LEDGER", None)
    facts = economics_observation(session.economics(), None)
    assert facts["slots"]["capacity"] == 8
    assert facts["slots"]["occupied"] is None and facts["slots"]["free"] is None
    assert facts["loans"] is None and facts["projects"] is None
    assert facts["stock"]["network"] is None and facts["stock"]["unreserved"] is None
    text = brief([], None, None, {}, 0, economics=facts)
    assert "loans unknown" in text and "owners unknown" in text
    assert "iron-plate network=? reserved=? unreserved=?" in text


def test_cap_failure_is_unknown_instead_of_default_eight(session, monkeypatch):
    monkeypatch.setattr(ab, "_production_started", unavailable)
    slots = session.economics()["slots"]
    assert slots["capacity"] is None and slots["unlimited"] is None
    assert slots["occupied"] == 4 and slots["free"] is None


def test_unlimited_is_distinct_from_failed_cap(session, monkeypatch):
    monkeypatch.setattr(ab, "_independent_mall_ready", lambda *a: True)
    monkeypatch.setattr(ab, "mall_demand_slot_count", lambda *a: 10)
    slots = session.economics()["slots"]
    assert slots["scope"] == "demand" and slots["unlimited"] is True
    assert slots["capacity"] is None and slots["free"] is None and slots["occupied"] == 10


def test_fully_reserved_projects_retain_ownership_without_rebalancing(session, monkeypatch):
    project = SimpleNamespace(project_id="paired_mall_gear", target_item="iron-gear-wheel", state="ready",
                              required={"assembling-machine-1": 2, "iron-plate": 20},
                              reserved={"assembling-machine-1": 2, "iron-plate": 20})
    ab._MATERIAL_RESERVATION_LEDGER.projects[project.project_id] = project
    before = deepcopy(project.__dict__)
    facts = economics_observation(session.economics(), {"a": {"items": {"iron-plate": 12}},
                                                       "b": {"items": {"iron-plate": 8, "copper-plate": 7}}})
    assert facts["stock"]["network"] == {"iron-plate": 20, "copper-plate": 7}
    assert facts["stock"]["unreserved"]["iron-plate"] == 0
    assert facts["projects"][project.project_id]["missing"] == {}
    text = brief([], None, None, {}, 0, economics=facts)
    assert "paired_mall_gear ready missing none reserved assembling-machine-1 2, iron-plate 20" in text
    assert project.__dict__ == before


@pytest.mark.parametrize("networks", [None, {"a": {}}, {"a": {"items": None}}])
def test_missing_network_item_survey_is_not_empty(networks):
    facts = economics_observation({"projects": {}, "loans": []}, networks)
    assert facts["stock"]["network"] is None
    assert facts["stock"]["unreserved"] is None


def test_loan_progress_separates_stock_crafts_and_durable_credits(session, monkeypatch):
    loan = MallBootstrapLoan("transport-belt", "inserter", 5, "left", (10, 10), "iron-gear-wheel",
                             spare_target_count=10, step_recipe="iron-gear-wheel", step_target_count=4,
                             step_baseline_finished=100, step_required_crafts=4,
                             completed_step_targets=(("copper-cable", 8),))
    monkeypatch.setattr(mall_bootstrap, "active_bootstrap_loans", lambda *a: (loan,))
    monkeypatch.setattr(ab.live_base, "machine_health", lambda *a: ({loan.machine_position: "working"},
                                                                  {loan.machine_position: 102000.5}))
    economics = session.economics()
    before = deepcopy(economics)
    facts = economics_observation(economics, {"a": {"items": {"inserter": 2}}})
    projected = facts["loans"][0]
    assert projected["owner"] == "paired_mall_inserter"
    assert projected["target"] == 5 and projected["production_target"] == 10
    assert projected["step_completed_crafts"] == 2 and projected["step_required_crafts"] == 4
    assert projected["network_stock"] == 2 and projected["remaining_to_target"] == 3
    assert projected["completed_step_targets"] == {"copper-cable": 8}
    assert economics == before  # no projection mutates the survey or ledger
    text = brief([], None, None, {}, 0, economics=facts)
    assert "network=2/5 remaining=3 crafts=2/4 status=working" in text
    monkeypatch.setattr(ab.live_base, "machine_health", unavailable)
    assert session.economics()["loans"][0]["step_completed_crafts"] is None
    unknown = economics_observation(economics, None)
    assert unknown["loans"][0]["network_stock"] is None
    assert "network=?/5 remaining=?" in brief([], None, None, {}, 0, economics=unknown)


@pytest.mark.parametrize("limit", [0, 40, 1000, 8000])
def test_brief_bounds_mark_omissions(limit):
    economics = {"projects": {f"owner-{i}": {"state": "ready", "missing": {}, "reserved": {"iron-plate": i}}
                              for i in range(100)},
                 "loans": [{"lender": "gear", "making": "drill", "target": 10} for _ in range(100)]}
    text = brief([], None, None, {}, 0, limit=limit, economics=economics)
    assert len(text) <= limit
    if limit >= 1000:
        assert "omitted" in text or "truncated" in text


def test_runner_records_the_economics_used_by_policy(tmp_path):
    class World:
        root = None
        tick = 0

        def query(self):
            return {"tick": self.tick, "production": {}, "consumption": {}, "machines": [],
                    "networks": {"n": {"items": {"iron-plate": 17}}}, "recipes": {}}

        def advance(self, ticks):
            self.tick += ticks

    class Session:
        def economics(self):
            return {"projects": {}, "loans": [], "slots": {"capacity": 8, "occupied": 4, "free": 4}}

        def run(self, operation, **params):
            return {"operation": operation, "params": params, "ok": True}

        def close(self):
            pass

    class Policy(GuidedPolicy):
        def choose(self, state, menu):
            facts = state["observation_digest"]["mall_economics"]
            assert facts["stock"]["network"]["iron-plate"] == 17
            assert "iron-plate network=17" in state["brief"]
            return super().choose(state, menu)

    run(World(), Policy(1), session=Session(), decisions=1, measure_ticks=600, seed=1, run_id="economics", journal=tmp_path / "journal.jsonl", emit=lambda s: None)
    row = json.loads((tmp_path / "decision-states.jsonl").read_text().splitlines()[0])
    assert row["state"]["observation_digest"]["mall_economics"]["slots"]["free"] == 4


def test_dense_capacity_and_empty_occupied_are_independent(session, monkeypatch):
    monkeypatch.setattr(ab, "_dense_bootstrap_mall_enabled", lambda *a: True)
    monkeypatch.setattr(ab, "mall_slot_count", lambda *a: 0)
    slots = session.economics()["slots"]
    assert slots["capacity"] == ab.DEMAND_MALL_SLOT_TARGET
    assert slots["occupied"] == 0 and slots["free"] == slots["capacity"]


def test_transaction_survey_leaves_the_live_ledger_unbalanced(tmp_path, monkeypatch):
    # Review 2026-10-18: the oil survey's allocatable_stock rebalanced the live ledger, so a stock-gate
    # preview and its pre-write recheck disagreed (stock_gate_snapshot_changed) whenever stock moved.
    from orchestrator.material_reservations import MaterialReservationLedger
    from orchestrator import oil_transaction
    ledger = MaterialReservationLedger(tmp_path, episode_id="episode", surface="nauvis", force="player")
    ledger.declare("conversion_steel-plate", {"inserter": 40}, {"inserter": 30})
    monkeypatch.setattr(ab, "_MATERIAL_RESERVATION_LEDGER", ledger)

    class Store:
        def material_facts(self, client, ledger):
            return {"available_to_owner": ledger.allocatable_stock({"inserter": 32})}
    monkeypatch.setattr(oil_transaction.OilTransactionStore, "for_bridge", classmethod(lambda cls, *a: Store()))
    session = DeterministicSession.__new__(DeterministicSession)
    session.client, session.bridge = object(), object()

    facts = session.material_transactions()

    assert facts["opening_oil"]["available_to_owner"] == {"inserter": 0}
    assert ledger.projects["conversion_steel-plate"].reserved == {"inserter": 30}


def test_submitted_raise_is_recorded_as_a_floor_finite_demand_cannot_lower(monkeypatch):
    from orchestrator import mall_control
    session = DeterministicSession.__new__(DeterministicSession)
    monkeypatch.setattr(ab, "_MALL_STOCK_GATE_FLOORS", {})
    monkeypatch.setattr(ab, "_RAISED_STOCK_GATE_FLOORS", {})

    def raised(item, preview, *, observe, submit, floors, submitted=True):
        floors[("nauvis", "player", item)] = 200
        return {"ok": True, "submitted": submitted}
    monkeypatch.setattr(mall_control, "execute_raise", raised)
    assert session._raise_stock_gate("inserter", {})["ok"]
    assert ab._RAISED_STOCK_GATE_FLOORS == {("nauvis", "player", "inserter"): 200}
    monkeypatch.setattr(mall_control, "execute_raise", lambda item, preview, **k: {"ok": False, "submitted": False})
    ab._MALL_STOCK_GATE_FLOORS[("nauvis", "player", "pipe")] = 30
    session._raise_stock_gate("pipe", {})
    assert ("nauvis", "player", "pipe") not in ab._RAISED_STOCK_GATE_FLOORS  # an unsubmitted raise is not a floor
