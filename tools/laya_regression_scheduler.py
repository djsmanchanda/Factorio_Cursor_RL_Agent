# Path: tools/laya_regression_scheduler.py | Purpose: Durable, host-admitted full and checkpoint restart regression jobs.
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import signal
import subprocess
import sys
import time
import uuid
import re
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from tools.checkpoint_catalog import atomic_json
from tools import laya_status_journal, runner_process, laya_experiment_contract, laya_coordinator_health
from tools import laya_manipulation_check
from tools.deterministic_fleet_runtime import create_detached_checkout
from training.laya_restart_checkpoint import load as load_checkpoint
from training.laya_mission_contract import mission_settings, frozen_capability
from training import laya_milestone_report
from training.laya_operation_outcome import read_records, summarize_records, planned_job_accounting

# 2026-10-30 latest user directives (supersede the same-day 3-day scale
# request): mixed cohorts are 120-150 jobs — the cohort owner picks the exact
# count — and the temporary -25% concurrency cut is discarded, so per-cohort
# workers go back to the pre-cut nominal 30 (the batch config C43-C46c ran
# with; host admission still gates at the ~20-23 slot plateau those cohorts
# actually reached). C47's frozen 200-job/15-worker plan is untouched and
# runs to completion; these bounds apply to the queued successor and later.
MIN_MIXED_JOBS = 120
MAX_MIXED_JOBS = 150
MAX_MIXED_WORKERS = 30
MAX_MEASUREMENT_ARMS = 6
# cancelled_before_start is written by close-out/refreeze paths; without it a
# successor's after= gate treats a closed cohort as still running.
TERMINAL = {"completed", "failed", "timeout", "cancelled", "cancelled_before_start",
            "infrastructure_failure", "incompatible"}
#: strict_current is a convenience copy of the journal; only the last row is
#: needed for strict_current_at_end and strict_reached is already computed.
#: Keeping every row made one cohort's job-status.json 55 MB.
STRICT_CURRENT_KEEP = 20
# Live game speed is frozen per cohort. New cohorts run at 2x (120 UPS), as the
# batch cohorts through C48 did; roots frozen before the field existed ran at
# the runner's 1x default and keep it on resume, so no live cohort changes speed.
DEFAULT_GAME_SPEED = 2.0
LEGACY_GAME_SPEED = 1.0
ACTION_PATH = 'deterministic'


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


@contextmanager
def lock(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open('a') as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        yield


def tracked_hash(code):
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=code).decode().split('\0')
    names += subprocess.check_output(['git','ls-files','--others','--exclude-standard','-z'],cwd=code).decode().split('\0')
    result = hashlib.sha256()
    for name in sorted(set(filter(None, names))):
        path = code / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Frozen source must contain regular tracked files')
        result.update(name.encode() + b'\0' + sha(path).encode() + b'\n')
    return result.hexdigest()


def tree_hash(root):
    result = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Frozen mod input cannot contain symlinks')
        if path.is_file():
            result.update(path.relative_to(root).as_posix().encode()+b'\0'+sha(path).encode())
    return result.hexdigest()


def may_admit(registry, now, *, available_gb=None):
    """Caller must hold the same admission lock as the frozen cohort runners."""
    try:
        gate = read(registry / '_host-scheduler-gate/job-status.json')
        cap = gate['controller']['slots']
        if type(cap) is not int or cap < 0 or not 0 <= now - gate['updated'] <= 180:
            return False
        occupied = 0
        for path in registry.glob('*/job-status.json'):
            if path.parent.name.startswith('_host-scheduler'):
                continue
            data = read(path)
            jobs = data if isinstance(data,list) else data.get('jobs', [])
            occupied += sum(bool(j.get('admission_reserved')) or j.get('status') in {'preparing', 'running'}
                            for j in jobs)
        if available_gb is None:
            for line in Path('/proc/meminfo').read_text().splitlines():
                if line.startswith('MemAvailable:'):
                    available_gb = int(line.split()[1]) / 1048576
        return occupied < cap and available_gb is not None and available_gb >= 4.5
    except (OSError, ValueError, KeyError, TypeError):
        return False


def predecessors_done(registry, names):
    for name in names:
        try:
            data = read(registry / name / 'job-status.json')
            jobs = data['jobs']
            if not jobs or any(j.get('status') not in TERMINAL or j.get('admission_reserved') for j in jobs):
                return False
            freeze_path = registry / name / 'freeze.json'
            if freeze_path.exists():
                freeze = read(freeze_path)
                plan = read(registry/name/'planned-jobs.json')['jobs']
                if (len(jobs) != freeze['planned_jobs'] or len({j['id'] for j in jobs}) != len(jobs)
                        or {j['id'] for j in jobs} != {j['id'] for j in plan}
                        or not all(j.get('cleanup_completed') for j in jobs)):
                    return False
                unit = freeze.get('unit', name+'.service')
                active = subprocess.check_output(['systemctl','--user','show',unit,'-p','ActiveState','--value'],text=True).strip()
                if active not in {'inactive','failed'}:
                    return False
                configs = set()
                for job in jobs:
                    world = Path(job['world'])
                    configs.add(str(world/'server/config.ini').encode())
                    manifest = read(world/'manifest.json')
                    if not ports_free(manifest['game_port'],manifest['rcon_port']):
                        return False
                for proc in Path('/proc').glob('[0-9]*'):
                    try:
                        if configs.intersection((proc/'cmdline').read_bytes().split(b'\0')):
                            return False
                    except OSError:
                        continue
        except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError):
            return False
    return True


def _milestone_versions(path):
    """Predicate version per checkpoint from an arm's MilestoneDefinition calls (AST, no import)."""
    import ast
    versions = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "MilestoneDefinition"
                and len(node.args) >= 3):
            try:
                versions[ast.literal_eval(node.args[0])] = ast.literal_eval(node.args[2])
            except Exception:
                continue
    return versions


