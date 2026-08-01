# Path: tests/test_mall_inserter_sizing.py
# Purpose: Prove a mall cell's inserters are sized from its machine's real intake and output rates, not from the size of a requested batch.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planners.mall_layout import (  # noqa: E402
    compact_input_inserter,
    compact_output_inserter,
    generate_paired_mall_layout,
)
from planners.recipe_data import (  # noqa: E402
    FEEDER_RATES,
    LINE_RECIPES,
    MACHINE_SPEEDS,
)

_MALL = [r for r, s in LINE_RECIPES.items() if s["machine"] == "assembling-machine-2"]


def _rates(recipe: str) -> tuple[float, float]:
    spec = LINE_RECIPES[recipe]
    crafts = MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]
    return sum(spec["amounts"]) * crafts, spec.get("product_amount", 1) * crafts


@pytest.mark.parametrize("recipe", sorted(_MALL))
def test_no_mall_cell_is_throttled_by_its_own_inserters(recipe: str) -> None:
    """Eight of thirteen were: an electronic-circuit cell draws 6.0 items/s and
    was handed a 1.4/s inserter, running at a quarter speed however full its
    requester was."""
    spec = LINE_RECIPES[recipe]
    intake, output = _rates(recipe)

    chosen_in = compact_input_inserter(
        spec["machine"], spec["ingredients"], spec["amounts"], spec["craft_time"],
    )
    chosen_out = compact_output_inserter(
        spec["machine"], spec.get("product_amount", 1), spec["craft_time"],
    )

    assert FEEDER_RATES[chosen_in] >= intake, f"{recipe} input starves at {intake:.2f}/s"
    assert FEEDER_RATES[chosen_out] >= output, f"{recipe} output backs up at {output:.2f}/s"


def test_the_busiest_recipe_gets_the_biggest_tier() -> None:
    spec = LINE_RECIPES["electronic-circuit"]

    assert compact_input_inserter(
        spec["machine"], spec["ingredients"], spec["amounts"], spec["craft_time"],
    ) == "bulk-inserter"


def test_a_trickle_recipe_still_gets_a_plain_inserter() -> None:
    """Rate-driven cuts both ways -- it must not just upgrade everything."""
    spec = LINE_RECIPES["logistic-science-pack"]

    assert compact_input_inserter(
        spec["machine"], spec["ingredients"], spec["amounts"], spec["craft_time"],
    ) == "inserter"


def test_the_built_cell_uses_the_sized_inserters() -> None:
    spec = LINE_RECIPES["electronic-circuit"]
    plan = generate_paired_mall_layout(
        "electronic-circuit", spec["machine"], spec["ingredients"], spec["amounts"],
        (100, 100), "left", stock_target=6,
        product_amount=spec.get("product_amount", 1), craft_time=spec["craft_time"],
    )
    entities = {
        action["entity"]
        for phase in plan["phases"] for action in phase["actions"]
        if "inserter" in action["entity"]
    }

    assert "bulk-inserter" in entities, "the 6.0/s input side"
    assert "inserter" not in entities, "nothing left on the throttled default"
