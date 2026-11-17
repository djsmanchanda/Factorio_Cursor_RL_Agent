# Path: tools/laya_manipulation_check.py | Purpose: Declared per-arm exposure checks that pause an arm whose treatment never takes effect.
"""Manipulation check for regression cohorts.

An arm may declare, in its regression spec entry, the exposure that must be
observed for its treatment to count::

    "manipulation_check": {
        "after_jobs": 6,            # K: evaluate once K finished jobs have decision records (default 6)
        "min_jobs": 1,              # jobs among those K that must show the exposure (default 1)
        "exposure": {"form": "succeeded", "option_id": "op.production_service.copper-cable", "at_least": 1}
    }

Each form is measured per job from ``evidence/decision-states.jsonl`` only:

- ``offered``      decision records whose options include the option_id/family
- ``chosen``       decision records whose choice is the option_id/family
- ``succeeded``    as ``chosen`` with ``outcome.ok is True``
- ``rule_applied`` decision records whose ``choice.rules`` contains ``rule``
- ``network_stock`` largest provider network stock of ``item`` seen in any
  decision record (``state.observation_digest.saved_world.provider_stock.network``).
  A record whose census is missing or malformed is not an observation; only a
  valid census without the item is an observed zero.

A job shows the exposure when its measure is at least ``at_least``. Only jobs
with at least one usable exposure observation are measured; a job whose
telemetry is unavailable records ``value=null, shown=null`` with a cause and
never counts toward K. If fewer
than ``min_jobs`` of the first K measured jobs show it, the arm fails: its
queued jobs are cancelled with ``manipulation_check_failed`` and the verdict is
recorded in the cohort status. Running jobs are never touched. Arms without a
declaration are never evaluated.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import subprocess
import time

DEFAULT_AFTER_JOBS = 6
FORMS = {'offered', 'chosen', 'succeeded', 'rule_applied', 'network_stock'}
OPTION_FORMS = {'offered', 'chosen', 'succeeded'}
FAILED_CAUSE = 'manipulation_check_failed'
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')


def declarations(spec):
    """Arm -> declaration for the arms that declare a check, in spec order."""
    return {arm: config['manipulation_check'] for arm, config in (spec.get('arms') or {}).items()
            if isinstance(config, dict) and 'manipulation_check' in config}


def _positive_int(value, field):
    if type(value) is not int or value < 1:
        raise ValueError(f'manipulation check {field} must be a positive integer')
    return value


def validate(declaration, arm_jobs):
    """Shape check; ``arm_jobs`` is the arm's planned job count."""
    if not isinstance(declaration, dict) or not set(declaration) <= {'after_jobs', 'min_jobs', 'exposure'}:
        raise ValueError('manipulation check allows only after_jobs, min_jobs and exposure')
    exposure = declaration.get('exposure')
    form = exposure.get('form') if isinstance(exposure, dict) else None
    if form not in FORMS:
        raise ValueError(f'manipulation check form must be one of {sorted(FORMS)}')
    selector = {'offered': None, 'chosen': None, 'succeeded': None,
                'rule_applied': {'rule'}, 'network_stock': {'item'}}[form]
    keys = set(exposure) - {'form', 'at_least'}
    if form in OPTION_FORMS:
        if keys not in ({'option_id'}, {'family'}):
            raise ValueError(f'manipulation check form {form} takes exactly one of option_id or family')
    elif keys != selector:
        raise ValueError(f'manipulation check form {form} takes {sorted(selector)}')
    for key in keys:
        if not isinstance(exposure[key], str) or not _NAME.fullmatch(exposure[key]):
            raise ValueError(f'manipulation check {key} must be a bounded name')
    _positive_int(exposure.get('at_least', 1), 'at_least')
    after = _positive_int(declaration.get('after_jobs', DEFAULT_AFTER_JOBS), 'after_jobs')
    needed = _positive_int(declaration.get('min_jobs', 1), 'min_jobs')
    if after > arm_jobs:
        raise ValueError(f'manipulation check after_jobs {after} exceeds the arm\'s {arm_jobs} planned jobs')
    if needed > after:
        raise ValueError('manipulation check min_jobs exceeds after_jobs')
    return {'after_jobs': after, 'min_jobs': needed, 'exposure': {'at_least': 1, **exposure}}


