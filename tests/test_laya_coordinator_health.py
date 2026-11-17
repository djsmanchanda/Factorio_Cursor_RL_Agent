# Path: tests/test_laya_coordinator_health.py | Purpose: Verify independent coordinator health and unavailable cohort evidence.
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from tools import laya_coordinator_health as health
from tools import laya_regression_scheduler as scheduler
from tools import laya_cohort_dashboard as dashboard


def test_dashboard_direct_script_imports_without_project_pythonpath():
    result = subprocess.run([sys.executable, str(Path(dashboard.__file__).resolve()), '--help'],
                            cwd=Path(dashboard.__file__).resolve().parents[1],
                            env={key: value for key, value in os.environ.items() if key != 'PYTHONPATH'},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert '--registry' in result.stdout


def read(root):
    return json.loads((root / health.NAME).read_text())


def test_pulses_under_status_lock_and_stops(tmp_path, monkeypatch):
    status = tmp_path / 'job-status.json'
    status.write_bytes(b'{"progress_updated":2,"jobs":[]}')
    original = status.read_bytes()
    published = threading.Event()
    real = health.atomic_json
    def record(path, value):
        real(path, value)
        if value['state'] == 'running':
            published.set()
    monkeypatch.setattr(health, 'atomic_json', record)
    with scheduler.lock(tmp_path / 'status.lock'):
        with health.coordinator_health(tmp_path, interval=0.01):
            published.clear()
            assert published.wait(2)
            assert status.read_bytes() == original
    assert read(tmp_path)['state'] == 'completed'
    published.clear()
    assert not published.wait(0.05)
    assert status.read_bytes() == original


@pytest.mark.parametrize(('error', 'state'), [(KeyboardInterrupt, 'interrupted'), (RuntimeError, 'error')])
def test_terminal_state(tmp_path, error, state):
    with pytest.raises(error):
        with health.coordinator_health(tmp_path):
            raise error()
    assert read(tmp_path)['state'] == state


def test_run_campaign_pulses_during_blocked_recovery(tmp_path, monkeypatch):
    entered, release, pulse = threading.Event(), threading.Event(), threading.Event()
    original = health.atomic_json
    def record(path, value):
        original(path, value)
        if entered.is_set() and value['state'] == 'running':
            pulse.set()
    def recover(root):
        entered.set()
        assert release.wait(2)
        raise RuntimeError('recovery failed')
    monkeypatch.setattr(health, 'atomic_json', record)
    monkeypatch.setattr(scheduler, 'recover', recover)
    real_context = health.coordinator_health
    monkeypatch.setattr(health, 'coordinator_health', lambda root: real_context(root, interval=0.01))
    errors = []
    def run():
        try:
            scheduler.run_campaign(tmp_path)
        except RuntimeError as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(2)
        assert pulse.wait(2)
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and errors == ['recovery failed']
    assert read(tmp_path)['state'] == 'error'


def test_identity_and_clocks_are_required(tmp_path, monkeypatch):
    with health.coordinator_health(tmp_path, wall=lambda: 100, monotonic=lambda: 20):
        value = read(tmp_path)
        assert health.present(value, root=tmp_path, wall=lambda: 101, monotonic=lambda: 21)['status'] == 'running'
        for changes in ({'boot_id': 'wrong'}, {'creation_token': -1}, {'pid': 99999999}, {'wall': 102},
                        {'monotonic': 22}, {'clock_regressed': True}, {'version': True},
                        {'creation_token': True}, {'wall': '100'}, {'generation': 'unknown'}, {'cohort_root': 'wrong'},
                        {'source': {'protocol': health.SOURCE, 'components_sha256': {}}}):
            assert health.present({**value, **changes}, root=tmp_path, wall=lambda: 101, monotonic=lambda: 21)['status'] == 'unknown'
        assert health.present(value, root=tmp_path, wall=lambda: 110, monotonic=lambda: 21)['status'] == 'unknown'
        assert health.present(value, root=tmp_path, wall=lambda: 140, monotonic=lambda: 60)['status'] == 'stale'
    assert health.present(None)['status'] == 'unknown'


def test_original_interruption_survives_failed_terminal_publication(tmp_path, monkeypatch):
    original = health.atomic_json
    def publish(path, value):
        if value['state'] != 'running':
            raise OSError('disk unavailable')
        original(path, value)
    monkeypatch.setattr(health, 'atomic_json', publish)
    error = KeyboardInterrupt('stop')
    with pytest.raises(KeyboardInterrupt) as caught:
        with health.coordinator_health(tmp_path):
            raise error
    assert caught.value is error
    assert any('health publication failed' in note for note in caught.value.__notes__)


def test_publisher_remembers_clock_regression(tmp_path):
    samples = iter((100, 99))
    with health.coordinator_health(tmp_path, wall=lambda: next(samples), monotonic=lambda: 20):
        pass
    value = read(tmp_path)
    assert value['clock_regressed'] is True
    assert health.present(value, root=tmp_path)['status'] == 'unknown'


@pytest.mark.parametrize('kind', ['oversized', 'malformed', 'missing', 'malformed_row', 'duplicate_job', 'nonfinite'])
def test_dashboard_preserves_plan_when_status_unavailable(tmp_path, kind):
    registry = tmp_path / 'registry'
    root = registry / 'cohort'
    root.mkdir(parents=True)
    (root / 'freeze.json').write_text(json.dumps({'planned_jobs': 7}))
    if kind == 'oversized':
        (root / 'job-status.json').write_bytes(b' ' * (dashboard.MAX_JSON + 1))
    elif kind == 'malformed':
        (root / 'job-status.json').write_text('{')
    elif kind == 'malformed_row':
        (root / 'job-status.json').write_text('{"jobs":["unknown"]}')
    elif kind == 'duplicate_job':
        (root / 'job-status.json').write_text('{"jobs":[{"id":"a","status":"running"},{"id":"a","status":"completed"}]}')
    elif kind == 'nonfinite':
        (root / 'job-status.json').write_text('{"jobs":[],"updated":NaN}')
    app = dashboard.Dashboard(tmp_path, registry, tmp_path / 'host', tmp_path / 'data')
    row = app.snapshot()['cohorts'][0]
    assert row['planned'] == 7 and row['counts'] is None and row['unaccounted'] is None
    assert row['status_availability'] == {'state': 'unavailable', 'cause': 'malformed' if kind in ('malformed_row', 'duplicate_job', 'nonfinite') else kind}
    assert row['coordinator_health']['status'] == 'unknown'


def test_live_coordinator_does_not_make_missing_status_available(tmp_path):
    registry = tmp_path / 'registry'
    root = registry / 'cohort'
    root.mkdir(parents=True)
    (root / 'freeze.json').write_text('{"planned_jobs":7}')
    app = dashboard.Dashboard(tmp_path, registry, tmp_path / 'host', tmp_path / 'data')
    with health.coordinator_health(root):
        row = app.snapshot()['cohorts'][0]
        assert row['coordinator_health']['status'] == 'running'
        assert row['status_availability'] == {'state': 'unavailable', 'cause': 'missing'}
        assert row['planned'] == 7 and row['counts'] is None
        assert health.present(read(root), root=tmp_path / 'other')['status'] == 'unknown'


def test_dashboard_reports_unreadable_status(tmp_path, monkeypatch):
    from pathlib import Path
    registry = tmp_path / 'registry'
    root = registry / 'cohort'
    root.mkdir(parents=True)
    (root / 'freeze.json').write_text('{"planned_jobs":7}')
    status = root / 'job-status.json'
    status.write_text('{"jobs":[]}')
    real_open = Path.open
    def blocked(path, *args, **kwargs):
        if path == status:
            raise PermissionError('unreadable')
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', blocked)
    app = dashboard.Dashboard(tmp_path, registry, tmp_path / 'host', tmp_path / 'data')
    row = app.snapshot()['cohorts'][0]
    assert row['planned'] == 7 and row['counts'] is None
    assert row['status_availability'] == {'state': 'unavailable', 'cause': 'unreadable'}


def test_oversized_status_is_not_read(tmp_path, monkeypatch):
    from pathlib import Path
    path = tmp_path / 'job-status.json'
    with path.open('wb') as output:
        output.truncate(dashboard.MAX_JSON + 1)
    monkeypatch.setattr(Path, 'open', lambda *args, **kwargs: pytest.fail('oversized file read'))
    assert dashboard.aggregate_status(path) == (None, 'oversized')


def test_dashboard_reuses_status_until_its_file_identity_changes(tmp_path, monkeypatch):
    registry = tmp_path / 'registry'
    root = registry / 'cohort'
    root.mkdir(parents=True)
    (root / 'freeze.json').write_text('{"planned_jobs":1}')
    status = root / 'job-status.json'
    status.write_text('{"jobs":[{"id":"a","status":"queued"}]}')
    calls = []
    original = dashboard.aggregate_status
    def count(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(dashboard, 'aggregate_status', count)
    app = dashboard.Dashboard(tmp_path, registry, tmp_path / 'host', tmp_path / 'data')
    assert app.snapshot()['cohorts'][0]['counts'] == {'queued': 1}
    assert app.snapshot()['cohorts'][0]['counts'] == {'queued': 1}
    assert calls == [status]
    replacement = root / 'new.json'
    replacement.write_text('{"jobs":[{"id":"a","status":"running"}]}')
    replacement.replace(status)
    assert app.snapshot()['cohorts'][0]['counts'] == {'running': 1}
    assert calls == [status, status]


def test_run_campaign_pulses_during_blocked_executor(tmp_path, monkeypatch):
    entered, release, pulse = threading.Event(), threading.Event(), threading.Event()
    (tmp_path / 'regression.json').write_text('{}')
    (tmp_path / 'job-status.json').write_text('{"jobs": [{"status": "queued"}]}')
    original = health.atomic_json
    def record(path, value):
        original(path, value)
        if entered.is_set() and value['state'] == 'running':
            pulse.set()
    def execute(root, executor, *args):
        entered.set()
        assert release.wait(2)
        (root / 'job-status.json').write_text('{"jobs": []}')
        return 'completed'
    monkeypatch.setattr(health, 'atomic_json', record)
    monkeypatch.setattr(scheduler, 'recover', lambda root: None)
    monkeypatch.setattr(scheduler, 'validate_port_ranges', lambda *args: None)
    monkeypatch.setattr(scheduler, '_reserve_job', lambda root: ())
    monkeypatch.setattr(scheduler, '_execute_job', execute)
    real_context = health.coordinator_health
    monkeypatch.setattr(health, 'coordinator_health', lambda root: real_context(root, interval=0.01))
    result = []
    thread = threading.Thread(target=lambda: result.append(scheduler.run_campaign(tmp_path, workers=1)))
    thread.start()
    try:
        assert entered.wait(2)
        assert pulse.wait(2)
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and result == ['complete']
    assert read(tmp_path)['state'] == 'completed'
