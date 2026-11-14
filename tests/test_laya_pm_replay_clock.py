# Path: tests/test_laya_pm_replay_clock.py | Purpose: Exercise replay reload, live clock gating, and cleanup with fake boundaries.

import json

import pytest

from tools import laya_pm_replay as replay

pytestmark = pytest.mark.fast


class Time:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds


class Adapter:
    def __init__(self, events, ticks):
        self.events = events
        self.ticks = iter(ticks)
        self.current = 100

    def tick(self):
        self.current = next(self.ticks, self.current)
        self.events.append(('tick', self.current))
        return self.current

    def go_live(self):
        self.events.append('live')

    def close(self):
        self.events.append('adapter_close')


class Session:
    def __init__(self, events):
        self.events = events

    def run(self, operation, **kwargs):
        self.events.append('production')
        return {'code': 'material_shortage', 'state': 'supply_wait', 'ok': False}

    def economics(self):
        return {'cells': []}

    def close(self):
        self.events.append('session_close')


def run(tmp_path, ticks, *, deadline=20, max_steps=2, stall_seconds=1):
    events = ['paused_reload_verified']
    time = Time()
    result = replay.run_live_replay(
        {'reload_verified': True, 'observed_tick': 100},
        lambda: Adapter(events, ticks), lambda: Session(events), tmp_path / 'journal',
        deadline=deadline, clock=lambda: time.now, monotonic=lambda: time.now,
        sleep=time.sleep, stall_seconds=stall_seconds, max_steps=max_steps)
    return result, events


def test_verified_reload_then_live_and_each_attempt_has_new_tick(tmp_path):
    result, events = run(tmp_path, [100, 100, 101, 101, 102])
    assert events[:4] == ['paused_reload_verified', ('tick', 100), 'live', ('tick', 100)]
    assert [step['tick'] for step in result['steps']] == [101, 102]
    assert events.count('production') == 2
    assert result['outcome'] == 'attempt_budget_exhausted'
    assert all(step['state'] == 'supply_wait' for step in result['steps'])
    assert events[-2:] == ['session_close', 'adapter_close']
    rows = [json.loads(line) for line in (tmp_path / 'journal').read_text().splitlines()]
    assert rows[-1]['clock']['last_advanced_tick'] == 102


def test_stalled_clock_never_spends_attempts(tmp_path):
    result, events = run(tmp_path, [100])
    assert result['outcome'] == 'clock_not_advancing'
    assert result['steps'] == []
    assert 'production' not in events
    assert events[-2:] == ['session_close', 'adapter_close']


def test_stall_after_one_attempt_does_not_retry_same_tick(tmp_path):
    result, events = run(tmp_path, [100, 101])
    assert result['outcome'] == 'clock_not_advancing'
    assert len(result['steps']) == events.count('production') == 1


def test_deadline_ends_wait_and_closes_both_boundaries(tmp_path):
    result, events = run(tmp_path, [100], deadline=0.2, stall_seconds=5)
    assert result['outcome'] == 'deadline_reached'
    assert result['deadline_reached'] is True
    assert events[-2:] == ['session_close', 'adapter_close']


def test_unverified_reload_never_opens_adapter(tmp_path):
    events = []
    with pytest.raises(replay.ReplayError, match='reload'):
        replay.run_live_replay({'reload_verified': False}, lambda: events.append('open'),
                               lambda: None, tmp_path / 'journal')
    assert events == []


def test_game_tick_budget_stops_before_attempt(tmp_path):
    events = []
    time = Time()
    result = replay.run_live_replay(
        {'reload_verified': True, 'observed_tick': 100},
        lambda: Adapter(events, [100, 105]), lambda: Session(events), tmp_path / 'journal',
        max_game_ticks=5, clock=lambda: time.now, monotonic=lambda: time.now, sleep=time.sleep)
    assert result['outcome'] == 'game_tick_budget_exhausted'
    assert 'production' not in events


def test_real_reload_verification_precedes_live_start(tmp_path, monkeypatch):
    import training.laya_saved_world as saved_world

    events = []
    class PausedAdapter:
        def __init__(self, root):
            events.append('open_paused')
        def query(self):
            events.append('verify_paused')
            return {'tick': 100}
        def close(self):
            events.append('close_paused')

    monkeypatch.setattr(saved_world, 'SavedWorldAdapter', PausedAdapter)
    receipt = replay.reload_receipt(tmp_path, 100)
    time = Time()
    replay.run_live_replay(receipt, lambda: Adapter(events, [100, 101]),
                           lambda: Session(events), tmp_path / 'journal', max_steps=1,
                           clock=lambda: time.now, monotonic=lambda: time.now, sleep=time.sleep)
    assert events.index('verify_paused') < events.index('close_paused') < events.index('live')
    assert events.index('live') < events.index('production')


@pytest.mark.parametrize('failure', [None, 'reload', 'prepare'])
def test_prepared_world_is_stopped_on_completion_or_error(tmp_path, monkeypatch, failure):
    import subprocess

    root = tmp_path / 'new-world'
    calls = []

    def prepare(bundle, world, **kwargs):
        world.mkdir()
        (world / 'manifest.json').write_text('{}')
        if failure == 'prepare':
            raise replay.ReplayError('prepare failed after boot')
        return {'prepared': True}

    monkeypatch.setattr(replay, 'materialize', prepare)
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs: calls.append((command, kwargs)))
    try:
        with replay.replay_world(tmp_path / 'bundle', root, runtime_root=tmp_path / 'engine') as receipt:
            assert receipt['prepared'] is True
            if failure == 'reload':
                raise replay.ReplayError('reload failed')
    except replay.ReplayError:
        assert failure is not None
    assert len(calls) == 1
    command, options = calls[0]
    assert command[-3:] == ['stop', '--root', str(root)]
    assert options['env']['LAYA_CHECKPOINT_RUNTIME_ROOT'] == str(tmp_path / 'engine')
    assert options['timeout'] == 60


def test_existing_world_is_never_prepared_or_stopped(tmp_path, monkeypatch):
    import subprocess

    calls = []
    monkeypatch.setattr(replay, 'materialize', lambda *args, **kwargs: calls.append('prepare'))
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: calls.append('stop'))
    with pytest.raises(replay.ReplayError, match='fresh'):
        with replay.replay_world(tmp_path / 'bundle', tmp_path):
            pytest.fail('existing world must not be entered')
    assert calls == []


def test_regressing_clock_is_distinct_and_cleans_up(tmp_path):
    result, events = run(tmp_path, [100, 101, 99])
    assert result['outcome'] == 'clock_not_advancing'
    assert events.count('production') == 1
    assert events[-2:] == ['session_close', 'adapter_close']
