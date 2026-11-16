# Path: tests/test_owned_mall_resumption.py | Purpose: Preserve owned recipe intent after an interrupted paired-cell construction.
import copy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as ab, construction_coverage as coverage
from orchestrator import mall_builder, mall_construction, mall_bootstrap, live_base
from orchestrator.operation_invocations import Invocations
from orchestrator.operation_session import DeterministicSession
from orchestrator.parts_mall import MaterialShortage
from training.laya_ops_runner import with_economics

pytestmark = [pytest.mark.deterministic, pytest.mark.fast]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    world = SimpleNamespace(real=False, recipe='', gate=False, groups=[], submissions=[],
                            tick=100, pending=False, missing=False, loans=[], unit=91, circuit=False)
    ref = (0, 0)
    origin = mall_builder._cell_origins(ref)[2]
    pos = mall_builder._slot_position(origin, 'right')
    requester = (origin[0] + 4.5, origin[1] + 1.5)
    provider = (requester[0], requester[1] + 1)
    client = SimpleNamespace(command=lambda command: '{"configuration_preconditions":1,"unconfigured_recipe_preconditions":1}')
    s = DeterministicSession.__new__(DeterministicSession)
    s.client, s.reference_point, s.emit = client, ref, lambda *_: None
    s.script_output = tmp_path
    s.invocations = Invocations(tmp_path, 'episode')
    s._maintain_mall = lambda: None

    def build(_auth, plan):
        world.submissions.append(copy.deepcopy(plan)); world.tick += 1
        for phase in plan['phases']:
            for action in phase['actions']:
                if action.get('entity') in live_base.ASSEMBLER_TIERS:
                    world.gate = copy.deepcopy(action.get('logistic_condition', world.gate))
                    if 'recipe' in action:
                        assert action['configuration_preconditions']['unit_number'] == world.unit
                        assert action['configuration_preconditions']['recipe'] == world.recipe
                        assert action['configuration_preconditions']['requester']['groups'] == world.groups
                        world.recipe = action['recipe']
                if action.get('entity') == 'requester-chest':
                    world.groups.extend(section['group'] for section in action.get('logistic_sections', []))
        report = tmp_path / f'report-{world.tick}.json'
        report.write_text(json.dumps({'ok': True, 'tick': world.tick, 'failed_placements': 0,
            'recipe_failures': 0, 'attempted_placements': 6, 'succeeded_placements': 6,
            'placed_ghosts': 4, 'placed_entities': 0}))
        return report
    s.bridge = SimpleNamespace(script_output=tmp_path, build_layout=build, operation_invocations=s.invocations)

    monkeypatch.setattr(coverage, 'clear_plan_clutter', lambda *_a: None)
    monkeypatch.setattr(coverage, 'relocate_obstructing_poles', lambda *_a: None)
    monkeypatch.setattr(coverage, 'assert_affordable', lambda *_a: None)
    monkeypatch.setattr(coverage, 'active_material_ledger', lambda: None)
    monkeypatch.setattr(live_base, 'entity_names_at',
        lambda _client, _surface, positions: {
            p: 'requester-chest' if p == requester else 'substation'
            for p in positions
            if p == requester or p == mall_builder.cell_substation(ref, origin)
        })
    monkeypatch.setattr(live_base, 'requester_logistic_groups', lambda *_a: tuple(world.groups))
    monkeypatch.setattr(live_base, 'pole_network_id', lambda *_a: 1)
    monkeypatch.setattr(live_base, 'network_generation_kw', lambda *_a: 1000)
    monkeypatch.setattr(mall_builder, '_choose_slot', lambda *_a, **_k: (origin, 'right'))
    def await_hardware(_client, _surface, _force, converted, *_a, **_k):
        if converted and not world.real:
            raise MaterialShortage('paired_mall_inserter', {'inserter': 2}, {'inserter': 0})
    monkeypatch.setattr(coverage, '_await_bot_built_infrastructure', await_hardware)

    def survey(_client, _surface, _force, **_kwargs):
        if not world.real:
            return []
        return [{'position': list(pos), 'recipe': world.recipe, 'gate': world.gate,
                 'entity': 'assembling-machine-1', 'unit_number': world.unit,
                 'surface': 'nauvis', 'force': 'player', 'circuit_enabled': world.circuit,
                 'requesters': [{'position': list(requester), 'unit_number': 92,
                                 'force': 'player', 'groups': list(world.groups)}]}]
    monkeypatch.setattr(live_base, 'mall_gate_survey', survey)
    monkeypatch.setattr(mall_bootstrap, 'active_bootstrap_loans', lambda *_a: world.loans)
    def status(_client, _surface, _force, plan):
        n = len({(a['entity'], tuple(a['position'].values())) for ph in plan['phases'] for a in ph['actions']})
        return {'required': {'hardware': n}, 'installed': {'hardware': n - int(world.pending or world.missing)},
                'pending': {'hardware': int(world.pending)}, 'unplaced': {'hardware': int(world.missing)}}
    monkeypatch.setattr(live_base, 'plan_construction_status', status)
    return s, world, origin, pos, provider


