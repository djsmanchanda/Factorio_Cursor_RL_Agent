# Path: tests/test_steel_bootstrap.py
# Purpose: Compact steel geometry and phase safety regressions.
from types import SimpleNamespace
import json
import pytest
from planners.steel_bootstrap import steel_seed, VECTORS
from planners.plan_validation import validate_no_collisions, validate_build_plan
from orchestrator import autonomous_builder as builder
from orchestrator.material_reservations import MaterialReservationLedger


@pytest.mark.parametrize("direction", list(VECTORS))
@pytest.mark.parametrize("mined", [False, True])
def test_seed_has_no_feed_belt_and_fits(direction, mined):
    plan = steel_seed((0.5, 0.5), direction, mined=mined)
    validate_build_plan(plan)
    validate_no_collisions([("seed", plan)])
    actions = plan["phases"][0]["actions"]
    assert not any("belt" in a["entity"] or "requester" in a["entity"] for a in actions)
    assert sum(a["entity"] == "electric-furnace" for a in actions) == (2 if mined else 1)
    assert all("recipe" not in a for a in actions if a["entity"] == "electric-furnace")


def test_seed_cannot_precede_iron_retirement(monkeypatch):
    monkeypatch.setattr(builder, "_bootstrap_state", lambda *_a: SimpleNamespace(lifecycle_state="validating"))
    with pytest.raises(builder.ProductionPrerequisiteDeferred) as error:
        builder._build_compact_steel_seed(object(), object(), "nauvis", "player", (0,0), (0,0), print)
    assert error.value.code == "steel_iron_retirement_wait"


def test_steel_seed_adopts_legacy_reservation_without_taking_power_bridge_stock(tmp_path):
    """The old split identity must collapse to one pole-owning transaction."""
    ledger = MaterialReservationLedger(
        tmp_path / "script-output" / "factorio_cursor_rl",
        episode_id="episode-steel-legacy", surface="nauvis", force="player",
    )
    stock = {"medium-electric-pole": 4}
    ledger.declare(
        "conversion_steel-plate", {"medium-electric-pole": 2}, stock,
        target_item="steel-plate", priority=100,
    )
    ledger.declare("power_bridge", {"medium-electric-pole": 2}, stock, priority=50)
    ledger.declare(
        "compact_steel_seed",
        {"medium-electric-pole": 2, "electric-furnace": 1}, stock,
        target_item="steel-plate", priority=50,
    )

    builder._adopt_legacy_steel_seed_reservation(ledger, stock, lambda _m: None)

    assert ledger.projects["compact_steel_seed"].state == "completed"
    assert ledger.projects["conversion_steel-plate"].required == {
        "electric-furnace": 1, "medium-electric-pole": 2,
    }
    assert ledger.projects["conversion_steel-plate"].reserved == {
        "medium-electric-pole": 2,
    }
    assert ledger.projects["power_bridge"].reserved == {
        "medium-electric-pole": 2,
    }
    assert ledger.required_stock("medium-electric-pole") == 4


def test_steel_seed_adoption_renames_legacy_project_when_fence_is_absent(tmp_path):
    ledger = MaterialReservationLedger(
        tmp_path / "script-output" / "factorio_cursor_rl",
        episode_id="episode-steel-legacy-only", surface="nauvis", force="player",
    )
    stock = {"medium-electric-pole": 2}
    ledger.declare(
        "compact_steel_seed", {"medium-electric-pole": 2}, stock,
        target_item="steel-plate", priority=50,
    )

    builder._adopt_legacy_steel_seed_reservation(ledger, stock, lambda _m: None)

    assert set(ledger.projects) == {"conversion_steel-plate", "compact_steel_seed"}
    assert ledger.projects["compact_steel_seed"].state == "completed"
    project = ledger.projects["conversion_steel-plate"]
    assert project.reserved == {"medium-electric-pole": 2}
    assert project.priority == 100


