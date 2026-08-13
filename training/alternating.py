# Path: training/alternating.py
# Purpose: Schedule alternating RL policy families without leaving released slots idle.

"""Bounded A/B policy-cohort scheduling.

The scheduler is deliberately transport- and Factorio-agnostic.  A controller
owns the worker leases and calls :meth:`claim` when a slot becomes available,
then :meth:`complete` when that episode reaches a terminal report.  Once every
job in a family cohort has been *assigned*, the next family is eligible to use
newly released slots; already-running episodes are allowed to drain.  This is
the important distinction between an alternating queue and two independent
batches that both try to fill every server.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import cycle
from typing import Mapping, Sequence
import uuid


@dataclass(frozen=True)
class AlternatingFamily:
    """A named policy family and its seeded scenario templates."""

    name: str
    scenarios: tuple[Mapping, ...]

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("family name cannot be empty")
        if not self.scenarios:
            raise ValueError("a family needs at least one scenario")
        ids = [str(scenario.get("scenario_id", "")) for scenario in self.scenarios]
        if any(not scenario_id for scenario_id in ids):
            raise ValueError("each family scenario needs a scenario_id")
        if len(ids) != len(set(ids)):
            raise ValueError("family scenario ids must be unique")


@dataclass(frozen=True)
class AlternatingJob:
    """One immutable episode assignment returned to a worker."""

    episode_id: str
    family: str
    policy_index: int
    scenario: Mapping
    selection_seed: int
    worker_id: str


@dataclass(frozen=True)
class AlternatingStatus:
    """Read-only scheduler evidence suitable for the Observatory."""

    family: str
    policy_index: int
    pending: int
    active: int
    completed: int
    total: int
    family_cohorts_started: int


class AlternatingPolicyScheduler:
    """Alternate policy families while reusing every released worker slot.

    ``episodes_per_policy`` counts terminal attempts for *each* family cohort,
    not scenarios.  If a family has fewer templates than that count, templates
    are cycled with fresh selection seeds.  The queue is intentionally bounded:
    only one cohort is materialized at a time, so a 50-server/20-slot run does
    not allocate millions of episode records up front.
    """

    def __init__(
        self,
        families: Sequence[AlternatingFamily],
        *,
        episodes_per_policy: int = 1000,
        start_family: int = 0,
        seed_offset: int = 0,
    ) -> None:
        if len(families) < 2:
            raise ValueError("AB scheduling requires at least two families")
        names = [family.name for family in families]
        if len(names) != len(set(names)):
            raise ValueError("family names must be unique")
        if episodes_per_policy < 1:
            raise ValueError("episodes_per_policy must be positive")
        if not 0 <= start_family < len(families):
            raise ValueError("start_family is outside the family list")
        self._families = tuple(families)
        self._episodes_per_policy = episodes_per_policy
        self._family_index = start_family
        self._policy_index = 0
        self._seed_offset = seed_offset
        self._pending: list[AlternatingJob] = []
        self._active: dict[str, AlternatingJob] = {}
        self._completed = 0
        self._family_cohorts_started = 0
        self._load_cohort()

    @property
    def family(self) -> str:
        return self._families[self._family_index].name

    @property
    def policy_index(self) -> int:
        return self._policy_index

    @property
    def active_workers(self) -> frozenset[str]:
        return frozenset(self._active)

    @property
    def pending(self) -> int:
        return len(self._pending)

    def _load_cohort(self) -> None:
        family = self._families[self._family_index]
        templates = cycle(family.scenarios)
        jobs: list[AlternatingJob] = []
        for attempt in range(self._episodes_per_policy):
            scenario = next(templates)
            scenario_id = str(scenario["scenario_id"])
            seed = self._seed_offset + self._family_cohorts_started * self._episodes_per_policy + attempt
            jobs.append(AlternatingJob(
                episode_id=(
                    f"episode-{family.name}-p{self._policy_index:04d}"
                    f"-a{attempt:04d}-{uuid.uuid4().hex[:8]}"
                ),
                family=family.name,
                policy_index=self._policy_index,
                scenario=scenario,
                selection_seed=seed,
                worker_id="",
            ))
        self._pending = jobs
        self._family_cohorts_started += 1

    def _advance_if_assigned(self) -> None:
        """Move to the next family as soon as this cohort has no queued jobs.

        Active episodes are not interrupted.  This permits B to claim slots as
        A naturally finishes its tail, which is the intended ABAB behavior.
        """
        if self._pending:
            return
        self._family_index = (self._family_index + 1) % len(self._families)
        if self._family_index == 0:
            self._policy_index += 1
        self._load_cohort()

    def claim(self, worker_ids: Sequence[str]) -> tuple[AlternatingJob, ...]:
        """Assign at most one job to each currently idle worker.

        Unknown, duplicate, or currently busy worker IDs are ignored.  The
        caller can therefore pass its full server roster after every completion
        without maintaining a second lease table.
        """
        idle: list[str] = []
        seen: set[str] = set()
        for worker_id in worker_ids:
            worker_id = str(worker_id)
            if worker_id and worker_id not in seen and worker_id not in self._active:
                seen.add(worker_id)
                idle.append(worker_id)
        assignments: list[AlternatingJob] = []
        for worker_id in idle:
            if not self._pending:
                self._advance_if_assigned()
            job = self._pending.pop(0)
            assigned = replace(job, worker_id=worker_id)
            self._active[worker_id] = assigned
            assignments.append(assigned)
        self._advance_if_assigned()
        return tuple(assignments)

    def complete(self, worker_id: str, *, success: bool | None = None) -> AlternatingJob:
        """Release one worker and return its immutable assignment.

        ``success`` is accepted for controller telemetry but deliberately does
        not alter scheduling: failures are evidence, not a reason to starve the
        other family or manufacture an unbounded retry storm.
        """
        del success
        worker_id = str(worker_id)
        try:
            job = self._active.pop(worker_id)
        except KeyError as exc:
            raise KeyError(f"worker is not assigned: {worker_id}") from exc
        self._completed += 1
        return job

    def status(self) -> AlternatingStatus:
        return AlternatingStatus(
            family=self.family,
            policy_index=self.policy_index,
            pending=len(self._pending),
            active=len(self._active),
            completed=self._completed,
            total=self._episodes_per_policy,
            family_cohorts_started=self._family_cohorts_started,
        )

