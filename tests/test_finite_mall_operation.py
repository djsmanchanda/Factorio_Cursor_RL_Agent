# Path: tests/test_finite_mall_operation.py | Purpose: Reach legal finite production from a real session bill without new-cell admission.
import io
import json
from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as ab, finite_mall
from orchestrator.mall_bootstrap import MallBootstrapLoan, MallBootstrapStep, bootstrap_loan_plan
from orchestrator.operation_invocations import Invocations
from orchestrator.operation_session import DeterministicSession
from tools import laya_pm_replay as replay

pytestmark = [pytest.mark.deterministic, pytest.mark.fast]


@pytest.fixture
def session(tmp_path, monkeypatch):
    session = DeterministicSession.__new__(DeterministicSession)
    session.client = SimpleNamespace(command=lambda *_: '')
    session.reference_point = (0, 0)
    session.emit = lambda _: None
    session.invocations = Invocations(tmp_path, 'finite-test')
    session._maintain_mall = lambda: None
    session.submitted = []
    def submit(_auth, plan):
        session.submitted.append(plan)
        path = tmp_path / 'ack.json'
        path.write_text(json.dumps({'ok': True, 'tick': 100, 'failed_placements': 0, 'recipe_failures': 0}))
        return path
    session.bridge = SimpleNamespace(build_layout=submit, operation_invocations=session.invocations)
    session.origin, session.side, session.position = ab.mall_slot_positions((0, 0))[4]
    monkeypatch.setattr(ab, '_independent_mall_ready', lambda *_: False)
    monkeypatch.setattr(ab, '_bootstrap_mall_slot_limit', lambda *_: 8)
    monkeypatch.setattr(ab, 'mall_slot_count', lambda *_: 8)
    monkeypatch.setattr(ab, '_unfunded_ladder_ingredient', lambda *_: None)
    monkeypatch.setattr(ab, '_recipe_depends_on_fast_transport_belt', lambda *_: False)
    stock = {'inserter': 4, 'iron-plate': 100, 'electronic-circuit': 100, 'iron-gear-wheel': 100}
    monkeypatch.setattr(ab, '_transferable_or_available_stock', lambda *_: dict(stock))
    monkeypatch.setattr(ab.live_base, 'available_items', lambda *_: dict(stock))
    monkeypatch.setattr(ab.live_base, 'find_line', lambda *a, **kw: None)
    monkeypatch.setattr(ab, 'active_bootstrap_loans', lambda *_: [])
    monkeypatch.setattr(ab.live_base, 'unset_assemblers_at', lambda *_: {tuple(session.position): 'assembling-machine-1'})
    def entity(_c, _surface, position):
        if tuple(position) == tuple(session.position):
            return {'name': 'assembling-machine-1', 'force': 'player'}
        requester = (session.origin[0] + 4.5, session.origin[1] + 1.5)
        return {'name': 'requester-chest' if tuple(position) == requester else 'passive-provider-chest', 'force': 'player'}
    monkeypatch.setattr(ab.live_base, 'entity_at', entity)
    monkeypatch.setattr(ab.live_base, 'requester_logistic_groups', lambda *_: [])
    monkeypatch.setattr(ab, '_rationed_mall_completion_target', lambda *a: a[4])
    monkeypatch.setattr(ab, '_rationed_mall_spare_target', lambda *a: a[4])
    monkeypatch.setattr(ab, '_bootstrap_demand_cell_affordable', lambda *a: pytest.fail('reuse operation cannot allocate a ninth cell'))
    def loan_submit(c, bridge, surface, force, loan, emit, **kw):
        plan = bootstrap_loan_plan(loan, MallBootstrapStep('inserter', 16, 12), baseline_finished=100)
        plan.update(surface=surface, force=force)
        bridge.build_layout({}, plan)
        return 'borrowed finite inserter half'
    monkeypatch.setattr(ab, '_submit_bootstrap_loan', loan_submit)
    return session


