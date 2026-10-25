# Path: tests/test_laya_stock_gate_control.py | Purpose: Stateful evidence for explicit bill-backed standing-cell gate raises.
from copy import deepcopy
from types import SimpleNamespace

import pytest

from orchestrator import mall_control as control
from training.laya_ops import DeterministicSession, receipt
from training.laya_ops_runner import with_economics

pytestmark = pytest.mark.fast
ITEM = 'transport-belt'


def economics():
    return {'guarded_configuration': True,
            'projects': {'iron-foundation': {'state': 'supply_wait', 'active': True, 'required': {ITEM: 132},
                                           'reserved': {ITEM: 20}, 'missing': {ITEM: 112}}},
            'loans': [], 'stock': {'network': {ITEM: 20}},
            'cells': [{'recipe': ITEM, 'entity': 'assembling-machine-1', 'unit_number': 17,
                       'position': [1.5, 1.5], 'surface': 'nauvis', 'force': 'player',
                       'gate': {'signal': ITEM, 'comparator': '<', 'constant': 20},
                       'owner': 'paired_mall_transport-belt', 'reservation_owners': ['paired_mall_transport-belt'],
                       'standing': True, 'loaned': False, 'anchor': False, 'circuit_enabled': False,
                       'requester_position': [4.5, 1.5], 'requester_unit_number': 18,
                       'groups': ['mall:transport-belt:left'], 'side': 'left'}]}


class World:
    def __init__(self):
        self.facts = economics()
        self.floors = {}
        self.plans = []
        self.apply = True
        self.fail_readback = False
        self.other_configuration = {'requests': {'iron-plate': 30}, 'recipe': ITEM, 'wires': ['green']}

    def observe(self):
        if self.plans and self.fail_readback:
            raise ValueError('telemetry unavailable')
        return deepcopy(self.facts)

    def submit(self, plan, name):
        self.plans.append(deepcopy(plan))
        actions = plan['phases'][0]['actions']
        assert len(actions) == 1
        assert set(actions[0]) == {'action_type', 'entity', 'position', 'logistic_condition',
                                   'configuration_preconditions'}
        assert actions[0]['configuration_preconditions']['unit_number'] == 17
        assert actions[0]['configuration_preconditions']['requester']['unit_number'] == 18
        assert actions[0]['action_type'] == 'configure_entity'
        if self.apply:
            self.facts['cells'][0]['gate'] = deepcopy(actions[0]['logistic_condition'])

    def execute(self, preview):
        return control.execute_raise(ITEM, preview, observe=self.observe, submit=self.submit, floors=self.floors)


def test_bill_132_offers_explicit_raise_from_20_without_changing_other_options():
    menu = {'options': [{'family': 'wait', 'option_id': 'wait.300'}], 'needs': []}
    result = with_economics(menu, [], economics())
    option = result['options'][1]
    assert option['family'] == 'raise_stock_gate'
    assert option['params']['preview']['target_gate']['constant'] == 132
    assert option['preview']['current_gate']['constant'] == 20
    assert option['preview']['bill_owner'] == 'iron-foundation'
    assert option['bill'] == {} and option['features'] == {'bill_known': True}
    assert result['options'][0] == {'family': 'wait', 'option_id': 'wait.300'}
    assert result['needs'][0]['options'] == [option['option_id']]


def test_held_constructing_bill_remains_a_valid_active_demand():
    facts = economics()
    facts['projects']['iron-foundation']['state'] = 'constructing'
    assert control.raise_options(facts)[0]['preview']['target_gate']['constant'] == 132


@pytest.mark.parametrize('change', [
    lambda f: f.update(guarded_configuration=False), lambda f: f.update(guarded_configuration=None),
    lambda f: f['projects']['iron-foundation'].update(active=False),
    lambda f: f.update(cells=None), lambda f: f.update(projects=None), lambda f: f.update(loans=None),
    lambda f: f.update(stock={'network': None}), lambda f: f['cells'][0].update(owner=None),
    lambda f: f['cells'][0].update(loaned=True), lambda f: f['cells'][0].update(gate=None),
    lambda f: f['cells'][0].update(anchor=True), lambda f: f['cells'][0].update(circuit_enabled=True),
    lambda f: f['cells'][0].update(standing=False),
    lambda f: f['cells'][0]['gate'].update(signal='iron-plate'),
    lambda f: f['cells'][0]['gate'].update(second_signal='iron-plate'),
    lambda f: f['stock']['network'].update({ITEM: 132}),
    lambda f: f['projects']['iron-foundation']['reserved'].update({ITEM: 132}),
])
def test_unknown_unsafe_and_satisfied_cases_are_absent(change):
    facts = economics()
    change(facts)
    assert control.raise_options(facts) == []