def enqueue(repo, registry, spec):
    """Freeze immutable Git arms and every planned job before any dispatch."""
    if 'experiment_contract' in spec:
        laya_experiment_contract.validate(spec['experiment_contract'], spec, action_path=ACTION_PATH)
    name = spec['name']
    if not name or Path(name).name != name or name.startswith(('.', '_')):
        raise ValueError('Invalid suite name')
    for arm in spec.get('arms', {}):
        mission_settings(spec, arm, new=True)
    setup = spec.get('purpose') == 'disposable_source_setup'
    mixed = spec.get('design') == 'mixed-start-v1'
    # Full-origin/checkpoint cohorts may run a control plus several one-factor
    # variants on the same seeds, as the batch cohorts did (owner 2026-11-06);
    # the mixed-start A/A design stays two arms.
    arm_counts = {1} if setup else {2} if mixed else set(range(2, MAX_MEASUREMENT_ARMS+1))
    if len(spec['arms']) not in arm_counts or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', arm) for arm in spec['arms']):
        raise ValueError(f'Safe arm labels and one setup arm, two mixed-start arms or 2-{MAX_MEASUREMENT_ARMS} measurement arms required')
    if (not spec['seeds'] or any(type(s) is not int or s < 0 for s in spec['seeds'])
            or len(set(spec['seeds'])) != len(spec['seeds'])):
        raise ValueError('Distinct nonnegative seeds required')
    if spec['max_game_ticks'] <= 0 or spec['trial_wall_seconds'] <= 0:
        raise ValueError('Positive tick and wall budgets required')
    if spec.get('max_workers') is not None and (
            type(spec['max_workers']) is not int or not 1 <= spec['max_workers'] <= MAX_MIXED_WORKERS):
        raise ValueError(f'max_workers must be an integer in 1..{MAX_MIXED_WORKERS}')
    speed = spec.setdefault('game_speed', DEFAULT_GAME_SPEED)
    if type(speed) not in (int, float) or not .25 <= speed <= 8:
        raise ValueError('Game speed must be 0.25..8')
    for _arm, _config in (spec.get('arms') or {}).items():
        _target = (_config or {}).get('research_target')
        if _target is not None and (
                not isinstance(_target, str)
                or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}', _target)):
            raise ValueError('Per-arm research target must be a bounded technology name')
    if spec.get('research_target') is not None and (
            not isinstance(spec['research_target'], str) or
            not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}', spec['research_target'])):
        raise ValueError('Research target must be a bounded technology name')
    if spec.get('validate_reload_only') and (spec.get('include_full', True) or not setup):
        raise ValueError('Reload-only validation is an explicit checkpoint setup job')
    selection = spec.get('capture_checkpoints')
    if selection is not None and (not setup or not spec.get('checkpoint_catalog') or
            not isinstance(selection, list) or not selection or
            any(not isinstance(value, str) or value not in {f'C{i}' for i in range(1, 7)} for value in selection) or
            len(set(selection)) != len(selection)):
        raise ValueError('Capture selection requires distinct C1-C6 setup checkpoints and a catalog')
    if datetime.fromisoformat(spec['until']).timestamp() <= time.time():
        raise ValueError('A future campaign deadline is required')
    for predecessor in spec['after']:
        if Path(predecessor).name != predecessor or predecessor.startswith('.'):
            raise ValueError('Invalid predecessor')
    if mixed:
        from tools.laya_mixed_design import build_jobs
        from tools.laya_checkpoint_bank import validated_receipts
        origins = spec['origins']
        for origin, source in origins.items():
            if source.get('unavailable'):
                raise ValueError(f'C47 blocked: {origin} unavailable; no redistribution allowed')
            if origin == 'C0':
                continue
            bundle = Path(source['bundle'])
            meta, _ = load_checkpoint(bundle)
            if meta['checkpoint_id'] != origin or meta['generation_id'] != source['generation_id']:
                raise ValueError('Origin/generation pairing differs')
            if not validated_receipts(Path(source['bank']), source['generation_id']):
                raise ValueError(f'{origin} has no verified disposable reload')
        plan = build_jobs(origins, arms=tuple(spec['arms']), total_runs=spec.get('total_runs', MAX_MIXED_JOBS),
                          full_origin_fraction=spec.get('full_origin_fraction', .4),
                          policy_seeds=spec['seeds'], engine=spec['factorio_version'],
                          excluded=spec.get('excluded_origins'),
                          mods=tree_hash(Path(spec['mods_source'])),
                          budget={'ticks': spec['max_game_ticks'], 'wall_seconds': spec['trial_wall_seconds']})
        jobs = plan['jobs']
        if not MIN_MIXED_JOBS <= len(jobs) <= MAX_MIXED_JOBS:
            raise ValueError(f'Mixed cohort planned {len(jobs)} jobs; user size band is {MIN_MIXED_JOBS}-{MAX_MIXED_JOBS}')
        for job in jobs:
            job['bundle'] = (job['source'].get('bundle') if job['origin'] != 'C0' else None)
            job['bundle_sha256'] = sha(Path(job['bundle'])/'checkpoint.json') if job['bundle'] else None
            job['admission_reserved'] = False
    else:
        # Explicit source/reload setup, or legacy full-origin API. Never label
        # this as the requested mixed suite or redistribute unavailable lanes.
        inputs = [(None, 'C0', None)] if spec.get('include_full', True) else []
        for value in spec.get('checkpoints', []):
            path = Path(value).resolve()
            metadata, _ = load_checkpoint(path)
            origin = metadata['checkpoint_id']
            target = f'C{int(origin[1:])+2}' if origin in {'C1','C2','C3','C4'} else None
            inputs.append((str(path), origin, target))
        if not inputs:
            raise ValueError('No explicitly selected setup/full-origin input')
        jobs = []
        for bundle, origin, target in inputs:
            for seed in spec['seeds']:
                for arm in spec['arms']:
                    jobs.append({'id': f'{origin}-{arm}-s{seed}', 'arm': arm, 'seed': seed,
                        'origin': origin, 'checkpoint': origin, 'strict_target': target,
                        'bundle': bundle, 'bundle_sha256': sha(Path(bundle)/'checkpoint.json') if bundle else None,
                        'status': 'queued', 'admission_reserved': False})
        if setup and len(jobs) > 12:
            raise ValueError('Disposable setup cap exceeded')
    assignment = spec.get('experiment_contract', {}).get('assignment')
    if assignment is not None:
        from tools.laya_experiment_assignment import assign
        jobs = assign(jobs, spec['arms'], assignment, mixed=mixed)
    checks = {arm: laya_manipulation_check.validate(raw, sum(job['arm'] == arm for job in jobs))
              for arm, raw in laya_manipulation_check.declarations(spec).items()}
    validate_port_ranges(spec, len(jobs), registry)
    root = registry / name
    root.mkdir(parents=True, exist_ok=False)
    status = {'name': name, 'updated': time.time(), 'jobs': jobs}
    atomic_json(root/'job-status.json', status)
    freeze = {'state': 'preparing', 'planned_jobs': len(jobs), 'arms': [],
              'engine': spec.get('factorio_version', '2.1.23'), 'until': spec['until'],
              'kind': 'laya-mixed-start-v1' if mixed else 'laya-source-setup-v1', 'full_run_acceptance_gate': 'not_evaluated'}
    atomic_json(root/'freeze.json', freeze)
    atomic_json(root/'regression.json', spec)
    atomic_json(root/'planned-jobs.json', {'version':1, 'jobs':jobs})
    try:
        inputs_root = root/'inputs'
        inputs_root.mkdir()
        source = Path(spec['source_save']).resolve()
        original_hash = sha(source)
        shutil.copy2(source, inputs_root/'source.zip')
        if sha(source) != original_hash or sha(inputs_root/'source.zip') != original_hash:
            raise ValueError('Source save changed during freeze')
        mods = Path(spec['mods_source']).resolve()
        original_mods = tree_hash(mods)
        shutil.copytree(mods, inputs_root/'mods', symlinks=True)
        if tree_hash(mods) != original_mods or tree_hash(inputs_root/'mods') != original_mods:
            raise ValueError('Mod inputs changed during freeze')
        for job in jobs:
            if job['bundle']:
                meta, _ = load_checkpoint(job['bundle'])
                target = inputs_root / 'checkpoints' / meta['generation_id']
                if not target.exists():
                    shutil.copytree(job['bundle'], target)
                load_checkpoint(target)
                job['bundle'] = str(target)
                job['generation_id'] = meta['generation_id']
        atomic_json(root/'job-status.json', status)
        atomic_json(root/'planned-jobs.json', {'version':1, 'jobs':jobs})
        spec = {**spec, 'source_save': str(inputs_root/'source.zip'), 'mods_source': str(inputs_root/'mods')}
        atomic_json(root/'regression.json', spec)
        freeze['spec_sha256'] = sha(root/'regression.json')
        freeze['mods_sha256'] = original_mods
        binary = Path(spec['factorio_binary'])
        freeze['engine_sha256'] = sha(binary)
        predicate = None
        for arm, config in spec['arms'].items():
            code = root/'arms'/arm/'code'
            revision = create_detached_checkout(repo, code, config['revision'])
            interface = None
            if spec.get('reference_interface'):
                from tools.laya_reference_interface import install
                interface = install(repo, code)
            required = ['training/laya_restart_checkpoint.py', 'training/laya_restart_state.py']
            if not all((code/p).is_file() for p in required):
                raise ValueError('Every frozen arm requires the restart benchmark interface')
            current = sha(code/'orchestrator/checkpoint_milestones.py')
            if predicate is not None and predicate != current:
                raise ValueError('Strict predicate source differs between arms')
            predicate = current
            arm_versions = _milestone_versions(code/'orchestrator/checkpoint_milestones.py')
            for job in jobs:
                if not job['bundle']:
                    continue
                bundle_meta, bundle_ctx = load_checkpoint(job['bundle'])
                if bundle_ctx['predicate_sha256'] == current:
                    continue
                # A newer milestone version (c6-v2) changes the whole-file hash while the origin
                # predicate is unchanged. Accept only when the origin version still matches.
                origin = bundle_meta.get('checkpoint_id')
                bundle_version = ((bundle_meta.get('predicate') or {}).get('version'))
                if not bundle_version or arm_versions.get(origin) != bundle_version:
                    raise ValueError('Checkpoint predicate source differs from frozen arm')
            if config.get('variant'):
                variant = (code/config['variant']).resolve()
                variant.relative_to(code.resolve())
                if not variant.is_file():
                    raise ValueError('Variant must be tracked inside the frozen arm')
            if arm in checks:
                laya_manipulation_check.validate_against_code(checks[arm], code)
            runner = config.get('runner') or spec.get('runner', 'tools/run_laya_clean.py')
            mission = mission_settings(spec, arm)
            capability = frozen_capability(code, runner, spec['python']) if mission['objective_mode'] != 'legacy_unspecified' else None
            mission = mission_settings(spec, arm, capability=capability)
            freeze['arms'].append({'id': arm, 'revision': revision, 'runner': runner,
                                   'mission_capability_version': 1 if capability else None,
                                   'mission_capability': capability, 'effective_mission': mission, 'code_sha256': tracked_hash(code),
                                   'predicate_sha256': current, 'common_interface':interface})
        freeze['source_sha256'] = sha(spec['source_save'])
        if mixed and len({a['code_sha256'] for a in freeze['arms']}) != 1:
            raise ValueError('A/A calibration requires identical code, policy and interface')
        if 'experiment_contract' in spec:
            binding = laya_experiment_contract.make_binding(spec['experiment_contract'], jobs, freeze['arms'])
            freeze['experiment_contract'] = laya_experiment_contract.publish(root, binding)
        freeze['state'] = 'ready'
    except Exception as error:
        freeze.update(state='incompatible', error=str(error))
        for job in jobs:
            job.update(status='incompatible', termination_cause=str(error))
        atomic_json(root/'job-status.json', status)
        raise
    finally:
        atomic_json(root/'freeze.json', freeze)
    return root


