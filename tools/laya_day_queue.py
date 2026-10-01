#!/usr/bin/env python3
# Path: tools/laya_day_queue.py | Purpose: Chain corrected-sampler lab work under one explicit deadline, without production authority.

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time


ROUND = re.compile(r'\] \S+-r(\d+): level ([123]) seeds (\d+)\+(\d+)')


def resume_position(manifest, summary):
    config = manifest['config']
    last = int(config.get('LAYA_RESUME_ROUND', 0))
    seeds = {level: int(config[f'LAYA_L{level}_SEED']) for level in (1, 2, 3)}
    for match in ROUND.finditer(summary):
        number, level, start, count = map(int, match.groups())
        last = max(last, number)
        seeds[level] = max(seeds[level], start + count)
    return last, seeds


def predecessor_ready(run, manifest, now):
    if (run / 'pause.json').exists() or (run / 'stop-requested').exists():
        return False
    text = (run / 'summary.log').read_text() if (run / 'summary.log').exists() else ''
    if 'done; training workers stopped' in text:
        return True
    # A planned host cutoff can leave a partial seed block; its recorded labels
    # can be recovered. An unexplained early stop does not authorize a restart.
    return now >= dt.datetime.fromisoformat(manifest['deadline_utc']).timestamp()


class Queue:
    def __init__(self, repo, code, run, parent, deadline):
        self.repo, self.code, self.run, self.parent = repo, code, run, parent
        self.deadline = deadline.timestamp()
        self.manifest = json.loads((parent / 'experiment.json').read_text())
        self.workers = [int(n) for n in self.manifest['config']['LAYA_INSTANCES'].split()]
        if not self.workers or len(set(self.workers)) != len(self.workers) or any(n < 1 or n > 50 for n in self.workers):
            raise ValueError('invalid disposable worker allocation')
        self.python = str(repo / '.venv/bin/python')
        self.result = {'deadline': deadline.isoformat(), 'production_authority': False, 'commands': []}

    def left(self):
        return self.deadline - time.time()

    def record(self, phase, **fields):
        self.result.update(phase=phase, updated_utc=dt.datetime.now(dt.timezone.utc).isoformat(), **fields)
        temporary = self.run / 'status.tmp'
        temporary.write_text(json.dumps(self.result, indent=2) + '\n')
        temporary.replace(self.run / 'status.json')
        print(f'{self.result["updated_utc"]} {phase}', flush=True)

    def command(self, args, name, cap=1800, env=None):
        seconds = min(cap, self.left() - 120)
        if seconds <= 0:
            self.record('deadline_reached')
            return 124
        started = time.time()
        with (self.run / f'{name}.log').open('a') as output:
            child = subprocess.Popen([str(a) for a in args], cwd=self.code, env=env,
                                     stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = child.wait(timeout=seconds)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                code = 124
        self.result['commands'].append({'name': name, 'exit_code': code, 'seconds': round(time.time() - started, 1)})
        self.record(name, last_exit_code=code)
        return code

    def tool(self, name, *args):
        return [self.python, self.code / 'tools' / name, *args]

    def workers_start(self):
        for n in self.workers:
            args = [self.code / 'scripts/manage_linux_training_worker.sh', 'status', '--worker-count', '50', '--only', str(n)]
            status = subprocess.run(args, capture_output=True, text=True, check=True).stdout
            if 'is stopped' in status:
                if self.command([*args[:1], 'start', *args[2:]], f'worker-{n}-start', 60):
                    raise RuntimeError(f'worker {n} could not start')
            else:
                pid = (Path.home() / f'.local/share/factorio-rl/training/{n:02d}/worker/factorio.pid').read_text().strip()
                cgroup = Path('/proc', pid, 'cgroup').read_text()
                unit = os.environ.get('LAYA_QUEUE_UNIT')
                if not unit or unit not in cgroup:
                    raise RuntimeError(f'worker {n} is owned by another runtime')

    def evaluate(self, name, level, seed, policy, extra=()):
        instances = [arg for n in self.workers for arg in ('--instance', f'factorio-training-linux-{n:02d}')]
        return self.command(self.tool('run_laya_workflow_batch.py', *instances, '--slots', '1', '--stepped',
            '--game-speed', '32', '--level', str(level), '--policy', policy, '--seed-start', str(seed),
            '--episodes', '32', '--max-len', '2048', '--max-decisions', '60', '--max-no-progress', '12',
            '--evidence-dir', self.repo / 'data/laya-exploration', '--run-prefix', name, *extra), name, 1200)

    def recover_cases(self):
        prefixes = self.manifest['config']['LAYA_CASE_PREFIXES'].split()
        recovered = self.run / 'recovered-labels'
        recovered.mkdir()
        for prefix in prefixes:
            groups = {}
            for path in (self.repo / 'data/laya-counterfactual/labels').glob(f'{prefix}-r*-p*.jsonl'):
                groups.setdefault(path.name.rsplit('-p', 1)[0], []).append(path)
            for tag, paths in groups.items():
                target = self.repo / f'data/laya-pi/{tag}-cases.jsonl'
                if target.exists():
                    continue
                rows, malformed = [], 0
                for path in paths:
                    for line in path.open():
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            malformed += 1
                source = recovered / f'{tag}.jsonl'
                source.write_text(''.join(json.dumps(row) + '\n' for row in rows))
                self.record('recover_partial_round', recovered_round=tag, truncated_rows=malformed)
                if rows:
                    self.command(self.tool('laya_labels_to_cases.py', source, '--temperature-ticks', '100', '--output', target),
                                 f'recover-{tag}', 300)
        files = [path for prefix in prefixes for path in (self.repo / 'data/laya-pi').glob(f'{prefix}-r*-cases.jsonl')]
        last, seeds = resume_position(self.manifest, (self.parent / 'summary.log').read_text())
        files = [p for p in files if int(p.stem.split('-r')[-1].split('-')[0]) >= last - 7]
        return files, last, seeds, prefixes

    def independent(self, checkpoint, tag, baseline_tag):
        if self.left() < 1800:
            self.record('held_out_skipped_for_deadline')
            return
        self.workers_start()
        for level in (1, 2, 3):
            if self.left() < 300:
                break
            base = f'{baseline_tag}-L{level}'
            path = self.repo / f'data/laya-exploration/batches/{base}.jsonl'
            if not path.exists() and self.evaluate(base, level, 64001, 'rollout'):
                return
            candidate = f'{tag}-L{level}'
            if self.evaluate(candidate, level, 64001, 'laya', ('--checkpoint', str(checkpoint))):
                return
            self.command(self.tool('compare_laya_batches.py', path,
                self.repo / f'data/laya-exploration/batches/{candidate}.jsonl',
                '--seed-start', '64001', '--episodes', '32'), f'compare-{candidate}', 60)
            reports = [json.loads(line) for line in (self.run / f'compare-{candidate}.log').read_text().splitlines()
                       if line.startswith('{')]
            self.result.setdefault('paired_validation', []).append({'policy': tag, 'level': level, 'reports': reports})
            self.record('paired_validation_recorded')

    def execute(self):
        self.record('waiting_for_current_run')
        unit = self.manifest['unit']
        while self.left() > 600:
            state = subprocess.run(['systemctl', '--user', 'show', unit, '--property=ActiveState', '--value'],
                                   capture_output=True, text=True).stdout.strip()
            if state not in {'active', 'activating', 'deactivating'}:
                break
            time.sleep(30)
        if not predecessor_ready(self.parent, self.manifest, time.time()):
            self.record('blocked_predecessor_stopped_early')
            return
        if self.left() < 3 * 3600:
            self.record('skipped_insufficient_learning_window')
            return
        self.record('audit_and_contextual_teacher')
        files, last, seeds, prefixes = self.recover_cases()
        teacher = self.run / 'contextual-teacher.json'
        policy = 'rollout'
        if files and not self.command(self.tool('train_laya_features.py', *files,
            '--require-sampling-version', 'episode-relative-v1', '--output', teacher), 'teacher-fit', 1200):
            self.workers_start()
            pairs = []
            seed = int(self.manifest['config']['LAYA_EVAL_START'])
            prefix = self.manifest['config']['LAYA_PREFIX']
            complete = True
            for level in (1, 2, 3):
                name = f'day-20261002-teacher-L{level}'
                if self.evaluate(name, level, seed, 'features', ('--features-model', str(teacher))):
                    complete = False
                    break
                pairs += ['--level-pair', str(level), str(self.repo / f'data/laya-exploration/batches/{prefix}-eval-rollout-v4-L{level}.jsonl'),
                          str(self.repo / f'data/laya-exploration/batches/{name}.jsonl')]
            if complete and self.command(self.tool('laya_teacher_gate.py', *pairs, '--seed-start', str(seed), '--episodes', '32'),
                                         'teacher-admission', 60) == 0:
                policy = str(teacher)
        self.record('queued_learning_cycle', teacher=policy, consumed_round=last, next_seeds=seeds)
        self.workers_start()
        driver_name = 'day-20261002-learning'
        driver_run = self.repo / 'data/laya-pi' / driver_name
        driver_run.mkdir()
        shutil.copytree(self.code, driver_run / 'code')
        hours = (self.left() - 2700) / 3600
        if hours < 2.5:
            self.record('skipped_insufficient_learning_window')
            return
        env = {k: v for k, v in os.environ.items() if not k.startswith('LAYA_')}
        config = {**self.manifest['config'], 'LAYA_RUN_NAME': driver_name, 'LAYA_PREFIX': 'dq01',
            'LAYA_CASE_PREFIXES': ' '.join([*prefixes, 'dq01']), 'LAYA_RESUME_ROUND': str(last), 'LAYA_START_POLICY': policy,
            'LAYA_L1_SEED': str(seeds[1]), 'LAYA_L2_SEED': str(seeds[2]), 'LAYA_L3_SEED': str(seeds[3]),
            'LAYA_CASE_WINDOW_ROUNDS': '8', 'LAYA_FINAL_RESERVE_SECONDS': '9000', 'LAYA_FINETUNE_TIMEOUT': '110m',
            'LAYA_FINETUNE_FIRST': '1', 'LAYA_FINETUNE_EVERY': '0', 'LAYA_FINETUNE_FINAL': '1',
            'LAYA_DRIVER_FROZEN': '1', 'LAYA_REPO': str(self.repo),
            'LAYA_PROGRESS_PATH': str(self.run / 'main-training/progress.json')}
        (driver_run / 'experiment.json').write_text(json.dumps({'config': config, 'parent_queue': str(self.run),
            'deadline': dt.datetime.fromtimestamp(self.deadline, dt.timezone.utc).isoformat(), 'production_authority': False}, indent=2) + '\n')
        env.update(config)
        code = self.command(['bash', driver_run / 'code/scripts/laya_overnight.sh', str(hours)],
                            'learning-driver', self.left() - 2700, env)
        if code:
            self.record('learning_driver_incomplete', exit_code=code)
            return
        checkpoints = sorted((self.repo / 'data/laya-checkpoints').glob(f'laya-{driver_name}-*'), key=lambda p: p.stat().st_mtime, reverse=True)
        checkpoint = next((p for p in checkpoints if (p / 'evaluation-held-out.json').exists()
                           and json.loads((p / 'evaluation-held-out.json').read_text()).get('status') == 'ready'), None)
        if checkpoint is None:
            self.record('no_completed_student_checkpoint')
            return
        self.record('independent_paired_validation', checkpoint=str(checkpoint))
        self.independent(checkpoint, 'day-20261002-independent', 'day-20261002-independent-rollout')
        if self.left() >= 5400:
            # A throughput hypothesis, not a substitute for the validated model.
            config_path = json.loads((checkpoint / 'rl_agent_config.json').read_text())
            dataset = next((p for p in driver_run.glob('dataset-*.jsonl')
                            if hashlib.sha256(p.read_bytes()).hexdigest() == config_path['dataset_sha256']), None)
            if dataset:
                pilot = self.repo / 'data/laya-checkpoints/laya-day-20261002-throughput-pilot'
                pilot_env = {**env, 'LAYA_MICRO_BATCH': '2', 'LAYA_GRADIENT_ACCUMULATION': '8', 'LAYA_EPOCHS': '1',
                             'LAYA_MAX_LEN': '2048', 'LAYA_HEAD_MAX_LEN': '256', 'LAYA_GRADIENT_CHECKPOINTING': '1',
                             'LAYA_ATTN_IMPLEMENTATION': 'sdpa', 'LAYA_CHECKPOINT_INTERVAL': '25',
                             'LAYA_PROGRESS_PATH': str(self.run / 'pilot-training/progress.json')}
                self.record('throughput_pilot')
                if self.command(self.tool('train_laya_4060.py', '--cases', dataset, '--model-dir',
                    self.repo / 'data/laya-base/typed-decisions', '--output-dir', pilot), 'pilot-train', 2700, pilot_env) == 0:
                    if self.command(self.tool('evaluate_laya.py', '--cases', dataset, '--checkpoint', pilot,
                        '--base-model', self.repo / 'data/laya-base/typed-decisions', '--split', 'held_out',
                        '--output', pilot / 'evaluation-held-out.json'), 'pilot-offline', 600) == 0:
                        self.independent(pilot, 'day-20261002-pilot', 'day-20261002-independent-rollout')
        self.record('complete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--code', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--predecessor-run', type=Path, required=True)
    parser.add_argument('--deadline', required=True)
    args = parser.parse_args()
    deadline = dt.datetime.fromisoformat(args.deadline)
    if deadline.tzinfo is None or deadline.timestamp() <= time.time():
        parser.error('deadline must be a future time with explicit timezone')
    for path in (args.run_dir, args.predecessor_run):
        if not path.resolve().is_relative_to((args.repo / 'data/laya-pi').resolve()):
            parser.error('queue and predecessor must be inside repository lab evidence')
    queue = Queue(args.repo, args.code, args.run_dir, args.predecessor_run, deadline)
    try:
        queue.execute()
    except Exception as error:
        queue.record('failed', error=f'{type(error).__name__}: {error}')
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
