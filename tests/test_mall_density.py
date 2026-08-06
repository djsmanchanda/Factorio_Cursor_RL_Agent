# Path: tests/test_mall_density.py
# Purpose: Prove the denser paired-mall grid remains collision-free.

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.mall_builder import _CELL_PITCH  # noqa: E402
from planners.mall_layout import generate_paired_mall_layout  # noqa: E402
from planners.plan_validation import validate_no_collisions  # noqa: E402
from planners.recipe_data import LINE_RECIPES  # noqa: E402


def test_dense_mall_pitch_keeps_adjacent_cells_clear() -> None:
    """The tighter 3x3 district must not overlap machines or substations.

    Each cell is tested with a different side assignment so this covers the
    allocator's normal left/right alternation, not just one symmetric layout.
    """
    assert _CELL_PITCH == (11, 6)
    recipes = ("copper-cable", "iron-gear-wheel", "electronic-circuit")
    sides = ("left", "right", "right", "left", "left", "right", "right", "left", "right")
    plans = []
    for index, side in enumerate(sides):
        row, column = divmod(index, 3)
        origin = (column * _CELL_PITCH[0], row * _CELL_PITCH[1])
        recipe = recipes[index % len(recipes)]
        spec = LINE_RECIPES[recipe]
        plans.append((
            f"cell-{index}",
            generate_paired_mall_layout(
                recipe,
                spec["machine"],
                spec["ingredients"],
                spec["amounts"],
                origin,
                side,
                stock_target=6,
                product_amount=spec.get("product_amount", 1),
                craft_time=spec["craft_time"],
                set_recipe=spec.get("set_recipe", True),
            ),
        ))

    validate_no_collisions(plans)