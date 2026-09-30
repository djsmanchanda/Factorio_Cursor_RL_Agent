# Path: training/laya_lab_workflow.py | Purpose: Bind scoped training-lab telemetry to measured workflow decisions.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from training.isolation import assert_training_identity
from training.laya_work_graph import WorkGraph
from training.laya_workflow_observation import WorkflowObservationError, observe_workflow


def observe_lab_workflow(
    lab_report: Mapping[str, Any], branch_specs: Sequence[Mapping[str, Any]],
    previous_graph: WorkGraph | None = None,
) -> tuple[WorkGraph, dict[str, Any]]:
    """Use real disposable item counters, leaving unreported supply unknown.

    This is a read-only bridge. The training lab does not yet report inventory,
    construction bills, or every recipe, so those facts cannot be inferred from
    entity counts or a historical output counter.
    """
    if not isinstance(lab_report, Mapping):
        raise WorkflowObservationError("training lab report must be an object")
    if lab_report.get("status") not in {"ready", "running", "completed"}:
        raise WorkflowObservationError("training lab report is not an active or completed episode")
    surface, force = lab_report.get("surface"), lab_report.get("force")
    if not isinstance(surface, str) or not isinstance(force, str):
        raise WorkflowObservationError("training lab report lacks surface or force")
    try:
        assert_training_identity(surface, force)
    except ValueError as error:
        raise WorkflowObservationError(str(error)) from error
    metrics = lab_report.get("metrics")
    if not isinstance(metrics, Mapping):
        raise WorkflowObservationError("training lab report lacks metrics")
    production = metrics.get("item_production")
    if not isinstance(production, Mapping) or not isinstance(production.get("items"), Mapping):
        raise WorkflowObservationError("training lab item production telemetry is unavailable")
    raw_items = production["items"]
    if len(raw_items) > 16:
        raise WorkflowObservationError("training lab item production telemetry exceeds its bound")
    started_tick, elapsed_ticks = lab_report.get("started_tick"), lab_report.get("elapsed_ticks")
    if type(started_tick) is not int or started_tick < 0 or type(elapsed_ticks) is not int or elapsed_ticks < 0:
        raise WorkflowObservationError("training lab report lacks valid tick provenance")
    tick = started_tick + elapsed_ticks
    items: dict[str, dict[str, Any]] = {}
    for item, raw in raw_items.items():
        if not isinstance(item, str) or not isinstance(raw, Mapping):
            raise WorkflowObservationError("training lab item production entry is invalid")
        available = raw.get("available") is True
        items[item] = {
            "stock": None,
            "draw_rate": None,
            "capacity_rate": None,
            "production_rate": raw.get("window_rate_per_tick") if available else None,
            # A productive-tick streak is not proof that the target rate held.
            # The whole measured window must be long enough at its reported rate.
            "production_sustained_ticks": raw.get("window_ticks") if available else None,
            "provenance": {
                "surface": surface, "force": force, "tick": tick,
                "source": "training_lab.item_production",
                "available": available,
            },
        }
    live = metrics.get("live_entities")
    if isinstance(live, Mapping):
        # Per-name non-ghost counts from the lab sampler: completion evidence
        # for construction branches, unlike ghost-inclusive built_entities.
        # Only items a branch measures by count enter the bounded model view.
        counted = {
            spec.get("item") for spec in branch_specs
            if isinstance(spec, Mapping) and spec.get("metric") == "built_count"
        }
        for name, count in live.items():
            if name in counted and type(count) is int and count >= 0:
                items.setdefault(name, {
                    "stock": None, "draw_rate": None, "capacity_rate": None,
                    "production_rate": None,
                    "provenance": {"surface": surface, "force": force, "tick": tick,
                                   "source": "training_lab.live_entities"},
                })["metrics"] = {"built_count": {"value": count, "sustained_ticks": 0}}
    # This count excludes ghosts in the lab sampler; ordinary built_entities
    # includes ghosts and is not evidence that construction has completed.
    pole_count = metrics.get("electric_pole_count")
    if not isinstance(live, Mapping) and type(pole_count) is int and pole_count >= 0:
        items.setdefault("medium-electric-pole", {
            "stock": None, "draw_rate": None, "capacity_rate": None,
            "production_rate": None,
            "provenance": {"surface": surface, "force": force, "tick": tick,
                           "source": "training_lab.live_electric_poles"},
        })["metrics"] = {"built_count": {"value": pole_count, "sustained_ticks": 0}}
    branch_measurements = {}
    owned = metrics.get("live_entities_by_phase")
    if owned is not None:
        if not isinstance(owned, Mapping) or len(owned) > 16:
            raise WorkflowObservationError("phase-owned entity telemetry exceeds its bound or is invalid")
        for spec in branch_specs:
            scope = spec.get("measurement_scope")
            if scope is None:
                continue
            counts = owned.get(scope, {})
            if not isinstance(counts, Mapping):
                raise WorkflowObservationError("phase-owned entity counts are invalid")
            count = counts.get(spec["item"], 0)
            if type(count) is not int or count < 0:
                raise WorkflowObservationError("phase-owned entity count is invalid")
            branch_measurements[spec["branch_id"]] = {
                "metric": "built_count", "value": count, "sustained_ticks": 0,
            }
    report = {
        "runtime": "disposable_training", "surface": surface, "force": force,
        "items": items, "branch_measurements": branch_measurements,
    }
    return observe_workflow(report, branch_specs, previous_graph)


__all__ = ["observe_lab_workflow"]
