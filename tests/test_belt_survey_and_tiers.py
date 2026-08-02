# Path: tests/test_belt_survey_and_tiers.py
# Purpose: Prove a bridge surveys every tile its router may use, and that a basic plate keeps its belts when only one belt tier is short.

from __future__ import annotations

import inspect
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator.stage_services import _BRIDGE_SURVEY_MARGIN  # noqa: E402
from orchestrator.stage_transport import _BELT_TIERS_CHEAPEST_FIRST  # noqa: E402
from planners.belt_bridge import _ROUTE_SEARCH_MARGIN  # noqa: E402

_MINING = inspect.getsource(builder.build_mining_stage)


def test_the_survey_covers_everywhere_the_router_may_go() -> None:
    """A detour that leaves the surveyed box emits belt onto tiles never
    checked for occupancy. An iron-ore bridge did exactly that, laying
    transport-belt over the mine's own fast-transport-belt at x=15.5..17.5,
    and the ore never reached the furnaces."""
    assert _BRIDGE_SURVEY_MARGIN >= _ROUTE_SEARCH_MARGIN


def test_the_two_margins_cannot_drift_apart() -> None:
    """Derived from the router's own constant rather than restated, so raising
    the search radius cannot silently outrun the survey again."""
    source = inspect.getsource(sys.modules["orchestrator.stage_services"])
    line = next(
        line for line in source.splitlines()
        if line.startswith("_BRIDGE_SURVEY_MARGIN =")
    )

    assert "_ROUTE_SEARCH_MARGIN" in line


def test_every_stocked_belt_tier_is_tried_before_giving_up_on_belts() -> None:
    """Being short of one tier must not cost a basic plate its belts while a
    cheaper tier is stocked -- one run dropped iron to bot feeding purely
    because fast-transport-belt was short, with plain belt on a 200 target."""
    assert "_BELT_TIERS_CHEAPEST_FIRST" in _MINING
    assert "BELT TIER:" in _MINING


def test_the_beltless_smelter_is_reached_only_after_every_tier_fails() -> None:
    body = _MINING[_MINING.index("stock = live_base.available_items"):]
    tried = body[:body.index("BOOTSTRAP:")]

    assert "for tier in tiers:" in tried
    assert "build_conversion_stage(" in tried


def test_the_beltless_smelter_is_described_as_temporary() -> None:
    """It is slower than a belt and spends bots the base needs elsewhere, so it
    exists to be replaced."""
    assert "to be replaced once belts exist" in _MINING


def test_a_shortage_that_is_not_about_belts_is_never_swallowed() -> None:
    assert "if not any(belt in short_of.required" in _MINING


def test_the_preferred_tier_is_tried_first() -> None:
    """Downgrading is a fallback, not the default: a mine that can afford the
    faster belt should still get it."""
    body = _MINING[_MINING.index("tiers = ["):]

    assert body.index("_DEFAULT_BELT") < body.index("_BELT_TIERS_CHEAPEST_FIRST")


def test_only_tiers_the_base_actually_holds_are_attempted() -> None:
    """Placing ghosts of a belt nobody has just fails again one tier later."""
    body = _MINING[_MINING.index("tiers = ["):_MINING.index("shortage:")]

    assert "stock.get(tier, 0)" in body


def test_the_cheapest_tier_is_a_real_belt_the_agent_can_build() -> None:
    from planners.recipe_data import LINE_RECIPES

    assert _BELT_TIERS_CHEAPEST_FIRST[0] == "transport-belt"
    assert "transport-belt" in LINE_RECIPES