@pytest.mark.parametrize('change', [
    lambda f: f.update(guarded_configuration=False),
    lambda f: f.update(cells=[]), lambda f: f['cells'][0].update(unit_number=99),
    lambda f: f['cells'][0].update(recipe='inserter'),
    lambda f: f['cells'][0]['gate'].update(constant=21),
    lambda f: f['cells'][0].update(owner=None),
    lambda f: f['cells'][0].update(requester_unit_number=99),
    lambda f: f['projects']['iron-foundation']['required'].update({ITEM: 150}),
])
def test_changed_snapshot_is_blocked_without_any_submission_or_bookkeeping(change):
    world = World()
    preview = control.raise_options(world.observe())[0]['preview']
    change(world.facts)
    before = deepcopy(world.facts)
    result = world.execute(preview)
    assert result['code'] == 'stock_gate_snapshot_changed'
    assert not result['submitted'] and not result['verified']
    assert world.plans == [] and world.floors == {} and world.facts == before


def test_readback_proves_effect_preserves_reservations_and_configuration():
    world = World()
    original_projects = deepcopy(world.facts['projects'])
    config = deepcopy(world.other_configuration)
    preview = control.raise_options(world.observe())[0]['preview']
    result = world.execute(preview)
    assert result['ok'] and result['submitted'] and result['verified']
    assert result['code'] == 'stock_gate_verified'
    assert world.floors == {('nauvis', 'player', ITEM): 132}
    assert world.facts['projects'] == original_projects and world.other_configuration == config
    assert control.raise_options(world.observe()) == []
    assert receipt('raise_stock_gate', {'item': ITEM}, result)['observed']['gate']['constant'] == 132


@pytest.mark.parametrize('apply,unknown', [(False, False), (True, True)])
def test_submission_is_not_verified_effect(apply, unknown):
    world = World()
    preview = control.raise_options(world.observe())[0]['preview']
    world.apply, world.fail_readback = apply, unknown
    result = world.execute(preview)
    assert result['submitted'] and not result['ok'] and not result['verified']
    assert result['code'] == 'stock_gate_unverified'
    assert world.floors[('nauvis', 'player', ITEM)] == 132


def test_configuration_operation_never_runs_hidden_upkeep(monkeypatch):
    session = DeterministicSession.__new__(DeterministicSession)
    monkeypatch.setattr(session, '_operations', lambda: {'raise_stock_gate': lambda **kw: {
        'ok': False, 'code': 'stock_gate_snapshot_changed', 'submitted': False, 'verified': False}})
    monkeypatch.setattr(session, '_maintain_mall', lambda: pytest.fail('unrelated mutation'))
    assert session.run('raise_stock_gate', item=ITEM, preview={})['submitted'] is False


