# Path: tools/laya_regression_scheduler.py | Purpose: Durable, host-admitted full and checkpoint restart regression jobs.
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import signal
import subprocess
import time
import uuid
import re
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from tools.checkpoint_catalog import atomic_json
from tools.deterministic_fleet_runtime import create_detached_checkout
from training.laya_restart_checkpoint import load as load_checkpoint

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
TERMINAL = {"completed", "failed", "timeout", "cancelled", "infrastructure_failure", "incompatible"}
# Live game speed is frozen per cohort. New cohorts run at 2x (120 UPS), as the
# batch cohorts through C48 did; roots frozen before the field existed ran at
# the runner's 1x default and keep it on resume, so no live cohort changes speed.
DEFAULT_GAME_SPEED = 2.0
LEGACY_GAME_SPEED = 1.0


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
    name = spec['name']
    if not name or Path(name).name != name or name.startswith(('.', '_')):
        raise ValueError('Invalid suite name')
    setup = spec.get('purpose') == 'disposable_source_setup'
    if len(spec['arms']) != (1 if setup else 2) or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', arm) for arm in spec['arms']):
        raise ValueError('Safe arm labels and one setup arm or two measurement arms required')
    if (not spec['seeds'] or any(type(s) is not int or s < 0 for s in spec['seeds'])
            or len(set(spec['seeds'])) != len(spec['seeds'])):
        raise ValueError('Distinct nonnegative seeds required')
    if spec['max_game_ticks'] <= 0 or spec['trial_wall_seconds'] <= 0:
        raise ValueError('Positive tick and wall budgets required')
    speed = spec.setdefault('game_speed', DEFAULT_GAME_SPEED)
    if type(speed) not in (int, float) or not .25 <= speed <= 8:
        raise ValueError('Game speed must be 0.25..8')
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
    mixed = spec.get('design') == 'mixed-start-v1'
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
                raise ValueError('Both frozen arms require the restart benchmark interface')
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
            freeze['arms'].append({'id': arm, 'revision': revision, 'code_sha256': tracked_hash(code),
                                   'predicate_sha256': current, 'common_interface':interface})
        freeze['source_sha256'] = sha(spec['source_save'])
        if mixed and len({a['code_sha256'] for a in freeze['arms']}) != 1:
            raise ValueError('A/A calibration requires identical code, policy and interface')
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
            if row.get('event') == 'strict_current':
                observations.append(row)
            elif row.get('event') == 'summary':
                summary = row
            elif row.get('event') == 'research_acceptance':
                research = row
            elif row.get('event') == 'acceptance':
                statuses = row.get('statuses') or {}
                final = None if 'error' in statuses else [k for k,v in statuses.items() if v == 'passed']
                for key in final or []:
                    latches.setdefault(key, row.get('tick'))
    outcomes = []
    records = world/'evidence/decision-states.jsonl'
    if records.exists():
        for line in records.read_text().splitlines():
            try:
                outcomes.append(json.loads(line).get('outcome') or {})
            except ValueError:
                errors.append('malformed_decision_journal')
    loaded = summary.get('loaded_tick')
    if type(loaded) is not int:
        loaded = next((r['loaded_tick'] for r in observations if type(r.get('loaded_tick')) is int), None)
    reached, latency = {}, {}
    for row in observations:
        if type(loaded) is not int or type(row.get('tick')) is not int or row['tick'] <= loaded:
            continue
        for stage, passed in row.get('statuses', {}).items():
            if passed is True and stage not in reached:
                reached[stage] = row['tick']
                latency[stage] = {'ticks':row['tick']-loaded, 'time_seconds':row.get('elapsed_seconds'),
                                  'decisions':row.get('decisions')}
    target = job.get('strict_target')
    passed = True if target in reached else False if target and observations else None
    faults = summary.get('faults') or dict(__import__('collections').Counter(
        str(r.get('code') or 'tool_error') for r in outcomes if not r.get('ok')))
    relevant = [stage for stage in reached if stage > job.get('origin', job.get('checkpoint', 'C0'))]
    first = min(relevant, key=lambda stage: reached[stage]) if relevant else None
    return {'faults':faults, 'accepted_ever':sorted(latches), 'accepted_final':final,
            'legacy_latch_reached':latches, 'strict_reached':reached, 'loaded_tick':loaded,
            'strict_current':observations, 'strict_current_at_end':observations[-1]['statuses'] if observations else None,
            'stage_latency':latency, 'progress_latency':latency.get(first, {}),
            'runner_stop':summary.get('stop'), 'stop_classification':summary.get('stop_classification', 'unknown'),
            'research_acceptance':summary.get('research_acceptance') or research,
            'objective_version':summary.get('objective_version'),
            'decisions':summary.get('decisions'), 'failures':summary.get('failures'),
            'evidence_errors':errors, 'successor_passed':passed,
            'segment_results_are_conditional':job.get('bundle') is not None,
            'exact_continuation':False, 'full_run_acceptance_gate':'not_evaluated'}