def test_real_funding_session_uses_released_half_and_records_configuration(session):
    assert 'inserter' in ab.PERSISTENT_INTERMEDIATES
    bill = {'short': {'inserter': 12}, 'levels': {'inserter': 16}}
    rows = replay._fund_shortage(session, io.StringIO(), 24, bill, stock_reader=lambda _: {'inserter': 4},
                                finite_bill_batches=True)
    assert rows[0]['operation'] == 'produce_finite'
    assert rows[0]['outcome'] == 'temporary_mall_batch'
    facts = rows[0]['finite_batch_feasibility']
    assert facts['available_stock'] == 4 and facts['stock_target'] == 16
    assert facts['slot_cap'] == facts['occupied_slots'] == 8 and facts['standing_persistent_item']
    assert facts['selected']['position'] == list(session.position)
    assert any(c['reason'] == 'eligible_released_half' for c in facts['candidates'])
    actions = [a for p in session.submitted[0]['phases'] for a in p['actions']]
    assert all(a['action_type'] == 'configure_entity' for a in actions)
    assert any(a.get('recipe') == 'inserter' for a in actions)
    records = list(session.invocations.directory.glob('*.json'))
    assert len(records) == 1 and json.loads(records[0].read_text())['operation'] == 'produce_finite'


@pytest.mark.parametrize('kind', ['owned', 'unknown', 'active_origin'])
def test_ineligible_or_unknown_half_never_configures(session, monkeypatch, kind):
    if kind == 'owned':
        monkeypatch.setattr(ab.live_base, 'requester_logistic_groups', lambda *_: [f'mall:transport-belt:{session.side}'])
    elif kind == 'unknown':
        monkeypatch.setattr(ab.live_base, 'unset_assemblers_at', lambda *_: (_ for _ in ()).throw(ValueError('unknown survey')))
    else:
        # The vacancy selector must not reuse either half of an excluded loan origin.
        assert ab._vacant_mall_half(session.client, 'nauvis', 'player', 'inserter', (0, 0),
                                   excluded_origins={session.origin}) is None
        return
    result = session.run('produce_finite', item='inserter', stock_target=16)
    assert result['code'] == 'finite_batch_unavailable'
    assert result['details']['known'] is (kind != 'unknown')
    assert not session.submitted


@pytest.mark.parametrize('item', ['long-handed-inserter', 'iron-gear-wheel', 'copper-cable'])
def test_existing_batch_catalog_and_anchors_are_preserved(session, item):
    assert session.finite_batch_facts(item, 1)['supported'] is False
    assert session.run('produce_finite', item=item, stock_target=1)['code'] == 'finite_batch_unavailable'
    assert not session.submitted


def test_protected_producer_is_not_a_lender(session, monkeypatch):
    monkeypatch.setattr(ab.live_base, 'unset_assemblers_at', lambda *_: {})
    def line(_c, _surface, _force, recipe, tier, **kw):
        if recipe == 'iron-gear-wheel':
            return SimpleNamespace(machine_count=1, machine_positions=[session.position])
    monkeypatch.setattr(ab.live_base, 'find_line', line)
    facts = session.finite_batch_facts('inserter', 16)
    assert facts['known'] and not facts['eligible'] and facts['reason'] == 'no_legal_half'
    assert any(c['reason'] == 'protected_anchor_or_ingredient_producer' for c in facts['candidates'])
    assert not session.submitted


def test_already_delivered_target_needs_no_configuration(session, monkeypatch):
    monkeypatch.setattr(ab, '_transferable_or_available_stock', lambda *_: {'inserter': 16})
    assert session.run('produce_finite', item='inserter', stock_target=16)['code'] == 'stock_satisfied'
    assert not session.submitted


def test_missing_external_input_returns_bill_instead_of_building(session, monkeypatch):
    monkeypatch.setattr(ab, 'bootstrap_external_shortages', lambda *_: [SimpleNamespace(item='steel-plate', count=5)])
    monkeypatch.setattr(ab, '_production_started', lambda *_: False)
    monkeypatch.setattr(ab, 'ensure_produced', lambda *a, **kw: pytest.fail('finite reuse cannot construct an upstream producer'))
    result = session.run('produce_finite', item='inserter', stock_target=16)
    assert result['code'] == 'material_shortage' and result['details']['required'] == {'steel-plate': 5}
    assert not session.submitted


