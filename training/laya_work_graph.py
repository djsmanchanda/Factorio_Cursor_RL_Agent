# Path: training/laya_work_graph.py | Purpose: Track measured Laya work branches without choosing their order.

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Mapping


class WorkGraphError(ValueError):
    """A work graph or its measurement is ambiguous or malformed."""


@dataclass(frozen=True)
class CompletionTarget:
    """A measured lower bound; a placed ghost is never completion evidence."""

    metric: str
    minimum: float
    required_sustained_ticks: int = 0

    def __post_init__(self) -> None:
        if not self.metric or not self.metric.strip():
            raise WorkGraphError("completion metric is required")
        if type(self.minimum) not in {int, float} or not math.isfinite(self.minimum) or self.minimum <= 0:
            raise WorkGraphError("completion minimum must be finite and positive")
        if type(self.required_sustained_ticks) is not int or self.required_sustained_ticks < 0:
            raise WorkGraphError("required sustained ticks must be nonnegative")


@dataclass(frozen=True)
class CompletionMeasurement:
    """An externally observed outcome for exactly one metric."""

    metric: str
    value: float
    sustained_ticks: int = 0

    def __post_init__(self) -> None:
        if not self.metric or not self.metric.strip():
            raise WorkGraphError("measurement metric is required")
        if type(self.value) not in {int, float} or not math.isfinite(self.value) or self.value < 0:
            raise WorkGraphError("measurement value must be finite and nonnegative")
        if type(self.sustained_ticks) is not int or self.sustained_ticks < 0:
            raise WorkGraphError("measurement sustained ticks must be nonnegative")


@dataclass(frozen=True)
class Blocker:
    reason: str
    wake_condition: str

    def __post_init__(self) -> None:
        if not self.reason or not self.reason.strip():
            raise WorkGraphError("blocker reason is required")
        if not self.wake_condition or not self.wake_condition.strip():
            raise WorkGraphError("blocker wake condition is required")


@dataclass(frozen=True)
class WorkBranch:
    branch_id: str
    parent_goal_id: str
    completion: CompletionTarget
    requires: tuple[str, ...] = ()
    reserved_materials: tuple[tuple[str, int], ...] = ()
    status: str = "open"
    blocker: Blocker | None = None
    closed_target: CompletionTarget | None = None

    def __post_init__(self) -> None:
        if not self.branch_id or not self.branch_id.strip():
            raise WorkGraphError("branch ID is required")
        if not self.parent_goal_id or not self.parent_goal_id.strip():
            raise WorkGraphError(f"branch {self.branch_id!r} needs a parent goal")
        if self.status not in {"open", "blocked", "closed"}:
            raise WorkGraphError(f"branch {self.branch_id!r} has invalid status")
        if self.status == "closed" and self.closed_target is None:
            raise WorkGraphError(f"branch {self.branch_id!r} has ambiguous completion")
        if self.status == "closed" and self.closed_target != self.completion:
            raise WorkGraphError(f"branch {self.branch_id!r} closure does not match target")
        if self.status != "closed" and self.closed_target is not None:
            raise WorkGraphError(f"branch {self.branch_id!r} has stale closure evidence")
        if len(set(self.requires)) != len(self.requires):
            raise WorkGraphError(f"branch {self.branch_id!r} repeats a prerequisite")
        if any(not dependency or not dependency.strip() for dependency in self.requires):
            raise WorkGraphError(f"branch {self.branch_id!r} has an empty prerequisite")
        items = [item for item, _ in self.reserved_materials]
        if len(set(items)) != len(items) or any(not item or not item.strip() for item in items):
            raise WorkGraphError(f"branch {self.branch_id!r} has invalid material names")
        if any(type(quantity) is not int or quantity < 0 for _, quantity in self.reserved_materials):
            raise WorkGraphError(f"branch {self.branch_id!r} has invalid reservations")


