# Path: tests/test_rc5_loan_evidence.py | Purpose: Verify loan observation and gate clock behavior.
from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as b
from tests.test_laya_loan_delivery import _loan
from orchestrator.loan_gate_retries import GateRetries, identity

pytestmark = pytest.mark.fast


@pytest.fixture(autouse=True)
def retry_store(tmp_path, monkeypatch):
    store = GateRetries(tmp_path / 'script-output', 'episode')
    monkeypatch.setattr(b, '_loan_gate_retry_store', lambda *_: store)
    return store


def test_local_inputs_are_distinct_from_global_stock(monkeypatch):
    loan = _loan(monkeypatch)
    step = SimpleNamespace(recipe="electronic-circuit")
    monkeypatch.setattr(b.live_base, "chest_contents", lambda *_: {})
    monkeypatch.setattr(b.live_base, "loan_cell_item_count", lambda *_: 0)
    first = b._loan_input_evidence(object(), "nauvis", "player", loan, step)
    monkeypatch.setattr(b.live_base, "loan_cell_item_count", lambda *_: 100)
    second = b._loan_input_evidence(object(), "nauvis", "player", loan, step)
    assert first.global_stock == second.global_stock
    assert first.fed is False and second.fed is True
    assert first.incoming is None and first.logistic_network_id is None


def _observed_loan(monkeypatch, *, constant=1, circuit_enabled=False):
    loan = _loan(monkeypatch)
    store = b._loan_gate_retry_store(None, None, None)
    store.start(identity(loan, SimpleNamespace(recipe='electronic-circuit', target_count=3)), 862, proposed=True)
    monkeypatch.setattr(b.live_base, "loan_cell_item_count", lambda *_: 100)
    monkeypatch.setattr(b.live_base, "mall_gate_survey", lambda *_: [{
        "position": loan.machine_position, "recipe": "electronic-circuit",
        "circuit_enabled": circuit_enabled,
        "gate": {"signal": "electronic-circuit", "comparator": "<", "constant": constant},
    }])
    monkeypatch.setattr(b, "_deliver_cell_ingredients", lambda *_: False)
    ticks = iter(range(10, 100))
    monkeypatch.setattr(b.live_base, "game_tick", lambda *_: next(ticks))
    return loan


@pytest.mark.parametrize("ticks", [(10, 10), (10, 9)])
def test_same_or_regressing_tick_never_refreshes(monkeypatch, ticks):
    loan = _observed_loan(monkeypatch)
    samples = iter(ticks)
    monkeypatch.setattr(b.live_base, "game_tick", lambda *_: next(samples))
    result = b._submit_bootstrap_loan(object(), object(), "nauvis", "player", loan, lambda *_: None)
    assert "waits for" in result
    assert all(row["attempts"] == 0 for row in b._loan_gate_retry_store(None, None, None).state["records"].values())


@pytest.mark.parametrize("constant", [True, None, "1", 1.5, float("nan"), float("inf"), 2 ** 32])
def test_invalid_gate_threshold_never_spends_retry(monkeypatch, constant):
    loan = _observed_loan(monkeypatch, constant=constant)
    result = b._submit_bootstrap_loan(object(), object(), "nauvis", "player", loan, lambda *_: None)
    assert "waits for" in result
    assert all(row["attempts"] == 0 for row in b._loan_gate_retry_store(None, None, None).state["records"].values())


@pytest.mark.parametrize("circuit_enabled", [True, None])
def test_unsupported_control_readback_never_spends_retry(monkeypatch, circuit_enabled):
    loan = _observed_loan(monkeypatch, circuit_enabled=circuit_enabled)
    b._submit_bootstrap_loan(object(), object(), "nauvis", "player", loan, lambda *_: None)
    assert all(row["attempts"] == 0 for row in b._loan_gate_retry_store(None, None, None).state["records"].values())


@pytest.mark.parametrize("first_status", [None, "disabled_by_control_behavior"])
@pytest.mark.parametrize("missing", [None, "unknown_status_123", RuntimeError("read failed")])
def test_missing_status_never_claims_resumed(monkeypatch, first_status, missing):
    loan = _observed_loan(monkeypatch)
    monkeypatch.setattr(b, "_emit_loan_cell_telemetry", lambda *_a, **_k: None)
    samples = iter([first_status, missing] if first_status is not None else [missing])

    def read_status(*_):
        value = next(samples)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(b.live_base, "entity_status_name", read_status)
    messages = []
    result = b._submit_bootstrap_loan(object(), object(), "nauvis", "player", loan, messages.append)
    assert "waits for" in result
    assert not any("RESUMED" in message for message in messages)
    assert all(row["attempts"] == 0 for row in b._loan_gate_retry_store(None, None, None).state["records"].values())


def test_regressing_craft_counter_never_spends_retry(monkeypatch):
    loan = _observed_loan(monkeypatch)
    samples = iter([862, 861])
    monkeypatch.setattr(b, "_bootstrap_loan_products_finished", lambda *_: next(samples))
    result = b._submit_bootstrap_loan(object(), object(), "nauvis", "player", loan, lambda *_: None)
    assert "waits for" in result
    assert all(row["attempts"] == 0 for row in b._loan_gate_retry_store(None, None, None).state["records"].values())


def test_advancing_crafts_clear_step_retry_budget(monkeypatch):
    loan = _observed_loan(monkeypatch)
    key = identity(loan, SimpleNamespace(recipe='electronic-circuit', target_count=3))
    store = b._loan_gate_retry_store(None, None, None)
    store.charge(key, 7)
    store.charge(key, 9)
    samples = iter([862, 863])
    monkeypatch.setattr(b, "_bootstrap_loan_products_finished", lambda *_: next(samples))
    b._submit_bootstrap_loan(object(), object(), "nauvis", "player", loan, lambda *_: None)
    assert store.state['records'][key]['attempts'] == 0
    assert store.state['records'][key]['last_tick'] == 9


def test_unchanged_tick_cannot_spend_a_second_retry(monkeypatch):
    loan = _observed_loan(monkeypatch)
    samples = iter([10, 11, 10, 11])
    monkeypatch.setattr(b.live_base, "game_tick", lambda *_: next(samples))
    for _ in range(2):
        b._submit_bootstrap_loan(object(), object(), "nauvis", "player", loan, lambda *_: None)
    key = identity(loan, SimpleNamespace(recipe='electronic-circuit', target_count=3))
    assert b._loan_gate_retry_store(None, None, None).state['records'][key]['attempts'] == 1
