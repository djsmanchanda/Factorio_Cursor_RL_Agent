# Path: tools/episode_checkpoint.py
# Purpose: Save a stopped-runner world and durable state together for isolated diagnosis.
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import time
import zipfile
import uuid

from tools.runner_process import running_runner_pid
from tools.reliability_state import atomic_json


def sha256(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def capture(root: Path, *, client, timeout: int = 120) -> Path:
    """Ask the isolated server for a named save; never label the baseline as a failed world."""
    root = root.resolve()
    if running_runner_pid(root / 'logs/autonomous-run.pid') is not None:
        raise ValueError('checkpoint requires the episode runner to be stopped')
    manifest = json.loads((root / 'episode/current.json').read_text())
    if (manifest.get('surface'), manifest.get('force')) != ('nauvis', 'player') or not manifest.get('episode_id'):
        raise ValueError('checkpoint requires explicit deterministic episode identity')
    token = hashlib.sha256(manifest['episode_id'].encode()).hexdigest()[:20]
    destination = root / 'checkpoints' / token
    if (destination / 'checkpoint.json').exists():
        return destination
    nonce = uuid.uuid4().hex
    probe_name = 'checkpoint-scope-' + nonce + '.json'
    probe_path = root / 'script-output' / probe_name
    client.command('/sc helpers.write_file("' + probe_name + '",helpers.table_to_json({nonce="' + nonce + '"}),false)')
    try:
        if json.loads(probe_path.read_text()).get('nonce') != nonce:
            raise ValueError('checkpoint command channel does not match the runtime output tree')
    except (OSError, ValueError) as error:
        raise ValueError('checkpoint runtime/output pairing failed') from error
    finally:
        probe_path.unlink(missing_ok=True)
    save_name = 'reliability-' + token
    saved = root / 'saves' / (save_name + '.zip')
    if saved.exists():
        raise ValueError('unpaired checkpoint save exists; inspect before retrying')
    # The runner has stopped changing controller state. Factorio saves its own
    # mod state atomically at this tick; passive factory activity may continue.
    response = client.command('/sc game.server_save("' + save_name + '");rcon.print(game.tick)').strip()
    tick = int(response)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with zipfile.ZipFile(saved) as archive:
                if archive.testzip() is None:
                    break
        except (OSError, zipfile.BadZipFile):
            pass
        time.sleep(1)
    else:
        raise TimeoutError('checkpoint save did not finish')
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(saved, destination / 'world.zip')
    state = destination / 'state'
    for directory in ('logs', 'script-output', 'episode'):
        source = root / directory
        paths = source.rglob('*.json') if directory == 'script-output' else source.glob('*.json')
        for path in paths:
            if path.is_symlink():
                continue
            target = state / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    oil = root / 'logs/oil-transactions'
    if oil.is_dir():
        shutil.copytree(oil, state / 'logs/oil-transactions', dirs_exist_ok=True)
    for name in ('autonomous-run.log', 'deterministic-blockers.jsonl', 'deterministic-events.jsonl'):
        path = root / 'logs' / name
        if path.is_file():
            shutil.copy2(path, state / 'logs' / name)
    hashes = {str(path.relative_to(destination)): sha256(path) for path in destination.rglob('*') if path.is_file()}
    atomic_json(destination / 'checkpoint.json', dict(version=1, episode_id=manifest['episode_id'],
        tick=tick, source_root=str(root), manifest=manifest, files=hashes,
        purpose='diagnostic replay input; never a clean-start acceptance episode'))
    return destination


def extract(bundle: Path, destination: Path) -> Path:
    """Verify a checkpoint and extract to a NEW directory; do not start any runtime."""
    bundle = bundle.resolve()
    payload = json.loads((bundle / 'checkpoint.json').read_text())
    if payload.get('version') != 1 or not payload.get('files'):
        raise ValueError('invalid checkpoint manifest')
    for relative, digest in payload['files'].items():
        path = (bundle / relative).resolve()
        if not path.is_relative_to(bundle) or sha256(path) != digest:
            raise ValueError('checkpoint file hash/path mismatch')
    if destination.exists():
        raise ValueError('replay destination must be a new directory')
    destination.mkdir(parents=True)
    for relative in payload['files']:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundle / relative, target)
    shutil.copy2(bundle / 'checkpoint.json', destination / 'checkpoint.json')
    atomic_json(destination / 'REPLAY.json', dict(source_episode=payload['episode_id'], acceptance_eligible=False))
    return destination


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Extract a verified diagnostic checkpoint without starting Factorio.')
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(extract(args.checkpoint, args.destination))