def _tracked(code, pattern):
    names = subprocess.check_output(['git', 'ls-files', '-z', '--', pattern], cwd=code).decode().split('\0')
    return [code/name for name in names if name]


def code_vocabulary(code):
    """String constants in the arm's Laya Python, and rule ids in its quoted catalogs."""
    strings, rules = set(), set()
    for path in _tracked(code, 'training/*.py'):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        strings.update(node.value for node in ast.walk(tree)
                       if isinstance(node, ast.Constant) and isinstance(node.value, str))

    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get('rule_id'), str):
                rules.add(value['rule_id'])
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    for path in _tracked(code, 'docs/rl/*.json'):
        try:
            visit(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            continue
    return strings, rules


def validate_against_code(declaration, code):
    """Freeze-time check that the declared family, option or rule exists in the frozen arm."""
    exposure = declaration['exposure']
    if 'family' not in exposure and 'option_id' not in exposure and 'rule' not in exposure:
        return  # network_stock names a game item, which code cannot vouch for
    strings, rules = code_vocabulary(Path(code))
    if 'family' in exposure and exposure['family'] not in strings:
        raise ValueError(f"manipulation check family {exposure['family']!r} does not exist in the frozen arm")
    if 'option_id' in exposure:
        option = exposure['option_id']
        # Option ids are often composed (f"op.{family}.{item}"): accept a literal id or a known family segment.
        parts = option.split('.')
        family = parts[1] if parts[0] == 'op' and len(parts) > 1 else parts[0]
        if option not in strings and family not in strings:
            raise ValueError(f'manipulation check option {option!r} has no family in the frozen arm')
    if 'rule' in exposure and exposure['rule'] not in rules and exposure['rule'] not in strings:
        raise ValueError(f"manipulation check rule {exposure['rule']!r} does not exist in the frozen arm")


def _matches(option, exposure):
    if not isinstance(option, dict):
        return False
    if 'option_id' in exposure:
        return option.get('option_id') == exposure['option_id']
    return option.get('family') == exposure['family']


def _network_stock(row, item):
    """Stock of ``item`` in the row's provider network census, or None when the census is unusable.

    An item absent from a valid census is an observed zero; a missing or malformed census is not.
    """
    try:
        census = row['state']['observation_digest']['saved_world']['provider_stock']['network']
    except (KeyError, TypeError):
        return None
    if not isinstance(census, dict):
        return None
    value = census.get(item, 0)
    if type(value) not in (int, float) or value != value or value < 0 or value == float('inf'):
        return None
    return value


def measure_records(records, exposure):
    """Return (measure, decision record count, usable observation count) for one job's decision records.

    ``observations`` counts the decision rows that carry the telemetry this form reads,
    so a row without it can neither show nor refute the exposure.
    """
    form, count, decisions, observations = exposure['form'], 0, 0, 0
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get('choice'), dict):
            continue
        decisions += 1
        options = row.get('options') if isinstance(row.get('options'), list) else []
        choice = row['choice']
        if form == 'offered':
            if not isinstance(row.get('options'), list):
                continue
            observations += 1
            count += any(_matches(option, exposure) for option in options)
        elif form in ('chosen', 'succeeded'):
            observations += 1
            chosen = next((o for o in options if isinstance(o, dict)
                           and o.get('option_id') == choice.get('option_id')), {'option_id': choice.get('option_id')})
            outcome = row.get('outcome') if isinstance(row.get('outcome'), dict) else {}
            count += _matches(chosen, exposure) and (form == 'chosen' or outcome.get('ok') is True)
        elif form == 'rule_applied':
            observations += 1
            count += exposure['rule'] in (choice.get('rules') or ())
        else:
            value = _network_stock(row, exposure['item'])
            if value is None:
                continue
            observations += 1
            count = max(count, value)
    return count, decisions, observations