def test_observed_ownership_survives_configuration_history_and_rejects_foreign_claims(tmp_path, monkeypatch):
    import json
    from orchestrator.power_district import reservation_log_path, plan_footprint_tiles
    raw = deepcopy(economics()['cells'][0])
    for field in ('owner', 'reservation_owners', 'standing', 'loaned', 'anchor',
                  'requester_position', 'requester_unit_number', 'groups', 'side'):
        raw.pop(field)
    raw['requesters'] = [{'position': [4.5, 1.5], 'unit_number': 18, 'force': 'player',
                          'groups': ['mall:transport-belt:left']}]
    monkeypatch.setattr(control.live_base, 'mall_gate_survey', lambda *args: [deepcopy(raw)])
    monkeypatch.setattr(control, 'locate_mall_cell', lambda *args: ((0, 0), 'left'))
    survey = lambda: control.observe_cells(None, tmp_path, 'nauvis', 'player', (0, 0), ())
    assert survey()[0]['owner'] is None
    path = reservation_log_path(tmp_path)
    path.parent.mkdir(parents=True)
    tiles = sorted(plan_footprint_tiles({'phases': [{'actions': [{
        'entity': raw['entity'], 'position': {'x': 1.5, 'y': 1.5}}]}]}))
    own = {'name': 'paired_mall_transport-belt', 'tiles': tiles}
    path.write_text(json.dumps(dict(own, tiles=tiles[:1])) + '\n')
    assert survey()[0]['owner'] is None
    path.write_text(json.dumps(own) + '\n')
    assert survey()[0]['owner'] == own['name']
    # Normal construction records its recipe, provider and request updates too.
    # These receipts must not revoke the original full placement ownership.
    configuration_receipts = [
        {'name': name, 'tiles': tiles} for name in (
            'mall_recipe_transport-belt', 'mall_stock_gate_transport-belt',
            'mall_provider_limit_transport-belt', 'compact_mall_requests_transport-belt',
            'paired_mall_requests_transport-belt')]
    with path.open('a') as handle:
        for entry in configuration_receipts:
            handle.write(json.dumps(entry) + '\n')
    assert survey()[0]['owner'] == own['name']
    facts = economics()
    facts['cells'] = survey()
    assert control.raise_options(facts)
    from orchestrator.mall_bootstrap import (
        MallBootstrapLoan, MallBootstrapStep, bootstrap_loan_plan, restore_bootstrap_loan_plan,
    )
    from orchestrator.power_district import append_plan_reservation
    loan = MallBootstrapLoan(ITEM, 'pipe', 5, 'left', (4.5, 1.5), 'pipe')
    append_plan_reservation(tmp_path, 'bootstrap_loan_pipe', bootstrap_loan_plan(
        loan, MallBootstrapStep('pipe', 5, 5), baseline_finished=0))
    raw['recipe'] = 'pipe'
    raw['requesters'][0]['groups'] = [loan.group]
    facts['cells'] = survey()
    assert control.raise_options(facts) == []
    append_plan_reservation(tmp_path, 'restore_bootstrap_loan_pipe', restore_bootstrap_loan_plan(
        loan, stock_gate_target=20))
    raw['recipe'] = ITEM
    raw['requesters'][0]['groups'] = ['mall:transport-belt:left']
    facts['cells'] = survey()
    assert facts['cells'][0]['owner'] == own['name'] and control.raise_options(facts)
    # Historical logs did not distinguish configuration receipts. Migrate only
    # known configuration operations confined to this same cell.
    history = [json.loads(line) for line in path.read_text().splitlines()]
    for entry in history:
        entry.pop('configuration_only', None)
    path.write_text(''.join(json.dumps(entry) + '\n' for entry in history))
    assert survey()[0]['owner'] == own['name']
    # Explicit placement claims never become configuration history by name.
    counterfeit = dict(history[-1], configuration_only=False)
    with path.open('a') as handle:
        handle.write(json.dumps(counterfeit) + '\n')
    assert survey()[0]['owner'] is None
    path.write_text(''.join(json.dumps(entry) + '\n' for entry in history))
    legacy_outside_cell = dict(history[-1], tiles=[*history[-1]['tiles'], [99, 99]])
    with path.open('a') as handle:
        handle.write(json.dumps(legacy_outside_cell) + '\n')
    assert survey()[0]['owner'] is None
    path.write_text(''.join(json.dumps(entry) + '\n' for entry in history))
    # New configuration-only facts work without introducing another name exception.
    append_plan_reservation(tmp_path, 'future_settings_operation', {
        'phases': [{'actions': [{'action_type': 'configure_entity', 'entity': raw['entity'],
                                 'position': {'x': 1.5, 'y': 1.5}}]}]})
    assert survey()[0]['owner'] == own['name']
    before = path.read_text()
    with path.open('a') as handle:
        handle.write(json.dumps({'name': 'foreign-project', 'tiles': tiles[:1]}) + '\n')
    foreign = path.read_text()
    assert survey()[0]['owner'] is None
    assert path.read_text() == foreign and foreign.startswith(before)
    path.write_text('corrupt\n')
    with pytest.raises(ValueError):
        survey()


