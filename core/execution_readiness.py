# Path: core/execution_readiness.py
# Purpose: Propose permitted next actions based on reconciled progress.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class ExecutionProposal:
    allowed_actions: List[str]
    blocked_actions: List[dict]
    requires_human_approval: bool
    next_recommended_step: str

    def to_dict(self) -> dict:
        return {
            "allowed_actions": list(self.allowed_actions),
            "blocked_actions": list(self.blocked_actions),
            "requires_human_approval": self.requires_human_approval,
            "next_recommended_step": self.next_recommended_step,
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


def _validate_inputs(progress_state: dict, capacity_phasing: dict, build_intent: dict) -> None:
    required_progress = {"ultimate_capacity", "current_capacity", "committed_capacity", "active_phase_capacity"}
    if not required_progress.issubset(progress_state.keys()):
        raise ValueError("ProgressState missing required fields")

    required_phasing = {"desired_active_capacity", "previous_active_capacity", "reason", "next_allowed_actions"}
    if not required_phasing.issubset(capacity_phasing.keys()):
        raise ValueError("CapacityPhasing missing required fields")

    if "intents" not in build_intent:
        raise ValueError("BuildIntent missing intents")


def propose_execution(
    progress_state: dict,
    capacity_phasing: dict,
    build_intent: dict,
    reconciliation_status: str,
    schema_path: Path | None = None,
) -> ExecutionProposal:
    _validate_inputs(progress_state, capacity_phasing, build_intent)

    allowed_actions: List[str] = []
    blocked_actions: List[dict] = []

    if reconciliation_status == "BLOCKED":
        blocked_actions = [
            {"action": "project_more_ghosts", "reason": "reconciliation_blocked"},
            {"action": "request_phase_advance", "reason": "reconciliation_blocked"},
            {"action": "request_module_upgrade", "reason": "reconciliation_blocked"},
        ]
        proposal = ExecutionProposal(
            allowed_actions=["hold_position"],
            blocked_actions=blocked_actions,
            requires_human_approval=True,
            next_recommended_step="hold_position",
        )
    elif reconciliation_status == "PARTIAL":
        allowed_actions = ["hold_position"]
        blocked_actions = [
            {"action": "project_more_ghosts", "reason": "reconciliation_partial"},
            {"action": "request_phase_advance", "reason": "reconciliation_partial"},
            {"action": "request_module_upgrade", "reason": "reconciliation_partial"},
        ]
        proposal = ExecutionProposal(
            allowed_actions=allowed_actions,
            blocked_actions=blocked_actions,
            requires_human_approval=True,
            next_recommended_step="hold_position",
        )
    elif reconciliation_status == "OK":
        desired = int(capacity_phasing["desired_active_capacity"])
        previous = int(capacity_phasing["previous_active_capacity"])
        if desired > previous:
            allowed_actions = ["project_more_ghosts", "request_phase_advance"]
            proposal = ExecutionProposal(
                allowed_actions=allowed_actions,
                blocked_actions=[],
                requires_human_approval=True,
                next_recommended_step="request_phase_advance",
            )
        else:
            allowed_actions = ["hold_position"]
            proposal = ExecutionProposal(
                allowed_actions=allowed_actions,
                blocked_actions=[
                    {"action": "project_more_ghosts", "reason": "phase_complete_or_hold"},
                    {"action": "request_phase_advance", "reason": "phase_complete_or_hold"},
                ],
                requires_human_approval=True,
                next_recommended_step="hold_position",
            )
    else:
        raise ValueError(f"Unknown reconciliation status: {reconciliation_status}")

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "execution_proposal.schema.json"
    _validate_schema(proposal.to_dict(), schema_path, "ExecutionProposal")

    return proposal
