# Path: planners/city_planner/capability_resolver.py
# Purpose: Resolve which routed planning capabilities are available in context.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Set

from jsonschema import Draft7Validator

from planners.city_planner.capability_registry import CAPABILITIES


@dataclass(frozen=True)
class CapabilityResolution:
    intent: str
    available: List[str]
    blocked: List[str]
    missing_prerequisites: List[str]
    scope: str | None = None

    def to_dict(self) -> dict:
        payload = {
            "intent": self.intent,
            "available": list(self.available),
            "blocked": list(self.blocked),
            "missing_prerequisites": list(self.missing_prerequisites),
        }
        if self.scope is not None:
            payload["scope"] = self.scope
        return payload


def _missing_requirements(capability: str, context: Dict[str, bool]) -> List[str]:
    if capability not in CAPABILITIES:
        raise ValueError(f"Unknown capability: {capability}")
    requirements = CAPABILITIES.get(capability, {}).get("requires", [])
    return [req for req in requirements if not context.get(req, False)]


def _load_schema(schema_path: Path) -> dict:
    with schema_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_resolution(resolution: CapabilityResolution, schema_path: Path) -> None:
    schema = _load_schema(schema_path)
    validator = Draft7Validator(schema)
    errors = list(validator.iter_errors(resolution.to_dict()))
    if errors:
        messages = []
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {path}: {error.message}")
        raise ValueError("Capability resolution validation FAILED:\n" + "\n".join(messages))


def resolve_capabilities(
    request: dict,
    context: Dict[str, bool],
    schema_path: Path | None = None,
) -> CapabilityResolution:
    intent = request.get("intent")
    scope = request.get("scope")
    required = request.get("required_capabilities", [])

    available: List[str] = []
    blocked: List[str] = []
    missing: Set[str] = set()

    for capability in required:
        missing_reqs = _missing_requirements(capability, context)
        if missing_reqs:
            blocked.append(capability)
            missing.update(missing_reqs)
        else:
            available.append(capability)

    available.sort()
    blocked.sort()
    missing_sorted = sorted(missing)

    resolution = CapabilityResolution(
        intent=intent,
        available=available,
        blocked=blocked,
        missing_prerequisites=missing_sorted,
        scope=scope,
    )

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[2]
        schema_path = repo_root / "schemas" / "capability_resolution.schema.json"

    _validate_resolution(resolution, schema_path)
    return resolution


def resolve_many(
    requests: Iterable[dict],
    context: Dict[str, bool],
    schema_path: Path | None = None,
) -> List[CapabilityResolution]:
    resolutions = [resolve_capabilities(request, context, schema_path) for request in requests]
    resolutions.sort(key=lambda item: (item.intent or "", item.available))
    return resolutions
