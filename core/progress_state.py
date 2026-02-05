# Path: core/progress_state.py
# Purpose: Derive a read-only Progress State from inputs and validate it.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

from jsonschema import Draft7Validator

from tools.validate_snapshot import validate_snapshot_file


DEFAULT_PHASE_CAPACITIES: List[int] = [50, 100, 250, 1000]


@dataclass(frozen=True)
class ProgressState:
    ultimate_capacity: int
    current_capacity: int
    committed_capacity: int
    active_phase_capacity: int
    completed_phases: List[int]
    rationale: str

    def to_dict(self) -> dict:
        return {
            "ultimate_capacity": self.ultimate_capacity,
            "current_capacity": self.current_capacity,
            "committed_capacity": self.committed_capacity,
            "active_phase_capacity": self.active_phase_capacity,
            "completed_phases": list(self.completed_phases),
            "rationale": self.rationale,
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


def validate_progress_state(payload: dict, schema_path: Path) -> None:
    _validate_schema(payload, schema_path, "ProgressState")


def _derive_ultimate_capacity(build_intent: dict) -> int:
    intents = build_intent.get("intents")
    if not intents:
        raise ValueError("BuildIntent must include at least one intent")
    counts = [int(intent.get("count", 0)) for intent in intents]
    if any(count <= 0 for count in counts):
        raise ValueError("BuildIntent counts must be positive integers")
    return sum(counts)


def _derive_committed_capacity(build_intent: dict) -> int:
    return _derive_ultimate_capacity(build_intent)


def _derive_current_capacity(metrics: dict) -> int:
    if "current_capacity" not in metrics:
        raise ValueError("Metrics must include current_capacity")
    value = int(metrics.get("current_capacity"))
    if value < 0:
        raise ValueError("current_capacity must be non-negative")
    return value


def _derive_active_phase(phase_targets: List[int], current: int, committed: int, ultimate: int) -> int:
    if not phase_targets:
        raise ValueError("Phase targets must not be empty")
    target = max(current, committed)
    for phase in phase_targets:
        if phase >= target:
            return phase
    return ultimate


def compute_completed_phases(phase_targets: List[int], current: int) -> List[int]:
    return sorted([phase for phase in phase_targets if phase <= current])


def build_progress_state(
    snapshot_path: Path,
    metrics_path: Path,
    build_intent_path: Path,
    progress_schema_path: Path | None = None,
    build_intent_schema_path: Path | None = None,
) -> ProgressState:
    repo_root = Path(__file__).resolve().parents[1]

    snapshot_schema = repo_root / "schemas" / "snapshot.schema.json"
    errors = validate_snapshot_file(snapshot_path, snapshot_schema)
    if errors:
        messages = []
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {path}: {error.message}")
        raise ValueError("Snapshot validation FAILED:\n" + "\n".join(messages))

    metrics = _load_json(metrics_path)
    build_intent = _load_json(build_intent_path)

    if build_intent_schema_path is None:
        build_intent_schema_path = repo_root / "schemas" / "build_intent.schema.json"
    _validate_schema(build_intent, build_intent_schema_path, "BuildIntent")

    ultimate_capacity = _derive_ultimate_capacity(build_intent)
    committed_capacity = _derive_committed_capacity(build_intent)
    current_capacity = _derive_current_capacity(metrics)

    active_phase_capacity = _derive_active_phase(
        DEFAULT_PHASE_CAPACITIES,
        current_capacity,
        committed_capacity,
        ultimate_capacity,
    )
    completed_phases = compute_completed_phases(DEFAULT_PHASE_CAPACITIES, current_capacity)

    rationale = (
        f"Active phase selected for target {max(current_capacity, committed_capacity)} "
        f"within ultimate capacity {ultimate_capacity}."
    )

    progress = ProgressState(
        ultimate_capacity=ultimate_capacity,
        current_capacity=current_capacity,
        committed_capacity=committed_capacity,
        active_phase_capacity=active_phase_capacity,
        completed_phases=completed_phases,
        rationale=rationale,
    )

    if progress_schema_path is None:
        progress_schema_path = repo_root / "schemas" / "progress_state.schema.json"
    _validate_schema(progress.to_dict(), progress_schema_path, "ProgressState")

    return progress
