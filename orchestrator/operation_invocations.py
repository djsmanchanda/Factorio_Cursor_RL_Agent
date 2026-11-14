# Path: orchestrator/operation_invocations.py | Purpose: Persist selected submissions and survey their exact construction read-only.
"""Invocation evidence belongs to one episode and never resubmits a plan."""
import copy
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path

from orchestrator import live_base
from planners.plan_validation import validate_build_plan


class Invocations:
    def __init__(self, directory, episode_id):
        self.directory = Path(directory) / ('operation-invocations-' + hashlib.sha256(episode_id.encode()).hexdigest()[:20])
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, record):
        path = self.directory / (record['operation_id'] + '.json')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.directory,
                                             prefix='.' + path.name, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(record, handle, sort_keys=True, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            if hasattr(os, 'O_DIRECTORY'):
                descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def begin(self, operation, params):
        record = {'operation_id': uuid.uuid4().hex, 'operation': operation, 'params': copy.deepcopy(params),
                  'scope': {'surface': 'nauvis', 'force': 'player'}, 'submissions': [], 'finished': False}
        self.save(record)
        return record

    def submit(self, record, call, authorization, plan, *args, **kwargs):
        entry = {'authorization': copy.deepcopy(authorization), 'plan': copy.deepcopy(plan), 'dispatch': 'uncertain'}
        record['submissions'].append(entry)
        self.save(record)  # Durable before dispatch, including an interrupted call.
        result = call(authorization, plan, *args, **kwargs)
        entry['dispatch'] = 'returned'
        entry['receipt'] = str(result)
        self.save(record)
        return result

    def observe(self, client):
        for path in sorted(self.directory.glob('*.json')):
            try:
                record = json.loads(path.read_text())
                submissions = record['submissions']
                if (not record['finished'] or not submissions
                        or record.get('outcome', {}).get('code') not in {'submitted', 'prerequisite_advanced'}
                        or record['scope'] != {'surface': 'nauvis', 'force': 'player'}):
                    continue
                totals = dict.fromkeys(('required', 'installed', 'pending', 'unplaced'), 0)
                for entry in submissions:
                    if entry['dispatch'] != 'returned':
                        raise ValueError('uncertain dispatch')
                    plan = entry['plan']
                    if plan.get("surface") != "nauvis" or plan.get("force") != "player":
                        raise ValueError("scope mismatch")
                    validate_build_plan(plan)
                    actions = [a for phase in plan['phases'] for a in phase['actions']]
                    if not actions or any(a.get('action_type') not in {'place_entity', 'place_ghost', 'place_tile_ghost'} for a in actions):
                        raise ValueError('unsupported plan')
                    if any(a.get('surface', 'nauvis') != 'nauvis' or a.get('force', 'player') != 'player' for a in actions):
                        raise ValueError('scope mismatch')
                    survey = live_base.plan_construction_status(client, 'nauvis', 'player', plan)
                    for key in totals:
                        values = survey[key]
                        if not isinstance(values, dict) or any(type(n) is not int or n < 0 for n in values.values()):
                            raise ValueError('invalid survey')
                        totals[key] += sum(values.values())
                    names = set().union(*(survey[key] for key in totals))
                    if any(survey['required'].get(name, 0) != sum(
                            survey[key].get(name, 0) for key in ('installed', 'pending', 'unplaced'))
                           for name in names):
                        raise ValueError('inconsistent item survey')
                if not totals['required'] or totals['installed'] + totals['pending'] + totals['unplaced'] != totals['required']:
                    raise ValueError('incomplete survey')
                outcome = {'operation': record['operation'], 'construction': {**totals, 'missing': totals['unplaced']}}
                outcome['status'] = 'observed' if not totals['pending'] and not totals['unplaced'] else 'construction_pending'
            except Exception:  # Unreadable or unsupported evidence stays unknown.
                continue
            yield record['operation_id'], outcome
