# Path: core/recipe_categories.py
# Purpose: Validate recipe category facts while supporting legacy catalogs.

from collections.abc import Mapping


def recipe_categories(recipe: Mapping, label: str) -> frozenset[str]:
    category = recipe.get("category")
    if not isinstance(category, str) or not category.strip():
        raise ValueError(f"{label} category must be a nonempty string")
    if "categories" not in recipe:
        return frozenset((category,))
    categories = recipe["categories"]
    if (not isinstance(categories, list) or not categories
            or any(not isinstance(item, str) or not item.strip() for item in categories)
            or len(set(categories)) != len(categories) or category not in categories):
        raise ValueError(f"{label} categories are malformed or inconsistent")
    return frozenset(categories)
