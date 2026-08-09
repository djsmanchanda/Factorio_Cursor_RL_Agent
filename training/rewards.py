# Path: training/rewards.py
# Purpose: Compute transparent tick-based rewards while keeping safety lexicographic.

from __future__ import annotations

import math
from collections.abc import Mapping


def _finite(value: object, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def reward_components(scenario: Mapping, report: Mapping) -> dict[str, float]:
    """Return auditable components; safety remains an evaluation gate, not a tradeoff."""
    weights = scenario["reward_weights"]
    completed = report.get("status") == "completed"
    delivered = _finite(report.get("delivered_items", 0), "delivered_items")
    elapsed = _finite(report.get("elapsed_ticks", 0), "elapsed_ticks")
    materials = _finite(report.get("material_items", 0), "material_items")
    failed = _finite(report.get("failed_placements", 0), "failed_placements")
    extra_poles = _finite(report.get("extra_poles", 0), "extra_poles")
    components = {
        "completion": _finite(weights["completion"], "completion weight") if completed else 0.0,
        "delivered_item": delivered * _finite(weights["delivered_item"], "delivery weight"),
        "elapsed_tick": elapsed * _finite(weights["elapsed_tick"], "elapsed weight"),
        "material_item": materials * _finite(weights["material_item"], "material weight"),
        "failed_placement": failed * _finite(weights["failed_placement"], "placement weight"),
        "extra_pole": extra_poles * _finite(weights["extra_pole"], "pole weight"),
    }
    components["total"] = sum(components.values())
    return components


def safety_violation(report: Mapping) -> bool:
    """Identify failures that can never be outweighed by ordinary reward."""
    return report.get("failure_kind") in {"safety", "identity", "budget", "fixture"}
