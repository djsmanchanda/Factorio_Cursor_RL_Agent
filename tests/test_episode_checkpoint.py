# Path: tests/test_episode_checkpoint.py
# Purpose: Verify failed-world/state bundles are paired and never overwrite a replay destination.
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from tools import episode_checkpoint as checkpoint


def test_checkpoint_saves_world_and_pairs_controller_state(tmp_path, monkeypatch):
    root = tmp_path / 'isolated'
    (root / 'episode').mkdir(parents=True)
    (root / 'logs').mkdir()
    (root / 'saves').mkdir()
    (root / 'episode/current.json').write_text(json.dumps({
        'episode_id': 'e1', 'surface': 'nauvis', 'force': 'player',
        'bootstrap_profile': 'reduced-v1', 'source_save_sha256': 'save',
        'baseline_world_fingerprint': 'sha256:world',
        'deployed_factorio_mod_sha256': 'mod',
        'deployed_factorio_training_lab_sha256': 'training',
    }))
    (root / 'logs/materials.json').write_text('{"reserved":3}')
    for directory in ('deterministic-material-reservations', 'deterministic-bootstrap-districts',
                      'deterministic-bootstrap-work'):
        (root / 'logs' / directory).mkdir()
        (root / 'logs' / directory / 'e1.json').write_text('{"episode_id":"e1"}')
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
    assert 'state/logs/deterministic-material-reservations/e1.json' in manifest['files']
    assert 'state/logs/deterministic-bootstrap-districts/e1.json' in manifest['files']
    assert 'state/logs/deterministic-bootstrap-work/e1.json' in manifest['files']
    replay = checkpoint.extract(bundle, tmp_path / 'replay')
    replay_manifest = checkpoint.verify_replay(replay, expected_mod_contract={
        'deployed_factorio_mod_sha256': 'mod',
        'deployed_factorio_training_lab_sha256': 'training',
    })
    assert replay_manifest['acceptance_eligible'] is False
    candidate = tmp_path / 'candidate'
    shutil.copytree(replay, candidate)
    pair = checkpoint.prepare_pair(replay, candidate, tmp_path / 'pair')
    assert json.loads((pair / 'REPLAY_PAIR.json').read_text())['acceptance_eligible'] is False
    # The initial world and scheduler state stay paired while candidate code
    # and its deployed mod contract intentionally differ.
    current_path = candidate / 'state/episode/current.json'
    current = json.loads(current_path.read_text())
    current['deployed_factorio_mod_sha256'] = 'candidate-mod'
    current['repository_revision'] = 'candidate-revision'
    current_path.write_text(json.dumps(current))
    candidate_contract = json.loads((candidate / 'REPLAY.json').read_text())
    candidate_contract['mod_contract']['deployed_factorio_mod_sha256'] = 'candidate-mod'
    for sidecar in candidate_contract['sidecars']:
        if sidecar['path'] == 'state/episode/current.json':
            sidecar['sha256'] = checkpoint.sha256(current_path)
    (candidate / 'REPLAY.json').write_text(json.dumps(candidate_contract))
    changed_pair = checkpoint.prepare_pair(replay, candidate, tmp_path / 'changed-pair')
    assert json.loads((changed_pair / 'REPLAY_PAIR.json').read_text())['contracts']['candidate']['deployed_factorio_mod_sha256'] == 'candidate-mod'
    with pytest.raises(ValueError, match='expected contract'):
        checkpoint.verify_replay(replay, expected_mod_contract={
            'deployed_factorio_mod_sha256': 'other',
            'deployed_factorio_training_lab_sha256': 'training',
        })
    broken = tmp_path / 'broken'
    shutil.copytree(replay, broken)
    shutil.rmtree(broken / 'state/logs')
    broken_manifest = json.loads((broken / 'REPLAY.json').read_text())
    broken_manifest['sidecars'] = [record for record in broken_manifest['sidecars']
                                   if not record['path'].startswith('state/logs/')]
    (broken / 'REPLAY.json').write_text(json.dumps(broken_manifest))
    with pytest.raises(ValueError, match='controller log sidecars'):
        checkpoint.verify_replay(broken)
    with pytest.raises(ValueError, match='new directory'):
        checkpoint.extract(bundle, replay)
    (bundle / 'world.zip').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='mismatch'):
        checkpoint.extract(bundle, tmp_path / 'other')


def test_running_controller_refuses_checkpoint_before_saving(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, 'running_runner_pid', lambda _: 123)
    with pytest.raises(ValueError, match='stopped'):
        checkpoint.capture(tmp_path, client=object())
