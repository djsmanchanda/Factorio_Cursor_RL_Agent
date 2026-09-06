# Path: tests/test_cohesive_smelter_expansion.py
# Purpose: Prove live mining growth recovers one modular refinery, migrates only its owned tail, and preserves build ordering.

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator import live_base, refinery_state  # noqa: E402
from orchestrator.bootstrap_district import (  # noqa: E402
    BootstrapDistrictLedger, REQUIRED_RESERVATION_ROLES,
)
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
    assert build_source.index("_submit_mining_plan(") < build_source.index(
        "_extend_plate_smelter("
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
    assert target == 36


def test_refinery_recovery_ignores_disconnected_recipe_less_block(monkeypatch) -> None:
    """A direct copper starter must not adopt a starved iron module nearby."""
    visible = SimpleNamespace(machine_positions=((57.5, 26.5),))
    foreign = _furnaces(generate_managed_refinery_plan(
        "iron-plate", 6, origin_x=95.0, origin_y=15.0, variant="basic",
    ))
    idle = SimpleNamespace(machine_positions=foreign)
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: visible)
    monkeypatch.setattr(
        builder.live_base, "find_idle_machine_row", lambda *_a, **_k: idle,
    )
    captured: list[tuple[tuple[float, float], ...]] = []

    def reject_unmanaged(_client, _surface, _force, _recipe, positions, **_kwargs):
        captured.append(tuple(sorted(positions)))
        raise ValueError("not a complete managed module")

    monkeypatch.setattr(builder, "recover_managed_refinery", reject_unmanaged)
    extraction = SimpleNamespace(
        smelter_origin=(82.0, -79.0), system_drill_count_before=0,
        drill_count=6, mining_productivity_bonus=0.0, ore="copper-ore",
    )

    with pytest.raises(builder.ProductionPrerequisiteDeferred):
        builder._cohesive_smelter_target(
            object(), "nauvis", "player", "copper-plate", extraction, True,
            lambda _message: None,
        )

    assert captured == [((57.5, 26.5),)]


def test_cohesive_target_keeps_valid_block_when_partial_expansion_is_nearby(
    monkeypatch,
) -> None:
    """Recipe-less expansion furnaces must not invalidate the six-furnace block.

    A live iron run had six recipe-visible furnaces plus three or four nearby
    machines already placed for the next phase.  Merging those machines before
    recovery made a 9/10-furnace pseudo-module and permanently deferred the
    6->12 expansion.
    """
    state = _state("iron-plate", 6)
    extra = ((100.5, 100.5), (103.5, 100.5), (106.5, 100.5))
    line = SimpleNamespace(machine_positions=state.machine_positions)
    idle = SimpleNamespace(machine_positions=extra)
    calls = []

    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: line)
    monkeypatch.setattr(
        builder.live_base, "find_idle_machine_row", lambda *_a, **_k: idle,
    )

    def recover(_client, _surface, _force, recipe, positions):
        calls.append(tuple(sorted(positions)))
        if set(positions) != set(state.machine_positions):
            raise ValueError("furnaces do not form complete six-furnace modules")
        return state

    monkeypatch.setattr(builder, "recover_managed_refinery", recover)
    monkeypatch.setattr(builder, "smelter_count_for_drills", lambda *_a: 7)
    extraction = SimpleNamespace(
        smelter_origin=(100.0, 100.0), system_drill_count_before=6,
        drill_count=6, mining_productivity_bonus=0.0, ore="iron-ore",
    )

    recovered, target = builder._cohesive_smelter_target(
        object(), "nauvis", "player", "iron-plate", extraction, True,
        lambda _message: None,
    )

    assert recovered == state
    assert target == 12
    # Disconnected recipe-less machines are filtered before recovery, so the
    # valid recipe-visible block succeeds on the first ownership check.
    assert len(calls) == 1