def validate_port_ranges(spec, planned_jobs, registry=None):
    """Reject impossible ranges before creating a freeze or reserving a world.

    Factorio game ports are UDP and RCON is TCP: equal port numbers across
    those protocols do not collide. Same-protocol reuse needs an explicit
    predecessor gate, or proof the old jobs have released their worlds.
    """
    ranges = {}
    for field in ('game_port_base', 'rcon_port_base'):
        base = spec.get(field)
        if type(base) is not int or not 1 <= base <= base + planned_jobs - 1 <= 65535:
            raise ValueError(f'{field} range must fit in 1..65535 for {planned_jobs} jobs')
        ranges[field] = (base, base + planned_jobs - 1)
    if registry is None:
        return
    predecessors = set()
    pending = list(spec.get('after', []))
    while pending:
        name = pending.pop()
        if name in predecessors:
            continue
        predecessors.add(name)
        path = registry/name/'regression.json'
        if path.exists():
            pending.extend(read(path).get('after', []))
    for path in registry.glob('*/regression.json'):
        if path.parent.name in predecessors:
            continue
        other = read(path)
        status_path = path.parent/'job-status.json'
        if not status_path.exists():
            continue
        # Read the potentially large status only for a numeric overlap.
        count = read(path.parent/'freeze.json')['planned_jobs']
        overlaps = [field for field, (low, high) in ranges.items()
                    if type(other.get(field)) is int
                    and low <= other[field] + count - 1 and other[field] <= high]
        if not overlaps:
            continue
        jobs = read(status_path)['jobs']
        if any(j.get('status') not in TERMINAL or j.get('admission_reserved')
               or (j.get('world') and not j.get('cleanup_completed')) for j in jobs):
            raise ValueError(f'Port range overlaps {path.parent.name}: {", ".join(overlaps)}; '
                             'use distinct ranges or an explicit predecessor gate')


