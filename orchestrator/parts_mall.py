# Path: orchestrator/parts_mall.py
# Purpose: Turn construction-material shortages into persistent mall stock demands.

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from orchestrator import live_base
from tools.rcon_client import RconClient


# Opening stock, before anything is known about what the mission will cost.
#
# The belt figure is sized from a REAL connection, not from a round number: the
# observed mine at (12.5,-3.5) feeding a refinery at (-34,-26) is about seventy
# tiles on its own, and a conversion stage bridges one route per ingredient on
# top of its own line. 50 was under a third of a single link, so the first
# bridge failed, raised the target through MaterialShortage, and cost a pass
# doing it -- every time. User report, 2026-08-02: "just to connect two
# different spots sometimes 200+ belts are required".
#
# Undergrounds are placed in pairs wherever a route meets an obstacle, and a
# long cross-base run meets several.
STARTER_MALL_TARGETS = (
    ("transport-belt", 200),
    ("underground-belt", 20),
    ("inserter", 20),
    ("assembling-machine-1", 6),
    ("electric-furnace", 4),
)
_MACHINE_STOCK_TARGETS = {
    "assembling-machine-2": 6,
    "electric-furnace": 8,
    "chemical-plant": 4,
}
_OIL_STOCK_TARGETS = {
    "oil-refinery": 2,
    "pumpjack": 2,
    "offshore-pump": 2,
}


def mission_mall_targets(
    goals: tuple[str, ...], recipes: Mapping[str, Mapping],
) -> dict[str, int]:
    """Small construction stock needed before a complete mission starts."""
    targets = dict(STARTER_MALL_TARGETS)
    visited: set[str] = set()

    def visit(item: str) -> None:
        if item in visited:
            return
        visited.add(item)
        spec = recipes.get(item)
        if spec is None:
            return
        machine = spec.get("machine")
        if machine in _MACHINE_STOCK_TARGETS:
            targets[machine] = max(
                targets.get(machine, 0), _MACHINE_STOCK_TARGETS[machine],
            )
        if item in {"iron-plate", "copper-plate"}:
            targets["electric-mining-drill"] = max(
                targets.get("electric-mining-drill", 0), 6,
            )
        if machine == "chemical-plant" or spec.get("fluid_ingredients"):
            for equipment, count in _OIL_STOCK_TARGETS.items():
                targets[equipment] = max(targets.get(equipment, 0), count)
        for ingredient in spec.get("ingredients", ()):
            visit(ingredient)

    for goal in goals:
        visit(goal)
    if goals:
        targets["lab"] = 4
    return {item: count for item, count in targets.items() if item in recipes}


class MaterialShortage(RuntimeError):
    """A build plan can proceed after the base manufactures more stock."""

    def __init__(
        self, stage: str, required: Mapping[str, int], available: Mapping[str, int],
    ) -> None:
        self.stage = stage
        self.required = dict(required)
        self.available = dict(available)
        detail = ", ".join(
            f"{item}: need {target}, short {target - self.available.get(item, 0)}"
            for item, target in sorted(self.required.items())
        )
        super().__init__(f"{stage} needs material the base does not have -- {detail}")


def add_demands(targets: dict[str, int], shortage: MaterialShortage) -> None:
    """Keep the largest requested stock target for every mall item."""
    for item, target in shortage.required.items():
        targets[item] = max(targets.get(item, 0), target)


def wait_for_stock(
    client: RconClient, surface: str, force: str, item: str, target: int,
    emit: Callable[[str], None], *, poll_seconds: float = 5.0,
    report_seconds: float = 30.0, expansion_seconds: float = 60.0,
    on_stalled: Callable[[], bool] | None = None,
) -> bool:
    """Wait for stock; expand only after a full window with no progress."""
    last_report = 0.0
    next_expansion = time.monotonic() + expansion_seconds
    best_have = -1
    while True:
        have = live_base.available_items(client, surface, force).get(item, 0)
        if have >= target:
            emit(f"  MALL READY: {item} stock reached {have}/{target}")
            return True
        now = time.monotonic()
        if have > best_have:
            best_have = have
            next_expansion = now + expansion_seconds
        if on_stalled is not None and now >= next_expansion:
            emit(
                f"  MALL CAPACITY: {item} made no stock progress for "
                f"{expansion_seconds:.0f}s and remains below {target}; "
                "requesting the next upstream expansion phase"
            )
            if not on_stalled():
                return False
            next_expansion = time.monotonic() + expansion_seconds
        if now - last_report >= report_seconds:
            emit(f"  MALL WAIT: {item} stock is {have}/{target}; production continues")
            last_report = now
        time.sleep(poll_seconds)