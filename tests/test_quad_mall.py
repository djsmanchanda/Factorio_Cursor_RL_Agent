# Path: tests/test_quad_mall.py
# Purpose: Protect the reduced-seed four-slot mall module and its iron-release gate.

from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as builder
from orchestrator.mall_builder import (
    _cell_origins, build_quad_mall_stage, dense_mall_module_centers,
    preview_quad_mall_allocation, quad_mall_center,
    quad_mall_project_bill, quad_support_inserter_type,
    seed_cell_includes_substation,
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

    assert builder._bootstrap_mall_slot_limit(object(), "nauvis", "player") == 8


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


def test_mall_expansion_precedes_native_am2_upgrades(monkeypatch) -> None:
    """AM1 replacement stock stays available until the dense bank is full."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        builder, "_bootstrap_state",
        lambda _recipe: SimpleNamespace(
            measured_output_count=1, lifecycle_state="released",
        ),
    )
    monkeypatch.setattr(
        builder, "_direct_plate_foundation_ready", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder, "_dense_bootstrap_mall_enabled", lambda *_a: True,
    )
    monkeypatch.setattr(builder, "mall_slot_count", lambda *_a: 6)
    monkeypatch.setattr(
        builder, "_upgrade_bootstrap_mall",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("upgrade ran early")),
    )

    assert builder._promote_mall_after_iron(
        object(), object(), "nauvis", "player", {}, (3.0, -1.0),
        lambda _message: None,
    ) is False


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


def test_runtime_quad_reservation_uses_long_handed_support_inserters(monkeypatch) -> None:
    captured = {}

    def fake_bill(*args, **kwargs):
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder, "mall_slot_count", lambda *_a: 0)
    monkeypatch.setattr(builder, "_bootstrap_mall_slot_limit", lambda *_a: 4)
    monkeypatch.setattr(builder, "_quad_bootstrap_mall_enabled", lambda *_a: True)
    monkeypatch.setattr(
        builder, "preview_quad_mall_allocation",
        lambda *_a: (quad_mall_center((3.0, -1.0)), "top"),
    )
    monkeypatch.setattr(builder, "quad_mall_project_bill", fake_bill)
    monkeypatch.setattr(builder, "_transferable_or_available_stock", lambda *_a: {})

    affordable, shortage = builder._bootstrap_demand_cell_affordable(
        object(), "nauvis", "player", "electric-mining-drill", 1,
        (3.0, -1.0),
    )

    assert affordable is True
    assert shortage == {}
    assert captured["inserter_type"] == "inserter"
    assert captured["support_inserter_type"] == "long-handed-inserter"


def test_quad_vertical_transfers_use_long_handed_inserters_in_order() -> None:
    spec = LINE_RECIPES["electronic-circuit"]
    center = (39.5, 32.5)
    plan = generate_quad_mall_layout(
        "electronic-circuit", spec["machine"], spec["ingredients"],
        spec["amounts"], center, "bottom", craft_time=spec["craft_time"],
        inserter_type="inserter", support_inserter_type="long-handed-inserter",
    )
    by_position = {
        (action["position"]["x"], action["position"]["y"]): action
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity", "").endswith("inserter")
    }

    assert by_position[(center[0], center[1] + 3.0)]["entity"] == (
        "long-handed-inserter"
    )
    assert by_position[(center[0], center[1] + 2.0)]["entity"] == (
        "long-handed-inserter"
    )


def test_dense_vertical_supports_are_always_long_handed() -> None:
    assert quad_support_inserter_type("electronic-circuit") == (
        "long-handed-inserter"
    )
    assert quad_support_inserter_type("long-handed-inserter") == (
        "long-handed-inserter"
    )


def test_quad_replaces_old_regular_vertical_supports(monkeypatch) -> None:
    import orchestrator.mall_builder as mall_builder

    center = quad_mall_center((3.0, -1.0))
    positions = {
        (center[0], center[1] + 3.0): {"name": "inserter", "type": "inserter"},
        (center[0], center[1] + 2.0): {
            "name": "inserter", "type": "inserter",
        },
    }
    monkeypatch.setattr(
        mall_builder, "preview_quad_mall_allocation",
        lambda *_a, **_k: (center, "bottom"),
    )
    monkeypatch.setattr(
        mall_builder.live_base, "entity_at",
        lambda _c, _s, position: positions.get(position),
    )
    monkeypatch.setattr(
        mall_builder, "_submit",
        lambda _c, _b, _s, plan, _name, _emit: setattr(
            mall_builder, "_test_plan", plan,
        ),
    )
    monkeypatch.setattr(mall_builder, "_test_plan", None, raising=False)
    monkeypatch.setattr(
        mall_builder, "_existing_network_generation", lambda *_a: 5000.0,
    )

    build_quad_mall_stage(
        SimpleNamespace(command=lambda *_a: ""), SimpleNamespace(), "nauvis",
        "player", "electronic-circuit", {}, (3.0, -1.0),
        lambda *_a, **_k: None, lambda *_a: None,
    )

    removals = [
        action for action in mall_builder._test_plan["phases"][0]["actions"]
        if action["action_type"] == "remove_entity"
    ]
    assert len(removals) == 2
    assert all(action["entity"] == "inserter" for action in removals)
    machine = next(
        action for action in mall_builder._test_plan["phases"][0]["actions"]
        if action.get("entity", "").startswith("assembling-machine-")
    )
    assert "recipe" not in machine


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
    # The power route must preserve the interior service lanes of every
    # relative module, not just the half currently being submitted.
    assert (45, 37) in reserved
    assert (36, 42) in reserved


def test_dense_bank_has_three_reserved_module_centres() -> None:
    centers = dense_mall_module_centers((3.0, -1.0))

    assert len(centers) == 3
    assert centers[0] == quad_mall_center((3.0, -1.0))
    assert len(set(centers)) == 3


def test_quad_top_admission_parks_when_power_cannot_stage(monkeypatch) -> None:
    """Run 5 (2026-09-17): the first quad-top admission killed the run with
    an untyped `cannot stage generated power` StuckError at the shared seed
    substation. A staging wait on real infrastructure is a supply_wait:
    the admission must park with a named substation and retry while power
    lands."""
    import orchestrator.mall_builder as mall_builder

    center = quad_mall_center((3.0, -1.0))
    monkeypatch.setattr(
        mall_builder, "preview_quad_mall_allocation",
        lambda *_a, **_k: (center, "top"),
    )
    monkeypatch.setattr(
        mall_builder, "extend_power",
        lambda *_a, **_k: False,
    )
    submitted: list[str] = []
    monkeypatch.setattr(
        mall_builder, "_submit",
        lambda *_a: pytest.fail("admission must park before submitting"),
    )
    messages: list[str] = []

    with pytest.raises(builder.ProductionPrerequisiteDeferred) as deferred:
        build_quad_mall_stage(
            SimpleNamespace(command="/sc noop"), SimpleNamespace(), "nauvis",
            "player", "electronic-circuit", {}, (3.0, -1.0),
            lambda *_a: pytest.fail("stage-up must not run"),
            messages.append,
        )

    assert deferred.value.code == "quad_power_staging_deferred"
    assert deferred.value.state == "power_wait"
    assert deferred.value.details["substation"] == [
        seed_shared_substation((3.0, -1.0))[0],
        seed_shared_substation((3.0, -1.0))[1],
    ]
    assert any(
        "QUAD MALL POWER STAGING RETRY" in message for message in messages
    )


def test_quad_top_admission_accepts_an_already_powered_shared_seed_anchor(monkeypatch) -> None:
    """The quad seed-top admission stages power at the shared seed substation,
    which the seed bank already placed and powered. extend_power must not be
    surveyed into a false failure there: the only generating network is the
    position's own network, so the first attempt deferred forever and the mall
    stayed at its seed slots (live 2026-09-17)."""
    import orchestrator.mall_builder as mall_builder
    from orchestrator import live_base

    center = quad_mall_center((3.0, -1.0))
    monkeypatch.setattr(
        mall_builder, "preview_quad_mall_allocation",
        lambda *_a, **_k: (center, "top"),
    )
    monkeypatch.setattr(
        live_base, "pole_network_id", lambda *_a, **_k: 5,
    )
    monkeypatch.setattr(
        live_base, "network_generation_kw", lambda *_a, **_k: 5000.0,
    )
    monkeypatch.setattr(
        mall_builder, "extend_power",
        lambda *_a, **_k: pytest.fail("already-powered anchor must skip the bridge survey"),
    )
    submitted: list[str] = []
    monkeypatch.setattr(
        mall_builder, "_submit",
        lambda _client, _bridge, _surface, plan, label, _emit: submitted.append(label),
    )
    staged: list[tuple] = []
    messages: list[str] = []

    provider = build_quad_mall_stage(
        SimpleNamespace(command="/sc noop"), SimpleNamespace(), "nauvis",
        "player", "electronic-circuit", {}, (3.0, -1.0),
        lambda *args, **kwargs: staged.append(args), messages.append,
    )

    assert submitted == ["quad_mall_electronic-circuit_top"]
    assert staged and staged[0][4] == "quad bootstrap mall for electronic-circuit"
    assert provider[0] == center[0]


def test_quad_module_places_the_capability_aware_machine(monkeypatch) -> None:
    """The quad path forwarded the recipe's native tier (assembling-machine-2)
    even before the base could make one, so every expansion module queued an
    unfillable AM2 demand while tier-1 assemblers sat in stock and the mall
    stayed at its seed slots (live 2026-09-17). The admission must place the
    same capability-aware machine the compact path and its material
    reservation use."""
    from orchestrator import live_base
    import orchestrator.mall_builder as mall_builder
    from planners.quad_mall_layout import QUAD_MALL_MACHINE_OFFSETS

    placed = {}

    def fake_layout(recipe, machine, *_a, **_k):
        placed["machine"] = machine
        return generate_quad_mall_layout(
            recipe, machine, ["copper-plate"], [1.0],
            quad_mall_center((3.0, -1.0)), "top", craft_time=0.5,
            include_substation=False,
        )

    monkeypatch.setattr(mall_builder, "generate_quad_mall_layout", fake_layout)
    monkeypatch.setattr(
        mall_builder, "preview_quad_mall_allocation",
        lambda *_a, **_k: (quad_mall_center((3.0, -1.0)), "top"),
    )
    monkeypatch.setattr(
        mall_builder, "_existing_network_generation",
        lambda *_a, **_k: 5000.0,
    )
    submitted = []
    monkeypatch.setattr(
        mall_builder, "_submit",
        lambda _c, _b, _s, plan, label, _e: submitted.append(plan),
    )

    mall_builder.build_quad_mall_stage(
        SimpleNamespace(command="/sc noop"), SimpleNamespace(), "nauvis",
        "player", "copper-cable", {}, (3.0, -1.0),
        lambda *_a, **_k: None, (lambda *_a: None),
        machine_name="assembling-machine-1",
    )

    assert placed["machine"] == "assembling-machine-1"
    machine_actions = [
        action for phase in submitted[0]["phases"] for action in phase["actions"]
        if action.get("entity", "").startswith("assembling-machine")
    ]
    assert {action["entity"] for action in machine_actions} == {"assembling-machine-1"}
