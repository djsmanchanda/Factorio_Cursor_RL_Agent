# Path: tests/test_laya_ops_initialization.py | Purpose: Keep session startup policy-free and verify selected steel reservations with real ledgers.

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as builder, material_reservations
from orchestrator.bootstrap_district import REQUIRED_RESERVATION_ROLES
from planners import recipe_data
from orchestrator import operation_session as laya_ops
from planners.steel_bootstrap import steel_seed
from training.laya_ops_runner import BUILT, compile_options


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "laya-startup"
    server = root / "server"
    (server / "script-output").mkdir(parents=True)
    (server / "rcon-password").write_text("test-only")
    (root / "manifest.json").write_text(json.dumps({"rcon_port": 12345}))
    exports = []
    catalog_path = root / "catalog.json"
    catalog_path.write_text(json.dumps({"recipes": [], "machines": [], "stack_sizes": {}}))

    def export(*, force):
        exports.append(force)
        return catalog_path

    def unexpected(*args, **kwargs):
        pytest.fail("Session initialization chose work or accessed an unexpected live command")

    bridge = SimpleNamespace(export_recipe_catalog=export)
    monkeypatch.setattr(laya_ops, "GameBridge", lambda **kwargs: bridge)
    monkeypatch.setattr(laya_ops, "RconClient", lambda *args: SimpleNamespace(command=unexpected))
    for name in ("_BOOTSTRAP_DISTRICT_LEDGER", "_MATERIAL_RESERVATION_LEDGER", "_BOOTSTRAP_WORK_LEDGER"):
        monkeypatch.setattr(builder, name, None)
    monkeypatch.setattr(material_reservations, "_ACTIVE_LEDGER", None)
    monkeypatch.setattr(builder, "PriorityList", unexpected)
    monkeypatch.setattr(builder, "ensure_bootstrap_supply", unexpected)
    monkeypatch.setattr(builder, "_production_started", lambda *args: False)
    monkeypatch.setattr(builder, "_transferable_or_available_stock", lambda *args: {"medium-electric-pole": 5})
    monkeypatch.setattr(builder, "_material_sources_and_rates", lambda *args: ({}, {}))
    # These surveys are unrelated to the reservation being selected here.
    monkeypatch.setattr(laya_ops.DeterministicSession, "_maintain_mall", lambda self: None)
    monkeypatch.setattr(laya_ops.DeterministicSession, "_prepare_operation", lambda self: None)
    return SimpleNamespace(root=root, server=server, exports=exports, bridge=bridge, catalog_path=catalog_path)


def open_session(world, **kwargs):
    return laya_ops.DeterministicSession(world.root, emit=lambda message: None, **kwargs)


def test_opening_session_loads_facts_and_clears_caches_without_reserving_stock(world):
    builder.UNBACKED_DRAWS.add("old-shortage")
    builder._MALL_STOCK_GATE_FLOORS[("nauvis", "player", "transport-belt")] = 100
    session = open_session(world)

    assert world.exports == ["player"]
    assert not builder.UNBACKED_DRAWS and not builder._MALL_STOCK_GATE_FLOORS
    # Its only reservation is the plastic stage's output chest (user rule, 2026-10-20).
    assert set(builder._MATERIAL_RESERVATION_LEDGER.projects) == {builder._PLASTIC_OUTPUT_PACKET}
    assert not (world.server / "logs" / "autonomous-priorities.json").exists()


def test_selected_steel_reservation_protects_real_stock_from_a_competing_build(world):
    session = open_session(world)
    ledger = builder._MATERIAL_RESERVATION_LEDGER
    result = session.run("reserve_steel_seed")
    competing = ledger.declare("foundation", {"medium-electric-pole": 5}, {"medium-electric-pole": 5})

    assert result["ok"] and result["code"] == "reserved"
    assert ledger.projects["conversion_steel-plate"].reserved == {"medium-electric-pole": 2}
    assert competing.reserved == {"medium-electric-pole": 3}


