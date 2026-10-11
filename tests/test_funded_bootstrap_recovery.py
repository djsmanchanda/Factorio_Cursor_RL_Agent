# Path: tests/test_funded_bootstrap_recovery.py | Purpose: Protect finite mall recovery without sacrificing precursor supply or other projects' stock.
from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as b, live_base
from orchestrator.mall_bootstrap import MallBootstrapLoan, active_bootstrap_loans
from orchestrator.material_reservations import MaterialReservationLedger

pytestmark = pytest.mark.fast


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    ledger = MaterialReservationLedger(tmp_path / "script-output", episode_id="recovery", surface="nauvis", force="player")
    stock = {"electronic-circuit": 69, "iron-gear-wheel": 100, "iron-plate": 4800}
    monkeypatch.setattr(b, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setattr(b, "_BLOCKING_MALL_ITEMS", set())
    monkeypatch.setattr(b.live_base, "available_items", lambda *_: dict(stock))
    monkeypatch.setattr(b.live_base, "transferable_items", lambda *_: dict(stock))
    monkeypatch.setattr(b.live_base, "chest_contents", lambda *_: {})
    monkeypatch.setattr(b.live_base, "machine_input_contents", lambda *_: {})
    monkeypatch.setattr(b, "active_bootstrap_loans", lambda *_: [])
    line = live_base.LineState("electronic-circuit", 1, 0, (42.5, 39.5), ((45.5, 38.5),))
    monkeypatch.setattr(b.live_base, "find_line", lambda _c, _s, _f, recipe, *_a, **_k:
                        line if recipe == "electronic-circuit" else None)
    monkeypatch.setattr(b.live_base, "entity_at", lambda _c, _s, point:
                        {"name": "assembling-machine-1" if point == (45.5, 38.5)
                         else "requester-chest" if point == (42.5, 38.5)
                         else "passive-provider-chest"})
    monkeypatch.setattr(b, "mall_slot_uses_shared_provider", lambda *_: False)
    monkeypatch.setattr(b, "_bootstrap_loan_products_finished", lambda *_: 0)
    monkeypatch.setattr(b, "_missing_chemical_ladder_predecessor", lambda *_: None)
    plans = []
    monkeypatch.setattr(b, "_submit", lambda _c, _br, _s, plan, *_a, **_k: plans.append(plan))
    return SimpleNamespace(ledger=ledger, stock=stock, plans=plans,
                           client=SimpleNamespace(command=lambda _: "NONE"))


def start(recovery):
    return b._start_bootstrap_loan(recovery.client, object(), "nauvis", "player", "inserter", 8,
                                   (0, 0), lambda _: None, spare_target_count=10,
                                   allowed_original_recipes=frozenset({"electronic-circuit"}))


def test_funded_inserter_batch_borrows_and_restores_the_sole_circuit_cell(recovery, monkeypatch):
    assert start(recovery) is not None
    machine = recovery.plans[-1]["phases"][0]["actions"][1]
    assert machine["recipe"] == "inserter"
    assert machine["position"] == {"x": 45.5, "y": 38.5}
    # The requested spare ceiling is funded too; another project cannot spend its inputs.
    free = recovery.ledger.allocatable_stock(recovery.stock)
    assert recovery.stock["electronic-circuit"] - free["electronic-circuit"] >= 10
    current = MallBootstrapLoan(original_recipe="electronic-circuit", target_item="inserter",
                                target_count=8, spare_target_count=10, side="right",
                                requester_position=(42.5, 38.5), current_recipe="inserter",
                                step_recipe="inserter", step_target_count=10,
                                step_baseline_finished=0, step_required_crafts=10, step_minimum_crafts=8,
                                funded_batch=True)
    # Bots consumed every output immediately; completed crafts still end this finite loan.
    monkeypatch.setattr(b, "_bootstrap_loan_products_finished", lambda *_: 10)
    b._submit_bootstrap_loan(recovery.client, object(), "nauvis", "player", current, lambda _: None)
    restored = recovery.plans[-1]["phases"][0]["actions"][1]
    assert restored["recipe"] == "electronic-circuit"
    assert recovery.ledger.allocatable_stock(recovery.stock) == recovery.stock


def test_funded_loan_recovers_its_finite_budget_from_the_installed_tag(recovery):
    assert start(recovery) is not None
    requester = recovery.plans[-1]["phases"][0]["actions"][0]
    group = requester["logistic_sections"][0]["group"]
    client = SimpleNamespace(command=lambda _: group + "|42.5|38.5|assembling-machine-1,copper-cable|assembling-machine-1,inserter")
    current, = active_bootstrap_loans(client, "nauvis", "player")
    assert current.funded_batch is True
    assert current.group == group
    assert current.production_target == 10
    assert current.step_recipe == "inserter"
    assert current.step_required_crafts == 10
    assert current.original_recipe == "electronic-circuit"


def test_interrupted_configuration_can_resume_its_own_input_reservation(recovery, monkeypatch):
    recovery.stock.update({"electronic-circuit": 12, "iron-gear-wheel": 12, "iron-plate": 12})
    def fail(*_a, **_k):
        raise RuntimeError("transport interrupted before configuration")
    monkeypatch.setattr(b, "_submit", fail)
    with pytest.raises(RuntimeError, match="transport interrupted"):
        start(recovery)
    assert recovery.ledger.allocatable_stock(recovery.stock) == {
        "electronic-circuit": 0, "iron-gear-wheel": 0, "iron-plate": 0}
    monkeypatch.setattr(b, "_submit", lambda _c, _br, _s, plan, *_a, **_k: recovery.plans.append(plan))
    assert start(recovery) is not None
    assert recovery.plans[-1]["phases"][0]["actions"][1]["recipe"] == "inserter"


@pytest.mark.parametrize("transferable,held", [(0, 6), (2, 4)])
def test_funded_batch_counts_own_inputs_and_remaining_crafts_after_delivery(recovery, monkeypatch, transferable, held):
    assert start(recovery) is not None
    current = MallBootstrapLoan(original_recipe="electronic-circuit", target_item="inserter",
                                target_count=8, spare_target_count=10, side="right",
                                requester_position=(42.5, 38.5), current_recipe="inserter",
                                step_recipe="inserter", step_target_count=10,
                                step_baseline_finished=0, step_required_crafts=10, step_minimum_crafts=8,
                                funded_batch=True)
    recovery.stock.clear()
    if transferable:
        recovery.stock.update({item: transferable for item in ("electronic-circuit", "iron-gear-wheel", "iron-plate")})
    monkeypatch.setattr(b.live_base, "chest_contents", lambda *_:
                        {"electronic-circuit": held - 1, "iron-gear-wheel": held - 1, "iron-plate": held - 1})
    monkeypatch.setattr(b.live_base, "machine_input_contents", lambda *_:
                        {"electronic-circuit": 1, "iron-gear-wheel": 1, "iron-plate": 1})
    monkeypatch.setattr(b, "_bootstrap_loan_products_finished", lambda *_: 5)
    monkeypatch.setattr(b, "_emit_loan_cell_telemetry", lambda *_a, **_k: None)
    monkeypatch.setattr(b, "_deliver_cell_ingredients", lambda *_a, **_k: None)
    monkeypatch.setattr(b.live_base, "entity_status_name", lambda *_: "working")
    monkeypatch.setattr(b.time, "sleep", lambda _: None)
    before = len(recovery.plans)
    b._submit_bootstrap_loan(recovery.client, object(), "nauvis", "player", current, lambda _: None)
    assert len(recovery.plans) == before  # Neither restore nor recursive precursor retarget.
    assert recovery.ledger.stock_targets() == recovery.stock  # No second copy of the cell's inputs.


@pytest.mark.parametrize("missing", ["electronic-circuit", "iron-gear-wheel", "iron-plate"])
def test_sole_precursor_loan_requires_the_complete_batch(recovery, missing):
    recovery.stock[missing] = 1
    assert start(recovery) is None
    assert not recovery.plans


def test_another_requester_or_project_cannot_fund_the_precursor_loan(recovery, monkeypatch):
    # Broad stock sees 69 circuits, but only one is transferable.
    monkeypatch.setattr(b.live_base, "transferable_items", lambda *_: {**recovery.stock, "electronic-circuit": 1})
    assert start(recovery) is None
    monkeypatch.setattr(b.live_base, "transferable_items", lambda *_: dict(recovery.stock))
    recovery.ledger.declare("peer", {"electronic-circuit": 65}, recovery.stock, hold_until_producing=True)
    recovery.ledger.mark_constructing("peer", recovery.stock)
    assert start(recovery) is None
    assert not recovery.plans


def test_lost_funded_inputs_restore_the_precursor_instead_of_recursing(recovery, monkeypatch):
    assert start(recovery) is not None
    current = MallBootstrapLoan(original_recipe="electronic-circuit", target_item="inserter",
                                target_count=8, spare_target_count=10, side="right",
                                requester_position=(42.5, 38.5), current_recipe="inserter",
                                step_recipe="inserter", step_target_count=10,
                                step_baseline_finished=0, step_required_crafts=10, step_minimum_crafts=8,
                                funded_batch=True)
    recovery.stock.clear()
    b._submit_bootstrap_loan(recovery.client, object(), "nauvis", "player", current, lambda _: None)
    assert recovery.plans[-1]["phases"][0]["actions"][1]["recipe"] == "electronic-circuit"


def test_cell_pipeline_readiness_cannot_use_another_projects_inserters(tmp_path, monkeypatch):
    ledger = MaterialReservationLedger(tmp_path / "script-output", episode_id="pipeline", surface="nauvis", force="player")
    stock = {"assembling-machine-1": 1, "iron-plate": 1, "inserter": 4}
    ledger.declare("existing-build", {"inserter": 4}, stock, hold_until_producing=True)
    ledger.mark_constructing("existing-build", stock)
    monkeypatch.setattr(b, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setattr(b, "compact_mall_project_bill", lambda *_a, **_k:
                        {"assembling-machine-1": 1, "iron-plate": 1, "inserter": 2})
    monkeypatch.setattr(b, "_material_sources_and_rates", lambda *_: ({}, {}))
    monkeypatch.setattr(b.live_base, "transferable_items", lambda *_: stock)
    monkeypatch.setattr(b, "construction_supply_chain_is_scheduled", lambda *_: False)
    plan = SimpleNamespace(spec={}, mall_storage_limit=1, fill_provider=False,
                           mall_request_multiplier=None, shared_provider=False)
    with pytest.raises(b.MaterialShortage):
        b._reserve_compact_mall_project(object(), "nauvis", "player", "fast-inserter", plan,
                                        None, lambda _: None)
