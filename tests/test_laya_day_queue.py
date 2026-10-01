# Path: tests/test_laya_day_queue.py | Purpose: Protect interrupted seed blocks, explicit pauses and deadline-aware queue execution.

import datetime as dt
import json

from tools.laya_day_queue import Queue, predecessor_ready, resume_position


def manifest():
    return {'config': {'LAYA_RESUME_ROUND': '5', 'LAYA_L1_SEED': '5755', 'LAYA_L2_SEED': '6715',
                      'LAYA_L3_SEED': '8224', 'LAYA_INSTANCES': '01 02'},
            'deadline_utc': '2026-10-02T00:00:00+00:00', 'unit': 'owned.service'}


def test_resume_position_skips_started_partial_blocks():
    text = '[2026-10-02 02:00:00] rc02-r6: level 3 seeds 8224+24 continuation rollout\n'
    text += '[2026-10-02 02:30:00] rc02-r7: level 2 seeds 6715+24 continuation rollout\n'
    last, seeds = resume_position(manifest(), text)
    assert last == 7 and seeds == {1: 5755, 2: 6739, 3: 8248}


def test_explicit_stop_blocks_handoff_even_after_deadline(tmp_path):
    (tmp_path / 'summary.log').write_text('done; training workers stopped\n')
    assert predecessor_ready(tmp_path, manifest(), 0)
    (tmp_path / 'pause.json').write_text('{}')
    assert not predecessor_ready(tmp_path, manifest(), 2e9)
    (tmp_path / 'pause.json').unlink()
    (tmp_path / 'stop-requested').write_text('user stop')
    assert not predecessor_ready(tmp_path, manifest(), 2e9)


def test_only_planned_cutoff_can_recover_unfinished_predecessor(tmp_path):
    deadline = dt.datetime.fromisoformat(manifest()['deadline_utc']).timestamp()
    assert not predecessor_ready(tmp_path, manifest(), deadline - 1)
    assert predecessor_ready(tmp_path, manifest(), deadline)


def test_expired_queue_does_not_launch_a_process(tmp_path, monkeypatch):
    parent = tmp_path / 'parent'
    parent.mkdir()
    (parent / 'experiment.json').write_text(json.dumps(manifest()))
    run = tmp_path / 'queue'
    run.mkdir()
    queue = Queue(tmp_path, tmp_path, run, parent, dt.datetime.now(dt.timezone.utc))
    monkeypatch.setattr(queue, 'left', lambda: 119)
    monkeypatch.setattr('tools.laya_day_queue.subprocess.Popen', lambda *a, **k: (_ for _ in ()).throw(AssertionError('process launched')))
    assert queue.command(['unused'], 'expired') == 124
    assert json.loads((run / 'status.json').read_text())['phase'] == 'deadline_reached'


def test_command_records_actual_completion_without_factorio(tmp_path):
    import sys
    parent = tmp_path / 'parent'
    parent.mkdir()
    (parent / 'experiment.json').write_text(json.dumps(manifest()))
    run = tmp_path / 'queue'
    run.mkdir()
    queue = Queue(tmp_path, tmp_path, run, parent, dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10))
    assert queue.command([sys.executable, '-c', 'print("verified")'], 'smoke', cap=5) == 0
    assert (run / 'smoke.log').read_text().strip() == 'verified'
    report = json.loads((run / 'status.json').read_text())
    assert report['commands'][0]['exit_code'] == 0
    assert report['production_authority'] is False