@pytest.mark.parametrize("constructing", [False, True])
def test_steel_startup_fence_does_not_shrink_expanded_bill_on_restart(tmp_path, monkeypatch, constructing):
    ledger = MaterialReservationLedger(
        tmp_path / "script-output" / "factorio_cursor_rl",
        episode_id="episode-steel-restart", surface="nauvis", force="player",
    )
    stock = {"medium-electric-pole": 4}
    ledger.declare(
        "conversion_steel-plate",
        {"electric-furnace": 1, "medium-electric-pole": 2}, stock,
        target_item="steel-plate", priority=100,
    )
    if constructing:
        ledger.mark_constructing("conversion_steel-plate", stock)
    state_before = ledger.projects["conversion_steel-plate"].state
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setattr(builder, "_production_started", lambda *_a: False)
    monkeypatch.setattr(builder, "_steel_starter_power_seed_bill", lambda: {
        "medium-electric-pole": 2,
    })
    monkeypatch.setattr(builder, "_transferable_or_available_stock", lambda *_a: stock)
    monkeypatch.setattr(
        builder, "_material_sources_and_rates", lambda *_a: ({}, {}),
    )

    builder._reserve_steel_starter_power_seed(
        object(), "nauvis", "player", {"steel-plate": 1}, lambda _m: None,
    )

    assert ledger.projects["conversion_steel-plate"].required == {
        "electric-furnace": 1, "medium-electric-pole": 2,
    }
    assert ledger.projects["conversion_steel-plate"].state == state_before


def test_steel_promotion_is_gated_before_cached_source(monkeypatch):
    monkeypatch.setattr(builder, "MANAGED_INTERMEDIATE_SOURCES", {"steel-plate": (1,2)})
    monkeypatch.setattr(builder, "_complete_material_producer", lambda *_a: None)
    ready = [False]
    monkeypatch.setattr(builder, "_power_generation_capability_started", lambda *_a: ready[0])
    calls = []
    monkeypatch.setattr(builder, "_promote_compact_steel", lambda *_a: calls.append(True) or (3,4))
    client = SimpleNamespace(command=lambda *_a: "")
    assert builder.ensure_produced(client, object(), "nauvis", "player", "steel-plate", (0,0), print) == (1,2)
    assert not calls
    ready[0] = True
    assert builder.ensure_produced(client, object(), "nauvis", "player", "steel-plate", (0,0), print) == (3,4)
    assert calls == [True]


@pytest.mark.parametrize("crafted", [False, True])
def test_replacement_must_produce_before_seed_retirement(monkeypatch, tmp_path, crafted):
    from planners.smelter_block import generate_managed_refinery_plan, refinery_interfaces
    path = tmp_path / "episode.json"
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", SimpleNamespace(path=path))
    seed = steel_seed((0.5, 0.5), "south")
    path.with_suffix(".steel-seed.json").write_text(json.dumps(seed))
    plan = generate_managed_refinery_plan("steel-plate", 6, origin_x=40, origin_y=40, variant="basic")
    interface = refinery_interfaces(6, origin_x=40, origin_y=40, variant="basic")
    path.with_suffix(".steel-district.json").write_text(json.dumps({
        "plan":plan, "provider":interface.provider, "power":interface.power_anchor,
    }))
    monkeypatch.setattr(builder, "_submit", lambda *_a, **_k: None)
    monkeypatch.setattr(builder, "bring_stage_up", lambda *_a, **_k: None)
    monkeypatch.setattr(builder, "_diagnose_machines", lambda *_a, **_k: [])
    monkeypatch.setattr(builder.live_base, "machine_health", lambda _c,_s,ps: ({}, {p:1000 if crafted else 0.5 for p in ps}))
    monkeypatch.setattr(builder.live_base, "chest_contents", lambda *_a: {"steel-plate": int(crafted)})
    monkeypatch.setattr(builder, "_ensure_power_anchor_on_generated_network", lambda *_a: None)
    retired = []
    monkeypatch.setattr(builder, "retire_entities_via_bots", lambda *args: retired.append(args[4]))
    monkeypatch.setattr(builder, "_retire_unused_starter_power_branch", lambda *_a: None)
    if crafted:
        assert builder._promote_compact_steel(object(),object(),"nauvis","player",(0,0),print) == interface.provider
        assert len(retired) == 1
        assert json.loads(path.with_suffix(".steel-district.json").read_text())["complete"]
    else:
        with pytest.raises(builder.ProductionPrerequisiteDeferred):
            builder._promote_compact_steel(object(),object(),"nauvis","player",(0,0),print)
        assert retired == []


