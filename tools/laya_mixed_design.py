# Path: tools/laya_mixed_design.py | Purpose: Describe paired mixed-origin experiments without runtime side effects.
from __future__ import annotations

import copy
import math
import re

ORIGINS = tuple(f'C{i}' for i in range(7))


# The restart allocation weights C1-C6 5:5:5:5:8:8, with largest remainders.
WEIGHTS = {'C1': 5, 'C2': 5, 'C3': 5, 'C4': 5, 'C5': 8, 'C6': 8}


def allocate_pairs(total_runs=120, full_origin_fraction=.4, excluded=()):
    """Pairs per origin. An excluded origin is an explicit design change: its share is
    reallocated across the remaining restart origins by their original weights."""
    if type(total_runs) is not int or total_runs <= 0 or total_runs % 2:
        raise ValueError('total_runs must be a positive even integer')
    if isinstance(full_origin_fraction, bool) or not isinstance(full_origin_fraction, (int, float)) or not math.isfinite(full_origin_fraction) or not .3 <= full_origin_fraction <= .5:
        raise ValueError('full_origin_fraction must be between 0.3 and 0.5')
    excluded = set(excluded)
    if not excluded <= set(WEIGHTS) or len(excluded) >= len(WEIGHTS):
        raise ValueError('excluded origins must be a proper subset of C1-C6')
    included = [o for o in WEIGHTS if o not in excluded]
    pairs = total_runs // 2
    full = math.floor(pairs * full_origin_fraction + .5)
    if not .3 <= full / pairs <= .5 or pairs - full < len(included) or full < 1:
        raise ValueError('total cannot represent the requested mixture and cover every origin')
    remaining = pairs - full
    weights = [WEIGHTS[o] for o in included]
    raw = [remaining * w / sum(weights) for w in weights]
    counts = [int(x) for x in raw]
    for i in sorted(range(len(included)), key=lambda i: (-(raw[i] - counts[i]), i))[:remaining - sum(counts)]:
        counts[i] += 1
    if min(counts) < 1:
        raise ValueError('total cannot cover every origin with the specified weights')
    allocation = {'C0': full, **dict.fromkeys(excluded, 0), **dict(zip(included, counts))}
    return {origin: allocation[origin] for origin in ORIGINS}


def build_jobs(origins, *, arms=('reference_a', 'reference_b'), total_runs=120,
               full_origin_fraction=.4, policy_seeds, engine, mods, budget, excluded=None):
    """Return a plan; callers validate generation identities and seed registry freshness.

    Each origin maps to {'generation_id': ..., 'validated': True, 'immutable': True}
    or {'unavailable': True, 'reason': ...}. C0 also needs a pinned source identity.
    """
    excluded = dict(excluded or {})
    if any(not isinstance(reason, str) or not reason for reason in excluded.values()):
        raise ValueError('each excluded origin needs a reason')
    allocation = allocate_pairs(total_runs, full_origin_fraction, excluded)
    if len(arms) != 2 or len(set(arms)) != 2 or any(not isinstance(a, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', a) for a in arms):
        raise ValueError('exactly two distinct safe arm labels required')
    if set(origins) | set(excluded) != set(ORIGINS) or set(origins) & set(excluded):
        raise ValueError('every origin must be explicitly supplied or explicitly excluded')
    if not engine or not mods or not budget:
        raise ValueError('engine, mods and budget identities are required')
    seeds = list(policy_seeds)
    if len(seeds) != total_runs // 2 or any(type(s) is not int or s < 0 for s in seeds) or len(set(seeds)) != len(seeds):
        raise ValueError('one distinct nonnegative policy seed per pair is required')
    for origin, source in origins.items():
        if not isinstance(source, dict):
            raise ValueError(f'invalid source for {origin}')
        if source.get('unavailable') is True:
            if not source.get('reason'):
                raise ValueError(f'unavailable {origin} needs a reason')
        elif not (source.get('generation_id') and source.get('validated') is True and source.get('immutable') is True):
            raise ValueError(f'{origin} requires a validated immutable generation')
    # Round-robin with later checkpoints first, preserving planned unavailable lanes.
    left = allocation.copy()
    jobs = []
    pair = 0
    while any(left.values()):
        for origin in ('C6', 'C5', 'C4', 'C3', 'C2', 'C1', 'C0'):
            if not left[origin]:
                continue
            source = origins[origin]
            target = f'C{int(origin[1]) + 2}' if origin in ('C1', 'C2', 'C3', 'C4') else None
            for arm in (arms if pair % 2 == 0 else arms[::-1]):
                jobs.append({'id': f'pair-{pair:03d}-{arm}', 'pair_id': pair, 'origin': origin,
                             'checkpoint': origin, 'arm': arm, 'seed': seeds[pair],
                             'source': copy.deepcopy(source), 'generation_id': source.get('generation_id'),
                             'engine': copy.deepcopy(engine), 'mods': copy.deepcopy(mods),
                             'budget': copy.deepcopy(budget), 'strict_target': target,
                             'status': 'unavailable' if source.get('unavailable') is True else 'queued'})
            pair += 1
            left[origin] -= 1
    return {'allocation_pairs': allocation, 'allocation_runs': {k: 2*v for k, v in allocation.items()},
            'actual_full_origin_fraction': allocation['C0'] / (total_runs // 2),
            'unavailable_origins': {k: v['reason'] for k, v in origins.items() if v.get('unavailable') is True},
            'excluded_origins': excluded,
            'planned': total_runs, 'jobs': jobs}