@pytest.mark.parametrize('branch,item', [('external', 'iron-plate'), ('rotating', 'electronic-circuit')])
@pytest.mark.parametrize('physical,allocatable,credit,shortfall,level', [
    (10, 10, 0, 2, 12),
    (100, 0, 0, 12, 112),
    (100, 0, 5, 7, 107),
])
def test_prerequisite_bills_preserve_allocatable_deficits(
        session, monkeypatch, branch, item, physical, allocatable, credit, shortfall, level):
    actual = {'inserter': 4, 'iron-plate': 100, 'electronic-circuit': 100, 'iron-gear-wheel': 100}
    actual[item] = physical
    usable = {**actual, item: allocatable}
    monkeypatch.setattr(ab, '_bootstrap_loan_stock', lambda *_: (dict(actual), dict(usable)))
    monkeypatch.setattr(ab, '_production_started', lambda *_: False)
    monkeypatch.setattr(ab, 'ensure_produced', lambda *a, **kw: pytest.fail('reuse cannot build upstream'))
    monkeypatch.setattr(ab, '_restore_bootstrap_loan', lambda *a, **kw: pytest.fail('bill cannot discard an owned loan'))
    if branch == 'rotating' or credit:
        loan = MallBootstrapLoan('small-electric-pole', 'inserter', 16, session.side,
            (session.origin[0] + 4.5, session.origin[1] + 1.5), 'inserter',
            completed_step_targets=((item, credit),) if credit else ())
        monkeypatch.setattr(ab, 'active_bootstrap_loans', lambda *_: [loan])
    result = session.run('produce_finite', item='inserter', stock_target=16)
    assert result['code'] == 'material_shortage'
    bill = replay.pm_bill(result, actual)
    assert bill['short'] == {item: shortfall}
    assert bill['levels'] == {item: level}
    assert bill['required'][item] == 12
    assert bill['available'][item] == max(allocatable, credit)
    assert not session.submitted


@pytest.mark.parametrize('facts', [
    None, [], {'known': True},
    {'known': True, 'supported': None, 'eligible': False},
    {'known': 1, 'supported': False, 'eligible': False},
    {'known': True, 'supported': False, 'eligible': False, 'reason': 'unsupported_item',
     'item': 'pipe', 'stock_target': 16},
    {'known': True, 'supported': False, 'eligible': False, 'reason': 'unsupported_item',
     'item': 'inserter', 'stock_target': True},
])
def test_malformed_feasibility_is_journaled_without_dispatch(facts):
    session = SimpleNamespace(finite_batch_facts=lambda *_: facts,
                              run=lambda *a, **kw: pytest.fail('malformed facts forbid dispatch'))
    stream = io.StringIO()
    rows = replay._fund_shortage(session, stream, 1,
        {'short': {'inserter': 12}, 'levels': {'inserter': 16}},
        stock_reader=lambda _: {}, finite_bill_batches=True)
    assert rows[0]['outcome'] == 'finite_batch_unknown'
    assert rows[0]['finite_batch_feasibility']['reason'] == 'malformed_finite_batch_facts'
    assert json.loads(stream.getvalue())['outcome'] == 'finite_batch_unknown'


@pytest.mark.parametrize('supported,reason', [
    (False, 'unsupported_item'), (True, 'pending_owned_configuration'), (True, 'independent_mall'),
])
def test_explicit_fallback_contract_retains_ordinary_production(supported, reason):
    called = []
    facts = {'known': True, 'supported': supported, 'eligible': False, 'reason': reason,
             'item': 'inserter', 'stock_target': 16}
    session = SimpleNamespace(finite_batch_facts=lambda *_: facts,
        run=lambda operation, **kw: called.append(operation) or {'code': 'test_produce', 'ok': True})
    rows = replay._fund_shortage(session, io.StringIO(), 1,
        {'short': {'inserter': 12}, 'levels': {'inserter': 16}},
        stock_reader=lambda _: {}, finite_bill_batches=True)
    assert called == ['produce'] and rows[0]['outcome'] == 'test_produce'


def test_budget_spent_during_feasibility_starts_no_operation():
    spent = [False]
    class Budget:
        cause = None
        def check(self):
            self.cause = 'deadline_reached' if spent[0] else None
            return self.cause, 0
    def facts(*_):
        spent[0] = True
        return {'known': True, 'supported': True, 'eligible': True}
    session = SimpleNamespace(finite_batch_facts=facts, run=lambda *a, **kw: pytest.fail('deadline forbids dispatch'))
    assert replay._fund_shortage(session, io.StringIO(), 1, {'short': {'inserter': 12}, 'levels': {'inserter': 16}},
                                 budget=Budget(), stock_reader=lambda _: {}, finite_bill_batches=True) == []


def test_unknown_feasibility_never_falls_back_to_admission():
    session = SimpleNamespace(finite_batch_facts=lambda *_: {'known': False},
                              run=lambda *a, **kw: pytest.fail('unknown is not admission authority'))
    rows = replay._fund_shortage(session, io.StringIO(), 1, {'short': {'inserter': 12}, 'levels': {'inserter': 16}},
                                 stock_reader=lambda _: {}, finite_bill_batches=True)
    assert rows[0]['outcome'] == 'finite_batch_unknown'
