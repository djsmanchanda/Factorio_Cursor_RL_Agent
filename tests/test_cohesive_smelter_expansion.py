# Path: tests/test_cohesive_smelter_expansion.py
# Purpose: Prove live mining growth recovers one modular refinery, migrates only its owned tail, and preserves build ordering.

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator import live_base, refinery_state  # noqa: E402
from orchestrator.parts_mall import MaterialShortage  # noqa: E402
from orchestrator.stage_services import StuckError  # noqa: E402
from planners.plan_validation import actions  # noqa: E402
from planners.smelter_block import (  # noqa: E402
    generate_managed_refinery_plan,
    refinery_interfaces,
)


def _furnaces(plan: dict) -> tuple[tuple[float, float], ...]:
    return tuple(sorted(
        (action["position"]["x"], action["position"]["y"])
        for action in actions(plan) if action["entity"] == "electric-furnace"
    ))


def _state(recipe: str = "iron-plate", furnaces: int = 30):
    plan = generate_managed_refinery_plan(recipe, furnaces)
    return refinery_state.infer_refinery_state(recipe, _furnaces(plan))


def test_mining_expansion_validates_modular_ownership_before_drills() -> None:
    target_source = inspect.getsource(builder._cohesive_smelter_target)
    build_source = inspect.getsource(builder.build_mining_stage)

    assert "recover_managed_refinery(" in target_source
    assert build_source.index("_cohesive_smelter_target(") < build_source.index(
        "_submit_mining_plan("
    )
    assert build_source.index("_assert_atomic_plate_expansion_affordable(") < (
        build_source.index("_submit_mining_plan(")
    )


def test_cohesive_target_merges_starved_furnaces_and_rounds_modules(monkeypatch) -> None:
    plan = generate_managed_refinery_plan("iron-plate", 30)
    positions = _furnaces(plan)
    visible = SimpleNamespace(machine_positions=positions[:-1])
    idle = SimpleNamespace(machine_positions=(positions[-1],))
    captured = {}
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: visible)
    monkeypatch.setattr(
        builder.live_base, "find_idle_machine_row", lambda *_a, **_k: idle,
    )

    def recover(_client, _surface, _force, recipe, merged):
        captured["positions"] = merged
        return refinery_state.infer_refinery_state(recipe, merged)

    monkeypatch.setattr(builder, "recover_managed_refinery", recover)
    monkeypatch.setattr(builder, "smelter_count_for_drills", lambda *_a: 31)
    extraction = SimpleNamespace(
        smelter_origin=(100.0, 100.0), system_drill_count_before=20,
        drill_count=20, mining_productivity_bonus=0.0, ore="iron-ore",
    )

    state, target = builder._cohesive_smelter_target(
        object(), "nauvis", "player", "iron-plate", extraction, True,
        lambda _message: None,
    )

    assert len(captured["positions"]) == 30
    assert state.furnace_count == 30
    assert target == 48


