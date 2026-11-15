# Path: tools/laya_experiment_assignment.py | Purpose: Reproducibly order complete matched experiment blocks.
import hashlib
import json
import re


def validate(declaration):
    if (not isinstance(declaration, dict) or set(declaration) != {'method', 'seed'}
            or declaration['method'] != 'sha256-rotations-v1'
            or type(declaration['seed']) is not int or declaration['seed'] < 0):
        raise ValueError('assignment requires sha256-rotations-v1 and a nonnegative integer seed')


def assign(jobs, arms, declaration, *, mixed):
    validate(declaration)
    if not isinstance(jobs, list):
        raise ValueError('assignment requires a finite JSON job plan')
    try:
        json.dumps(jobs, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError('assignment requires a finite JSON job plan') from error
    arms = sorted(arms)
    if len(arms) < 2 or len(set(arms)) != len(arms):
        raise ValueError('assignment requires distinct measurement arms')
    blocks, ids = {}, set()
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get('id'), str) or not job['id'] or job['id'] in ids:
            raise ValueError('assignment requires unique job identities')
        ids.add(job['id'])
        if (job.get('arm') not in arms or not isinstance(job.get('origin'), str) or not job['origin']
                or type(job.get('seed')) is not int or job['seed'] < 0
                or (mixed and (type(job.get('pair_id')) is not int or job['pair_id'] < 0))):
            raise ValueError('malformed assignment block')
        if not mixed and job.get('bundle') is not None and (
                not isinstance(job.get('bundle_sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', job['bundle_sha256'])):
            raise ValueError('assignment requires immutable checkpoint identity')
        # Freeze relocates checkpoint directories. Their content identity stays
        # stable when reservation recomputes the declared order afterwards.
        key = [job['pair_id']] if mixed else [job['origin'], job.get('bundle_sha256'), job['seed']]
        key = json.dumps(key, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        blocks.setdefault(key, []).append(job)
    if not blocks:
        raise ValueError('assignment requires matched blocks')
    for rows in blocks.values():
        if len(rows) != len(arms) or {row['arm'] for row in rows} != set(arms):
            raise ValueError('assignment requires complete matched blocks')
        shared = [{k: v for k, v in row.items() if k not in ('id', 'arm', 'status', 'admission_reserved')} for row in rows]
        if any(row != shared[0] for row in shared[1:]):
            raise ValueError('assignment block identities differ')
    def rank(domain, value):
        payload = json.dumps([declaration['seed'], domain, value], ensure_ascii=False, separators=(',', ':')).encode()
        return hashlib.sha256(payload).digest(), value
    keys = sorted(blocks, key=lambda key: rank('block', key))
    order = sorted(arms, key=lambda arm: rank('arm', arm))
    result = []
    for index, key in enumerate(keys):
        rows = {row['arm']: row for row in blocks[key]}
        shift = index % len(arms)
        result.extend(rows[arm] for arm in order[shift:] + order[:shift])
    return result
