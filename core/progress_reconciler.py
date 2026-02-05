# Path: core/progress_reconciler.py
# Purpose: Reconcile observed ghost state with planned ProgressState deterministically.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

from jsonschema import Draft7Validator, RefResolver

from core.progress_state import DEFAULT_PHASE_CAPACITIES, ProgressState, compute_completed_phases, validate_progress_state


@dataclass(frozen=True)
class ProgressReconciliation:
    status: str
    reason: str
    progress_state: ProgressState

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "progress_state": self.progress_state.to_dict(),
        }


def _load_schema(schema_path: Path) -> dict:
    with schema_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_payload(payload: dict, schema_path: Path, label: str) -> None:
    schema = _load_schema(schema_path)
    resolver = RefResolver(base_uri=schema_path.parent.as_uri() + "/", referrer=schema)
    validator = Draft7Validator(schema, resolver=resolver)
    errors = list(validator.iter_errors(payload))
    if errors:
        messages = []
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {path}: {error.message}")
        raise ValueError(f"{label} validation FAILED:\n" + "\n".join(messages))


def _derive_active_phase(phase_targets: List[int], current: int, committed: int, ultimate: int) -> int:
    target = max(current, committed)
    for phase in phase_targets:
        if phase >= target:
            return phase
    return ultimate


def reconcile_progress_state(
    progress_state: dict,
    ghost_observation: dict,
    progress_schema_path: Path | None = None,
    observation_schema_path: Path | None = None,
    reconciliation_schema_path: Path | None = None,
) -> ProgressReconciliation:
    repo_root = Path(__file__).resolve().parents[1]

    if progress_schema_path is None:
        progress_schema_path = repo_root / "schemas" / "progress_state.schema.json"
    if observation_schema_path is None:
        observation_schema_path = repo_root / "schemas" / "ghost_observation.schema.json"
    if reconciliation_schema_path is None:
        reconciliation_schema_path = repo_root / "schemas" / "progress_reconciliation.schema.json"

    validate_progress_state(progress_state, progress_schema_path)
    _validate_payload(ghost_observation, observation_schema_path, "GhostObservation")

    observed_committed = len(ghost_observation.get("ghosts", []))

    previous_committed = int(progress_state["committed_capacity"])
    previous_active = int(progress_state["active_phase_capacity"])
    current_capacity = int(progress_state["current_capacity"])
    ultimate_capacity = int(progress_state["ultimate_capacity"])

    if observed_committed < previous_committed:
        raise ValueError("Committed capacity regression detected")
    if observed_committed > ultimate_capacity:
        raise ValueError("Observed committed capacity exceeds ultimate capacity")
    if current_capacity > previous_active:
        raise ValueError("Current capacity exceeds active phase capacity")
    if observed_committed > previous_active:
        raise ValueError("Observed committed capacity exceeds active phase capacity")

    derived_phase = _derive_active_phase(
        DEFAULT_PHASE_CAPACITIES,
        current_capacity,
        observed_committed,
        ultimate_capacity,
    )

    if derived_phase < previous_active:
        raise ValueError("Derived phase regressed below previous active phase")

    if derived_phase > previous_active:
        status = "PARTIAL"
        reason = "phase_advance_required"
        active_phase_capacity = previous_active
    elif observed_committed < previous_active:
        status = "PARTIAL"
        reason = "phase_incomplete"
        active_phase_capacity = previous_active
    else:
        status = "OK"
        reason = "reconciled"
        active_phase_capacity = previous_active

    completed_phases = compute_completed_phases(DEFAULT_PHASE_CAPACITIES, current_capacity)

    reconciled_state = ProgressState(
        ultimate_capacity=ultimate_capacity,
        current_capacity=current_capacity,
        committed_capacity=observed_committed,
        active_phase_capacity=active_phase_capacity,
        completed_phases=completed_phases,
        rationale=progress_state.get("rationale", "reconciled"),
    )

    reconciliation = ProgressReconciliation(
        status=status,
        reason=reason,
        progress_state=reconciled_state,
    )

    _validate_payload(reconciliation.to_dict(), reconciliation_schema_path, "ProgressReconciliation")
    return reconciliation
