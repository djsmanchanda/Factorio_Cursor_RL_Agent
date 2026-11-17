#!/usr/bin/env python3
# Path: tools/laya_dispatch_handoff.py | Purpose: Drain admissions and replace only an idle cohort coordinator with adaptive dispatch.
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import laya_host_scheduler as host
from tools import laya_regression_scheduler as regression

MANUAL_KEYS = ('manual_target', 'manual_target_until', 'manual_previous_target', 'manual_stack')


def drained(jobs):
    return all(not j.get('admission_reserved') and j.get('status') not in {'preparing', 'running'}
               and (not j.get('preparation_started') or j.get('cleanup_completed') is True) for j in jobs)


def command(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=40)


def restore(state, host_root):
    config = host._load_config(host_root)
    if config.get('dispatch_handoff_token') != state['token']:
        return
    # A later explicit ceiling supersedes this drain, but never the cooling mode.
    if config.get('manual_target') == 0:
        host._set_config(host_root, **state['previous_manual'], dispatch_handoff_token=None)
        current = host._load_state(host_root)
        current.update(target=state['previous_target'], trial_base=None, trial_total_ups=None,
                       trial_started=None, trial_ready_since=None, settle_until=0.0)
        host._atomic(host_root/'state.json', current)
    else:
        host._set_config(host_root, dispatch_handoff_token=None)


def arm(repo, cohort, old_unit, new_unit, path):
    host_root = repo/'data/laya-host'
    with host.control_lock(host_root):
        config = host._load_config(host_root)
        if config.get('manual_target') is not None or config.get('dispatch_handoff_token'):
            raise ValueError('An existing manual ceiling or handoff must be resolved first')
        spec = regression.read(cohort/'regression.json')
        if regression.sha(cohort/'regression.json') != regression.read(cohort/'freeze.json')['spec_sha256']:
            raise ValueError('Frozen specification changed')
        state = {'phase':'draining', 'token':uuid.uuid4().hex, 'armed':time.time(),
                 'expires':min(time.time()+7200, datetime.fromisoformat(spec['until']).timestamp()),
                 'repo':str(repo), 'cohort':str(cohort), 'old_unit':old_unit, 'new_unit':new_unit,
                 'previous_manual':{k:config.get(k, [] if k == 'manual_stack' else None) for k in MANUAL_KEYS},
                 'previous_target':host._load_state(host_root).get('target', 1),
                 'frozen_spec_sha256':regression.sha(cohort/'regression.json')}
        host._atomic(path,state)
        host._set_config(host_root, manual_target=0, manual_target_until=state['expires'],
                         manual_previous_target=state['previous_target'], manual_stack=[],
                         dispatch_handoff_token=state['token'])
    return state


def tick(path):
    state = regression.read(path)
    if state['phase'] != 'draining':
        return state
    repo, cohort = Path(state['repo']), Path(state['cohort'])
    host_root = repo/'data/laya-host'
    with host.control_lock(host_root):
        config = host._load_config(host_root)
        if config.get('dispatch_handoff_token') != state['token'] or config.get('manual_target') != 0:
            restore(state, host_root)
            state['phase'] = 'superseded'
        elif time.time() >= state['expires']:
            restore(state, host_root)
            state['phase'] = 'expired_without_restart'
        else:
            # The old coordinator reads this same gate while holding this lock.
            with regression.lock(cohort.parent/'.host-admission.lock'):
                gate = regression.read(cohort.parent/'_host-scheduler-gate/job-status.json')
                jobs = regression.read(cohort/'job-status.json')['jobs']
                if gate['controller']['slots'] != 0 or not drained(jobs):
                    return state
                if regression.sha(cohort/'regression.json') != state['frozen_spec_sha256']:
                    raise ValueError('Frozen specification changed during drain')
                if any(not regression.ports_free(j['game_port'],j['rcon_port']) for j in jobs if j.get('preparation_started')):
                    raise ValueError('Released job still has a live listener')
                command(['systemctl','--user','stop',state['old_unit']])
                try:
                    if any(j['status']=='queued' for j in jobs):
                        remaining = max(60, int(datetime.fromisoformat(regression.read(cohort/'regression.json')['until']).timestamp()-time.time()))
                        command(['systemd-run','--user','--collect','--unit',state['new_unit'],
                                 '--property=RuntimeMaxSec='+str(remaining),
                                 '--working-directory='+str(ROOT), sys.executable,
                                 str(ROOT/'tools/laya_checkpoint_regression.py'),'run',
                                 '--adaptive-workers','--root',str(cohort),'--registry',str(cohort.parent)])
                        command(['systemctl','--user','is-active','--quiet',state['new_unit']])
                    state['phase'] = 'complete'
                    state['switched'] = time.time()
                except Exception:
                    command(['systemctl','--user','start',state['old_unit']])
                    raise
                finally:
                    restore(state, host_root)
        host._atomic(path,state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--arm',action='store_true')
    parser.add_argument('--timer',help='stop this timer after a terminal handoff outcome')
    parser.add_argument('--repo',type=Path)
    parser.add_argument('--cohort',type=Path)
    parser.add_argument('--old-unit')
    parser.add_argument('--new-unit')
    args = parser.parse_args()
    result = arm(args.repo.resolve(),args.cohort.resolve(),args.old_unit,args.new_unit,args.state) if args.arm else tick(args.state)
    print(json.dumps({k:v for k,v in result.items() if k != 'token'},sort_keys=True))
    if args.timer and result['phase'] != 'draining':
        command(['systemctl','--user','stop',args.timer])


if __name__ == '__main__':
    main()
