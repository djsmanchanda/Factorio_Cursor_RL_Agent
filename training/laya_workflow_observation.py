# Path: training/laya_workflow_observation.py | Purpose: Adapt disposable factory reports into measured Laya work and supply evidence.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from training.isolation import assert_training_identity
from training.laya_supply_evidence import project_supply_evidence
from training.laya_work_graph import (
    Blocker,
    CompletionMeasurement,
    CompletionTarget,
    WorkBranch,
    WorkGraph,
    reconcile_work_graph,
)


class WorkflowObservationError(ValueError):
    """A report or declared branch cannot be adapted unambiguously."""


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkflowObservationError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowObservationError(f"{label} must be a non-empty string")
    return value.strip()


def _ticks(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise WorkflowObservationError(f"{label} must be a nonnegative integer")
    return value


def _measurement(item_facts: Mapping[str, Any], metric: str) -> CompletionMeasurement | None:
    if metric == "production_rate":
        value = item_facts.get("production_rate")
        ticks = item_facts.get("production_sustained_ticks")
    else:
        metrics = _object(item_facts.get("metrics", {}), "item metrics")
        measured = metrics.get(metric)
        if measured is None:
            return None
        record = _object(measured, f"metric {metric}")
        value, ticks = record.get("value"), record.get("sustained_ticks")
    # A rate without an explicit observation window is not sustained evidence.
    if value is None or ticks is None:
        return None
    return CompletionMeasurement(metric, value, _ticks(ticks, f"{metric} sustained_ticks"))


def observe_workflow(
    report: Mapping[str, Any],
    branch_specs: Sequence[Mapping[str, Any]],
    previous_graph: WorkGraph | None = None,
) -> tuple[WorkGraph, dict[str, Any]]:
    """Project one complete disposable report without choosing goals or actions.

    Each branch spec declares `branch_id`, `parent_goal_id`, `item`, `metric`,
    `target_minimum`, optional `required_sustained_ticks`, and optional
    `requires`. An item supplies its `production_rate` with
    `production_sustained_ticks`, or an explicit named `metrics` record. Missing
    current measurements leave branches open even if a previous report closed
    them. Target changes therefore reopen immediately. `previous_graph` checks
    stable branch identity; it does not import stale measurements or blockers.
    """
    report = _object(report, "report")
    if report.get("runtime") != "disposable_training":
        raise WorkflowObservationError("report must identify a disposable training runtime")
    surface = _text(report.get("surface"), "surface")
    force = _text(report.get("force"), "force")
    try:
        assert_training_identity(surface, force)
    except ValueError as exc:
        raise WorkflowObservationError(str(exc)) from exc
    items = _object(report.get("items"), "report items")
    branch_measurements = _object(report.get("branch_measurements", {}), "branch measurements")
    if not isinstance(branch_specs, Sequence) or isinstance(branch_specs, (str, bytes)):
        raise WorkflowObservationError("branch_specs must be a sequence")

    branches: list[WorkBranch] = []
    measurements: dict[str, CompletionMeasurement] = {}
    for index, raw_spec in enumerate(branch_specs):
        spec = _object(raw_spec, f"branch_specs[{index}]")
        branch_id = _text(spec.get("branch_id"), f"branch_specs[{index}].branch_id")
        item = _text(spec.get("item"), f"branch {branch_id}.item")
        metric = _text(spec.get("metric"), f"branch {branch_id}.metric")
        dependencies = spec.get("requires", ())
        if not isinstance(dependencies, (list, tuple)):
            raise WorkflowObservationError(f"branch {branch_id}.requires must be a sequence")
        requires = tuple(_text(dependency, f"branch {branch_id} prerequisite") for dependency in dependencies)
        target = CompletionTarget(
            metric,
            spec.get("target_minimum"),
            _ticks(spec.get("required_sustained_ticks", 0), f"branch {branch_id} required_sustained_ticks"),
        )
        if metric == "production_rate" and target.required_sustained_ticks == 0:
            raise WorkflowObservationError(f"branch {branch_id} needs a positive sustained window for production_rate")
        reserved = _object(spec.get("reserved_materials", {}), f"branch {branch_id}.reserved_materials")
        branch = WorkBranch(
            branch_id=branch_id,
            parent_goal_id=_text(spec.get("parent_goal_id"), f"branch {branch_id}.parent_goal_id"),
            completion=target,
            requires=requires,
            reserved_materials=tuple(reserved.items()),
        )
        branches.append(branch)
        facts = items.get(item)
        if spec.get("measurement_scope") is not None:
            raw = branch_measurements.get(branch_id)
            if raw is not None:
                record = _object(raw, f"branch {branch_id} measurement")
                measurements[branch_id] = CompletionMeasurement(
                    _text(record.get("metric"), "branch measurement metric"), record.get("value"),
                    _ticks(record.get("sustained_ticks"), "branch measurement sustained_ticks"),
                )
        elif facts is not None:
            measurement = _measurement(_object(facts, f"items.{item}"), metric)
            if measurement is not None:
                measurements[branch_id] = measurement

    graph = WorkGraph(tuple(branches))
    if set(branch_measurements) - {branch.branch_id for branch in branches}:
        raise WorkflowObservationError("measurements reference unknown branches")
    if previous_graph is not None:
        old = {branch.branch_id: (branch.parent_goal_id, branch.requires) for branch in previous_graph.branches}
        new = {branch.branch_id: (branch.parent_goal_id, branch.requires) for branch in graph.branches}
        if old != new:
            raise WorkflowObservationError("branch identities or dependencies changed between observations")
    raw_blockers = _object(report.get("blockers", {}), "report blockers")
    unknown = set(raw_blockers) - {branch.branch_id for branch in graph.branches}
    if unknown:
        raise WorkflowObservationError(f"blockers reference unknown branches: {sorted(unknown)}")
    blockers = {}
    for branch_id, raw_blocker in raw_blockers.items():
        if raw_blocker is None:
            continue
        blocker = _object(raw_blocker, f"blocker {branch_id}")
        blockers[branch_id] = Blocker(
            _text(blocker.get("reason"), f"blocker {branch_id}.reason"),
            _text(blocker.get("wake_condition"), f"blocker {branch_id}.wake_condition"),
        )
    graph = reconcile_work_graph(graph, measurements, blockers=blockers)
    supply = project_supply_evidence(
        items,
        committed_bills=report.get("committed_bills", ()),
        reservations=report.get("reservations", ()),
        recipes=report.get("recipes"),
    )
    return graph, supply


__all__ = ["WorkflowObservationError", "observe_workflow"]
