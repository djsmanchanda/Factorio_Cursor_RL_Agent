# Path: tools/laya_matrix_campaign.py | Purpose: Run frozen factorial Laya cohorts with conservative admission, phase budgets and complete evidence.
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time

from tools.rcon_client import RconClient

TERMINAL_ERRORS = {'runner_error', 'prepare_failed', 'harness_error'}


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


def lines(path, offset=0):
    """Read complete append records without truncating large JSON rows."""
    try:
        with Path(path).open('rb') as stream:
            stream.seek(offset)
            data = stream.read()
    except FileNotFoundError:
        return [], offset
    end = data.rfind(b'\n') + 1
    return [json.loads(line) for line in data[:end].splitlines() if line.strip()], offset + end


def percentile(values, fraction=.95):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def gate(worlds, baselines, min_samples=100):
    """Exact approved sample fractions; no missing world or baseline can pass."""
    details = {}
    for name, samples in worlds.items():
        ups, decisions = samples['ups'], samples['decisions']
        baseline = baselines.get(samples['code_arm'])
        p95 = percentile(decisions)
        row = {'ups_samples': len(ups), 'controller_samples': len(decisions), 'controller_p95_s': p95,
               'fraction_ge_100': sum(u >= 100 for u in ups) / len(ups) if ups else 0,
               'fraction_ge_110': sum(u >= 110 for u in ups) / len(ups) if ups else 0}
        # External worlds count for UPS, but their uninstrumented controller is
        # outside our latency cohort. They are never signaled by this controller.
        latency = samples.get('external', False) or (baseline is not None and p95 is not None
                    and len(decisions) >= 3 and p95 <= 5 and p95 <= baseline * 2)
        row['passed'] = (len(ups) >= min_samples and row['fraction_ge_100'] >= .98
                         and row['fraction_ge_110'] >= .95 and latency)
        details[name] = row
    return bool(details) and all(r['passed'] for r in details.values()), details


def owned_server(world):
    """Return a PID only when both private config/save arguments match."""
    world = Path(world)
    try:
        pid = int((world / 'server/factorio.pid').read_text())
        args = Path(f'/proc/{pid}/cmdline').read_bytes().decode().split('\0')
        for flag, expected in [('--config', world / 'server/config.ini'),
                               ('--start-server', world / 'server/saves/mod_playground.zip')]:
            if args[args.index(flag) + 1] != str(expected):
                return None
        return pid
    except (OSError, ValueError, IndexError):
        return None


def summarize(world):
    world = Path(world)
    rows, _ = lines(world / 'evidence/clean-decisions.jsonl')
    accepted, current, packets, first_times = set(), {}, {}, {}
    for r in rows:
        if r.get('event') != 'acceptance':
            continue
        current = r.get('statuses', {})
        reference = r.get('details_path')
        if not reference:
            continue
        packet = json.loads(Path(reference).read_text())
        for key, result in packet['results'].items():
            # A monotonic latch is not the original predicate evidence.
            if result['status'] == 'passed' and not result['evidence'].get('monotonic_reached'):
                accepted.add(key)
                if key not in packets:
                    packets[key] = reference
                    first_times[key] = r.get('wall_epoch')
        current = {k: v['status'] for k, v in packet.get('current', {}).items()}
    states, _ = lines(world / 'evidence/decision-states.jsonl')
    codes = Counter((r.get('outcome') or {}).get('code') for r in states)
    phase_ids = [r['phase'] for r in rows if r.get('event') == 'phase_reached']
    errors = []
    for r in states:
        outcome = r.get('outcome', {})
        if outcome.get('code') in ('tool_error', 'untyped_stuck'):
            errors.append({'index': r.get('index'), 'tick': r.get('tick'),
                           'recorded_at': r.get('recorded_at'), 'outcome': outcome})
    return {'decisions': sum(r.get('event') == 'decision' for r in rows),
            'accepted_verified': sorted(accepted), 'current_acceptance': current,
            'acceptance_packets': packets, 'first_acceptance_epoch': first_times,
            'proxy_phases': phase_ids, 'outcome_codes': {k: v for k, v in codes.items() if k},
            'action_errors': errors, 'training_record_path': str(world / 'evidence/decision-states.jsonl'),
            'choice_points': sum(len(r.get('options', [])) > 1 for r in states)}


def memory_available():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) * 1024
    return 0


