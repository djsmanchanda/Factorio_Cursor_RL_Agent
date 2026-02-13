# Path: core/construction_pressure_policy.py
# Purpose: Deterministically derive construction pressure from ProgressState backlog.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class ConstructionPressureSignal:
    backlog_ratio: float
    pressure_level: str
    expansion_damping: float
    rationale: str

    def to_dict(self) -> dict:
        return {
            "backlog_ratio": float(self.backlog_ratio),
            "pressure_level": self.pressure_level,
            "expansion_damping": float(self.expansion_damping),
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
            loc = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {loc}: {error.message}")
        raise ValueError(f"{label} validation FAILED:\n" + "\n".join(messages))


def compute_construction_pressure_signal(
    progress_state: dict,
    schema_path: Path | None = None,
) -> ConstructionPressureSignal:
    required = {"committed_capacity", "current_capacity", "active_phase_capacity"}
    missing = sorted(required.difference(progress_state.keys()))
    if missing:
        raise ValueError(f"ProgressState missing required fields for construction pressure: {missing}")

    committed = int(progress_state["committed_capacity"])
    current = int(progress_state["current_capacity"])
    active_phase = int(progress_state["active_phase_capacity"])
    if active_phase == 0:
        raise ValueError("active_phase_capacity must be non-zero for construction pressure")
    if committed < 0 or current < 0 or active_phase < 0:
        raise ValueError("ProgressState capacities must be non-negative for construction pressure")

    backlog = committed - current
    backlog_ratio = min(1.0, max(0.0, float(backlog) / float(active_phase)))

    if backlog_ratio < 0.15:
        level, damping = "LOW", 0.0
    elif backlog_ratio <= 0.40:
        level, damping = "MEDIUM", 0.20
    else:
        level, damping = "HIGH", 0.50

    signal = ConstructionPressureSignal(
        backlog_ratio=float(round(backlog_ratio, 6)),
        pressure_level=level,
        expansion_damping=damping,
        rationale=(
            f"Backlog ratio {backlog_ratio:.3f} from "
            f"(committed-current)/active_phase mapped to {level} with damping {damping:.2f}."
        ),
    )

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "construction_pressure.schema.json"
    _validate_schema(signal.to_dict(), schema_path, "ConstructionPressureSignal")
    return signal
