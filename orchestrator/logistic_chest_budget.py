# Path: orchestrator/logistic_chest_budget.py
# Purpose: Derive the finite kit logistic-chest reserve required by startup dependencies.

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

#: The starter kit grants a finite number of these and the rebuilt base cannot
#: make more until the advanced-circuit ladder runs.  A cell whose startup bill
#: consumes one must not take the last unit from a dependency that needs it to
#: establish production.
FINITE_LOGISTIC_CHESTS = ("passive-provider-chest", "requester-chest")


@dataclass(frozen=True)
class DependencyChestBudget:
    """Prospective finite-chest hold for required startup dependency cells."""

    targets: Mapping[str, int]
    claimants: tuple[str, ...]


def plan_dependency_chest_budget(
    pending_items: Iterable[str], *,
    bill_for: Callable[[str], Mapping[str, int]],
    project_id_for: Callable[[str], str],
) -> DependencyChestBudget:
    """Reserve the finite chests the not-yet-established dependencies need.

    ``bill_for`` returns each pending dependency's prospective cell bill (the
    same recipe-derived bill its own reservation will use), so nothing here is
    hardcoded per item or coordinate.  Distinct standing cells each consume their own
    chest, so their complete bills sum; repeated aliases for one claimant merge
    by maximum; a dependency whose bill needs no finite chest is not a
    claimant and adds no hold.
    """
    by_claimant: dict[str, dict[str, int]] = {}
    for item in pending_items:
        try:
            bill = bill_for(str(item))
        except (KeyError, TypeError, ValueError):
            continue
        chests = {
            str(name): int(count)
            for name, count in bill.items()
            if name in FINITE_LOGISTIC_CHESTS and int(count) > 0
        }
        if not chests:
            continue
        owner = str(project_id_for(str(item)))
        current = by_claimant.setdefault(owner, {})
        for name, count in chests.items():
            current[name] = max(current.get(name, 0), count)
    totals: Counter[str] = Counter()
    for bill in by_claimant.values():
        totals.update(bill)
    return DependencyChestBudget(targets=dict(sorted(totals.items())), claimants=tuple(by_claimant))
