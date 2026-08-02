# Path: tests/test_recipe_catalog_contract.py
# Purpose: Hold every LINE_RECIPES entry to the real Factorio 2.0 recipe data exported from the live player force.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from planners.recipe_data import (
    LINE_MAX_INGREDIENTS,
    LINE_RECIPES,
    MACHINE_SPEEDS,
    MALL_ONLY_RECIPES,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "fixtures" if False else ROOT / "tests" / "fixtures" / "player_recipe_catalog.json"

# Machines this planner places, and the recipe categories each one can actually
# craft. Queried live against Factorio 2.0.77 (Space Age):
#   /sc prototypes.entity[name].crafting_categories
# Recorded here because a recipe assigned to a machine that cannot craft its
# category builds a line that silently never runs.
MACHINE_CATEGORIES = {
    "assembling-machine-2": {
        "advanced-crafting", "basic-crafting", "crafting", "crafting-with-fluid",
        "crafting-with-fluid-or-metallurgy", "cryogenics-or-assembling", "electronics",
        "electronics-or-assembling", "electronics-with-fluid", "metallurgy-or-assembling",
        "organic-or-assembling", "organic-or-hand-crafting", "parameters", "pressing",
    },
    "electric-furnace": {"smelting"},
    "chemical-plant": {"chemistry", "chemistry-or-cryogenics", "organic-or-chemistry", "parameters"},
    "oil-refinery": {"oil-processing", "parameters"},
}


def _catalog() -> dict[str, dict]:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    return {recipe["name"]: recipe for recipe in payload["recipes"]}


def _raw_resources() -> set[str]:
    return set(json.loads(CATALOG.read_text(encoding="utf-8"))["raw_resources"])


CATALOG_RECIPES = _catalog()
# Mining/pumping "recipes" (iron-plate from iron-ore, etc.) are planner-side
# smelting shorthands, not literal game recipes with the same ingredients, so
# they are checked for category/machine only, not ingredient equality.
SMELTING = {name for name, spec in LINE_RECIPES.items() if spec["machine"] == "electric-furnace"}
ASSEMBLED = sorted(set(LINE_RECIPES) - SMELTING)


@pytest.mark.parametrize("name", ASSEMBLED)
def test_line_recipe_matches_the_live_game_recipe(name: str) -> None:
    """Ingredients, amounts, output count and craft time must equal the real
    Factorio 2.0 recipe -- transcription drift here builds a line whose
    throughput math and feeder counts are quietly wrong."""
    assert name in CATALOG_RECIPES, f"{name} is not an enabled recipe on the player force"
    spec = LINE_RECIPES[name]
    actual = CATALOG_RECIPES[name]

    # The planner models solid ingredients (belt/inserter-fed) separately from
    # fluid ones (pipe-fed), so recombine them before comparing with the game's
    # single ingredient list.
    expected_ingredients = dict(zip(spec["ingredients"], spec["amounts"], strict=True))
    expected_ingredients.update(spec.get("fluid_ingredients", {}))
    live_ingredients = {i["name"]: i["amount"] for i in actual["ingredients"]}
    assert expected_ingredients == live_ingredients

    product = next(p for p in actual["products"] if p["name"] == name)
    assert spec["product_amount"] == product["amount"]
    assert round(spec["craft_time"] * 60) == actual["energy_ticks"]


@pytest.mark.parametrize("name", sorted(LINE_RECIPES))
def test_line_recipe_machine_can_craft_its_category(name: str) -> None:
    """A recipe pinned to a machine that does not support its crafting category
    produces a line that builds fine and then never runs."""
    spec = LINE_RECIPES[name]
    machine = spec["machine"]
    assert machine in MACHINE_CATEGORIES, f"no recorded crafting categories for {machine}"
    assert machine in MACHINE_SPEEDS, f"{machine} has no MACHINE_SPEEDS entry"
    if name not in CATALOG_RECIPES:
        pytest.skip(f"{name} is a planner-side smelting shorthand, not a literal game recipe")
    category = CATALOG_RECIPES[name]["category"]
    assert category in MACHINE_CATEGORIES[machine], (
        f"{name} is category {category!r}, which {machine} cannot craft"
    )


# Recipes whose chain is NOT yet resolvable by orchestrator/autonomous_builder,
# because it passes through an intermediate that needs a fluid stage the real-base
# builder cannot build yet (plan item C in docs/30). They are kept in LINE_RECIPES
# because the synthetic-sandbox pipeline supplies those fluids by other means.
# This is a recorded gap, not a passing case -- delete an entry from this set the
# moment its chain really does resolve.
FLUID_BLOCKED_CHAINS = {
    "processing-unit",    # needs advanced-circuit, plus sulfuric-acid directly
}


@pytest.mark.parametrize("name", ASSEMBLED)
def test_every_ingredient_is_producible_or_raw(name: str) -> None:
    """The autonomous builder recurses over LINE_RECIPES until it reaches a raw
    resource; an ingredient that is neither in LINE_RECIPES nor mineable is a
    dead end that only surfaces mid-build as a StuckError."""
    if name in FLUID_BLOCKED_CHAINS:
        pytest.skip(f"{name} is a known fluid-blocked chain (docs/30 item C)")
    raw = _raw_resources()
    for ingredient in LINE_RECIPES[name]["ingredients"]:
        assert ingredient in LINE_RECIPES or ingredient in raw, (
            f"{name} needs {ingredient!r}, which has no LINE_RECIPES entry and is not raw"
        )


def test_logistic_science_pack_chain_fully_resolves_to_raw_resources() -> None:
    """The current build target must be end-to-end resolvable: every branch of
    its recipe tree has to bottom out in something the miner can actually dig,
    or the autonomous builder gets partway and raises."""
    raw = _raw_resources()
    seen: set[str] = set()
    pending = ["logistic-science-pack"]
    while pending:
        item = pending.pop()
        if item in seen or item in raw:
            continue
        seen.add(item)
        assert item in LINE_RECIPES, f"{item!r} blocks the logistic-science chain"
        spec = LINE_RECIPES[item]
        assert not spec.get("fluid_ingredients"), f"{item} needs fluids the builder cannot supply yet"
        pending.extend(spec["ingredients"])
    assert {"inserter", "transport-belt", "electronic-circuit", "iron-plate"} <= seen


def test_three_ingredient_recipes_declare_the_auxiliary_at_index_one() -> None:
    """generate_line_layout only supports a third solid ingredient when it is
    declared as the auxiliary AT INDEX 1; anything else raises at plan time."""
    for name, spec in LINE_RECIPES.items():
        if len(spec["ingredients"]) != LINE_MAX_INGREDIENTS:
            continue
        assert spec.get("auxiliary_ingredient_index") == 1, (
            f"{name} has {len(spec['ingredients'])} ingredients but no auxiliary at index 1"
        )


def test_a_recipe_too_wide_for_a_line_declares_no_auxiliary() -> None:
    """An auxiliary index on a four-ingredient recipe would claim it fits a
    line layout, which refuses a fourth ingredient outright. These are mall
    cells for good -- see MALL_ONLY_RECIPES."""
    for name, spec in LINE_RECIPES.items():
        if len(spec["ingredients"]) <= LINE_MAX_INGREDIENTS:
            continue
        assert name in MALL_ONLY_RECIPES, f"{name} is too wide for a line and unlisted"
        assert "auxiliary_ingredient_index" not in spec, name
