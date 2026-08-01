# Path: tests/test_feeder_rate_honesty.py
# Purpose: Prove throughput figures that were never observed on this base are discounted before anything is sized from them, and that nothing sizes off the raw ceiling.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planners import recipe_data  # noqa: E402
from planners.recipe_data import (  # noqa: E402
    FEEDER_RATES,
    UNMEASURED_FEEDER_RATES,
    UNMEASURED_RATE_DERATING,
    feeder_rate,
)

_SOURCE_DIRS = ("planners", "orchestrator", "core")


def test_a_measured_tier_is_used_at_its_face_value() -> None:
    assert "fast-inserter" not in UNMEASURED_FEEDER_RATES
    assert feeder_rate("fast-inserter") == FEEDER_RATES["fast-inserter"]


@pytest.mark.parametrize("tier", sorted(UNMEASURED_FEEDER_RATES))
def test_an_unmeasured_tier_is_discounted(tier: str) -> None:
    """Erring low costs a feed point. Erring high builds a line that runs
    throttled and reports itself healthy, which reads downstream as saturated."""
    assert feeder_rate(tier) == FEEDER_RATES[tier] * UNMEASURED_RATE_DERATING
    assert feeder_rate(tier) < FEEDER_RATES[tier]


def test_the_derating_only_ever_lowers_a_rate() -> None:
    assert 0 < UNMEASURED_RATE_DERATING < 1


def test_every_rate_is_still_ordered_by_tier() -> None:
    """Discounting some entries and not others must not let a cheaper tier
    overtake a dearer one, which would make the selector pick backwards."""
    ordered = ["inserter", "fast-inserter", "bulk-inserter", "stack-inserter"]
    rates = [feeder_rate(tier) for tier in ordered]

    assert rates == sorted(rates)


def test_an_unknown_inserter_is_refused_rather_than_defaulted() -> None:
    with pytest.raises(ValueError, match="No feeder throughput known"):
        feeder_rate("long-handed-inserter")


def test_every_unmeasured_name_is_a_real_tier() -> None:
    assert UNMEASURED_FEEDER_RATES <= set(FEEDER_RATES)


def test_nothing_sizes_off_the_raw_ceiling() -> None:
    """FEEDER_RATES is a best-case ceiling. Reading it directly is the mistake
    this seam exists to prevent, so production code must go through
    feeder_rate() -- the table itself stays importable for tests and docs."""
    offenders = []
    for directory in _SOURCE_DIRS:
        for path in (REPO_ROOT / directory).rglob("*.py"):
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if "FEEDER_RATES[" in line:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}")

    assert offenders == []


def test_the_seam_is_where_a_live_measurement_lands() -> None:
    """A measured tier is applied by removing it from one frozenset, so the
    procedure in docs/21 has somewhere to put its answer."""
    assert isinstance(UNMEASURED_FEEDER_RATES, frozenset)
    assert feeder_rate.__module__ == recipe_data.__name__
