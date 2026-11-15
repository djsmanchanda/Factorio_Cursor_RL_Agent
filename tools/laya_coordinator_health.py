# Path: tools/laya_coordinator_health.py | Purpose: Independent bounded coordinator-thread liveness evidence.
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

if __package__:
    from tools.checkpoint_catalog import atomic_json
    from tools import runner_process
else:
    from checkpoint_catalog import atomic_json
    import runner_process

NAME = 'coordinator-health.json'
SOURCE = 'laya_regression_scheduler.coordinator_health'
INTERVAL = 5.0
MAX_AGE = 30.0


@lru_cache(maxsize=1)
def source_identity():
    """Identify this frozen implementation, rather than trusting a protocol label."""
    root = Path(__file__).resolve().parent
    try:
        return {'protocol': SOURCE, 'components_sha256': {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ('laya_coordinator_health.py', 'laya_regression_scheduler.py')}}
    except OSError:
        return None


def boot_identity():
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip() or None
    except OSError:
        return None


@contextmanager
def coordinator_health(root, *, interval=INTERVAL, wall=time.time, monotonic=time.monotonic):
    pid = os.getpid()
    identity = {'version': 1, 'generation': uuid.uuid4().hex, 'boot_id': boot_identity(),
                'pid': pid, 'creation_token': runner_process._process_creation_token(pid),
                'cohort_root': str(Path(root).resolve()), 'source': source_identity()}
    stop = threading.Event()

    previous = None
    regressed = False

    def publish(state):
        nonlocal previous, regressed
        sample = (wall(), monotonic())
        if previous is not None and (sample[0] < previous[0] or sample[1] < previous[1]):
            regressed = True
        previous = sample
        atomic_json(Path(root) / NAME, {**identity, 'wall': sample[0], 'monotonic': sample[1],
                                      'clock_regressed': regressed, 'state': state})

    def pulse():
        while not stop.wait(interval):
            publish('running')

    publish('running')
    thread = threading.Thread(target=pulse, name='coordinator-health', daemon=True)
    thread.start()
    state = 'completed'
    original_error = None
    try:
        yield
    except (KeyboardInterrupt, SystemExit) as error:
        original_error = error
        state = 'interrupted'
        raise
    except BaseException as error:
        original_error = error
        state = 'error'
        raise
    finally:
        stop.set()
        thread.join()
        try:
            publish(state)
        except BaseException as error:
            if original_error is None:
                raise
            original_error.add_note('Coordinator health publication failed: ' + type(error).__name__)


def present(payload, *, root=None, wall=time.time, monotonic=time.monotonic):
    result = {'status': 'unknown'}
    try:
        expected_source = source_identity()
        if (not isinstance(payload, dict) or type(payload.get('version')) is not int or payload['version'] != 1
                or expected_source is None or payload['source'] != expected_source
                or root is None or payload.get('cohort_root') != str(Path(root).resolve())
                or payload['clock_regressed'] is not False):
            return result
        if not isinstance(payload['generation'], str) or not re.fullmatch(r'[0-9a-f]{32}', payload['generation']):
            return result
        boot = boot_identity()
        pid, token = payload['pid'], payload['creation_token']
        if (not boot or payload['boot_id'] != boot or type(pid) is not int or pid <= 0
                or type(token) is not int or token <= 0):
            return result
        if runner_process._process_creation_token(pid) != token:
            return result
        if any(type(payload[key]) not in (int, float) for key in ('wall', 'monotonic')):
            return result
        sample_wall, sample_mono = payload['wall'], payload['monotonic']
        age_wall, age_mono = wall() - sample_wall, monotonic() - sample_mono
        if not all(math.isfinite(x) for x in (sample_wall, sample_mono, age_wall, age_mono)):
            return result
        if min(age_wall, age_mono) < 0 or abs(age_wall - age_mono) > 5:
            return result
        state = payload['state']
        if state not in ('running', 'completed', 'interrupted', 'error'):
            return result
        result = {'status': 'stale' if state == 'running' and max(age_wall, age_mono) > MAX_AGE else state,
                  'generation': payload['generation'], 'age_seconds': age_mono}
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return result