def test_cohesive_target_defers_an_incomplete_visible_refinery(monkeypatch) -> None:
    """Partially configured furnaces are pending construction, never capacity."""
    partial = (
        (3.5, 4.5), (9.5, 4.5), (3.5, 7.5), (9.5, 7.5),
        (3.5, 10.5), (9.5, 10.5), (15.5, 4.5),
    )
    line = SimpleNamespace(machine_positions=partial)
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: line)
    monkeypatch.setattr(
        builder.live_base, "find_idle_machine_row", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder, "recover_managed_refinery",
        lambda *_a: (_ for _ in ()).throw(ValueError("incomplete module")),
    )
    extraction = SimpleNamespace(
        smelter_origin=(0.0, 0.0), system_drill_count_before=6,
        drill_count=6, mining_productivity_bonus=0.0, ore="iron-ore",
    )

    with pytest.raises(builder.ProductionPrerequisiteDeferred, match="incomplete furnace modules"):
        builder._cohesive_smelter_target(
            object(), "nauvis", "player", "iron-plate", extraction, True,
            lambda _message: None,
        )


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
    assert target == 36


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


def test_plate_expansion_marks_an_obstructing_roboport_for_relocation(monkeypatch) -> None:
    plan = {"phases": [{"actions": [{
        "action_type": "place_ghost", "entity": "electric-furnace",
        "position": {"x": 1.5, "y": 1.5},
    }]}]}
    client = SimpleNamespace(command=lambda _command: "")
    monkeypatch.setattr(
        builder.live_base, "occupied_tile_owners",
        lambda *_a, **_k: {(0, 0): ("roboport", 0.0, 0.0)},
    )
    monkeypatch.setattr(builder.live_base, "water_tiles", lambda *_a: set())

    preparation = builder._plate_expansion_foundation(
        client, "nauvis", "player", "iron-plate", plan,
    )

    assert preparation is not None
    assert preparation["relocate_roboports"] == [(0.0, 0.0)]


def test_obstructing_roboport_is_replaced_before_removal(monkeypatch) -> None:
    submitted = []
    monkeypatch.setattr(
        builder.live_base, "roboport_positions",
        lambda *_a: [(0.0, 0.0), (30.0, 0.0)],
    )
    monkeypatch.setattr(builder.live_base, "area_clear", lambda *_a: True)
    monkeypatch.setattr(builder, "extend_power", lambda *_a: True)
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, name, _emit, **_k: submitted.append((name, plan)),
    )

    builder._relocate_roboport_for_expansion(
        SimpleNamespace(command=lambda _text: ""), object(), "nauvis", "player",
        (0.0, 0.0), {(0, 0)}, lambda _message: None,
    )

    assert [name for name, _plan in submitted] == [
        "relocate_smelter_roboport", "retire_obstructing_roboport",
    ]
    placed = submitted[0][1]["phases"][0]["actions"][0]["position"]
    assert (placed["x"], placed["y"]) != (0.0, 0.0)
    removed = submitted[1][1]["phases"][0]["actions"][0]["position"]
    assert removed == {"x": 0.0, "y": 0.0}


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
        builder, "_electric_furnace_producer_started", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder, "_assert_atomic_plate_expansion_affordable",
        lambda *_a, **_k: foundation,
    )
    monkeypatch.setattr(
        builder, "_place_plate_expansion_foundation",
        lambda *_a: calls.append("foundation"),
    )
    monkeypatch.setattr(
        builder, "_submit_mining_plan", lambda *_a, **_k: calls.append("mine"),
    )
    monkeypatch.setattr(
        builder, "_extend_plate_smelter",
        lambda *_a, **_k: calls.append("extend") or (30.0, 40.0),
    )

    output = builder.build_mining_stage(
        object(), object(), "nauvis", "player", "iron-plate", (0.0, 0.0),
        lambda _message: None, expand=True,
    )

    assert output == (30.0, 40.0)
    assert calls == ["foundation", "mine", "extend", "retire"]


