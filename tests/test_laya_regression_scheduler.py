# Path: tests/test_laya_regression_scheduler.py | Purpose: Verify earned restart bundles, shared admission, planned accounting and crash recovery.
import json
import os
from pathlib import Path
import subprocess
import sys
import time
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


def frozen_suite(tmp_path, arms=('candidate', 'reference'), contract=None, spec_updates=None, files=None):
    repo = tmp_path/'repo'
    repo.mkdir()
    sources = {name: '# fixture source\n' for name in ('training/laya_restart_checkpoint.py','training/laya_restart_state.py',
               'orchestrator/checkpoint_milestones.py','tools/run_laya_clean.py')}
    for name, text in {**sources, **(files or {})}.items():
        file=repo/name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
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
    if contract is not None:
        spec['experiment_contract'] = contract
    spec.update(spec_updates or {})
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
        return subprocess.CompletedProcess(command, 0)
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
    scheduler._publish(root, lambda status: status.update(last_admit=0))
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


@pytest.mark.parametrize('field', ['game_port_base', 'rcon_port_base'])
@pytest.mark.parametrize('base', [0, 65533, 65600, True, '49100'])
def test_invalid_port_range_rejected_before_freeze(tmp_path, field, base):
    spec = {'game_port_base': 49100, 'rcon_port_base': 49200, field: base}
    with pytest.raises(ValueError, match=field):
        scheduler.validate_port_ranges(spec, 4, tmp_path/'registry')
    assert not (tmp_path/'registry').exists()


def test_ports_conflict_only_with_same_protocol_or_unreleased_worlds(tmp_path):
    registry = tmp_path/'registry'
    root = registry/'old'
    atomic_json(root/'regression.json', {'game_port_base': 50000, 'rcon_port_base': 51000})
    atomic_json(root/'freeze.json', {'planned_jobs': 4})
    atomic_json(root/'job-status.json', {'jobs': [{'status': 'running'}]})
    spec = {'game_port_base': 50002, 'rcon_port_base': 52000}
    with pytest.raises(ValueError, match='overlaps old'):
        scheduler.validate_port_ranges(spec, 4, registry)
    scheduler.validate_port_ranges({**spec, 'after': ['old']}, 4, registry)
    # UDP and TCP can use the same numeric port. Bounds are inclusive.
    scheduler.validate_port_ranges({'game_port_base': 51000, 'rcon_port_base': 50000}, 4, registry)
    scheduler.validate_port_ranges({'game_port_base': 65532, 'rcon_port_base': 65532}, 4)
    atomic_json(root/'job-status.json', {'jobs': [{'status': 'completed', 'world': 'owned'}]})
    with pytest.raises(ValueError, match='overlaps old'):
        scheduler.validate_port_ranges(spec, 4, registry)
    atomic_json(root/'job-status.json', {'jobs': [{'status': 'cancelled_before_start'}]})
    scheduler.validate_port_ranges(spec, 4, registry)


def test_geometry_status_receipts_are_not_invented_tool_errors(tmp_path):
    evidence = tmp_path/'evidence'
    evidence.mkdir()
    outcomes = [{'status': 'planned'}, {'status': 'construction_pending'},
                {'ok': True}, {'ok': False, 'code': 'power_route_unavailable'},
                {'status': 'failed', 'error_type': 'SavedWorldError', 'error': 'selected plan became illegal'}]
    (evidence/'decision-states.jsonl').write_text('\n'.join(
        json.dumps({'outcome': outcome}) for outcome in outcomes))
    result = scheduler.collect_result(tmp_path, {'origin': 'C0'})
    assert result['faults'] == {'power_route_unavailable': 1, 'runner_error:SavedWorldError': 1}
    (evidence/'clean-decisions.jsonl').write_text(json.dumps({'event': 'summary', 'faults': {}, 'stop': 'stalled'}))
    assert scheduler.collect_result(tmp_path, {'origin': 'C0'})['faults'] == {}
    assert scheduler.collect_result(tmp_path, {'origin': 'C0'})['stop_classification'] == 'genuine_failure'


