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
  decision record (``state.observation_digest.saved_world.provider_stock.network``)

A job shows the exposure when its measure is at least ``at_least``. If fewer
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


def measure_records(records, exposure):
    """Return (measure, decision record count) for one job's decision records."""
    form, count, decisions = exposure['form'], 0, 0
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get('choice'), dict):
            continue
        decisions += 1
        options = row.get('options') if isinstance(row.get('options'), list) else []
        choice = row['choice']
        if form == 'offered':
            count += any(_matches(option, exposure) for option in options)
        elif form in ('chosen', 'succeeded'):
            chosen = next((o for o in options if isinstance(o, dict)
                           and o.get('option_id') == choice.get('option_id')), {'option_id': choice.get('option_id')})
            outcome = row.get('outcome') if isinstance(row.get('outcome'), dict) else {}
            count += _matches(chosen, exposure) and (form == 'chosen' or outcome.get('ok') is True)
        elif form == 'rule_applied':
            count += exposure['rule'] in (choice.get('rules') or ())
        else:
            try:
                value = row['state']['observation_digest']['saved_world']['provider_stock']['network'].get(exposure['item'], 0)
            except (KeyError, TypeError, AttributeError):
                continue
            if type(value) in (int, float):
                count = max(count, value)
    return count, decisions


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
        value, decisions = measure_records(rows, exposure)
        record.update(value=value, decisions=decisions, shown=value >= exposure['at_least'])
    except Exception as error:  # noqa: BLE001 -- runs in the scheduler's finally; must never block terminal publication
        record.update(value=None, decisions=0, shown=None, error=f'{type(error).__name__}: {error}')
    return record


def evaluate(spec, status, terminal):
    """Record verdicts for arms that reached K measured jobs; cancel a failed arm's queued jobs.

    Mutates ``status`` only for specs that declare a check. Returns the arms given a verdict now.
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
        measured = sorted((job for job in status['jobs'] if job.get('arm') == arm and job.get('status') in terminal
                           and isinstance(job.get('exposure'), dict) and (job['exposure'].get('decisions') or 0) > 0),
                          key=lambda job: (job['exposure'].get('measured_at') or 0, job['id']))
        if len(measured) < declaration['after_jobs']:
            continue
        window = measured[:declaration['after_jobs']]
        shown = [job['id'] for job in window if job['exposure'].get('shown') is True]
        passed = len(shown) >= declaration['min_jobs']
        verdict = {'state': 'passed' if passed else 'failed', 'evaluated_at': time.time(),
                   'declaration': declaration, 'evaluated_jobs': [job['id'] for job in window],
                   'values': {job['id']: job['exposure'].get('value') for job in window},
                   'jobs_showing_exposure': shown, 'cancelled_jobs': []}
        if not passed:
            for job in status['jobs']:
                if job.get('arm') == arm and job.get('status') == 'queued' and not job.get('admission_reserved'):
                    job.update(status='cancelled', termination_cause=FAILED_CAUSE)
                    verdict['cancelled_jobs'].append(job['id'])
            verdict['reason'] = (f"{len(shown)} of the first {len(window)} measured jobs showed "
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
        measured = [job for job in status.get('jobs', []) if job.get('arm') == arm and job.get('status') in terminal
                    and isinstance(job.get('exposure'), dict) and (job['exposure'].get('decisions') or 0) > 0]
        result[arm] = {'state': 'pending', 'declaration': raw, 'measured_jobs': len(measured)}
    return {'arms': result, 'control_arms_unchecked': [arm for arm in (spec.get('arms') or {}) if arm not in declared]}
