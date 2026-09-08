# Path: tests/test_starter_stock.py
# Purpose: Prove the opening belt stock covers a real cross-base connection, since a first bridge that cannot be paid for costs a pass every time.

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator.parts_mall import STARTER_MALL_TARGETS  # noqa: E402
from planners.recipe_data import LINE_RECIPES  # noqa: E402

_OPENING = dict(STARTER_MALL_TARGETS)

# The connection from the observed run: a mine output at (12.5,-3.5) feeding a
# refinery placed at (-34,-26).
_OBSERVED_LINK_TILES = abs(12.5 - -34) + abs(-3.5 - -26)


def test_the_opening_belt_stock_covers_one_real_connection() -> None:
    """'just to connect two different spots sometimes 200+ belts are required'."""
    assert _OPENING["transport-belt"] >= 200


def test_the_opening_stock_covers_the_link_actually_observed() -> None:
    assert _OPENING["transport-belt"] >= _OBSERVED_LINK_TILES


def test_a_stage_can_bridge_every_ingredient_it_has() -> None:
    """A conversion stage bridges one route per ingredient, so the widest
    recipe on a line is what the opening figure has to survive."""
    widest = max(
        len(spec["ingredients"])
        for spec in LINE_RECIPES.values()
        if spec["machine"].startswith("assembling-machine")
    )

    assert _OPENING["transport-belt"] >= widest * _OBSERVED_LINK_TILES / 2


def test_undergrounds_are_stocked_because_routes_meet_obstacles() -> None:
    """A tunnel is a PAIR of entities, and a long run needs several."""
    assert _OPENING.get("underground-belt", 0) >= 20
    assert _OPENING["underground-belt"] % 2 == 0


def test_electric_furnaces_are_not_a_background_bootstrap_reserve() -> None:
    """Their recipe is demand-driven after coal/oil/plastic prerequisites exist."""
    assert "electric-furnace" not in _OPENING


def test_opening_power_stock_avoids_the_pre_plastic_accumulator_gate() -> None:
    assert _OPENING["solar-panel"] >= 12
    assert "accumulator" not in _OPENING


def test_every_opening_item_is_something_the_agent_can_build() -> None:
    import json

    from planners.recipe_data import install_catalog_line_recipes

    install_catalog_line_recipes(json.loads(
        (REPO_ROOT / "tests" / "fixtures" / "player_recipe_catalog.json").read_text()
    ))

    for item in _OPENING:
        assert item in LINE_RECIPES, f"{item} is stocked but cannot be produced"
