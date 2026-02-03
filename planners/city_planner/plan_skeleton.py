# Path: planners/city_planner/plan_skeleton.py
# Purpose: Generate a validated plan skeleton after readiness is confirmed.

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class PlanPhase:
    phase: str
    requires_capabilities: List[str]
    constraints: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "requires_capabilities": list(self.requires_capabilities),
            "constraints": list(self.constraints),
        }


@dataclass(frozen=True)
class PlanSkeleton:
    intent: str
    scope: str
    phases: List[PlanPhase]
    explicitly_not_doing: List[str] = field(default_factory=list)
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        payload = {
            "intent": self.intent,
            "scope": self.scope,
            "phases": [phase.to_dict() for phase in self.phases],
            "explicitly_not_doing": list(self.explicitly_not_doing),
        }
        if self.notes is not None:
            payload["notes"] = self.notes
        return payload


_PHASES_BY_INTENT: Dict[str, List[PlanPhase]] = {
    "prepare_transport_migration": [
        PlanPhase(
            phase="transport_strategy_selection",
            requires_capabilities=["rail_corridor_planning"],
            constraints=["no_existing_blocks_modified"],
        ),
        PlanPhase(
            phase="interface_definition",
            requires_capabilities=["station_interface_definition"],
            constraints=["no_throughput_reduction"],
        ),
        PlanPhase(
            phase="block_boundary_definition",
            requires_capabilities=["block_planning"],
            constraints=[],
        ),
        PlanPhase(
            phase="block_topology_planning",
            requires_capabilities=["block_planning", "dependency_graph_planning"],
            constraints=[],
        ),
        PlanPhase(
            phase="migration_planning",
            requires_capabilities=["block_level_dependency_analysis"],
            constraints=["shadow_blocks_only"],
        ),
    ],
    "reduce_bot_dependency": [
        PlanPhase(
            phase="transport_strategy_selection",
            requires_capabilities=["belt_backbone_planning"],
            constraints=["no_existing_blocks_modified"],
        ),
    ],
}


_EXPLICITLY_NOT_DOING = ["placing_blocks", "placing_rails", "issuing_construction_orders"]


def _load_schema(schema_path: Path) -> dict:
    with schema_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_skeleton(skeleton: PlanSkeleton, schema_path: Path) -> None:
    schema = _load_schema(schema_path)
    validator = Draft7Validator(schema)
    errors = list(validator.iter_errors(skeleton.to_dict()))
    if errors:
        messages = []
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {path}: {error.message}")
        raise ValueError("Plan skeleton validation FAILED:\n" + "\n".join(messages))


def generate_plan_skeleton(
    intent: dict,
    resolution: dict,
    gate_decision: dict,
    schema_path: Optional[Path] = None,
) -> PlanSkeleton:
    status = gate_decision.get("status")
    if status != "ready":
        reason = gate_decision.get("reason", "not_ready")
        raise ValueError(f"Planning gate not ready: {reason}")

    intent_name = intent.get("intent")
    scope = intent.get("scope")

    phases = _PHASES_BY_INTENT.get(intent_name, [])
    skeleton = PlanSkeleton(
        intent=intent_name,
        scope=scope,
        phases=phases,
        explicitly_not_doing=list(_EXPLICITLY_NOT_DOING),
        notes="Skeleton only; no geometry or blueprints selected",
    )

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[2]
        schema_path = repo_root / "schemas" / "plan_skeleton.schema.json"

    _validate_skeleton(skeleton, schema_path)
    return skeleton
