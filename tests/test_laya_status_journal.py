# Path: tests/test_laya_status_journal.py | Purpose: Exercise scheduler durable publication and recovery without live services.
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from tools import laya_regression_scheduler as scheduler
from tools.checkpoint_catalog import atomic_json
from tools.laya_status_journal import JournalUnavailable

pytestmark = [pytest.mark.ops, pytest.mark.fast]


def setup_root(root):
    jobs = [{'id': 'a', 'arm': 'arm', 'status': 'queued', 'admission_reserved': False},
            {'id': 'b', 'arm': 'arm', 'status': 'queued', 'admission_reserved': False}]
    atomic_json(root / 'planned-jobs.json', {'version': 1, 'jobs': jobs})
    atomic_json(root / 'job-status.json', {'jobs': jobs})
    atomic_json(root / 'regression.json', {'python': 'python', 'factorio_binary': '/tmp/a/b/bin'})
    return jobs


def test_failed_projection_replays_result_and_cleanup_reservation(tmp_path, monkeypatch):
    setup_root(tmp_path)
    scheduler._update_job(tmp_path, 'a', {'status': 'running', 'admission_reserved': True, 'world': '/tmp/world'})
    atomic = scheduler.atomic_json
    monkeypatch.setattr(scheduler, 'atomic_json', lambda *args: (_ for _ in ()).throw(OSError('replace failed')))
    with pytest.raises(OSError, match='replace failed'):
        scheduler._update_job(tmp_path, 'a', {'status': 'completed', 'result': {'score': 42}})
    monkeypatch.setattr(scheduler, 'atomic_json', atomic)
    monkeypatch.setattr(scheduler, 'scoped_stop', lambda *args: (_ for _ in ()).throw(ValueError('cleanup failed')))
    with pytest.raises(ValueError, match='cleanup failed'):
        scheduler.recover(tmp_path)
    job = scheduler.read(tmp_path / 'job-status.json')['jobs'][0]
    assert job['status'] == 'completed' and job['result'] == {'score': 42} and job['admission_reserved']
    monkeypatch.setattr(scheduler, 'scoped_stop', lambda *args: None)
    scheduler.recover(tmp_path)
    job = scheduler.read(tmp_path / 'job-status.json')['jobs'][0]
    assert job['result'] == {'score': 42} and job['cleanup_completed'] and not job['admission_reserved']


def test_concurrent_updates_and_missing_projection(tmp_path):
    setup_root(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda identity: scheduler._update_job(tmp_path, identity, {'status': 'completed'}), ['a', 'b']))
    (tmp_path / 'job-status.json').unlink()
    scheduler._publish(tmp_path, lambda status: None)
    status = scheduler.read(tmp_path / 'job-status.json')
    assert [r['status'] for r in status['jobs']] == ['completed', 'completed']
    assert status['accounting']['planned_jobs'] == 2


@pytest.mark.parametrize('corruption', [b'{"version":1', b'{"version":1}\n'])
def test_corrupt_journal_prevents_publication_and_recovery(tmp_path, corruption):
    setup_root(tmp_path)
    scheduler._publish(tmp_path, lambda status: None)
    with (tmp_path / 'job-status.journal.jsonl').open('ab') as stream:
        stream.write(corruption)
    with pytest.raises(JournalUnavailable):
        scheduler._publish(tmp_path, lambda status: pytest.fail('must not dispatch'))
    with pytest.raises(JournalUnavailable):
        scheduler.recover(tmp_path)


def test_missing_legacy_row_is_unknown_and_stays_in_denominator(tmp_path):
    jobs = setup_root(tmp_path)
    atomic_json(tmp_path / 'job-status.json', {'jobs': jobs[:1]})
    scheduler._publish(tmp_path, lambda status: None)
    status = scheduler.read(tmp_path / 'job-status.json')
    assert status['jobs'][1]['status'] == 'unknown'
    assert status['jobs'][1]['admission_reserved'] is None
    assert status['accounting']['planned_jobs'] == 2
    baseline = json.loads((tmp_path / 'job-status.journal.jsonl').read_text().splitlines()[0])
    assert baseline['provenance'] == 'unknown'


@pytest.mark.parametrize('event', [
    b'{"version":true,"seq":3,"kind":"delta","rows":{},"metadata":{"set":{},"delete":[]}}\n',
    b'{"version":1,"version":1,"seq":3,"kind":"delta","rows":{},"metadata":{"set":{},"delete":[]}}\n',
    b'{"version":1,"seq":3,"kind":"delta","rows":{},"metadata":{"set":{"updated":NaN},"delete":[]}}\n'])
def test_ambiguous_json_is_not_accepted_as_durable_state(tmp_path, event):
    setup_root(tmp_path)
    scheduler._publish(tmp_path, lambda status: None)
    with (tmp_path / 'job-status.journal.jsonl').open('ab') as handle:
        handle.write(event)
    with pytest.raises(JournalUnavailable):
        scheduler._publish(tmp_path, lambda status: pytest.fail('dispatch must stop'))


def test_own_publications_reuse_validated_state_but_external_edits_force_replay(tmp_path, monkeypatch):
    from tools import laya_status_journal as journal
    setup_root(tmp_path)
    scheduler._publish(tmp_path, lambda status: None)
    def unexpected_read(*args):
        pytest.fail('unchanged validated journal should not be reparsed')
    with monkeypatch.context() as patch:
        patch.setattr(journal.Path, 'read_bytes', unexpected_read)
        scheduler._update_job(tmp_path, 'a', {'status': 'running'})
        scheduler._update_job(tmp_path, 'b', {'status': 'completed'})
    # A same-size edit changes file identity metadata and cannot hide behind cache.
    path = tmp_path / 'job-status.journal.jsonl'
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b'"version":1', b'"version":9', 1))
    with pytest.raises(JournalUnavailable):
        scheduler._publish(tmp_path, lambda status: pytest.fail('must validate externally edited evidence'))
