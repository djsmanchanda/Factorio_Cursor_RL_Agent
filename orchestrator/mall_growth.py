# Path: orchestrator/mall_growth.py
# Purpose: Pure, demand-driven mall slot-growth facts shared by the deterministic
# admission and Laya's option facts, so neither duplicates decision geometry.

"""Decide whether another mall slot may be grown from measured facts.

The whole point is to keep the *decision* in one small, testable place.  The
deterministic controller and Laya both feed observed facts in and read the same
verdict out, instead of re-deriving "is the mall allowed to grow" in two
languages.

Two honesty rules matter here:

* A quantity that was not measured is ``None`` -- never zero.  An unknown
  six-drill checkpoint does not pass, and an unknown plate surplus does not
  manufacture demand.
* Consumption is a rate read from the engine's cumulative counters over an
  elapsed window.  A change in stock levels is *not* a rate; a caller that only
  has a stock delta must pass ``None`` rather than a fabricated number.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

VERSION = "mall-growth-v1"
#: The initial iron/copper/stone mines are complete at six drills.
SIX_DRILL_MINIMUM = 6
#: Every plate whose six-drill checkpoint guards mall growth.
PLATE_ITEMS = ("iron-plate", "copper-plate", "stone-brick")
#: Exact useful seed for one future paired/dense slot plus its long-handed
#: supports: a small reserve, never an unbounded stockpile.
SEED_RESERVE = {"assembling-machine-1": 1, "long-handed-inserter": 2}


def six_drill_checkpoint(observed_drills: Mapping[str, int | None]) -> bool:
    """True only when every plate's managed mine is verified at six drills.

    A missing or unreadable count is not ready.  Callers pass ``None`` for a
    plate they could not probe; the starter's own drills must already be
    excluded by the caller.
    """
    for plate in PLATE_ITEMS:
        count = observed_drills.get(plate)
        if count is None or int(count) < SIX_DRILL_MINIMUM:
            return False
    return True


def plate_surplus(
    production_per_s: Mapping[str, float | None],
    mall_consumption_per_s: Mapping[str, float | None],
    plates: tuple[str, ...] = PLATE_ITEMS,
) -> dict[str, float | None]:
    """Measured produced-minus-consumed rate per plate; ``None`` when unknown.

    Both inputs must be real rates (cumulative counters over elapsed time).
    A net inventory delta is a level, not throughput, and is never accepted
    here; pass ``None`` when only a stock delta is available.
    """
    surplus: dict[str, float | None] = {}
    for plate in plates:
        produced = production_per_s.get(plate)
        used = mall_consumption_per_s.get(plate)
        surplus[plate] = (
            None if produced is None or used is None
            else float(produced) - float(used)
        )
    return surplus


def seed_ready(
    stock: Mapping[str, int] | None, reserve: Mapping[str, int] = SEED_RESERVE,
) -> bool | None:
    """Whether the small future-slot seed is funded; unread stock stays unknown."""
    if stock is None:
        return None
    return all(int(stock.get(item, 0)) >= count for item, count in reserve.items())


def growth_demand(
    surplus: Mapping[str, float | None], unmet_demand: bool,
) -> bool:
    """Demand is any measured positive plate surplus or an explicit unmet item.

    Unknown surplus is not demand; it simply adds nothing.
    """
    return bool(unmet_demand) or any(
        value is not None and value > 0 for value in surplus.values()
    )


@dataclass(frozen=True)
class MallGrowthFacts:
    """One immutable admission verdict plus the facts that produced it."""

    version: str
    checkpoint_ready: bool
    demand: bool
    surplus: Mapping[str, float | None]
    free_slots: int | None
    seed_ready: bool | None
    admit: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "checkpoint_ready": self.checkpoint_ready,
            "demand": self.demand,
            "plate_surplus_per_s": dict(self.surplus),
            "free_slots": self.free_slots,
            "seed_ready": self.seed_ready,
            "admit": self.admit,
            "reason": self.reason,
        }


def mall_growth_facts(
    *,
    observed_drills: Mapping[str, int | None],
    committed_slots: int | None,
    slot_limit: int | None,
    production_per_s: Mapping[str, float | None],
    mall_consumption_per_s: Mapping[str, float | None],
    unmet_demand: bool,
    stock: Mapping[str, int] | None,
    reserve: Mapping[str, int] = SEED_RESERVE,
) -> MallGrowthFacts:
    """Compose the demand-driven slot-growth facts and admission verdict.

    A slot is admitted only when the verified six-drill checkpoint holds for
    every plate, there is demand, the current cap still has room, and the small
    seed reserve is funded.  ``slot_limit=None`` means the post-transition
    unlimited pool.
    """
    checkpoint = six_drill_checkpoint(observed_drills)
    surplus = plate_surplus(production_per_s, mall_consumption_per_s)
    demand = growth_demand(surplus, unmet_demand)
    seed = seed_ready(stock, reserve)
    free = (
        None if slot_limit is None or committed_slots is None
        else max(0, int(slot_limit) - int(committed_slots))
    )
    if not checkpoint:
        reason, admit = "six-drill checkpoint not verified", False
    elif committed_slots is None and slot_limit is not None:
        reason, admit = "slot capacity unknown", False
    elif not demand:
        reason, admit = "no measured plate surplus or unmet demand", False
    elif seed is None:
        reason, admit = "seed reserve unknown", False
    elif not seed:
        reason, admit = "seed reserve not funded", False
    elif free is not None and free <= 0:
        reason, admit = "slot capacity full", False
    else:
        reason, admit = "demand + verified six-drill checkpoint", True
    return MallGrowthFacts(
        VERSION, checkpoint, demand, surplus, free, seed, admit, reason,
    )