def test_cohesive_target_uses_deployed_origin_not_new_search_site(monkeypatch) -> None:
    state = _state()
    line = SimpleNamespace(machine_positions=state.machine_positions)
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: line)
    monkeypatch.setattr(
        builder.live_base, "find_idle_machine_row", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(builder, "recover_managed_refinery", lambda *_a: state)
    monkeypatch.setattr(builder, "smelter_count_for_drills", lambda *_a: 31)
    extraction = SimpleNamespace(
        smelter_origin=(500.0, 500.0), system_drill_count_before=20,
        drill_count=20, mining_productivity_bonus=0.0, ore="iron-ore",
    )

    recovered, target = builder._cohesive_smelter_target(
        object(), "nauvis", "player", "iron-plate", extraction, True,
        lambda _message: None,
    )

    assert recovered.origin == (0.0, 0.0)
    assert target == 48


def test_plate_expansion_preflight_rejects_real_infrastructure(monkeypatch) -> None:
    plan = {"phases": [{"actions": [{
        "action_type": "place_ghost", "entity": "electric-furnace",
        "position": {"x": 1.5, "y": 1.5},
    }]}]}
    client = SimpleNamespace(command=lambda _command: "")
    monkeypatch.setattr(builder.live_base, "occupied_tile_owners", lambda *_a, **_k: {(0, 0): ("medium-electric-pole", 50.5, 50.5)})
    monkeypatch.setattr(builder.live_base, "water_tiles", lambda *_a: set())

    with pytest.raises(StuckError, match="intersects real infrastructure"):
        builder._plate_expansion_foundation(client, "nauvis", "player", "iron-plate", plan)


def test_own_sibling_mine_scaffold_does_not_collide_with_the_refinery(monkeypatch) -> None:
    """The 2026-08-21 iron failure: the mine submitted seconds earlier held a
    substation whose live bounding box covered ore-route tiles the declared
    footprint never claimed. Ownership is decided by planned centre."""
    plan = {"phases": [{"actions": [{
        "action_type": "place_ghost", "entity": "electric-furnace",
        "position": {"x": 1.5, "y": 1.5},
    }]}]}
    client = SimpleNamespace(command=lambda _command: "")
    monkeypatch.setattr(builder.live_base, "occupied_tile_owners", lambda *_a, **_k: {
        (11, 5): ("substation", 10.0, 4.0),
        (11, 6): ("substation", 10.0, 4.0),
    })
    monkeypatch.setattr(builder.live_base, "water_tiles", lambda *_a: set())

    assert builder._plate_expansion_foundation(
        client, "nauvis", "player", "iron-plate", plan,
        own_action_positions={("substation", 10.0, 4.0)},
    ) is None


def test_a_blocked_initial_site_defers_instead_of_queuing_a_phantom_bill(monkeypatch) -> None:
    extraction = SimpleNamespace(
        build_plan=None, expansion_positions=(), row_drill_count=0,
        expansion_step=-1, mine_origin=(10.0, 20.0), drill_count=2,
        furnace_count=6, mining_productivity_bonus=0.0,
        smelter_origin=(85.0, 82.0), ore_output=(12.5, -1.5),
        smelter_flow_direction="east", ore="iron-ore",
        system_drill_count_before=2, system_drill_target=2,
    )
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "plan_local_extraction", lambda *_a, **_k: extraction)

    def blocked(*_args, **_kwargs):
        raise StuckError(
            "iron-plate refinery extension intersects real infrastructure at [(11, 5)]"
        )

    monkeypatch.setattr(builder, "_build_initial_plate_smelter", blocked)
    with pytest.raises(builder.ProductionPrerequisiteDeferred):
        builder.build_mining_stage(
            object(), object(), "nauvis", "player", "iron-plate",
            (0.0, 0.0), lambda _message: None,
        )


def test_authorized_ore_interface_tiles_do_not_collide_with_refinery(monkeypatch) -> None:
    """The mine output belt is part of the same initial refinery transaction."""
    plan = {"phases": [{"actions": [{
        "action_type": "place_ghost", "entity": "transport-belt",
        "position": {"x": 0.5, "y": 0.5},
    }]}]}
    client = SimpleNamespace(command=lambda _command: "")
    monkeypatch.setattr(builder.live_base, "occupied_tile_owners", lambda *_a, **_k: {(0, 0): ("transport-belt", 0.5, 0.5)})
    monkeypatch.setattr(builder.live_base, "water_tiles", lambda *_a: set())

    assert builder._plate_expansion_foundation(
        client, "nauvis", "player", "copper-plate", plan, allowed_tiles={(0, 0)},
    ) is None


def test_replacement_tiles_do_not_collide_with_the_owned_end(monkeypatch) -> None:
    plan = {"phases": [{"actions": [
        {"action_type": "remove_entity", "entity": "fast-transport-belt",
         "position": {"x": 0.5, "y": 0.5}},
        {"action_type": "place_ghost", "entity": "fast-transport-belt",
         "position": {"x": 0.5, "y": 0.5}, "direction": "south"},
    ]}]}
    client = SimpleNamespace(command=lambda _command: "")
    monkeypatch.setattr(builder.live_base, "occupied_tile_owners", lambda *_a, **_k: {(0, 0): ("transport-belt", 0.5, 0.5)})
    monkeypatch.setattr(builder.live_base, "water_tiles", lambda *_a: set())

    assert builder._plate_expansion_foundation(
        client, "nauvis", "player", "iron-plate", plan,
    ) is None