@pytest.mark.parametrize("poles", [0, 1])
def test_underfunded_seed_stays_retryable_and_reserves_new_stock(world, monkeypatch, poles):
    stock = {"medium-electric-pole": poles}
    monkeypatch.setattr(builder, "_transferable_or_available_stock", lambda *args: stock)
    session = open_session(world)
    failed = session.run("reserve_steel_seed")

    assert not failed["ok"] and failed["code"] == "material_shortage"
    assert failed["details"]["required"] == {"medium-electric-pole": 2}
    assert failed["details"]["available"].get("medium-electric-pole", 0) == poles
    assert "op.reserve_steel_seed" in {option["option_id"] for option in
                                      compile_options({"needs": []}, [failed])["options"]}
    stock["medium-electric-pole"] = 2
    assert session.run("reserve_steel_seed")["code"] == "reserved"
    assert builder._MATERIAL_RESERVATION_LEDGER.projects["conversion_steel-plate"].reserved == stock


def test_reopened_fence_rechecks_stock_that_has_disappeared(world, monkeypatch):
    session = open_session(world)
    session.run("reserve_steel_seed")
    session.close()
    monkeypatch.setattr(builder, "_transferable_or_available_stock", lambda *args: {})

    failed = open_session(world).run("reserve_steel_seed")

    assert not failed["ok"] and failed["code"] == "material_shortage"
    assert builder._MATERIAL_RESERVATION_LEDGER.projects["conversion_steel-plate"].reserved == {}


@pytest.mark.parametrize("producing", [False, True])
def test_committed_or_producing_seed_does_not_require_spent_poles(world, monkeypatch, producing):
    session = open_session(world)
    session.run("reserve_steel_seed")
    ledger = builder._MATERIAL_RESERVATION_LEDGER
    ledger.mark_constructing("conversion_steel-plate", {})
    monkeypatch.setattr(builder, "_production_started", lambda *args: producing)
    monkeypatch.setattr(builder, "_transferable_or_available_stock", lambda *args: {})

    result = session.run("reserve_steel_seed")

    assert result["ok"] and result["code"] == ("seed_producing" if producing else "seed_committed")
    # Neither is a build receipt: nothing was placed by this call.
    assert result["code"] not in BUILT


@pytest.mark.parametrize("submitted", [False, True])
def test_committed_belt_side_seed_keeps_its_plan_bill(world, monkeypatch, submitted):
    # The belt-side seed bills one pole, fewer than the two-pole opening fence (review 2026-10-18).
    bill = material_reservations.plan_material_bill(steel_seed((0.5, 0.5), "east"))
    assert bill["medium-electric-pole"] == 1
    # One more passive-provider chest than the seed bills: the startup fence holds one for plastic.
    stock = {**bill, "medium-electric-pole": 4, "passive-provider-chest": bill.get("passive-provider-chest", 0) + 1}
    monkeypatch.setattr(builder, "_transferable_or_available_stock", lambda *args: stock)
    session = open_session(world)
    ledger = builder._MATERIAL_RESERVATION_LEDGER
    # What the steel operation records: assert_affordable(reserve_project=True), the persisted plan, then _submit.
    ledger.declare("conversion_steel-plate", bill, stock, target_item="steel-plate", priority=100)
    ledger.path.with_suffix(".steel-seed.json").write_text("{}")
    if submitted:
        ledger.mark_constructing("conversion_steel-plate", stock)

    result = session.run("reserve_steel_seed")

    project = ledger.projects["conversion_steel-plate"]
    assert result["ok"] and result["code"] == "seed_committed"
    assert project.required == bill
    assert project.state == ("constructing" if submitted else "ready")