def test_report_uses_early_latches_after_history_trim_and_per_arm_target(tmp_path):
    root, _ = frozen_suite(tmp_path)
    spec = scheduler.read(root/'regression.json')
    spec['arms']['candidate']['research_target'] = 'mining-productivity-4'
    atomic_json(root/'regression.json', spec)
    status = scheduler.read(root/'job-status.json')
    status['jobs'][0].update(status='completed', loaded_tick=100, strict_reached={'C4': 120, 'C7': 150},
                            strict_current=[{'tick': 2000, 'statuses': {'C4': False, 'C7': None}}])
    atomic_json(root/'job-status.json', status)
    group = next(g for g in report(root)['groups'] if g['arm'] == 'candidate')
    assert group['strict_reach']['C4']['reached'] == 1
    assert group['strict_reach']['C7']['reached'] == 1
    assert group['held_at_end']['C7']['unknown'] == 2
    assert group['research']['target'] == 'mining-productivity-4'


def test_waiting_dispatch_heartbeats_without_rewriting_the_whole_status(tmp_path):
    """A waiting dispatch must not rewrite the cohort status on every poll.

    The status carries every job's strict history (15 MB in C94); rewriting it
    under the global admission lock starved other cohorts' admissions.
    """
    root = tmp_path/'cohort'
    root.mkdir()
    now = time.time()
    atomic_json(root/'job-status.json', {'updated': now, 'jobs': [{'id': 'a', 'status': 'queued'}]})
    before = (root/'job-status.json').stat().st_mtime_ns
    waiting = lambda status: 'waiting_for_host_budget'
    assert scheduler._publish(root, waiting, min_interval=240) == 'waiting_for_host_budget'
    assert (root/'job-status.json').stat().st_mtime_ns == before
    # The heartbeat still refreshes once the interval has elapsed.
    atomic_json(root/'job-status.json', {'updated': now-241, 'jobs': [{'id': 'a', 'status': 'queued'}]})
    scheduler._publish(root, waiting, min_interval=240)
    assert json.loads((root/'job-status.json').read_text())['updated'] > now-241
    # A transition publishes immediately even inside the interval.
    atomic_json(root/'job-status.json', {'updated': time.time(), 'jobs': [{'id': 'a', 'status': 'queued'}]})
    scheduler._publish(root, lambda status: {'id': 'a', 'status': 'preparing'}, min_interval=240)
    assert json.loads((root/'job-status.json').read_text())['updated'] > now


def test_max_workers_defaults_to_the_pool_size_and_is_bounded():
    assert scheduler.cohort_workers({}) == scheduler.MAX_MIXED_WORKERS
    assert scheduler.cohort_workers({'max_workers': 8}) == 8
    for bad in (0, -1, scheduler.MAX_MIXED_WORKERS + 1, '8', 8.0, True):
        with pytest.raises(ValueError):
            scheduler.cohort_workers({'max_workers': bad})


def test_contended_admission_does_not_queue_dispatch_threads(tmp_path):
    root, _ = frozen_suite(tmp_path)
    atomic_json(root.parent/'predecessor/job-status.json', {'jobs':[{'status':'completed'}]})
    with scheduler.lock(root.parent/'.host-admission.lock'):
        assert scheduler._reserve_job(root) == 'waiting_for_admission_lock'
    assert all(j['status']=='queued' for j in scheduler.read(root/'job-status.json')['jobs'])


