# Path: tools/laya_status_journal.py | Purpose: Durable scheduler status transitions under the scheduler status lock.
from __future__ import annotations

import hashlib
import json
import os
import copy
from collections import OrderedDict
from pathlib import Path

_CACHE = OrderedDict()


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate journal field')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('non-finite journal number')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=constant)


def _signature(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _remember(root, status, seq, plan):
    path = (Path(root) / 'job-status.journal.jsonl').resolve()
    _CACHE[path] = (_signature(path), _plan_hash(plan), copy.deepcopy(status), seq)
    _CACHE.move_to_end(path)
    while len(_CACHE) > 16:
        _CACHE.popitem(last=False)


class JournalUnavailable(ValueError):
    """The scheduler cannot safely infer state from its durable evidence."""


def _rows(rows):
    if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get('id'), str)
                                         or not r['id'] for r in rows):
        raise JournalUnavailable('invalid job rows')
    ids = [r['id'] for r in rows]
    if len(ids) != len(set(ids)):
        raise JournalUnavailable('duplicate job identity')
    return ids


def _plan(root):
    path = root / 'planned-jobs.json'
    if not path.exists():
        return None
    try:
        plan = _json(path.read_text())
        if not isinstance(plan, dict) or type(plan.get('version', 1)) is not int or plan.get('version', 1) != 1:
            raise JournalUnavailable('invalid planned jobs schema')
        _rows(plan.get('jobs'))
        return plan['jobs']
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise JournalUnavailable(f'planned jobs unavailable: {exc}') from exc


def _plan_hash(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest() if plan is not None else None


def _validate(status, plan):
    if not isinstance(status, dict):
        raise JournalUnavailable('invalid status')
    ids = _rows(status.get('jobs'))
    if plan is not None and ids != _rows(plan):
        raise JournalUnavailable('job identities or order differ from immutable plan')


def _append(path, record):
    data = (json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
    new = not path.exists()
    with path.open('ab') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if new:
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def load(root):
    """Load or import the legacy projection. Caller holds status.lock."""
    root = Path(root)
    path = root / 'job-status.journal.jsonl'
    plan = _plan(root)
    if not path.exists():
        try:
            status = _json((root / 'job-status.json').read_text())
        except FileNotFoundError:
            if plan is None:
                raise JournalUnavailable('missing status and planned jobs')
            status = {'name': root.name, 'jobs': []}
        except (OSError, ValueError) as exc:
            raise JournalUnavailable(f'legacy status unavailable: {exc}') from exc
        if not isinstance(status, dict):
            raise JournalUnavailable('invalid legacy status')
        rows = status.get('jobs')
        ids = _rows(rows)
        if plan is not None:
            by_id = dict(zip(ids, rows))
            if set(ids) - set(_rows(plan)):
                raise JournalUnavailable('unplanned legacy job')
            status['jobs'] = [by_id.get(p['id'], {**p, 'status': 'unknown', 'admission_reserved': None,
                                                    'legacy_status_missing': True}) for p in plan]
        _validate(status, plan)
        _append(path, {'version': 1, 'seq': 1, 'kind': 'legacy_baseline',
                       'provenance': 'unknown', 'plan_sha256': _plan_hash(plan), 'status': status})
        _remember(root, status, 1, plan)
        return status, 1, plan
    try:
        # The scheduler is the sole writer under status.lock. Reuse its own
        # validated prefix; any external edit or replacement forces a replay.
        cached = _CACHE.get(path.resolve())
        if cached is not None and cached[:2] == (_signature(path), _plan_hash(plan)):
            return copy.deepcopy(cached[2]), cached[3], plan
        raw = path.read_bytes()
        if not raw or not raw.endswith(b'\n'):
            raise JournalUnavailable('empty or truncated journal')
        status = None
        for seq, line in enumerate(raw.splitlines(), 1):
            event = _json(line)
            if not isinstance(event, dict) or type(event.get('version')) is not int or event['version'] != 1 or type(event.get('seq')) is not int or event['seq'] != seq:
                raise JournalUnavailable('invalid journal version or sequence')
            if seq == 1:
                if event.get('kind') != 'legacy_baseline' or event.get('provenance') != 'unknown' or event.get('plan_sha256') != _plan_hash(plan):
                    raise JournalUnavailable('invalid journal baseline')
                status = event.get('status')
                _validate(status, plan)
            else:
                if event.get('kind') != 'delta' or not isinstance(event.get('rows'), dict) or not isinstance(event.get('metadata'), dict):
                    raise JournalUnavailable('invalid journal delta')
                rows = {r['id']: r for r in status['jobs']}
                for identity, row in event['rows'].items():
                    if identity not in rows or not isinstance(row, dict) or row.get('id') != identity:
                        raise JournalUnavailable('invalid journal row identity')
                    rows[identity] = row
                status['jobs'] = [rows[r['id']] for r in status['jobs']]
                metadata = event['metadata']
                if set(metadata) != {'set', 'delete'} or not isinstance(metadata['set'], dict) or not isinstance(metadata['delete'], list) or any(not isinstance(k, str) for k in metadata['delete']) or 'jobs' in metadata['set'] or 'jobs' in metadata['delete']:
                    raise JournalUnavailable('invalid journal metadata')
                for key in metadata['delete']:
                    status.pop(key, None)
                status.update(metadata['set'])
                _validate(status, plan)
        _remember(root, status, seq, plan)
        return status, seq, plan
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise JournalUnavailable(f'status journal unavailable: {exc}') from exc


def append(root, previous, status, seq, plan):
    _validate(status, plan)
    if _rows(previous['jobs']) != _rows(status['jobs']):
        raise JournalUnavailable('job identities or order changed')
    old = {r['id']: r for r in previous['jobs']}
    rows = {r['id']: r for r in status['jobs'] if r != old[r['id']]}
    before = {k: v for k, v in previous.items() if k != 'jobs'}
    after = {k: v for k, v in status.items() if k != 'jobs'}
    metadata = {'set': {k: v for k, v in after.items() if k not in before or v != before[k]},
                'delete': sorted(before.keys() - after.keys())}
    _append(Path(root) / 'job-status.journal.jsonl',
            {'version': 1, 'seq': seq + 1, 'kind': 'delta', 'rows': rows, 'metadata': metadata})
    _remember(root, status, seq + 1, plan)