def test_mining_expansion_submits_drills_before_refinery_growth_waits(
    monkeypatch,
) -> None:
    """A slow furnace-growth wait must not suppress its paired mine batch."""
    state = _state("iron-plate", 6)
    extraction = SimpleNamespace(
        build_plan={"phases": [{"name": "direct_mine_output", "actions": [{
            "action_type": "place_ghost", "entity": "electric-mining-drill",
            "position": {"x": 1.5, "y": 1.5},
        }]}]},
        expansion_positions=(), drill_count=6, furnace_count=6,
        mining_productivity_bonus=0.0, ore_output=(10.0, 20.0),
        smelter_flow_direction="east", system_drill_count_before=6,
        system_drill_target=12, ore="iron-ore",
    )
    calls: list[str] = []
    monkeypatch.setattr(builder, "retire_depleted_mines", lambda *_a: None)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "plan_local_extraction", lambda *_a, **_k: extraction)
    monkeypatch.setattr(builder, "_cohesive_smelter_target", lambda *_a: (state, 12))
    monkeypatch.setattr(builder, "_electric_furnace_producer_started", lambda *_a: True)
    monkeypatch.setattr(
        builder, "_assert_atomic_plate_expansion_affordable",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(builder, "_place_plate_expansion_foundation", lambda *_a: None)
    monkeypatch.setattr(
        builder, "_submit_mining_plan", lambda *_a, **_k: calls.append("mine"),
    )
    monkeypatch.setattr(
        builder, "_extend_plate_smelter",
        lambda *_a, **_k: (_ for _ in ()).throw(
            builder.ProductionPrerequisiteDeferred(
                "growth must finish", code="refinery_growth_construction_wait",
                state="constructing",
            )
        ),
    )

    with pytest.raises(builder.ProductionPrerequisiteDeferred):
        builder.build_mining_stage(
            object(), object(), "nauvis", "player", "iron-plate", (0.0, 0.0),
            lambda _message: None, expand=True,
        )

    assert calls == ["mine"]


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
        lambda *_a, **_k: (_ for _ in ()).throw(
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
        builder, "_electric_furnace_producer_started", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder, "_assert_atomic_plate_expansion_affordable",
        lambda *_a, **_k: (_ for _ in ()).throw(
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


def test_bootstrap_cap_applies_to_the_cohesive_total_not_the_new_batch(
    monkeypatch,
) -> None:
    extraction = SimpleNamespace(
        build_plan={"phases": []}, drill_count=12, furnace_count=12,
        mining_productivity_bonus=0.0, ore_output=(10.0, 20.0),
        smelter_flow_direction="east", system_drill_count_before=12,
        system_drill_target=24, ore="iron-ore", smelter_origin=(30.0, 30.0),
    )
    state = _state("iron-plate", 12)
    monkeypatch.setattr(builder, "retire_depleted_mines", lambda *_a: None)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "plan_local_extraction", lambda *_a, **_k: extraction)
    monkeypatch.setattr(builder, "_cohesive_smelter_target", lambda *_a: (state, 24))
    monkeypatch.setattr(
        builder, "_electric_furnace_producer_started", lambda *_a: False,
    )
    monkeypatch.setattr(
        builder, "_assert_atomic_plate_expansion_affordable",
        lambda *_a, **_k: pytest.fail("cap must run before expansion preflight"),
    )

    with pytest.raises(builder.ProductionPrerequisiteDeferred) as failure:
        builder.build_mining_stage(
            object(), object(), "nauvis", "player", "iron-plate",
            (0.0, 0.0), lambda _message: None, expand=True,
        )

    assert failure.value.code == "electric_furnace_supply_wait"
    assert failure.value.details["planned_furnaces"] == 24


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

    with pytest.raises(
        builder.ProductionPrerequisiteDeferred,
        match="no recoverable managed refinery",
    ):
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
        lambda *_a, **_k: calls.append("coverage"),
    )
    monkeypatch.setattr(
        builder, "extend_power", lambda *_a, **_k: calls.append("power") or True,
    )
    monkeypatch.setattr(builder, "_submit", lambda *_a, **_k: calls.append("submit"))
    monkeypatch.setattr(builder, "missing_refinery_placements", lambda *_a: ())
    monkeypatch.setattr(builder, "_wait_for_ghosts", lambda *_a, **_k: 0)
    monkeypatch.setattr(builder, "assert_affordable", lambda *_a, **_k: None)
    monkeypatch.setattr(
        builder, "_bring_modular_refinery_up", lambda *_a, **_k: calls.append("bring"),
    )

    client = SimpleNamespace(command=lambda _text: "")
    builder._extend_plate_smelter(
        client, object(), "nauvis", "player", "iron-plate",
        state, 60, (10.0, 10.0), lambda _message: None,
    )

    assert calls[0] == "coverage"
    assert calls[-2:] == ["submit", "bring"]


def test_rejected_extension_does_not_commit_future_bootstrap_ownership(
    monkeypatch,
) -> None:
    state = _state("iron-plate", 6)
    calls = []
    monkeypatch.setattr(builder, "assert_refinery_removals_owned", lambda *_a: None)
    monkeypatch.setattr(builder, "_prepare_replacement_services", lambda *_a: None)
    monkeypatch.setattr(
        builder, "missing_refinery_placements",
        lambda *_a: ({"entity": "electric-furnace"},),
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda *_a, **_k: (_ for _ in ()).throw(
            MaterialShortage("extend_iron-plate_refinery", {"electric-furnace": 3}, {})
        ),
    )
    monkeypatch.setattr(
        builder, "_record_bootstrap_replacement",
        lambda *_a: calls.append("ownership"),
    )

    with pytest.raises(MaterialShortage):
        builder._extend_plate_smelter(
            object(), object(), "nauvis", "player", "iron-plate",
            state, 12, (10.0, 10.0), lambda _message: None,
        )

    assert calls == []


def test_unfinished_growth_defers_without_submitting_provider_cutover(
    monkeypatch,
) -> None:
    state = _state("iron-plate", 6)
    submitted: list[tuple[str, dict]] = []
    committed: list[int] = []
    monkeypatch.setattr(builder, "assert_refinery_removals_owned", lambda *_a: None)
    monkeypatch.setattr(builder, "_prepare_replacement_services", lambda *_a: None)
    monkeypatch.setattr(
        builder, "missing_refinery_placements",
        lambda *_a: ({"entity": "electric-furnace"},),
    )
    monkeypatch.setattr(builder, "_wait_for_ghosts", lambda *_a, **_k: 7)
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, name, _e, **_k: submitted.append((name, plan)),
    )
    monkeypatch.setattr(
        builder, "_record_bootstrap_replacement",
        lambda _r, _p, _o, count: committed.append(count),
    )

    with pytest.raises(
        builder.ProductionPrerequisiteDeferred,
        match="growth must finish before its provider moves",
    ):
        builder._extend_plate_smelter(
            object(), object(), "nauvis", "player", "iron-plate",
            state, 12, (10.0, 10.0), lambda _message: None,
        )

    assert [name for name, _plan in submitted] == ["prepare_iron-plate_refinery_growth"]
    assert not any(
        action["action_type"] == "remove_entity"
        for action in actions(submitted[0][1])
    )
    assert committed == []


