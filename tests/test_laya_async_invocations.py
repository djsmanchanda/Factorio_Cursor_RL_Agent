# Path: tests/test_laya_async_invocations.py | Purpose: Exercise selected-operation evidence across decisions and reopening.
import json
import copy
from types import SimpleNamespace

import pytest

from orchestrator.operation_invocations import Invocations
from orchestrator.operation_session import DeterministicSession, receipt
from training.laya_guided_policy import GuidedPolicy
from training.laya_ops_runner import run
from training.laya_operation_outcome import summarize_records

pytestmark = [pytest.mark.deterministic, pytest.mark.fast]

class FakeWorld:
    root = None
    def __init__(self):
        self.tick = 1000
    def query(self):
        return {"tick": self.tick, "production": {}, "consumption": {}, "machines": [], "networks": {},
                "ports": [], "resources": [], "recipes": {}}
    def advance(self, ticks):
        self.tick += ticks

PLAN = {'surface': 'nauvis', 'force': 'player', 'atomic': True, 'phases': [{'name': 'p', 'actions': [
    {'action_type': 'place_ghost', 'entity': 'transport-belt', 'position': {'x': 10, 'y': 10}}]}]}


def session(tmp_path, plan=PLAN):
    s = DeterministicSession.__new__(DeterministicSession)
    s.invocations = Invocations(tmp_path, 'episode')
    s.client = object()
    calls = []
    def build(auth, selected):
        calls.append(selected)
        return tmp_path / 'receipt.json'
    s.bridge = SimpleNamespace(build_layout=build)
    s._operations = lambda: {name: lambda **params: s.bridge.build_layout({'allowed': True}, plan)
                             for name in ('starter', 'foundation', 'reserve_steel_seed', 'produce', 'mall_slot')}
    s._maintain_mall = lambda: None
    return s, calls


def test_selected_plan_reopens_and_surveys_without_build(tmp_path, monkeypatch):
    s, calls = session(tmp_path)
    outcome = s.run('starter', recipe='iron-plate')
    identity = outcome['operation_id']
    states = {'required': {'transport-belt': 1}, 'installed': {}, 'pending': {'transport-belt': 1}, 'unplaced': {}}
    monkeypatch.setattr('orchestrator.live_base.plan_construction_status', lambda *args: states)
    assert dict(s.observe_invocations())[identity]['status'] == 'construction_pending'
    states.update(installed={'transport-belt': 1}, pending={})
    reopened = Invocations(tmp_path, 'episode')
    assert dict(reopened.observe(s.client))[identity]['status'] == 'observed'
    assert len(calls) == 1
    assert list(Invocations(tmp_path, 'other').observe(s.client)) == []


def test_invalid_and_uncertain_never_deliver(tmp_path, monkeypatch):
    s, calls = session(tmp_path, {'surface': 'nauvis', 'force': 'player', 'atomic': True, 'phases': []})
    s.run('starter', recipe='iron-plate')
    monkeypatch.setattr('orchestrator.live_base.plan_construction_status', lambda *args: {})
    assert list(s.observe_invocations()) == []
    s2, _ = session(tmp_path / 'other')
    def interrupted(*args):
        raise RuntimeError('lost response')
    s2.bridge.build_layout = interrupted
    try:
        s2.run('starter', recipe='iron-plate')
    except RuntimeError:
        pass
    assert list(s2.observe_invocations()) == []
    evidence = json.loads(next(s2.invocations.directory.glob('*.json')).read_text())
    assert evidence['submissions'][0]['dispatch'] == 'uncertain'


def test_receipts_do_not_overclaim():
    assert receipt('produce', {}, None)['code'] == 'prerequisite_advanced'
    assert receipt('produce', {}, False)['code'] == 'prerequisite_advanced'
    assert receipt('retire_starter', {}, {'submitted': False, 'lifecycle': 'pioneer'})['code'] != 'done'
    assert receipt('retire_starter', {}, {'submitted': True, 'lifecycle': 'retiring'})['code'] != 'done'
    assert receipt('retire_starter', {}, {'submitted': True, 'lifecycle': 'released'})['code'] == 'done'