def test_adaptive_campaign_exceeds_old_cap_but_obeys_host_budget(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    root, registry = frozen_suite(tmp_path, arms=tuple(f'arm{i}' for i in range(6)))
    spec = scheduler.read(root/'regression.json')
    spec['max_workers'] = 8
    atomic_json(root/'regression.json', spec)
    freeze = scheduler.read(root/'freeze.json')
    freeze['spec_sha256'] = scheduler.sha(root/'regression.json')
    atomic_json(root/'freeze.json', freeze)
    atomic_json(registry/'predecessor/job-status.json', {'jobs':[{'status':'completed'}]})
    release, full = threading.Event(), threading.Event()
    def budget(*args, **kwargs):
        jobs = scheduler.read(root/'job-status.json')['jobs']
        return sum(bool(j.get('admission_reserved')) for j in jobs) < 10
    def execute(root, executor, spec, freeze, job):
        if sum(bool(j.get('admission_reserved')) for j in scheduler.read(root/'job-status.json')['jobs']) == 10:
            full.set()
        assert release.wait(25)
        scheduler._update_job(root, job['id'], {'status':'completed','admission_reserved':False,'cleanup_completed':True})
        return 'completed'
    # Remove the production spacing only in this bounded concurrency test.
    original_publish = scheduler._publish
    def publish(root, mutate, **kwargs):
        def without_spacing(status):
            status['last_admit'] = 0
            return mutate(status)
        return original_publish(root, without_spacing, **kwargs)
    monkeypatch.setattr(scheduler, '_publish', publish)
    monkeypatch.setattr(scheduler, 'may_admit', budget)
    monkeypatch.setattr(scheduler, 'ports_free', lambda *args: True)
    monkeypatch.setattr(scheduler, '_execute_job', execute)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(scheduler.run_campaign, root, adaptive=True)
        try:
            assert full.wait(20), 'host budget of ten was never filled above the frozen cap of eight'
            jobs = scheduler.read(root/'job-status.json')['jobs']
            assert sum(bool(j.get('admission_reserved')) for j in jobs) == 10
            assert sum(j['status']=='queued' for j in jobs) == 2
        finally:
            release.set()
        assert result.result(timeout=20) == 'complete'
    assert scheduler.read(root/'dispatch-control.json')['frozen_max_workers'] == 8
    assert scheduler.sha(root/'regression.json') == freeze['spec_sha256']


def declared_contract():
    return {'version': 1, 'hypothesis': 'Treatment improves completion', 'factor': 'policy weight',
            'arms': {'control': 'reference', 'treatment': ['candidate']}, 'action_path': 'deterministic',
            'eligible_start': 'all full-origin starts', 'endpoint': 'C1 completion', 'predicate_version': 'c1-v1',
            'window': {'game_ticks': 1000, 'wall_seconds': 60}, 'sampling_unit': 'job',
            'sampling_rule': 'each planned seed and arm', 'missing_outcome_rule': 'retain_all_planned_jobs',
            'adoption_rule': 'docs/rl/laya_variants/README.md'}


def test_declared_contract_freeze_reservation_and_missing_outcomes(tmp_path, monkeypatch):
    root, registry = frozen_suite(tmp_path, contract=declared_contract())
    freeze = scheduler.read(root/'freeze.json')
    assert freeze['experiment_contract']['sha256'] == scheduler.sha(root/'experiment-contract.json')
    assert report(root)['experiment_contract']['state'] == 'valid_declared'
    atomic_json(registry/'predecessor/job-status.json', {'jobs': [{'status': 'completed'}]})
    monkeypatch.setattr(scheduler, 'may_admit', lambda *a, **kw: True)
    monkeypatch.setattr(scheduler, 'ports_free', lambda *a: True)
    assert scheduler._reserve_job(root)[2]['admission_reserved'] is True
    result = report(root)
    assert result['planned'] == 4 and sum(g['unknown'] for g in result['groups']) == 4
    assert result['exposure_evidence'] == result['effective_intervention_evidence'] == 'unknown'
    (root/'job-status.json').unlink()
    result = report(root)
    assert result['planned'] == 4 and sum(g['unknown'] for g in result['groups']) == 4


def test_legacy_and_calibration_remain_descriptive(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, 'frozen_capability', lambda *args: {'objective_modes': ['measurement_only']})
    root, _ = frozen_suite(tmp_path, spec_updates={'objective_mode': 'measurement_only'})
    result = report(root)
    assert result['experiment_contract']['state'] == 'descriptive_unknown'
    assert result['inference_eligibility'] == 'unknown' and result['planned'] == 4


@pytest.mark.parametrize('change', ['missing', 'arm', 'window', 'path', 'actual_path', 'unknown_field', 'boolean_tick'])
def test_bad_contract_rejected_before_freeze(tmp_path, change):
    contract = declared_contract()
    updates = {}
    if change == 'missing':
        del contract['hypothesis']
    elif change == 'arm':
        contract['arms']['control'] = 'absent'
    elif change == 'window':
        contract['window']['game_ticks'] += 1
    elif change == 'path':
        updates['action_path'] = 'geometry'
    elif change == 'actual_path':
        contract['action_path'] = 'geometry'
    elif change == 'unknown_field':
        contract['endpoint_typo'] = 'wrong'
    else:
        contract['window']['game_ticks'] = True
    with pytest.raises(ValueError):
        frozen_suite(tmp_path, contract=contract, spec_updates=updates)


@pytest.mark.parametrize('change', ['contract', 'missing', 'order', 'job', 'arm', 'status_arm'])
def test_tampered_binding_blocks_reservation(tmp_path, change):
    root, registry = frozen_suite(tmp_path, contract=declared_contract())
    atomic_json(registry/'predecessor/job-status.json', {'jobs': [{'status': 'completed'}]})
    if change == 'contract':
        (root/'experiment-contract.json').write_text('{}')
    elif change == 'missing':
        (root/'experiment-contract.json').unlink()
    elif change in ('order', 'job'):
        plan = scheduler.read(root/'planned-jobs.json')
        if change == 'order':
            plan['jobs'].reverse()
        else:
            plan['jobs'][0]['seed'] = 99
        atomic_json(root/'planned-jobs.json', plan)
    elif change == 'arm':
        freeze = scheduler.read(root/'freeze.json')
        freeze['arms'][0]['code_sha256'] = 'changed'
        atomic_json(root/'freeze.json', freeze)
    else:
        status = scheduler.read(root/'job-status.json')
        status['jobs'][0]['arm'] = 'reference'
        atomic_json(root/'job-status.json', status)
    with pytest.raises(ValueError):
        scheduler._reserve_job(root)
    assert not any(j.get('admission_reserved') for j in scheduler.read(root/'job-status.json')['jobs'])


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'wrong_arm', 'wrong_seed', 'malformed_file', 'plan_order'])
def test_contract_report_preserves_bound_denominator_and_identity(tmp_path, change):
    root, _ = frozen_suite(tmp_path, contract=declared_contract())
    status = scheduler.read(root/'job-status.json')
    expected = status['jobs'][0]
    if change == 'missing':
        status['jobs'].pop(0)
    elif change == 'duplicate':
        status['jobs'].append({**expected, 'successor_passed': True})
    elif change == 'wrong_arm':
        status['jobs'][0] = {**expected, 'arm': 'reference', 'successor_passed': True}
    elif change == 'wrong_seed':
        status['jobs'][0] = {**expected, 'seed': 99, 'successor_passed': True}
    elif change == 'plan_order':
        planned = scheduler.read(root/'planned-jobs.json')
        planned['jobs'].reverse()
        atomic_json(root/'planned-jobs.json', planned)
    atomic_json(root/'job-status.json', status)
    if change == 'malformed_file':
        (root/'job-status.json').write_text('{broken')
    result = report(root)
    assert result['denominator_evidence']['state'] == 'bound_plan'
    assert result['planned'] == 4 and sum(g['planned'] for g in result['groups']) == 4
    assert result['jobs'][0]['arm'] == expected['arm'] and result['jobs'][0]['seed'] == expected['seed']
    assert sum(g['observed_successors'] for g in result['groups']) == 0
    assert sum(g['unknown'] for g in result['groups']) == 4
    if change == 'plan_order':
        assert result['experiment_contract']['state'] == 'invalid'
    assert result['inference_eligibility'] == 'unknown'


