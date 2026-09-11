# Path: tests/test_campaign_supervisor.py
# Purpose: Protect bounded campaign supervision, ownership, and console evidence.
import json
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import campaign_supervisor as supervisor


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(server_data=tmp_path, python_bin=None, source_save=tmp_path / 'source.zip',
                           technology='mining-productivity-4', rcon_port=27017, game_port=34199,
                           runtime_root=None, gui_mods=None)


def setup_window(config, deadline):
    directory = config.server_data / 'logs/campaign-supervisor/window'
    supervisor.atomic_json(directory / 'settings.json', {'deadline_epoch': deadline})
    supervisor.atomic_json(directory.parent / 'current.json', {'settings': str(directory / 'settings.json')})
    return directory


def test_start_and_resume_refuse_existing_controller_lock(config, monkeypatch):
    monkeypatch.setattr(supervisor, '_busy', lambda _: False)
    with supervisor._exclusive(config, 'campaign-controller.lock'):
        assert supervisor.controller_busy(config)
        for action in (supervisor.start, supervisor.resume):
            with pytest.raises(ValueError, match='owns this runtime'):
                action(config)
    assert not supervisor.controller_busy(config)


def test_start_rejects_unexpired_window_and_resume_preserves_deadline(config, monkeypatch):
    monkeypatch.setattr(supervisor, '_busy', lambda _: False)
    monkeypatch.setattr(supervisor.time, 'time', lambda: 100)
    directory = setup_window(config, 200)
    launches = []
    monkeypatch.setattr(supervisor, '_launch', lambda cfg, path: launches.append(path))
    with pytest.raises(ValueError, match='unexpired'):
        supervisor.start(config)
    supervisor.resume(config)
    assert launches == [directory / 'settings.json']
    assert json.loads((directory / 'settings.json').read_text())['deadline_epoch'] == 200
    monkeypatch.setattr(supervisor.time, 'time', lambda: 201)
    with pytest.raises(ValueError, match='expired'):
        supervisor.resume(config)


def test_status_reads_role_states_and_bounded_findings(config, monkeypatch):
    monkeypatch.setattr(supervisor, '_busy', lambda _: False)
    directory = setup_window(config, 200)
    supervisor.atomic_json(directory / 'status.json', {'phase': 'observing'})
    role = directory / 'agents/observers/episode-1/inventory'
    supervisor.atomic_json(role / 'state.json', {'status': 'complete', 'checkpoint': 2})
    (role / 'findings.md').write_text('evidence' * 1000)
    (role.parent / 'board.md').write_text('board')
    data = supervisor.status(config)
    assert data['phase'] == 'interrupted'
    assert data['agents']['inventory']['checkpoint'] == 2
    assert data['notes'].startswith('board\n\nevidence')
    assert len(data['notes']) <= 16000


def test_group_cleanup_kills_descendants_even_if_leader_finished(monkeypatch):
    kills = []
    monkeypatch.setattr(supervisor.os, 'killpg', lambda pid, sig: kills.append((pid, sig)))
    supervisor._stop_process_group(SimpleNamespace(pid=123, wait=lambda **kwargs: 0))
    assert kills == [(123, signal.SIGTERM), (123, signal.SIGKILL)]


def test_deadline_terminates_controller_group(config, monkeypatch):
    directory = setup_window(config, 102)
    settings = {'deadline_epoch': 102, 'python': '/python', 'opencode': '/opencode',
                'state_root': str(config.server_data), 'source_save': '/source',
                'rcon_port': 27017, 'technology': 'mining-productivity-4'}
    supervisor.atomic_json(directory / 'settings.json', settings)
    times = iter([100, 103])
    monkeypatch.setattr(supervisor.time, 'time', lambda: next(times))
    launches, stopped = [], []
    process = SimpleNamespace(pid=123, poll=lambda: None)
    monkeypatch.setattr(supervisor.subprocess, 'Popen', lambda command, **kw: launches.append(kw) or process)
    monkeypatch.setattr(supervisor, '_stop_process_group', lambda p: stopped.append(p.pid))
    assert supervisor.supervise(directory / 'settings.json') == 0
    assert launches[0]['start_new_session'] is True
    assert stopped
    assert supervisor._read(directory / 'status.json')['phase'] == 'deadline'


def test_new_window_refuses_an_unowned_active_runner(config, monkeypatch):
    monkeypatch.setattr(supervisor, '_busy', lambda _: False)
    monkeypatch.setattr(supervisor, 'running_runner_pid', lambda _: 123)
    with pytest.raises(ValueError, match='runner is already active'):
        supervisor.start(config)