def test_plate_expansion_preflight_stages_landfill_for_water(monkeypatch) -> None:
    plan = {"phases": [{"actions": [{
        "action_type": "place_ghost", "entity": "electric-furnace",
        "position": {"x": 1.5, "y": 1.5},
    }]}]}
    client = SimpleNamespace(command=lambda _command: "")
    monkeypatch.setattr(builder.live_base, "occupied_tile_owners", lambda *_a, **_k: {})
    monkeypatch.setattr(
        builder.live_base, "water_tiles", lambda *_a: {(0, 0), (2, 2), (9, 9)},
    )

    foundation = builder._plate_expansion_foundation(
        client, "nauvis", "player", "iron-plate", plan,
    )

    assert foundation is not None
    assert [action["position"] for action in foundation["phases"][0]["actions"]] == [
        {"x": 0, "y": 0}, {"x": 2, "y": 2},
    ]


def test_mining_expansion_places_landfill_before_its_mine(monkeypatch) -> None:
    calls: list[str] = []
    foundation = {"phases": [{"actions": [{
        "action_type": "place_tile_ghost", "tile": "landfill",
        "position": {"x": 0, "y": 0},
    }]}]}
    extraction = SimpleNamespace(
        build_plan=None, drill_count=0, furnace_count=0,
        mining_productivity_bonus=0.0, ore_output=(10.0, 20.0),
        smelter_flow_direction="east", system_drill_count_before=30,
        system_drill_target=30, ore="iron-ore",
    )
    state = _state()
    monkeypatch.setattr(builder, "retire_depleted_mines", lambda *_a: calls.append("retire"))
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "plan_local_extraction", lambda *_a, **_k: extraction)
    monkeypatch.setattr(builder, "_cohesive_smelter_target", lambda *_a: (state, 60))
    monkeypatch.setattr(
        builder, "_assert_atomic_plate_expansion_affordable", lambda *_a: foundation,
    )
    monkeypatch.setattr(
        builder, "_place_plate_expansion_foundation",
        lambda *_a: calls.append("foundation"),
    )
    monkeypatch.setattr(builder, "_submit_mining_plan", lambda *_a: calls.append("mine"))
    monkeypatch.setattr(builder, "_extend_plate_smelter", lambda *_a: (30.0, 40.0))

    output = builder.build_mining_stage(
        object(), object(), "nauvis", "player", "iron-plate", (0.0, 0.0),
        lambda _message: None, expand=True,
    )

    assert output == (30.0, 40.0)
    assert calls == ["foundation", "mine", "retire"]


def test_atomic_preflight_counts_mine_and_modular_delta(monkeypatch) -> None:
    captured = {}
    state = _state()
    extraction = SimpleNamespace(build_plan={"phases": [{"actions": [{
        "action_type": "place_ghost", "entity": "electric-mining-drill",
        "position": {"x": 1.5, "y": 1.5},
    }]}]})
    delta = {"phases": [{"actions": [{
        "action_type": "place_ghost", "entity": "electric-furnace",
        "position": {"x": 10.5, "y": 10.5},
    }]}]}
    monkeypatch.setattr(
        builder, "generate_managed_refinery_extension_plan", lambda *_a, **_k: delta,
    )
    monkeypatch.setattr(builder, "assert_refinery_removals_owned", lambda *_a: None)
    monkeypatch.setattr(builder, "_plate_expansion_foundation", lambda *_a, **_k: None)
    monkeypatch.setattr(
        builder, "assert_affordable",
        lambda *_a: captured.update(plan=_a[3], name=_a[4]),
    )

    builder._assert_atomic_plate_expansion_affordable(
        object(), "nauvis", "player", "iron-plate",
        extraction, state, 60, lambda _message: None,
    )

    assert captured["name"] == "expand_iron-plate_system"
    assert [
        action["entity"] for phase in captured["plan"]["phases"]
        for action in phase["actions"]
    ] == ["electric-mining-drill", "electric-furnace"]


