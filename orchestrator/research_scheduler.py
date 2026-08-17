# Path: orchestrator/research_scheduler.py
# Purpose: Pure, allow-list-bounded research candidate ranking from saved observations.

"""Rank an explicitly finite set of technology candidates without mutation.

The scheduler has no technology-tree traversal, RCON access, or hidden
priority list.  Every benefit and cost term comes from the caller's
goal-derived state.  It only returns a proposed selection; a separate,
authorized actuator remains responsible for changing Factorio research.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


RESEARCH_SELECTION_VERSION = "1.0.0"
SELECTION_ALLOWED_DIAGNOSIS = "research_not_selected"


def _require_finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _require_item_count_map(value: object, label: str) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    normalized: dict[str, int] = {}
    for item, count in value.items():
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} item names must be non-empty strings")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"{label}[{item!r}] must be a non-negative integer")
        normalized[item] = count
    return normalized


def _normalize_allow_list(allow_list: object) -> tuple[str, ...]:
    # Reject iterators: a caller must make the bounded candidate set explicit,
    # rather than handing a potentially unbounded technology traversal to this
    # pure ranker.
    if not isinstance(allow_list, (list, tuple, set, frozenset)):
        raise ValueError("allow_list must be a finite list, tuple, set, or frozenset")
    candidates = list(allow_list)
    if not candidates:
        raise ValueError("allow_list must contain at least one technology")
    if any(not isinstance(name, str) or not name.strip() for name in candidates):
        raise ValueError("allow_list entries must be non-empty strings")
    if len(candidates) != len(set(candidates)):
        raise ValueError("allow_list must not contain duplicate technologies")
    return tuple(sorted(candidates))


def _identity_key(identity: Mapping[str, object]) -> tuple[str, str, str, int]:
    return (
        str(identity["schema_version"]),
        str(identity["surface"]),
        str(identity["force"]),
        int(identity["tick"]),
    )


def _normalize_identity(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    required = {"schema_version", "tick", "surface", "force"}
    if set(value) != required:
        raise ValueError(f"{label} must contain exactly schema_version, tick, surface, and force")
    schema_version = value["schema_version"]
    surface = value["surface"]
    force = value["force"]
    tick = value["tick"]
    if not isinstance(schema_version, str) or not schema_version:
        raise ValueError(f"{label}.schema_version must be a non-empty string")
    if not isinstance(surface, str) or not surface:
        raise ValueError(f"{label}.surface must be a non-empty string")
    if not isinstance(force, str) or not force:
        raise ValueError(f"{label}.force must be a non-empty string")
    if isinstance(tick, bool) or not isinstance(tick, int) or tick < 0:
        raise ValueError(f"{label}.tick must be a non-negative integer")
    return {"schema_version": schema_version, "tick": tick, "surface": surface, "force": force}


def _input_report_identities(
    diagnosis: Mapping[str, Any], candidate_states: Mapping[str, Any]
) -> list[dict[str, object]]:
    raw_identities = diagnosis.get("input_report_identities", [])
    if not isinstance(raw_identities, list):
        raise ValueError("diagnosis.input_report_identities must be an array")
    identities = [
        _normalize_identity(identity, "diagnosis.input_report_identities entry")
        for identity in raw_identities
    ]
    for technology in sorted(candidate_states):
        state = candidate_states[technology]
        if not isinstance(state, Mapping):
            continue
        identity = state.get("input_report_identity", state.get("report_identity"))
        if identity is not None:
            identities.append(_normalize_identity(identity, f"state[{technology!r}] report identity"))

    deduplicated: dict[tuple[str, str, str, int], dict[str, object]] = {}
    for identity in identities:
        deduplicated[_identity_key(identity)] = identity
    return [deduplicated[key] for key in sorted(deduplicated)]


def _score_components(state: Mapping[str, Any], technology: str) -> dict[str, float]:
    required_packs = _require_item_count_map(
        state.get("required_science_packs", {}), f"state[{technology!r}].required_science_packs"
    )
    required_pack_total = float(sum(required_packs.values()))
    goal_unlock_value = _require_finite_number(
        state.get("goal_unlock_value", 0.0), f"state[{technology!r}].goal_unlock_value"
    )
    prerequisite_value = _require_finite_number(
        state.get("prerequisite_value", 0.0), f"state[{technology!r}].prerequisite_value"
    )
    supply_feasibility = _require_finite_number(
        state.get("supply_feasibility", 0.0), f"state[{technology!r}].supply_feasibility"
    )
    if not 0.0 <= supply_feasibility <= 1.0:
        raise ValueError(f"state[{technology!r}].supply_feasibility must be between 0 and 1")
    opportunity_cost = _require_finite_number(
        state.get("opportunity_cost", 0.0), f"state[{technology!r}].opportunity_cost"
    )
    if opportunity_cost < 0.0:
        raise ValueError(f"state[{technology!r}].opportunity_cost must not be negative")

    components = {
        "goal_unlock_value": goal_unlock_value,
        "prerequisite_value": prerequisite_value,
        "required_science_pack_total": -required_pack_total,
        "supply_feasibility": supply_feasibility,
        "opportunity_cost": -opportunity_cost,
    }
    components["total"] = sum(components.values())
    return components


def _eligibility_reasons(state: object) -> tuple[Mapping[str, Any] | None, list[str]]:
    if not isinstance(state, Mapping):
        return None, ["missing_force_state"]
    reasons: list[str] = []
    if state.get("enabled") is not True:
        reasons.append("disabled")
    if state.get("researched") is True:
        reasons.append("already_researched")
    if state.get("observed") is not True:
        reasons.append("unobserved")
    return state, reasons


def rank_research_candidates(
    allow_list: object,
    force_technology_state: Mapping[str, Any],
    latest_diagnosis: Mapping[str, Any],
) -> dict[str, object]:
    """Return a deterministic research-selection proposal or a safe deferral.

    ``allow_list`` is finite and goal-derived. ``force_technology_state`` is
    caller-supplied state for technology names, with each candidate declaring
    ``enabled``, ``researched``, and ``observed`` plus score inputs.  The
    scheduler deliberately selects only after ``research_not_selected``;
    faults on an active research item call for repair or observation, not a
    silent research switch.
    """
    allowed = _normalize_allow_list(allow_list)
    if not isinstance(force_technology_state, Mapping):
        raise ValueError("force_technology_state must be an object")
    if not isinstance(latest_diagnosis, Mapping):
        raise ValueError("latest_diagnosis must be an object")
    diagnosis_name = latest_diagnosis.get("diagnosis")
    if not isinstance(diagnosis_name, str) or not diagnosis_name:
        raise ValueError("latest_diagnosis.diagnosis must be a non-empty string")
    report_identities = _input_report_identities(latest_diagnosis, force_technology_state)

    rejections: dict[str, list[str]] = {}
    components_by_technology: dict[str, dict[str, float]] = {}
    eligible: list[tuple[float, str]] = []
    known_technologies = sorted(set(allowed).union(force_technology_state))
    allowed_set = set(allowed)

    for technology in known_technologies:
        if technology not in allowed_set:
            rejections[technology] = ["outside_allow_list"]
            continue
        state, reasons = _eligibility_reasons(force_technology_state.get(technology))
        if reasons:
            rejections[technology] = reasons
            continue
        assert state is not None
        components = _score_components(state, technology)
        components_by_technology[technology] = components
        eligible.append((components["total"], technology))

    if diagnosis_name != SELECTION_ALLOWED_DIAGNOSIS:
        for _score, technology in eligible:
            rejections.setdefault(technology, []).append("selection_deferred_by_diagnosis")
        eligible = []

    selected: str | None = None
    if eligible:
        # Sort name ascending after score descending: ties have a stable,
        # transparent outcome without encoding a technology priority order.
        selected = sorted(eligible, key=lambda item: (-item[0], item[1]))[0][1]

    status = "selected" if selected is not None else "no_eligible_candidate"
    return {
        "schema_version": RESEARCH_SELECTION_VERSION,
        "status": status,
        "selected_technology": selected,
        "diagnosis": diagnosis_name,
        "score_components": {
            technology: components_by_technology[technology]
            for technology in sorted(components_by_technology)
        },
        "rejections": {technology: rejections[technology] for technology in sorted(rejections)},
        "input_report_identities": report_identities,
    }
