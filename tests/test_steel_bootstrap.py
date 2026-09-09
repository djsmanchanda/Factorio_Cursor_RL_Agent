# Path: tests/test_steel_bootstrap.py
# Purpose: Compact steel geometry and phase safety regressions.
from types import SimpleNamespace
import json
import pytest
from planners.steel_bootstrap import steel_seed, VECTORS
from planners.plan_validation import validate_no_collisions, validate_build_plan
from orchestrator import autonomous_builder as builder


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
