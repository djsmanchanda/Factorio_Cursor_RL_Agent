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


REPLAY_SCHEMA_VERSION = 1


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
    # These nested stores contain the actual durable scheduler/construction
    # state. Top-level log snapshots alone cannot reconstruct a failed episode.
    for name in ('deterministic-material-reservations', 'deterministic-bootstrap-districts',
                 'deterministic-bootstrap-work'):
        source = root / 'logs' / name
        if source.is_dir():
            for path in source.rglob('*.json'):
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError('checkpoint contains an unsafe durable sidecar')
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
    manifest = payload.get('manifest') or {}
    world = destination / 'world.zip'
    sidecars = sorted(
        str(path.relative_to(destination))
        for path in destination.glob('state/**/*')
        if path.is_file()
    )
    # REPLAY.json is a diagnostic fixture contract.  It intentionally carries
    # no command or restore instruction: a separate disposable runtime must
    # decide whether it can consume the fixture.
    atomic_json(destination / 'REPLAY.json', dict(
        schema_version=REPLAY_SCHEMA_VERSION,
        kind='diagnostic-replay',
        source_episode=payload['episode_id'],
        acceptance_eligible=False,
        provenance={key: manifest.get(key) for key in (
            'episode_id', 'surface', 'force', 'bootstrap_profile',
            'source_save_sha256', 'baseline_world_fingerprint',
        )},
        mod_contract={
            'deployed_factorio_mod_sha256': manifest.get('deployed_factorio_mod_sha256'),
            'deployed_factorio_training_lab_sha256': manifest.get('deployed_factorio_training_lab_sha256'),
        },
        world={'path': 'world.zip', 'sha256': sha256(world)},
        sidecars=[{'path': path, 'sha256': sha256(destination / path)} for path in sidecars],
    ))
    return destination


def _safe_fixture_path(root: Path, relative: str) -> Path:
    raw = root / relative
    if raw.is_symlink():
        raise ValueError('replay fixture cannot contain symlinked files')
    path = raw.resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('replay fixture contains an unsafe path')
    return path


def _contract(payload: dict) -> dict:
    contract = payload.get('mod_contract')
    if not isinstance(contract, dict):
        raise ValueError('replay fixture is missing its mod contract')
    required = ('deployed_factorio_mod_sha256', 'deployed_factorio_training_lab_sha256')
    if any(not isinstance(contract.get(key), str) or not contract[key] for key in required):
        raise ValueError('replay fixture has an incomplete mod contract')
    return {key: contract[key] for key in required}


