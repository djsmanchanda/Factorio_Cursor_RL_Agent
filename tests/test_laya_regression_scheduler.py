# Path: tests/test_laya_regression_scheduler.py | Purpose: Verify earned restart bundles, shared admission, planned accounting and crash recovery.
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools import laya_regression_scheduler as scheduler
from tools.checkpoint_catalog import atomic_json
from tools.laya_checkpoint_regression import report
from training import laya_restart_checkpoint as checkpoint
from orchestrator.checkpoint_milestones import CheckpointEvaluator
from training.laya_restart_state import export_restart_state
from tests.test_laya_restart_state import snapshot
from tests.test_milestone_checkpoint import FakeClient

pytestmark = pytest.mark.fast


def test_real_strict_baseline_save_bundle_roundtrip_and_tamper_rejection(tmp_path):
    root = tmp_path/'source'
    server = root/'server'
    for name in ('saves', 'script-output', 'logs'):
        (server/name).mkdir(parents=True)
    manifest = {'production_authority': False, 'runtime': 'disposable_saved_world',
                'checkpoint_lineage': {'origin': 'C0', 'source_sha256': 'a'*64},
                'mod_hashes': {'mod/control.lua': 'hash'},
                'repository_revision': 'abcdef0123456789'}
    baseline, current = snapshot(0, 1), snapshot(100, 2)
    evaluator = CheckpointEvaluator()
    assert evaluator.evaluate('C4', current, baseline=baseline).passed
    client = FakeClient(server)
    class Bridge:
        def command(self, command):
            if 'tick_paused=' in command:
                return ''
            if 'paused=game.tick_paused' in command:
                return json.dumps({'tick':100,'paused':True})
            response = client.command(command)
            return '100' if 'server_save' in command else response
    session = SimpleNamespace(_baseline=baseline, _history=[baseline, current], episode_id='laya-origin',
        acceptance=lambda: {'C4': 'passed'}, export_restart_state=lambda mod_scope: export_restart_state(
            server/'script-output', 'laya-origin', mod_scope, baseline, [baseline, current], evaluator))
    adapter = SimpleNamespace(root=root, manifest=manifest, bridge=Bridge(),
        checkpoint_ownership_complete=lambda s: s is session,
        checkpoint_engine_receipt={'version':'2.1.24','build':87808,'binary_sha256':'a'*64,'validated':True})
    (server/'script-output/logs').mkdir()
    (server/'script-output/logs/deterministic-plan-reservations.jsonl').write_text('{"claim":"owned"}\n')
    bundle = checkpoint.capture(adapter, session, 'C4', {'phases_reached':['C0','C1','C2','C3'],
        '_checkpoint_boundary':{'pid':os.getpid(),'run_id':'test-run','operation_active':False,'decisions':3}},
        [{'ok': True, 'operation': 'starter', 'params': {'recipe':'iron-plate'}}], run_id='test-run')
    metadata, context = checkpoint.load(bundle)
    assert metadata['capture_tick'] == 100
    assert context['exact_continuation'] is False
    assert context['lifecycle'][0]['operation'] == 'starter'
    from tools import laya_checkpoint_bank as bank
    generation = bank.register(tmp_path/'bank', bundle)
    assert bank.discover(tmp_path/'bank')['C4'][0]['validated'] is False
    with pytest.raises(ValueError,match='no live reload'):
        bank.select(tmp_path/'bank','C4',generation)
    with pytest.raises(ValueError,match='already registered'):
        bank.register(tmp_path/'bank',bundle)
    destination = tmp_path/'copy'
    destination.mkdir()
    atomic_json(destination/'manifest.json', {**manifest, 'source_sha256': metadata['world']['sha256']})
    restored, context = checkpoint.restore(bundle, destination)
    assert restored.baseline == baseline
    assert restored.history[-1] == current
    assert restored.evaluator.reached == {'C4'}
    assert (destination/'server/script-output/logs/deterministic-plan-reservations.jsonl').read_text() == '{"claim":"owned"}\n'
    (bundle/'state/logs/laya-restart-context.json').write_text('{}')
    with pytest.raises(ValueError, match='hash'):
        checkpoint.load(bundle)


def test_host_admission_counts_stale_and_reserved_worlds_and_stale_gate_blocks(tmp_path):
    registry = tmp_path/'registry'
    atomic_json(registry/'_host-scheduler-gate/job-status.json', {'updated': 100, 'controller': {'slots': 2}})
    atomic_json(registry/'old/job-status.json', {'updated': 0, 'jobs': [{'status':'running'}]})
    atomic_json(registry/'new/job-status.json', {'jobs': [{'status':'queued','admission_reserved':True}]})
    assert not scheduler.may_admit(registry, 100, available_gb=20)
    atomic_json(registry/'new/job-status.json', {'jobs': []})
    assert scheduler.may_admit(registry, 100, available_gb=20)
    assert not scheduler.may_admit(registry, 281, available_gb=20)
    assert not scheduler.may_admit(registry, 100, available_gb=2)


