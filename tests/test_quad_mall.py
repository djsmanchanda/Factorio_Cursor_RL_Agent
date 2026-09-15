# Path: tests/test_quad_mall.py
# Purpose: Protect the reduced-seed four-slot mall module and its iron-release gate.

from __future__ import annotations

from orchestrator import autonomous_builder as builder
from orchestrator.mall_builder import quad_mall_project_bill
from planners.plan_validation import validate_no_collisions
from planners.quad_mall_layout import generate_quad_mall_layout
from planners.recipe_data import LINE_RECIPES


def test_quad_module_has_four_machines_two_providers_and_one_requester() -> None:
    plans = []
    for recipe, group in (("copper-cable", "top"), ("electronic-circuit", "bottom")):
        spec = LINE_RECIPES[recipe]
        plans.append((
            recipe,
            generate_quad_mall_layout(
                recipe, spec["machine"], spec["ingredients"], spec["amounts"],
                (0.0, 0.0), group, craft_time=spec["craft_time"],
                product_amount=spec.get("product_amount", 1),
            ),
        ))

    validate_no_collisions(plans)
    actions = [
        action for _name, plan in plans
        for phase in plan["phases"] for action in phase["actions"]
    ]
    assert sum(action["entity"] == "assembling-machine-2" for action in actions) == 4
    assert sum(action["entity"] == "requester-chest" for action in actions) == 1
    assert sum(action["entity"] == "passive-provider-chest" for action in actions) == 2


def test_pre_advanced_mall_releases_quad_capacity_after_iron_pioneer(
    monkeypatch,
) -> None:
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder, "_production_started", lambda *_a: False)
    monkeypatch.setattr(
        builder, "_bootstrap_state",
        lambda recipe: type("State", (), {"lifecycle_state": "released"})()
        if recipe == "iron-plate" else None,
    )

    assert builder._bootstrap_mall_slot_limit(object(), "nauvis", "player") == 12


def test_quad_bottom_half_reuses_requester_and_substation() -> None:
    top = quad_mall_project_bill("copper-cable", "top", machine_name="assembling-machine-1")
    bottom = quad_mall_project_bill("electronic-circuit", "bottom", machine_name="assembling-machine-1")
    assert top["requester-chest"] == 1
    assert top["substation"] == 1
    assert bottom.get("requester-chest", 0) == 0
    assert bottom.get("substation", 0) == 0