def ports_free(game, rcon):
    sockets = []
    try:
        for port, kind in ((game, socket.SOCK_DGRAM), (rcon, socket.SOCK_STREAM)):
            sock = socket.socket(socket.AF_INET, kind)
            sockets.append(sock)
            sock.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False
    finally:
        for sock in sockets:
            sock.close()


def verify_engine(world, checksum):
    pid = int((world/'server/factorio.pid').read_text())
    if sha((Path('/proc')/str(pid)/'exe').resolve()) != checksum:
        raise ValueError('Prepared engine differs from frozen binary')


def scoped_stop(code, world, python, runtime_root=None):
    from tools.runner_process import running_runner_pid
    record = world/'laya-runner.pid.json'
    runner = running_runner_pid(record)
    if runner is not None:
        arguments = (Path('/proc')/str(runner)/'cmdline').read_bytes().split(b'\0')
        scripts = [str(code/'tools/run_laya_clean.py').encode(), str(code/'tools/run_laya_mixed.py').encode()]
        if str(world).encode() not in arguments or not any(script in arguments for script in scripts):
            raise ValueError('Runner identity differs; reservation retained')
        descriptor = os.pidfd_open(runner)
        try:
            if running_runner_pid(record) == runner:
                signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        finally:
            os.close(descriptor)
        deadline = time.monotonic()+30
        while running_runner_pid(record) is not None and time.monotonic() < deadline:
            time.sleep(.2)
        if running_runner_pid(record) is not None:
            raise ValueError('Runner still finishing an operation; reservation retained')
    pidfile = world/'server/factorio.pid' 
    if pidfile.exists():
        pid = int(pidfile.read_text())
        proc = Path('/proc')/str(pid)
        if proc.exists():
            args = (proc/'cmdline').read_bytes().split(b'\0')
            if str(world/'server/config.ini').encode() not in args or (proc/'exe').resolve().name != 'factorio':
                raise ValueError('Server identity cannot be established; reservation retained')
            subprocess.run([python, str(code/'tools/run_laya_clean.py'), 'stop', '--root', str(world)],
                           cwd=code, check=True, capture_output=True, timeout=90,
                           env={**os.environ, **({'LAYA_CHECKPOINT_RUNTIME_ROOT':str(runtime_root)} if runtime_root else {})})
            if proc.exists():
                raise ValueError('Server remains alive; reservation retained')

    manifest = world/'manifest.json'
    if manifest.exists():
        data = read(manifest)
        if not ports_free(data['game_port'], data['rcon_port']):
            raise ValueError('Owned ports remain occupied; reservation retained')
    # A removed PID file must not turn a still-alive owned process into a free slot.
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            if (proc/'exe').resolve().name == 'factorio' and str(world/'server/config.ini').encode() in (proc/'cmdline').read_bytes().split(b'\0'):
                raise ValueError('Owned Factorio remains alive; reservation retained')
        except (OSError, RuntimeError):
            continue