def _publish(root, mutate):
    with lock(root/'status.lock'):
        status = read(root/'job-status.json')
        value = mutate(status)
        status['updated'] = time.time()
        atomic_json(root/'job-status.json', status)
        return value


def _update_job(root, identity, values):
    def change(status):
        next(j for j in status['jobs'] if j['id'] == identity).update(values)
    _publish(root, change)


def recover(root):
    spec = read(root/'regression.json')
    for job in read(root/'job-status.json')['jobs']:
        if job.get('admission_reserved'):
            scoped_stop(root/'arms'/job['arm']/'code', Path(job['world']), spec['python'],
                        Path(spec['factorio_binary']).resolve().parents[2])
            updates = {'admission_reserved':False, 'cleanup_completed':True,
                       'runner_exited':job.get('runner_exited') or time.time()}
            # A terminal result is published before cleanup; a coordinator
            # interruption in that interval must not erase the result.
            if job.get('status') not in TERMINAL:
                updates.update(status='infrastructure_failure', termination_cause='coordinator_interrupted_attempt')
            _update_job(root, job['id'], updates)


def _dispatch(root, executor):
    spec, freeze = read(root/'regression.json'), read(root/'freeze.json')
    if sha(root/'regression.json') != freeze.get('spec_sha256') or freeze['state'] != 'ready':
        raise ValueError('Suite is not ready or frozen specification changed')
    if not predecessors_done(root.parent, spec['after']):
        return 'waiting_for_predecessors'
    def reserve(status):
        if time.time() >= datetime.fromisoformat(spec['until']).timestamp():
            for item in status['jobs']:
                if item['status'] == 'queued':
                    item.update(status='cancelled', termination_cause='campaign_deadline_unstarted')
            return 'deadline'
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
    with lock(root.parent/'.host-admission.lock'):
        job = _publish(root, reserve)
    if isinstance(job, str):
        return job
    code = root/'arms'/job['arm']/'code'
    world = Path(job['world'])
    identity = next(a for a in freeze['arms'] if a['id'] == job['arm'])
    deadline = time.monotonic()+min(spec['trial_wall_seconds'], datetime.fromisoformat(spec['until']).timestamp()-time.time())
    result = {}
    try:
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
            base = [spec['python'], str(code/spec.get('runner', 'tools/run_laya_clean.py'))]
            def execute(arguments):
                return executor(base+arguments, cwd=code, check=True, stdout=log, stderr=subprocess.STDOUT,
                                env={**os.environ, 'LAYA_CHECKPOINT_RUNTIME_ROOT':str(Path(spec['factorio_binary']).resolve().parents[2])},
                                timeout=max(.1, deadline-time.monotonic()))
            restart = ['--restart-checkpoint', job['bundle']] if job['bundle'] else []
            common = ['--root',str(world),'--actions','deterministic']
            execute(['prepare',*common,'--game-port',str(job['game_port']), '--rcon-port',str(job['rcon_port']),
                     '--mods-source',spec['mods_source'],'--source-save',spec['source_save'],*restart])
            verify_engine(world, freeze['engine_sha256'])
            _update_job(root, job['id'], {'status':'running','started':time.time()})
            run = ['run',*common,'--no-helper','--seed',str(job['seed']),
                   '--max-game-ticks',str(spec['max_game_ticks']),
                   '--speed',f"{float(spec.get('game_speed', LEGACY_GAME_SPEED)):g}",*restart]
            if job.get('strict_target'):
                run += ['--strict-target',job['strict_target']]
            if spec.get('research_target'):
                run += ['--research-target', spec['research_target']]
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
    finally:
        # Publish terminal evidence before cleanup; retain the reservation if
        # physical cleanup fails, so the host cannot over-admit.
        _update_job(root, job['id'], result)
        scoped_stop(code, world, spec['python'], Path(spec['factorio_binary']).resolve().parents[2])
        _update_job(root, job['id'], {'admission_reserved':False,'cleanup_completed':True,
            'runner_exited':time.time(), 'server_seconds':time.time()-job['preparation_started']})
    return result['status']


def run_one(root, *, executor=subprocess.run):
    root = root.resolve()
    with lock(root/'coordinator.lock'):
        recover(root)
        return _dispatch(root, executor)


def run_campaign(root, *, workers=MAX_MIXED_WORKERS, executor=subprocess.run):
    """Interleave the frozen plan with concurrent, host-admitted attempts."""
    root = root.resolve()
    with lock(root/'coordinator.lock'):
        recover(root)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            active = set()
            while True:
                status = read(root/'job-status.json')
                queued = any(j['status']=='queued' for j in status['jobs'])
                if not active and not queued:
                    return 'complete'
                if len(active) < workers and queued:
                    active.add(pool.submit(_dispatch, root, executor))
                done, active = wait(active, timeout=1, return_when=FIRST_COMPLETED)
                idle = False
                for future in done:
                    outcome = future.result()
                    idle |= outcome.startswith('waiting') or outcome == 'ports_unavailable'
                if idle:
                    time.sleep(10)
