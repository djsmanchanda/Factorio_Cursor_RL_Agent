#!/usr/bin/env python3
# Path: tools/laya_pm_replay.py | Purpose: Validate C4 replay provenance and distinguish actual PM consumption from generic C6 passes.
"""Read-only gates for the C4-to-PM diagnostic fixture; never launches a world."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.laya_checkpoint_bank import validated_receipts
from training.laya_restart_checkpoint import load


def preflight(bank: Path, generation: str) -> dict:
    if not generation or Path(generation).name != generation or generation.startswith('.'):
        raise ValueError('Invalid checkpoint generation')
    bundle = bank/'generations'/generation
    metadata, _ = load(bundle)
    if metadata['checkpoint_id'] != 'C4' or metadata['predicate']['version'] != 'c4-v1':
        raise ValueError('PM replay needs a verified C4 bundle with a compatible origin predicate')
    receipts = validated_receipts(bank, generation)
    if not receipts:
        raise ValueError('PM replay needs a hash-bound disposable reload validation')
    return {'kind': 'laya-pm-diagnostic-preflight-v1', 'bundle': str(bundle.resolve()),
            'generation': generation, 'capture_tick': metadata['capture_tick'],
            'world_sha256': metadata['world']['sha256'], 'reload_receipts': len(receipts),
            'acceptance_eligible': metadata.get('acceptance_eligible') is True,
            'evidence_scope': 'diagnostic_replay_only',
            'next_gate': 'fresh target-code reload, PM placement receipt, then c6-v3 PM consumption'}


def consumption(world: Path) -> dict:
    manifest = json.loads((world/'manifest.json').read_text())
    if manifest.get('production_authority') is not False:
        raise ValueError('PM replay evidence must come from an explicitly disposable world')
    matches = []
    with (world/'evidence/clean-decisions.jsonl').open() as stream:
        for line in stream:
            row = json.loads(line)
            if row.get('event') != 'strict_current':
                continue
            predicate = (row.get('predicate_evidence') or {}).get('C6') or {}
            count = ((predicate.get('evidence') or {}).get('machine_consumption') or {}).get('productivity-module')
            if (predicate.get('predicate_version') == 'c6-v3'
                    and (row.get('statuses') or {}).get('C6') is True
                    and type(count) is int and count > 0):
                matches.append({'tick': row.get('tick'), 'run_id': row.get('run_id'), 'crafts': count})
    return {'kind': 'laya-pm-consumption-v1', 'world': str(world.resolve()),
            'pm_consumption_verified': bool(matches), 'samples': matches,
            'evidence_scope': 'diagnostic_replay_only',
            'placement_receipt_required': True, 'automatic_adoption': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    prepare = commands.add_parser('preflight')
    prepare.add_argument('--bank', type=Path, required=True)
    prepare.add_argument('--generation', required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('--world', type=Path, required=True)
    args = parser.parse_args(argv)
    result = preflight(args.bank, args.generation) if args.action == 'preflight' else consumption(args.world)
    print(json.dumps(result, indent=2))
    return 0 if args.action == 'preflight' or result['pm_consumption_verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