def verify_replay(replay: Path, *, expected_mod_contract: dict | None = None) -> dict:
    """Verify an extracted diagnostic fixture without starting any runtime.

    A fixture is useful only when the world, durable controller sidecars and
    the mod contract travel together.  This function is deliberately stricter
    than ``extract`` so old bundles can still be inspected while new paired
    replays cannot silently run against an untracked mod tree.
    """
    replay = replay.resolve()
    try:
        payload = json.loads((replay / 'REPLAY.json').read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError('replay fixture manifest is unreadable') from error
    if payload.get('schema_version') != REPLAY_SCHEMA_VERSION or payload.get('kind') != 'diagnostic-replay':
        raise ValueError('invalid diagnostic replay fixture manifest')
    if payload.get('acceptance_eligible') is not False:
        raise ValueError('diagnostic replay cannot be acceptance eligible')
    provenance = payload.get('provenance')
    if not isinstance(provenance, dict) or not provenance.get('episode_id'):
        raise ValueError('replay fixture is missing episode provenance')
    if provenance.get('surface') != 'nauvis' or provenance.get('force') != 'player':
        raise ValueError('replay fixture scope is not deterministic Nauvis/player')
    for key in ('source_save_sha256', 'baseline_world_fingerprint', 'bootstrap_profile'):
        if not isinstance(provenance.get(key), str) or not provenance[key]:
            raise ValueError(f'replay fixture is missing provenance field: {key}')
    contract = _contract(payload)
    if expected_mod_contract is not None:
        expected = {key: expected_mod_contract.get(key) for key in contract}
        if expected != contract:
            raise ValueError('replay fixture mod contract does not match the expected contract')

    world_record = payload.get('world')
    if (not isinstance(world_record, dict) or world_record.get('path') != 'world.zip'
            or not isinstance(world_record.get('sha256'), str)):
        raise ValueError('replay fixture is missing its world')
    world = _safe_fixture_path(replay, world_record['path'])
    if not world.is_file() or sha256(world) != world_record.get('sha256'):
        raise ValueError('replay fixture world hash mismatch')

    sidecars = payload.get('sidecars')
    if not isinstance(sidecars, list) or not sidecars:
        raise ValueError('replay fixture is missing durable sidecars')
    records = {}
    for record in sidecars:
        if (not isinstance(record, dict) or not isinstance(record.get('path'), str)
                or not isinstance(record.get('sha256'), str)):
            raise ValueError('invalid replay sidecar record')
        path = _safe_fixture_path(replay, record['path'])
        if not path.is_file() or sha256(path) != record.get('sha256'):
            raise ValueError(f'replay sidecar hash mismatch: {record.get("path")}')
        records[record['path']] = path
    if 'state/episode/current.json' not in records or not any(path.startswith('state/logs/') for path in records):
        raise ValueError('replay fixture requires episode and controller log sidecars')
    current = json.loads(records['state/episode/current.json'].read_text())
    if current.get('episode_id') != provenance['episode_id']:
        raise ValueError('episode sidecar does not match replay provenance')
    for key in ('surface', 'force', 'bootstrap_profile', 'source_save_sha256', 'baseline_world_fingerprint'):
        if current.get(key) != provenance.get(key):
            raise ValueError(f'episode sidecar does not match replay provenance: {key}')
    for key in ('deployed_factorio_mod_sha256', 'deployed_factorio_training_lab_sha256'):
        if current.get(key) != contract[key]:
            raise ValueError(f'episode sidecar does not match mod contract: {key}')
    return dict(payload, verified=True, mod_contract=contract)


def prepare_pair(baseline: Path, candidate: Path, destination: Path, *,
                 baseline_mod_contract: dict | None = None,
                 candidate_mod_contract: dict | None = None) -> Path:
    """Create an offline baseline/candidate diagnostic pair in a new directory."""
    baseline_payload = verify_replay(baseline, expected_mod_contract=baseline_mod_contract)
    candidate_payload = verify_replay(candidate, expected_mod_contract=candidate_mod_contract)
    if destination.exists():
        raise ValueError('replay pair destination must be a new directory')
    left = baseline.resolve()
    right = candidate.resolve()
    destination_resolved = destination.resolve()
    if destination_resolved.is_relative_to(left) or destination_resolved.is_relative_to(right):
        raise ValueError('replay pair destination cannot be inside an input fixture')
    if baseline_payload['provenance'].get('source_save_sha256') != candidate_payload['provenance'].get('source_save_sha256'):
        raise ValueError('baseline and candidate do not share a source save')
    if baseline_payload['world']['sha256'] != candidate_payload['world']['sha256']:
        raise ValueError('baseline and candidate do not share the same diagnostic world')
    if baseline_payload['provenance'] != candidate_payload['provenance']:
        raise ValueError('baseline and candidate do not share episode provenance')
    def durable_records(payload):
        return {entry['path']: entry['sha256'] for entry in payload['sidecars']
                if entry['path'] != 'state/episode/current.json'}
    if durable_records(baseline_payload) != durable_records(candidate_payload):
        raise ValueError('baseline and candidate do not share durable starting state')
    # Candidate code/mod identity may differ while world and scheduler input
    # remain paired. No other episode fields may be silently changed.
    code_fields = {'repository_revision', 'deployed_factorio_mod_sha256',
                   'deployed_factorio_training_lab_sha256', 'dirty_file_count'}
    def episode_input(root):
        episode = json.loads((root / 'state/episode/current.json').read_text())
        return {key: value for key, value in episode.items() if key not in code_fields}
    if episode_input(left) != episode_input(right):
        raise ValueError('baseline and candidate episode inputs differ beyond code identity')
    destination.mkdir(parents=True)
    shutil.copytree(left, destination / 'baseline')
    shutil.copytree(right, destination / 'candidate')
    atomic_json(destination / 'REPLAY_PAIR.json', {
        'schema_version': REPLAY_SCHEMA_VERSION,
        'kind': 'diagnostic-replay-pair',
        'acceptance_eligible': False,
        'baseline': 'baseline',
        'candidate': 'candidate',
        'compatibility': {
            key: baseline_payload['provenance'].get(key)
            for key in ('source_save_sha256', 'baseline_world_fingerprint', 'bootstrap_profile', 'surface', 'force')
        },
        'contracts': {
            'baseline': baseline_payload['mod_contract'],
            'candidate': candidate_payload['mod_contract'],
        },
    })
    return destination


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Prepare or verify diagnostic replay fixtures without starting Factorio.')
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--replay', type=Path)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    if args.checkpoint:
        if args.destination is None:
            parser.error('--checkpoint requires --destination')
        print(extract(args.checkpoint, args.destination))
    elif args.replay:
        print(json.dumps(verify_replay(args.replay), indent=2, sort_keys=True))
    elif args.baseline and args.candidate:
        if args.destination is None:
            parser.error('--baseline/--candidate require --destination')
        print(prepare_pair(args.baseline, args.candidate, args.destination))
    else:
        parser.error('choose --checkpoint, --replay, or --baseline with --candidate')
