# Path: orchestrator/laya_operations.py
# Purpose: Compile and validator-prune seed-specific Laya operation options without ranking them.

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from orchestrator.laya_tool_registry import OperationDefinition


class LayaOperationError(ValueError):
    """Raised when a candidate operation cannot enter the director boundary."""


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_hash(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class OperationOption:
    """One legal alternative presented to Laya.

    The compiler creates options; it never scores, sorts by utility, or chooses
    one.  ``signature`` binds the complete option identity, including its
    parameters and scope, so a stale answer cannot be executed after the world
    changes.
    """

    option_id: str
    operation_family: str
    tool_id: str
    signature: str
    summary: str
    features: Mapping[str, float | None]
    scope: Mapping[str, Any]
    risk_tier: str
    plan_hash: str | None = None

    def to_state_option(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "option_id": self.option_id,
            "operation_family": self.operation_family,
            "tool_id": self.tool_id,
            "signature": self.signature,
            "plan_hash": self.plan_hash,
            "summary": self.summary,
            "features": dict(self.features),
            "scope": dict(self.scope),
            "risk_tier": self.risk_tier,
        }
        return payload


Validator = Callable[[OperationOption], tuple[bool, str | None]]


class OperationCompiler:
    """Turn mechanics-owned alternatives into a bounded, auditable option set."""

    def __init__(self, definitions: Iterable[OperationDefinition], *, max_options: int = 20):
        if not 1 <= max_options <= 20:
            raise ValueError("max_options must be between 1 and 20")
        self._definitions = {definition.tool_id: definition for definition in definitions}
        self.max_options = max_options

    def compile(self, raw_options: Iterable[Mapping[str, Any]]) -> tuple[OperationOption, ...]:
        """Compile raw generator output in input order; never apply utility ranking."""
        options: list[OperationOption] = []
        seen: set[str] = set()
        for raw in raw_options:
            tool_id = str(raw.get("tool_id", ""))
            definition = self._definitions.get(tool_id)
            if definition is None:
                raise LayaOperationError(f"unknown tool_id: {tool_id!r}")
            if not definition.executable:
                raise LayaOperationError(f"observation-only tool cannot be selected: {tool_id}")
            option_id = str(raw.get("option_id", ""))
            if not option_id or option_id in seen:
                raise LayaOperationError(f"option ids must be unique and non-empty: {option_id!r}")
            summary = str(raw.get("summary", "")).strip()
            scope = raw.get("scope")
            if not summary or not isinstance(scope, Mapping):
                raise LayaOperationError(f"option {option_id!r} lacks summary or scope")
            features = raw.get("features") or {}
            if not isinstance(features, Mapping) or not features:
                raise LayaOperationError(f"option {option_id!r} needs numeric features")
            normalized_features: dict[str, float | None] = {}
            for name, value in features.items():
                if not isinstance(name, str) or not name:
                    raise LayaOperationError("feature names must be non-empty strings")
                if value is not None and not isinstance(value, (int, float)):
                    raise LayaOperationError(f"feature {name!r} must be numeric or null")
                normalized_features[name] = None if value is None else float(value)
            plan_hash = raw.get("plan_hash")
            if plan_hash is not None and not str(plan_hash).startswith("sha256:"):
                raise LayaOperationError(f"option {option_id!r} has an invalid plan hash")
            identity = {
                "option_id": option_id,
                "tool_id": tool_id,
                "operation_family": definition.operation_family,
                "plan_hash": plan_hash,
                "scope": dict(scope),
                "parameters": raw.get("parameters") or {},
            }
            signature = str(raw.get("signature") or sha256_hash(identity))
            if not signature.startswith("sha256:"):
                raise LayaOperationError(f"option {option_id!r} has an invalid signature")
            options.append(OperationOption(
                option_id=option_id,
                operation_family=definition.operation_family,
                tool_id=tool_id,
                signature=signature,
                summary=summary,
                features=normalized_features,
                scope=dict(scope),
                risk_tier=definition.risk_tier,
                plan_hash=None if plan_hash is None else str(plan_hash),
            ))
            seen.add(option_id)
            if len(options) > self.max_options:
                raise LayaOperationError(
                    f"option space contains {len(options)} entries; split it before Laya selection"
                )
        if not options:
            raise LayaOperationError("operation compiler produced no options")
        return tuple(options)

    def prune(
        self,
        options: Iterable[OperationOption],
        validators: Iterable[Validator],
    ) -> tuple[tuple[OperationOption, ...], tuple[dict[str, Any], ...]]:
        """Apply hard validators and return (eligible, pruned) without fallback selection."""
        eligible: list[OperationOption] = []
        pruned: list[dict[str, Any]] = []
        checks = tuple(validators)
        for option in options:
            reasons: list[str] = []
            for validator in checks:
                passed, reason = validator(option)
                if not passed:
                    reasons.append(str(reason or "validator_rejected"))
            if reasons:
                pruned.append({
                    "option": option.to_state_option(),
                    "validator_ids": [getattr(validator, "validator_id", "validator") for validator in checks],
                    "prune_reason": reasons[0],
                    "detail": "; ".join(reasons),
                })
            else:
                eligible.append(option)
        return tuple(eligible), tuple(pruned)


def escalation_option(scope: Mapping[str, Any], *, reason: str) -> OperationOption:
    """Create the sole safe option when no executable alternative survives validation."""
    identity = {"tool_id": "escalate", "scope": dict(scope), "reason": reason}
    signature = sha256_hash(identity)
    return OperationOption(
        option_id="escalate.no_legal_option",
        operation_family="escalate",
        tool_id="escalate",
        signature=signature,
        summary=reason,
        features={"safe_stop": 1.0},
        scope=dict(scope),
        risk_tier="read_only",
    )


__all__ = ["LayaOperationError", "OperationCompiler", "OperationOption", "escalation_option", "sha256_hash"]
