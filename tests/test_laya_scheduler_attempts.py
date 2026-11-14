# Path: tests/test_laya_scheduler_attempts.py | Purpose: Verify durable scheduler invocation and interruption evidence.
import subprocess
from types import SimpleNamespace

import pytest

from tools import laya_regression_scheduler as scheduler
from tools.checkpoint_catalog import atomic_json
from tests.test_laya_regression_scheduler import frozen_suite

pytestmark = pytest.mark.fast


def setup_dispatch(tmp_path, monkeypatch):
    root, registry = frozen_suite(tmp_path)
    atomic_json(registry/'predecessor/job-status.json', {'jobs':[{'status':'completed'}]})
    monkeypatch.setattr(scheduler, 'may_admit', lambda *a, **kw: True)
    monkeypatch.setattr(scheduler, 'ports_free', lambda *a: True)
    monkeypatch.setattr(scheduler, 'verify_engine', lambda *a: None)
    monkeypatch.setattr(scheduler, 'scoped_stop', lambda *a: None)
    return root


def job(root):
    return scheduler.read(root/'job-status.json')['jobs'][0]


@pytest.mark.parametrize(('failure', 'expected'), [
    (subprocess.CalledProcessError(7, 'cmd'), {'kind':'exit', 'code':7}),
    (subprocess.CalledProcessError(-9, 'cmd'), {'kind':'signal', 'signal':9}),
    (subprocess.TimeoutExpired('cmd', 1), {'kind':'timeout'}),
    (ValueError('failure'), {'kind':'exception', 'type':'ValueError'}),
    (KeyboardInterrupt(), {'kind':'interruption', 'type':'KeyboardInterrupt'}),
    (SystemExit(2), {'kind':'interruption', 'type':'SystemExit'}),
])
def test_receipts_and_terminal_evidence(tmp_path, monkeypatch, failure, expected):
    root = setup_dispatch(tmp_path, monkeypatch)
    calls = []
    def executor(argv, **kwargs):
        calls.append((argv, str(kwargs['cwd'])))
        if argv[2] == 'run':
            raise failure
        return subprocess.CompletedProcess(argv, 0)
    if isinstance(failure, (KeyboardInterrupt, SystemExit)):
        with pytest.raises(type(failure)):
            scheduler.run_one(root, executor=executor)
    else:
        scheduler.run_one(root, executor=executor)
    row = job(root)
    attempt = row['attempts'][0]
    assert attempt['version'] == 1 and attempt['id']
    assert attempt['coordinator']['pid'] and 'creation_token' in attempt['coordinator']
    assert attempt['child_identity'] is None and attempt['runner_identity'] is None
    assert [(x['argv'], x['cwd']) for x in attempt['invocations']] == calls
    assert [x['phase'] for x in attempt['invocations']] == ['prepare', 'run']
    assert attempt['invocations'][0]['outcome'] == {'kind':'exit', 'code':0}
    assert attempt['invocations'][1]['outcome'] == expected
    assert all(x['finished'] >= x['started'] for x in attempt['invocations'])
    assert attempt['finished'] >= attempt['started']
    assert row['status'] in scheduler.TERMINAL and not row['admission_reserved']
    assert len(scheduler.read(root/'job-status.json')['jobs']) == 4


def test_cleanup_failure_recovery_and_retry_retains_attempt(tmp_path, monkeypatch):
    root = setup_dispatch(tmp_path, monkeypatch)
    monkeypatch.setattr(scheduler, 'scoped_stop', lambda *a: (_ for _ in ()).throw(ValueError('cleanup')))
    def fail(argv, **kwargs):
        raise subprocess.CalledProcessError(3, argv)
    with pytest.raises(ValueError, match='cleanup'):
        scheduler.run_one(root, executor=fail)
    assert job(root)['admission_reserved'] and not job(root)['cleanup_completed']
    with pytest.raises(ValueError, match='cleanup'):
        scheduler.run_one(root, executor=lambda *a, **k: pytest.fail('dispatched during recovery'))
    monkeypatch.setattr(scheduler, 'scoped_stop', lambda *a: None)
    scheduler.recover(root)
    assert not job(root)['admission_reserved']
    scheduler._publish(root, lambda status: (status['jobs'][0].update(status='queued'), status.update(last_admit=0)))
    scheduler.run_one(root, executor=fail)
    attempts = job(root)['attempts']
    assert len(attempts) == 2 and attempts[0]['id'] != attempts[1]['id']
    assert attempts[0]['invocations'][0]['outcome'] == {'kind':'exit', 'code':3}


