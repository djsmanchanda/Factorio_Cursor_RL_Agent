# Path: tests/test_inserter_affordability.py
# Purpose: Prove a stage substitutes an adequate inserter tier it can actually build, so needing a part that needs the stage does not deadlock.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import build_decisions, live_base  # noqa: E402
from planners.recipe_data import (  # noqa: E402
    feeder_rate,
    FEED_HEADROOM,
    inserter_tiers_covering,
    machine_handled_rates,
)


@pytest.fixture
def stocked(monkeypatch):
    """Drive _stage_inserter_type against a chosen base inventory."""
    def _run(stock: dict[str, int]) -> tuple[str, str]:
        monkeypatch.setattr(
            live_base, "available_items", lambda *_args, **_kwargs: stock
        )
        said: list[str] = []
        tier = build_decisions._stage_inserter_type(
            None, "nauvis", "player", "iron-plate", 7,
            "transport-belt", "west", said.append,
        )
        return tier, said[0]
    return _run


def test_substitutes_up_when_the_right_sized_tier_is_short(stocked) -> None:
    """The live deadlock: the smelter row needs plain inserters, inserters need
    iron plate, and iron plate needs the smelter row."""
    needed = build_decisions._line_inserter_count(
        "iron-plate", 7, "transport-belt", "inserter", "west",
    )
    tier, message = stocked({"inserter": needed - 1, "fast-inserter": 60})

    assert tier == "fast-inserter"
    assert "in stock" in message and "fast-inserter" in message


def test_keeps_the_cheapest_tier_when_it_is_stocked(stocked) -> None:
    needed = build_decisions._line_inserter_count(
        "iron-plate", 7, "transport-belt", "inserter", "west",
    )
    tier, _ = stocked({"inserter": needed, "fast-inserter": 60})

    assert tier == "inserter"


def test_falls_back_to_the_right_size_when_nothing_is_stocked(stocked) -> None:
    """With no adequate tier available the mall should be asked for the CHEAPEST
    part, not an oversized one it would also have to learn to build."""
    tier, message = stocked({"inserter": 0, "fast-inserter": 0})

    assert tier == "inserter"
    assert "no adequate tier is stocked" in message


def test_never_substitutes_down_to_an_inadequate_tier(stocked) -> None:
    """Substituting UP costs materials; substituting DOWN starves the line."""
    peak = max(machine_handled_rates("electronic-circuit")) * FEED_HEADROOM
    tier, _ = stocked({"inserter": 9999, "fast-inserter": 9999, "bulk-inserter": 9999})

    assert feeder_rate(tier) >= max(machine_handled_rates("iron-plate")) * FEED_HEADROOM
    assert "inserter" not in inserter_tiers_covering(peak), "e-circuit needs more than a plain inserter"


def test_covering_tiers_are_cheapest_first_and_all_adequate() -> None:
    demand = 1.0
    covering = inserter_tiers_covering(demand)

    assert covering[0] == "inserter"
    assert all(feeder_rate(tier) >= demand for tier in covering)
    assert list(covering) == sorted(covering, key=lambda tier: feeder_rate(tier))


def test_covering_never_returns_empty_even_past_every_tier() -> None:
    """A demand no tier covers still needs an answer -- the biggest one."""
    assert inserter_tiers_covering(10_000) == ("bulk-inserter",)


def test_line_inserter_count_scales_with_the_row() -> None:
    small = build_decisions._line_inserter_count(
        "iron-plate", 2, "transport-belt", "inserter", "west",
    )
    large = build_decisions._line_inserter_count(
        "iron-plate", 7, "transport-belt", "inserter", "west",
    )

    assert 0 < small < large
