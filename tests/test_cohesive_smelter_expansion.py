# Path: tests/test_cohesive_smelter_expansion.py
# Purpose: Prove mining expansion grows one direct-belt plate refinery instead of planning another independent site.

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
from orchestrator import live_base  # noqa: E402
from orchestrator.parts_mall import MaterialShortage  # noqa: E402
from orchestrator.stage_services import StuckError  # noqa: E402


def _actions(plan: dict) -> list[dict]:
    return [
        action
        for phase in plan["phases"]
        for action in phase["actions"]
    ]


def test_plate_expansion_is_addition_only_and_keeps_one_origin() -> None:
    plan, _full, output = builder._plate_line_extension_plan(
        "iron-plate", 5, 12, (-10, -32),
        "transport-belt", "inserter", "east",
    )
    actions = _actions(plan)
    furnaces = [
        action for action in actions
        if action["entity"] == "electric-furnace"
    ]

    assert len(furnaces) == 7
    assert {action["position"]["y"] for action in furnaces} == {-28.5}
    assert min(action["position"]["x"] for action in furnaces) == 6.5
    assert output == (27.5, -23.5)
    assert not [action for action in actions if action["action_type"] == "remove_entity"]


def test_plate_expansion_preserves_direct_belts_and_old_side_tap() -> None:
    plan, full, _output = builder._plate_line_extension_plan(
        "copper-plate", 5, 12, (80, -19),
        "transport-belt", "inserter", "east",
    )
    actions = _actions(plan)

    assert not [action for action in actions if action["entity"] == "infinity-chest"]
    assert not [
        action for action in actions
        if action["position"] == {"x": 96.5, "y": -10.5}
    ], "the old provider remains live and connected"
    assert any(
        action["entity"] == "transport-belt"
        and action["position"] == {"x": 96.5, "y": -12.5}
        for action in _actions(full)
    ), "the complete output trunk still passes the old side tap"


def test_ambiguous_multi_site_smelters_fail_before_another_site_is_added() -> None:
    line = SimpleNamespace(
        recipe="iron-plate",
        machine_count=4,
        machine_positions=((1.5, 3.5), (4.5, 3.5), (1.5, 20.5), (4.5, 20.5)),
    )

    with pytest.raises(StuckError, match="separate row"):
        builder._existing_plate_smelter(object(), "nauvis", line)


def test_legacy_steel_output_still_identifies_the_managed_row(monkeypatch) -> None:
    line = SimpleNamespace(
        recipe="iron-plate", machine_count=2,
        machine_positions=((1.5, 3.5), (4.5, 3.5)),
    )
    entities = {
        (-1.5, 8.5): {"name": "steel-chest"},
        (0.5, 0.5): {"name": "transport-belt"},
        (1.5, 1.5): {"name": "fast-inserter"},
    }
    monkeypatch.setattr(
        builder.live_base, "entity_at",
        lambda _client, _surface, position: entities.get(tuple(position)),
    )

    recovered = builder._existing_plate_smelter(object(), "nauvis", line)

    assert recovered.flow_direction == "west"
    assert recovered.output == (-1.5, 8.5)

def test_plate_row_recovery_uses_stable_origin_when_one_furnace_loses_recipe(monkeypatch) -> None:
    line = SimpleNamespace(
        recipe="iron-plate", machine_count=6,
        machine_positions=((4.5, 3.5), (7.5, 3.5), (10.5, 3.5),
                           (13.5, 3.5), (16.5, 3.5), (19.5, 3.5)),
    )
    entities = {
        (-1.5, 8.5): {"name": "steel-chest"},
        (0.5, 0.5): {"name": "transport-belt"},
        (1.5, 1.5): {"name": "fast-inserter"},
    }
    monkeypatch.setattr(
        builder.live_base, "entity_at",
        lambda _client, _surface, position: entities.get(tuple(position)),
    )

    recovered = builder._existing_plate_smelter(
        object(), "nauvis", line, expected_origin=(0.0, 0.0),
    )

    assert recovered.origin == (0.0, 0.0)
    assert recovered.flow_direction == "west"
    assert recovered.output == (-1.5, 8.5)