def test_runner_emits_observation_and_shared_summary(tmp_path, monkeypatch):
    s, calls = session(tmp_path)
    monkeypatch.setattr('orchestrator.live_base.plan_construction_status', lambda *args: {
        'required': {'transport-belt': 1}, 'installed': {'transport-belt': 1}, 'pending': {}, 'unplaced': {}})
    summary = run(FakeWorld(), GuidedPolicy(3), journal=tmp_path / 'clean-decisions.jsonl', decisions=3,
                  measure_ticks=600, seed=3, run_id='t', session=s, emit=lambda *_: None)
    events = [json.loads(x) for x in (tmp_path / 'clean-decisions.jsonl').read_text().splitlines()]
    observed = [x for x in events if x.get('event') == 'operation_observation']
    assert observed and observed[0]['tick'] is not None
    actual = summarize_records(tmp_path / 'decision-states.jsonl')
    assert actual['outcome_counts']['states']['delivered'] >= 1
    assert summary['outcome_counts'] == actual['outcome_counts']
    assert calls


def test_selected_record_excludes_maintenance_and_unrelated_submissions(tmp_path):
    s, calls = session(tmp_path)
    maintenance = copy.deepcopy(PLAN)
    maintenance['phases'][0]['actions'][0]['position']['x'] = 20
    original = s.bridge.build_layout
    s._maintain_mall = lambda: s.bridge.build_layout({}, maintenance)
    result = s.run('starter', recipe='iron-plate')
    s.bridge.build_layout({}, maintenance)
    assert s.bridge.build_layout is original
    record = json.loads((s.invocations.directory / (result['operation_id'] + '.json')).read_text())
    assert len(calls) == 3
    assert [entry['plan'] for entry in record['submissions']] == [PLAN]


@pytest.mark.parametrize('mixed', [False, True])
def test_configuration_plans_are_unknown_without_configuration_readback(tmp_path, monkeypatch, mixed):
    plan = copy.deepcopy(PLAN)
    configuration = {'action_type': 'configure_entity', 'entity': 'transport-belt',
                     'position': {'x': 10, 'y': 10}}
    plan['phases'][0]['actions'] = ([plan['phases'][0]['actions'][0]] if mixed else []) + [configuration]
    s, _ = session(tmp_path, plan)
    s.run('starter', recipe='iron-plate')
    monkeypatch.setattr('orchestrator.live_base.plan_construction_status',
                        lambda *args: pytest.fail('placement survey cannot verify configuration'))
    assert list(s.observe_invocations()) == []


@pytest.mark.parametrize('survey', [None, {},
    {'required': {'transport-belt': True}, 'installed': {'transport-belt': 1}, 'pending': {}, 'unplaced': {}},
    {'required': {'transport-belt': 1}, 'installed': {'pipe': 1}, 'pending': {}, 'unplaced': {}},
    {'required': {'transport-belt': 1}, 'installed': {'transport-belt': 2}, 'pending': {}, 'unplaced': {}}])
def test_missing_and_inconsistent_surveys_cannot_deliver(tmp_path, monkeypatch, survey):
    s, _ = session(tmp_path)
    s.run('starter', recipe='iron-plate')
    monkeypatch.setattr('orchestrator.live_base.plan_construction_status', lambda *args: survey)
    assert list(s.observe_invocations()) == []


