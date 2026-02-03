# Path: planners/city_planner/phases/transport_strategy_phase.py
# Purpose: Produce a read-only transport strategy phase result.

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class PhaseResult:
    phase: str
    decision: str
    alternatives: List[str] = field(default_factory=list)
    rationale: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "decision": self.decision,
            "alternatives": list(self.alternatives),
            "rationale": list(self.rationale),
            "constraints": list(self.constraints),
        }


def _load_schema(schema_path: Path) -> dict:
    with schema_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_phase_result(result: PhaseResult, schema_path: Path) -> None:
    schema = _load_schema(schema_path)
    validator = Draft7Validator(schema)
    errors = list(validator.iter_errors(result.to_dict()))
    if errors:
        messages = []
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {path}: {error.message}")
        raise ValueError("Phase result validation FAILED:\n" + "\n".join(messages))


def evaluate_transport_strategy(
    phase_entry: dict,
    capability_resolution: dict,
    metrics: Optional[dict] = None,
    schema_path: Optional[Path] = None,
) -> PhaseResult:
    scope = capability_resolution.get("scope", "local")
    available = set(capability_resolution.get("available", []))
    blocked = set(capability_resolution.get("blocked", []))

    decision = "belt_backbone"
    alternatives: List[str] = []
    rationale: List[str] = []

    if scope == "city" and "rail_corridor_planning" in available:
        decision = "rail_preferred"
        alternatives = ["belt_backbone"]
        rationale.append("city_scope")
        rationale.append("rail_capability_available")
    else:
        decision = "belt_backbone"
        alternatives = ["rail_preferred"] if "rail_corridor_planning" not in blocked else []
        rationale.append("rail_capability_unavailable" if "rail_corridor_planning" in blocked else "local_scope")

    if metrics and metrics.get("bot_density_high"):
        rationale.append("bot_density_high")

    constraints = list(phase_entry.get("constraints", []))

    result = PhaseResult(
        phase=phase_entry.get("phase"),
        decision=decision,
        alternatives=alternatives,
        rationale=rationale,
        constraints=constraints,
    )

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[3]
        schema_path = repo_root / "schemas" / "phase_result.schema.json"

    _validate_phase_result(result, schema_path)
    return result
