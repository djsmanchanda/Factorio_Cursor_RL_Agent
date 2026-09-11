# Path: tools/campaign_supervisor.py
# Purpose: Supervise one bounded local OpenCode campaign independently of the console process.
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

from tools.reliability_state import atomic_json
from tools.runner_process import running_runner_pid

MODEL = 'opencode-go/muse-spark-1.3-contributor'
VARIANT = 'xhigh'
REPO = Path(__file__).resolve().parents[1]


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _root(config) -> Path:
    return config.server_data / 'logs' / 'campaign-supervisor'


def _unit(config) -> str:
    token = hashlib.sha256(str(config.server_data.resolve()).encode()).hexdigest()[:12]
    return 'factorio-reliability-' + token


def _service(config, *arguments):
    return subprocess.run(['systemctl', '--user', *arguments, _unit(config)], capture_output=True, text=True, timeout=20)


def _launch(config, settings: Path) -> None:
    python = str(config.python_bin or REPO / '.venv/bin/python')
    opencode = shutil.which('opencode')
    if opencode is None:
        raise ValueError('OpenCode is not on the Operations Console PATH')
    command = ['systemd-run', '--user', '--collect', '--quiet', '--unit=' + _unit(config),
               '--property=KillMode=control-group', '--property=TimeoutStopSec=20',
               '--working-directory=' + str(REPO), '--setenv=PATH=' + os.environ.get('PATH', ''),
               '--property=StandardOutput=append:' + str(settings.parent / 'supervisor.log'),
               '--property=StandardError=append:' + str(settings.parent / 'supervisor.log'),
               python, '-m', 'tools.campaign_supervisor', '--settings', str(settings)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Could not start campaign service: ' + result.stderr[-1000:])


def _busy(config) -> bool:
    return _service(config, 'is-active', '--quiet').returncode == 0


@contextmanager
def _exclusive(config, name):
    path = config.server_data / 'logs' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Another campaign controller or lifecycle request owns this runtime') from error
        yield


def controller_busy(config) -> bool:
    if _busy(config):
        return True
    try:
        with _exclusive(config, 'campaign-controller.lock'):
            return False
    except ValueError:
        return True


def start(config) -> None:
    with _exclusive(config, 'campaign-supervisor.lock'):
        with _exclusive(config, 'campaign-controller.lock'):
            pass
        _start(config)


def resume(config) -> None:
    with _exclusive(config, 'campaign-supervisor.lock'):
        with _exclusive(config, 'campaign-controller.lock'):
            pass
        _resume(config)


def _start(config) -> None:
    if _busy(config):
        raise ValueError('A supervised campaign is already running')
    if running_runner_pid(config.server_data / 'logs/autonomous-run.pid'):
        raise ValueError('A runner is already active; stop it before starting a new supervised window')
    prior = _read(_root(config) / 'current.json')
    if prior:
        prior_settings = _read(Path(prior['settings']))
        if prior_settings.get('deadline_epoch', 0) > time.time():
            raise ValueError('An unexpired campaign window exists; use Resume to preserve its budget and pending work')
    run_dir = _root(config) / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    run_dir.mkdir(parents=True, exist_ok=False)
    settings = run_dir / 'settings.json'
    python = str(config.python_bin or REPO / '.venv/bin/python')
    opencode = shutil.which('opencode')
    if opencode is None:
        raise ValueError('OpenCode is not on the Operations Console PATH')
    payload = dict(version=1, state_root=str(config.server_data.resolve()), source_save=str(config.source_save.resolve()),
                   python=python, opencode=opencode, technology=config.technology, rcon_port=config.rcon_port,
                   game_port=config.game_port, runtime_root=str(config.runtime_root) if config.runtime_root else None,
                   gui_mods=str(config.gui_mods) if config.gui_mods else None,
                   deadline_epoch=time.time() + 12 * 3600, model=MODEL, variant=VARIANT)
    atomic_json(settings, payload)
    atomic_json(_root(config) / 'current.json', {'settings': str(settings)})
    _launch(config, settings)


def _resume(config) -> None:
    if _busy(config):
        raise ValueError('Campaign is already running')
    current = _read(_root(config) / 'current.json')
    if not current:
        raise ValueError('No campaign window to resume')
    settings = Path(current['settings'])
    if _read(settings).get('deadline_epoch', 0) <= time.time():
        raise ValueError('Campaign deadline expired; start a new 12-hour window')
    controller = _read(settings.parent / 'controller.json')
    manifest = _read(config.server_data / 'episode/current.json')
    owned_episode = controller.get('active_episode_id')
    intent_episode = (controller.get('lifecycle_intent') or {}).get('expected_episode')
    matches = bool(manifest.get('episode_id') and manifest['episode_id'] in (owned_episode, intent_episode))
    if running_runner_pid(config.server_data / 'logs/autonomous-run.pid') and not matches:
        raise ValueError('Active runner does not match recorded episode ownership; inspect before restarting')
    _launch(config, settings)


def stop(config) -> None:
    result = _service(config, 'stop')
    if result.returncode:
        raise RuntimeError(result.stderr[-1000:])
    current = _read(_root(config) / 'current.json')
    if current:
        path = Path(current['settings']).parent / 'status.json'
        data = _read(path)
        data.update(phase='stopped', reason='Stopped from console. Factorio runner/server are managed separately.')
        atomic_json(path, data)


def status(config) -> dict:
    running = _busy(config)
    current = _read(_root(config) / 'current.json')
    if not current:
        return dict(running=running, phase='idle', reason='No supervised campaign started', model=MODEL, variant=VARIANT)
    directory = Path(current['settings']).parent
    settings = _read(directory / 'settings.json')
    data = _read(directory / 'status.json')
    controller = _read(directory / 'controller.json')
    reliability = _read(config.server_data / 'logs/reliability-state.json')
    progress = _read(directory / 'progress.json')
    if running and progress:
        data.update(phase=progress.get('phase', data.get('phase')), reason=progress.get('reason', ''))
    if not running and data.get('phase') not in ('stopped', 'deadline', 'parked', 'complete', 'blocked'):
        data.update(phase='interrupted', reason='Supervisor exited; Resume retains the original deadline and controller state')
    notes, agents = '', {}
    boards = sorted((directory / 'agents/observers').glob('*/board.md'), key=lambda p: p.stat().st_mtime)
    if boards:
        with boards[-1].open(errors='replace') as stream:
            notes = stream.read(4000)
        for state_path in sorted(boards[-1].parent.glob('*/state.json'))[:4]:
            agents[state_path.parent.name] = _read(state_path)
            findings = state_path.parent / 'findings.md'
            try:
                with findings.open(errors='replace') as stream:
                    notes += '\n\n' + stream.read(2500)
            except OSError:
                pass
    return {**data, 'running': running, 'deadline': settings.get('deadline_epoch'),
            'model': MODEL, 'variant': VARIANT, 'completed_runs': controller.get('completed_cycles', 0),
            'acceptance_streak': len(reliability.get('streak', [])), 'required_successes': 3,
            'milestones': reliability.get('runs', [])[-5:], 'notes': notes, 'agents': agents,
            'board_path': str(boards[-1]) if boards else None}


def _stop_process_group(process) -> None:
    # The controller and its OpenCode children share a dedicated process group.
    # Kill the group even if its leader has exited and left models behind.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def supervise(settings_path: Path) -> int:
    settings = _read(settings_path)
    directory = settings_path.parent
    deadline = settings['deadline_epoch']
    controller_path = directory / 'controller.json'
    if not controller_path.exists():
        atomic_json(controller_path, {'deadline_utc': datetime.fromtimestamp(deadline, timezone.utc).isoformat()})
    command = [settings['python'], '-m', 'tools.opencode_campaign_orchestrator', '--plastic-reliability',
               '--aspect-observers', '--state-root', settings['state_root'], '--source-save', settings['source_save'],
               '--state-file', str(controller_path), '--opencode-log-dir', str(directory / 'agents'),
               '--observations', str(directory / 'journal.md'), '--progress-file', str(directory / 'progress.json'),
               '--model', MODEL, '--variant', VARIANT, '--opencode-bin', settings['opencode'],
               '--python', settings['python'], '--rcon-port', str(settings['rcon_port']),
               '--game-port', str(settings.get('game_port', 34199)), '--technology', settings['technology'],
               '--max-runtime-hours', '12']
    for field in ('runtime_root', 'gui_mods'):
        if settings.get(field):
            command += ['--' + field.replace('_', '-'), settings[field]]
    failures = 0
    while time.time() < deadline:
        atomic_json(directory / 'status.json', dict(phase='running', reason='Controller active', recovery_attempts=failures))
        with (directory / 'controller.log').open('ab') as output:
            process = subprocess.Popen(command, cwd=REPO, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                while process.poll() is None:
                    if time.time() >= deadline:
                        _stop_process_group(process)
                        atomic_json(directory / 'status.json', dict(phase='deadline', reason='12-hour window ended; inspect independently managed runner/server'))
                        return 0
                    time.sleep(1)
                code = process.returncode
            finally:
                _stop_process_group(process)
        progress = _read(directory / 'progress.json')
        if code == 0:
            atomic_json(directory / 'status.json', dict(phase=progress.get('phase', 'parked'), reason=progress.get('reason', 'Controller stopped normally; inspect journal'), recovery_attempts=failures))
            return 0
        state = _read(controller_path)
        # Recover only a recorded episode/session/review. Never blindly repeat
        # a fresh lifecycle if the controller died before persisting ownership.
        recoverable = bool(state.get('active_session_id') or state.get('pending_fix') or state.get('active_episode_id') or state.get('lifecycle_intent'))
        failures += 1
        if not recoverable or failures > 3:
            atomic_json(directory / 'status.json', dict(phase='parked', reason='Controller failed; safe automatic recovery unavailable or exhausted. Inspect controller.log.', recovery_attempts=failures))
            return 1
        atomic_json(directory / 'status.json', dict(phase='recovering', reason=f'Resuming recorded state after controller exit {code}', recovery_attempts=failures))
        time.sleep(min(5 * failures, max(0, deadline - time.time())))
    atomic_json(directory / 'status.json', dict(phase='deadline', reason='Campaign deadline expired'))
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(supervise(args.settings))