def measure_job(world, declaration):
    """Per-job exposure record stored on the job row; never raises."""
    exposure = declaration['exposure']
    record = {'form': exposure['form'], 'measured_at': time.time()}
    try:
        rows = []
        path = Path(world)/'evidence/decision-states.jsonl'
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        value, decisions, observations = measure_records(rows, exposure)
        if observations:
            record.update(value=value, decisions=decisions, observations=observations, shown=value >= exposure['at_least'])
        else:
            cause = 'exposure_telemetry_unavailable' if decisions else 'no_decision_records'
            record.update(value=None, decisions=decisions, observations=0, shown=None, cause=cause)
    except Exception as error:  # noqa: BLE001 -- runs in the scheduler's finally; must never block terminal publication
        record.update(value=None, decisions=0, observations=0, shown=None, cause='measurement_error', error=f'{type(error).__name__}: {error}')
    return record


def _usable(job, terminal):
    """A terminal job whose exposure record holds at least one usable observation.

    Records written before ``observations`` existed fall back to their decision count.
    """
    exposure = job.get('exposure')
    return (job.get('status') in terminal and isinstance(exposure, dict)
            and (exposure.get('observations', exposure.get('decisions')) or 0) > 0)


def _measured(status, arm, terminal):
    return sorted((job for job in status['jobs'] if job.get('arm') == arm and _usable(job, terminal)),
                  key=lambda job: (job['exposure'].get('measured_at') or 0, job['id']))


def _verdict(declaration, window, state, **extra):
    shown = [job['id'] for job in window if job['exposure'].get('shown') is True]
    return {'state': state, 'evaluated_at': time.time(), 'declaration': declaration,
            'evaluated_jobs': [job['id'] for job in window],
            'values': {job['id']: job['exposure'].get('value') for job in window},
            'jobs_showing_exposure': shown, 'cancelled_jobs': [], **extra}


def evaluate(spec, status, terminal):
    """Record verdicts for arms that reached K measured jobs; cancel a failed arm's queued jobs.

    Mutates ``status`` only for specs that declare a check. Returns the arms given a verdict now.
    An arm whose jobs are all terminal but fewer than K were measured gets an ``inconclusive``
    verdict: nothing more can arrive, and an unmeasured treatment is not a failed one.
    """
    declared = declarations(spec)
    if not declared:
        return []
    verdicts = status.setdefault('manipulation_checks', {})
    recorded = []
    for arm, raw in declared.items():
        if arm in verdicts:
            continue
        declaration = validate(raw, sum(job.get('arm') == arm for job in status['jobs']))
        measured = _measured(status, arm, terminal)
        if len(measured) < declaration['after_jobs']:
            jobs = [job for job in status['jobs'] if job.get('arm') == arm]
            if jobs and all(job.get('status') in terminal for job in jobs):
                verdicts[arm] = _verdict(
                    declaration, measured, 'inconclusive', measured_jobs=len(measured),
                    unmeasured_jobs={job['id']: (job.get('exposure') or {}).get('cause') or 'no_exposure_record'
                                     for job in jobs if not _usable(job, terminal)},
                    reason=f"{len(measured)} of {declaration['after_jobs']} required jobs had usable exposure telemetry")
                recorded.append(arm)
            continue
        window = measured[:declaration['after_jobs']]
        passed = sum(job['exposure'].get('shown') is True for job in window) >= declaration['min_jobs']
        verdict = _verdict(declaration, window, 'passed' if passed else 'failed')
        if not passed:
            for job in status['jobs']:
                if job.get('arm') == arm and job.get('status') == 'queued' and not job.get('admission_reserved'):
                    job.update(status='cancelled', termination_cause=FAILED_CAUSE)
                    verdict['cancelled_jobs'].append(job['id'])
            verdict['reason'] = (f"{len(verdict['jobs_showing_exposure'])} of the first {len(window)} measured jobs showed "
                                 f"{declaration['exposure']}; {declaration['min_jobs']} required")
        verdicts[arm] = verdict
        recorded.append(arm)
    return recorded


