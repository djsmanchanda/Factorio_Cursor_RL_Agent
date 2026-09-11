# Path: orchestrator/bootstrap_profiles.py
# Purpose: Retain deterministic bootstrap profile identities without item grants.

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class BootstrapProfile:
    name: str
    version: int
    seed_stock: Mapping[str, int]


BOOTSTRAP_PROFILE_SPECS = {
    "reduced-v1": BootstrapProfile(
        name="reduced-v1",
        version=1,
        # Both profiles must obtain construction items from existing production.
        seed_stock={},
    ),
    "supplied-v1": BootstrapProfile(
        name="supplied-v1", version=1, seed_stock={},
    ),
}
BOOTSTRAP_PROFILES = tuple(BOOTSTRAP_PROFILE_SPECS)


def bootstrap_profile(name: str) -> BootstrapProfile:
    try:
        return BOOTSTRAP_PROFILE_SPECS[name]
    except KeyError as error:
        raise ValueError(
            f"Unknown bootstrap profile {name!r}; expected one of "
            + ", ".join(BOOTSTRAP_PROFILES)
        ) from error