def collect_result(world, job):
    latches, final, errors, observations, summary, research = {}, None, [], [], {}, None
    journal = world/'evidence/clean-decisions.jsonl'
    if journal.exists():
        for line in journal.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                errors.append('malformed_acceptance_journal')
                continue
            if not isinstance(row, dict):
                errors.append('malformed_acceptance_journal')
                continue
            if row.get('event') == 'strict_current':
                observations.append(row)
            elif row.get('event') == 'summary':
                summary = row
            elif row.get('event') == 'research_acceptance':
                research = row
            elif row.get('event') == 'acceptance':
                statuses = row.get('statuses') or {}
                if not isinstance(statuses, dict):
                    errors.append('malformed_acceptance_statuses')
                    final = None
                    continue
                final = None if 'error' in statuses else [k for k,v in statuses.items() if v == 'passed']
                for key in final or []:
                    latches.setdefault(key, row.get('tick'))
    records = world/'evidence/decision-states.jsonl'
    rows = read_records(records)
    outcomes = [(r.get('outcome') if isinstance(r.get('outcome'), dict) else {}, r.get('typed_outcome'))
                for r in rows]
    errors.extend('malformed_decision_journal' for r in rows if r.get('evidence_error'))
    updates = []
    if journal.exists():
        updates = [r for r in read_records(journal) if r.get('event') == 'operation_observation']
    accounting = summarize_records(records, observations=updates)
    loaded = summary.get('loaded_tick')
    if type(loaded) is not int:
        loaded = next((r['loaded_tick'] for r in observations if type(r.get('loaded_tick')) is int), None)
    reached, latency = {}, {}
    for row in observations:
        if type(loaded) is not int or type(row.get('tick')) is not int or row['tick'] <= loaded:
            continue
        statuses = row.get('statuses')
        for stage, passed in (statuses.items() if isinstance(statuses, dict) else ()):
            if passed is True and stage not in reached:
                reached[stage] = row['tick']
                latency[stage] = {'ticks':row['tick']-loaded, 'time_seconds':row.get('elapsed_seconds'),
                                  'decisions':row.get('decisions')}
    target = job.get('strict_target')
    known_target = [(r.get('statuses') or {}).get(target) for r in observations
                    if isinstance(r.get('statuses'), dict) and type(r.get('tick')) is int
                    and type(loaded) is int and r['tick'] > loaded]
    legacy_reached = dict(reached)
    contracts = [(r.get('predicate_evidence') or {}).get('_contract') for r in observations
                 if isinstance(r.get('predicate_evidence'), dict)]
    contract = next((c for c in reversed(contracts) if isinstance(c, dict)), None)
    basis, eligible = 'legacy_current_pass_after_load', None
    if contract is not None:
        basis = 'observed_predicate_transitions'
        reached = {}
        for event in contract.get('observed_events') or ():
            if (isinstance(event, dict) and event.get('kind') == 'predicate_transition'
                    and isinstance(event.get('checkpoint'), str) and type(event.get('tick')) is int
                    and type(loaded) is int and event['tick'] > loaded):
                reached.setdefault(event['checkpoint'], event['tick'])
        inherited = contract.get('inherited_start')
        initial = (inherited.get('predicates') or {}).get(target) if isinstance(inherited, dict) else None
        if isinstance(initial, dict) and initial.get('status') in ('passed', 'failed'):
            eligible = initial['status'] == 'failed'
    if contract is not None:
        samples_by_tick = {r.get('tick'): r for r in observations if type(r.get('tick')) is int}
        latency = {stage: {'ticks': tick-loaded,
                          'time_seconds': samples_by_tick.get(tick, {}).get('elapsed_seconds'),
                          'decisions': samples_by_tick.get(tick, {}).get('decisions')}
                   for stage, tick in reached.items()}
    passed = (True if target in reached and eligible is not False else False if any(v is False for v in known_target)
              and (contract is None or eligible is True) else None)
    # Geometry receipts use status rather than ok. A missing field is unknown,
    # not a failed tool call; an explicitly empty summary is authoritative too.
    faults = summary.get('faults')
    if faults is None:
        faults = dict(__import__('collections').Counter(
            str(r.get('code') or ('runner_error:' + str(r.get('error_type') or 'unknown')
                                  if r.get('status') == 'failed' else 'tool_error'))
            for r, _ in outcomes if r.get('ok') is False or r.get('status') == 'failed'))
    classification = summary.get('stop_classification')
    if classification is None:
        classification = 'genuine_failure' if summary.get('stop') == 'stalled' else 'unknown'
    # One milestone block for every reader: contract evidence when present, else the same rule over polls, else the latch.
    milestones = laya_milestone_report.from_journal_rows(observations, loaded, ever=sorted(latches), final=final)
    from_contract = milestones['source'] == 'contract'
    from orchestrator.checkpoint_milestones import MILESTONE_IDS
    rank = lambda stage: MILESTONE_IDS.index(stage) if stage in MILESTONE_IDS else len(MILESTONE_IDS)
    relevant = [stage for stage in reached if rank(stage) > rank(job.get('origin', job.get('checkpoint', 'C0')))]
    versions = milestones.get('predicate_versions') or {}
    first = min(relevant, key=lambda stage: reached[stage]) if relevant else None
    return {**accounting, 'legacy_counter_meanings': {'failures':'runner caught exceptions', 'faults':'historical summary or legacy fallback'}, 'faults':faults,
            'milestones':milestones, 'accepted_ever':milestones['earned'] if from_contract else sorted(latches),
            # c5-v1 (stable window) and c5-v2 (planned rate) measure different things; readers must not pool them.
            'c5_predicate_version':versions.get('C5'), 'c6a_predicate_version':versions.get('C6a'),
            'c6_predicate_version':versions.get('C6'),
            'accepted_ever_basis':'earned_events' if from_contract else 'legacy_latch', 'accepted_final':final,
            'legacy_latch_reached':latches, 'strict_reached':reached, 'loaded_tick':loaded,
            'strict_reached_basis':basis, 'legacy_current_pass_reached':legacy_reached,
            'objective_eligible':eligible, 'milestone_evidence':contract,
            'strict_current':observations[-STRICT_CURRENT_KEEP:], 'strict_current_at_end':observations[-1].get('statuses') if observations else None,
            'stage_latency':latency, 'progress_latency':latency.get(first, {}),
            'runner_stop':summary.get('stop'), 'stop_classification':classification,
            'research_acceptance':summary.get('research_acceptance') or research,
            'objective_version':summary.get('objective_version'),
            'decisions':summary.get('decisions'), 'failures':summary.get('failures'),
            'evidence_errors':errors, 'successor_passed':passed,
            'segment_results_are_conditional':job.get('bundle') is not None,
            'exact_continuation':False, 'full_run_acceptance_gate':'not_evaluated'}


#: A dispatch that finds no host budget changes nothing, but rewriting the whole
#: job-status (which carries every job's strict history) on each poll holds the
#: global admission lock for seconds. Refresh the cohort's liveness at this
#: interval instead, so the host keeps seeing it without the write storm.
HEARTBEAT_SECONDS = 240
WAITING_OUTCOMES = frozenset({'waiting_for_host_budget', 'waiting_for_admission_interval', 'ports_unavailable', 'waiting_for_admission_lock'})