def frozen_suite(tmp_path, arms=('candidate', 'reference')):
    repo = tmp_path/'repo'
    repo.mkdir()
    for name in ('training/laya_restart_checkpoint.py','training/laya_restart_state.py',
                 'orchestrator/checkpoint_milestones.py','tools/run_laya_clean.py'):
        file=repo/name
        file.parent.mkdir(exist_ok=True)
        file.write_text('# fixture source\n')
    (repo/'.gitignore').write_text('data/\n')
    subprocess.run(['git','init','-q',str(repo)],check=True)
    subprocess.run(['git','add','.'],cwd=repo,check=True)
    subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                    'commit','-qm','fixture'],cwd=repo,check=True)
    source = tmp_path/'source.zip'
    source.write_bytes(b'fixture source')
    mods = tmp_path/'mods'
    mods.mkdir()
    (mods/'mod-list.json').write_text('{"mods":[]}')
    spec = {'name':'regression-test','arms':{arm:{'revision':'HEAD'} for arm in arms},
        'seeds':[1,2], 'max_game_ticks':1000,'trial_wall_seconds':60,'until':'2099-01-01T00:00:00+00:00',
        'source_save':str(source),'mods_source':str(mods),'python':sys.executable,
        'factorio_binary':sys.executable, 'after':['predecessor'], 'game_port_base':49100,'rcon_port_base':49200}
    registry = tmp_path/'registry'
    root = scheduler.enqueue(repo,registry,spec)
    return root, registry


def test_freeze_plans_every_job_and_refuses_launch_before_predecessor_or_gate(tmp_path):
    root, registry = frozen_suite(tmp_path)
    assert len(scheduler.read(root/'job-status.json')['jobs']) == 4
    assert scheduler.run_one(root) == 'waiting_for_predecessors'
    atomic_json(registry/'predecessor/job-status.json', {'jobs':[{'status':'completed'}]})
    assert scheduler.run_one(root) == 'waiting_for_host_budget'
    assert report(root)['planned'] == 4
    assert sum(g['unknown'] for g in report(root)['groups']) == 4


def test_full_origin_cohort_runs_a_control_and_several_variants_on_shared_seeds(tmp_path):
    root, _ = frozen_suite(tmp_path, arms=('reference', 'hold', 'chests', 'plastic'))
    jobs = scheduler.read(root/'job-status.json')['jobs']
    # Each seed's arms are admitted together, so every variant pairs with the control.
    assert [j['arm'] for j in jobs[:4]] == ['reference', 'hold', 'chests', 'plastic']
    assert {j['seed'] for j in jobs[:4]} == {1} and len(jobs) == 8
    assert len(scheduler.read(root/'freeze.json')['arms']) == 4
    (tmp_path/'too-many').mkdir()
    with pytest.raises(ValueError, match='measurement arms'):
        frozen_suite(tmp_path/'too-many', arms=tuple(f'arm{i}' for i in range(7)))


def test_capture_selection_rejects_invalid_or_nonsetup_lanes(tmp_path):
    spec = {'name':'setup', 'purpose':'disposable_source_setup', 'arms':{'reference':{}},
            'seeds':[1], 'max_game_ticks':100, 'trial_wall_seconds':60,
            'checkpoint_catalog':'catalog', 'capture_checkpoints':['C1', 'C1']}
    with pytest.raises(ValueError, match='Capture selection'):
        scheduler.enqueue(tmp_path, tmp_path/'registry', spec)
    spec['capture_checkpoints'] = ['C0']
    with pytest.raises(ValueError, match='Capture selection'):
        scheduler.enqueue(tmp_path, tmp_path/'registry', spec)
    assert not (tmp_path/'registry').exists()


