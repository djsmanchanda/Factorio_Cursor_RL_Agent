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
from tools import laya_experiment_contract, laya_manipulation_check
from training.laya_restart_checkpoint import load


def report(root):
    """Account for every planned job; current observations are distinct from latches."""
    spec = read(root/'regression.json')
    try:
        status = read(root/'job-status.json')
        if not isinstance(status, dict) or not isinstance(status.get('jobs'), list):
            raise ValueError('invalid status projection')
    except (OSError, ValueError) as error:
        status = {'name': spec.get('name', root.name), 'jobs': [], 'evidence_error': str(error)}
    freeze = {}
    try:
        freeze = read(root/'freeze.json')
        declaration = laya_experiment_contract.verify(root, spec, freeze)
    except (OSError, ValueError, KeyError, TypeError) as error:
        declaration = {'state': 'invalid', 'reference': None, 'error': str(error)}
    denominator = {'state': 'legacy_status_only'}
    try:
        # The bound plan survives damage to its mutable on-disk projection.
        if freeze.get('experiment_contract') is not None:
            planned = laya_experiment_contract.load_binding(root, freeze['experiment_contract'])['planned_jobs']
            denominator = {'state': 'bound_plan'}
        else:
            planned = read(root/'planned-jobs.json')['jobs']
            denominator = {'state': 'planned_jobs_file'}
        if (not isinstance(planned, list)
                or any(not isinstance(job, dict) or not isinstance(job.get('id'), str) for job in planned)
                or len({job['id'] for job in planned}) != len(planned)):
            raise ValueError('invalid planned job identities')
    except (OSError, KeyError, TypeError, ValueError) as error:
        if freeze.get('experiment_contract') is not None:
            denominator = {'state': 'unavailable', 'error': str(error)}
            status['jobs'] = []
        else:
            denominator = {'state': 'legacy_status_only', 'error': str(error)}
    else:
        observed = {}
        for row in status['jobs']:
            if isinstance(row, dict) and isinstance(row.get('id'), str):
                observed.setdefault(row['id'], []).append(row)
        projected = []
        for job in planned:
            candidates = observed.get(job['id'], [])
            row = candidates[0] if len(candidates) == 1 else None
            try:
                conflict = row is not None and any(
                    k in row and laya_experiment_contract.canonical(row[k]) != laya_experiment_contract.canonical(v)
                    for k, v in job.items() if k not in ('status', 'admission_reserved'))
            except (TypeError, ValueError):
                conflict = True
            if row is None or conflict:
                projected.append({**job, 'status': 'unknown',
                                  'status_evidence': 'conflicting' if candidates else 'missing'})
            else:
                projected.append({**job, **row, 'status': row.get('status', 'unknown')})
        status['jobs'] = projected
    groups = {}
    for job in status['jobs']:
        origin = job.get('origin', job.get('checkpoint', 'unknown'))
        generation = job.get('generation_id') or (job.get('source') or {}).get('generation_id') or job.get('bundle_sha256') or 'unknown'
        key = (origin, job['arm'], generation)
        group = groups.setdefault(key, {'origin': origin, 'checkpoint': origin, 'arm': job['arm'],
            'generation': generation, 'conditioning': 'full_origin' if origin == 'C0' else 'restart_conditioned',
            'planned': 0, 'started': 0, 'terminal': 0, 'observed_successors': 0, 'unknown': 0, 'timeouts': 0,
            'research': {'target':(spec.get('arms') or {}).get(job['arm'], {}).get('research_target') or spec.get('research_target'), 'observed':0, 'earned':0,
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
        index = int(origin[1:]) if origin in {f'C{i}' for i in range(10)} else 9
        for stage in (f'C{i}' for i in range(index + 1, 10)):
            rows = [row for row in observations if isinstance(row, dict) and
                    type(row.get('tick')) is int and type(loaded) is int and row['tick'] > loaded and
                    isinstance(row.get('statuses'), dict) and row['statuses'].get(stage) is True]
            reached = (job.get('strict_reached') or {}).get(stage)
            reached = reached if type(reached) is int and type(loaded) is int and reached > loaded else None
            bucket = group['strict_reach'].setdefault(stage, {'reached': 0, 'not_observed': 0, 'ticks_from_load': []})
            bucket['reached' if rows or reached is not None else 'not_observed'] += 1
            if reached is not None or rows:
                first = (job.get('stage_latency') or {}).get(stage, {})
                if rows:
                    first = min(rows,key=lambda row:row['tick'])
                bucket['ticks_from_load'].append(reached - loaded if reached is not None else first['tick'] - loaded)
                bucket.setdefault('time_seconds_from_load', []).append(first.get('elapsed_seconds', first.get('time_seconds')))
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
    for stage in (f'C{i}' for i in range(1,10)):
        by_origin = {}
        for origin in (f'C{i}' for i in range(7)):
            rows = [j for j in status['jobs'] if j.get('origin',j.get('checkpoint')) == origin]
            if rows:
                by_origin[origin] = {'planned':len(rows), 'observed_post_load':sum(stage in j.get('strict_reached',{}) for j in rows)}
        comparisons.append({'stage':stage,'origins':by_origin})
    result = {'experiment_contract': declaration, 'exposure_evidence': 'unknown',
            'effective_intervention_evidence': 'unknown', 'inference_eligibility': 'unknown',
            'kind': 'laya-restart-segment-v1', 'name': status['name'], 'groups': list(groups.values()),
            'planned': None if denominator['state'] == 'unavailable' else len(status['jobs']),
            'denominator_evidence': denominator, 'jobs': status['jobs'], 'until': spec.get('until'),
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
    # Only cohorts that declare a manipulation check gain these fields.
    checks = laya_manipulation_check.summarize(spec, status, TERMINAL)
    if checks is not None:
        result['manipulation_check'] = checks
        result['exposure_evidence'] = 'declared_manipulation_check'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['enqueue', 'step', 'run', 'cleanup', 'report', 'catalog', 'validate'])
    parser.add_argument('--adaptive-workers', action='store_true',
                        help='use host admission instead of the frozen dispatch-pool cap; records an operational override')
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
        state = run_one(root) if args.action == 'step' else run_campaign(root, adaptive=args.adaptive_workers)
        print(json.dumps({'time':time.time(),'state':state}),flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f'Regression stopped: {error}', file=sys.stderr)
        raise SystemExit(2)
