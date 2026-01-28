# Path: planners/supervisor/intent_generator.py
# Purpose: Translate policy signals into high-level intents without actions.

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from planners.supervisor.policy_evaluator import PolicySignal
from jsonschema import Draft7Validator


@dataclass(frozen=True)
class Intent:
    intent: str
    scope: str
    urgency: str
    blocked_by: List[str] = field(default_factory=list)
    confidence: float = 1.0
    evidence: List[dict] = field(default_factory=list)
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        payload = {
            "intent": self.intent,
            "scope": self.scope,
            "urgency": self.urgency,
            "blocked_by": list(self.blocked_by),
            "confidence": self.confidence,
            "evidence": list(self.evidence),
        }
        if self.notes is not None:
            payload["notes"] = self.notes
        return payload


def _urgency_for_level(level: str) -> str:
    if level == "critical":
        return "critical"
    if level == "warn":
        return "medium"
    return "low"


def _load_schema(schema_path: Path) -> dict:
    with schema_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_intents(intents: List[Intent], schema_path: Path) -> None:
    schema = _load_schema(schema_path)
    validator = Draft7Validator(schema)
    errors = []
    for intent in intents:
        errors.extend(list(validator.iter_errors(intent.to_dict())))

    if errors:
        messages = []
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {path}: {error.message}")
        raise ValueError("Intent validation FAILED:\n" + "\n".join(messages))


def generate_intents(signals: Iterable[PolicySignal], schema_path: Optional[Path] = None) -> List[Intent]:
    intents: List[Intent] = []
    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[2]
        schema_path = repo_root / "schemas" / "intent.schema.json"

    for signal in signals:
        if signal.policy == "bot_saturation" and signal.level in {"warn", "critical"}:
            intents.append(
                Intent(
                    intent="reduce_bot_dependency",
                    scope="local",
                    urgency=_urgency_for_level(signal.level),
                    blocked_by=[],
                    confidence=1.0,
                    evidence=[
                        {"source": "bot_density", "value": signal.value},
                        {"source": "policy.bot_saturation", "value": signal.level},
                    ],
                    notes=f"Bot density {signal.level}: {signal.value:.6f} vs {signal.threshold:.6f}",
                )
            )

        if signal.policy == "construction_bot_load" and signal.level == "critical":
            intents.append(
                Intent(
                    intent="reduce_bot_dependency",
                    scope="local",
                    urgency=_urgency_for_level(signal.level),
                    blocked_by=[],
                    confidence=1.0,
                    evidence=[
                        {"source": "active_construction_bots", "value": signal.value},
                        {"source": "policy.construction_bot_load", "value": signal.level},
                    ],
                    notes=f"Active construction bots critical: {signal.value:.0f} vs {signal.threshold:.0f}",
                )
            )

    _validate_intents(intents, schema_path)
    return intents