def test_legacy_batched_refresh_preserves_both_declared_cells_after_loan(tmp_path, monkeypatch):
    import json
    from orchestrator.mall_bootstrap import (
        MallBootstrapLoan, MallBootstrapStep, bootstrap_loan_plan, restore_bootstrap_loan_plan,
    )
    from orchestrator.power_district import append_plan_reservation, reservation_log_path
    from planners.mall_layout import generate_paired_mall_layout, generate_mall_stock_gate_update

    owner = f'paired_mall_{ITEM}'
    raw = []
    for index, side in enumerate(('left', 'right')):
        plan = generate_paired_mall_layout(
            ITEM, 'assembling-machine-1', ['iron-plate', 'iron-gear-wheel'], [1, 1],
            (32, 32), side, craft_time=0.5, stock_gate_target=20)
        append_plan_reservation(tmp_path, owner, plan)
        actions = plan['phases'][0]['actions']
        assembler = next(a for a in actions if a['entity'] == 'assembling-machine-1')
        requester = next(a for a in actions if a['entity'] == 'requester-chest')
        raw.append({
            'recipe': ITEM, 'entity': assembler['entity'], 'unit_number': 17 + index,
            'position': list(assembler['position'].values()), 'surface': 'nauvis', 'force': 'player',
            'gate': assembler['logistic_condition'], 'circuit_enabled': False,
            'requesters': [{'position': list(requester['position'].values()),
                            'unit_number': 19, 'force': 'player',
                            'groups': ['mall:transport-belt:left', 'mall:transport-belt:right']}],
        })
    monkeypatch.setattr(control.live_base, 'mall_gate_survey', lambda *args: deepcopy(raw))
    survey = lambda: control.observe_cells(None, tmp_path, 'nauvis', 'player', (0, 0), ())
    assert [c['owner'] for c in survey()] == [owner, owner]
    loan = MallBootstrapLoan(ITEM, 'pipe', 5, 'left', (36.5, 33.5), 'pipe')
    append_plan_reservation(tmp_path, 'bootstrap_loan_pipe', bootstrap_loan_plan(
        loan, MallBootstrapStep('pipe', 5, 5), baseline_finished=0))
    append_plan_reservation(tmp_path, 'restore_bootstrap_loan_pipe', restore_bootstrap_loan_plan(
        loan, stock_gate_target=20))
    append_plan_reservation(tmp_path, f'mall_stock_gate_{ITEM}', generate_mall_stock_gate_update(
        ITEM, 'assembling-machine-1', [tuple(m['position']) for m in raw], 20))
    path = reservation_log_path(tmp_path)
    history = [json.loads(line) for line in path.read_text().splitlines()]
    for entry in history:
        entry.pop('configuration_only', None)

    def write_history(entries):
        path.write_text(''.join(json.dumps(entry) + '\n' for entry in entries))

    write_history(history)
    facts = economics()
    facts['cells'] = survey()
    assert [c['owner'] for c in facts['cells']] == [owner, owner]
    assert len(control.raise_options(facts)) == 2
    # A named receipt may not claim foreign tiles or override explicit placement evidence.
    for invalid in (dict(history[-1], tiles=[*history[-1]['tiles'], [99, 99]]),
                    dict(history[-1], configuration_only=False)):
        write_history([*history[:-1], invalid])
        assert [c['owner'] for c in survey()] == [None, None]
    write_history([*history, {'name': 'foreign-project', 'tiles': [[33, 33]]}])
    assert [c['owner'] for c in survey()] == [None, owner]
    # The settings history cannot supply a missing original placement claim.
    write_history([dict(e, configuration_only=True) if i == 1 else e
                   for i, e in enumerate(history[:-1])])
    assert survey()[1]['owner'] is None