def test_dispatch_records_an_actual_command_failure_and_cleans_reservation(tmp_path, monkeypatch):
    root, registry = frozen_suite(tmp_path)
    atomic_json(registry/'predecessor/job-status.json', {'jobs':[{'status':'completed'}]})
    monkeypatch.setattr(scheduler,'may_admit',lambda *a,**kw: True)
    monkeypatch.setattr(scheduler,'ports_free',lambda *a: True)
    calls=[]
    def fail(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(7,command)
    assert scheduler.run_one(root, executor=fail) == 'infrastructure_failure'
    job = scheduler.read(root/'job-status.json')['jobs'][0]
    assert job['admission_reserved'] is False
    assert 'CalledProcessError' in job['termination_cause']
    assert calls[0][2] == 'prepare'
    assert report(root)['planned'] == 4


def dispatched_run_command(root, registry, monkeypatch):
    atomic_json(registry/'predecessor/job-status.json', {'jobs':[{'status':'completed'}]})
    monkeypatch.setattr(scheduler,'may_admit',lambda *a,**kw: True)
    monkeypatch.setattr(scheduler,'ports_free',lambda *a: True)
    monkeypatch.setattr(scheduler,'verify_engine',lambda *a: None)
    calls=[]
    def record(command, **kwargs):
        calls.append(command)
        if command[2] == 'run':
            raise subprocess.CalledProcessError(7,command)
    scheduler.run_one(root, executor=record)
    return calls[-1]


def test_new_cohorts_freeze_2x_speed_and_legacy_roots_keep_1x(tmp_path, monkeypatch):
    root, registry = frozen_suite(tmp_path)
    assert scheduler.read(root/'regression.json')['game_speed'] == 2.0
    run = dispatched_run_command(root, registry, monkeypatch)
    assert run[run.index('--speed')+1] == '2'
    spec = scheduler.read(root/'regression.json')
    del spec['game_speed']  # a root frozen before the field existed
    atomic_json(root/'regression.json', spec)
    atomic_json(root/'job-status.json', {**scheduler.read(root/'job-status.json'), 'last_admit': 0})
    freeze = scheduler.read(root/'freeze.json')
    atomic_json(root/'freeze.json', {**freeze, 'spec_sha256': scheduler.sha(root/'regression.json')})
    run = dispatched_run_command(root, registry, monkeypatch)
    assert run[run.index('--speed')+1] == '1'


def test_recovery_retains_reservation_when_owned_cleanup_cannot_be_verified(tmp_path, monkeypatch):
    root, registry = frozen_suite(tmp_path)
    data=scheduler.read(root/'job-status.json')
    data['jobs'][0].update(status='running',admission_reserved=True,world=str(tmp_path/'orphan'))
    atomic_json(root/'job-status.json',data)
    def blocked(*args): raise ValueError('still alive')
    monkeypatch.setattr(scheduler,'scoped_stop',blocked)
    with pytest.raises(ValueError,match='still alive'):
        scheduler.run_one(root)
    assert scheduler.read(root/'job-status.json')['jobs'][0]['admission_reserved'] is True


def test_recovery_preserves_a_published_terminal_result(tmp_path, monkeypatch):
    root, _ = frozen_suite(tmp_path)
    status = scheduler.read(root/'job-status.json')
    status['jobs'][0].update(status='completed', admission_reserved=True, world=str(tmp_path/'world'),
                             termination_cause='stalled', strict_reached={'C6':123})
    atomic_json(root/'job-status.json', status)
    monkeypatch.setattr(scheduler, 'scoped_stop', lambda *args: None)
    scheduler.recover(root)
    job = scheduler.read(root/'job-status.json')['jobs'][0]
    assert job['status'] == 'completed' and job['termination_cause'] == 'stalled'
    assert job['strict_reached'] == {'C6':123}
    assert job['cleanup_completed'] and not job['admission_reserved']


def test_missing_telemetry_is_unknown_and_success_is_not_fresh_acceptance(tmp_path):
    job={'strict_target':'C6','bundle':'checkpoint'}
    result=scheduler.collect_result(tmp_path,job)
    assert result['successor_passed'] is None
    evidence=tmp_path/'evidence'
    evidence.mkdir()
    rows=[{'event':'acceptance','tick':100,'statuses':{'C5':'passed','C6':'failed'}},
          {'event':'acceptance','tick':120,'statuses':{'C5':'passed','C6':'passed'}}]
    (evidence/'clean-decisions.jsonl').write_text('\n'.join(map(json.dumps,rows)))
    result=scheduler.collect_result(tmp_path,job)
    assert result['successor_passed'] is None and result['legacy_latch_reached']['C6']==120
    rows += [{'event':'strict_current','loaded_tick':100,'tick':120,'statuses':{'C6':True},
              'elapsed_seconds':2,'decisions':3}]
    (evidence/'clean-decisions.jsonl').write_text('\n'.join(map(json.dumps,rows)))
    result=scheduler.collect_result(tmp_path,job)
    assert result['successor_passed'] is True and result['strict_reached']['C6']==120
    assert result['full_run_acceptance_gate']=='not_evaluated'
    assert result['exact_continuation'] is False


def test_research_evidence_is_reported_per_origin_and_missing_is_unknown(tmp_path):
    root, _ = frozen_suite(tmp_path)
    spec = scheduler.read(root/'regression.json')
    spec['research_target'] = 'mining-productivity-4'
    atomic_json(root/'regression.json', spec)
    status = scheduler.read(root/'job-status.json')
    status['jobs'][0].update(status='completed', started=1,
        research_acceptance={'available':True,'earned':False,'research_units':2})
    atomic_json(root/'job-status.json', status)
    groups = report(root)['groups']
    assert sum(g['research']['observed'] for g in groups) == 1
    assert sum(g['research']['progressed'] for g in groups) == 1
    assert sum(g['research']['earned'] for g in groups) == 0
    assert sum(g['research']['unknown'] for g in groups) == 3
    assert all(g['research']['target'] == 'mining-productivity-4' for g in groups)


def test_collect_result_preserves_research_receipt(tmp_path):
    evidence = tmp_path/'evidence'
    evidence.mkdir()
    receipt = {'available':True, 'earned':True, 'research_units':10}
    (evidence/'clean-decisions.jsonl').write_text(json.dumps({'event':'summary',
        'objective_version':'laya-research-consumption-v1','research_acceptance':receipt})+'\n')
    result = scheduler.collect_result(tmp_path, {'origin':'C0','strict_target':None})
    assert result['research_acceptance'] == receipt
    assert result['objective_version'] == 'laya-research-consumption-v1'