class Campaign:
    def __init__(self, manifest):
        self.m = manifest
        self.root = Path(manifest['root'])
        self.jobs = [dict(j, status='queued') for j in manifest['jobs']]
        self.active = {}
        self.telemetry = {}
        self.baseline_samples = defaultdict(list)
        self.baselines = {}
        self.target = 4
        self.stopping = False
        self.last_gate_epoch = 0
        self.healthy = self.unhealthy = 0
        self.latest_gate = {}
        self.confirmation_selected = False
        self.done_reserves = False

    def save(self, phase):
        snapshot = [dict(j) for j in self.jobs]
        configs = {}
        for c in self.m['configurations']:
            rs = [j for j in snapshot if j['config'] == c['id'] and j['cohort'] in ('core', 'reserve')]
            configs[c['id']] = {'planned': len(rs), 'statuses': dict(Counter(j['status'] for j in rs)),
                'real_acceptance': {f'C{i}': sum(f'C{i}' in j.get('accepted_verified', []) for j in rs) for i in range(7)},
                'terminal_errors': sum(j['status'] in TERMINAL_ERRORS for j in rs),
                'decisions': sum(j.get('decisions', 0) for j in rs)}
        value = {'phase': phase, 'updated_utc': datetime.now(timezone.utc).isoformat(),
                 'start_epoch': self.m['start_epoch'], 'deadline_epoch': self.m['deadline_epoch'],
                 'active': len(self.active), 'target_new_worlds': self.target,
                 'low_load_controller_p95': self.baselines, 'gate': self.latest_gate,
                 'planned': len(snapshot), 'statuses': dict(Counter(j['status'] for j in snapshot)),
                 'configurations': configs, 'memory_available': memory_available(),
                 'confirmation_selected': self.confirmation_selected}
        atomic(self.root / 'progress.json', value)
        atomic(self.root / 'job-status.json', snapshot)

    @staticmethod
    def stop_process(process, grace=20):
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        except ProcessLookupError:
            pass

    def run_job(self, job, slot):
        code, world = Path(job['code']), Path(job['world'])
        runner = [self.m['python'], str(code / 'tools/run_laya_clean.py')]
        output = self.root / 'logs' / (job['id'] + '.out')
        job['log_path'] = str(output)
        process = None
        env = dict(os.environ, PYTHONPATH=str(code), PYTHONDONTWRITEBYTECODE='1',
                   FACTORIO_RL_RUN_LEDGER=str(code / 'data/matrix-run-ledger.jsonl'))
        try:
            with output.open('a') as log:
                command = runner + ['prepare', '--root', str(world), '--source-save', self.m['source_save'],
                    '--mods-source', self.m['mods'], '--game-port', str(self.m['game_base'] + slot),
                    '--rcon-port', str(self.m['rcon_base'] + slot)]
                process = subprocess.Popen(command, cwd=code, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                prepare_end = min(time.time() + 180, self.m['trial_deadline_epoch'])
                while process.poll() is None and not self.stopping and time.time() < prepare_end:
                    time.sleep(.5)
                if process.poll() is None:
                    self.stop_process(process)
                    raise RuntimeError('preparation interrupted or timed out')
                if process.returncode:
                    raise RuntimeError(f'prepare exit {process.returncode}; see {output}')
                if self.stopping or time.time() >= self.m['trial_deadline_epoch']:
                    job['status'] = 'deadline_before_run'
                    return
                command = runner + ['run', '--root', str(world), '--seed', str(job['seed']),
                    '--variant', job['variant'], '--no-helper', '--actions', 'deterministic', '--speed', '2',
                    '--max-game-ticks', str(job['trial_seconds'] * 120), '--decisions', '0',
                    '--experiment', self.m['experiment']]
                process = subprocess.Popen(command, cwd=code, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                started = time.time()
                job.update(status='running', started_epoch=started, runner_pid=process.pid)
                end = min(started + job['trial_seconds'], self.m['trial_deadline_epoch'])
                while process.poll() is None and not self.stopping and time.time() < end:
                    time.sleep(.5)
                if process.poll() is None:
                    job['status'] = 'interrupted' if self.stopping else 'wall_timeout'
                    self.stop_process(process)
                else:
                    job['status'] = 'completed' if process.returncode == 0 else 'runner_error'
                job['returncode'] = process.returncode
                job['elapsed_s'] = time.time() - started
                job.update(summarize(world))
                # The runner's own stall/window stop remains separate from exit status.
                text = (world / 'logs/laya-runner.log').read_text()
                summary_lines = [line for line in text.splitlines() if 'SUMMARY: stop=' in line]
                if summary_lines:
                    job['runner_stop'] = summary_lines[-1].split('SUMMARY: stop=')[1].split()[0]
        except Exception as error:
            self.stop_process(process)
            job.update(status='harness_error' if 'started_epoch' in job else 'prepare_failed',
                       error=f'{type(error).__name__}: {error}')
            try:
                job.update(summarize(world))
            except Exception as evidence_error:
                job['evidence_error'] = str(evidence_error)
        finally:
            try:
                with output.open('a') as log:
                    subprocess.run(runner + ['stop', '--root', str(world)], cwd=code, env=env,
                                   stdout=log, stderr=subprocess.STDOUT, timeout=30)
            except Exception as error:
                job['cleanup_error'] = str(error)
            pid = owned_server(world)
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                    for _ in range(20):
                        if owned_server(world) != pid:
                            break
                        time.sleep(.1)
                    if owned_server(world) == pid:
                        os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            job['ended_epoch'] = time.time()

    def sample(self, job):
        key, world = job['id'], Path(job['world'])
        state = self.telemetry.setdefault(key, {'previous': None, 'ups': deque(), 'decisions': deque(), 'offset': 0})
        now = time.monotonic()
        try:
            if not owned_server(world):
                raise RuntimeError('server identity unavailable')
            manifest = json.loads((world / 'manifest.json').read_text())
            password = (world / 'server/rcon-password').read_text().strip()
            client = RconClient('127.0.0.1', manifest['rcon_port'], password, timeout=2)
            try:
                tick = int(client.command('/sc rcon.print(game.tick)').strip())
            finally:
                client.close()
            observed = time.monotonic()
            if state['previous']:
                old_time, old_tick = state['previous']
                state['ups'].append((observed, (tick - old_tick) / (observed - old_time)))
            state['previous'] = observed, tick
        except Exception as error:
            state['ups'].append((now, 0.0))
            state['previous'] = None
            state['error'] = f'{type(error).__name__}: {error}'
        if not job.get('external'):
            records, state['offset'] = lines(world / 'evidence/controller-performance.jsonl', state['offset'])
            for r in records:
                state['decisions'].append((now, r['controller_seconds']))
                if job['cohort'] == 'calibration' and job['arm'] not in self.baselines:
                    self.baseline_samples[job['arm']].append(r['controller_seconds'])
        for field in ('ups', 'decisions'):
            while state[field] and state[field][0][0] < now - 300:
                state[field].popleft()
        return key, {'code_arm': job['arm'], 'external': job.get('external', False),
                     **{field: [v for _, v in state[field]] for field in ('ups', 'decisions')}}

    def adjust(self, worlds, fully_measured, now):
        for arm, samples in self.baseline_samples.items():
            if arm not in self.baselines and len(samples) >= 30:
                self.baselines[arm] = percentile(samples)
        passed, details = gate(worlds, self.baselines)
        passed &= (fully_measured and any(not w.get('external') for w in worlds.values())
                   and all(a['id'] in self.baselines for a in self.m['arms']))
        self.latest_gate = {'passed': passed, 'all_active_measured': fully_measured, 'worlds': details}
        if now - self.last_gate_epoch < 300:
            return
        self.last_gate_epoch = now
        # Incomplete windows hold the current target; they are not overload proof.
        complete = (bool(worlds) and fully_measured and all(len(w['ups']) >= 100 for w in worlds.values())
                    and all(a['id'] in self.baselines for a in self.m['arms']))
        if not complete:
            self.healthy = self.unhealthy = 0
            return
        self.healthy = self.healthy + 1 if passed else 0
        self.unhealthy = self.unhealthy + 1 if not passed else 0
        external = sum(w.get('external', False) for w in worlds.values())
        if self.healthy >= 2 and memory_available() > 3 * 1024**3:
            total = self.target + external
            next_total = next((n for n in (12, 20, 24, 28, 30) if n > total), 30)
            self.target = max(1, next_total - external)
            self.healthy = 0
        elif self.unhealthy >= 2 or memory_available() < 2 * 1024**3:
            self.target = max(1, self.target - 4)
            self.unhealthy = 0

    def phase(self, now):
        start = self.m['start_epoch']
        if now < start + 1800 and any(j['cohort'] == 'calibration' and j['status'] == 'queued' for j in self.jobs):
            return 'calibration'
        if now < self.m['main_deadline_epoch']:
            return 'core'
        return 'confirmation'

    def make_job(self, config, seed, cohort, seconds=1800):
        arm = next(a for a in self.m['arms'] if a['id'] == config['code_arm'])
        name = f'{cohort}-{config["id"]}-s{seed}'
        return {'id': name, 'config': config['id'], 'arm': arm['id'], 'epsilon': config['epsilon'], 'seed': seed,
                'cohort': cohort, 'trial_seconds': seconds, 'code': arm['code'], 'variant': config['variant'],
                'world': str(Path(arm['code']) / 'data/laya-saved-world' / self.m['experiment'] / name), 'status': 'queued'}

    def reserves(self, now):
        if self.done_reserves or any(j['status'] in ('queued', 'preparing', 'running') and j['cohort'] in ('core', 'reserve') for j in self.jobs):
            return
        # Reserve admission depends only on remaining capacity/time, never scores.
        admitted = {j['seed'] for j in self.jobs if j['cohort'] == 'reserve'}
        seed = next((s for s in self.m['reserve_seeds'] if s not in admitted), None)
        required = math.ceil(24 / max(1, self.target)) * 1980 + 120
        if seed is None or self.m['main_deadline_epoch'] - now < required:
            self.done_reserves = True
            return
        self.jobs.extend(self.make_job(c, seed, 'reserve') for c in self.m['configurations'])

    def select_confirmation(self):
        if self.confirmation_selected:
            return
        self.confirmation_selected = True
        population = [j for j in self.jobs if j['cohort'] in ('core', 'reserve')]
        matched = {s for s in {j['seed'] for j in population}
                   if len([j for j in population if j['seed'] == s and j['status'] in
                           ('completed', 'runner_error', 'wall_timeout', 'prepare_failed', 'harness_error')])
                   == len(self.m['configurations'])}
        scored = []
        for c in self.m['configurations']:
            rs = [j for j in population if j['config'] == c['id'] and j['seed'] in matched]
            score = (sum('C2' in j.get('accepted_verified', []) for j in rs),
                     sum('C1' in j.get('accepted_verified', []) for j in rs),
                     -sum(j['status'] in TERMINAL_ERRORS for j in rs))
            scored.append((score, c))
        scored.sort(key=lambda item: (item[0], -abs(item[1]['epsilon'] - .15)), reverse=True)
        best = scored[0][1]
        ids = [a['id'] for a in self.m['arms']]
        i = ids.index(best['code_arm'])
        neighbors = {ids[i], ids[i - 1 if i else 1]}
        epsilon = best['epsilon'] if best['epsilon'] != .15 else next(c['epsilon'] for _, c in scored if c['epsilon'] != .15)
        selected = [c for c in self.m['configurations'] if c['code_arm'] in neighbors and c['epsilon'] in (.15, epsilon)]
        self.jobs.extend(self.make_job(c, seed, 'confirmation') for seed in (86001, 86002, 86003) for c in selected)
        atomic(self.root / 'confirmation-selection.json', {'configs': selected, 'matched_development_seeds': sorted(matched),
               'inconclusive': not bool(matched),
               'rule': 'C2, C1, fewer terminal errors on complete matched seed blocks; adjacent geometry and distinct epsilon; development selection only'})

    def final_report(self):
        self.save('COMPLETE' if not self.stopping else 'INTERRUPTED')
        records = [{'job': j['id'], 'cohort': j['cohort'], 'records': j.get('training_record_path'),
                    'choice_points': j.get('choice_points', 0), 'errors_quarantined': len(j.get('action_errors', [])),
                    'counterfactual_labels_ready': False} for j in self.jobs]
        atomic(self.root / 'decision-data-manifest.json', records)
        progress = json.loads((self.root / 'progress.json').read_text())
        text = ['# Laya matrix result', '', f'Planned/admitted jobs: {len(self.jobs)}.',
                f'Statuses: {progress["statuses"]}', '',
                '| Configuration | Jobs | Verified C1 | Verified C2 | Runner/harness errors |', '|---|---:|---:|---:|---:|']
        for name, r in progress['configurations'].items():
            text.append(f'| {name} | {r["planned"]} | {r["real_acceptance"]["C1"]} | {r["real_acceptance"]["C2"]} | {r["terminal_errors"]} |')
        text.extend(['', 'Calibration and confirmation remain separate cohorts in job-status.json.',
                     'No adoption or neural weight update. Unstarted/censored jobs remain explicit. Full timings, predicate packets and errors are in job-status.json.'])
        (self.root / 'report.md').write_text('\n'.join(text) + '\n')

    def run(self):
        (self.root / 'logs').mkdir(parents=True, exist_ok=True)
        slots = deque(range(30))
        self.save('STARTING')
        with ThreadPoolExecutor(30) as pool, ThreadPoolExecutor(32) as probes:
            try:
                while not self.stopping and time.time() < self.m['trial_deadline_epoch']:
                    now = time.time()
                    for future in list(self.active):
                        if future.done():
                            future.result()
                            _, slot = self.active.pop(future)
                            slots.append(slot)
                    if sum(j['status'] == 'prepare_failed' for j in self.jobs) >= 2:
                        # Repeating a broken setup is not policy evaluation.
                        self.stopping = True
                        atomic(self.root / 'setup-failure.json', {'reason': 'two preparation failures; admissions stopped'})
                        break
                    phase = self.phase(now)
                    if phase != 'calibration':
                        for j in self.jobs:
                            if j['cohort'] == 'calibration' and j['status'] == 'queued':
                                j['status'] = 'not_started_phase_deadline'
                    if phase == 'core':
                        self.reserves(now)
                    else:
                        if phase == 'confirmation':
                            for j in self.jobs:
                                if j['cohort'] in ('core', 'reserve') and j['status'] == 'queued':
                                    j['status'] = 'not_started_phase_deadline'
                            if not any(j['cohort'] in ('core', 'reserve') for j, _ in self.active.values()):
                                self.select_confirmation()
                    cohorts = {'calibration'} if phase == 'calibration' else ({'core', 'reserve'} if phase == 'core' else {'confirmation'})
                    pending = [j for j in self.jobs if j['status'] == 'queued' and j['cohort'] in cohorts]
                    external = [dict(j, external=True) for j in self.m.get('external_worlds', []) if owned_server(j['world'])]
                    room = min(self.target, 30 - len(external))
                    if memory_available() < 2 * 1024**3:
                        room = 0
                    while pending and slots and len(self.active) < room:
                        j, slot = pending.pop(0), slots.popleft()
                        phase_end = (self.m['start_epoch'] + 1800 if j['cohort'] == 'calibration'
                                     else self.m['main_deadline_epoch'] if j['cohort'] in ('core', 'reserve')
                                     else self.m['trial_deadline_epoch'])
                        if now + j['trial_seconds'] + 180 > phase_end:
                            j['status'] = 'not_started_budget'
                            slots.append(slot)
                            continue
                        j['status'] = 'preparing'
                        self.active[pool.submit(self.run_job, j, slot)] = j, slot
                    measured = [j for j, _ in self.active.values() if j['status'] == 'running' and now - j['started_epoch'] > 45]
                    worlds = dict(probes.map(self.sample, measured + external))
                    for key, value in worlds.items():
                        with (self.root / 'performance.jsonl').open('a') as f:
                            f.write(json.dumps({'job': key, 'at': now, **{k: v for k, v in value.items() if k not in ('ups', 'decisions')},
                                                'ups': value['ups'][-1] if value['ups'] else None,
                                                'controller_p95_s': percentile(value['decisions'])}) + '\n')
                    self.adjust(worlds, len(measured) == len(self.active), now)
                    self.save(phase.upper())
                    if self.confirmation_selected and not self.active and not pending:
                        break
                    time.sleep(2)
            finally:
                self.stopping = self.stopping or time.time() >= self.m['deadline_epoch']
                for j in self.jobs:
                    if j['status'] == 'queued':
                        j['status'] = 'not_started_interrupted' if self.stopping else 'not_started_budget'
                for future in self.active:
                    future.result()
                self.active.clear()
                self.final_report()


def verify(manifest):
    """Refuse changed controller, code or inputs before any world is prepared."""
    for filename, expected in manifest['frozen_files'].items():
        if hashlib.sha256(Path(filename).read_bytes()).hexdigest() != expected:
            raise ValueError(f'frozen file changed: {filename}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--finalize-only', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    campaign = Campaign(manifest)
    if args.finalize_only:
        progress = Path(manifest['root']) / 'progress.json'
        if progress.exists():
            previous = json.loads(progress.read_text())
            if previous['phase'] in ('COMPLETE', 'INTERRUPTED'):
                return
            campaign.baselines = previous.get('low_load_controller_p95', {})
            campaign.latest_gate = previous.get('gate', {})
        state = Path(manifest['root']) / 'job-status.json'
        if state.exists():
            campaign.jobs = json.loads(state.read_text())
            for j in campaign.jobs:
                if j['status'] in ('queued', 'preparing', 'running'):
                    j['status'] = 'interrupted_at_service_stop'
        campaign.stopping = True
        campaign.final_report()
        return
    verify(manifest)
    signal.signal(signal.SIGTERM, lambda *_: setattr(campaign, 'stopping', True))
    signal.signal(signal.SIGINT, lambda *_: setattr(campaign, 'stopping', True))
    campaign.run()


if __name__ == '__main__':
    main()