def test_configuration_receipts_do_not_claim_placement_ownership(tmp_path):
    import json
    from orchestrator.power_district import append_plan_reservation, reservation_log_path

    def plan(kind, **extra):
        return {'phases': [{'actions': [{'action_type': kind, 'entity': 'assembling-machine-1',
                                        'position': {'x': 1.5, 'y': 1.5}}]}], **extra}

    append_plan_reservation(tmp_path, 'settings', plan('configure_entity'))
    append_plan_reservation(tmp_path, 'build', plan('place_ghost'))
    append_plan_reservation(tmp_path, 'explicit-claim', plan('configure_entity', reserved_tiles=[[0, 0]]))
    entries = [json.loads(line) for line in reservation_log_path(tmp_path).read_text().splitlines()]
    assert [entry['configuration_only'] for entry in entries] == [True, False, False]
    assert entries[0]['tiles'] == entries[1]['tiles']  # Collision evidence remains available.


def test_session_uses_shared_validated_bridge_submission_without_ledger_mutation(tmp_path):
    from planners.sandbox_infrastructure import build_layout_authorization
    world = World()
    selected = control.raise_options(world.observe())[0]['preview']
    session = DeterministicSession.__new__(DeterministicSession)
    calls = []
    import json
    report = tmp_path / 'report.json'
    report.write_text(json.dumps({'ok': True}))
    session.bridge = SimpleNamespace(build_layout=lambda authorization, plan: (
        calls.append((authorization, deepcopy(plan))) or report))
    control.execute_raise(ITEM, selected, observe=world.observe,
                          submit=session._submit_stock_gate, floors=world.floors)
    assert len(calls) == 1
    expected = build_layout_authorization([('gate', calls[0][1])])
    actual = dict(calls[0][0])
    actual.pop('authorization_timestamp')
    expected.pop('authorization_timestamp')
    assert actual == expected
    assert calls[0][1]['phases'][0]['actions'][0]['action_type'] == 'configure_entity'


@pytest.mark.parametrize('requested', [20, None])
def test_refresh_preserves_explicit_floor_for_numeric_and_default_targets(monkeypatch, requested):
    from orchestrator import autonomous_builder as ab
    from types import SimpleNamespace
    # Exercise the same existing refresh path ensure uses, with real planners.
    monkeypatch.setattr(ab, '_MALL_STOCK_GATE_FLOORS', {('nauvis', 'player', ITEM): 132})
    monkeypatch.setattr(ab, '_RAISED_STOCK_GATE_FLOORS', {('nauvis', 'player', ITEM): 132})
    monkeypatch.setattr(ab, '_MALL_REFRESH_SIGNATURES', set())
    monkeypatch.setattr(ab, '_MALL_PROVIDER_CAPACITY_FLOORS', {})
    monkeypatch.setattr(ab, '_MALL_ITEM_PROVIDER_LIMITS', {})
    monkeypatch.setattr(ab, '_mineable', lambda item: False)
    monkeypatch.setattr(ab, '_paired_mall_provider', lambda *a: (4.5, .5))
    monkeypatch.setattr(ab, 'mall_slot_uses_shared_provider', lambda *a: False)
    monkeypatch.setattr(ab.live_base, 'entity_at', lambda *a: None)
    plans = []
    monkeypatch.setattr(ab, '_submit', lambda c, b, s, plan, name, emit: plans.append(plan))
    plan = SimpleNamespace(existing=SimpleNamespace(machine_positions=((1.5, 1.5),)),
                           spec={'machine': 'assembling-machine-1'}, mall_storage_limit=1,
                           mall_request_multiplier=None, fill_provider=False)
    ab._refresh_mall_cell(object(), None, 'nauvis', 'player', ITEM, plan, lambda _: None,
                          upgrade_bootstrap=False, stock_gate_target=requested)
    gates = [a['logistic_condition'] for p in plans for phase in p['phases']
             for a in phase['actions'] if 'logistic_condition' in a]
    assert gates == [{'signal': ITEM, 'comparator': '<', 'constant': 132}]