def test_unmanaged_refinery_expansion_defers_without_opening_replacement(monkeypatch) -> None:
    calls = []
    extraction = SimpleNamespace(
        build_plan=None, drill_count=6, furnace_count=6,
        mining_productivity_bonus=0.0, ore_output=(10.0, 20.0),
        smelter_flow_direction="east", system_drill_count_before=6,
        system_drill_target=12, ore="iron-ore", smelter_origin=(30.0, 30.0),
    )
    monkeypatch.setattr(builder, "retire_depleted_mines", lambda *_a: None)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "plan_local_extraction", lambda *_a, **_k: extraction)
    monkeypatch.setattr(
        builder, "_cohesive_smelter_target",
        lambda *_a: (_ for _ in ()).throw(
            StuckError("iron-plate furnaces do not form complete six-furnace modules")
        ),
    )
    monkeypatch.setattr(
        builder, "_build_initial_plate_smelter",
        lambda *_a, **kwargs: calls.append(("refinery", kwargs.get("preflight_only", False)))
        or (31.0, 41.0),
    )
    monkeypatch.setattr(builder, "_submit_mining_plan", lambda *_a: calls.append(("mine",)))

    with pytest.raises(StuckError, match="complete six-furnace modules"):
        builder.build_mining_stage(
            object(), object(), "nauvis", "player", "iron-plate", (0.0, 0.0),
            lambda _message: None, expand=True,
        )

    assert calls == []


def test_blocked_refinery_tail_defers_without_opening_new_site(monkeypatch) -> None:
    calls = []
    extraction = SimpleNamespace(
        build_plan=None, drill_count=6, furnace_count=6,
        mining_productivity_bonus=0.0, ore_output=(10.0, 20.0),
        smelter_flow_direction="east", system_drill_count_before=6,
        system_drill_target=12, ore="iron-ore", smelter_origin=(30.0, 30.0),
    )
    state = _state()
    monkeypatch.setattr(builder, "retire_depleted_mines", lambda *_a: None)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "plan_local_extraction", lambda *_a, **_k: extraction)
    monkeypatch.setattr(builder, "_cohesive_smelter_target", lambda *_a: (state, 12))
    monkeypatch.setattr(
        builder, "_assert_atomic_plate_expansion_affordable",
        lambda *_a: (_ for _ in ()).throw(
            StuckError("iron-plate refinery extension intersects real infrastructure at [(1, 2)]")
        ),
    )
    monkeypatch.setattr(
        builder, "_build_initial_plate_smelter",
        lambda *_a, **kwargs: calls.append(("refinery", kwargs.get("preflight_only", False)))
        or (31.0, 41.0),
    )
    monkeypatch.setattr(builder, "_submit_mining_plan", lambda *_a: calls.append(("mine",)))

    with pytest.raises(
        builder.ProductionPrerequisiteDeferred,
        match="refinery extension intersects real infrastructure",
    ):
        builder.build_mining_stage(
            object(), object(), "nauvis", "player", "iron-plate", (0.0, 0.0),
            lambda _message: None, expand=True,
        )

    assert calls == []


def test_same_force_service_entity_is_not_owned_without_exact_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        builder.live_base, "entity_at",
        lambda *_a: {
            "name": "medium-electric-pole", "type": "electric-pole", "force": "player",
        },
    )

    assert not builder._own_service_infrastructure(
        object(), "nauvis", "player", ("medium-electric-pole", 10.5, 20.5),
    )


def test_mining_expansion_rejects_full_bill_before_submitting_mine(monkeypatch) -> None:
    extraction = SimpleNamespace(build_plan={"phases": []})
    state = _state()
    monkeypatch.setattr(builder, "retire_depleted_mines", lambda *_a: None)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "plan_local_extraction", lambda *_a, **_k: extraction)
    monkeypatch.setattr(builder, "_cohesive_smelter_target", lambda *_a: (state, 60))
    monkeypatch.setattr(
        builder, "_assert_atomic_plate_expansion_affordable",
        lambda *_a: (_ for _ in ()).throw(
            MaterialShortage("expand_iron-plate_system", {"fast-transport-belt": 74}, {})
        ),
    )
    monkeypatch.setattr(
        builder, "_submit_mining_plan",
        lambda *_a: pytest.fail("mining must wait for its refinery bill"),
    )

    with pytest.raises(MaterialShortage, match="fast-transport-belt"):
        builder.build_mining_stage(
            object(), object(), "nauvis", "player", "iron-plate",
            (0.0, 0.0), lambda _message: None, expand=True,
        )