def test_terminal_projection_crash_keeps_reservation_for_recovery(tmp_path, monkeypatch):
    root = setup_dispatch(tmp_path, monkeypatch)
    original = scheduler.atomic_json
    def crash(path, data):
        if path == root/'job-status.json' and data['jobs'][0].get('status') == 'infrastructure_failure':
            raise OSError('projection crash')
        return original(path, data)
    monkeypatch.setattr(scheduler, 'atomic_json', crash)
    with pytest.raises(OSError, match='projection crash'):
        scheduler.run_one(root, executor=lambda argv, **kw: (_ for _ in ()).throw(subprocess.CalledProcessError(4, argv)))
    monkeypatch.setattr(scheduler, 'atomic_json', original)
    monkeypatch.setattr(scheduler, 'scoped_stop', lambda *a: (_ for _ in ()).throw(ValueError('uncertain')))
    with pytest.raises(ValueError, match='uncertain'):
        scheduler.recover(root)
    assert job(root)['admission_reserved'] and job(root)['status'] == 'infrastructure_failure'
    monkeypatch.setattr(scheduler, 'scoped_stop', lambda *a: None)
    scheduler.recover(root)
    assert not job(root)['admission_reserved']


def test_interrupt_remains_visible_when_cleanup_fails(tmp_path, monkeypatch):
    root = setup_dispatch(tmp_path, monkeypatch)
    monkeypatch.setattr(scheduler, 'scoped_stop', lambda *a: (_ for _ in ()).throw(ValueError('cleanup')))
    with pytest.raises(KeyboardInterrupt) as error:
        scheduler.run_one(root, executor=lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert any('cleanup failed' in note for note in error.value.__notes__)
    assert job(root)['status'] == 'infrastructure_failure' and job(root)['admission_reserved']


@pytest.mark.parametrize('returned', [None, SimpleNamespace(returncode=True), SimpleNamespace(returncode='0')])
def test_missing_returncode_is_unknown_and_prevents_run_dispatch(tmp_path, monkeypatch, returned):
    root = setup_dispatch(tmp_path, monkeypatch)
    calls = []
    def executor(argv, **kwargs):
        calls.append(argv)
        return returned
    assert scheduler.run_one(root, executor=executor) == 'infrastructure_failure'
    assert len(calls) == 1 and calls[0][2] == 'prepare'
    assert job(root)['attempts'][0]['invocations'][0]['outcome'] == {
        'kind':'unknown', 'reason':'returncode_unavailable'}


@pytest.mark.parametrize('returncode', [7, -9])
def test_nonzero_return_is_failure_even_if_executor_ignores_check(tmp_path, monkeypatch, returncode):
    root = setup_dispatch(tmp_path, monkeypatch)
    calls = []
    def executor(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode)
    assert scheduler.run_one(root, executor=executor) == 'infrastructure_failure'
    assert len(calls) == 1
    expected = {'kind':'exit', 'code':returncode} if returncode > 0 else {'kind':'signal', 'signal':-returncode}
    assert job(root)['attempts'][0]['invocations'][0]['outcome'] == expected


def test_invocation_is_durable_before_dispatch(tmp_path, monkeypatch):
    root = setup_dispatch(tmp_path, monkeypatch)
    def executor(argv, **kwargs):
        attempt = job(root)['attempts'][0]
        receipt = attempt['invocations'][-1]
        assert receipt['argv'] == argv and receipt['cwd'] == str(kwargs['cwd'])
        assert receipt['outcome'] is None
        assert not any(key in receipt for key in ('env', 'password'))
        return subprocess.CompletedProcess(argv, 0)
    assert scheduler.run_one(root, executor=executor) == 'completed'


def test_dispatch_projection_failure_never_launches_and_recovers_unknown(tmp_path, monkeypatch):
    root = setup_dispatch(tmp_path, monkeypatch)
    original = scheduler.atomic_json
    def crash(path, data):
        attempts = (data.get('jobs') or [{}])[0].get('attempts') or []
        if path == root/'job-status.json' and attempts and attempts[-1]['invocations']:
            raise OSError('dispatch projection crash')
        return original(path, data)
    monkeypatch.setattr(scheduler, 'atomic_json', crash)
    with pytest.raises(OSError, match='dispatch projection crash'):
        scheduler.run_one(root, executor=lambda *a, **k: pytest.fail('dispatch was not published'))
    monkeypatch.setattr(scheduler, 'atomic_json', original)
    monkeypatch.setattr(scheduler, 'scoped_stop', lambda *a: (_ for _ in ()).throw(ValueError('still uncertain')))
    with pytest.raises(ValueError, match='still uncertain'):
        scheduler.recover(root)
    row = job(root)
    assert row['status'] == 'infrastructure_failure' and row['admission_reserved']
    assert row['attempts'][0]['invocations'][0]['outcome'] == {
        'kind':'unknown', 'reason':'coordinator_interrupted'}
    monkeypatch.setattr(scheduler, 'scoped_stop', lambda *a: None)
    scheduler.recover(root)
    assert job(root)['cleanup_completed'] and not job(root)['admission_reserved']