def test_contract_publication_is_durable_and_exclusive(tmp_path, monkeypatch):
    from tools import laya_experiment_contract as contract
    binding = contract.make_binding(declared_contract(), [], [])
    original = contract.publish(tmp_path, binding)
    raw = (tmp_path/'experiment-contract.json').read_bytes()
    with pytest.raises(FileExistsError):
        contract.publish(tmp_path, {**binding, 'version': 2})
    assert (tmp_path/'experiment-contract.json').read_bytes() == raw
    assert original['sha256'] == scheduler.sha(tmp_path/'experiment-contract.json')
    failed = tmp_path/'failed'
    failed.mkdir()
    monkeypatch.setattr(contract.os, 'fsync', lambda _: (_ for _ in ()).throw(OSError('flush failed')))
    with pytest.raises(OSError):
        contract.publish(failed, binding)
    assert list(failed.iterdir()) == []


def test_binding_boolean_version_and_changed_spec_are_refused(tmp_path):
    root, _ = frozen_suite(tmp_path, contract=declared_contract())
    from tools import laya_experiment_contract as contract
    freeze = scheduler.read(root/'freeze.json')
    binding = contract.load_binding(root, freeze['experiment_contract'])
    binding['version'] = True
    (root/'experiment-contract.json').write_bytes(contract.canonical(binding))
    freeze['experiment_contract']['sha256'] = scheduler.sha(root/'experiment-contract.json')
    atomic_json(root/'freeze.json', freeze)
    assert report(root)['experiment_contract']['state'] == 'invalid'
    with pytest.raises(ValueError):
        scheduler._reserve_job(root)
    # Restore the original artifact, then change an otherwise unvalidated spec field.
    binding['version'] = 1
    (root/'experiment-contract.json').write_bytes(contract.canonical(binding))
    freeze['experiment_contract']['sha256'] = scheduler.sha(root/'experiment-contract.json')
    atomic_json(root/'freeze.json', freeze)
    spec = scheduler.read(root/'regression.json')
    spec['seeds'] = [99]
    atomic_json(root/'regression.json', spec)
    assert report(root)['experiment_contract']['state'] == 'invalid'