def _publish(root, mutate, min_interval=0.0):
    with lock(root/'status.lock'):
        status, seq, plan = laya_status_journal.load(root)
        projection = root/'job-status.json'
        try:
            projected = read(projection)
        except FileNotFoundError:
            projected = None
        except (OSError, ValueError) as exc:
            raise laya_status_journal.JournalUnavailable(f'status projection unavailable: {exc}') from exc
        if projected != status:
            atomic_json(projection, status)
        previous = copy.deepcopy(status)
        value = mutate(status)
        if (isinstance(value, str) and value in WAITING_OUTCOMES
                and time.time() - float(status.get('updated') or 0.0) < min_interval):
            return value
        status['updated'] = time.time()
        plan_path = root/'planned-jobs.json'
        if plan_path.is_file():
            status['accounting'] = planned_job_accounting(read(plan_path)['jobs'],
                [job for job in status['jobs'] if not job.get('legacy_status_missing')])
        laya_status_journal.append(root, previous, status, seq, plan)
        atomic_json(root/'job-status.json', status)
        return value


def _update_job(root, identity, values):
    def change(status):
        next(j for j in status['jobs'] if j['id'] == identity).update(values)
        status['progress_updated'] = time.time()
    _publish(root, change)


def record_checks(spec, status):
    """Evaluate declared manipulation checks in place; a failure here must not block publication."""
    try:
        return laya_manipulation_check.evaluate(spec, status, TERMINAL)
    except Exception as error:  # noqa: BLE001 -- the check is advisory for terminal publication and recovery
        status['manipulation_check_error'] = f'{type(error).__name__}: {error}'
        return []


def finalize_checks(root, spec):
    """Durably publish any declared verdict that is due, whether or not more admissions follow.

    Writes nothing when no verdict is due, so it is cheap to call at recovery and at campaign end.
    """
    if not laya_manipulation_check.declarations(spec):
        return []
    if not laya_manipulation_check.evaluate(spec, copy.deepcopy(read(root/'job-status.json')), TERMINAL):
        return []
    return _publish(root, lambda status: record_checks(spec, status))


def recover(root):
    spec = read(root/'regression.json')
    jobs = _publish(root, lambda status: copy.deepcopy(status['jobs']))
    for job in jobs:
        if job.get('admission_reserved'):
            def interrupted(status):
                row = next(j for j in status['jobs'] if j['id'] == job['id'])
                if row.get('status') not in TERMINAL:
                    row.update(status='infrastructure_failure', termination_cause='coordinator_interrupted_attempt',
                               cleanup_completed=False)
                if row.get('attempts'):
                    attempt = row['attempts'][-1]
                    attempt['outcome'] = attempt.get('outcome') or {'kind':'unknown', 'reason':'coordinator_interrupted'}
                    for invocation in attempt['invocations']:
                        if invocation.get('outcome') is None:
                            invocation['outcome'] = {'kind':'unknown', 'reason':'coordinator_interrupted'}
                            invocation['recovered_at'] = time.time()
            _publish(root, interrupted)
            scoped_stop(root/'arms'/job['arm']/'code', Path(job['world']), spec['python'],
                        Path(spec['factorio_binary']).resolve().parents[2])
            updates = {'admission_reserved':False, 'cleanup_completed':True,
                       'runner_exited':job.get('runner_exited') or time.time()}
            # A terminal result is published before cleanup; a coordinator
            # interruption in that interval must not erase the result.
            if job.get('status') not in TERMINAL:
                updates.update(status='infrastructure_failure', termination_cause='coordinator_interrupted_attempt')
            _update_job(root, job['id'], updates)
    finalize_checks(root, spec)


