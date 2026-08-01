# Path: tests/test_intermediate_promotion.py
# Purpose: Prove a saturated intermediate cell promotes to a shared line on its own evidence, and grows on the same phase ladder the mining system uses.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.extraction_capacity import EXTRACTION_DRILL_PHASES  # noqa: E402
from orchestrator.intermediate_scaling import (  # noqa: E402
    MALL_INTERMEDIATE_RATE_LIMIT,
    PROMOTED_LINE_PHASES,
    promoted_line_machine_count,
)

GEARS = "iron-gear-wheel"


def test_a_saturated_cell_promotes_without_measurable_demand() -> None:
    """The chicken-and-egg: live_intermediate_demand only counts WORKING
    consumers, and the consumers starved of gears are not working -- so a gear
    cell running flat out reported 0.00/s and was never promoted."""
    assert promoted_line_machine_count(GEARS, 0.0, 1) is None
    assert promoted_line_machine_count(GEARS, 0.0, 1, saturated=True) == 6


def test_saturation_climbs_the_ladder_one_phase_at_a_time() -> None:
    """A machine running flat out cannot go faster; the only move is more of
    them, and the base steps 6 -> 20 -> 50 -> 100."""
    sizes, have = [], 1
    for _ in range(len(PROMOTED_LINE_PHASES)):
        have = promoted_line_machine_count(GEARS, 0.0, have, saturated=True)
        sizes.append(have)

    assert sizes == [6, 20, 50, 100]


def test_the_top_phase_is_a_ceiling_not_a_loop() -> None:
    top = PROMOTED_LINE_PHASES[-1]

    assert promoted_line_machine_count(GEARS, 0.0, top, saturated=True) == top


def test_the_ladder_is_the_one_the_mining_system_uses() -> None:
    """Extraction and the assembly it feeds must step up together; a parallel
    copy of the ladder could drift."""
    assert PROMOTED_LINE_PHASES == EXTRACTION_DRILL_PHASES == (6, 20, 50, 100)


@pytest.mark.parametrize("demand,expected", [(4.0, 6), (20.0, 20), (60.0, 50), (200.0, 100)])
def test_demand_driven_sizing_also_snaps_to_the_ladder(demand: float, expected: int) -> None:
    assert promoted_line_machine_count(GEARS, demand, 0) == expected


def test_an_idle_cell_under_the_rate_limit_is_left_alone() -> None:
    """Saturation is the new trigger; it must not promote everything."""
    assert MALL_INTERMEDIATE_RATE_LIMIT == 3.0
    assert promoted_line_machine_count(GEARS, 1.0, 1) is None


def test_only_promotable_intermediates_are_scaled_this_way() -> None:
    """iron-plate scales by opening mines, not by promoting a mall cell."""
    assert promoted_line_machine_count("iron-plate", 99.0, 1, saturated=True) is None


def test_saturation_never_proposes_a_line_it_already_has() -> None:
    """Re-proposing the current size would rebuild instead of expanding."""
    for have in PROMOTED_LINE_PHASES[:-1]:
        assert promoted_line_machine_count(GEARS, 0.0, have, saturated=True) > have
