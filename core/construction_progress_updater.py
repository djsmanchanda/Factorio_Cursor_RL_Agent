# Path: core/construction_progress_updater.py
# Purpose: Update ProgressState from construction reports deterministically.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft7Validator

from core.progress_state import DEFAULT_PHASE_CAPACITIES, ProgressState, compute_completed_phases, validate_progress_state


@dataclass(frozen=True)
class ConstructionProgress:
    status: str
    reason: str
    started: int
    completed: int
    previous_current_capacity: int
    updated_current_capacity: int

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "started": self.started,
            "completed": self.completed,
            "previous_current_capacity": self.previous_current_capacity,
            "updated_current_capacity": self.updated_current_capacity,
        }


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_schema(payload: dict, schema_path: Path, label: str) -> None:
    schema = _load_json(schema_path)
    validator = Draft7Validator(schema)
    errors = list(validator.iter_errors(payload))
    if errors:
        messages = []
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {path}: {error.message}")
        raise ValueError(f"{label} validation FAILED:\n" + "\n".join(messages))


def _validate_construction_report(payload: dict, schema_path: Path) -> None:
    _validate_schema(payload, schema_path, "ConstructionReport")


def update_progress_from_construction(
    progress_state: dict,
    construction_report: dict,
    progress_schema_path: Path | None = None,
    report_schema_path: Path | None = None,
    progress_update_schema_path: Path | None = None,
) -> tuple[ConstructionProgress, ProgressState]:
    repo_root = Path(__file__).resolve().parents[1]

    if progress_schema_path is None:
        progress_schema_path = repo_root / "schemas" / "progress_state.schema.json"
    if report_schema_path is None:
        report_schema_path = repo_root / "schemas" / "construction_report.schema.json"
    if progress_update_schema_path is None:
        progress_update_schema_path = repo_root / "schemas" / "construction_progress.schema.json"

    validate_progress_state(progress_state, progress_schema_path)
    _validate_construction_report(construction_report, report_schema_path)

    if construction_report.get("surface") != "planner-sandbox":
        raise ValueError("ConstructionReport surface must be planner-sandbox")

    if construction_report.get("blocked"):
        reason = construction_report.get("blocked_reason", "blocked")
        progress = ConstructionProgress(
            status="BLOCKED",
            reason=reason,
            started=int(construction_report.get("started", 0)),
            completed=int(construction_report.get("completed", 0)),
            previous_current_capacity=int(progress_state["current_capacity"]),
            updated_current_capacity=int(progress_state["current_capacity"]),
        )
        _validate_schema(progress.to_dict(), progress_update_schema_path, "ConstructionProgress")
        return progress, ProgressState(**progress_state)

    started = int(construction_report.get("started", 0))
    completed = int(construction_report.get("completed", 0))
    if completed > started:
        raise ValueError("ConstructionReport completed cannot exceed started")

    previous_current = int(progress_state["current_capacity"])
    committed_capacity = int(progress_state["committed_capacity"])
    active_phase_capacity = int(progress_state["active_phase_capacity"])
    ultimate_capacity = int(progress_state["ultimate_capacity"])

    updated_current = previous_current + completed

    if updated_current < previous_current:
        raise ValueError("Current capacity regression detected")
    if updated_current > committed_capacity:
        raise ValueError("Updated current capacity exceeds committed capacity")
    if updated_current > active_phase_capacity:
        raise ValueError("Updated current capacity exceeds active phase capacity")
    if updated_current > ultimate_capacity:
        raise ValueError("Updated current capacity exceeds ultimate capacity")

    if completed == started:
        status = "OK"
        reason = "construction_complete"
    else:
        status = "PARTIAL"
        reason = "construction_incomplete"

    progress_update = ConstructionProgress(
        status=status,
        reason=reason,
        started=started,
        completed=completed,
        previous_current_capacity=previous_current,
        updated_current_capacity=updated_current,
    )

    _validate_schema(progress_update.to_dict(), progress_update_schema_path, "ConstructionProgress")

    completed_phases = compute_completed_phases(DEFAULT_PHASE_CAPACITIES, updated_current)

    updated_progress = ProgressState(
        ultimate_capacity=ultimate_capacity,
        current_capacity=updated_current,
        committed_capacity=committed_capacity,
        active_phase_capacity=active_phase_capacity,
        completed_phases=completed_phases,
        rationale=progress_state.get("rationale", "construction_update"),
    )

    validate_progress_state(updated_progress.to_dict(), progress_schema_path)

    return progress_update, updated_progress
