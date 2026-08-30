# Path: tests/test_transport_mode_selection.py
# Purpose: Prove belt-vs-bots follows measured demand, so a trickle stops demanding a belt corridor the base cannot afford.

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator.stage_transport import (  # noqa: E402
    _BOT_THROUGHPUT_LIMIT,
    _ingredient_demand,
    _transport_mode,
)
from planners.recipe_data import LINE_RECIPES  # noqa: E402


def _modes(recipe: str, machine_count: int, *, allow_logistic_inputs: bool = False):
    """The mapping build_conversion_stage now derives per ingredient."""
    return {
        ingredient: (
            "logistic" if allow_logistic_inputs
            else _transport_mode(recipe, ingredient, machine_count)
        )
        for ingredient in LINE_RECIPES[recipe]["ingredients"]
    }


def test_a_trickle_uses_bots_not_a_belt_corridor() -> None:
    """The run-killer: a 2-machine science stage draws 0.30 copper-plate/s -- a
    tenth of what bots carry -- and was handed a 128-tile belt it could not
    afford, ending the run."""
    demand = _ingredient_demand("automation-science-pack", "copper-plate", 2)

    assert demand < _BOT_THROUGHPUT_LIMIT
    assert _modes("automation-science-pack", 2)["copper-plate"] == "logistic"


@pytest.mark.parametrize("recipe", ["iron-plate", "copper-plate"])
def test_smelter_rows_still_take_a_belt(recipe: str) -> None:
    """A 7-furnace row pulls 4.38 ore/s, well past the bot limit; these stages
    also depend on a single belt ingredient for their direct-belt input path."""
    modes = _modes(recipe, 7)

    assert len(modes) == 1
    assert next(iter(modes.values())) == "belt"


def test_a_hungry_intermediate_still_takes_a_belt() -> None:
    assert _modes("iron-gear-wheel", 2)["iron-plate"] == "belt"
    assert _modes("electronic-circuit", 2)["copper-cable"] == "belt"


def test_allow_logistic_inputs_still_forces_bots() -> None:
    """The bootstrap path must be able to avoid belts regardless of demand."""
    forced = _modes("iron-gear-wheel", 2, allow_logistic_inputs=True)

    assert _ingredient_demand("iron-gear-wheel", "iron-plate", 2) > _BOT_THROUGHPUT_LIMIT
    assert forced["iron-plate"] == "logistic"


def test_mode_switches_exactly_at_the_bot_limit() -> None:
    """At or below the limit bots serve it; only strictly above needs a belt."""
    for recipe in ("automation-science-pack", "iron-gear-wheel"):
        for ingredient in LINE_RECIPES[recipe]["ingredients"]:
            for count in (1, 2, 5, 10):
                demand = _ingredient_demand(recipe, ingredient, count)
                mode = _transport_mode(recipe, ingredient, count)
                expected = "belt" if demand > _BOT_THROUGHPUT_LIMIT else "logistic"
                assert mode == expected, f"{recipe}/{ingredient} x{count} at {demand}/s"