def test_seeded_assignment_boundaries(tmp_path, monkeypatch):
    from collections import Counter
    from tools import laya_mixed_design

    def mixed_jobs(origins, *, arms, policy_seeds, **kwargs):
        return {'jobs': [dict(id=f'pair-{i}-{arm}', pair_id=i, origin='C0', checkpoint='C0',
                              arm=arm, seed=seed, source=origins['C0'], strict_target=None, status='queued')
                         for i, seed in enumerate(policy_seeds) for arm in arms]}
    monkeypatch.setattr(laya_mixed_design, 'build_jobs', mixed_jobs)
    monkeypatch.setattr(scheduler, 'frozen_capability', lambda *args: {'objective_modes': ['measurement_only']})
    for mixed in (False, True):
        plans = []
        for seed in (41, 41, 42):
            case = tmp_path / f'{mixed}-{len(plans)}'
            case.mkdir()
            contract = declared_contract()
            contract['assignment'] = {'method': 'sha256-rotations-v1', 'seed': seed}
            updates = {'seeds': list(range(60 if mixed else 12))}
            if mixed:
                updates.update(design='mixed-start-v1', objective_mode='measurement_only', origins={'C0': {'generation_id': 'source'}},
                               factorio_version='2.1.23')
            root, registry = frozen_suite(case, contract=contract, spec_updates=updates)
            plan = scheduler.read(root/'planned-jobs.json')['jobs']
            plans.append([j['id'] for j in plan])
            assert len(set(plans[-1])) == len(plan) == 2 * len(updates['seeds'])
            assert all(len({j['seed'] for j in plan[i:i+2]}) == 1 for i in range(0, len(plan), 2))
            assert Counter(j['arm'] for j in plan[::2]) == Counter(j['arm'] for j in plan[1::2])
            assert report(root)['planned'] == len(plan)
            atomic_json(registry/'predecessor/job-status.json', {'jobs': [{'status': 'completed'}]})
            monkeypatch.setattr(scheduler, 'may_admit', lambda *a, **kw: True)
            monkeypatch.setattr(scheduler, 'ports_free', lambda *a: True)
            assert scheduler._reserve_job(root)[2]['id'] == plan[0]['id']
            reordered = scheduler.read(root/'planned-jobs.json')
            reordered['jobs'].reverse()
            atomic_json(root/'planned-jobs.json', reordered)
            with pytest.raises(ValueError):
                scheduler._reserve_job(root)
            assert report(root)['planned'] == len(plan)
        assert plans[0] == plans[1] and plans[0] != plans[2]
        assert set(plans[0]) == set(plans[2])


@pytest.mark.parametrize('declaration', [{'method': 'sha256-rotations-v1', 'seed': True},
                                          {'method': 'sha256-rotations-v1', 'seed': -1},
                                          {'method': 'unknown', 'seed': 1}])
