# Path: tools/laya_admission.py | Purpose: Drain disposable world concurrency through admission, preserving active runs.
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import tempfile
import time


def _process_stamp(pid: int) -> str | None:
    try:
        fields = (Path('/proc') / str(pid) / 'stat').read_text().rpartition(')')[2].split()
        return None if fields[0] == 'Z' else fields[19]
    except (FileNotFoundError, PermissionError, ProcessLookupError, IndexError):
        return None


def _live_worlds(scopes: list[Path]) -> set[str]:
    live = set()
    for scope in scopes:
        for root in scope.iterdir() if scope.exists() else []:
            if not root.is_dir():
                continue
            server = root / 'server'
            try:
                pid = int((server / 'factorio.pid').read_text())
                argv = (Path('/proc') / str(pid) / 'cmdline').read_bytes().decode().split('\0')
                if (argv[argv.index('--config') + 1] == str(server / 'config.ini')
                        and argv[argv.index('--start-server') + 1] == str(server / 'saves/mod_playground.zip')):
                    live.add(str(root.resolve()))
            except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError, IndexError):
                continue
    return live


def _write(path: Path, value: dict) -> None:
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as out:
        temporary = Path(out.name)
        try:
            json.dump(value, out)
            out.write('\n')
            out.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def wait_for_admission(root: Path, control: Path, *, poll_seconds: float = 2.0) -> None:
    """A fair gate for newly prepared worlds; existing servers are never signaled."""
    if not control.exists():
        return
    owner = {'root': str(root.resolve()), 'pid': os.getpid(), 'stamp': _process_stamp(os.getpid()),
             'parent_pid': os.getppid(), 'parent_stamp': _process_stamp(os.getppid())}
    if owner['stamp'] is None or owner['parent_stamp'] is None:
        raise RuntimeError('cannot identify the preparing process and its parent')
    state_path = control.with_name('admission-state.json')
    announced = False
    while True:
        if _process_stamp(owner['parent_pid']) != owner['parent_stamp']:
            raise RuntimeError('preparation parent ended while waiting for admission')
        with control.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            config = json.loads(control.read_text())
            limit = config.get('limit')
            scopes = [Path(p).resolve() for p in config.get('scopes', [])]
            if (config.get('version') != 'laya-admission-v1' or type(limit) is not int
                    or not 0 <= limit <= 40 or not scopes
                    or any(p == control.parent.resolve() or not p.is_relative_to(control.parent.resolve()) for p in scopes)):
                raise ValueError('invalid disposable-world admission control')
            if root.resolve().parent not in scopes:
                return
            state = json.loads(state_path.read_text()) if state_path.exists() else {'tickets': []}
            before = json.dumps(state, sort_keys=True)
            tickets = [t for t in state['tickets'] if _process_stamp(t['pid']) == t['stamp']
                       and _process_stamp(t['parent_pid']) == t['parent_stamp']]
            if not any(all(t[k] == owner[k] for k in owner) for t in tickets):
                tickets.append({**owner, 'status': 'waiting'})
            live = _live_worlds(scopes)
            reserved = sum(t['status'] == 'reserved' and t['root'] not in live for t in tickets)
            first = next((t for t in tickets if t['status'] == 'waiting'), None)
            mine = first is not None and all(first[k] == owner[k] for k in owner)
            admitted = mine and len(live) + reserved < limit
            if admitted:
                first['status'] = 'reserved'
            state['tickets'] = tickets
            if json.dumps(state, sort_keys=True) != before:
                _write(state_path, state)
            if admitted:
                return
            if not announced:
                print(f'ADMISSION WAIT: {len(live)} active worlds, limit {limit}; queued preparation retained', flush=True)
                announced = True
        time.sleep(poll_seconds)