def interrupt_build(rig, monkeypatch, item='inserter'):
    s, world, _, _, _ = rig
    monkeypatch.setitem(ab.LINE_RECIPES, item, {**ab.LINE_RECIPES[item], 'machine': 'assembling-machine-1'})
    s._operations = lambda: {'produce': lambda **_p: mall_builder.build_compact_mall_stage(
        s.client, s.bridge, 'nauvis', 'player', item, {}, s.reference_point,
        lambda *_a, **_k: pytest.fail('bring-up must not follow the interrupted construction'), s.emit,
        stock_target=14, stock_gate_target=14)}
    outcome = s.run('produce', item=item, stock_target=14)
    assert outcome['code'] == 'material_shortage'
    assert len(world.submissions) == 1 and world.recipe == ''
    world.real = True
    world.groups.append('mall:big-electric-pole:left')
    s.invocations = Invocations(s.script_output, 'episode')  # actual journal reopen
    s.bridge.operation_invocations = s.invocations
    return outcome


@pytest.mark.parametrize('item', ['inserter', 'transport-belt'])
def test_interrupted_build_resumes_in_place_through_real_produce(rig, monkeypatch, item):
    s, world, _, pos, provider = rig
    interrupt_build(rig, monkeypatch, item)
    fact, = mall_construction.observe(s.client, s.invocations, s.reference_point)
    assert fact['reason'] == 'ready' and fact['guard']['recipe'] == ''
    assert mall_construction.options({'pending_mall_cells': [fact]}) == []
    menu = with_economics({'options': [], 'needs': []}, [], {
        'pending_mall_cells': [fact], 'owned_mall_resume_guarded': True})
    assert menu['options'][0]['family'] == 'resume_mall_cell'
    assert len(world.submissions) == 1  # observation does not configure
    # The rest of ensure sees this same configured producer. A new allocation
    # is forbidden even at 8/8; unrelated production/supply work is mocked.
    monkeypatch.setattr(ab, '_ensure_chemical_ladder_predecessor', lambda *_a: None)
    monkeypatch.setattr(ab, '_is_pre_core_temporary_mall_item', lambda *_a: False)
    monkeypatch.setattr(ab, '_service_bootstrap_loan', lambda *_a: None)
    monkeypatch.setattr(ab, '_effective_mall_stock_gate', lambda *_a, **kw: kw['requested'])
    monkeypatch.setattr(ab, '_allocate_dynamic_belt_capacity', lambda *_a, **kw: False)
    monkeypatch.setattr(ab, '_complete_material_producer', lambda *_a: None)
    monkeypatch.setattr(ab, '_tag_finite_mall_cells', lambda *_a: None)
    monkeypatch.setattr(ab, 'MANAGED_INTERMEDIATE_SOURCES', {})
    monkeypatch.setattr(ab, '_refresh_mall_cell', lambda *_a, **kw: provider)
    def plan(*_a, **_kw):
        assert world.recipe == item
        return SimpleNamespace(existing=SimpleNamespace(working_count=1, produced_count=0,
                               machine_positions=[pos]), at_size=True, promote_to_line=False,
                               production_target=14)
    monkeypatch.setattr(ab, '_plan_line', plan)
    monkeypatch.setattr(ab, '_serve_healthy_line', lambda *_a, **kw: provider)
    monkeypatch.setattr(ab, '_build_assembled_stage', lambda *_a, **kw: pytest.fail('new cell requested'))
    monkeypatch.setattr(ab, 'mall_slot_count', lambda *_a: 8)
    monkeypatch.setattr(ab, '_bootstrap_mall_slot_limit', lambda *_a: 8)
    s._operations = lambda: {'produce': lambda **params: ab.produce_to_demand(
        s.client, s.bridge, 'nauvis', 'player', params['item'], s.reference_point,
        s.emit, stock_target=params['stock_target'])}
    groups = list(world.groups)
    assert s.run('produce', item=item, stock_target=14)['code'] == 'producing'
    assert world.recipe == item and world.groups == groups
    assert len(world.submissions) == 2
    assert all(a['action_type'] == 'configure_entity'
               for p in world.submissions[-1]['phases'] for a in p['actions'])
    assert mall_construction.observe(s.client, s.invocations, s.reference_point) == []
    s.run('produce', item=item, stock_target=14)
    assert len(world.submissions) == 2  # the completed intent cannot configure twice


