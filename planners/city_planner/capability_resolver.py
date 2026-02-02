# Path: planners/city_planner/capability_resolver.py
# Purpose: Resolve which routed planning capabilities are available in context.

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Set

from planners.city_planner.capability_registry import CAPABILITIES


@dataclass(frozen=True)
class CapabilityResolution:
    intent: str
    available: List[str]
    blocked: List[str]
    missing_prerequisites: List[str]


def _missing_requirements(capability: str, context: Dict[str, bool]) -> List[str]:
    requirements = CAPABILITIES.get(capability, {}).get("requires", [])
    return [req for req in requirements if not context.get(req, False)]


def resolve_capabilities(request: dict, context: Dict[str, bool]) -> CapabilityResolution:
    intent = request.get("intent")
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

    return CapabilityResolution(
        intent=intent,
        available=available,
        blocked=blocked,
        missing_prerequisites=missing_sorted,
    )


def resolve_many(requests: Iterable[dict], context: Dict[str, bool]) -> List[CapabilityResolution]:
    resolutions = [resolve_capabilities(request, context) for request in requests]
    resolutions.sort(key=lambda item: (item.intent or "", item.available))
    return resolutions
