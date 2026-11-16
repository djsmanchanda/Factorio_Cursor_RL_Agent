# Path: orchestrator/operation_invocations.py | Purpose: Persist selected submissions and survey their exact construction read-only.
"""Invocation evidence belongs to one episode and never resubmits a plan."""
import copy
import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

from orchestrator import live_base
from planners.plan_validation import validate_build_plan


class ReadbackRequired(RuntimeError):
    """Prior dispatch provenance is unknown; mutation requires correlated readback."""


def _valid_ack(report):
    return (isinstance(report, dict)
            and set(report) == {'ok', 'tick', 'failed_placements', 'recipe_failures'}
            and type(report['ok']) is bool
            and all(type(report[key]) is int and report[key] >= 0
                    for key in ('tick', 'failed_placements', 'recipe_failures')))


def _strict_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate invocation field')
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError('nonfinite invocation field')
    return json.loads(data, object_pairs_hook=unique, parse_constant=invalid_constant)


def decode_record(data, name, episode_id):
    """Validate transferred evidence without treating an uncertain call as returned."""
    record = _strict_json(data)
    if name == 'legacy-unknown.json':
        if record != {'provenance': 'unknown'}:
            raise ValueError('invalid legacy provenance marker')
        return record
    if (not re.fullmatch(r'[0-9a-f]{32}\.json', name)
            or not isinstance(record, dict) or record.get('operation_id') != name[:-5]
            or not isinstance(record.get('operation'), str) or not isinstance(record.get('params'), dict)
            or record.get('scope') != {'surface': 'nauvis', 'force': 'player'}
            or type(record.get('finished')) is not bool or not isinstance(record.get('submissions'), list)):
        raise ValueError('malformed invocation')
    # Earlier local journals were scoped by their episode directory only.
    if ('episode_id' in record or 'version' in record) and (
            type(record.get('version')) is not int or record['version'] != 1
            or record.get('episode_id') != episode_id):
        raise ValueError('invocation episode mismatch')
    for entry in record['submissions']:
        if (not isinstance(entry, dict) or not isinstance(entry.get('authorization'), dict)
                or not isinstance(entry.get('plan'), dict) or entry.get('dispatch') not in ('uncertain', 'returned')
                or (entry['dispatch'] == 'returned' and not isinstance(entry.get('receipt'), str))):
            raise ValueError('malformed submission')
        if entry.get('report') is not None and not _valid_ack(entry['report']):
            raise ValueError('malformed submission acknowledgement')
        if entry['plan'].get('surface') != 'nauvis' or entry['plan'].get('force') != 'player':
            raise ValueError('invalid submission scope')
        validate_build_plan(entry['plan'])
        if any(action.get('surface', 'nauvis') != 'nauvis' or action.get('force', 'player') != 'player'
               for phase in entry['plan']['phases'] for action in phase['actions']):
            raise ValueError('invalid action scope')
    return record


class Invocations:
    def __init__(self, directory, episode_id):
        self.episode_id = episode_id
        self.directory = Path(directory) / ('operation-invocations-' + hashlib.sha256(episode_id.encode()).hexdigest()[:20])
        self._check_directory()
        parents = []
        parent = self.directory
        while not parent.exists():
            parent = parent.parent
            parents.append(parent)
        self.directory.mkdir(parents=True, exist_ok=True)
        for parent in parents:
            descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _check_directory(self):
        if any(path.is_symlink() for path in (self.directory, *self.directory.parents)):
            raise ReadbackRequired('Invocation directory crosses a symlink')

    def save(self, record):
        self._check_directory()
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
        self.require_clear()
        record = {'version': 1, 'episode_id': self.episode_id,
                  'operation_id': uuid.uuid4().hex, 'operation': operation, 'params': copy.deepcopy(params),
                  'scope': {'surface': 'nauvis', 'force': 'player'}, 'submissions': [], 'finished': False}
        self.save(record)
        return record

    def require_clear(self):
        try:
            self._check_directory()
            for path in self.directory.iterdir():
                if path.is_symlink() or not path.is_file():
                    raise ValueError("unknown invocation provenance")
                record = decode_record(path.read_bytes(), path.name, self.episode_id)
                if path.name == 'legacy-unknown.json' or any(entry['dispatch'] != 'returned' for entry in record['submissions']):
                    raise ValueError('unresolved submission provenance')
        except (OSError, ValueError, TypeError) as error:
            raise ReadbackRequired("Invocation readback required; prior dispatch is unknown") from error

    def submit(self, record, call, authorization, plan, *args, **kwargs):
        self.require_clear()
        entry = {'authorization': copy.deepcopy(authorization), 'plan': copy.deepcopy(plan), 'dispatch': 'uncertain'}
        record['submissions'].append(entry)
        self.save(record)  # Durable before dispatch, including an interrupted call.
        result = call(authorization, plan, *args, **kwargs)
        entry['dispatch'] = 'returned'
        entry['receipt'] = str(result)
        entry['report'] = self._report(entry['receipt'])
        self.save(record)
        return result

    def _report(self, receipt):
        """Retain the returned acknowledgement across checkpoint relocation."""
        try:
            path = Path(receipt)
            if path.is_symlink() or not path.resolve().is_relative_to(self.directory.parent.resolve()):
                return None
            report = _strict_json(path.read_text())
            if isinstance(report, dict):
                compact = {key: report.get(key) for key in
                           ('ok', 'tick', 'failed_placements', 'recipe_failures')}
                if _valid_ack(compact):
                    return compact
        except (OSError, ValueError, TypeError):
            pass
        return None

    def returned_plans(self):
        """Selected evidence, including construction waits; this never clears a fence."""
        self.require_clear()
        for path in sorted(self.directory.glob('*.json')):
            record = decode_record(path.read_bytes(), path.name, self.episode_id)
            for index, entry in enumerate(record['submissions']):
                if entry['dispatch'] == 'returned':
                    yield {"operation_id": record['operation_id'], "submission": index,
                           "plan": copy.deepcopy(entry['plan']),
                           "report": copy.deepcopy(entry.get('report') or self._report(entry['receipt']))}

    def observe(self, client):
        for path in sorted(self.directory.glob('*.json')):
            try:
                record = decode_record(path.read_bytes(), path.name, self.episode_id)
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
