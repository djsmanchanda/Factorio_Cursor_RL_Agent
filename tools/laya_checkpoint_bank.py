# Path: tools/laya_checkpoint_bank.py | Purpose: Keep immutable checkpoint candidates and manually selected tracks.
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import uuid
from contextlib import contextmanager
import fcntl

from tools.checkpoint_catalog import atomic_json
from tools import milestone_checkpoint as checkpoints
from training import laya_restart_checkpoint as restart


def _id(value):
    return checkpoints._safe_id(value, 'bank identity')


def register(bank, bundle):
    bank, bundle = Path(bank), Path(bundle)
    metadata, _ = restart.load(bundle)
    generation = _id(metadata['generation_id'])
    target = bank / 'generations' / generation
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError('Generation already registered')
    staging = target.with_name('.' + generation + '.' + uuid.uuid4().hex)
    try:
        shutil.copytree(bundle, staging, symlinks=True)
        restart.load(staging)
        staging.rename(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return generation


def _candidate(bank, generation):
    path = Path(bank) / 'generations' / _id(generation)
    metadata, context = restart.load(path)
    if metadata['generation_id'] != generation:
        raise ValueError('Generation identity mismatch')
    return path, metadata, context


def checked_receipt(bank, generation, receipt, *, live=False):
    path, meta, context = _candidate(bank, generation)
    if not isinstance(receipt, dict) or not isinstance(receipt.get('mod_hashes'), dict) or not receipt['mod_hashes']:
        raise ValueError('Reload receipt must identify its target mods')
    migration = restart.migration_identity(meta, context, {'mod_hashes': receipt['mod_hashes']},
                                           receipt.get('migration_contract'))
    expected = {'kind': 'laya-disposable-reload-v1', 'status': 'passed',
                'generation': generation, 'checkpoint': meta['checkpoint_id'],
                'manifest_sha256': checkpoints.sha256(path / 'checkpoint.json'),
                'world_sha256': meta['world']['sha256'],
                'context_sha256': meta['files']['state/logs/laya-restart-context.json'],
                'loaded_tick': meta['capture_tick'], 'origin_episode': meta['lineage_id'],
                'engine': context['engine'], 'migration_contract_sha256': migration,
                'ownership_reconstructed': True}
    predicate = receipt.get('predicate', {}) if isinstance(receipt, dict) else {}
    if (not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in expected.items())
            or receipt['engine'].get('source') != 'proc'
            or predicate.get('status') != 'passed' or predicate.get('checkpoint_id') != meta['checkpoint_id']
            or predicate.get('predicate_version') != meta['predicate']['version']
            or not receipt.get('server_start_ticks') or type(receipt.get('server_pid')) is not int
            or not receipt.get('reload_root') or Path(receipt['reload_root']).resolve() == Path(context['source_root']).resolve()):
        raise ValueError('Invalid or unbound live reload receipt')
    if live:
        # A supplied JSON file alone cannot register runtime validation. Confirm
        # the independent server is still alive and bound to this copied world.
        root = Path(receipt['reload_root'])
        from types import SimpleNamespace
        pid = receipt['server_pid']
        proc = Path(f'/proc/{pid}')
        manifest = json.loads((root / 'manifest.json').read_text())
        arguments = (proc / 'cmdline').read_bytes().split(b'\0')
        if (str(root / 'server/config.ini').encode() not in arguments
                or (proc / 'stat').read_text().split(') ', 1)[1].split()[19] != receipt['server_start_ticks']
                or manifest.get('production_authority') is not False
                or manifest.get('source_sha256') != meta['world']['sha256']
                or manifest.get('mod_hashes') != receipt['mod_hashes']
                or restart._engine(SimpleNamespace(root=root)) != context['engine']):
            raise ValueError('Independent live reload identity cannot be corroborated')
    return receipt


def record_validation(bank, generation, receipt):
    checked_receipt(bank, generation, receipt, live=True)
    dest = Path(bank) / 'validations' / _id(generation) / (uuid.uuid4().hex + '.json')
    atomic_json(dest, receipt)
    return dest


def validated_receipts(bank, generation, *, mod_hashes=None):
    _, meta, _ = _candidate(bank, generation)
    target_mods = meta['provenance']['mod_hashes'] if mod_hashes is None else mod_hashes
    result = []
    for file in sorted((Path(bank) / 'validations' / _id(generation)).glob('*.json')):
        receipt = checked_receipt(bank, generation, json.loads(file.read_text()))
        if receipt['mod_hashes'] == target_mods:
            result.append(receipt)
    return result


def discover(bank):
    root = Path(bank)
    found = {}
    for path in sorted((root / 'generations').glob('*')):
        if path.is_dir() and not path.name.startswith('.'):
            _, meta, _ = _candidate(root, path.name)
            found.setdefault(meta['checkpoint_id'], []).append({'generation': path.name,
                'validated': bool(validated_receipts(root, path.name))})
    return {f'C{i}': found.get(f'C{i}', []) for i in range(7)} | found


def inspect(bank):
    path = Path(bank) / 'selections.json'
    return {'candidates': discover(bank), 'selections': json.loads(path.read_text()) if path.exists() else {}}


def _select(bank, checkpoint, generation, track='anchor'):
    if track not in {'anchor', 'representative'}:
        raise ValueError('Unknown track')
    root = Path(bank)
    _, meta, _ = _candidate(root, generation)
    if meta['checkpoint_id'] != checkpoint:
        raise ValueError('Checkpoint mismatch')
    if not validated_receipts(root, generation):
        raise ValueError('Generation has no live reload validation')
    _, _, context = _candidate(root, generation)
    if track == 'anchor' and context.get('provisional') is not False:
        raise ValueError('Only earned C0 lineage can be an anchor')
    registry = Path(__file__).resolve().parents[1] / 'data/laya-experiments'
    for status in registry.glob('*/job-status.json'):
        data = json.loads(status.read_text())
        if any(j.get('admission_reserved') or j.get('status') in {'preparing', 'running'} for j in data.get('jobs', [])):
            raise ValueError('Select checkpoint generations only between cohorts')
    state_path = root / 'selections.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    key = checkpoint + ':' + track
    history = state.setdefault(key, [])
    history.append(generation)
    atomic_json(state_path, state)
    return generation


@contextmanager
def bank_lock(bank):
    root = Path(bank)
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.selection.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def select(bank, checkpoint, generation, track='anchor'):
    with bank_lock(bank):
        return _select(bank, checkpoint, generation, track)


def rollback(bank, checkpoint, track='anchor'):
    with bank_lock(bank):
        state = json.loads((Path(bank) / 'selections.json').read_text())
        history = state.get(checkpoint + ':' + track, [])
        if len(history) < 2:
            raise ValueError('No previous selection')
        return _select(bank, checkpoint, history[-2], track)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['register', 'record-validation', 'inspect', 'discover', 'select', 'rollback'])
    parser.add_argument('bank')
    parser.add_argument('--bundle')
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--checkpoint')
    parser.add_argument('--generation')
    parser.add_argument('--track', default='anchor')
    args = parser.parse_args()
    actions = {'record-validation': lambda: str(record_validation(args.bank, args.generation, json.loads(args.receipt.read_text()))), 'register': lambda: register(args.bank, args.bundle), 'inspect': lambda: inspect(args.bank),
               'discover': lambda: discover(args.bank),
               'select': lambda: select(args.bank, args.checkpoint, args.generation, args.track),
               'rollback': lambda: rollback(args.bank, args.checkpoint, args.track)}
    print(json.dumps(actions[args.action](), indent=2))


if __name__ == '__main__':
    main()
