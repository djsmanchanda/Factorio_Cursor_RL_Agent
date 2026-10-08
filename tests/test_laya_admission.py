# Path: tests/test_laya_admission.py | Purpose: Protect finish-first concurrency draining and fair startup reservations.
import json
import os
from pathlib import Path

import pytest

from tools import laya_admission as admission


def fixture(tmp_path, limit=25):
    scope = tmp_path / 'exp-a'
    scope.mkdir()
    control = tmp_path / 'admission-control.json'
    control.write_text(json.dumps({'version': 'laya-admission-v1', 'limit': limit, 'scopes': [str(scope)]}))
    return scope / 'next', control


def test_draining_waits_until_below_cap_and_does_not_signal_active_runs(tmp_path, monkeypatch):
    root, control = fixture(tmp_path)
    samples = iter([40, 25, 24])
    waits = []
    monkeypatch.setattr(admission, '_live_worlds', lambda _: {str(i) for i in range(next(samples))})
    monkeypatch.setattr(admission.time, 'sleep', waits.append)
    monkeypatch.setattr(admission.os, 'kill', lambda *_: pytest.fail('admission must never signal active runs'))
    admission.wait_for_admission(root, control, poll_seconds=0)
    assert len(waits) == 2
    state = json.loads(control.with_name('admission-state.json').read_text())
    assert state['tickets'][0]['status'] == 'reserved'
    assert not root.exists()


def test_live_reservation_counts_before_a_server_pid_exists(tmp_path, monkeypatch):
    root, control = fixture(tmp_path, limit=1)
    state_path = control.with_name('admission-state.json')
    first = {'root': str(root.parent / 'first'), 'pid': 1000001, 'stamp': 'alive',
             'parent_pid': 1000002, 'parent_stamp': 'parent', 'status': 'reserved'}
    state_path.write_text(json.dumps({'tickets': [first]}))
    original = admission._process_stamp
    monkeypatch.setattr(admission, '_process_stamp', lambda pid: {1000001:'alive',1000002:'parent'}.get(pid, original(pid)))
    monkeypatch.setattr(admission, '_live_worlds', lambda _: set())
    waits = []

    def finish(_):
        waits.append(True)
        state_path.write_text(json.dumps({'tickets': []}))

    monkeypatch.setattr(admission.time, 'sleep', finish)
    admission.wait_for_admission(root, control, poll_seconds=0)
    assert waits == [True]


def test_waiting_tickets_remain_fifo(tmp_path, monkeypatch):
    root, control = fixture(tmp_path, limit=2)
    state_path = control.with_name('admission-state.json')
    first = {'root': str(root.parent / 'first'), 'pid': 1000001, 'stamp': 'alive',
             'parent_pid': 1000002, 'parent_stamp': 'parent', 'status': 'waiting'}
    state_path.write_text(json.dumps({'tickets': [first]}))
    original = admission._process_stamp
    monkeypatch.setattr(admission, '_process_stamp', lambda pid: {1000001:'alive',1000002:'parent'}.get(pid, original(pid)))
    monkeypatch.setattr(admission, '_live_worlds', lambda _: set())
    waits = []

    def admit_first(_):
        waits.append(True)
        state = json.loads(state_path.read_text())
        state['tickets'][0]['status'] = 'reserved'
        state_path.write_text(json.dumps(state))

    monkeypatch.setattr(admission.time, 'sleep', admit_first)
    admission.wait_for_admission(root, control, poll_seconds=0)
    assert waits == [True]
    assert [t['status'] for t in json.loads(state_path.read_text())['tickets']] == ['reserved','reserved']


def test_stale_pid_generation_does_not_hold_a_slot(tmp_path, monkeypatch):
    root, control = fixture(tmp_path, limit=1)
    state_path = control.with_name('admission-state.json')
    stale = {'root': str(root.parent / 'old'), 'pid': os.getpid(), 'stamp': 'old-generation',
             'parent_pid': os.getppid(), 'parent_stamp': admission._process_stamp(os.getppid()), 'status': 'reserved'}
    state_path.write_text(json.dumps({'tickets': [stale]}))
    monkeypatch.setattr(admission, '_live_worlds', lambda _: set())
    admission.wait_for_admission(root, control, poll_seconds=0)
    tickets = json.loads(state_path.read_text())['tickets']
    assert len(tickets) == 1 and tickets[0]['stamp'] != 'old-generation'


def test_unscoped_world_and_absent_control_keep_existing_behavior(tmp_path):
    admission.wait_for_admission(tmp_path/'world', tmp_path/'missing.json')
    root, control = fixture(tmp_path)
    admission.wait_for_admission(tmp_path/'other'/'world', control)
    assert not control.with_name('admission-state.json').exists()


def test_waiter_exits_if_its_preparation_parent_ends(tmp_path, monkeypatch):
    root, control = fixture(tmp_path, limit=0)
    monkeypatch.setattr(admission, '_live_worlds', lambda _: set())
    original = admission._process_stamp
    parent_alive = [True]
    monkeypatch.setattr(admission, '_process_stamp', lambda pid: None if pid == os.getppid() and not parent_alive[0] else original(pid))
    monkeypatch.setattr(admission.time, 'sleep', lambda _: parent_alive.__setitem__(0, False))
    with pytest.raises(RuntimeError, match='parent ended'):
        admission.wait_for_admission(root, control, poll_seconds=0)
