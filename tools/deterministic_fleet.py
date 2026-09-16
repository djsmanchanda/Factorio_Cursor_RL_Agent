"""Pure scheduling and result accounting for deterministic Factorio lanes.

This module deliberately knows nothing about Factorio, RCON, subprocesses, or
filesystem layout.  It models the part of a fleet controller that must remain
deterministic when the process that owns a lane is restarted: pinned suite
inputs, bounded dispatch, checkpoint progress, timing evidence, and ranking.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from math import inf
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence


MAX_ACTIVE_LANES = 8
TIMING_SAMPLE_LIMIT = 20
SLOWDOWN_FACTOR = 2.5


class LaneStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FUNCTIONAL_FAILED = "functional_failed"
    FAILED = "functional_failed"  # Backward-compatible name for callers.
    TIMED_OUT = "timed_out"
    INCOMPATIBLE = "incompatible"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    CANCELLED = "cancelled"
    PREEMPTED = "preempted"


class LaneKind(str, Enum):
    ENDPOINT = "endpoint"
    MIDDLE = "middle"
    VERIFICATION = "verification"
    AD_HOC = "ad_hoc"


@dataclass(frozen=True)
class CheckpointTimeline:
    """An explicitly ordered checkpoint registry.

    IDs need not sort lexicographically, so inserting ``C1a`` is safe as long
    as callers provide the new registry order.  The registry version belongs
    to the suite snapshot, not to a mutable scheduler singleton.
    """

    checkpoint_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        ids = tuple(self.checkpoint_ids)
        if not ids or any(not isinstance(item, str) or not item for item in ids):
            raise ValueError("checkpoint timeline must contain non-empty IDs")
        if len(set(ids)) != len(ids):
            raise ValueError("checkpoint IDs must be unique")
        object.__setattr__(self, "checkpoint_ids", ids)

    @property
    def first(self) -> str:
        return self.checkpoint_ids[0]

    @property
    def latest(self) -> str:
        return self.checkpoint_ids[-1]

    def index(self, checkpoint_id: str) -> int:
        try:
            return self.checkpoint_ids.index(checkpoint_id)
        except ValueError as exc:
            raise ValueError(f"unknown checkpoint: {checkpoint_id}") from exc

    def successor(self, checkpoint_id: str) -> str | None:
        index = self.index(checkpoint_id) + 1
        return self.checkpoint_ids[index] if index < len(self.checkpoint_ids) else None


@dataclass(frozen=True)
class SuiteInputSnapshot:
    """Immutable inputs pinned when a suite enters the queue."""

    suite_id: str
    commit_sha: str
    registry_version: str
    mission_id: str
    factorio_version: str
    checkpoint_generations: Mapping[str, str]
    frontier_checkpoint: str | None = None
    enqueue_order: int = 0

    def __post_init__(self) -> None:
        if not self.suite_id or not self.commit_sha:
            raise ValueError("suite_id and commit_sha are required")
        if not self.registry_version or not self.mission_id or not self.factorio_version:
            raise ValueError("registry, mission, and Factorio versions are required")
        generations = dict(self.checkpoint_generations)
        if not generations:
            raise ValueError("a suite must pin at least one checkpoint generation")
        if any(not key or not value for key, value in generations.items()):
            raise ValueError("checkpoint generations must have non-empty IDs and values")
        object.__setattr__(self, "checkpoint_generations", MappingProxyType(generations))

    def frontier(self, timeline: CheckpointTimeline) -> str:
        if self.frontier_checkpoint is not None:
            timeline.index(self.frontier_checkpoint)
            return self.frontier_checkpoint
        available = [item for item in timeline.checkpoint_ids if item in self.checkpoint_generations]
        if not available:
            raise ValueError("suite does not pin a checkpoint in the supplied timeline")
        return available[-1]


@dataclass
class LaneProgress:
    """Mutable evidence accumulated by one running lane."""

    origin_checkpoint: str
    reached_checkpoints: list[str] = field(default_factory=list)
    transition_durations: dict[str, float] = field(default_factory=dict)
    slow_warning: bool = False
    warning_reasons: list[str] = field(default_factory=list)
    failure_checkpoint: str | None = None
    failure_reason: str | None = None
    functional_failure: bool = False
    log_tail: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.reached_checkpoints:
            self.reached_checkpoints.append(self.origin_checkpoint)

    @property
    def passed_checkpoints(self) -> tuple[str, ...]:
        return tuple(self.reached_checkpoints[1:])

    @property
    def furthest_checkpoint(self) -> str:
        return self.reached_checkpoints[-1]

    @property
    def tick_count(self) -> int:
        return len(self.passed_checkpoints)

    @property
    def double_tick_count(self) -> int:
        # A lane's second successor is one double-tick milestone.  Further
        # progress remains visible as ordinary ticks, rather than inventing a
        # new double-tick for every later checkpoint.
        return 1 if self.has_double_tick else 0

    @property
    def has_double_tick(self) -> bool:
        return self.tick_count >= 2

    def append_log_tail(self, lines: Iterable[str]) -> None:
        self.log_tail = tuple(str(line) for line in lines)[-100:]


@dataclass
class LaneRequest:
    lane_id: str
    snapshot: SuiteInputSnapshot
    origin_checkpoint: str
    target_checkpoint: str | None
    kind: LaneKind
    enqueue_order: int
    status: LaneStatus = LaneStatus.QUEUED
    waiting_opportunities: int = 0
    helper_enabled: bool = False
    progress: LaneProgress | None = None
    started_order: int | None = None
    finished_order: int | None = None
    elapsed_seconds: float | None = None

    @property
    def is_endpoint(self) -> bool:
        return self.kind is LaneKind.ENDPOINT

    @property
    def reclaimable(self) -> bool:
        return (
            self.status is LaneStatus.RUNNING
            and self.kind is LaneKind.MIDDLE
            and self.progress is not None
            and self.progress.has_double_tick
        )

    @property
    def display_marks(self) -> tuple[str, ...]:
        if self.progress is None:
            return ()
        return tuple(
            "✓✓" if self.kind is LaneKind.MIDDLE and index == 1 else "✓"
            for index, _checkpoint in enumerate(self.progress.passed_checkpoints)
        )


@dataclass(frozen=True)
class TimingKey:
    predicate_version: str
    factorio_version: str


@dataclass(frozen=True)
class TimingAssessment:
    key: TimingKey
    sample_count: int
    trimmed_average_seconds: float | None
    threshold_seconds: float | None
    warning: bool
    stop_if_waiting: bool


class TimingHistory:
    """Historical transition timing, bounded to the latest 20 successes."""

    def __init__(self, *, slowdown_factor: float = SLOWDOWN_FACTOR) -> None:
        if slowdown_factor <= 1:
            raise ValueError("slowdown factor must be greater than one")
        self.slowdown_factor = float(slowdown_factor)
        self._samples: dict[TimingKey, list[float]] = defaultdict(list)

    def record_success(
        self,
        predicate_version: str,
        factorio_version: str,
        duration_seconds: float,
        *,
        canonical: bool = True,
    ) -> bool:
        """Record an eligible success; return whether it was retained."""
        if duration_seconds <= 0:
            raise ValueError("duration must be positive")
        if not canonical:
            return False
        key = TimingKey(predicate_version, factorio_version)
        samples = self._samples[key]
        samples.append(float(duration_seconds))
        del samples[:-TIMING_SAMPLE_LIMIT]
        return True

    def samples(self, predicate_version: str, factorio_version: str) -> tuple[float, ...]:
        return tuple(self._samples.get(TimingKey(predicate_version, factorio_version), ()))

    @staticmethod
    def trimmed_mean(samples: Sequence[float]) -> float | None:
        if not samples:
            return None
        ordered = sorted(float(item) for item in samples)
        if len(ordered) >= 5:
            trim = max(1, int(len(ordered) * 0.10))
            if len(ordered) > trim * 2:
                ordered = ordered[trim:-trim]
        return sum(ordered) / len(ordered)

    def assess(
        self,
        predicate_version: str,
        factorio_version: str,
        duration_seconds: float,
        *,
        work_waiting: bool = False,
    ) -> TimingAssessment:
        if duration_seconds <= 0:
            raise ValueError("duration must be positive")
        key = TimingKey(predicate_version, factorio_version)
        samples = self._samples.get(key, ())
        average = self.trimmed_mean(samples) if len(samples) >= 3 else None
        threshold = average * self.slowdown_factor if average is not None else None
        warning = threshold is not None and duration_seconds >= threshold
        return TimingAssessment(
            key=key,
            sample_count=len(samples),
            trimmed_average_seconds=average,
            threshold_seconds=threshold,
            warning=warning,
            stop_if_waiting=warning and work_waiting,
        )


class FleetScheduler:
    """Deterministic queue and lane-state model for an eight-server fleet."""

    def __init__(self, timeline: CheckpointTimeline, *, max_active: int = MAX_ACTIVE_LANES) -> None:
        if not 1 <= max_active <= MAX_ACTIVE_LANES:
            raise ValueError(f"max_active must be between 1 and {MAX_ACTIVE_LANES}")
        self.timeline = timeline
        self.max_active = max_active
        self._pending: dict[str, LaneRequest] = {}
        self._active: dict[str, LaneRequest] = {}
        self._history: dict[str, LaneRequest] = {}
        self._sequence = 0
        self._dispatch_count = 0
        self._finish_sequence = 0
        self._ordinary_since_middle = 0
        self._suite_order: dict[str, int] = {}
        self._next_suite_order = 0

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def pending(self) -> tuple[LaneRequest, ...]:
        return tuple(sorted(self._pending.values(), key=lambda lane: lane.enqueue_order))

    @property
    def active(self) -> tuple[LaneRequest, ...]:
        return tuple(sorted(self._active.values(), key=lambda lane: lane.started_order or 0))

    @property
    def all_lanes(self) -> tuple[LaneRequest, ...]:
        return tuple(sorted(self._history.values(), key=lambda lane: lane.enqueue_order))

    def _new_lane(
        self,
        snapshot: SuiteInputSnapshot,
        origin_checkpoint: str,
        kind: LaneKind,
        *,
        target_checkpoint: str | None = None,
        helper_enabled: bool = False,
    ) -> LaneRequest:
        if snapshot.suite_id not in self._suite_order:
            self._next_suite_order += 1
            self._suite_order[snapshot.suite_id] = snapshot.enqueue_order or self._next_suite_order
        self.timeline.index(origin_checkpoint)
        if target_checkpoint is not None:
            self.timeline.index(target_checkpoint)
        self._sequence += 1
        lane = LaneRequest(
            lane_id=f"lane-{self._sequence:06d}",
            snapshot=snapshot,
            origin_checkpoint=origin_checkpoint,
            target_checkpoint=target_checkpoint or self.timeline.successor(origin_checkpoint),
            kind=kind,
            enqueue_order=self._sequence,
            helper_enabled=helper_enabled,
        )
        self._pending[lane.lane_id] = lane
        self._history[lane.lane_id] = lane
        return lane

    def enqueue_suite(
        self,
        snapshot: SuiteInputSnapshot,
        *,
        include_middle: bool = True,
        helper_on_frontier: bool = True,
    ) -> tuple[LaneRequest, ...]:
        """Enqueue C0, frontier, and optional intermediate lanes for a suite."""
        frontier = snapshot.frontier(self.timeline)
        lanes = [self._new_lane(snapshot, self.timeline.first, LaneKind.ENDPOINT)]
        if frontier != self.timeline.first:
            lanes.append(
                self._new_lane(
                    snapshot,
                    frontier,
                    LaneKind.ENDPOINT,
                    helper_enabled=helper_on_frontier,
                )
            )
        if include_middle:
            low = self.timeline.index(self.timeline.first)
            high = self.timeline.index(frontier)
            for checkpoint_id in self.timeline.checkpoint_ids[low + 1 : high]:
                if checkpoint_id in snapshot.checkpoint_generations:
                    lanes.append(self._new_lane(snapshot, checkpoint_id, LaneKind.MIDDLE))
        return tuple(lanes)

    def enqueue_lane(
        self,
        snapshot: SuiteInputSnapshot,
        origin_checkpoint: str,
        *,
        kind: LaneKind | None = None,
        target_checkpoint: str | None = None,
        helper_enabled: bool = False,
    ) -> LaneRequest:
        frontier = snapshot.frontier(self.timeline)
        if kind is None:
            if origin_checkpoint in (self.timeline.first, frontier):
                kind = LaneKind.ENDPOINT
            else:
                kind = LaneKind.MIDDLE
        return self._new_lane(
            snapshot,
            origin_checkpoint,
            kind,
            target_checkpoint=target_checkpoint,
            helper_enabled=helper_enabled,
        )

    def enqueue_verification(
        self,
        snapshot: SuiteInputSnapshot,
        checkpoint_id: str,
        *,
        helper_enabled: bool = False,
    ) -> LaneRequest:
        return self._new_lane(
            snapshot,
            checkpoint_id,
            LaneKind.VERIFICATION,
            helper_enabled=helper_enabled,
        )

    def enqueue_ad_hoc(
        self,
        snapshot: SuiteInputSnapshot,
        origin_checkpoint: str,
        *,
        helper_enabled: bool = False,
    ) -> LaneRequest:
        return self._new_lane(
            snapshot,
            origin_checkpoint,
            LaneKind.AD_HOC,
            helper_enabled=helper_enabled,
        )

    def cancel_queued(self, lane_id: str) -> LaneRequest:
        lane = self._pending.get(lane_id)
        if lane is None:
            raise ValueError(f"lane is not queued: {lane_id}")
        del self._pending[lane_id]
        lane.status = LaneStatus.CANCELLED
        return lane

    def _newest_suite_id(self) -> str | None:
        queued = tuple(self._pending.values())
        return max(queued, key=lambda lane: self._suite_order[lane.snapshot.suite_id]).snapshot.suite_id if queued else None

    def _oldest(self, lanes: Iterable[LaneRequest]) -> LaneRequest | None:
        return min(lanes, key=lambda lane: lane.enqueue_order, default=None)

    def _newest_endpoint(self, newest_suite_id: str) -> LaneRequest | None:
        candidates = [
            lane for lane in self._pending.values()
            if lane.snapshot.suite_id == newest_suite_id and lane.kind is LaneKind.ENDPOINT
        ]
        # C0 first, then frontier.  Explicit timeline order avoids C10/C2
        # lexical surprises.
        return min(candidates, key=lambda lane: (self.timeline.index(lane.origin_checkpoint), lane.enqueue_order), default=None)

    def _newest_middle(self, newest_suite_id: str) -> LaneRequest | None:
        return self._oldest(
            lane for lane in self._pending.values()
            if lane.snapshot.suite_id == newest_suite_id and lane.kind is LaneKind.MIDDLE
        )

    def next_to_start(self) -> LaneRequest | None:
        """Select and start one lane, or return ``None`` when no slot exists."""
        if self.active_count >= self.max_active or not self._pending:
            return None

        newest_suite = self._newest_suite_id()
        if newest_suite is None:  # pragma: no cover - guarded by pending check
            return None
        verifications = [
            lane for lane in self._pending.values()
            if lane.kind is LaneKind.VERIFICATION and lane.waiting_opportunities >= 5
        ]
        due_verification = self._oldest(verifications)
        ordinary_verification = self._oldest(
            lane for lane in self._pending.values()
            if lane.kind is LaneKind.VERIFICATION
        )
        newest_endpoint = self._newest_endpoint(newest_suite)
        newest_middle = self._newest_middle(newest_suite)

        if due_verification is not None:
            selected = due_verification
        elif newest_endpoint is not None:
            selected = newest_endpoint
        elif ordinary_verification is not None:
            selected = ordinary_verification
        elif newest_middle is not None and self._ordinary_since_middle >= 5:
            selected = newest_middle
        else:
            # Older queued work is deliberately ahead of ordinary newest-suite
            # middle lanes.  This prevents an unlimited commit queue from
            # starving earlier comparisons.
            older = self._oldest(
                lane for lane in self._pending.values()
                if lane.snapshot.suite_id != newest_suite
            )
            if older is not None:
                selected = older
            elif newest_middle is not None:
                selected = newest_middle
            else:
                # A verification request is priority 2, not priority 1: an
                # ordinary lane from the same suite gets the first slot until
                # the verification has aged through five opportunities.
                ordinary = self._oldest(
                    lane for lane in self._pending.values()
                    if lane.kind is not LaneKind.VERIFICATION
                )
                selected = ordinary or self._oldest(self._pending.values())

        assert selected is not None
        del self._pending[selected.lane_id]
        self._dispatch_count += 1
        selected.started_order = self._dispatch_count
        selected.status = LaneStatus.RUNNING
        selected.progress = LaneProgress(selected.origin_checkpoint)
        self._active[selected.lane_id] = selected

        if selected.kind is not LaneKind.VERIFICATION:
            self._ordinary_since_middle += 1
            if selected.kind is LaneKind.MIDDLE and selected.snapshot.suite_id == newest_suite:
                self._ordinary_since_middle = 0
            for lane in self._pending.values():
                if lane.kind is LaneKind.VERIFICATION:
                    lane.waiting_opportunities += 1
        return selected

    def record_checkpoint(
        self,
        lane_id: str,
        checkpoint_id: str,
        *,
        duration_seconds: float | None = None,
        slow_warning: bool = False,
        warning_reason: str | None = None,
    ) -> LaneRequest:
        lane = self._active.get(lane_id)
        if lane is None or lane.progress is None:
            raise ValueError(f"lane is not running: {lane_id}")
        checkpoint_index = self.timeline.index(checkpoint_id)
        current_index = self.timeline.index(lane.progress.furthest_checkpoint)
        if checkpoint_index <= current_index:
            raise ValueError("checkpoint progress must move forward")
        if checkpoint_index != current_index + 1:
            raise ValueError("checkpoint progress must be recorded one successor at a time")
        if duration_seconds is not None and duration_seconds <= 0:
            raise ValueError("duration must be positive")
        lane.progress.reached_checkpoints.append(checkpoint_id)
        if duration_seconds is not None:
            lane.progress.transition_durations[checkpoint_id] = float(duration_seconds)
        if slow_warning:
            lane.progress.slow_warning = True
            if warning_reason:
                lane.progress.warning_reasons.append(warning_reason)
        return lane

    def record_slow_warning(self, lane_id: str, reason: str) -> LaneRequest:
        lane = self._active.get(lane_id)
        if lane is None or lane.progress is None:
            raise ValueError(f"lane is not running: {lane_id}")
        lane.progress.slow_warning = True
        lane.progress.warning_reasons.append(reason)
        return lane

    def record_failure(
        self,
        lane_id: str,
        reason: str,
        *,
        failure_checkpoint: str | None = None,
        functional: bool = True,
        outcome: LaneStatus | None = None,
        log_lines: Iterable[str] = (),
        elapsed_seconds: float | None = None,
    ) -> LaneRequest:
        lane = self._active.pop(lane_id, None)
        if lane is None or lane.progress is None:
            raise ValueError(f"lane is not running: {lane_id}")
        if failure_checkpoint is not None:
            self.timeline.index(failure_checkpoint)
        lane.progress.failure_checkpoint = failure_checkpoint
        lane.progress.failure_reason = reason
        if outcome is None:
            outcome = LaneStatus.FUNCTIONAL_FAILED if functional else LaneStatus.INFRASTRUCTURE_ERROR
        if outcome not in {
            LaneStatus.FUNCTIONAL_FAILED,
            LaneStatus.TIMED_OUT,
            LaneStatus.INCOMPATIBLE,
            LaneStatus.INFRASTRUCTURE_ERROR,
        }:
            raise ValueError("failure outcome must be a terminal failure status")
        lane.progress.functional_failure = outcome in {
            LaneStatus.FUNCTIONAL_FAILED, LaneStatus.TIMED_OUT,
        }
        lane.progress.append_log_tail(log_lines)
        lane.status = outcome
        self._finish_sequence += 1
        lane.finished_order = self._finish_sequence
        lane.elapsed_seconds = elapsed_seconds
        return lane

    def complete_lane(
        self,
        lane_id: str,
        *,
        elapsed_seconds: float | None = None,
        log_lines: Iterable[str] = (),
    ) -> LaneRequest:
        lane = self._active.pop(lane_id, None)
        if lane is None or lane.progress is None:
            raise ValueError(f"lane is not running: {lane_id}")
        lane.progress.append_log_tail(log_lines)
        lane.status = LaneStatus.COMPLETED
        self._finish_sequence += 1
        lane.finished_order = self._finish_sequence
        lane.elapsed_seconds = elapsed_seconds
        return lane

    def reclaimable_lanes(self) -> tuple[LaneRequest, ...]:
        """Return eligible middle lanes; this method never stops one itself."""
        return tuple(
            sorted(
                (lane for lane in self._active.values() if lane.reclaimable),
                key=lambda lane: lane.started_order or inf,
            )
        )

    def release_reclaimable(self, lane_id: str) -> LaneRequest:
        lane = self._active.pop(lane_id, None)
        if lane is None or not lane.reclaimable:
            raise ValueError(f"lane is not an eligible reclaimable middle lane: {lane_id}")
        lane.status = LaneStatus.PREEMPTED
        self._finish_sequence += 1
        lane.finished_order = self._finish_sequence
        return lane


@dataclass(frozen=True)
class CommitSummary:
    commit_sha: str
    contiguous_failure_free_prefix: int
    intermediate_failures: int
    furthest_checkpoint: int
    double_ticks: int
    slow_warnings: int
    normalized_duration: float | None
    results: tuple[LaneRequest, ...] = ()

    @property
    def normalized_speed(self) -> float:
        # Lower duration is better.  Missing timing sorts after measured runs.
        return self.normalized_duration if self.normalized_duration is not None else inf


def summarize_commit(
    commit_sha: str,
    results: Iterable[LaneRequest],
    timeline: CheckpointTimeline,
) -> CommitSummary:
    lanes = tuple(lane for lane in results if lane.snapshot.commit_sha == commit_sha)
    furthest = max(
        (timeline.index(lane.progress.furthest_checkpoint) for lane in lanes if lane.progress),
        default=0,
    )
    failure_indexes = [
        timeline.index(lane.progress.failure_checkpoint)
        for lane in lanes
        if lane.progress and lane.progress.functional_failure and lane.progress.failure_checkpoint is not None
    ]
    # failure_checkpoint is the first checkpoint the lane failed to reach.
    # Therefore a failure at C4 proves a contiguous prefix only through C3.
    prefix = max(0, min(failure_indexes) - 1) if failure_indexes else furthest
    intermediate_failures = sum(
        1
        for lane in lanes
        if lane.progress
        and lane.progress.functional_failure
        and timeline.index(lane.origin_checkpoint) > 0
    )
    durations = [
        lane.elapsed_seconds for lane in lanes
        if lane.elapsed_seconds is not None and lane.elapsed_seconds > 0
    ]
    normalized_duration = sum(durations) / len(durations) if durations else None
    return CommitSummary(
        commit_sha=commit_sha,
        contiguous_failure_free_prefix=prefix,
        intermediate_failures=intermediate_failures,
        furthest_checkpoint=furthest,
        double_ticks=sum(lane.progress.double_tick_count for lane in lanes if lane.progress),
        slow_warnings=sum(1 for lane in lanes if lane.progress and lane.progress.slow_warning),
        normalized_duration=normalized_duration,
        results=lanes,
    )


def rank_commits(summaries: Iterable[CommitSummary]) -> tuple[CommitSummary, ...]:
    """Rank stability before speed, as required for commit comparison."""
    return tuple(sorted(
        summaries,
        key=lambda summary: (
            -summary.contiguous_failure_free_prefix,
            summary.intermediate_failures,
            -summary.furthest_checkpoint,
            -summary.double_ticks,
            summary.slow_warnings,
            summary.normalized_speed,
            summary.commit_sha,
        ),
    ))


@dataclass(frozen=True)
class FrontierResultProjection:
    commit_code: str
    lane_id: str
    origin_checkpoint: str
    furthest_checkpoint: str
    status: str
    elapsed_seconds: float | None
    failure_reason: str | None
    starred: bool
    log_tail: tuple[str, ...]


def last_frontier_results(
    lanes: Iterable[LaneRequest],
    frontier_checkpoint: str,
    *,
    limit: int = 10,
) -> tuple[FrontierResultProjection, ...]:
    """Project the latest frontier-origin results for the console's last-10 view."""
    if limit < 1:
        raise ValueError("limit must be positive")
    candidates = [
        lane for lane in lanes
        if lane.origin_checkpoint == frontier_checkpoint and lane.finished_order is not None
    ]
    candidates.sort(key=lambda lane: lane.finished_order or 0, reverse=True)
    return tuple(
        FrontierResultProjection(
            commit_code=lane.snapshot.commit_sha[:8],
            lane_id=lane.lane_id,
            origin_checkpoint=lane.origin_checkpoint,
            furthest_checkpoint=(lane.progress.furthest_checkpoint if lane.progress else lane.origin_checkpoint),
            status=lane.status.value,
            elapsed_seconds=lane.elapsed_seconds,
            failure_reason=(lane.progress.failure_reason if lane.progress else None),
            starred=lane.origin_checkpoint != "C0",
            log_tail=(lane.progress.log_tail if lane.progress else ()),
        )
        for lane in candidates[:limit]
    )
