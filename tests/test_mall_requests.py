# Path: tests/test_mall_requests.py
# Purpose: Prove a shared mall requester carries one labelled request group per machine it feeds, scaled by multiplier and rewritten without accumulating.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
from planners.mall_layout import (  # noqa: E402
    MALL_SUPPLY_SECONDS,
    generate_paired_mall_layout,
    machine_crafts_per_second,
    recipe_group_name,
    recipe_group_requests,
    request_multiplier,
)
from planners.recipe_data import LINE_RECIPES, MACHINE_SPEEDS  # noqa: E402

_SCHEMA = Draft7Validator(
    json.loads((REPO_ROOT / "schemas" / "build_plan.schema.json").read_text(encoding="utf-8"))
)
_ORIGIN = (100, 100)


def _half(recipe: str, side: str, *, stock_target: int = 6, machine: str | None = None) -> dict:
    spec = LINE_RECIPES[recipe]
    return generate_paired_mall_layout(
        recipe, machine or spec["machine"], spec["ingredients"], spec["amounts"], _ORIGIN, side,
        stock_target=stock_target, product_amount=spec.get("product_amount", 1),
        craft_time=spec["craft_time"], set_recipe=spec.get("set_recipe", True),
    )


def _requester(plan: dict) -> dict:
    return next(
        action for phase in plan["phases"] for action in phase["actions"]
        if action["entity"] == "requester-chest"
    )


def test_each_half_declares_only_its_own_labelled_group() -> None:
    """A half must not speak for the machine across from it: the executor
    upserts by label, so declaring one group leaves the other half's alone."""
    sections = _requester(_half("electronic-circuit", "left", stock_target=50))["logistic_sections"]

    assert len(sections) == 1
    assert sections[0]["group"] == recipe_group_name(
        "electronic-circuit", "left",
    )


def test_both_halves_label_the_same_chest_differently() -> None:
    left = _requester(_half("electronic-circuit", "left", stock_target=50))
    right = _requester(_half("copper-cable", "right", stock_target=200))

    assert left["position"] == right["position"], "both halves share one requester"
    assert left["logistic_sections"][0]["group"] != right["logistic_sections"][0]["group"]


def test_bootstrap_right_half_can_share_the_left_provider() -> None:
    spec = LINE_RECIPES["advanced-circuit"]
    plan = generate_paired_mall_layout(
        "advanced-circuit", spec["machine"], spec["ingredients"],
        spec["amounts"], _ORIGIN, "right", stock_target=1,
        craft_time=spec["craft_time"], shared_provider=True,
    )
    actions = plan["phases"][0]["actions"]
    provider = next(
        action for action in actions
        if action["entity"] == "passive-provider-chest"
    )
    output = next(
        action for action in actions
        if action.get("entity", "").endswith("inserter")
        and action["position"] == {"x": 105.5, "y": 100.5}
    )

    assert provider["position"] == {"x": 104.5, "y": 100.5}
    assert provider["inventory_limit"]["fill_chest"] is True
    assert output["direction"] == "east"
    assert not list(_SCHEMA.iter_errors(plan))


@pytest.mark.parametrize("recipe", ["copper-cable", "iron-gear-wheel"])
def test_two_matching_halves_each_contribute_their_own_request(recipe: str) -> None:
    left_chest = _requester(_half(recipe, "left"))
    right_chest = _requester(_half(recipe, "right"))
    left = left_chest["logistic_sections"][0]
    right = right_chest["logistic_sections"][0]

    assert left["group"] != right["group"]
    assert left_chest["clear_logistic_groups"] == [recipe_group_name(recipe)]
    assert right_chest["clear_logistic_groups"] == [recipe_group_name(recipe)]
    assert left["multiplier"] == 23
    assert right["multiplier"] == 23
    assert sum(section["multiplier"] for section in (left, right)) == 46


def test_group_holds_per_craft_amounts_and_multiplier_carries_the_rate() -> None:
    """The group is the recipe's canonical ingredient set, so one label stays
    reusable across cells running the same part at different speeds."""
    spec = LINE_RECIPES["electronic-circuit"]
    section = _requester(_half("electronic-circuit", "left"))["logistic_sections"][0]

    assert section["requests"] == recipe_group_requests(spec["ingredients"], spec["amounts"])
    assert all(request["count"] <= 3 for request in section["requests"]), "per craft, not per buffer"


