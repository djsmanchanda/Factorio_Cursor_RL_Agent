# Path: training/power.py
# Purpose: Define shared training-contract roles for electricity producers and storage.

from __future__ import annotations

POWER_SOURCE_ENTITIES = frozenset({
    "electric-energy-interface",
    "solar-panel",
    "steam-engine",
    "steam-turbine",
    "fusion-generator",
})
POWER_STORAGE_ENTITIES = frozenset({"accumulator"})


def energy_role(entity_name: str) -> str | None:
    """Return the training electricity role, without treating storage as generation."""
    if entity_name in POWER_SOURCE_ENTITIES:
        return "source"
    if entity_name in POWER_STORAGE_ENTITIES:
        return "storage"
    return None