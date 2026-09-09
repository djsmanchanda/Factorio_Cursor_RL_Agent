# Path: tests/test_expansion_infrastructure_cost.py
# Purpose: Expansion uses buildable side taps and stocked, economical power.

from types import SimpleNamespace

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