@pytest.mark.parametrize("recipe", ["iron-plate", "stone-brick"])
def test_mining_expansion_refuses_without_recoverable_refinery(monkeypatch, recipe) -> None:
    extraction = SimpleNamespace(build_plan={"phases": []})
    monkeypatch.setattr(builder, "retire_depleted_mines", lambda *_a: None)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "plan_local_extraction", lambda *_a, **_k: extraction)
    monkeypatch.setattr(builder, "_cohesive_smelter_target", lambda *_a: (None, None))
    monkeypatch.setattr(
        builder, "_submit_mining_plan",
        lambda *_a: pytest.fail("missing refinery must block mine expansion"),
    )

    with pytest.raises(StuckError, match="no recoverable managed refinery"):
        builder.build_mining_stage(
            object(), object(), "nauvis", "player", recipe,
            (0.0, 0.0), lambda _message: None, expand=True,
        )


def test_existing_modular_refinery_runs_power_recovery(monkeypatch) -> None:
    state = _state("copper-plate", 6)
    calls = []
    monkeypatch.setattr(
        builder, "_bring_modular_refinery_up",
        lambda *_a, **_k: calls.append((_a, _k)),
    )

    output = builder._extend_plate_smelter(
        object(), object(), "nauvis", "player", "copper-plate",
        state, 6, (77.5, -18.5), lambda _message: None,
    )

    assert output == state.interfaces.provider
    assert calls


def test_basic_refinery_no_growth_keeps_basic_provider(monkeypatch) -> None:
    plan = generate_managed_refinery_plan("iron-plate", 6, variant="basic")
    state = refinery_state.infer_refinery_state("iron-plate", _furnaces(plan), variant="basic")
    calls = []
    monkeypatch.setattr(
        builder, "_bring_modular_refinery_up",
        lambda *_a, **_k: calls.append(_k.get("variant")),
    )

    output = builder._extend_plate_smelter(
        object(), object(), "nauvis", "player", "iron-plate",
        state, 6, (10.0, 10.0), lambda _message: None,
    )

    assert output == state.interfaces.provider
    assert calls == ["basic"]



def test_basic_refinery_growth_preserves_bootstrap_variant(monkeypatch) -> None:
    plan = generate_managed_refinery_plan("iron-plate", 6, variant="basic")
    state = refinery_state.infer_refinery_state("iron-plate", _furnaces(plan), variant="basic")
    captured = {}
    monkeypatch.setattr(
        builder, "generate_managed_refinery_extension_plan",
        lambda *args, **kwargs: captured.update(kwargs) or {"phases": []},
    )
    monkeypatch.setattr(builder, "assert_refinery_removals_owned", lambda *_a: None)
    monkeypatch.setattr(builder, "_ensure_plan_construction_coverage", lambda *_a: None)
    monkeypatch.setattr(builder, "_submit", lambda *_a, **_k: None)
    monkeypatch.setattr(builder, "_bring_modular_refinery_up", lambda *_a, **_k: None)

    builder._extend_plate_smelter(
        object(), object(), "nauvis", "player", "iron-plate",
        state, 12, (10.0, 10.0), lambda _message: None,
    )

    assert captured["current_variant"] == "basic"
    assert captured["target_variant"] == "basic"
def test_extension_adds_coverage_before_tail_migration(monkeypatch) -> None:
    state = _state()
    calls = []
    monkeypatch.setattr(builder, "assert_refinery_removals_owned", lambda *_a: None)
    monkeypatch.setattr(
        builder, "_ensure_plan_construction_coverage",
        lambda *_a: calls.append("coverage"),
    )
    monkeypatch.setattr(
        builder, "extend_power", lambda *_a: calls.append("power") or True,
    )
    monkeypatch.setattr(builder, "_submit", lambda *_a, **_k: calls.append("submit"))
    monkeypatch.setattr(
        builder, "_bring_modular_refinery_up", lambda *_a, **_k: calls.append("bring"),
    )

    client = SimpleNamespace(command=lambda _text: "")
    builder._extend_plate_smelter(
        client, object(), "nauvis", "player", "iron-plate",
        state, 60, (10.0, 10.0), lambda _message: None,
    )

    assert calls[:2] == ["coverage", "power"]
    assert calls[-2:] == ["submit", "bring"]