def test_assignment_bad_declaration_refused_before_publication(tmp_path, declaration):
    contract = declared_contract()
    contract['assignment'] = declaration
    with pytest.raises(ValueError):
        frozen_suite(tmp_path, contract=contract)
    assert not (tmp_path/'registry').exists()


def test_assignment_rejects_incomplete_blocks_before_publication(tmp_path, monkeypatch):
    from tools import laya_experiment_assignment
    original = laya_experiment_assignment.assign
    def broken(jobs, *args, **kwargs):
        return original(jobs[:-1], *args, **kwargs)
    monkeypatch.setattr(laya_experiment_assignment, 'assign', broken)
    contract = declared_contract()
    contract['assignment'] = {'method': 'sha256-rotations-v1', 'seed': 3}
    with pytest.raises(ValueError, match='complete matched'):
        frozen_suite(tmp_path, contract=contract)
    assert not (tmp_path/'registry').exists()


def test_assignment_rotations_balance_remainders_and_reject_bad_blocks():
    from collections import Counter
    from tools.laya_experiment_assignment import assign
    arms = ['a', 'b', 'c', 'd']
    jobs = [dict(id=f'{seed}-{arm}', arm=arm, seed=seed, origin='C0', bundle=None,
                 strict_target=None) for seed in range(11) for arm in arms]
    declaration = {'method': 'sha256-rotations-v1', 'seed': 7}
    ordered = assign(jobs, arms, declaration, mixed=False)
    for position in range(len(arms)):
        counts = Counter(row['arm'] for row in ordered[position::len(arms)])
        assert max(counts.values()) - min(counts.values()) <= 1
    assert assign(ordered, arms, declaration, mixed=False) == ordered
    for bad in (jobs[:-1], jobs + [jobs[0]], jobs[:1] + [{**jobs[1], 'strict_target': 'C2'}] + jobs[2:]):
        with pytest.raises(ValueError):
            assign(bad, arms, declaration, mixed=False)


def test_assignment_recomputed_even_if_binding_and_plan_rewritten(tmp_path):
    from tools import laya_experiment_contract as contract_module
    contract = declared_contract()
    contract['assignment'] = {'method': 'sha256-rotations-v1', 'seed': 5}
    root, _ = frozen_suite(tmp_path, contract=contract, spec_updates={'seeds': list(range(8))})
    freeze = scheduler.read(root/'freeze.json')
    binding = contract_module.load_binding(root, freeze['experiment_contract'])
    binding['planned_jobs'].reverse()
    (root/'experiment-contract.json').write_bytes(contract_module.canonical(binding))
    freeze['experiment_contract']['sha256'] = scheduler.sha(root/'experiment-contract.json')
    atomic_json(root/'freeze.json', freeze)
    atomic_json(root/'planned-jobs.json', {'version': 1, 'jobs': binding['planned_jobs']})
    with pytest.raises(ValueError, match='assignment order'):
        scheduler._reserve_job(root)


