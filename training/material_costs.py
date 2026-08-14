# Path: training/material_costs.py
# Purpose: Share auditable construction-item costs between candidates and rewards.

from __future__ import annotations

import math
from collections.abc import Mapping

MATERIAL_COST_BY_ENTITY = {
    "electric-mining-drill": 60,
    "fast-inserter": 11,
    "medium-electric-pole": 5,
    "transport-belt": 2,
    "splitter": 20,
    "underground-belt": 10,
    "express-transport-belt": 9,
    "express-underground-belt": 40,
    "express-loader": 50,
}


def construction_budget_material_cost(budget: Mapping) -> float:
    """Return the maximum material cost permitted by a mining budget."""
    unknown = set(budget).difference(MATERIAL_COST_BY_ENTITY)
    if unknown:
        raise ValueError(f"construction budget has no material costs for: {sorted(unknown)}")
    total = 0.0
    for entity, count in budget.items():
        value = float(count)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"construction budget count for {entity} must be non-negative")
        total += MATERIAL_COST_BY_ENTITY[entity] * value
    if total <= 0:
        raise ValueError("construction budget material cost must be greater than zero")
    return total
