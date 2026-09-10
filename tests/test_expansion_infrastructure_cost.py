# Path: tests/test_expansion_infrastructure_cost.py
# Purpose: Expansion uses buildable side taps and stocked, economical power.

from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as builder, stage_services as services
from orchestrator.material_reservations import plan_material_bill
from planners.infrastructure import POLE_SPECS, strip_local_power
from planners.infrastructure_geometry import distance, footprint_tile_indices
from planners.local_layout_planner import LocalLayoutPlanner
from planners.mall_layout import generate_mall_stock_gate_update
from planners.smelter_block import generate_managed_refinery_extension_plan


def test_refinery_growth_side_tap_does_not_require_advanced_inserters():
    plan = generate_managed_refinery_extension_plan(
        "iron-plate", 6, 12, current_variant="basic",
    )
    bill = plan_material_bill(plan)
    assert bill.get("fast-inserter") == 1
    assert "bulk-inserter" not in bill


def test_existing_machine_gate_has_no_machine_bill():
    plan = generate_mall_stock_gate_update(
        "copper-cable", "assembling-machine-1", [(36.5, 38.5), (47.5, 38.5)], 50,
    )
    assert plan_material_bill(plan) == {}
    assert all(a["action_type"] == "configure_entity" for p in plan["phases"] for a in p["actions"])


def test_steel_uses_stocked_substation_when_medium_poles_are_exhausted():
    plan = strip_local_power(LocalLayoutPlanner().generate_line_layout(
        "steel-plate", 1, 108, 43, belt_type="transport-belt",
        inserter_type="inserter", feed_style="chest", terminal_collector=True,
    ), remove_substations=False)
    builder._side_sample_plate_output(plan, (108, 43), 1, "transport-belt")
    plan, anchor = builder._use_presteel_starter_power(plan, {"substation": 46})
    bill = plan_material_bill(plan)
    assert anchor == "substation" and bill["substation"] == 1
    assert not {"small-electric-pole", "medium-electric-pole"}.intersection(bill)


def test_stocked_long_reach_route_uses_fewer_poles_and_legal_edges(monkeypatch):
    monkeypatch.setattr(services, "_PENDING_POWER_BRIDGES", {})
    monkeypatch.setattr(services.live_base, "pole_network_id", lambda *_a: 2)
    monkeypatch.setattr(services.live_base, "nearest_powered_pole", lambda *_a, **_k: ((0, 0), "substation"))
    monkeypatch.setattr(services.live_base, "entity_at", lambda *_a: {"name": "substation"})
    monkeypatch.setattr(services.live_base, "occupied_tiles", lambda *_a, **_k: set())
    monkeypatch.setattr(services.live_base, "transferable_items", lambda *_a: {"big-electric-pole": 10})
    monkeypatch.setattr(services.live_base, "network_generation_kw", lambda *_a: 1000)
    monkeypatch.setattr(services, "_await_bot_built_infrastructure", lambda *_a, **_k: None)
    submitted = []
    monkeypatch.setattr(services, "_submit", lambda _c, _b, _s, p, *_a, **_k: submitted.append(p))
    services.extend_power(SimpleNamespace(command=lambda *_a: ""), object(), "nauvis", "player", (100, 0), lambda _m: None)
    actions = submitted[0]["phases"][0]["actions"]
    assert len(actions) <= 4
    assert all(a["entity"] == "big-electric-pole" for a in actions)
    points = [(0, 0), *[(a["position"]["x"], a["position"]["y"]) for a in actions], (100, 0)]
    names = ["substation", *[a["entity"] for a in actions], "substation"]
    for index in range(len(points) - 1):
        assert distance(points[index], points[index + 1]) <= min(POLE_SPECS[names[index]]["wire"], POLE_SPECS[names[index + 1]]["wire"])


def test_long_reach_route_checks_entire_pole_footprint():
    blocked = {(x, y) for x in range(10, 91) for y in range(-1, 2)}
    path = services._long_reach_bridge_hops((0, 0), (100, 0), "medium-electric-pole", "substation", "big-electric-pole", blocked)
    assert path is not None and path
    assert all(not footprint_tile_indices(p, 2).intersection(blocked) for p in path)
    assert distance((0, 0), path[0]) <= 9


@pytest.mark.parametrize("medium,big", [(50, 0), (50, 20), (0, 20)])
def test_remote_roboport_corridor_preserves_stocked_substations(monkeypatch, medium, big):
    monkeypatch.setattr(services, "_PENDING_POWER_BRIDGES", {})
    monkeypatch.setattr(services.live_base, "pole_network_id", lambda *_: None)
    monkeypatch.setattr(services.live_base, "nearest_powered_pole", lambda *_, **__: ((0, 0), "substation"))
    monkeypatch.setattr(services.live_base, "entity_at", lambda *_: {"name": "roboport"})
    monkeypatch.setattr(services.live_base, "occupied_tiles", lambda *_, **__: footprint_tile_indices((100, 0), 4))
    monkeypatch.setattr(services.live_base, "transferable_items", lambda *_: {
        "substation": 100, "medium-electric-pole": medium, "big-electric-pole": big,
    })
    monkeypatch.setattr(services.live_base, "network_generation_kw", lambda *_: 1000)
    monkeypatch.setattr(services, "_await_bot_built_infrastructure", lambda *_, **__: None)
    submitted = []
    monkeypatch.setattr(services, "_submit", lambda _c, _b, _s, p, *_, **__: submitted.append(p))
    services.extend_power(SimpleNamespace(command=lambda *_: ""), object(), "nauvis", "player", (100, 0), lambda _: None)
    actions = submitted[0]["phases"][0]["actions"]
    names = [a["entity"] for a in actions]
    assert set(names) <= {"medium-electric-pole", "big-electric-pole"}
    assert ("big-electric-pole" in names) == bool(big)
    if not medium:
        assert set(names) == {"big-electric-pole"}
    points = [(0, 0)] + [(a["position"]["x"], a["position"]["y"]) for a in actions]
    edge_names = ["substation", *names]
    for left, right, ln, rn in zip(points, points[1:], edge_names, edge_names[1:]):
        assert distance(left, right) <= min(POLE_SPECS[ln]["wire"], POLE_SPECS[rn]["wire"])
    terminal = points[-1]
    assert max(abs(terminal[0] - 100), abs(terminal[1])) < POLE_SPECS[names[-1]]["supply"] + 2


def test_substation_manufacturing_requires_advanced_circuit_production(monkeypatch):
    import json
    from pathlib import Path

    catalog = json.loads((Path(__file__).parent / "fixtures/player_recipe_catalog.json").read_text())
    recipe = next(r for r in catalog["recipes"] if r["name"] == "substation")
    monkeypatch.setitem(builder.LINE_RECIPES, "substation", {
        "ingredients": [i["name"] for i in recipe["ingredients"]],
    })
    monkeypatch.setattr(builder, "_chemical_capability_started", lambda _c, _s, _f, item: item != "advanced-circuit")
    assert builder._unfunded_ladder_ingredient(object(), "nauvis", "player", "substation") == "advanced-circuit"
    monkeypatch.setattr(builder, "_chemical_capability_started", lambda *_: True)
    assert builder._unfunded_ladder_ingredient(object(), "nauvis", "player", "substation") is None