def test_replacement_services_use_the_future_footprint(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        builder, "_ensure_plan_construction_coverage",
        lambda *_a: calls.append("coverage"),
    )
    monkeypatch.setattr(
        builder, "extend_power",
        lambda *_a: calls.append(("power", _a[4])) or True,
    )
    replacement = {"phases": [{"actions": [
        {"entity": "substation", "position": {"x": 10.0, "y": 10.0}},
        {"entity": "medium-electric-pole", "position": {"x": 20.0, "y": 10.0}},
    ]}]}
    delta = {"phases": [{"actions": [
        {"action_type": "remove_entity", "entity": "transport-belt",
         "position": {"x": 1.0, "y": 1.0}},
    ]}]}

    builder._prepare_replacement_services(
        SimpleNamespace(command=lambda _text: ""), object(), "nauvis", "player",
        replacement, delta, lambda _message: None,
    )

    assert calls == ["coverage", ("power", (10.0, 10.0)), ("power", (20.0, 10.0))]


def test_replacement_services_refuse_roboport_removal_without_alternative(monkeypatch) -> None:
    monkeypatch.setattr(
        builder, "_ensure_plan_construction_coverage",
        lambda *_a: pytest.fail("coverage must not be claimed before the alternate chain"),
    )
    delta = {"phases": [{"actions": [
        {"action_type": "remove_entity", "entity": "roboport",
         "position": {"x": 1.0, "y": 1.0}},
    ]}]}

    with pytest.raises(StuckError, match="alternate coverage chain"):
        builder._prepare_replacement_services(
            SimpleNamespace(command=lambda _text: ""), object(), "nauvis", "player",
            {"phases": []}, delta, lambda _message: None,
        )


def test_initial_refinery_uses_head_on_ore_belt_and_provider_side_tap(monkeypatch) -> None:
    captured = {}
    order = []
    extraction = SimpleNamespace(
        smelter_origin=(20.0, -10.0), furnace_count=2, ore="iron-ore",
    )

    def preflight(*args, **kwargs):
        captured["source"] = args[5]
        captured["feed"] = args[6]
        captured["kwargs"] = kwargs
        return ([{
            "action_type": "place_ghost", "entity": "transport-belt",
            "position": {"x": 10.5, "y": 10.5}, "direction": "east",
        }], "transport-belt")

    monkeypatch.setattr(builder, "preflight_ingredient_transport", preflight)
    monkeypatch.setattr(builder, "_plate_expansion_foundation", lambda *_a, **_k: None)
    monkeypatch.setattr(builder, "assert_affordable", lambda *_a: None)
    monkeypatch.setattr(builder, "_ensure_plan_construction_coverage", lambda *_a: None)
    monkeypatch.setattr(
        builder, "_submit",
        lambda *_a, **_k: (captured.update(plan=_a[3]), order.append("submit")),
    )
    monkeypatch.setattr(
        builder, "_bring_modular_refinery_up",
        lambda *_a, **_k: order.append("healthy"),
    )
    monkeypatch.setattr(
        builder, "_retire_standing_bootstrap_cells",
        lambda *_a, **_k: order.append("retire") or 1,
    )

    output = builder._build_initial_plate_smelter(
        object(), object(), "nauvis", "player", "iron-plate", extraction,
        (5.5, -2.5), (0.0, 0.0), lambda _message: None,
    )

    interface = refinery_interfaces(6, origin_x=20, origin_y=-10, variant="basic")
    assert captured["source"] == (5.5, -2.5)
    assert captured["feed"] == interface.ore_inputs[0]
    assert captured["kwargs"]["destination_is_belt"] is True
    assert captured["kwargs"]["destination_belt_direction"] == "east"
    assert captured["kwargs"]["reserved_transport_belts"] > 0
    assert output == interface.provider
    assert order == ["submit", "healthy", "retire"]
    assert any(
        action["entity"] == "passive-provider-chest"
        for action in actions(captured["plan"])
    )