def test_cohesive_target_merges_recipe_less_furnace_before_extension(monkeypatch) -> None:
    visible = SimpleNamespace(
        recipe="iron-plate", machine_count=6,
        working_count=6,
        machine_positions=((4.5, 3.5), (7.5, 3.5), (10.5, 3.5),
                           (13.5, 3.5), (16.5, 3.5), (19.5, 3.5)),
    )
    idle = SimpleNamespace(machine_positions=((1.5, 3.5),))
    entities = {
        (-1.5, 8.5): {"name": "steel-chest"},
        (0.5, 0.5): {"name": "transport-belt"},
        (1.5, 1.5): {"name": "fast-inserter"},
    }
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: visible)
    monkeypatch.setattr(builder.live_base, "find_idle_machine_row", lambda *_a: idle)
    monkeypatch.setattr(
        builder.live_base, "entity_at",
        lambda _client, _surface, position: entities.get(tuple(position)),
    )
    extraction = SimpleNamespace(
        smelter_origin=(0.0, 0.0), system_drill_count_before=6,
        drill_count=14, mining_productivity_bonus=0.0, ore="iron-ore",
    )

    existing, _target = builder._cohesive_smelter_target(
        object(), "nauvis", "player", "iron-plate", extraction, True,
        lambda _message: None,
    )

    assert existing.machine_count == 7
    assert existing.machine_positions[0] == (1.5, 3.5)

def test_cohesive_expansion_recovers_the_deployed_row_not_a_new_site(monkeypatch) -> None:
    """An expansion search may choose a different free site than the live row."""
    line = SimpleNamespace(
        recipe="iron-plate", machine_count=2,
        machine_positions=((1.5, 3.5), (4.5, 3.5)),
    )
    entities = {
        (-1.5, 8.5): {"name": "steel-chest"},
        (0.5, 0.5): {"name": "transport-belt"},
        (1.5, 1.5): {"name": "fast-inserter"},
    }
    idle_searches: list[tuple[float, float]] = []
    extraction = SimpleNamespace(
        smelter_origin=(85.0, 82.0), system_drill_count_before=6,
        drill_count=14, mining_productivity_bonus=0.0, ore="iron-ore",
    )
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_args: line)
    monkeypatch.setattr(
        builder.live_base, "find_idle_machine_row",
        lambda *_args: idle_searches.append(_args[-1]) or None,
    )
    monkeypatch.setattr(
        builder.live_base, "entity_at",
        lambda _client, _surface, position: entities.get(tuple(position)),
    )

    existing, target = builder._cohesive_smelter_target(
        object(), "nauvis", "player", "iron-plate", extraction, True,
        lambda _message: None,
    )

    assert existing is line
    assert target is not None
    assert idle_searches == [(1.5, 3.5)]


def test_mining_expansion_validates_cohesion_before_submitting_drills() -> None:
    target_source = inspect.getsource(builder._cohesive_smelter_target)
    build_source = inspect.getsource(builder.build_mining_stage)

    assert "_existing_plate_smelter(" in target_source
    assert build_source.index("_cohesive_smelter_target(") < build_source.index(
        "_submit_mining_plan("
    )
    assert build_source.index("_assert_atomic_plate_expansion_affordable(") < build_source.index(
        "_submit_mining_plan("
    )
    assert build_source.index("_extend_plate_smelter(") < build_source.index(
        "_build_initial_plate_smelter("
    )
    assert "system_drill_count_before + extraction.drill_count" in target_source


def test_plate_expansion_preflight_rejects_real_infrastructure(monkeypatch) -> None:
    plan = {"phases": [{"actions": [{
        "action_type": "place_ghost", "entity": "electric-furnace",
        "position": {"x": 1.5, "y": 1.5},
    }]}]}
    client = SimpleNamespace(command=lambda _command: "")
    monkeypatch.setattr(builder.live_base, "occupied_tiles", lambda *_a, **_k: {(0, 0)})
    monkeypatch.setattr(builder.live_base, "water_tiles", lambda *_a: set())

    with pytest.raises(StuckError, match="intersects real infrastructure"):
        builder._plate_expansion_foundation(client, "nauvis", "iron-plate", plan)


