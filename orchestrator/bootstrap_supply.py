# Path: orchestrator/bootstrap_supply.py
# Purpose: Validate existing bootstrap stock without granting items or placing infrastructure.

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from orchestrator import live_base
from tools.rcon_client import RconClient

Point = tuple[float, float]


class BootstrapSupplyError(RuntimeError):
    """Existing network stock does not satisfy declared bootstrap requirements."""


@dataclass(frozen=True)
class BootstrapSupplyResult:
    targets: Mapping[str, int]
    before: Mapping[str, int]
    inserted: Mapping[str, int]


def ensure_bootstrap_supply(
    client: RconClient,
    surface: str,
    force: str,
    targets: Mapping[str, int],
    reference_point: Point,
) -> BootstrapSupplyResult:
    """Check existing stock only; never grant missing construction items.

    reference_point and the empty inserted result are retained for caller compatibility."""
    normalized = {
        str(item): int(count) for item, count in targets.items() if int(count) > 0
    }
    if not normalized:
        return BootstrapSupplyResult({}, {}, {})
    before_all = live_base.available_items(client, surface, force)
    before = {item: int(before_all.get(item, 0)) for item in normalized}
    deficits = {
        item: target - before[item]
        for item, target in normalized.items()
        if before[item] < target
    }
    if not deficits:
        return BootstrapSupplyResult(normalized, before, {})

    raise BootstrapSupplyError(
        "Bootstrap supply unavailable; item grants are disabled: "
        + ", ".join(
            f"{item}={before[item]}/{normalized[item]} (missing {count})"
            for item, count in sorted(deficits.items())
        )
    )