def summarize(spec, status, terminal):
    """Report view: recorded verdicts, plus the pending state of declared arms without one."""
    declared = declarations(spec)
    if not declared:
        return None
    verdicts = status.get('manipulation_checks') if isinstance(status.get('manipulation_checks'), dict) else {}
    result = {}
    for arm, raw in declared.items():
        if arm in verdicts:
            result[arm] = verdicts[arm]
            continue
        arm_jobs = [job for job in status.get('jobs', []) if job.get('arm') == arm]
        measured = [job for job in arm_jobs if _usable(job, terminal)]
        unmeasured = {job['id']: (job.get('exposure') or {}).get('cause') or 'no_exposure_record'
                      for job in arm_jobs if job.get('status') in terminal and not _usable(job, terminal)}
        result[arm] = {'state': 'pending', 'declaration': raw, 'measured_jobs': len(measured),
                       'unmeasured_jobs': unmeasured}
    return {'arms': result, 'control_arms_unchecked': [arm for arm in (spec.get('arms') or {}) if arm not in declared]}


def offline_verdicts(root, terminal, *, remeasure=True):
    """Verdicts for a cohort directory computed read-only; nothing is written.

    Reads ``regression.json`` and ``job-status.json`` and, with ``remeasure``, re-reads each terminal
    job's ``evidence/decision-states.jsonl`` so records written under older measurement rules are
    judged by the current ones. Returns the verdicts computed now and, separately, any the
    scheduler had already published.
    """
    import copy
    root = Path(root)
    spec = json.loads((root/'regression.json').read_text())
    status = copy.deepcopy(json.loads((root/'job-status.json').read_text()))
    published = copy.deepcopy(status.get('manipulation_checks') or {})
    status['manipulation_checks'] = {}
    declared = declarations(spec)
    if remeasure:
        for job in status['jobs']:
            if job.get('arm') in declared and job.get('status') in terminal and job.get('world'):
                kept = (job.get('exposure') or {}).get('measured_at')
                job['exposure'] = measure_job(job['world'], validate(
                    declared[job['arm']], sum(row.get('arm') == job['arm'] for row in status['jobs'])))
                if kept is not None:  # keep the scheduler's original window order
                    job['exposure']['measured_at'] = kept
    evaluate(spec, status, terminal)
    return {'cohort': root.name, 'remeasured': remeasure,
            'verdicts': status['manipulation_checks'], 'published_verdicts': published}


def main(argv=None):
    """``offline COHORT_DIR``: print the verdicts a finished cohort's declared checks yield; read-only."""
    import argparse
    import sys
    from tools.laya_regression_scheduler import TERMINAL
    parser = argparse.ArgumentParser(description=main.__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    offline = sub.add_parser('offline', help='evaluate a cohort directory without writing into it')
    offline.add_argument('cohort', type=Path)
    offline.add_argument('--stored-exposure', action='store_true',
                         help='use the exposure records stored on the job rows instead of re-reading each world')
    offline.add_argument('--output', type=Path, help='also write the JSON here (refused inside the cohort directory)')
    args = parser.parse_args(argv)
    cohort = args.cohort.resolve()
    result = offline_verdicts(cohort, TERMINAL, remeasure=not args.stored_exposure)
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output is not None:
        out = args.output.resolve()
        if cohort == out or cohort in out.parents:
            parser.error('--output must be outside the cohort directory')
        out.write_text(text + '\n')
    sys.stdout.write(text + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