def test_retry_uses_ledger_owner_after_all_growth_furnaces_are_visible(
    monkeypatch,
) -> None:
    old_plan = generate_managed_refinery_plan("iron-plate", 6, variant="basic")
    grown_plan = generate_managed_refinery_plan("iron-plate", 12, variant="basic")
    observed = refinery_state.infer_refinery_state(
        "iron-plate", _furnaces(grown_plan), variant="basic",
    )
    owned = tuple(
        action for action in actions(old_plan)
        if action["action_type"] in {"place_entity", "place_ghost"}
    )
    monkeypatch.setattr(
        builder, "_bootstrap_state",
        lambda _recipe: SimpleNamespace(
            replacement_origin=observed.origin,
            replacement_furnaces=6,
            replacement_actions=owned,
        ),
    )

    committed = builder._committed_refinery_state(
        "iron-plate", observed, 12,
    )

    assert observed.furnace_count == 12
    assert committed.furnace_count == 6
    assert committed.owned_actions == owned


def test_replacement_services_use_the_future_footprint(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        builder, "_ensure_plan_construction_coverage",
        lambda *_a, **_k: calls.append("coverage"),
    )
    monkeypatch.setattr(
        builder, "extend_power",
        lambda *_a, **_k: calls.append(("power", _a[4], _k["reserved_tiles"])) or True,
    )
    replacement = {"phases": [{"actions": [
        {"action_type": "place_ghost", "entity": "substation", "position": {"x": 10.0, "y": 10.0}},
        {"action_type": "place_ghost", "entity": "medium-electric-pole", "position": {"x": 20.0, "y": 10.0}},
    ]}]}
    delta = {"phases": [{"actions": [
        {"action_type": "remove_entity", "entity": "transport-belt",
         "position": {"x": 1.0, "y": 1.0}},
    ]}]}

    builder._prepare_replacement_services(
        SimpleNamespace(command=lambda _text: ""), object(), "nauvis", "player",
        replacement, delta, lambda _message: None,
    )

    future = builder.planned_footprint_tiles(replacement)
    assert calls == [
        "coverage",
        ("power", (10.0, 10.0), future),
        ("power", (20.0, 10.0), future),
    ]


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
    transport_tiles = frozenset({(10, 10), (11, 10)})
    owned_route = ({
        "action_type": "place_ghost", "entity": "transport-belt",
        "position": {"x": 10.5, "y": 10.5}, "direction": "east",
    },)
    monkeypatch.setattr(
        builder, "_bootstrap_state",
        lambda _recipe: SimpleNamespace(
            lifecycle_state="provisioning",
            replacement_origin=(20.0, -10.0),
            reservations={"transport_service": transport_tiles},
            transport_source=(5.5, -2.5), transport_actions=owned_route,
        ),
    )

    monkeypatch.setattr(
        builder, "preflight_ingredient_transport",
        lambda *_a, **_k: pytest.fail("owned route must not be replanned"),
    )
    monkeypatch.setattr(builder, "_plate_expansion_foundation", lambda *_a, **_k: None)
    monkeypatch.setattr(builder, "assert_affordable", lambda *_a: None)
    monkeypatch.setattr(builder, "_ensure_plan_construction_coverage", lambda *_a: None)
    monkeypatch.setattr(
        builder, "_submit",
        lambda *_a, **_k: (captured.update(plan=_a[3]), order.append("submit")),
    )
    monkeypatch.setattr(
        builder, "_mark_bootstrap_replacement_submitted",
        lambda _recipe: order.append("submitted"),
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
    assert output == interface.provider
    assert order == ["submit", "submitted", "healthy", "retire"]
    assert owned_route[0] in actions(captured["plan"])
    assert any(
        action["entity"] == "passive-provider-chest"
        for action in actions(captured["plan"])
    )


def test_submitted_bootstrap_reconciles_without_replanning_or_resubmitting(
    tmp_path, monkeypatch,
) -> None:
    ledger = BootstrapDistrictLedger(
        tmp_path / "script-output" / "factorio_cursor_rl",
        episode_id="episode-1", surface="nauvis", force="player",
        bootstrap_profile="reduced-v1",
    )
    ledger.record_pioneer(
        "copper-plate", "copper-ore", [{
            "action_type": "place_ghost", "entity": "electric-furnace",
            "position": {"x": 1.5, "y": 2.5},
        }],
    )
    reservations = {
        role: frozenset({(index, 10)})
        for index, role in enumerate(sorted(REQUIRED_RESERVATION_ROLES))
    }
    ledger.provision(
        "copper-plate", reservations=reservations,
        replacement_origin=(20.0, 30.0), replacement_provider=(34.5, 42.5),
        replacement_furnaces=6,
        replacement_actions=[{
            "action_type": "place_ghost", "entity": "electric-furnace",
            "position": {"x": 20.5, "y": 30.5},
        }],
        transport_source=(10.5, 10.5),
        transport_actions=[{
            "action_type": "place_ghost", "entity": "transport-belt",
            "position": {"x": 10.5, "y": 10.5}, "direction": "east",
        }],
    )
    state = ledger.mark_replacement_submitted("copper-plate")
    extraction = SimpleNamespace(
        build_plan=None, expansion_positions=(), smelter_reserved_area=None,
        smelter_origin=state.replacement_origin, ore_output=state.transport_source,
        ore="copper-ore", row_drill_count=0, drill_count=0,
    )
    monkeypatch.setattr(builder, "_bootstrap_state", lambda _recipe: state)
    monkeypatch.setattr(builder, "_essential_belt_type", lambda *_a: "transport-belt")
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "plan_local_extraction", lambda *_a, **_k: extraction)
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: None)
    monkeypatch.setattr(builder.live_base, "find_idle_machine_row", lambda *_a, **_k: None)
    reconciled = []
    monkeypatch.setattr(
        builder, "_reconcile_submitted_bootstrap_replacement",
        lambda *_a: reconciled.append(_a[4]) or state.replacement_provider,
    )
    monkeypatch.setattr(
        builder, "_build_initial_plate_smelter",
        lambda *_a, **_k: pytest.fail("submitted replacement must not be replayed"),
    )

    output = builder.build_mining_stage(
        object(), object(), "nauvis", "player", "copper-plate",
        (0.0, 0.0), lambda _message: None,
    )

    assert output == state.replacement_provider
    assert reconciled == [state]


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


