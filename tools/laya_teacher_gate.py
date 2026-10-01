#!/usr/bin/env python3
# Path: tools/laya_teacher_gate.py | Purpose: Admit a disposable training continuation only from matched completion/cost evidence.

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from tools.compare_laya_batches import load, compare


def assess(baseline, candidate, expected):
    """0 keeps the candidate, 3 chooses the verified baseline, 2 blocks errors.

    This gate selects a training teacher, never a production policy. A cheaper
    failed cohort cannot be admitted without a measured completion. Runtime or
    evidence failures block instead of being explained away as policy behavior.
    """
    try:
        reference = compare(baseline,baseline,expected,(36_000,10.0,60.0))
        result = compare(baseline,candidate,expected,(36_000,10.0,60.0))
    except (ValueError,KeyError,TypeError) as error:
        return 2, {'status':'blocked','reason':str(error),'eligible_for_real_nauvis':False}
    report = {'version':'1.0.0','purpose':'training_teacher','reference':reference,'candidate':result,
              'eligible_for_real_nauvis':False}
    if reference['errors'] or reference['safety_violations'] or result['errors'] or result['safety_violations']:
        return 2, {**report,'status':'blocked','reason':'teacher evidence contains runtime or safety failures'}
    if result['completed']==0 and reference['completed']==0:
        return 2, {**report,'status':'blocked','reason':'no teacher completed this cohort'}
    if result['completion_losses'] or result['completed'] < reference['completed'] or (
        result['completed']==reference['completed'] and result['mean_cost_ticks'] > reference['mean_cost_ticks']
    ):
        return 3, {**report,'status':'use_reference','reason':'candidate loses completion or mean cost'}
    return 0, {**report,'status':'admitted'}


def assess_levels(baselines, candidates, expected):
    """A globally reused teacher must pass every enabled curriculum level."""
    if not baselines or set(baselines) != set(candidates):
        return 2, {'status': 'blocked', 'reason': 'missing enabled-level evidence',
                   'eligible_for_real_nauvis': False}
    reports = {}
    codes = []
    for level in sorted(baselines):
        code, report = assess(baselines[level], candidates[level], expected)
        reports[str(level)] = report
        codes.append(code)
    code = 2 if 2 in codes else 3 if 3 in codes else 0
    return code, {'version': '1.0.0', 'purpose': 'training_teacher',
                  'status': {0: 'admitted', 2: 'blocked', 3: 'use_reference'}[code],
                  'levels': reports, 'eligible_for_real_nauvis': False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline',type=Path,nargs='?')
    parser.add_argument('candidate',type=Path,nargs='?')
    parser.add_argument('--level-pair', nargs=3, action='append', metavar=('LEVEL', 'BASELINE', 'CANDIDATE'),
                        help='Repeat for every enabled level before admitting a global continuation')
    parser.add_argument('--seed-start',type=int,required=True)
    parser.add_argument('--episodes',type=int,required=True)
    args=parser.parse_args(argv)
    if args.seed_start<0 or args.episodes<1:
        parser.error('expected cohort must have a nonnegative start and positive size')
    if args.level_pair:
        if args.baseline or args.candidate:
            parser.error('use positional batches or --level-pair, not both')
        levels = [pair[0] for pair in args.level_pair]
        if len(set(levels)) != len(levels) or any(level not in {'1', '2', '3'} for level in levels):
            parser.error('level pairs require unique workflow levels 1, 2 or 3')
    elif args.baseline is None or args.candidate is None:
        parser.error('provide both batches or --level-pair')
    try:
        expected = set(range(args.seed_start, args.seed_start + args.episodes))
        if args.level_pair:
            code, report = assess_levels(
                {level: load(Path(base)) for level, base, _ in args.level_pair},
                {level: load(Path(candidate)) for level, _, candidate in args.level_pair}, expected)
        else:
            code,report=assess(load(args.baseline),load(args.candidate),expected)
    except (OSError,ValueError) as error:
        code,report=2,{'status':'blocked','reason':str(error),'eligible_for_real_nauvis':False}
    print(json.dumps(report))
    return code


if __name__=='__main__':
    raise SystemExit(main())
