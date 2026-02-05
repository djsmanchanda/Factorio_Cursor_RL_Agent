# Path: core/execution_authorizer.py
# Purpose: Create schema-validated execution authorizations from proposals.

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class ExecutionAuthorization:
    approved_actions: List[str]
    denied_actions: List[dict]
    authorization_timestamp: str
    authorization_source: str
    scope_limits: Optional[dict] = None

    def to_dict(self) -> dict:
        payload = {
            "approved_actions": list(self.approved_actions),
            "denied_actions": list(self.denied_actions),
            "authorization_timestamp": self.authorization_timestamp,
            "authorization_source": self.authorization_source,
        }
        if self.scope_limits is not None:
            payload["scope_limits"] = self.scope_limits
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


def authorize_execution(
    proposal: dict,
    approved_actions: List[str],
    authorization_source: str,
    denied_reasons: Optional[dict] = None,
    scope_limits: Optional[dict] = None,
    schema_path: Path | None = None,
    timestamp: Optional[str] = None,
) -> ExecutionAuthorization:
    allowed = proposal.get("allowed_actions")
    blocked = proposal.get("blocked_actions")

    if allowed is None or blocked is None:
        raise ValueError("ExecutionProposal must include allowed_actions and blocked_actions")

    if any(item.get("reason") == "reconciliation_blocked" for item in blocked):
        raise ValueError("BLOCKED proposals cannot be authorized")

    allowed_set = set(allowed)
    approved_set = set(approved_actions)
    if not approved_set.issubset(allowed_set):
        raise ValueError("Approved actions must be a subset of allowed actions")

    denied_reasons = denied_reasons or {}
    denied_actions = []
    for action in sorted(allowed_set.difference(approved_set)):
        reason = denied_reasons.get(action, "not_authorized")
        denied_actions.append({"action": action, "reason": reason})

    for item in blocked:
        denied_actions.append({"action": item.get("action"), "reason": item.get("reason")})

    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()

    authorization = ExecutionAuthorization(
        approved_actions=sorted(list(approved_set)),
        denied_actions=denied_actions,
        authorization_timestamp=timestamp,
        authorization_source=authorization_source,
        scope_limits=scope_limits,
    )

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "execution_authorization.schema.json"

    _validate_schema(authorization.to_dict(), schema_path, "ExecutionAuthorization")
    return authorization