def test_readonly_survey_executes_lua_with_real_gate_and_requester_identity():
    import json
    import shutil
    import subprocess
    lua = shutil.which('lua')
    if not lua:
        pytest.skip('Lua interpreter unavailable')
    expected = [{'entity': 'assembling-machine-1', 'unit_number': 17, 'recipe': ITEM,
                 'surface': 'nauvis', 'force': 'player', 'position': [1.5, 1.5],
                 'gate': {'signal': ITEM, 'comparator': '<', 'constant': 20},
                 'circuit_enabled': False, 'requesters': []}]

    class Client:
        def command(self, command):
            harness = '''
local behavior={connect_to_logistic_network=true,circuit_enable_disable=false,
 logistic_condition={condition={first_signal={type="item",name="transport-belt"},comparator="<",constant=20}}}
local machine={name="assembling-machine-1",unit_number=17,position={x=1.5,y=1.5},force={name="player"},
 get_recipe=function() return {name="transport-belt"} end,get_control_behavior=function() return behavior end}
local chest={unit_number=18,position={x=4.5,y=1.5},force={name="player"},
 get_logistic_sections=function() return {sections={{valid=true,group="mall:transport-belt:left"}}} end}
local surface={name="nauvis",find_entities_filtered=function(filter)
 if filter.type then return {machine} end
 if filter.position[1]==4.5 then return {chest} end
 return {} end}
game={surfaces={nauvis=surface}}
helpers={table_to_json=function(rows)
 assert(#rows==1 and rows[1].unit_number==17 and rows[1].circuit_enabled==false)
 assert(rows[1].gate.constant==20 and rows[1].gate.signal=="transport-belt")
 assert(#rows[1].requesters==1 and rows[1].requesters[1].unit_number==18)
 assert(rows[1].requesters[1].groups[1]=="mall:transport-belt:left")
 return ENCODED end}
rcon={print=print}
'''.replace('ENCODED', json.dumps(json.dumps(expected)))
            result = subprocess.run([lua, '-'], input=harness + command.removeprefix('/sc '),
                                    text=True, capture_output=True, check=True)
            return result.stdout

    assert control.live_base.mall_gate_survey(Client(), 'nauvis', 'player') == expected


def test_unknown_precheck_and_submission_exception_are_truthful():
    world = World()
    preview = control.raise_options(world.observe())[0]['preview']
    def unknown():
        raise ValueError('survey failed')
    result = control.execute_raise(ITEM, preview, observe=unknown, submit=world.submit, floors=world.floors)
    assert result['code'] == 'stock_gate_observation_unknown' and not result['submitted']
    def reject(plan, name):
        raise ValueError('executor rejected')
    result = control.execute_raise(ITEM, preview, observe=world.observe, submit=reject, floors=world.floors)
    assert result['code'] == 'stock_gate_submission_failed'
    assert result['submission_attempted'] and not result['submitted'] and not result['verified']


@pytest.mark.parametrize('verified', [True, False])
def test_runner_persists_configuration_submission_and_readback_evidence(tmp_path, verified):
    import json
    from training.laya_guided_policy import GuidedPolicy
    from training.laya_ops_runner import run

    observed = deepcopy(economics()['cells'][0])
    observed['gate']['constant'] = 132
    evidence = {'submitted': True, 'submission_attempted': True, 'verified': verified,
                'observed': observed, 'error': None if verified else 'readback changed'}

    class Adapter:
        root = None

        def query(self):
            return {'tick': 0, 'production': {}, 'consumption': {}, 'machines': [],
                    'networks': {'1': {'items': {ITEM: 20}}}, 'recipes': {}}

    class Session:
        def economics(self):
            return economics()

        def run(self, operation, **params):
            assert operation == 'raise_stock_gate'
            return {'operation': operation, 'params': params, 'ok': verified,
                    'code': 'stock_gate_verified' if verified else 'stock_gate_unverified', **evidence}

        def close(self):
            pass

    class Policy(GuidedPolicy):
        def choose(self, state, menu):
            option = next(o for o in menu['options'] if o['family'] == 'raise_stock_gate')
            return {'option_id': option['option_id'], 'need_id': option['need_id'], 'probability': 1,
                    'rules': [], 'distribution': []}

    run(Adapter(), Policy(1), journal=tmp_path / 'journal.jsonl', decisions=1, measure_ticks=600,
        seed=1, run_id='gate-evidence', session=Session())
    record = json.loads((tmp_path / 'decision-states.jsonl').read_text().splitlines()[0])
    assert record['outcome']['configuration_receipt'] == evidence