def test_partial_prerequisite_construction_reconciles_without_economic_completion(tmp_path, monkeypatch):
    s, calls = session(tmp_path)
    second = copy.deepcopy(PLAN)
    second['phases'][0]['actions'][0]['position']['x'] = 30
    def prerequisite(**params):
        s.bridge.build_layout({}, PLAN)
        s.bridge.build_layout({}, second)
        return None
    s._operations = lambda: {'produce': prerequisite}
    selected = s.run('produce', item='advanced-circuit')
    states = {'required': {'transport-belt': 1}, 'installed': {'transport-belt': 1}, 'pending': {}, 'unplaced': {}}
    def survey(*args):
        if args[-1] == second:
            return {**states, 'installed': {}, 'pending': {'transport-belt': 1}}
        return states
    monkeypatch.setattr('orchestrator.live_base.plan_construction_status', survey)
    assert dict(s.observe_invocations())[selected['operation_id']]['status'] == 'construction_pending'
    records = tmp_path / 'decision-states.jsonl'
    records.write_text(json.dumps({'run_id': 'r', 'index': 0, 'choice': {'family': 'produce'}, 'outcome': selected}) + '\n')
    monkeypatch.setattr('orchestrator.live_base.plan_construction_status', lambda *args: states)
    observations = [{'event': 'operation_observation', 'operation_id': identity, 'outcome': outcome}
                    for identity, outcome in Invocations(tmp_path, 'episode').observe(s.client)]
    result = summarize_records(records, observations=observations)
    assert result['initial_outcome_counts']['states']['prerequisite_advanced'] == 1
    assert result['outcome_counts']['states']['delivered'] == 1
    assert result['outcome_counts']['states']['completed'] == 0
    assert len(calls) == 2


def test_failed_persistence_prevents_dispatch_and_preserves_previous_record(tmp_path, monkeypatch):
    s, calls = session(tmp_path)
    record = s.invocations.begin('starter', {})
    before = (s.invocations.directory / (record['operation_id'] + '.json')).read_text()
    def disk_failure(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr('orchestrator.operation_invocations.os.replace', disk_failure)
    with pytest.raises(OSError, match='disk unavailable'):
        s.invocations.submit(record, s.bridge.build_layout, {}, PLAN)
    assert calls == []
    assert (s.invocations.directory / (record['operation_id'] + '.json')).read_text() == before


def test_interrupted_dispatch_roundtrips_and_fences_reconstruction(tmp_path):
    from orchestrator.checkpoint_restart_state import export_restart_state, import_restart_state
    from orchestrator.checkpoint_milestones import CheckpointEvaluator, FactorySnapshot
    from orchestrator.operation_invocations import ReadbackRequired
    source = tmp_path / 'source'
    invocations = Invocations(source, 'episode')
    record = invocations.begin('starter', {})
    def interrupt(*args):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        invocations.submit(record, interrupt, {}, PLAN)
    original = (invocations.directory / (record['operation_id'] + '.json')).read_bytes()
    with pytest.raises(ReadbackRequired):
        Invocations(source, 'episode').submit(invocations.begin('starter', {}), lambda *args: pytest.fail('resent'), {}, PLAN)
    baseline = FactorySnapshot()
    bundle = export_restart_state(source, 'episode', 'mods', baseline, [baseline], CheckpointEvaluator())
    target = tmp_path / 'target'
    import_restart_state(bundle, target, episode_id='episode', mod_scope='mods')
    restored = Invocations(target, 'episode')
    assert (restored.directory / (record['operation_id'] + '.json')).read_bytes() == original
    s, calls = session(target)
    assert s.run('starter')['code'] == 'readback_required'
    assert calls == []
    with pytest.raises(ReadbackRequired):
        restored.submit(restored.begin('starter', {}), lambda *args: pytest.fail('resent'), {}, PLAN)


def test_empty_and_returned_journals_do_not_fence_but_malformed_does(tmp_path):
    from orchestrator.operation_invocations import ReadbackRequired
    inv = Invocations(tmp_path, 'episode')
    inv.begin('starter', {})
    record = inv.begin('starter', {})
    assert inv.submit(record, lambda *args: 'receipt', {}, PLAN) == 'receipt'
    Invocations(tmp_path, 'episode').require_clear()
    (inv.directory / ('a' * 32 + '.json')).write_text('{')
    with pytest.raises(ReadbackRequired):
        inv.require_clear()


def test_constructor_skips_mutating_initialization_when_uncertain(tmp_path, monkeypatch):
    root = tmp_path / 'root'
    (root / 'server').mkdir(parents=True)
    (root / 'manifest.json').write_text('{"rcon_port": 1}')
    (root / 'server' / 'rcon-password').write_text('password')
    inv = Invocations(root / 'server' / 'script-output', 'episode')
    record = inv.begin('starter', {})
    with pytest.raises(KeyboardInterrupt):
        inv.submit(record, lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()), {}, PLAN)
    monkeypatch.setattr('orchestrator.operation_session.RconClient', lambda *args: object())
    monkeypatch.setattr('orchestrator.operation_session.GameBridge', lambda **kwargs: object())
    monkeypatch.setattr('orchestrator.operation_session.ab.initialize_operation_runtime', lambda *args: pytest.fail('mutating initialization'))
    s = DeterministicSession(root, logical_episode_id='episode')
    assert s.run('starter')['code'] == 'readback_required'