def test_assignment_uses_real_mixed_origin_generator_before_freeze_and_admission(tmp_path, monkeypatch):
    import hashlib
    from collections import Counter
    from tools import laya_checkpoint_bank
    from tools.laya_mixed_design import build_jobs
    origins = {f'C{i}': {'generation_id': f'g{i}', 'validated': True, 'immutable': True} for i in range(7)}
    for origin, source in origins.items():
        if origin == 'C0':
            continue
        bundle = tmp_path / 'bundles' / origin
        bundle.mkdir(parents=True)
        (bundle / 'checkpoint.json').write_text('{}')
        source.update(bundle=str(bundle), bank=str(tmp_path / 'bank'))
    predicate_hash = hashlib.sha256(b'# fixture source\n').hexdigest()
    def checkpoint_metadata(path):
        name = Path(path).name
        origin = 'C' + name[1:] if name.startswith('g') else name
        return ({'checkpoint_id': origin, 'generation_id': origins[origin]['generation_id']},
                {'predicate_sha256': predicate_hash})
    monkeypatch.setattr(scheduler, 'load_checkpoint', checkpoint_metadata)
    monkeypatch.setattr(laya_checkpoint_bank, 'validated_receipts', lambda *a: [{'offline': True}])
    monkeypatch.setattr(scheduler, 'frozen_capability', lambda *a: {'objective_modes': ['measurement_only']})
    contract = declared_contract()
    contract['assignment'] = {'method': 'sha256-rotations-v1', 'seed': 2026}
    root, registry = frozen_suite(tmp_path, contract=contract, spec_updates={
        'seeds': list(range(60)), 'total_runs': 120, 'design': 'mixed-start-v1', 'objective_mode': 'measurement_only',
        'origins': origins, 'factorio_version': '2.1.24'})
    freeze = scheduler.read(root / 'freeze.json')
    original = build_jobs(origins, arms=('candidate', 'reference'), policy_seeds=range(60),
                          engine='2.1.24', mods=freeze['mods_sha256'], budget={'ticks': 1000, 'wall_seconds': 60})['jobs']
    plan = scheduler.read(root / 'planned-jobs.json')['jobs']
    fields = ('id', 'pair_id', 'origin', 'checkpoint', 'arm', 'seed', 'source',
              'generation_id', 'engine', 'mods', 'budget', 'strict_target')
    by_id = lambda rows: {row['id']: {field: row[field] for field in fields} for row in rows}
    assert by_id(plan) == by_id(original)
    assert Counter(row['origin'] for row in plan) == Counter(row['origin'] for row in original)
    assert Counter(row['arm'] for row in plan[::2]) == Counter(row['arm'] for row in plan[1::2])
    assert [row['id'] for row in plan] != [row['id'] for row in original]
    atomic_json(registry / 'predecessor/job-status.json', {'jobs': [{'status': 'completed'}]})
    monkeypatch.setattr(scheduler, 'may_admit', lambda *a, **k: True)
    monkeypatch.setattr(scheduler, 'ports_free', lambda *a: True)
    assert scheduler._reserve_job(root)[2]['id'] == plan[0]['id']
    assert report(root)['planned'] == len(original) == 120


def test_assignment_nonfinite_identity_fails_before_publication(tmp_path, monkeypatch):
    from tools import laya_experiment_assignment
    original = laya_experiment_assignment.assign
    def invalid(jobs, *args, **kwargs):
        for job in jobs:
            job['unknown_source_fact'] = float('nan')
        return original(jobs, *args, **kwargs)
    monkeypatch.setattr(laya_experiment_assignment, 'assign', invalid)
    contract = declared_contract()
    contract['assignment'] = {'method': 'sha256-rotations-v1', 'seed': 1}
    with pytest.raises(ValueError, match='finite JSON'):
        frozen_suite(tmp_path, contract=contract)
    assert not (tmp_path / 'registry').exists()


def test_assignment_checkpoint_order_survives_input_relocation_at_freeze(tmp_path, monkeypatch):
    import hashlib
    bundle = tmp_path / 'original-checkpoint'
    bundle.mkdir()
    (bundle / 'checkpoint.json').write_text('{}')
    predicate_hash = hashlib.sha256(b'# fixture source\n').hexdigest()
    monkeypatch.setattr(scheduler, 'load_checkpoint', lambda path: (
        {'checkpoint_id': 'C2', 'generation_id': 'generation-C2'}, {'predicate_sha256': predicate_hash}))
    contract = declared_contract()
    contract['assignment'] = {'method': 'sha256-rotations-v1', 'seed': 29}
    root, registry = frozen_suite(tmp_path, contract=contract, spec_updates={
        'seeds': list(range(8)), 'checkpoints': [str(bundle)]})
    plan = scheduler.read(root / 'planned-jobs.json')['jobs']
    assert len(plan) == 32
    assert all(row['bundle'] != str(bundle) for row in plan if row['origin'] == 'C2')
    atomic_json(registry / 'predecessor/job-status.json', {'jobs': [{'status': 'completed'}]})
    monkeypatch.setattr(scheduler, 'may_admit', lambda *a, **k: True)
    monkeypatch.setattr(scheduler, 'ports_free', lambda *a: True)
    assert scheduler._reserve_job(root)[2]['id'] == plan[0]['id']
    assert report(root)['experiment_contract']['state'] == 'valid_declared'
