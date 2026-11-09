#!/usr/bin/env python3
# Path: tools/laya_checkpoint_regression.py | Purpose: Freeze, queue and report full and restart-conditioned Laya regressions.
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.laya_regression_scheduler import enqueue, read, run_one, run_campaign, recover, TERMINAL
from training.laya_restart_checkpoint import load


def report(root):
    """Account for every planned job; current observations are distinct from latches."""
    status, spec = read(root/'job-status.json'), read(root/'regression.json')
    groups = {}
    for job in status['jobs']:
        origin = job.get('origin', job.get('checkpoint', 'unknown'))
        generation = job.get('generation_id') or (job.get('source') or {}).get('generation_id') or job.get('bundle_sha256') or 'unknown'
        key = (origin, job['arm'], generation)
        group = groups.setdefault(key, {'origin': origin, 'checkpoint': origin, 'arm': job['arm'],
            'generation': generation, 'conditioning': 'full_origin' if origin == 'C0' else 'restart_conditioned',
            'planned': 0, 'started': 0, 'terminal': 0, 'observed_successors': 0, 'unknown': 0, 'timeouts': 0,
            'research': {'target':spec.get('research_target'), 'observed':0, 'earned':0,
                         'progressed':0, 'unknown':0},
            'strict_reach': {}, 'held_at_end': {}, 'classifications': {}, 'faults': {},
            'latency': {'time_seconds': [], 'ticks': [], 'decisions': [], 'server_seconds': [],
                        'missing': {'time_seconds': 0, 'ticks': 0, 'decisions': 0, 'server_seconds': 0}}})
        group['planned'] += 1
        state = job.get('status', 'unknown')
        group['started'] += bool(job.get('started') or job.get('started_at'))
        group['terminal'] += state in TERMINAL
        # Preserve legacy fields, explicitly labelled as legacy latch evidence.
        group['observed_successors'] += job.get('successor_passed') is True
        group['unknown'] += job.get('successor_passed') is None
        group['timeouts'] += state == 'timeout'
        research = job.get('research_acceptance')
        if isinstance(research, dict) and research.get('available') is True:
            group['research']['observed'] += 1
            group['research']['earned'] += research.get('earned') is True
            group['research']['progressed'] += type(research.get('research_units')) in (int, float) and research['research_units'] > 0
        else:
            group['research']['unknown'] += 1
        observations = job.get('strict_current')
        observations = observations if isinstance(observations, list) else []
        loaded = job.get('loaded_tick')
        index = int(origin[1:]) if origin in {f'C{i}' for i in range(7)} else 6
        for stage in (f'C{i}' for i in (index + 1, index + 2) if i <= 6):
            rows = [row for row in observations if isinstance(row, dict) and
                    type(row.get('tick')) is int and type(loaded) is int and row['tick'] > loaded and
                    isinstance(row.get('statuses'), dict) and row['statuses'].get(stage) is True]
            bucket = group['strict_reach'].setdefault(stage, {'reached': 0, 'not_observed': 0, 'ticks_from_load': []})
            bucket['reached' if rows else 'not_observed'] += 1
            if rows:
                bucket['ticks_from_load'].append(min(row['tick'] for row in rows) - loaded)
                first = min(rows,key=lambda row:row['tick'])
                bucket.setdefault('time_seconds_from_load', []).append(first.get('elapsed_seconds'))
                bucket.setdefault('decisions_from_load', []).append(first.get('decisions'))
            final = job.get('strict_current_at_end')
            held = group['held_at_end'].setdefault(stage, {'true': 0, 'false': 0, 'unknown': 0})
            value = final.get(stage) if isinstance(final, dict) else None
            held['true' if value is True else 'false' if value is False else 'unknown'] += 1
        reason = job.get('stop_classification') or job.get('stop_reason')
        if state == 'timeout' or reason in ('tick_timeout', 'wall_timeout', 'deadline_timeout', 'timeout'):
            category = 'right_censored_timeout'
        elif reason in ('target_success', 'genuine_failure', 'safe_stop', 'infrastructure_failure', 'capability_limit', 'missing_capability'):
            category = reason
        else:
            category = 'unknown'
        group['classifications'][category] = group['classifications'].get(category, 0) + 1
        faults = job.get('faults') or {}
        if isinstance(faults, dict):
            counts = faults
            if isinstance(faults.get('event_counts'), dict):
                counts = {f'{category}:{code}':count for category, values in faults['event_counts'].items()
                          for code,count in values.items()}
            for name, count in counts.items():
                if type(count) is int:
                    group['faults'][name] = group['faults'].get(name, 0) + count
        elif isinstance(faults, list):
            for name in faults:
                group['faults'][str(name)] = group['faults'].get(str(name), 0) + 1
        for metric in ('time_seconds', 'ticks', 'decisions', 'server_seconds'):
            value = (job.get('progress_latency') or {}).get(metric) if metric != 'server_seconds' else job.get(metric)
            if type(value) in (int, float) and value >= 0:
                group['latency'][metric].append(value)
            else:
                group['latency']['missing'][metric] += 1
    signals = [{'origin':j.get('origin',j.get('checkpoint')), 'arm':j['arm'], 'generation':j.get('generation_id'),
                'latency':j.get('progress_latency')} for j in status['jobs'] if j.get('progress_latency')]
    comparisons = []
    for stage in (f'C{i}' for i in range(1,7)):
        by_origin = {}
        for origin in (f'C{i}' for i in range(7)):
            rows = [j for j in status['jobs'] if j.get('origin',j.get('checkpoint')) == origin]
            if rows:
                by_origin[origin] = {'planned':len(rows), 'observed_post_load':sum(stage in j.get('strict_reached',{}) for j in rows)}
        comparisons.append({'stage':stage,'origins':by_origin})
    return {'kind': 'laya-restart-segment-v1', 'name': status['name'], 'groups': list(groups.values()),
            'planned': len(status['jobs']), 'jobs': status['jobs'], 'until': spec.get('until'),
            'legacy_successor_fields_are_latches': True,
            'strict_evidence': 'post-load strict_current observations only; absent evidence is not failure',
            'proxy_phase_success': 'not inferred from strict evidence',
            'first_useful_stage_signal': signals,
            'stage_coverage':sorted({g['origin'] for g in groups.values() if g['started']}),
            'full_origin_comparison': comparisons,
            'full_origin_agreement':'Review stage-specific counts, failure signatures and direction; different starting conditions preclude pooled rates.',
            'repeated_failures':[{ 'origin':g['origin'],'arm':g['arm'],'generation':g['generation'],'faults':g['faults']} for g in groups.values() if g['faults']],
            'interpretation': 'A/A measures wiring, not policy improvement, adoption, or significance; restart outcomes are conditional.',
            'full_run_acceptance_gate': 'not_evaluated', 'automatic_adoption': False,
            'same_seed_is_counterfactual': False, 'exact_continuation': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['enqueue', 'step', 'run', 'cleanup', 'report', 'catalog', 'validate'])
    parser.add_argument('--spec', type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--registry', type=Path, default=ROOT/'data/laya-experiments')
    parser.add_argument('--bundle', type=Path)
    args = parser.parse_args(argv)
    if args.action == 'enqueue':
        if not args.spec:
            parser.error('--spec required')
        root = enqueue(ROOT, args.registry.resolve(), read(args.spec))
        print(json.dumps({'root': str(root), 'planned': report(root)['planned']}))
        return 0
    if args.action == 'validate':
        if not args.bundle:
            parser.error('--bundle required')
        metadata, context = load(args.bundle)
        print(json.dumps({'checkpoint': metadata['checkpoint_id'], 'tick': metadata['capture_tick'],
                          'kind': context['kind'], 'exact_continuation': False}, indent=2))
        return 0
    if not args.root:
        parser.error('--root required')
    root = args.root.resolve()
    root.relative_to(args.registry.resolve())
    if args.action == 'cleanup':
        recover(root)
        print(json.dumps({'cleanup':'verified'}))
    elif args.action == 'catalog':
        rows = []
        for bundle in sorted((root/'catalog').glob('laya-*')):
            try:
                metadata, context = load(bundle)
                rows.append({'bundle': str(bundle), 'checkpoint': metadata['checkpoint_id'],
                             'tick': metadata['capture_tick'], 'status': 'verified'})
            except (OSError, ValueError) as error:
                rows.append({'bundle': str(bundle), 'status': 'incompatible', 'error': str(error)})
        print(json.dumps(rows, indent=2))
    elif args.action == 'report':
        print(json.dumps(report(root), indent=2))
    else:
        state = run_one(root) if args.action == 'step' else run_campaign(root)
        print(json.dumps({'time':time.time(),'state':state}),flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f'Regression stopped: {error}', file=sys.stderr)
        raise SystemExit(2)
