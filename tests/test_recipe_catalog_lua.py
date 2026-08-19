# Path: tests/test_recipe_catalog_lua.py
# Purpose: Guard the Factorio 2.1 recipe-category compatibility fix.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "factorio_mod" / "recipe_catalog.lua"


def test_recipe_catalog_uses_factorio_21_plural_categories() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "local function primary_recipe_category(recipe)" in source
    assert "local categories = recipe.categories" in source
    assert "categories = recipe.prototype.categories" in source
    assert "category = primary_recipe_category(recipe)" in source
    assert "recipe.category" not in source
