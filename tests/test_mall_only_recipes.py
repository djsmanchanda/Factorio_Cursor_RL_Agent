# Path: tests/test_mall_only_recipes.py
# Purpose: Prove the agent can learn to build the machine it builds everything else with, and that a recipe too wide for a belt-fed line is never promoted onto one.

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator.intermediate_scaling import is_promotable  # noqa: E402
from planners.local_layout_planner import LocalLayoutPlanner  # noqa: E402
from planners.mall_layout import generate_paired_mall_layout  # noqa: E402
from planners.plan_validation import validate_build_plan  # noqa: E402
from planners.recipe_data import (  # noqa: E402
    LINE_MAX_INGREDIENTS,
    LINE_RECIPES,
    MALL_ONLY_MAX_INGREDIENTS,
    MALL_ONLY_RECIPES,
    install_catalog_line_recipes,
)

_CATALOG = json.loads(
    (REPO_ROOT / "tests" / "fixtures" / "player_recipe_catalog.json").read_text()
)


@pytest.fixture(scope="module", autouse=True)
def _catalog():
    install_catalog_line_recipes(_CATALOG)


def test_the_agent_can_build_the_machine_it_builds_everything_with() -> None:
    """Every line in the system runs on an assembling-machine-2. Its recipe
    takes four ingredients, so the three-ingredient filter dropped it entirely
    and the agent could only ever take them from the player's starter kit."""
    assert LINE_RECIPES["assembling-machine-2"]["ingredients"] == [
        "assembling-machine-1", "electronic-circuit", "iron-gear-wheel", "steel-plate",
    ]


def test_every_line_recipe_runs_on_a_machine_the_agent_can_make() -> None:
    machines = {spec["machine"] for spec in LINE_RECIPES.values()}
    assert "assembling-machine-2" in machines
    assert "assembling-machine-2" in LINE_RECIPES


@pytest.mark.parametrize("recipe", ["assembling-machine-2", "bulk-inserter"])
def test_a_four_ingredient_recipe_is_learned(recipe: str) -> None:
    assert recipe in LINE_RECIPES
    assert len(LINE_RECIPES[recipe]["ingredients"]) > LINE_MAX_INGREDIENTS


@pytest.mark.parametrize("recipe", ["assembling-machine-2", "bulk-inserter"])
def test_a_wide_recipe_is_never_promoted_to_a_belt_fed_line(recipe: str) -> None:
    """A line carries two main lanes plus one auxiliary. Promoting one of these
    would crash the layout, so the mall keeps them for good."""
    assert not is_promotable(recipe)


@pytest.mark.parametrize("recipe", ["assembling-machine-2", "bulk-inserter"])
def test_a_wide_recipe_still_builds_as_a_mall_cell(recipe: str) -> None:
    spec = LINE_RECIPES[recipe]
    plan = generate_paired_mall_layout(
        recipe, spec["machine"], spec["ingredients"], spec["amounts"], (0, 0), "left",
        stock_target=6, product_amount=spec["product_amount"],
        craft_time=spec["craft_time"],
    )
    plan["surface"], plan["force"] = "nauvis", "player"

    validate_build_plan(plan)


@pytest.mark.parametrize("recipe", ["assembling-machine-2", "bulk-inserter"])
def test_the_line_layout_really_would_refuse_it(recipe: str) -> None:
    """The reason promotion is barred, asserted rather than assumed."""
    with pytest.raises(ValueError, match="at most two main-belt ingredients"):
        LocalLayoutPlanner().generate_line_layout(recipe, 6, 0, 0)


def test_every_promotable_recipe_fits_a_line() -> None:
    for item, spec in LINE_RECIPES.items():
        if is_promotable(item):
            assert len(spec["ingredients"]) <= LINE_MAX_INGREDIENTS, item


def test_the_mall_allowance_stays_within_what_a_cell_can_request() -> None:
    for recipe in MALL_ONLY_RECIPES:
        spec = LINE_RECIPES.get(recipe)
        if spec is not None:
            assert len(spec["ingredients"]) <= MALL_ONLY_MAX_INGREDIENTS, recipe