def _reserve_job(root):
    spec, freeze = read(root/'regression.json'), read(root/'freeze.json')
    if sha(root/'regression.json') != freeze.get('spec_sha256') or freeze['state'] != 'ready':
        raise ValueError('Suite is not ready or frozen specification changed')
    laya_experiment_contract.verify(root, spec, freeze, action_path=ACTION_PATH)
    if not predecessors_done(root.parent, spec['after']):
        return 'waiting_for_predecessors'
    def reserve(status):
        laya_experiment_contract.verify(root, spec, freeze, status, action_path=ACTION_PATH)
        if any(j.get('legacy_status_missing') for j in status['jobs']):
            raise laya_status_journal.JournalUnavailable('legacy job status missing; dispatch unavailable')
        if time.time() >= datetime.fromisoformat(spec['until']).timestamp():
            for item in status['jobs']:
                if item['status'] == 'queued':
                    item.update(status='cancelled', termination_cause='campaign_deadline_unstarted')
            return 'deadline'
        # Declared arms only; a new verdict is published before any further admission.
        if laya_manipulation_check.evaluate(spec, status, TERMINAL):
            return 'manipulation_check_recorded'
        job = next((j for j in status['jobs'] if j['status'] == 'queued'), None)
        if job is None:
            return 'complete'
        game, rcon = spec['game_port_base']+status['jobs'].index(job), spec['rcon_port_base']+status['jobs'].index(job)
        if not (1 <= game <= 65535 and 1 <= rcon <= 65535) or not ports_free(game, rcon):
            return 'ports_unavailable'
        if not may_admit(root.parent, time.time()):
            return 'waiting_for_host_budget'
        if time.time()-status.get('last_admit',0) < 10:
            return 'waiting_for_admission_interval'
        world = root/'arms'/job['arm']/'code/data/laya-saved-world'/root.name/job['id']
        job.update(status='preparing', world=str(world), game_port=game, rcon_port=rcon,
                   admission_reserved=True, preparation_started=time.time())
        status['last_admit'] = time.time()
        return dict(job)
    # All admission records, including this suite, use the host's real lock.
    # Do not queue a cohort's idle workers on the shared lock.
    with (root.parent/'.host-admission.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'waiting_for_admission_lock'
        job = _publish(root, reserve, min_interval=HEARTBEAT_SECONDS)
    return job if isinstance(job, str) else (spec, freeze, job)


def _dispatch(root, executor):
    reserved = _reserve_job(root)
    return reserved if isinstance(reserved, str) else _execute_job(root, executor, *reserved)


def _execute_job(root, executor, spec, freeze, job):
    code = root/'arms'/job['arm']/'code'
    world = Path(job['world'])
    identity = next(a for a in freeze['arms'] if a['id'] == job['arm'])
    deadline = time.monotonic()+min(spec['trial_wall_seconds'], datetime.fromisoformat(spec['until']).timestamp()-time.time())
    result = {}
    attempt_id = uuid.uuid4().hex
    try:
        try:
            boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip() or None
        except OSError:
            boot_id = None
        attempt = {'version':1, 'id':attempt_id, 'started':time.time(),
                   'boot_id':boot_id, 'coordinator':{'pid':os.getpid(),
                   'creation_token':runner_process._process_creation_token(os.getpid())},
                   'child_identity':None, 'runner_identity':None,
                   'frozen_identity':{'arm':identity, 'source_sha256':freeze.get('source_sha256'),
                       'mods_sha256':freeze.get('mods_sha256'), 'engine_sha256':freeze.get('engine_sha256'),
                       'spec_sha256':freeze.get('spec_sha256'), 'bundle_sha256':job.get('bundle_sha256')},
                   'mission':mission_settings(spec, job['arm']), 'invocations':[], 'outcome':None}
        def begin(status):
            row = next(j for j in status['jobs'] if j['id'] == job['id'])
            row.setdefault('attempts', []).append(attempt)
            row['cleanup_completed'] = False
        _publish(root, begin)
        if (tracked_hash(code) != identity['code_sha256'] or sha(spec['source_save']) != freeze['source_sha256']
                or tree_hash(Path(spec['mods_source'])) != freeze['mods_sha256']
                or sha(spec['factorio_binary']) != freeze['engine_sha256']):
            raise ValueError('Frozen inputs changed')
        if job['bundle']:
            load_checkpoint(job['bundle'])
            if sha(Path(job['bundle'])/'checkpoint.json') != job['bundle_sha256']:
                raise ValueError('Checkpoint manifest changed')
        (root/'logs').mkdir(exist_ok=True)
        with (root/'logs'/f'{job["id"]}.log').open('a') as log:
            # Track B arms run a different binary interface; allow one runner
            # per arm, falling back to the cohort-wide runner.
            arm_runner = ((spec.get('arms') or {}).get(job.get('arm')) or {}).get('runner')
            base = [spec['python'], str(code/(arm_runner or spec.get('runner', 'tools/run_laya_clean.py')))]
            effective = mission_settings(spec, job['arm'])
            capability = {}
            if effective['objective_mode'] != 'legacy_unspecified':
                selected_runner = arm_runner or spec.get('runner', 'tools/run_laya_clean.py')
                capability = frozen_capability(code, selected_runner, spec['python'])
                if identity.get('runner') != selected_runner or identity.get('mission_capability') != capability:
                    raise ValueError('Frozen mission capability changed')
                effective = mission_settings(spec, job['arm'], capability=capability)
            def set_mission(status):
                row = next(j for j in status['jobs'] if j['id'] == job['id'])
                next(a for a in row['attempts'] if a['id'] == attempt_id)['mission'] = effective
            _publish(root, set_mission)
            def execute(arguments):
                command = base + arguments
                receipt = {'phase':arguments[0], 'argv':command, 'cwd':str(code),
                           'started':time.time(), 'child_identity':None, 'runner_identity':None, 'outcome':None}
                def add(status):
                    row = next(j for j in status['jobs'] if j['id'] == job['id'])
                    next(a for a in row['attempts'] if a['id'] == attempt_id)['invocations'].append(receipt)
                _publish(root, add)
                try:
                    completed = executor(command, cwd=code, check=True, stdout=log, stderr=subprocess.STDOUT,
                                env={**os.environ, 'LAYA_CHECKPOINT_RUNTIME_ROOT':str(Path(spec['factorio_binary']).resolve().parents[2])},
                                timeout=max(.1, deadline-time.monotonic()))
                except BaseException as error:
                    if isinstance(error, subprocess.CalledProcessError):
                        outcome = ({'kind':'signal', 'signal':-error.returncode} if error.returncode < 0
                                   else {'kind':'exit', 'code':error.returncode})
                    elif isinstance(error, subprocess.TimeoutExpired):
                        outcome = {'kind':'timeout'}
                    elif isinstance(error, Exception):
                        outcome = {'kind':'exception', 'type':type(error).__name__}
                    else:
                        outcome = {'kind':'interruption', 'type':type(error).__name__}
                    def finish_error(status):
                        row = next(j for j in status['jobs'] if j['id'] == job['id'])
                        next(a for a in row['attempts'] if a['id'] == attempt_id)['invocations'][-1].update(
                            outcome=outcome, finished=time.time())
                    try:
                        _publish(root, finish_error)
                    except BaseException as publication_error:
                        if not isinstance(error, (KeyboardInterrupt, SystemExit)):
                            raise
                        error.add_note(f'Invocation evidence publication failed: {type(publication_error).__name__}')
                    raise
                code_returned = getattr(completed, 'returncode', None)
                if type(code_returned) is not int:
                    outcome = {'kind':'unknown', 'reason':'returncode_unavailable'}
                else:
                    outcome = ({'kind':'signal', 'signal':-code_returned} if code_returned < 0
                               else {'kind':'exit', 'code':code_returned})
                def finish(status):
                    row = next(j for j in status['jobs'] if j['id'] == job['id'])
                    next(a for a in row['attempts'] if a['id'] == attempt_id)['invocations'][-1].update(
                        outcome=outcome, finished=time.time())
                _publish(root, finish)
                if type(code_returned) is not int:
                    raise RuntimeError('Executor return code is unavailable')
                if code_returned != 0:
                    raise subprocess.CalledProcessError(code_returned, command)
                return completed
            restart = ['--restart-checkpoint', job['bundle']] if job['bundle'] else []
            common = ['--root',str(world),'--actions',ACTION_PATH]
            execute(['prepare',*common,'--game-port',str(job['game_port']), '--rcon-port',str(job['rcon_port']),
                     '--mods-source',spec['mods_source'],'--source-save',spec['source_save'],*restart])
            verify_engine(world, freeze['engine_sha256'])
            _update_job(root, job['id'], {'status':'running','started':time.time()})
            run = ['run',*common,'--no-helper','--seed',str(job['seed']),
                   '--max-game-ticks',str(spec['max_game_ticks']),
                   '--speed',f"{float(spec.get('game_speed', LEGACY_GAME_SPEED)):g}",*restart]
            if job.get('strict_target'):
                run += ['--strict-target',job['strict_target']]
            arm_research = ((spec.get('arms') or {}).get(job.get('arm')) or {}).get('research_target')
            target = (effective['research_target'] if effective['objective_mode'] != 'legacy_unspecified'
                      else arm_research or spec.get('research_target'))
            if target:
                run += ['--research-target', target]
            if spec.get('checkpoint_catalog'):
                run += ['--checkpoint-catalog',spec['checkpoint_catalog']]
            if spec.get('capture_checkpoints'):
                run += ['--capture-checkpoints', *spec['capture_checkpoints']]
            if spec.get('checkpoint_bank'):
                run += ['--checkpoint-bank',spec['checkpoint_bank']]
            if spec.get('validate_reload_only'):
                run += ['--validate-reload-only']
            variant = spec['arms'][job['arm']].get('variant')
            if variant:
                run += ['--variant',str(code/variant)]
            if effective['objective_mode'] != 'legacy_unspecified':
                run += ['--objective-mode', effective['objective_mode']]
                _update_job(root, job['id'], {'effective_mission': effective, 'run_command': base + run})
            execute(run)
            if tracked_hash(code) != identity['code_sha256']:
                raise ValueError('Frozen code changed during attempt')
            result = {'status':'completed', **collect_result(world,job)}
    except subprocess.TimeoutExpired:
        result = {'status':'timeout', **collect_result(world,job),
                  'termination_cause':'wall_budget', 'stop_classification':'wall_timeout'}
    except Exception as error:
        result = {'status':'infrastructure_failure', **collect_result(world,job),
                  'termination_cause':f'{type(error).__name__}: {error}', 'stop_classification':'infrastructure_failure'}
    except BaseException as error:
        result = {'status':'infrastructure_failure', 'termination_cause':f'interrupted: {type(error).__name__}',
                  'stop_classification':'unknown'}
        raise
    finally:
        declared = laya_manipulation_check.declarations(spec).get(job['arm'])
        if result and declared is not None:
            result['exposure'] = laya_manipulation_check.measure_job(
                world, laya_manipulation_check.validate(declared, freeze['planned_jobs']))
        # Publish terminal evidence before cleanup; retain the reservation if
        # physical cleanup fails, so the host cannot over-admit.
        def terminal(status):
            row = next(j for j in status['jobs'] if j['id'] == job['id'])
            row.update(result or {'status':'infrastructure_failure', 'termination_cause':'unknown_attempt_outcome'})
            row['cleanup_completed'] = False
            for item in row.get('attempts', []):
                if item['id'] == attempt_id:
                    item['outcome'] = {'kind':row['status'], 'termination_cause':row.get('termination_cause')}
                    item['finished'] = time.time()
            # The verdict is published with the result that completes it, so it does not wait for another admission.
            record_checks(spec, status)
        pending = sys.exception()
        try:
            _publish(root, terminal)
            scoped_stop(code, world, spec['python'], Path(spec['factorio_binary']).resolve().parents[2])
            _update_job(root, job['id'], {'admission_reserved':False,'cleanup_completed':True,
                'runner_exited':time.time(), 'server_seconds':time.time()-job['preparation_started']})
        except BaseException as cleanup_error:
            if not isinstance(pending, (KeyboardInterrupt, SystemExit)):
                raise
            pending.add_note(f'Publication or cleanup failed: {type(cleanup_error).__name__}; reservation retained if not durably released')
    return result['status']


def run_one(root, *, executor=subprocess.run):
    root = root.resolve()
    with lock(root/'coordinator.lock'), laya_coordinator_health.coordinator_health(root):
        recover(root)
        return _dispatch(root, executor)


def cohort_workers(spec, workers=MAX_MIXED_WORKERS, *, adaptive=False):
    """Retain the frozen pool limit unless host-adaptive dispatch is requested.

    Adaptive mode raises only the execution-pool ceiling. The host admission
    gate still approves each world before it receives an execution thread.
    """
    value = spec.get('max_workers')
    if value is None:
        return int(workers)
    if type(value) is not int or not 1 <= value <= int(workers):
        raise ValueError(f'max_workers must be an integer in 1..{int(workers)}')
    return int(workers) if adaptive else value


def run_campaign(root, *, workers=MAX_MIXED_WORKERS, executor=subprocess.run, adaptive=False):
    """Interleave the frozen plan with concurrent, host-admitted attempts."""
    root = root.resolve()
    with lock(root/'coordinator.lock'), laya_coordinator_health.coordinator_health(root):
        recover(root)
        spec = read(root/'regression.json')
        validate_port_ranges(spec, len(read(root/'job-status.json')['jobs']))
        workers = cohort_workers(spec, workers, adaptive=adaptive)
        if adaptive:
            atomic_json(root/'dispatch-control.json', {'mode':'host-adaptive', 'pool_max':workers,
                'frozen_max_workers':spec.get('max_workers'), 'started':time.time(),
                'coordinator_pid':os.getpid(), 'scheduler_source_sha256':sha(Path(__file__))})
        with ThreadPoolExecutor(max_workers=workers) as pool:
            active = set()
            while True:
                status = read(root/'job-status.json')
                queued = any(j['status']=='queued' for j in status['jobs'])
                if not active and not queued:
                    finalize_checks(root, spec)
                    return 'complete'
                idle = False
                if len(active) < workers and queued:
                    reserved = _reserve_job(root)
                    if isinstance(reserved, str):
                        idle = reserved.startswith('waiting') or reserved == 'ports_unavailable'
                    else:
                        active.add(pool.submit(_execute_job, root, executor, *reserved))
                done, active = wait(active, timeout=1, return_when=FIRST_COMPLETED)
                for future in done:
                    outcome = future.result()
                    idle |= outcome.startswith('waiting') or outcome == 'ports_unavailable'
                if idle:
                    time.sleep(10)
