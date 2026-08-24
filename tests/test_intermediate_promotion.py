# Path: tests/test_intermediate_promotion.py
# Purpose: Prove a saturated intermediate cell promotes to a shared line on its own evidence, and grows on the same phase ladder the mining system uses.

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.extraction_capacity import EXTRACTION_DRILL_PHASES  # noqa: E402
from orchestrator.intermediate_scaling import (  # noqa: E402
    MALL_INTERMEDIATE_RATE_LIMIT,
    PROMOTED_LINE_PHASES,
    promoted_line_belt_type,
    promoted_line_machine_count,
)
from orchestrator import autonomous_builder as builder  # noqa: E402

GEARS = "iron-gear-wheel"


def test_promotion_does_not_use_unexecutable_stocked_belts(monkeypatch) -> None:
    from planners.recipe_data import LINE_RECIPES

    for tier in ("fast-transport-belt", "express-transport-belt", "turbo-transport-belt"):
        monkeypatch.delitem(LINE_RECIPES, tier, raising=False)

    assert promoted_line_belt_type(
        GEARS, 6, {"transport-belt": 100, "express-transport-belt": 4000},
    ) == "transport-belt"

def test_a_saturated_cell_promotes_without_measurable_demand() -> None:
    """The chicken-and-egg: live_intermediate_demand only counts WORKING
    consumers, and the consumers starved of gears are not working -- so a gear
    cell running flat out reported 0.00/s and was never promoted."""
    assert promoted_line_machine_count(GEARS, 0.0, 1) is None
    assert promoted_line_machine_count(GEARS, 0.0, 1, saturated=True) == 6


def test_saturation_climbs_the_ladder_one_phase_at_a_time() -> None:
    """A machine running flat out cannot go faster; the only move is more of
    them, and the base grows in complete doubled six-machine sets."""
    sizes, have = [], 1
    for _ in range(len(PROMOTED_LINE_PHASES)):
        have = promoted_line_machine_count(GEARS, 0.0, have, saturated=True)
        sizes.append(have)

    assert sizes == [6, 12, 24, 48, 96]


def test_the_top_phase_is_a_ceiling_not_a_loop() -> None:
    top = PROMOTED_LINE_PHASES[-1]

    assert promoted_line_machine_count(GEARS, 0.0, top, saturated=True) == top


def test_the_ladder_is_the_one_the_mining_system_uses() -> None:
    """Extraction and the assembly it feeds must step up together; a parallel
    copy of the ladder could drift."""
    assert PROMOTED_LINE_PHASES == EXTRACTION_DRILL_PHASES == (6, 12, 24, 48, 96)


@pytest.mark.parametrize("demand,expected", [(4.0, 6), (20.0, 24), (60.0, 96), (200.0, 96)])
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


def test_promoted_pipe_defers_to_its_starved_iron_extraction(monkeypatch) -> None:
    """A large pipe backlog is not evidence that six pipe assemblers have iron.

    The live failure created a 9/s pipe line while the six-furnace iron system
    was still ore-starved, then tried to belt from a provider chest enclosed by
    its own inserters.  The next action must be iron extraction, not the line.
    """
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_args: {"iron-plate": 0},
    )
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_args: SimpleNamespace(working_count=6),
    )

    shortfall = builder._promotion_upstream_shortfall(
        object(), "nauvis", "player", "pipe", 6,
    )

    assert shortfall is not None
    extraction, available_rate, required_rate = shortfall
    assert extraction == "iron-plate"
    assert available_rate < required_rate


def test_promoted_line_expands_raw_input_before_building_downstream(monkeypatch) -> None:
    plan = SimpleNamespace(
        spec=builder.LINE_RECIPES["pipe"],
        promote_to_line=True,
        promoted_count=6,
        existing=None,
        mall_storage_limit=1,
        fill_provider=False,
    )
    monkeypatch.setattr(
        builder, "_ingredient_sources", lambda *_args, **_kwargs: {"iron-plate": (1.5, 1.5)},
    )
    monkeypatch.setattr(
        builder, "_promotion_upstream_shortfall",
        lambda *_args: ("iron-plate", 3.75, 9.0),
    )
    expanded: list[str] = []
    monkeypatch.setattr(
        builder, "build_mining_stage",
        lambda *_args, **_kwargs: expanded.append("iron-plate"),
    )
    monkeypatch.setattr(
        builder, "build_conversion_stage",
        lambda *_args, **_kwargs: pytest.fail("pipe line must not be built first"),
    )

    builder._build_assembled_stage(
        object(), object(), "nauvis", "player", "pipe", (0.0, 0.0),
        lambda _message: None, plan, None, upgrade_bootstrap=False,
    )

    assert expanded == ["iron-plate"]