def test_initial_refinery_keeps_the_mine_transaction_planned_on_build_pass(
    monkeypatch,
) -> None:
    captured = {}
    extraction = SimpleNamespace(
        smelter_origin=(20.0, -10.0), furnace_count=2, ore="iron-ore",
        build_plan={"phases": []},
    )

    def preflight(*args, **kwargs):
        captured["kwargs"] = kwargs
        return ([], "transport-belt")

    monkeypatch.setattr(builder, "preflight_ingredient_transport", preflight)
    monkeypatch.setattr(
        builder, "_plate_expansion_foundation", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(builder, "assert_affordable", lambda *_a: None)
    monkeypatch.setattr(builder, "_ensure_plan_construction_coverage", lambda *_a: None)
    monkeypatch.setattr(builder, "_submit", lambda *_a, **_k: None)
    monkeypatch.setattr(
        builder, "_bring_modular_refinery_up", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder, "_retire_standing_bootstrap_cells", lambda *_a, **_k: 0,
    )

    builder._build_initial_plate_smelter(
        object(), object(), "nauvis", "player", "iron-plate", extraction,
        (5.5, -2.5), (0.0, 0.0), lambda _message: None,
    )

    assert captured["kwargs"]["planned_belt_source"] == (6.5, -2.5)


def test_bootstrap_retirement_removes_the_mine_logistic_intake(monkeypatch) -> None:
    monkeypatch.setattr(
        builder.live_base, "bootstrap_cell_origins",
        lambda *_a, **_k: [(145.5, -74.5)],
    )
    monkeypatch.setattr(
        builder.live_base, "intake_candidate_tiles",
        # The completed haul extends the apparent row past the original head;
        # teardown must still inspect the recorded ore output first.
        lambda *_a, **_k: [(91.5, -39.5)],
    )

    def entity_at(_client, _surface, position):
        if position == (89.5, -40.5):
            return {"name": "fast-inserter", "type": "inserter"}
        if position == (89.5, -41.5):
            return {"name": "passive-provider-chest", "type": "logistic-container"}
        return None

    monkeypatch.setattr(builder.live_base, "entity_at", entity_at)
    submissions = []
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, name, _e, **_k: submissions.append((name, plan)),
    )

    removed = builder._retire_standing_bootstrap_cells(
        object(), object(), "nauvis", "player", "copper-plate",
        "copper-ore", (89.5, -39.5), lambda _message: None,
    )

    assert removed == 1
    assert [name for name, _plan in submissions] == [
        "retire_logistic_copper-plate_cell",
        "retire_copper-ore_logistic_intake",
    ]
    intake_actions = submissions[1][1]["phases"][0]["actions"]
    assert {(action["entity"], tuple(action["position"].values()))
            for action in intake_actions} == {
        ("fast-inserter", (89.5, -40.5)),
        ("passive-provider-chest", (89.5, -41.5)),
    }


def test_planned_footprint_ignores_retirement_actions() -> None:
    plan = {"phases": [{"actions": [
        {"action_type": "remove_entity", "entity": "electric-furnace",
         "position": {"x": 1.5, "y": 1.5}},
        {"action_type": "place_ghost", "entity": "fast-transport-belt",
         "position": {"x": 10.5, "y": 10.5}},
    ]}]}

    assert builder.planned_footprint_tiles(plan) == {(10, 10)}


class _RconOutput:
    def __init__(self, output: str):
        self.output = output
        self.commands = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        return self.output


def test_idle_furnace_survey_includes_real_and_ghost_rows() -> None:
    client = _RconOutput("80.5:-15.5,83.5:-15.5")

    line = live_base.find_idle_machine_row(
        client, "nauvis", "player", "copper-plate", "electric-furnace",
        (80.0, -19.0),
    )

    assert line is not None
    assert line.machine_positions == ((80.5, -15.5), (83.5, -15.5))
    assert "ghost_name='electric-furnace'" in client.commands[0]


def test_partial_conversion_failure_bridges_its_scaffold(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(builder.live_base, "pole_network_id", lambda *_a: 8)
    monkeypatch.setattr(
        builder, "extend_power", lambda *args: calls.append(args) or True,
    )

    builder._recover_partial_conversion_power(
        object(), object(), "nauvis", "player", "copper-plate",
        (75.0, -14.5), lambda _message: None,
    )

    assert calls and calls[0][4] == (75.0, -14.5)
