# Path: tests/test_episode_checkpoint.py
# Purpose: Verify failed-world/state bundles are paired and never overwrite a replay destination.
import json
from pathlib import Path
import zipfile

import pytest

from tools import episode_checkpoint as checkpoint


def test_checkpoint_saves_world_and_pairs_controller_state(tmp_path, monkeypatch):
    root = tmp_path / 'isolated'
    (root / 'episode').mkdir(parents=True)
    (root / 'logs').mkdir()
    (root / 'saves').mkdir()
    (root / 'episode/current.json').write_text(json.dumps({'episode_id': 'e1', 'surface': 'nauvis', 'force': 'player'}))
    (root / 'logs/materials.json').write_text('{"reserved":3}')
    monkeypatch.setattr(checkpoint, 'running_runner_pid', lambda _: None)
    class Client:
        def command(self, command):
            if 'helpers.write_file' in command:
                name = command.split('write_file("')[1].split('"')[0]
                nonce = command.split('nonce="')[1].split('"')[0]
                (root / 'script-output').mkdir(exist_ok=True)
                (root / 'script-output' / name).write_text(json.dumps({'nonce': nonce}))
                return ''
            name = command.split('server_save("')[1].split('"')[0]
            with zipfile.ZipFile(root / 'saves' / (name + '.zip'), 'w') as archive:
                archive.writestr('level.dat', 'failed-world-state')
            return '12345'
    bundle = checkpoint.capture(root, client=Client())
    manifest = json.loads((bundle / 'checkpoint.json').read_text())
    assert manifest['tick'] == 12345
    assert 'world.zip' in manifest['files']
    assert 'state/logs/materials.json' in manifest['files']
    replay = checkpoint.extract(bundle, tmp_path / 'replay')
    assert json.loads((replay / 'REPLAY.json').read_text())['acceptance_eligible'] is False
    with pytest.raises(ValueError, match='new directory'):
        checkpoint.extract(bundle, replay)
    (bundle / 'world.zip').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='mismatch'):
        checkpoint.extract(bundle, tmp_path / 'other')


def test_running_controller_refuses_checkpoint_before_saving(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, 'running_runner_pid', lambda _: 123)
    with pytest.raises(ValueError, match='stopped'):
        checkpoint.capture(tmp_path, client=object())