@pytest.mark.parametrize('change,reason', [
    ('pending', 'construction_pending'), ('missing', 'support_missing'),
    ('ownership', 'ownership_or_loan_changed'), ('loan', 'ownership_or_loan_changed'),
    ('machine', 'machine_missing_or_changed'), ('unknown', 'observation_unknown'),
    ('gate', 'configuration_changed'), ('gate_unknown', 'configuration_changed'),
    ('circuit', 'configuration_changed')])
def test_unready_owned_intent_never_configures_another_cell(rig, monkeypatch, change, reason):
    s, world, _, _, _ = rig
    interrupt_build(rig, monkeypatch)
    if change == 'pending': world.pending = True
    if change == 'missing': world.missing = True
    if change == 'ownership': world.groups = ['mall:other:right']
    if change == 'loan': world.loans = [SimpleNamespace(requester_position=mall_construction.selected_cells(
        s.invocations, s.reference_point)[0]['requester_position'])]
    if change == 'machine': world.real = False
    if change == 'gate': world.gate['constant'] += 1
    if change == 'gate_unknown': world.gate = None
    if change == 'circuit': world.circuit = True
    if change == 'unknown': monkeypatch.setattr(live_base, 'mall_gate_survey', lambda *_a, **_k: (_ for _ in ()).throw(ValueError('unknown')))
    fact, = mall_construction.observe(s.client, s.invocations, s.reference_point)
    assert fact['reason'] == reason
    result = mall_construction.resume(s.client, s.bridge, s.invocations, s.reference_point,
        s.emit, item='inserter', claim=fact['claim'], expected=fact['guard'])
    assert not result['ok'] and not result['configured'] and len(world.submissions) == 1


def test_stale_identity_and_old_executor_cannot_configure(rig, monkeypatch):
    s, world, _, _, _ = rig
    interrupt_build(rig, monkeypatch)
    fact, = mall_construction.observe(s.client, s.invocations, s.reference_point)
    world.unit += 1
    assert not mall_construction.resume(s.client, s.bridge, s.invocations, s.reference_point,
        s.emit, item='inserter', claim=fact['claim'], expected=fact['guard'])['ok']
    world.unit -= 1
    s.client.command = lambda _command: '{"configuration_preconditions":1}'
    result = mall_construction.resume(s.client, s.bridge, s.invocations, s.reference_point,
        s.emit, item='inserter', claim=fact['claim'], expected=fact['guard'])
    assert result['code'] == 'owned_mall_resume_guard_unavailable'
    assert len(world.submissions) == 1


def test_returned_acknowledgement_survives_receipt_relocation(rig, monkeypatch):
    s, world, _, _, _ = rig
    interrupt_build(rig, monkeypatch)
    record = json.loads(next(s.invocations.directory.glob('*.json')).read_text())
    assert record['submissions'][0]['report']['ok']
    Path(record['submissions'][0]['receipt']).unlink()
    fact, = mall_construction.observe(s.client, Invocations(s.script_output, 'episode'), s.reference_point)
    assert fact['reason'] == 'ready'


def test_completed_configuration_cannot_resurrect_a_released_cell(rig, monkeypatch):
    s, world, _, _, _ = rig
    interrupt_build(rig, monkeypatch)
    fact, = mall_construction.observe(s.client, s.invocations, s.reference_point)
    s._operations = lambda: {'resume_mall_cell': lambda **params: mall_construction.resume(
        s.client, s.bridge, s.invocations, s.reference_point, s.emit, **params)}
    result = s.run('resume_mall_cell', item='inserter', claim=fact['claim'], expected=fact['guard'])
    assert result['code'] == 'owned_mall_resumed'
    world.recipe = ''  # A later loan release must not reopen old construction.
    assert mall_construction.selected_cells(Invocations(s.script_output, 'episode'), s.reference_point) == []