def test_plate_expansion_preflight_stages_landfill_for_water(monkeypatch) -> None:
    plan = {"phases": [{"actions": [{
        "action_type": "place_ghost", "entity": "electric-furnace",
        "position": {"x": 1.5, "y": 1.5},
    }]}]}
    client = SimpleNamespace(command=lambda _command: "")
    monkeypatch.setattr(builder.live_base, "occupied_tiles", lambda *_a, **_k: set())
    monkeypatch.setattr(builder.live_base, "water_tiles", lambda *_a: {(0, 0), (2, 2), (9, 9)})

    foundation = builder._plate_expansion_foundation(
        client, "nauvis", "iron-plate", plan,
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
        smelter_flow_direction="west", system_drill_count_before=7,
        system_drill_target=7, ore="iron-ore",
    )
    line = SimpleNamespace(machine_count=7)
    monkeypatch.setattr(builder, "retire_depleted_mines", lambda *_a: None)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "plan_local_extraction", lambda *_a, **_k: extraction)
    monkeypatch.setattr(builder, "_cohesive_smelter_target", lambda *_a: (line, 19))
    monkeypatch.setattr(builder, "_assert_atomic_plate_expansion_affordable", lambda *_a: foundation)
    monkeypatch.setattr(builder, "_place_plate_expansion_foundation", lambda *_a: calls.append("foundation"))
    monkeypatch.setattr(builder, "_submit_mining_plan", lambda *_a: calls.append("mine"))
    monkeypatch.setattr(builder, "_extend_plate_smelter", lambda *_a: (30.0, 40.0))

    output = builder.build_mining_stage(
        object(), object(), "nauvis", "player", "iron-plate", (0.0, 0.0),
        lambda _message: None, expand=True,
    )

    assert output == (30.0, 40.0)
    assert calls == ["foundation", "mine"]


def test_atomic_expansion_preflight_counts_mine_and_smelter_ghosts(monkeypatch) -> None:
    captured: dict = {}
    extraction = SimpleNamespace(build_plan={"phases": [{"actions": [
        {"action_type": "place_ghost", "entity": "electric-mining-drill"},
    ]}]})
    line = SimpleNamespace(recipe="iron-plate", machine_count=7)
    existing = SimpleNamespace(
        origin=(0.0, 0.0), belt_type="transport-belt",
        inserter_type="inserter", flow_direction="west",
    )
    monkeypatch.setattr(builder, "_existing_plate_smelter", lambda *_a: existing)
    monkeypatch.setattr(
        builder, "_plate_line_extension_plan",
        lambda *_a: ({"phases": [{"actions": [
            {"action_type": "place_ghost", "entity": "electric-furnace"},
        ]}]}, {}, (0.0, 0.0)),
    )
    monkeypatch.setattr(
        builder, "assert_affordable",
        lambda *_a: captured.update(plan=_a[3], name=_a[4]),
    )

    builder._assert_atomic_plate_expansion_affordable(
        object(), "nauvis", "player", "iron-plate", extraction, line, 19,
        lambda _message: None,
    )

    assert captured["name"] == "expand_iron-plate_system"
    assert [
        action["entity"] for phase in captured["plan"]["phases"]
        for action in phase["actions"]
    ] == ["electric-mining-drill", "electric-furnace"]


def test_mining_expansion_rejects_the_full_bill_before_submitting_mine(monkeypatch) -> None:
    extraction = SimpleNamespace(build_plan={"phases": []})
    line = SimpleNamespace(machine_count=7)
    monkeypatch.setattr(builder, "retire_depleted_mines", lambda *_a: None)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "plan_local_extraction", lambda *_a, **_k: extraction)
    monkeypatch.setattr(builder, "_cohesive_smelter_target", lambda *_a: (line, 19))
    monkeypatch.setattr(
        builder, "_assert_atomic_plate_expansion_affordable",
        lambda *_a: (_ for _ in ()).throw(
            MaterialShortage("expand_iron-plate_system", {"fast-transport-belt": 74}, {})
        ),
    )
    monkeypatch.setattr(
        builder, "_submit_mining_plan",
        lambda *_a: pytest.fail("mining must wait for its smelter bill"),
    )

    with pytest.raises(MaterialShortage, match="fast-transport-belt"):
        builder.build_mining_stage(
            object(), object(), "nauvis", "player", "iron-plate",
            (0.0, 0.0), lambda _message: None, expand=True,
        )