@pytest.mark.parametrize('owned,current,allowed', [('owned', 'owned', True), ('owned', 'other', False), (None, 'other', False)])
def test_resume_running_episode_requires_manifest_ownership(config, monkeypatch, owned, current, allowed):
    monkeypatch.setattr(supervisor, '_busy', lambda _: False)
    monkeypatch.setattr(supervisor, 'running_runner_pid', lambda _: 123)
    monkeypatch.setattr(supervisor.time, 'time', lambda: 100)
    directory = setup_window(config, 200)
    supervisor.atomic_json(directory / 'controller.json', {'active_episode_id': owned})
    supervisor.atomic_json(config.server_data / 'episode/current.json', {'episode_id': current})
    launches = []
    monkeypatch.setattr(supervisor, '_launch', lambda *args: launches.append(args))
    if allowed:
        supervisor.resume(config)
        assert len(launches) == 1
    else:
        with pytest.raises(ValueError, match='ownership'):
            supervisor.resume(config)
        assert not launches


@pytest.mark.parametrize('state,attempts', [({}, 1), ({'active_episode_id': 'owned'}, 4),
                                          ({'lifecycle_intent': {'expected_episode': 'owned'}}, 4)])
def test_nonzero_controller_recovery_is_bounded_and_preserves_deadline(config, monkeypatch, state, attempts):
    directory = setup_window(config, 200)
    settings = {'deadline_epoch': 200, 'python': '/python', 'opencode': '/opencode',
                'state_root': str(config.server_data), 'source_save': '/source',
                'rcon_port': 27017, 'technology': 'mining-productivity-4'}
    supervisor.atomic_json(directory / 'settings.json', settings)
    original_deadline = '1970-01-01T00:03:20+00:00'
    supervisor.atomic_json(directory / 'controller.json', {**state, 'deadline_utc': original_deadline})
    monkeypatch.setattr(supervisor.time, 'time', lambda: 100)
    monkeypatch.setattr(supervisor.time, 'sleep', lambda _: None)
    launches = []
    process = SimpleNamespace(pid=123, poll=lambda: 1, returncode=1)
    monkeypatch.setattr(supervisor.subprocess, 'Popen', lambda command, **kw: launches.append(command) or process)
    monkeypatch.setattr(supervisor, '_stop_process_group', lambda p: None)
    assert supervisor.supervise(directory / 'settings.json') == 1
    assert len(launches) == attempts
    assert supervisor._read(directory / 'status.json')['phase'] == 'parked'
    assert supervisor._read(directory / 'controller.json')['deadline_utc'] == original_deadline


def test_start_settings_and_controller_cli_contract(config, monkeypatch):
    from tools import opencode_campaign_orchestrator as controller
    monkeypatch.setattr(supervisor, '_busy', lambda _: False)
    monkeypatch.setattr(supervisor, 'running_runner_pid', lambda _: None)
    monkeypatch.setattr(supervisor.shutil, 'which', lambda _: '/bin/opencode')
    monkeypatch.setattr(supervisor.time, 'time', lambda: 100)
    monkeypatch.setattr(supervisor, '_launch', lambda *args: None)
    supervisor.start(config)
    settings_path = Path(supervisor._read(supervisor._root(config) / 'current.json')['settings'])
    settings = supervisor._read(settings_path)
    assert settings['deadline_epoch'] == 43300
    assert settings['model'] == supervisor.MODEL
    commands = []
    monkeypatch.setattr(supervisor.subprocess, 'Popen', lambda command, **kw: commands.append(command) or SimpleNamespace(pid=123, poll=lambda: 0, returncode=0))
    monkeypatch.setattr(supervisor, '_stop_process_group', lambda p: None)
    assert supervisor.supervise(settings_path) == 0
    parsed = []
    monkeypatch.setattr(controller, 'run_campaign', lambda cfg: parsed.append(cfg) or 0)
    assert controller.main(commands[0][3:]) == 0
    assert parsed[0].plastic_reliability is True
    assert parsed[0].aspect_observers is True
    assert parsed[0].model == supervisor.MODEL
    assert parsed[0].variant == supervisor.VARIANT


def test_resume_accepts_only_exact_pending_lifecycle_episode(config, monkeypatch):
    monkeypatch.setattr(supervisor, '_busy', lambda _: False)
    monkeypatch.setattr(supervisor, 'running_runner_pid', lambda _: 123)
    monkeypatch.setattr(supervisor.time, 'time', lambda: 100)
    directory = setup_window(config, 200)
    supervisor.atomic_json(directory / 'controller.json', {'lifecycle_intent': {'expected_episode': 'new', 'previous_episode': 'old'}})
    launches = []
    monkeypatch.setattr(supervisor, '_launch', lambda *args: launches.append(args))
    supervisor.atomic_json(config.server_data / 'episode/current.json', {'episode_id': 'unrelated'})
    with pytest.raises(ValueError, match='ownership'):
        supervisor.resume(config)
    supervisor.atomic_json(config.server_data / 'episode/current.json', {'episode_id': 'new'})
    supervisor.resume(config)
    assert len(launches) == 1
