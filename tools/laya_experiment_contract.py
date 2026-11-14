# Path: tools/laya_experiment_contract.py | Purpose: Validate and bind declared experiment designs without inferring runtime exposure.
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile

FIELDS = {'version', 'hypothesis', 'factor', 'arms', 'action_path', 'eligible_start',
          'endpoint', 'predicate_version', 'sampling_unit', 'sampling_rule',
          'adoption_rule', 'missing_outcome_rule', 'window'}


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


def validate(contract, spec, *, action_path='deterministic'):
    if (not isinstance(contract, dict) or set(contract) != FIELDS
            or type(contract.get('version')) is not int or contract['version'] != 1):
        raise ValueError('experiment contract requires version 1')
    for field in ('hypothesis', 'factor', 'eligible_start', 'endpoint', 'predicate_version', 'sampling_unit', 'sampling_rule', 'adoption_rule'):
        if not isinstance(contract.get(field), str) or not contract[field].strip():
            raise ValueError(f'experiment contract requires {field}')
    if contract.get('missing_outcome_rule') != 'retain_all_planned_jobs':
        raise ValueError('experiment contract must retain all planned jobs')
    arms = contract.get('arms')
    if not isinstance(arms, dict) or set(arms) != {'control', 'treatment'}:
        raise ValueError('experiment contract requires control and treatment arm mapping')
    control, treatment = arms['control'], arms['treatment']
    if not isinstance(control, str) or not isinstance(treatment, list) or not treatment or any(not isinstance(a, str) for a in treatment):
        raise ValueError('invalid experiment arm mapping')
    references = [control, *treatment]
    if len(set(references)) != len(references) or set(references) != set(spec['arms']):
        raise ValueError('experiment arm references must match declared arms')
    path = contract.get('action_path')
    if path != action_path:
        raise ValueError('experiment action path contradicts scheduler dispatch')
    for declared in [spec.get('action_path'), spec.get('actions'),
                     *(a.get('action_path') for a in spec['arms'].values()),
                     *(a.get('actions') for a in spec['arms'].values())]:
        if declared is not None and declared != path:
            raise ValueError('experiment action path contradicts specification')
    window = contract.get('window')
    if (not isinstance(window, dict) or set(window) != {'game_ticks', 'wall_seconds'}
            or type(window['game_ticks']) is not int or window['game_ticks'] <= 0
            or type(window['wall_seconds']) not in (int, float)
            or not math.isfinite(window['wall_seconds']) or window['wall_seconds'] <= 0):
        raise ValueError('experiment contract requires positive game and wall windows')
    if window != {'game_ticks': spec['max_game_ticks'], 'wall_seconds': spec['trial_wall_seconds']}:
        raise ValueError('experiment window contradicts specification')
    canonical(contract)


def make_binding(contract, jobs, arms):
    return {'version': 1, 'contract': contract, 'planned_jobs': jobs, 'arm_sources': arms}


def publish(root, binding):
    """Publish complete, durable bytes exclusively before the suite becomes ready."""
    path = root / 'experiment-contract.json'
    raw = canonical(binding)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=root)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)
    return {'path': path.name, 'sha256': hashlib.sha256(raw).hexdigest()}


def load_binding(root, reference):
    if (not isinstance(reference, dict) or set(reference) != {'path', 'sha256'}
            or reference['path'] != 'experiment-contract.json'):
        raise ValueError('invalid contract reference')
    raw = (root / reference['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference['sha256']:
        raise ValueError('experiment contract digest mismatch')
    binding = json.loads(raw)
    if (not isinstance(binding, dict)
            or set(binding) != {'version', 'contract', 'planned_jobs', 'arm_sources'}
            or type(binding['version']) is not int or binding['version'] != 1
            or canonical(binding) != raw
            or not isinstance(binding['planned_jobs'], list)
            or not isinstance(binding['arm_sources'], list)):
        raise ValueError('invalid canonical experiment binding')
    jobs = binding['planned_jobs']
    if (any(not isinstance(job, dict) or not isinstance(job.get('id'), str) for job in jobs)
            or len({job['id'] for job in jobs}) != len(jobs)):
        raise ValueError('invalid experiment job identities')
    return binding


def verify(root, spec, freeze, status=None, *, action_path='deterministic'):
    reference = freeze.get('experiment_contract')
    if reference is None:
        if 'experiment_contract' in spec:
            raise ValueError('experiment contract binding missing')
        return {'state': 'descriptive_unknown', 'reference': None}
    try:
        if hashlib.sha256((root / 'regression.json').read_bytes()).hexdigest() != freeze.get('spec_sha256'):
            raise ValueError('frozen experiment specification changed')
        binding = load_binding(root, reference)
        validate(binding['contract'], spec, action_path=action_path)
        if (canonical(binding['contract']) != canonical(spec.get('experiment_contract'))
                or canonical(binding['arm_sources']) != canonical(freeze['arms'])):
            raise ValueError('experiment contract arm binding mismatch')
        plan = json.loads((root / 'planned-jobs.json').read_text())['jobs']
        if canonical(binding['planned_jobs']) != canonical(plan) or len(plan) != freeze['planned_jobs']:
            raise ValueError('experiment planned job binding mismatch')
        if status is not None:
            rows = status['jobs']
            if (not isinstance(rows, list) or len(rows) != len(plan)
                    or any(not isinstance(row, dict) or any(canonical(row.get(k)) != canonical(v)
                           for k, v in job.items() if k not in ('status', 'admission_reserved'))
                           for row, job in zip(rows, plan))):
                raise ValueError('experiment status job identity mismatch')
        return {'state': 'valid_declared', 'reference': reference}
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(f'experiment contract binding unavailable: {error}') from error
