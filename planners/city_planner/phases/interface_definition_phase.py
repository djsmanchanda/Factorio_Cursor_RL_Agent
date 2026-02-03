# Path: planners/city_planner/phases/interface_definition_phase.py
# Purpose: Produce a read-only interface definition phase result.

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


def _find_transport_decision(prior_results: List[dict]) -> Optional[str]:
    for result in prior_results:
        if result.get("phase") == "transport_strategy_selection":
            return result.get("decision")
    return None


def evaluate_interface_definition(
    phase_entry: dict,
    capability_resolution: dict,
    prior_phase_results: List[dict],
    metrics: Optional[dict] = None,
    schema_path: Optional[Path] = None,
) -> PhaseResult:
    scope = capability_resolution.get("scope", "local")
    available = set(capability_resolution.get("available", []))

    transport_decision = _find_transport_decision(prior_phase_results)
    if transport_decision is None:
        raise ValueError("Missing transport_strategy_selection phase result")

    decision = "belt_only_interfaces"
    alternatives: List[str] = []
    rationale: List[str] = []
    constraints: List[str] = []

    if transport_decision == "rail_preferred":
        if "rail_corridor_planning" not in available:
            raise ValueError("Missing capability: rail_corridor_planning")

        decision = "station_based_interfaces"
        alternatives = ["hybrid_interfaces"]
        rationale.extend(["rail_preferred_transport", "city_scope"])
        constraints.extend(
            [
                "no_mainline_stations",
                "station_on_siding_only",
                "fixed_train_length",
                "no_cross_block_belts",
                "no_cross_block_bots",
            ]
        )
    elif transport_decision == "belt_preferred" and scope in {"local", "block"}:
        decision = "belt_only_interfaces"
        alternatives = ["hybrid_interfaces"]
        rationale.extend(["belt_preferred_transport", f"{scope}_scope"])
        constraints.extend(["limited_distance_only", "no_cross_block_rails"])
    elif transport_decision == "belt_preferred" and scope == "city":
        decision = "hybrid_interfaces"
        alternatives = ["belt_only_interfaces"]
        rationale.extend(["belt_preferred_transport", "city_scope"])
        constraints.extend(["no_cross_block_belts", "station_required_for_long_distance", "belts_internal_only"])
    else:
        decision = "belt_only_interfaces"
        alternatives = ["hybrid_interfaces"]
        rationale.append("transport_strategy_unrecognized")

    if metrics and metrics.get("bot_density_high"):
        rationale.append("bot_density_high")

    constraints.extend(list(phase_entry.get("constraints", [])))

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
