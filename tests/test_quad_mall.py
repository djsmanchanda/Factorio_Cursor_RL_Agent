# Path: tests/test_quad_mall.py
# Purpose: Protect the reduced-seed four-slot mall module and its iron-release gate.

from __future__ import annotations

from orchestrator import autonomous_builder as builder
from orchestrator.mall_builder import (
    _cell_origins, dense_mall_module_centers, quad_mall_center,
    quad_mall_project_bill, seed_cell_includes_substation,
    seed_expansion_reserved_tiles, seed_shared_substation,
)
from planners.plan_validation import validate_no_collisions
from planners.quad_mall_layout import generate_quad_mall_layout
from planners.mall_layout import generate_paired_mall_layout
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
    monkeypatch.setattr(builder, "_dense_bootstrap_mall_enabled", lambda *_a: True)
    monkeypatch.setattr(
        builder, "_bootstrap_state",
        lambda recipe: type("State", (), {"lifecycle_state": "released"})()
        if recipe == "iron-plate" else None,
    )

    assert builder._bootstrap_mall_slot_limit(object(), "nauvis", "player") == 12


def test_pre_advanced_mall_starts_with_one_dense_four_slot_module(monkeypatch) -> None:
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder, "_production_started", lambda *_a: False)
    monkeypatch.setattr(builder, "_bootstrap_state", lambda _recipe: None)
    monkeypatch.setattr(builder, "_STARTUP_METAL_STARTERS_OBSERVED", True)

    assert builder._bootstrap_mall_slot_limit(object(), "nauvis", "player") == 4


def test_quad_expansion_uses_catalog_capability_after_iron_release(monkeypatch) -> None:
    """Catalog availability must unlock expansion; waiting for output is circular."""
    monkeypatch.setitem(
        LINE_RECIPES, "long-handed-inserter",
        {"machine": "assembling-machine-1", "ingredients": ["iron-plate"],
         "amounts": [1], "product_amount": 1, "craft_time": 0.5},
    )
    monkeypatch.setattr(builder, "_STARTUP_METAL_STARTERS_OBSERVED", True)
    monkeypatch.setattr(
        builder, "_bootstrap_state",
        lambda recipe: type("State", (), {"lifecycle_state": "released"})()
        if recipe == "iron-plate" else None,
    )
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder, "_production_started", lambda *_a: False)

    assert builder._dense_bootstrap_mall_enabled(
        object(), "nauvis", "player",
    )


def test_rotating_loan_finds_missing_circuit_prerequisite(monkeypatch) -> None:
    monkeypatch.setattr(
        builder, "_bootstrap_loan_stock",
        lambda *_a: ({"fast-inserter": 0}, {"electronic-circuit": 0}),
    )
    monkeypatch.setattr(builder, "_production_started", lambda *_a: False)
    monkeypatch.setitem(
        LINE_RECIPES, "fast-inserter",
        {"ingredients": ["electronic-circuit"], "amounts": [1],
         "product_amount": 1},
    )
    assert builder._missing_rotating_input(
        object(), "nauvis", "player", "fast-inserter", 1,
    ) == ("electronic-circuit", 1)


def test_quad_bottom_half_reuses_requester_and_substation() -> None:
    top = quad_mall_project_bill("copper-cable", "top", machine_name="assembling-machine-1")
    bottom = quad_mall_project_bill("electronic-circuit", "bottom", machine_name="assembling-machine-1")
    assert top["requester-chest"] == 1
    assert top["substation"] == 1
    assert bottom.get("requester-chest", 0) == 0
    assert bottom.get("substation", 0) == 0


def test_quad_bill_includes_support_inserters_before_submission() -> None:
    bill = quad_mall_project_bill(
        "transport-belt", "top", machine_name="assembling-machine-1",
    )

    assert bill["long-handed-inserter"] == 2


def test_quad_expansion_reuses_the_staggered_seed_geometry() -> None:
    """The first dense requester aligns with the two expandable seed cells."""
    reference = (3.0, -1.0)
    center = quad_mall_center(reference)
    assert center == (39.5, 32.5)
    assert _cell_origins(reference)[:2] == [(35, 31), (41, 36)]


def test_seed_power_is_shared_and_keeps_the_bottom_bay_clear() -> None:
    spec = LINE_RECIPES["iron-gear-wheel"]
    reference = (3.0, -1.0)
    plans = [
        generate_paired_mall_layout(
            "iron-gear-wheel", spec["machine"], spec["ingredients"],
            spec["amounts"], origin, "left", craft_time=spec["craft_time"],
            include_substation=seed_cell_includes_substation(reference, origin, "left"),
            substation_position=seed_shared_substation(reference),
        )
        for origin in _cell_origins(reference)[:2]
    ]
    substations = [
        action["position"] for plan in plans for phase in plan["phases"]
        for action in phase["actions"] if action.get("entity") == "substation"
    ]
    assert substations == [{"x": 43.0, "y": 35.0}]


def test_seed_power_bridge_reserves_all_dense_module_tiles() -> None:
    reserved = seed_expansion_reserved_tiles((3.0, -1.0))
    assert (39, 27) in reserved
    assert (36, 47) in reserved


def test_dense_bank_has_three_reserved_module_centres() -> None:
    centers = dense_mall_module_centers((3.0, -1.0))

    assert len(centers) == 3
    assert centers[0] == quad_mall_center((3.0, -1.0))
    assert len(set(centers)) == 3
