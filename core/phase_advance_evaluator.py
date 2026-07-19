# Path: core/phase_advance_evaluator.py
# Purpose: Propose and authorize capacity phase advancement deterministically.

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft7Validator

from core.progress_state import DEFAULT_PHASE_CAPACITIES, compute_next_phase_capacity, validate_progress_state


@dataclass(frozen=True)
class PhaseAdvanceProposal:
    status: str
    reason: str
    current_phase: int
    next_phase: int

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "current_phase": self.current_phase,
            "next_phase": self.next_phase,
        }


@dataclass(frozen=True)
class PhaseAdvanceAuthorization:
    approved: bool
    reason: str
    authorization_timestamp: str
    authorization_source: str
    approved_next_phase: int | None = None

    def to_dict(self) -> dict:
        payload = {
            "approved": self.approved,
            "reason": self.reason,
            "authorization_timestamp": self.authorization_timestamp,
            "authorization_source": self.authorization_source,
        }
        if self.approved_next_phase is not None:
            payload["approved_next_phase"] = self.approved_next_phase
        return payload


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


def _next_phase(current_phase: int, ultimate: int) -> int:
    # The schedule's next threshold can overshoot a build intent whose
    # ultimate capacity ends mid-phase; the advance target is always capped.
    return min(compute_next_phase_capacity(DEFAULT_PHASE_CAPACITIES, current_phase, ultimate), ultimate)


def propose_phase_advance(
    progress_state: dict,
    construction_progress: dict,
    proposal_schema_path: Path | None = None,
) -> PhaseAdvanceProposal:
    repo_root = Path(__file__).resolve().parents[1]
    if proposal_schema_path is None:
        proposal_schema_path = repo_root / "schemas" / "phase_advance_proposal.schema.json"

    validate_progress_state(progress_state, repo_root / "schemas" / "progress_state.schema.json")

    if construction_progress.get("status") == "BLOCKED":
        proposal = PhaseAdvanceProposal(
            status="INELIGIBLE",
            reason="construction_blocked",
            current_phase=int(progress_state["active_phase_capacity"]),
            next_phase=int(progress_state["active_phase_capacity"]),
        )
        _validate_schema(proposal.to_dict(), proposal_schema_path, "PhaseAdvanceProposal")
        return proposal

    current_phase = int(progress_state["active_phase_capacity"])
    current_capacity = int(progress_state["current_capacity"])
    ultimate_capacity = int(progress_state["ultimate_capacity"])

    next_phase = _next_phase(current_phase, ultimate_capacity)

    if current_capacity < current_phase:
        proposal = PhaseAdvanceProposal(
            status="INELIGIBLE",
            reason="phase_incomplete",
            current_phase=current_phase,
            next_phase=next_phase,
        )
    else:
        proposal = PhaseAdvanceProposal(
            status="ELIGIBLE",
            reason="phase_complete",
            current_phase=current_phase,
            next_phase=next_phase,
        )

    _validate_schema(proposal.to_dict(), proposal_schema_path, "PhaseAdvanceProposal")
    return proposal


def authorize_phase_advance(
    proposal: dict,
    approved: bool,
    authorization_source: str,
    reason: str,
    authorization_schema_path: Path | None = None,
    timestamp: str | None = None,
) -> PhaseAdvanceAuthorization:
    repo_root = Path(__file__).resolve().parents[1]
    if authorization_schema_path is None:
        authorization_schema_path = repo_root / "schemas" / "phase_advance_authorization.schema.json"

    if proposal.get("status") != "ELIGIBLE" and approved:
        raise ValueError("Cannot approve ineligible phase advance proposal")

    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()

    next_phase = proposal.get("next_phase") if approved else None
    authorization = PhaseAdvanceAuthorization(
        approved=approved,
        reason=reason,
        authorization_timestamp=timestamp,
        authorization_source=authorization_source,
        approved_next_phase=next_phase,
    )

    _validate_schema(authorization.to_dict(), authorization_schema_path, "PhaseAdvanceAuthorization")
    return authorization
