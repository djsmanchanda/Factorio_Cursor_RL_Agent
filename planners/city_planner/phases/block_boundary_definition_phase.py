# Path: planners/city_planner/phases/block_boundary_definition_phase.py
# Purpose: Produce a read-only block boundary definition phase result.

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


def _find_phase_decision(prior_results: List[dict], phase_name: str) -> Optional[str]:
    for result in prior_results:
        if result.get("phase") == phase_name:
            return result.get("decision")
    return None


def evaluate_block_boundary_definition(
    phase_entry: dict,
    capability_resolution: dict,
    prior_phase_results: List[dict],
    metrics: Optional[dict] = None,
    schema_path: Optional[Path] = None,
) -> PhaseResult:
    scope = capability_resolution.get("scope", "local")
    available = set(capability_resolution.get("available", []))

    transport_decision = _find_phase_decision(prior_phase_results, "transport_strategy_selection")
    interface_decision = _find_phase_decision(prior_phase_results, "interface_definition")

    if transport_decision is None:
        raise ValueError("Missing transport_strategy_selection phase result")
    if interface_decision is None:
        raise ValueError("Missing interface_definition phase result")

    decision = "monolithic_layout"
    alternatives: List[str] = []
    rationale: List[str] = []
    constraints: List[str] = []

    if transport_decision == "rail_preferred" and interface_decision == "station_based_interfaces":
        if "block_planning" not in available:
            raise ValueError("Missing capability: block_planning")

        decision = "strict_block_isolation"
        alternatives = ["soft_block_isolation"]
        rationale.extend(["rail_preferred_transport", "station_based_interfaces"])
        constraints.extend(
            [
                "no_cross_block_belts",
                "no_cross_block_bots",
                "rail_only_inter_block",
                "blocks_immutable",
                "shadow_blocks_only",
            ]
        )
    elif interface_decision == "hybrid_interfaces":
        if "block_planning" not in available:
            raise ValueError("Missing capability: block_planning")

        decision = "soft_block_isolation"
        alternatives = ["strict_block_isolation"]
        rationale.append("hybrid_interfaces")
        constraints.extend(
            [
                "rail_only_long_distance",
                "block_internal_bots_allowed",
                "no_cross_block_mainline_belts",
            ]
        )
    elif interface_decision == "belt_only_interfaces" and scope == "local":
        decision = "monolithic_layout"
        alternatives = []
        rationale.extend(["belt_only_interfaces", "local_scope"])
        constraints.append("organic_growth_allowed")
    else:
        decision = "monolithic_layout"
        alternatives = []
        rationale.append("interface_scope_unrecognized")

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
