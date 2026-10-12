# Path: orchestrator/work_queue.py | Purpose: Durable goal order and bounded, dependency-aware execution.
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from orchestrator.priority_list import age_bonus


@dataclass
class WorkJob:
    job_id: str
    operation: str
    params: dict
    priority: int
    created_tick: int
    sequence: int
    kind: str = 'construction'
    state: str = 'ready'
    parents: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    required: dict[str, int] = field(default_factory=dict)
    delivered: int = 0
    reserved: dict[str, int] = field(default_factory=dict)
    checkpoint: dict = field(default_factory=dict)
    reason: str = ''
    retry_tick: int = 0
    blocked_signature: str | None = None

    @property
    def item(self):
        return self.params.get('item') or self.params.get('recipe')

    @property
    def target(self):
        return int(self.params.get('stock_target', 0))


class WorkQueue:
    """Laya owns goal order; execution owns continuation and causal prerequisites.

    One command stream advances several independent lanes. Stock jobs represent
    one finite bill, not a continually refilled stock policy. Scoped delivery
    credits are cumulative and never inferred from global force production.
    Physical claims and layout remain owned by the shared material/planner tools.
    """
    VERSION = 1

    def __init__(self, path: Path, *, world_id: str, tick: int):
        self.path, self.world_id = path, world_id
        self.jobs: dict[str, WorkJob] = {}
        if path.exists():
            payload = json.loads(path.read_text())
            if payload.get('version') != self.VERSION or payload.get('world_id') != world_id:
                raise ValueError('Work queue world/version mismatch')
            self.jobs = {j['job_id']: WorkJob(**j) for j in payload['jobs']}
            if tick < payload['tick']:
                # A copied/reset world's clock cannot postpone all its work.
                for job in self.jobs.values():
                    job.retry_tick = tick
                    job.created_tick = min(tick, job.created_tick)
                    job.blocked_signature = None
        self.tick = tick

    @staticmethod
    def identity(operation, params):
        # Raising a finite target changes the bill, not the job identity.
        identity = {k: v for k, v in params.items() if k != 'stock_target'}
        digest = hashlib.sha256(json.dumps([operation, identity], sort_keys=True).encode()).hexdigest()[:16]
        return f'{operation}:{digest}'

    def enqueue(self, operation, params, *, priority, tick, kind=None, parent=None):
        job_id = self.identity(operation, params)
        job = self.jobs.get(job_id)
        if job is None:
            job = WorkJob(job_id, operation, dict(params), priority, tick, len(self.jobs),
                          kind or ('production' if operation == 'produce' else 'construction'))
            self.jobs[job_id] = job
        elif int(params.get('stock_target', 0)) > job.target:
            job.params['stock_target'] = int(params['stock_target'])
            job.state, job.blocked_signature = 'ready', None
        job.priority = max(job.priority, priority)
        if parent and parent not in job.parents and job.state == 'complete' and job.kind in {'production', 'spare'}:
            # A new consumer owns a new bill. Previous deliveries cannot pay
            # for it, and a lingering old lane cannot be counted again.
            lanes = job.checkpoint.get('lane_credits', {})
            floors = job.checkpoint.setdefault('lane_floors', {})
            for lane, count in lanes.items():
                floors[lane] = floors.get(lane, 0) + count
            job.checkpoint['lane_credits'] = {}
            job.delivered, job.state, job.blocked_signature = 0, 'ready', None
        if parent and parent not in job.parents:
            if self._path(job_id, parent):
                self.jobs[parent].reason = f'dependency_cycle:{job_id}'
                self.jobs[parent].state = 'blocked'
                return None
            job.parents.append(parent)
            self.jobs[parent].dependencies.append(job_id)
        self.tick = tick
        self.save()
        return job

    def _path(self, start, target):
        todo, seen = [start], set()
        while todo:
            here = todo.pop()
            if here == target:
                return True
            if here not in seen:
                seen.add(here)
                todo.extend(self.jobs[here].dependencies)
        return False

    def promote(self, job_id, *, tick):
        active = [j.priority for j in self.jobs.values() if j.state != 'complete']
        priority = max(active, default=50) + 10
        todo, seen = [job_id], set()
        while todo:
            here = todo.pop()
            if here not in seen:
                seen.add(here)
                job = self.jobs[here]
                job.priority = priority
                job.retry_tick = min(job.retry_tick, tick)
                todo.extend(job.dependencies)
        self.save()

    def signature(self, job, snapshot):
        stock = snapshot.get('stock') or {}
        claims = snapshot.get('claims') or {}
        # Irrelevant surplus crafting cannot wake an unchanged shortage.
        values = {i: [stock.get(i, 0), claims.get(i), snapshot.get('dependency_budget', {}).get(i, 0)]
                  for i in job.required}
        if job.reason.startswith('geometry:'):
            values['layout'] = snapshot.get('layout')
        return json.dumps(values, sort_keys=True)

    def reconcile(self, snapshot):
        self.tick = int(snapshot['tick'])
        stock = snapshot.get('stock')
        owned = snapshot.get('owned', {})
        for job in self.jobs.values():
            evidence = owned.get(job.job_id, {})
            # Credit is a monotonic sum from the owning job's lane/delivery record.
            job.delivered = max(job.delivered, int(evidence.get('delivered', 0)))
            job.reserved = dict(evidence.get('reserved', job.reserved))
            if job.checkpoint.get('pending_entities') and evidence.get('pending_ready') is True:
                job.checkpoint.pop('pending_entities')
                job.state, job.reason, job.retry_tick = 'ready', '', self.tick
            if evidence.get('complete') is True or (
                job.kind in {'production', 'spare'} and stock is not None and job.target > 0
                and job.delivered + int(evidence.get('stock', stock.get(job.item, 0))) >= job.target
            ):
                job.state, job.reason = 'complete', 'bill fulfilled'
            elif job.state == 'blocked' and job.blocked_signature is not None:
                if self.signature(job, snapshot) != job.blocked_signature:
                    job.state, job.blocked_signature, job.retry_tick = 'ready', None, self.tick
        self.save()

    def ready(self, snapshot):
        candidates = [j for j in self.jobs.values()
                      if j.state != 'complete' and j.retry_tick <= self.tick
                      and j.blocked_signature is None
                      and not j.checkpoint.get('pending_entities')
                      and not j.reason.startswith('dependency_cycle:')
                      and (j.kind != 'spare' or not any(
                          higher.state != 'complete' and higher.priority > j.priority
                          for higher in self.jobs.values()))
                      and all(self.jobs[d].state == 'complete' for d in j.dependencies)]
        return sorted(candidates, key=lambda j: (
            j.state == 'blocked',
            j.checkpoint.get('last_attempt_tick', -1) if j.state == 'blocked' else 0,
            -(j.priority + age_bonus(self.tick-j.created_tick)), j.sequence, j.job_id))

    def record(self, job, receipt, snapshot, *, can_produce: Callable[[str], bool]):
        details = receipt.get('details') or {}
        code = receipt.get('code') or ''
        job.checkpoint['receipt'] = receipt
        job.checkpoint['last_attempt_tick'] = self.tick
        if code == 'infrastructure_construction_wait' and details.get('pending'):
            job.checkpoint['pending_entities'] = details['pending']
        job.retry_tick = self.tick + 120
        if code == 'material_shortage':
            job.required = {i: int(n) for i, n in (details.get('required') or {}).items() if int(n) > 0}
            job.state, job.reason = 'blocked', 'material_shortage'
            job.blocked_signature = self.signature(job, snapshot)
            for item, count in job.required.items():
                if int((snapshot.get("stock") or {}).get(item, 0)) >= count:
                    continue
                if can_produce(item):
                    self.enqueue('produce', {'item': item, 'stock_target': count}, priority=job.priority+1,
                                 tick=self.tick, parent=job.job_id)
                else:
                    job.reason = f'unsupported_supply:{item}'
        elif not receipt.get('ok'):
            job.state, job.reason = 'running' if receipt.get('progress') else 'blocked', code
            # Explicit toolkit predecessor evidence is stronger than a recipe forecast.
            precursor = details.get('predecessor') or details.get('rung') or details.get('prerequisite')
            if precursor and can_produce(precursor):
                self.enqueue('produce', {'item': precursor, 'stock_target': int(details.get('required_stock') or 1)},
                             priority=job.priority, tick=self.tick, parent=job.job_id)
            if code.endswith('_unbuildable'):
                job.reason = f'geometry:{code}'
                job.blocked_signature = self.signature(job, snapshot)
        elif job.kind in {'production', 'spare'}:
            # A producer's existence does not prove the finite batch was delivered.
            job.state, job.reason = 'running', 'awaiting_output'
        else:
            job.state, job.reason = 'complete', 'operation verified'
        self.save()

    def pulse(self, snapshot, execute, *, can_produce, preempt=lambda _a, _b: False, max_jobs=4):
        self.reconcile(snapshot)
        served, receipts = set(), []
        for _ in range(max_jobs):
            job = next((j for j in self.ready(snapshot) if j.job_id not in served), None)
            if job is None:
                break
            served.add(job.job_id)
            for lower in self.jobs.values():
                if lower.state == 'running' and lower.priority < job.priority and preempt(lower, job):
                    lower.state, lower.reason = 'paused', f'preempted_for:{job.job_id}'
            params = dict(job.params)
            if job.kind in {'production', 'spare'}:
                params['stock_target'] = max(1, job.target-job.delivered)
            result = execute(job, params)
            result['job_id'] = job.job_id
            self.record(job, result, snapshot, can_produce=can_produce)
            receipts.append(result)
            self.reconcile(snapshot)
        return receipts

    def describe(self):
        return [asdict(j) for j in sorted(self.jobs.values(), key=lambda j: (-j.priority, j.sequence))]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps({'version': self.VERSION, 'world_id': self.world_id,
                                   'tick': self.tick, 'jobs': self.describe()}, indent=2)+'\n')
        temp.replace(self.path)