def test_the_worked_example_from_the_floor() -> None:
    """copper-cable on an assembling-machine-2: 1.5 crafts/s consumes 1.5
    copper-plate/s, so a fifteen-second buffer is 23 plates."""
    section = _requester(_half("copper-cable", "left"))["logistic_sections"][0]

    assert section["requests"] == [{"name": "copper-plate", "count": 1}]
    assert section["multiplier"] == 23


@pytest.mark.parametrize("recipe", ["electronic-circuit", "copper-cable", "iron-gear-wheel"])
def test_request_covers_exactly_the_supply_window(recipe: str) -> None:
    """Every ingredient must arrive in the quantity the machine eats during
    MALL_SUPPLY_SECONDS -- never less, and no more than one craft's rounding."""
    spec = LINE_RECIPES[recipe]
    crafts_per_second = machine_crafts_per_second(spec["machine"], spec["craft_time"])
    section = _requester(_half(recipe, "left"))["logistic_sections"][0]

    for request, amount in zip(section["requests"], spec["amounts"]):
        effective = request["count"] * section["multiplier"]
        consumed = amount * crafts_per_second * MALL_SUPPLY_SECONDS
        assert effective >= consumed, f"{recipe} starves on {request['name']}"
        assert effective - consumed < amount, f"{recipe} over-buffers {request['name']}"


def test_a_faster_machine_asks_for_proportionally_more() -> None:
    """The point of sizing in time: an upgraded cell re-derives its own buffer."""
    tiers = [
        _requester(_half("copper-cable", "left", machine=machine))["logistic_sections"][0]
        for machine in ("assembling-machine-1", "assembling-machine-2", "assembling-machine-3")
    ]

    assert [section["multiplier"] for section in tiers] == [15, 23, 38]
    assert all(section["requests"] == tiers[0]["requests"] for section in tiers), (
        "only the scale moves; the group's per-craft contents are the recipe"
    )


def test_multiplier_tracks_crafting_speed_not_the_stock_target() -> None:
    """Stock target governs the PROVIDER buffer; the requester exists to keep
    the machine fed, which is a rate question."""
    small = _requester(_half("copper-cable", "right", stock_target=6))["logistic_sections"][0]
    large = _requester(_half("copper-cable", "right", stock_target=400))["logistic_sections"][0]

    assert small == large


def test_requester_multiplier_can_be_reduced_during_bootstrap() -> None:
    """Starter belt components must not reserve the normal ten-second buffer."""
    spec = LINE_RECIPES["copper-cable"]
    plan = generate_paired_mall_layout(
        "copper-cable", spec["machine"], spec["ingredients"], spec["amounts"],
        _ORIGIN, "left", craft_time=spec["craft_time"],
        request_multiplier_override=2,
    )

    assert _requester(plan)["logistic_sections"][0]["multiplier"] == 2


def test_replanning_a_half_is_identical_and_never_accumulates() -> None:
    """The old design merged onto whatever the chest already held, so a retry
    after a partial failure inflated the counts permanently."""
    assert _requester(_half("copper-cable", "right")) == _requester(_half("copper-cable", "right"))


@pytest.mark.parametrize("side", ["left", "right"])
def test_paired_plan_matches_the_build_plan_schema(side: str) -> None:
    errors = sorted(_SCHEMA.iter_errors(_half("electronic-circuit", side)),
                    key=lambda error: list(error.path))
    assert not errors, errors[0].message if errors else ""


def test_every_mall_recipe_can_size_its_own_request() -> None:
    """A recipe whose machine has no known speed would otherwise fail deep in a
    live build rather than here."""
    for recipe, spec in LINE_RECIPES.items():
        assert spec["machine"] in MACHINE_SPEEDS, f"{recipe} uses unmeasured {spec['machine']}"
        assert request_multiplier(spec["machine"], spec["craft_time"]) >= 1


def test_request_multiplier_rejects_impossible_inputs() -> None:
    with pytest.raises(ValueError, match="craft time"):
        request_multiplier("assembling-machine-2", 0)
    with pytest.raises(ValueError, match="MACHINE_SPEEDS"):
        request_multiplier("nonexistent-machine", 0.5)


def test_group_requests_reject_misaligned_ingredients() -> None:
    with pytest.raises(ValueError):
        recipe_group_requests(["iron-plate", "copper-cable"], [1])
