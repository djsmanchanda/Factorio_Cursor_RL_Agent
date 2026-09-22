# Path: orchestrator/laya_decision_telemetry.py
# Purpose: Append validated, provenance-carrying Laya decisions without changing execution.

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from jsonschema import Draft7Validator
from referencing import Registry, Resource


_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = json.loads((_ROOT / "schemas" / "laya_decision.schema.json").read_text(encoding="utf-8"))
_STATE_SCHEMA = json.loads((_ROOT / "schemas" / "laya_state.schema.json").read_text(encoding="utf-8"))
_BASE_URI = "https://factorio-agent.local/schemas/"
_SCHEMA["$id"] = _BASE_URI + "laya_decision.schema.json"
_STATE_SCHEMA["$id"] = _BASE_URI + "laya_state.schema.json"
_REGISTRY = Registry().with_resources([
    (_BASE_URI + "laya_decision.schema.json", Resource.from_contents(_SCHEMA)),
    (_BASE_URI + "laya_state.schema.json", Resource.from_contents(_STATE_SCHEMA)),
])
_VALIDATOR = Draft7Validator(_SCHEMA, registry=_REGISTRY)


class LayaDecisionTelemetryError(ValueError):
    """Raised when a decision evidence record is malformed."""


class LayaDecisionLog:
    """Append-only JSONL writer. A failed log write is explicit, never a policy fallback."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def validate(self, record: Mapping) -> None:
        errors = sorted(_VALIDATOR.iter_errors(dict(record)), key=lambda error: list(error.path))
        if errors:
            detail = "; ".join(
                f"{'/'.join(map(str, error.path)) or '<root>'}: {error.message}"
                for error in errors
            )
            raise LayaDecisionTelemetryError(f"decision record validation failed: {detail}")

    def append(self, record: Mapping) -> dict:
        payload = dict(record)
        self.validate(payload)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False))
            handle.write("\n")
        return payload


__all__ = ["LayaDecisionLog", "LayaDecisionTelemetryError"]