@pytest.mark.parametrize("recipe", ["iron-plate", "stone-brick"])
def test_mining_expansion_refuses_without_a_recoverable_refinery(monkeypatch, recipe) -> None:
    extraction = SimpleNamespace(build_plan={"phases": []})
    monkeypatch.setattr(builder, "retire_depleted_mines", lambda *_a: None)
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "plan_local_extraction", lambda *_a, **_k: extraction)
    monkeypatch.setattr(builder, "_cohesive_smelter_target", lambda *_a: (None, None))
    monkeypatch.setattr(
        builder, "_submit_mining_plan",
        lambda *_a: pytest.fail("a missing refinery must block mine expansion"),
    )

    with pytest.raises(StuckError, match="no recoverable managed refinery"):
        builder.build_mining_stage(
            object(), object(), "nauvis", "player", recipe,
            (0.0, 0.0), lambda _message: None, expand=True,
        )


def test_westbound_expansion_keeps_the_direct_ore_handoff_fixed() -> None:
    old, old_feed, _old_output = builder._plate_line_layout(
        "iron-plate", 5, (80, -19), "transport-belt", "inserter", "west",
    )
    plan, full, output = builder._plate_line_extension_plan(
        "iron-plate", 5, 12, (80, -19),
        "transport-belt", "inserter", "west",
    )
    old_furnaces = {
        (action["position"]["x"], action["position"]["y"])
        for action in _actions(old) if action["entity"] == "electric-furnace"
    }
    added_furnaces = [
        action for action in _actions(plan)
        if action["entity"] == "electric-furnace"
    ]
    input_belts = [
        action for action in _actions(full)
        if "transport-belt" in action["entity"]
        and action["position"]["y"] == old_feed[1]
    ]

    assert any(
        action["position"] == {"x": old_feed[0], "y": old_feed[1]}
        for action in input_belts
    ), "the original ore handoff remains the expanded row's endpoint"
    assert max(action["position"]["x"] for action in input_belts) == old_feed[0]
    assert not [
        action for action in _actions(plan)
        if action["position"]["y"] == old_feed[1]
        and action["position"]["x"] > old_feed[0]
    ], "a direct ore row must not overwrite or extend its mine-side belt endpoint"
    assert all(action["position"]["x"] < min(x for x, _y in old_furnaces)
               for action in added_furnaces)
    assert output == (57.5, -10.5)
    assert not [action for action in _actions(plan)
                if action["action_type"] == "remove_entity"]
    assert len([action for action in _actions(full)
                if action["entity"] == "electric-furnace"]) == 12


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
    assert line.recipe == "copper-plate"
    assert line.machine_positions == ((80.5, -15.5), (83.5, -15.5))
    assert "ghost_name='electric-furnace'" in client.commands[0]


def test_idle_plate_row_is_recovered_before_new_site(monkeypatch) -> None:
    idle = live_base.LineState(
        recipe="copper-plate", machine_count=2, working_count=0,
        output_position=(83.5, -15.5),
        machine_positions=((80.5, -15.5), (83.5, -15.5)),
    )
    extraction = SimpleNamespace(
        smelter_origin=(80.0, -19.0), system_drill_count_before=2,
        drill_count=0, mining_productivity_bonus=0.3, ore="copper-ore",
    )
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: None)
    monkeypatch.setattr(
        builder.live_base, "find_idle_machine_row", lambda *_a, **_k: idle,
    )
    monkeypatch.setattr(
        builder, "_existing_plate_smelter",
        lambda *_a: SimpleNamespace(),
    )

    existing, target = builder._cohesive_smelter_target(
        object(), "nauvis", "player", "copper-plate", extraction, True,
        lambda _message: None,
    )

    assert existing is idle
    assert target is not None


def test_existing_idle_plate_row_runs_power_recovery(monkeypatch) -> None:
    line = SimpleNamespace(
        recipe="copper-plate", machine_count=2,
        machine_positions=((80.5, -15.5), (83.5, -15.5)),
    )
    existing = SimpleNamespace(
        origin=(80.0, -19.0), output=(86.5, -10.5),
        belt_type="transport-belt", inserter_type="inserter",
        flow_direction="east",
    )
    calls = []
    monkeypatch.setattr(builder, "_existing_plate_smelter", lambda *_a: existing)
    monkeypatch.setattr(
        builder, "bring_stage_up", lambda *args, **_kwargs: calls.append(args),
    )

    output = builder._extend_plate_smelter(
        object(), object(), "nauvis", "player", "copper-plate", line, 2,
        (77.5, -18.5), lambda _message: None,
    )

    assert output == existing.output
    assert calls and calls[0][7] == (75.0, -14.5)
    assert calls[0][8] == list(line.machine_positions)

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