def test_reopening_session_keeps_the_selected_expanded_reservation(world):
    session = open_session(world)
    ledger = builder._MATERIAL_RESERVATION_LEDGER
    stock = {"medium-electric-pole": 5, "electric-furnace": 1}
    ledger.declare("conversion_steel-plate", {"medium-electric-pole": 2, "electric-furnace": 1}, stock,
                   target_item="steel-plate", priority=100)
    before = ledger.path.read_bytes()
    session.close()

    reopened = open_session(world)
    assert builder._MATERIAL_RESERVATION_LEDGER.path.read_bytes() == before
    assert builder._MATERIAL_RESERVATION_LEDGER.projects["conversion_steel-plate"].reserved == {
        "medium-electric-pole": 2, "electric-furnace": 1}
    reopened.run("reserve_steel_seed")
    assert builder._MATERIAL_RESERVATION_LEDGER.projects["conversion_steel-plate"].required == {
        "medium-electric-pole": 2, "electric-furnace": 1}


def test_deterministic_run_still_applies_its_startup_fence_and_scheduler(world, monkeypatch):
    session = open_session(world)
    monkeypatch.setattr(builder.live_base, "game_tick", lambda client: 42)
    scheduler = object()
    monkeypatch.setattr(builder, "PriorityList", lambda path, tick: scheduler)
    monkeypatch.setattr(builder, "mission_mall_targets", lambda goals, recipes: {"steel-plate": 1})

    targets, background, priorities = builder._open_the_run(
        session.client, world.bridge, "nauvis", "player", "automation-science-pack", (),
        session.script_output, lambda message: None, bootstrap_profile_name="reduced-v1",
        reference_point=(0, 0))

    assert targets == {} and background == {"steel-plate": 1}
    assert priorities is scheduler
    assert builder._MATERIAL_RESERVATION_LEDGER.projects["conversion_steel-plate"].reserved == {
        "medium-electric-pole": 2}


def test_session_still_rejects_a_goal_without_executable_recipe_knowledge(world):
    with pytest.raises(builder.StuckError, match="No executable recipe knowledge"):
        open_session(world, goal_item="unknown-item")
    assert builder._MATERIAL_RESERVATION_LEDGER.projects == {}


def test_session_loads_live_recipes_before_validating_a_new_goal(world, monkeypatch):
    recipes = deepcopy(builder.LINE_RECIPES)
    monkeypatch.setattr(builder, "LINE_RECIPES", recipes)
    monkeypatch.setattr(recipe_data, "LINE_RECIPES", recipes)
    world.catalog_path.write_text(json.dumps({"recipes": [{
        "name": "startup-live-item", "enabled": True, "supported": True, "category": "crafting",
        "ingredients": [{"type": "item", "name": "iron-plate", "amount": 1}],
        "products": [{"type": "item", "name": "startup-live-item", "amount": 1}], "energy_ticks": 30,
    }]}))

    open_session(world, goal_item="startup-live-item")

    assert recipes["startup-live-item"]["ingredients"] == ["iron-plate"]
    assert set(builder._MATERIAL_RESERVATION_LEDGER.projects) == {builder._PLASTIC_OUTPUT_PACKET}


def test_reopening_restores_district_space_and_reconciles_only_measured_work(world, monkeypatch):
    session = open_session(world)
    districts = builder._BOOTSTRAP_DISTRICT_LEDGER
    action = {"action_type": "place_ghost", "entity": "electric-furnace", "position": {"x": 20.5, "y": 30.5}}
    districts.record_pioneer("iron-plate", "iron-ore", [action])
    districts.provision("iron-plate", reservations={role: frozenset({(20, 30), (21, 30)})
                                                  for role in REQUIRED_RESERVATION_ROLES},
                        replacement_origin=(20, 30), replacement_provider=(25.5, 30.5),
                        replacement_furnaces=6, replacement_actions=[action])
    builder._BOOTSTRAP_WORK_LEDGER.depend("steel-plate", "iron-plate", reason="missing input")
    builder._BOOTSTRAP_WORK_LEDGER.depend("steel-plate", "copper-plate", reason="still missing")
    monkeypatch.setattr(builder, "_has_producer", lambda client, surface, force, item: item == "iron-plate")
    session.close()

    open_session(world)

    assert builder._REFINERY_SITE_RESERVATIONS[("nauvis", "player", "iron-plate")] == ((20, 30), (22, 31))
    assert builder._BOOTSTRAP_WORK_LEDGER.edges == {"steel-plate": {"copper-plate": "still missing"}}