def test_executor_guard_rejection_is_a_typed_changed_result(rig, monkeypatch):
    from orchestrator.construction_coverage import StuckError
    s, world, _, _, _ = rig
    interrupt_build(rig, monkeypatch)
    fact, = mall_construction.observe(s.client, s.invocations, s.reference_point)
    def reject(*_a, **_kw):
        raise StuckError('changed at write', code='infrastructure_collision',
                         details={'blockers': [{'reason': 'configuration_precondition_failed:recipe_mismatch'}]})
    monkeypatch.setattr(mall_construction, '_configure_built_mall_recipes', reject)
    result = mall_construction.resume(s.client, s.bridge, s.invocations, s.reference_point,
        s.emit, item='inserter', claim=fact['claim'], expected=fact['guard'])
    assert result['code'] == 'owned_mall_resume_changed' and not result['configured']


@pytest.mark.parametrize('bad', [[], {'ok': True},
    {'ok': True, 'tick': 1, 'failed_placements': False, 'recipe_failures': 0}])
def test_malformed_acknowledgements_never_authorize_resumption(rig, monkeypatch, bad):
    from orchestrator.operation_invocations import ReadbackRequired
    s, _, _, _, _ = rig
    interrupt_build(rig, monkeypatch)
    path = next(s.invocations.directory.glob('*.json'))
    record = json.loads(path.read_text())
    record['submissions'][0]['report'] = bad
    path.write_text(json.dumps(record))
    with pytest.raises(ReadbackRequired):
        mall_construction.observe(s.client, Invocations(s.script_output, 'episode'), s.reference_point)


@pytest.mark.parametrize('fallback', [False, True])
@pytest.mark.parametrize('raw', [
    '{"ok":false,"ok":true,"tick":101,"failed_placements":0,"recipe_failures":0}',
    '{"ok":true,"tick":101,"failed_placements":0,"recipe_failures":0,"extra":NaN}',
    '{"ok":true,"tick":101,"failed_placements":0,"recipe_failures":0,"extra":Infinity}',
])
def test_raw_acknowledgement_must_be_strict_json(rig, monkeypatch, raw, fallback):
    s, world, _, _, _ = rig
    if not fallback:
        original = s.bridge.build_layout
        def malformed(*args):
            path = original(*args)
            path.write_text(raw[:-1] + ',"succeeded_placements":6,"attempted_placements":6,'
                            '"placed_ghosts":4,"placed_entities":0}')
            return path
        s.bridge.build_layout = malformed
    interrupt_build(rig, monkeypatch)
    path = next(s.invocations.directory.glob('*.json'))
    record = json.loads(path.read_text())
    if fallback:
        record['submissions'][0]['report'] = None
        path.write_text(json.dumps(record))
        Path(record['submissions'][0]['receipt']).write_text(raw)
    else:
        assert record['submissions'][0]['report'] is None
    assert mall_construction.observe(s.client, s.invocations, s.reference_point) == []
    assert mall_construction.resume(s.client, s.bridge, s.invocations, s.reference_point,
        s.emit, item='inserter', claim=record['operation_id'] + ':0', expected={})['configured'] is False
    assert world.recipe == '' and len(world.submissions) == 1


@pytest.mark.parametrize('connection', ['nil', '0', '"false"', 'false'])
def test_unconfigured_survey_preserves_unknown_connection_state(connection):
    lua = shutil.which('lua')
    if lua is None:
        pytest.skip('Lua interpreter required')
    expected = 'false' if connection == 'false' else 'nil'
    class Client:
        def command(self, command):
            harness = '''
local behavior={circuit_enable_disable=false,connect_to_logistic_network=CONNECTION}
local machine={name='assembling-machine-1',unit_number=91,position={x=1.5,y=1.5},force={name='player'},
 get_recipe=function() return nil end,get_control_behavior=function() return behavior end}
local surface={name='nauvis',find_entities_filtered=function(filter)
 if filter.type then return {machine} end return {} end}
game={surfaces={nauvis=surface}}
helpers={table_to_json=function(rows)
 assert(#rows==1 and rows[1].recipe=='' and rows[1].circuit_enabled==false)
 assert(rows[1].gate==EXPECTED)
 return '[]' end}
rcon={print=print}
'''.replace('CONNECTION', connection).replace('EXPECTED', expected)
            return subprocess.run([lua, '-'], input=harness + command.removeprefix('/sc '),
                                  text=True, capture_output=True, check=True).stdout
    live_base.mall_gate_survey(Client(), 'nauvis', 'player', include_unconfigured=True)
