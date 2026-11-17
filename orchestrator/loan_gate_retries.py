# Path: orchestrator/loan_gate_retries.py | Purpose: Charge bounded loan gate repairs durably before dispatch.
"""One runner owns one episode. Missing historical budgets are unknown."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

LIMIT = 3
MAX_RECORDS = 256
MAX_BYTES = 256 * 1024


def retry_path(script_output, episode_id):
    if not isinstance(episode_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', episode_id):
        raise ValueError('Gate retry storage requires an episode identity')
    return Path(script_output) / ('loan-gate-retries-' + hashlib.sha256(episode_id.encode()).hexdigest()[:20] + '.json')


def empty_state(episode_id, surface='nauvis', force='player', *, provenance='current'):
    return {'version': 1, 'scope': {'episode_id': episode_id, 'surface': surface, 'force': force},
            'provenance': provenance, 'records': {}}


def decode_state(data, episode_id, surface='nauvis', force='player'):
    if len(data) > MAX_BYTES:
        raise ValueError('Gate retry file exceeds bound')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate gate retry field')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('Nonfinite gate retry field')
    state = json.loads(data, object_pairs_hook=unique, parse_constant=invalid)
    if (not isinstance(state, dict) or set(state) != {'version', 'scope', 'provenance', 'records'}
            or type(state['version']) is not int or state['version'] != 1
            or state['scope'] != empty_state(episode_id, surface, force)['scope']
            or state['provenance'] not in ('current', 'unknown')
            or not isinstance(state['records'], dict) or len(state['records']) > MAX_RECORDS):
        raise ValueError('Gate retry version, scope or shape mismatch')
    for key, row in state['records'].items():
        if (not re.fullmatch(r'[0-9a-f]{64}', key) or not isinstance(row, dict)
                or set(row) != {'attempts', 'last_tick', 'best_crafts', 'known'}
                or type(row['known']) is not bool
                or type(row['attempts']) is not int or not 0 <= row['attempts'] <= LIMIT
                or type(row['best_crafts']) is not int or row['best_crafts'] < 0
                or (row['last_tick'] is not None and (type(row['last_tick']) is not int or row['last_tick'] < 0))
                or (row['attempts'] > 0 and row['last_tick'] is None)):
            raise ValueError('Malformed gate retry record')
    return state


def identity(loan, step):
    # Refreshing configuration changes its group baseline; it is still the same
    # obligation. Cell and loan identity prevent cross-cell budget borrowing.
    value = [loan.original_recipe, loan.target_item, loan.target_count, loan.side,
             list(loan.requester_position), step.recipe, step.target_count]
    return hashlib.sha256(json.dumps(value, allow_nan=False, separators=(',', ':')).encode()).hexdigest()


class GateRetries:
    def __init__(self, script_output, episode_id, surface='nauvis', force='player'):
        self.path = retry_path(script_output, episode_id)
        self.episode_id, self.surface, self.force = episode_id, surface, force
        self._faulted = False
        self._check_path()
        if self.path.exists():
            if self.path.stat().st_size > MAX_BYTES:
                raise ValueError('Gate retry file exceeds bound')
            self.state = decode_state(self.path.read_bytes(), episode_id, surface, force)
        else:
            self.state = empty_state(episode_id, surface, force)

    def _check_path(self):
        if any(p.is_symlink() for p in (self.path, *self.path.parents)):
            raise ValueError('Gate retry path crosses a symlink')

    def _save(self, state):
        if self._faulted:
            raise OSError('Reopen gate retry storage after failed publication')
        self._check_path()
        data = json.dumps(state, allow_nan=False, sort_keys=True).encode()
        decode_state(data, self.episode_id, self.surface, self.force)
        if len(data) > MAX_BYTES:
            raise ValueError('Gate retry file exceeds bound')
        directories = {self.path.parent}
        parent = self.path.parent
        while not parent.exists():
            parent = parent.parent
            directories.add(parent)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.gate-retry-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
                descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except BaseException:
            self._faulted = True
            raise
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.state = state

    def start(self, key, crafts, *, proposed):
        if key in self.state['records']:
            return
        state = copy.deepcopy(self.state)
        state['records'][key] = {'attempts': 0, 'last_tick': None, 'best_crafts': crafts,
                                 'known': proposed and state['provenance'] == 'current'}
        self._save(state)

    def observe(self, key, before, after, before_tick, after_tick):
        """Only monotonic observations beyond the saved high-water mark reset a budget."""
        if (any(type(x) is not int or x < 0 for x in (before, after, before_tick, after_tick))
                or after < before or after_tick <= before_tick):
            return False
        state = copy.deepcopy(self.state)
        row = state['records'].get(key)
        if row is None:
            row = state['records'][key] = {'attempts': 0, 'last_tick': None,
                                           'best_crafts': before, 'known': False}
        if before < row['best_crafts'] or (row['last_tick'] is not None and after_tick <= row['last_tick']):
            return False
        if after > row['best_crafts']:
            row.update(attempts=0, best_crafts=after, known=True)
        if state != self.state:
            self._save(state)
        return row['known']

    def charge(self, key, tick):
        if type(tick) is not int or tick < 0:
            raise ValueError('Invalid gate retry tick')
        row = self.state['records'].get(key)
        if row is None or not row['known'] or (row['last_tick'] is not None and tick <= row['last_tick']):
            return None
        if row['attempts'] >= LIMIT:
            return 0  # Caller reports the existing exhausted-budget outcome.
        state = copy.deepcopy(self.state)
        row = state['records'][key]
        row.update(attempts=row['attempts'] + 1, last_tick=tick)
        self._save(state)
        return row['attempts']
