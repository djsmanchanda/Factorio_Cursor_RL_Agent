# Path: planners/city_planner/phases/block_topology_planning_phase.py
# Purpose: Produce a read-only block topology planning phase result.

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class PhaseResult:
    phase: str
    decision: str
    alternatives: List[str] = field(default_factory=list)
    rationale: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    topology: Optional[dict] = None

    def to_dict(self) -> dict:
        payload = {
            "phase": self.phase,
            "decision": self.decision,
            "alternatives": list(self.alternatives),
            "rationale": list(self.rationale),
            "constraints": list(self.constraints),
        }
        if self.topology is not None:
            payload["topology"] = self.topology
        return payload


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


def _build_topology_from_metrics(metrics: dict) -> Dict[str, dict]:
    required_keys = {"labs_count", "smelters_present", "circuits_present"}
    missing = required_keys.difference(metrics.keys())
    if missing:
        raise ValueError(f"Missing required metrics: {sorted(missing)}")

    blocks: Dict[str, int] = {}
    labs_count = metrics.get("labs_count", 0)
    smelters_present = metrics.get("smelters_present", False)
    circuits_present = metrics.get("circuits_present", False)

    if smelters_present:
        blocks["smelting"] = 1
    if circuits_present:
        blocks["circuits"] = 1
    if labs_count and int(labs_count) > 0:
        blocks["science"] = 1

    dependencies: List[List[str]] = []
    if "smelting" in blocks and "circuits" in blocks:
        dependencies.append(["smelting", "circuits"])
    if "circuits" in blocks and "science" in blocks:
        dependencies.append(["circuits", "science"])

    return {"blocks": blocks, "dependencies": dependencies}


def evaluate_block_topology_planning(
    phase_entry: dict,
    capability_resolution: dict,
    prior_phase_results: List[dict],
    metrics: Optional[dict] = None,
    schema_path: Optional[Path] = None,
) -> PhaseResult:
    scope = capability_resolution.get("scope", "local")
    available = set(capability_resolution.get("available", []))

    boundary_decision = _find_phase_decision(prior_phase_results, "block_boundary_definition")
    transport_decision = _find_phase_decision(prior_phase_results, "transport_strategy_selection")
    interface_decision = _find_phase_decision(prior_phase_results, "interface_definition")

    if boundary_decision is None:
        raise ValueError("Missing block_boundary_definition phase result")
        if transport_decision is None:
            raise ValueError("Missing transport_strategy_selection phase result")
        if interface_decision is None:
            raise ValueError("Missing interface_definition phase result")

    decision = "single_block"
    alternatives: List[str] = []
    rationale: List[str] = []
    constraints: List[str] = []
    topology: Optional[dict] = None

    if boundary_decision == "monolithic_layout":
        decision = "single_block"
        alternatives = []
        rationale.extend(["monolithic_layout"])
        topology = {"blocks": {"factory": 1}, "dependencies": []}
    elif boundary_decision == "strict_block_isolation":
        if "dependency_graph_planning" not in available:
            raise ValueError("Missing capability: dependency_graph_planning")
        if "block_planning" not in available:
            raise ValueError("Missing capability: block_planning")
        if metrics is None:
            raise ValueError("Missing metrics for block topology planning")

        decision = "multi_block_dag"
        alternatives = ["federated_blocks"]
        rationale.extend(
            [
                "strict_block_isolation",
                "rail_preferred_transport" if transport_decision == "rail_preferred" else "transport_decision_unknown",
                f"{scope}_scope",
            ]
        )
        constraints.extend(["no_cycles", "feed_forward_only", "no_mutual_dependencies"])
        topology = _build_topology_from_metrics(metrics)
    elif boundary_decision == "soft_block_isolation":
        if "dependency_graph_planning" not in available:
            raise ValueError("Missing capability: dependency_graph_planning")
        if "block_planning" not in available:
            raise ValueError("Missing capability: block_planning")
        if metrics is None:
            raise ValueError("Missing metrics for block topology planning")

        decision = "federated_blocks"
        alternatives = ["multi_block_dag"]
        rationale.extend(["soft_block_isolation", f"{scope}_scope"])
        constraints.extend(["core_blocks_immutable", "aux_blocks_mutable"])
        topology = _build_topology_from_metrics(metrics)
    else:
        raise ValueError(f"Unsupported block boundary decision: {boundary_decision}")

    if interface_decision == "station_based_interfaces":
        rationale.append("station_based_interfaces")

    constraints.extend(list(phase_entry.get("constraints", [])))

    result = PhaseResult(
        phase=phase_entry.get("phase"),
        decision=decision,
        alternatives=alternatives,
        rationale=rationale,
        constraints=constraints,
        topology=topology,
    )

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[3]
        schema_path = repo_root / "schemas" / "phase_result.schema.json"

    _validate_phase_result(result, schema_path)
    return result
