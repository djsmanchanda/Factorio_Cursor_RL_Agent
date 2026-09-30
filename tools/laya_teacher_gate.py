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


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline',type=Path)
    parser.add_argument('candidate',type=Path)
    parser.add_argument('--seed-start',type=int,required=True)
    parser.add_argument('--episodes',type=int,required=True)
    args=parser.parse_args(argv)
    if args.seed_start<0 or args.episodes<1:
        parser.error('expected cohort must have a nonnegative start and positive size')
    try:
        code,report=assess(load(args.baseline),load(args.candidate),set(range(args.seed_start,args.seed_start+args.episodes)))
    except (OSError,ValueError) as error:
        code,report=2,{'status':'blocked','reason':str(error),'eligible_for_real_nauvis':False}
    print(json.dumps(report))
    return code


if __name__=='__main__':
    raise SystemExit(main())