def _ladder_loan(item):
    return SimpleNamespace(target_item=item)


def test_oil_ladder_breaks_steel_promotion_gate(monkeypatch):
    """2026-09-11: oil-refinery loan built at 0/1 on steel-plate while the
    6-furnace promotion waited on advanced circuits (which wait on oil)."""
    monkeypatch.setattr(
        builder, "active_bootstrap_loans", lambda *_a: [_ladder_loan("oil-refinery")],
    )
    monkeypatch.setattr(
        builder, "_production_started",
        lambda _c, _s, _f, item: item == "steel-plate",
    )
    monkeypatch.setattr(
        builder, "_power_generation_capability_started", lambda *_a: False,
    )
    assert builder._oil_ladder_waits_on_steel_seed(object(), "nauvis", "player") is True


def test_steel_gate_holds_without_ladder_or_seed(monkeypatch):
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: [])
    monkeypatch.setattr(builder, "_production_started", lambda _c, _s, _f, _i: True)
    monkeypatch.setattr(
        builder, "_power_generation_capability_started", lambda *_a: False,
    )
    assert builder._oil_ladder_waits_on_steel_seed(object(), "nauvis", "player") is False
    monkeypatch.setattr(
        builder, "active_bootstrap_loans",
        lambda *_a: [_ladder_loan("chemical-plant")],
    )
    monkeypatch.setattr(builder, "_production_started", lambda _c, _s, _f, _i: False)
    assert builder._oil_ladder_waits_on_steel_seed(object(), "nauvis", "player") is False
    monkeypatch.setattr(builder, "_production_started", lambda _c, _s, _f, _i: True)
    monkeypatch.setattr(
        builder, "_power_generation_capability_started", lambda *_a: True,
    )
    assert builder._oil_ladder_waits_on_steel_seed(object(), "nauvis", "player") is False


def test_prep_promotes_for_ladder_before_advanced_circuits(monkeypatch, tmp_path):
    """The prep reaches promotion (not the early False) when the ladder waits."""
    path = tmp_path / "episode.json"
    monkeypatch.setattr(
        builder, "_MATERIAL_RESERVATION_LEDGER", SimpleNamespace(path=path)
    )
    path.with_suffix(".steel-seed.json").write_text(json.dumps({"phases": []}))
    monkeypatch.setattr(
        builder, "active_bootstrap_loans", lambda *_a: [_ladder_loan("pumpjack")],
    )
    monkeypatch.setattr(
        builder, "_production_started",
        lambda _c, _s, _f, item: item == "steel-plate",
    )
    monkeypatch.setattr(
        builder, "_power_generation_capability_started", lambda *_a: False,
    )
    monkeypatch.setattr(builder, "MANAGED_INTERMEDIATE_SOURCES", {})
    promoted = []
    monkeypatch.setattr(
        builder, "_promote_compact_steel",
        lambda *_a: promoted.append(True) or (7, 8),
    )
    assert builder._prep_steel_district(
        object(), object(), "nauvis", "player", (0, 0), {}, print
    ) is True
    assert promoted == [True]
    assert builder.MANAGED_INTERMEDIATE_SOURCES["steel-plate"] == (7, 8)