@dataclass(frozen=True)
class WorkGraph:
    branches: tuple[WorkBranch, ...]

    def __post_init__(self) -> None:
        by_id = {branch.branch_id: branch for branch in self.branches}
        if len(by_id) != len(self.branches):
            raise WorkGraphError("duplicate branch ID")
        for branch in self.branches:
            for dependency in branch.requires:
                if dependency not in by_id:
                    raise WorkGraphError(f"branch {branch.branch_id!r} needs unknown branch {dependency!r}")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(branch_id: str) -> None:
            if branch_id in visiting:
                raise WorkGraphError(f"cyclic prerequisite at {branch_id!r}")
            if branch_id in visited:
                return
            visiting.add(branch_id)
            for dependency in by_id[branch_id].requires:
                visit(dependency)
            visiting.remove(branch_id)
            visited.add(branch_id)

        for branch_id in by_id:
            visit(branch_id)


def reconcile_work_graph(
    graph: WorkGraph,
    measurements: Mapping[str, CompletionMeasurement],
    *,
    blockers: Mapping[str, Blocker | None] | None = None,
    targets: Mapping[str, CompletionTarget] | None = None,
) -> WorkGraph:
    """Apply external facts; do not infer demand, priority, or an action.

    Missing measurements preserve an established closure unless its target
    changed. A fresh measurement below target reopens a closed branch. A wake
    condition is descriptive; only a supplied cleared blocker unblocks work.
    """
    blockers = blockers or {}
    targets = targets or {}
    known = {branch.branch_id for branch in graph.branches}
    for name, facts in (("measurement", measurements), ("blocker", blockers), ("target", targets)):
        unknown = set(facts) - known
        if unknown:
            raise WorkGraphError(f"{name} for unknown branches: {sorted(unknown)}")
    updated: list[WorkBranch] = []
    for branch in graph.branches:
        target = targets.get(branch.branch_id, branch.completion)
        blocker = blockers.get(branch.branch_id, branch.blocker)
        measurement = measurements.get(branch.branch_id)
        status = branch.status
        closed_target = branch.closed_target
        if target != branch.completion and status == "closed":
            status, closed_target = "open", None
        if measurement is not None:
            if measurement.metric != target.metric:
                raise WorkGraphError(f"branch {branch.branch_id!r} measurement metric does not match target")
            satisfied = (
                measurement.value >= target.minimum
                and measurement.sustained_ticks >= target.required_sustained_ticks
            )
            status, closed_target = ("closed", target) if satisfied else ("open", None)
        updated.append(replace(branch, completion=target, blocker=blocker, status=status, closed_target=closed_target))

    closed = {branch.branch_id for branch in updated if branch.status == "closed"}
    normalized = []
    for branch in updated:
        if branch.status == "closed":
            normalized.append(branch)
        else:
            blocked = branch.blocker is not None or any(dependency not in closed for dependency in branch.requires)
            normalized.append(replace(branch, status="blocked" if blocked else "open", closed_target=None))
    return WorkGraph(tuple(normalized))


def project_work_graph(graph: WorkGraph) -> dict[str, list[dict]]:
    """Return stable, serializable sets without strategic ranking.

    Eligible branches have closed prerequisites. Runnable branches are eligible
    and have no current blocker. Blocked branches lack a prerequisite or have
    an explicit blocker; the wake hint never clears a blocker by itself.
    """
    graph = reconcile_work_graph(graph, {})
    closed_ids = {branch.branch_id for branch in graph.branches if branch.status == "closed"}
    projection: dict[str, list[dict]] = {key: [] for key in ("eligible", "runnable", "blocked", "closed")}
    for branch in sorted(graph.branches, key=lambda item: item.branch_id):
        missing = sorted(set(branch.requires) - closed_ids)
        record = {
            "branch_id": branch.branch_id,
            "parent_goal_id": branch.parent_goal_id,
            "status": branch.status,
            "requires": list(branch.requires),
            "unmet_prerequisites": missing,
            "blocker": None if branch.blocker is None else {
                "reason": branch.blocker.reason,
                "wake_condition": branch.blocker.wake_condition,
            },
            "reserved_materials": {item: amount for item, amount in branch.reserved_materials},
            "completion": {
                "metric": branch.completion.metric,
                "minimum": branch.completion.minimum,
                "required_sustained_ticks": branch.completion.required_sustained_ticks,
            },
        }
        if branch.status == "closed":
            projection["closed"].append(record)
        elif not missing:
            projection["eligible"].append(record)
            projection["blocked" if branch.blocker else "runnable"].append(record)
        else:
            projection["blocked"].append(record)
    return projection