def test_earmarked_initial_refinery_is_not_advertised_before_it_is_built(
    monkeypatch,
) -> None:
    extraction = SimpleNamespace(
        smelter_origin=(20.0, -10.0), furnace_count=6, ore="iron-ore",
    )
    monkeypatch.setattr(
        builder, "preflight_ingredient_transport", lambda *_a, **_k: ([], "transport-belt"),
    )
    monkeypatch.setattr(builder, "_plate_expansion_foundation", lambda *_a, **_k: None)
    monkeypatch.setattr(builder, "assert_affordable", lambda *_a: None)
    submitted: list[dict] = []
    monkeypatch.setattr(
        builder, "_submit", lambda *_a, **_k: submitted.append(_a[3]),
    )
    coverage: list[tuple[float, float]] = []
    monkeypatch.setattr(
        builder, "ensure_logistic_coverage",
        lambda *_a, **_k: coverage.extend(_a[4]) or False,
    )
    network_ids = iter((8, 8))
    generation = iter((0.0, 167.0))
    monkeypatch.setattr(
        builder.live_base, "pole_network_id", lambda *_a: next(network_ids),
    )
    monkeypatch.setattr(
        builder.live_base, "network_generation_kw", lambda *_a: next(generation),
    )
    powered: list[tuple[float, float]] = []
    monkeypatch.setattr(
        builder, "extend_power", lambda *_a, **_k: powered.append(_a[4]) or True,
    )
    monkeypatch.setattr(
        builder, "_bring_modular_refinery_up", lambda *_a, **_k: pytest.fail("must stay asynchronous"),
    )

    output = builder._build_initial_plate_smelter(
        object(), object(), "nauvis", "player", "iron-plate", extraction,
        (5.5, -2.5), (0.0, 0.0), lambda _message: None,
        allow_unfunded_ghosts=True,
    )

    interface = refinery_interfaces(6, origin_x=20, origin_y=-10, variant="basic")
    assert output is None
    assert coverage == [interface.provider]
    assert powered == [interface.power_anchor]
    assert any(
        action["entity"] == "medium-electric-pole"
        and action["action_type"] == "place_ghost"
        for action in actions(submitted[0])
    )


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
    retirements = []
    monkeypatch.setattr(
        builder, "retire_entities_via_bots",
        lambda _c, _b, _s, _f, plan, label, _e, **_k:
            retirements.append((label, plan)),
    )

    removed = builder._retire_standing_bootstrap_cells(
        object(), object(), "nauvis", "player", "copper-plate",
        "copper-ore", (89.5, -39.5), lambda _message: None,
    )

    assert removed == 1
    assert [label for label, _plan in retirements] == [
        "legacy_logistic_copper-plate_starter",
        "legacy_copper-ore_logistic_intake",
    ]
    intake_actions = retirements[1][1]["phases"][0]["actions"]
    assert {(action["entity"], tuple(action["position"].values()))
            for action in intake_actions} == {
        ("fast-inserter", (89.5, -40.5)),
        ("passive-provider-chest", (89.5, -41.5)),
    }


def test_direct_starter_retires_only_after_the_full_refinery_is_healthy(
    monkeypatch,
) -> None:
    starter = builder.live_base.DirectPlateStarter(
        (61.5, 24.5), "north", 1,
    )
    monkeypatch.setattr(
        builder.live_base, "direct_plate_starter",
        lambda *_a, **_k: starter,
    )
    monkeypatch.setattr(
        builder.live_base, "bootstrap_cell_origins", lambda *_a, **_k: [],
    )
    retirements = []
    monkeypatch.setattr(
        builder, "retire_entities_via_bots",
        lambda _c, _b, _s, _f, plan, label, _e, **_k:
            retirements.append((label, plan)),
    )

    removed = builder._retire_standing_bootstrap_cells(
        object(), object(), "nauvis", "player", "copper-plate",
        "copper-ore", (113.5, -39.5), lambda _message: None,
    )

    assert removed == 1
    assert [label for label, _plan in retirements] == [
        "direct_copper-plate_starter",
    ]
    assert {action["entity"] for action in retirements[0][1]["phases"][0]["actions"]} == {
        "electric-mining-drill", "electric-furnace",
        "inserter", "passive-provider-chest",
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
