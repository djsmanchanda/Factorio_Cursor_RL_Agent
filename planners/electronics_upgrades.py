# Path: planners/electronics_upgrades.py
# Purpose: Build explicit deterministic logistics-tier upgrades for electronics bundles.

from __future__ import annotations

from planners.plan_validation import actions

INITIAL_TO_PREMIUM = {
    "fast-transport-belt": "express-transport-belt",
    "fast-inserter": "stack-inserter",
}


def build_premium_upgrade_plan(
    bundle: dict,
    premium_tiers: dict[str, str] | None = None,
) -> dict:
    """Return position-stable upgrades; execution remains separately authorized."""
    targets = premium_tiers or INITIAL_TO_PREMIUM
    invalid = set(targets) - set(INITIAL_TO_PREMIUM)
    if invalid:
        raise ValueError(f"Unsupported initial logistics tiers: {sorted(invalid)}")
    entries: set[tuple[str, str, str, float, float]] = set()
    for block, plan in bundle["plans"]:
        for action in actions(plan):
            source = action.get("entity")
            target = targets.get(source)
            if action.get("action_type") not in {"place_entity", "place_ghost"} or not target:
                continue
            position = action["position"]
            entries.add((block, source, target, position["x"], position["y"]))
    return {
        "actions": [
            {
                "action": "entity_tier_upgrade",
                "block": block,
                "from_name": source,
                "to_name": target,
                "position": {"x": x, "y": y},
            }
            for block, source, target, x, y in sorted(entries)
        ]
    }