def test_current_session_cannot_repeat_interrupted_build_or_maintain(tmp_path):
    from orchestrator.operation_invocations import ReadbackRequired
    s, _ = session(tmp_path)
    calls = []
    def interrupted(*args):
        calls.append('dispatch')
        raise KeyboardInterrupt()
    s.bridge.build_layout = interrupted
    with pytest.raises(KeyboardInterrupt):
        s.run('starter')
    assert s.last_operation_id
    assert s.run('starter')['code'] == 'readback_required'
    assert s.last_operation_id is None
    with pytest.raises(ReadbackRequired):
        DeterministicSession._maintain_mall(s)
    with pytest.raises(ReadbackRequired):
        s._submit_stock_gate(PLAN, 'gate')
    assert calls == ['dispatch']


def test_real_constructor_after_checkpoint_transfer_stays_fenced(tmp_path, monkeypatch):
    from orchestrator.checkpoint_restart_state import export_restart_state, import_restart_state
    from orchestrator.checkpoint_milestones import CheckpointEvaluator, FactorySnapshot
    root = tmp_path / 'root'
    (root / 'server').mkdir(parents=True)
    (root / 'manifest.json').write_text('{"rcon_port": 1}')
    (root / 'server' / 'rcon-password').write_text('password')
    inv = Invocations(tmp_path / 'source', 'episode')
    record = inv.begin('starter', {})
    with pytest.raises(KeyboardInterrupt):
        inv.submit(record, lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()), {}, PLAN)
    baseline = FactorySnapshot()
    bundle = export_restart_state(tmp_path / 'source', 'episode', 'mods', baseline, [baseline], CheckpointEvaluator())
    import_restart_state(bundle, root / 'server/script-output', episode_id='episode', mod_scope='mods')
    monkeypatch.setattr('orchestrator.operation_session.RconClient', lambda *args: object())
    monkeypatch.setattr('orchestrator.operation_session.GameBridge', lambda **kwargs: object())
    for name in ('initialize_operation_runtime', '_restore_bootstrap_reservations',
                 '_reconcile_bootstrap_work', '_reserve_plastic_output_chest'):
        monkeypatch.setattr('orchestrator.operation_session.ab.' + name,
                            lambda *args: pytest.fail('constructor maintenance dispatched'))
    restored = DeterministicSession(root, logical_episode_id='episode')
    assert restored.run('produce', item='advanced-circuit')['code'] == 'readback_required'
    assert json.loads(next(restored.invocations.directory.glob('*.json')).read_text()) == record


def test_symlink_cannot_change_invocation_ownership(tmp_path):
    from orchestrator.operation_invocations import ReadbackRequired
    inv = Invocations(tmp_path / 'source', 'episode')
    inv.begin('starter', {})
    destination = tmp_path / 'target'
    destination.mkdir()
    (destination / inv.directory.name).symlink_to(inv.directory, target_is_directory=True)
    with pytest.raises(ReadbackRequired):
        Invocations(destination, 'episode')
