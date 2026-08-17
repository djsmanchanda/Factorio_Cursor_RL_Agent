# Path: orchestrator/science_diagnosis.py
# Purpose: Derive replayable research-health diagnoses from read-only ScienceStatus reports.

"""Pure deterministic diagnosis for Factorio 2.1 science telemetry.

This module deliberately consumes saved ``ScienceStatus`` dictionaries rather
than a GameBridge.  It never opens RCON, changes research, or decides which
technology to select.  Its result preserves the identities of every report it
used so a later selection or repair decision can be audited and replayed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


SCIENCE_STATUS_VERSION = "1.0.0"
SCIENCE_DIAGNOSIS_VERSION = "1.0.0"

RESEARCH_NOT_SELECTED = "research_not_selected"
SCIENCE_DELIVERY_OR_SUPPLY_MISSING = "science_delivery_or_supply_missing"
LAB_POWER_LIMITED = "lab_power_limited"
RESEARCH_PROGRESS_STALLED = "research_progress_stalled"
LAB_CAPACITY_LIMITED = "lab_capacity_limited"
RESEARCH_PROGRESSING = "research_progressing"
SCIENCE_OBSERVATION_INCONCLUSIVE = "science_observation_inconclusive"


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _require_nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_tick(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _require_count(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _require_progress(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number between 0 and 1")
    progress = float(value)
    if not math.isfinite(progress) or not 0.0 <= progress <= 1.0:
        raise ValueError(f"{label} must be a number between 0 and 1")
    return progress


def _require_item_counts(value: object, label: str) -> Mapping[str, int]:
    counts = _require_mapping(value, label)
    normalized: dict[str, int] = {}
    for item, count in counts.items():
        name = _require_nonempty_string(item, f"{label} item name")
        normalized[name] = _require_count(count, f"{label}[{name!r}]")
    return normalized


def report_identity(report: Mapping[str, Any]) -> dict[str, object]:
    """Return the stable identity carried by one successful ScienceStatus.

    The Phase 1 report contract has no file-path or server-local identifier.
    Its schema version, tick, surface, and force therefore form the identity
    required for a single deterministic runtime.  Callers must retain server
    identity alongside saved reports when comparing different runtimes.
    """
    report = _require_mapping(report, "ScienceStatus")
    schema_version = _require_nonempty_string(report.get("schema_version"), "schema_version")
    if schema_version != SCIENCE_STATUS_VERSION:
        raise ValueError(
            f"unsupported ScienceStatus schema_version {schema_version!r}; "
            f"expected {SCIENCE_STATUS_VERSION!r}"
        )
    if report.get("ok") is not True:
        raise ValueError("ScienceStatus must be a successful report (ok=true)")
    return {
        "schema_version": schema_version,
        "tick": _require_tick(report.get("tick"), "tick"),
        "surface": _require_nonempty_string(report.get("surface"), "surface"),
        "force": _require_nonempty_string(report.get("force"), "force"),
    }


def _validated_state(report: Mapping[str, Any]) -> dict[str, object]:
    """Validate only fields diagnosis consumes and normalize their types."""
    identity = report_identity(report)
    research = _require_mapping(report.get("research"), "research")
    labs = _require_mapping(report.get("labs"), "labs")

    current = research.get("current")
    if current is not None:
        current = _require_nonempty_string(current, "research.current")

    queue = research.get("queue")
    if not isinstance(queue, list) or any(
        not isinstance(name, str) or not name.strip() for name in queue
    ):
        raise ValueError("research.queue must be an array of non-empty strings")

    return {
        "identity": identity,
        "current": current,
        "progress": _require_progress(research.get("progress"), "research.progress"),
        "required_packs": _require_item_counts(
            research.get("current_science_packs"), "research.current_science_packs"
        ),
        "working_labs": _require_count(labs.get("working"), "labs.working"),
        "total_labs": _require_count(labs.get("total"), "labs.total"),
        "status_counts": _require_item_counts(labs.get("status_counts"), "labs.status_counts"),
        "input_inventory": _require_item_counts(
            labs.get("input_inventory"), "labs.input_inventory"
        ),
    }


def _identity_key(identity: Mapping[str, object]) -> tuple[str, str, str, int]:
    return (
        str(identity["schema_version"]),
        str(identity["surface"]),
        str(identity["force"]),
        int(identity["tick"]),
    )


def _same_target(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return (
        left["schema_version"] == right["schema_version"]
        and left["surface"] == right["surface"]
        and left["force"] == right["force"]
    )


def _comparison_state(
    current: Mapping[str, object], comparison_reports: Sequence[Mapping[str, Any]] | None
) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
    """Validate and select the earliest report in a measured prior window."""
    if comparison_reports is None:
        return None, []
    if isinstance(comparison_reports, (str, bytes)) or not isinstance(comparison_reports, Sequence):
        raise ValueError("comparison_reports must be an ordered sequence of prior ScienceStatus reports")

    current_identity = current["identity"]
    prior_states = [_validated_state(report) for report in comparison_reports]
    for prior in prior_states:
        identity = prior["identity"]
        if not _same_target(identity, current_identity):
            raise ValueError("comparison reports must use the same schema_version, surface, and force")
        if int(identity["tick"]) >= int(current_identity["tick"]):
            raise ValueError("comparison report ticks must be earlier than the current report tick")

    ordered = sorted(prior_states, key=lambda item: int(item["identity"]["tick"]))
    ticks = [int(item["identity"]["tick"]) for item in ordered]
    if len(ticks) != len(set(ticks)):
        raise ValueError("comparison reports must have distinct ticks")
    return (ordered[0] if ordered else None), [item["identity"] for item in ordered]


def _result(
    diagnosis: str,
    planner_handoff: str,
    rationale: str,
    current_identity: Mapping[str, object],
    comparison_identities: Sequence[Mapping[str, object]],
    comparison: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": SCIENCE_DIAGNOSIS_VERSION,
        "diagnosis": diagnosis,
        "planner_handoff": planner_handoff,
        "rationale": rationale,
        "input_report_identities": [
            dict(identity) for identity in [*comparison_identities, current_identity]
        ],
        "comparison": dict(comparison),
    }


def diagnose_science_status(
    current_report: Mapping[str, Any],
    comparison_reports: Sequence[Mapping[str, Any]] | None = None,
    *,
    target_working_labs: int | None = None,
) -> dict[str, object]:
    """Diagnose the research state using a current report and prior window.

    ``comparison_reports`` is an optional ordered collection of reports from
    the same surface and force, each strictly earlier than ``current_report``.
    A window is *required* for the ``research_progress_stalled`` verdict;
    a one-report call may still safely identify missing research, power, or an
    explicit lab-capacity shortfall.

    ``target_working_labs`` is intentionally caller-owned.  It is never
    inferred from a fixed lab count, preserving the plan's separation between
    a hard observation and a goal-specific capacity preference.
    """
    current = _validated_state(current_report)
    if target_working_labs is not None:
        target_working_labs = _require_count(target_working_labs, "target_working_labs")

    comparison_start, comparison_identities = _comparison_state(current, comparison_reports)
    current_identity = current["identity"]
    comparison: dict[str, object] = {
        "start_tick": None,
        "end_tick": current_identity["tick"],
        "ticks_elapsed": None,
        "research_progress_delta": None,
    }
    if comparison_start is not None:
        start_identity = comparison_start["identity"]
        comparison["start_tick"] = start_identity["tick"]
        comparison["ticks_elapsed"] = int(current_identity["tick"]) - int(start_identity["tick"])
        if comparison_start["current"] == current["current"]:
            comparison["research_progress_delta"] = (
                float(current["progress"]) - float(comparison_start["progress"])
            )

    current_research = current["current"]
    if current_research is None:
        return _result(
            RESEARCH_NOT_SELECTED,
            "request_authorized_selection",
            "No current research is selected for the observed force.",
            current_identity,
            comparison_identities,
            comparison,
        )

    required_packs = current["required_packs"]
    input_inventory = current["input_inventory"]
    pack_names = sorted(required_packs)
    present_pack_names = [name for name in pack_names if input_inventory.get(name, 0) > 0]
    all_required_packs_present = bool(pack_names) and len(present_pack_names) == len(pack_names)
    zero_required_packs_present = bool(pack_names) and not present_pack_names
    working_labs = int(current["working_labs"])
    no_power_labs = int(current["status_counts"].get("no_power", 0))

    if zero_required_packs_present and working_labs == 0:
        return _result(
            SCIENCE_DELIVERY_OR_SUPPLY_MISSING,
            "trace_science_supply_delivery_inserter",
            "No required science packs are buffered in labs and no lab is working.",
            current_identity,
            comparison_identities,
            comparison,
        )

    if present_pack_names and no_power_labs > 0:
        return _result(
            LAB_POWER_LIMITED,
            "run_power_diagnosis",
            f"{no_power_labs} lab(s) report no_power while required science packs are buffered.",
            current_identity,
            comparison_identities,
            comparison,
        )

    progress_delta = comparison["research_progress_delta"]
    if (
        all_required_packs_present
        and working_labs > 0
        and progress_delta is not None
        and float(progress_delta) == 0.0
    ):
        return _result(
            RESEARCH_PROGRESS_STALLED,
            "reread_research_state",
            "Required science packs and working labs persisted across the comparison window, "
            "but research progress did not advance.",
            current_identity,
            comparison_identities,
            comparison,
        )

    if target_working_labs is not None and working_labs < target_working_labs:
        return _result(
            LAB_CAPACITY_LIMITED,
            "propose_validated_lab_capacity",
            f"{working_labs} working lab(s) are below the explicit target of {target_working_labs}.",
            current_identity,
            comparison_identities,
            comparison,
        )

    if progress_delta is not None and float(progress_delta) > 0.0:
        return _result(
            RESEARCH_PROGRESSING,
            "measure_consumption_rate",
            f"Research progress advanced by {float(progress_delta):.6f} over "
            f"{comparison['ticks_elapsed']} ticks.",
            current_identity,
            comparison_identities,
            comparison,
        )

    return _result(
        SCIENCE_OBSERVATION_INCONCLUSIVE,
        "collect_comparison_window",
        "The available read-only lab observations do not yet establish a ranked research fault.",
        current_identity,
        comparison_identities,
        comparison,
    )
