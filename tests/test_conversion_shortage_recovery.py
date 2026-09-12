# Path: tests/test_conversion_shortage_recovery.py | Purpose: Keep nested conversion shortages queued without recursive recovery or hidden planner errors.
import pytest

from orchestrator import autonomous_builder as builder
from orchestrator.parts_mall import MaterialShortage


def setup_shortage(monkeypatch, nested):
    attempts = []
    monkeypatch.setattr(builder, '_transferable_or_available_stock', lambda *_: {})
    monkeypatch.setattr(builder, '_production_started', lambda *_: False)

    def ensure(*args, **kwargs):
        raise MaterialShortage('compact_steel_seed', {'medium-electric-pole': 14}, {'medium-electric-pole': 4})

    def batch(*args, **kwargs):
        attempts.append(args[4])
        raise nested

    monkeypatch.setattr(builder, 'ensure_produced', ensure)
    monkeypatch.setattr(builder, '_rationed_mall_batch', batch)
    return attempts


def test_nested_shortage_is_queued_once_and_unrelated_demand_survives(monkeypatch):
    nested = MaterialShortage('steel_power_connection', {'medium-electric-pole': 16, 'inserter': 2}, {'medium-electric-pole': 4})
    attempts = setup_shortage(monkeypatch, nested)
    targets = {'transport-belt': 20}
    messages = []
    assert builder._ensure_mall_item(object(), object(), 'nauvis', 'player', 'steel-plate', 18,
        targets, (0, 0), messages.append, background=False) == (False, None)
    assert targets == {'transport-belt': 20, 'medium-electric-pole': 16, 'inserter': 2}
    assert attempts == ['medium-electric-pole']
    assert any('steel_power_connection' in message for message in messages)
    # A later observation can complete the parent; the recovery path leaves
    # no sticky failure flag or recursion that prevents normal resumption.
    monkeypatch.setattr(builder, 'ensure_produced', lambda *_a, **_k: (9, 10))
    assert builder._ensure_mall_item(object(), object(), 'nauvis', 'player', 'steel-plate', 18,
        targets, (0, 0), messages.append, background=False) == (True, (9, 10))
    assert attempts == ['medium-electric-pole']


def test_nested_supply_wait_yields_without_retrying_batch(monkeypatch):
    attempts = setup_shortage(monkeypatch, builder.ProductionPrerequisiteDeferred('awaiting constructed steel', code='steel_iron_retirement_wait'))
    targets = {}
    assert builder._ensure_mall_item(object(), object(), 'nauvis', 'player', 'steel-plate', 18,
        targets, (0, 0), lambda _: None, background=False) == (False, None)
    assert targets == {'medium-electric-pole': 14}
    assert attempts == ['medium-electric-pole']


@pytest.mark.parametrize('error', [
    builder.StuckError('collision must remain a failure'),
    builder.ProductionPrerequisiteDeferred('chemical handoff', code='chemical_capability_handoff'),
])
def test_recovery_preserves_hard_failures_and_chemical_retry_contract(monkeypatch, error):
    setup_shortage(monkeypatch, error)
    with pytest.raises(type(error)) as failure:
        builder._ensure_mall_item(object(), object(), 'nauvis', 'player', 'steel-plate', 18,
            {}, (0, 0), lambda _: None, background=False)
    assert failure.value is error


def test_saved_steel_seed_uses_its_fenced_poles_without_taking_bridge_stock(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from orchestrator import stage_services
    from orchestrator.material_reservations import MaterialReservationLedger, plan_material_bill
    from planners.steel_bootstrap import steel_seed

    ledger = MaterialReservationLedger(tmp_path, episode_id='steel-replay', surface='nauvis', force='player')
    plan = steel_seed((20.5, 10.5), 'east', mined=True)
    plan.update(surface='nauvis', force='player')
    stock = {**plan_material_bill(plan), 'medium-electric-pole': 4}
    ledger.declare('conversion_steel-plate', {'medium-electric-pole': 2}, stock, priority=100)
    ledger.declare('power_bridge', {'medium-electric-pole': 6}, stock, priority=50)
    ledger.declare('compact_steel_seed', plan_material_bill(plan), stock, priority=50)
    ledger.path.with_suffix('.steel-seed.json').write_text(json.dumps(plan))
    monkeypatch.setattr(builder, '_MATERIAL_RESERVATION_LEDGER', ledger)
    monkeypatch.setattr(builder, '_bootstrap_state', lambda _: SimpleNamespace(lifecycle_state='released'))
    monkeypatch.setattr(builder, '_transferable_or_available_stock', lambda *_: stock)
    monkeypatch.setattr(stage_services.live_base, 'transferable_items', lambda *_: stock)
    monkeypatch.setattr(stage_services, 'active_material_ledger', lambda: ledger)
    monkeypatch.setattr(stage_services, '_reservation_supply_estimates', lambda *_: ({}, {}))
    submitted = []

    def submit(c, bridge, surface, actual_plan, name, emit, **kwargs):
        stage_services.assert_affordable(c, surface, 'player', actual_plan, name, emit,
            reserve_project=True, reservation_priority=kwargs.get('reservation_priority', 50))
        submitted.append((name, actual_plan))

    monkeypatch.setattr(builder, '_submit', submit)
    monkeypatch.setattr(builder, 'bring_stage_up', lambda *_a, **_k: None)
    monkeypatch.setattr(builder, '_ensure_power_anchor_on_generated_network', lambda *_a, **_k: None)
    monkeypatch.setattr(builder, '_diagnose_machines', lambda *_a, **_k: [])
    output = builder._build_compact_steel_seed(object(), object(), 'nauvis', 'player', (0, 0), (0, 0), lambda _: None)
    assert output is not None
    assert submitted == [('conversion_steel-plate', plan)]
    assert ledger.projects['conversion_steel-plate'].reserved['medium-electric-pole'] == 2
    assert ledger.projects['power_bridge'].reserved['medium-electric-pole'] == 2
    assert ledger.projects['compact_steel_seed'].state == 'completed'

    # Resume the saved ledger, then repeat adoption. Ownership is durable and
    # the completed alias cannot create another demand on the next attempt.
    reloaded = MaterialReservationLedger(tmp_path, episode_id='steel-replay', surface='nauvis', force='player')
    before = reloaded.path.read_bytes()
    builder._adopt_legacy_steel_seed_reservation(reloaded, stock, lambda _: None)
    assert reloaded.path.read_bytes() == before
    assert reloaded.projects['power_bridge'].reserved['medium-electric-pole'] == 2
