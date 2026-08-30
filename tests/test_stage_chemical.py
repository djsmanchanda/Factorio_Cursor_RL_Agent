# Path: tests/test_stage_chemical.py
# Purpose: Verify chemical construction stages keep landfill and pipe ghosts ordered.

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from orchestrator import extraction_state, live_base, resource_patches, stage_chemical
from orchestrator import autonomous_builder
from orchestrator.stage_services import _ghost_materials

Point = tuple[float, float]


def test_oil_capacity_contract_uses_live_factorio_rates() -> None:
    assert stage_chemical.oil_processing_recipe(0) == "basic-oil-processing"
    assert stage_chemical.oil_processing_recipe(1) == "advanced-oil-processing"
    assert stage_chemical.pumpjack_crude_rate(2.19, 0.0) == pytest.approx(21.9)
    assert stage_chemical.petroleum_rate("basic-oil-processing") == 9.0
    assert stage_chemical.petroleum_rate(
        "advanced-oil-processing", crack_all_outputs=False,
    ) == 11.0
    assert stage_chemical.petroleum_rate("advanced-oil-processing") == 19.5


def test_battery_row_has_real_item_and_acid_feeds() -> None:
    plan = stage_chemical.generate_fluid_machine_row("battery", 1)
    stage_chemical._swap_infinity_chests(plan, {})
    actions = [
        action for phase in plan["phases"] for action in phase["actions"]
    ]
    assert any(
        action.get("entity") == "chemical-plant"
        and action.get("recipe") == "battery"
        for action in actions
    )
    assert sum(
        action.get("entity") == "requester-chest" for action in actions
    ) == 2
    assert stage_chemical.header_attachment(
        "battery", "sulfuric-acid", 1, 0, 0,
    )["attach"]


def test_real_builder_delegates_battery_to_the_chemical_stage(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        autonomous_builder, "_ensure_chemical_ladder_predecessor",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        autonomous_builder, "ensure_battery_cell",
        lambda *args: calls.append(args[4]) or (12.5, 13.5),
    )

    result = autonomous_builder.ensure_produced(
        object(), object(), "nauvis", "player", "battery", (1.0, 2.0),
        lambda _message: None,
    )

    assert result == (12.5, 13.5)
    assert calls == [(1.0, 2.0)]


def test_existing_battery_cell_is_serviced_before_reuse(monkeypatch) -> None:
    surveys = iter([
        SimpleNamespace(machine_positions=((10.5, 20.5),), working_count=0),
        SimpleNamespace(machine_positions=((10.5, 20.5),), working_count=1),
    ])
    serviced = []
    monkeypatch.setattr(
        stage_chemical.live_base, "find_line", lambda *_args: next(surveys),
    )
    monkeypatch.setattr(
        stage_chemical.live_base, "nearest_container",
        lambda *_args, **_kwargs: (14.5, 26.5),
    )

    result = stage_chemical.ensure_battery_cell(
        object(), object(), "nauvis", "player", (0.0, 0.0),
        lambda *args, **_kwargs: serviced.append(args[4]),
        lambda _message: None,
    )

    assert result == (14.5, 26.5)
    assert serviced == ["battery chemical cell"]


def test_pumpjack_faces_the_local_oil_cell() -> None:
    east = stage_chemical._pumpjack_site_nearest((-286.5, -98.5), (-220.0, -98.0))
    north = stage_chemical._pumpjack_site_nearest((-286.5, -98.5), (-286.0, -160.0))

    assert east["direction"] == "east"
    assert east["output"] == (-285, -100)
    assert north["direction"] == "north"
    assert north["output"] == (-288, -101)


def test_live_east_pumpjack_pipe_starts_outside_the_machine() -> None:
    site = stage_chemical._pumpjack_site_nearest(
        (-268.5, -98.5), (-237.0, -91.0),
    )

    assert site["direction"] == "east"
    assert site["output"] == (-267, -100)
    plan = stage_chemical.generate_pumpjack_source([site], [site["output"]])
    pipe = next(
        action for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == "pipe"
    )
    assert pipe["position"] == {"x": -266.5, "y": -99.5}


def test_requested_straight_shoreline_has_adjacent_land_output() -> None:
    site = {
        "position": (-97.5, 15.5), "output": (-98, 14),
        "resource": "water", "direction": "south",
    }

    plan = stage_chemical.generate_offshore_pump_source([site], [site["output"]])
    pipe = next(
        action for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == "pipe"
    )
    assert pipe["position"] == {"x": -97.5, "y": 14.5}


def test_separate_petroleum_packets_share_a_complete_network() -> None:
    """Plastic first, then sulfur, must retain the missing live corner pipe."""
    ox, oy = -258, -108
    px, py = -282, -40
    refinery = stage_chemical.generate_fluid_machine_row(
        "basic-oil-processing", 1, ox, oy,
    )
    plastic = stage_chemical.generate_fluid_machine_row("plastic-bar", 2, px, py)
    sulfur = stage_chemical.generate_fluid_machine_row("sulfur", 2, ox + 18, oy + 16)
    plans = (refinery, plastic, sulfur)

    source = stage_chemical.header_attachment(
        "basic-oil-processing", "petroleum-gas", 1, ox, oy,
    )["attach"]
    targets = (
        stage_chemical.header_attachment(
            "plastic-bar", "petroleum-gas", 2, px, py,
        )["attach"],
        stage_chemical.header_attachment(
            "sulfur", "petroleum-gas", 2, ox + 18, oy + 16,
        )["attach"],
    )
    foreign = (
        stage_chemical.fluid_network_segments("basic-oil-processing", 1, ox, oy)
        + stage_chemical.fluid_network_segments("plastic-bar", 2, px, py)
        + stage_chemical.fluid_network_segments("sulfur", 2, ox + 18, oy + 16)
    )
    hard = stage_chemical._planned_hard_tiles(*plans) - {source, *targets}
    pipe_positions: set[Point] = set()
    for target in targets:
        link, segments, _crossed_water = stage_chemical._route_oil_fluid_link(
            source, [target], "petroleum-gas", foreign=foreign, hard=hard,
            terrain_water=set(), existing_tiles=[],
        )
        foreign.extend(segments)
        packet_positions = {
            (action["position"]["x"], action["position"]["y"])
            for phase in link["phases"] for action in phase["actions"]
            if action.get("entity") == "pipe"
        }
        assert not pipe_positions & packet_positions
        pipe_positions |= packet_positions

    assert (-252.5, -107.5) in pipe_positions


def test_oil_cell_search_is_anchored_to_crude_not_the_base(monkeypatch) -> None:
    captured = {}

    def find_clear(_client, _surface, near, width, height, **kwargs):
        captured.update(near=near, width=width, height=height, kwargs=kwargs)
        return (-250.0, -120.0)

    monkeypatch.setattr(stage_chemical.live_base, "find_clear_area", find_clear)

    result = stage_chemical._find_oil_cell_site(
        object(), "nauvis", (-286.5, -98.5),
    )

    assert result == (-250.0, -120.0)
    assert captured["near"] == (-286.5, -98.5)
    assert captured["kwargs"]["max_radius"] == 80.0


def test_plastic_site_is_surveyed_at_coal_refinery_midpoint(monkeypatch) -> None:
    captured = {}

    def find_clear(_client, _surface, near, width, height, **kwargs):
        captured.update(near=near, width=width, height=height, kwargs=kwargs)
        return (-305.0, -45.0)

    monkeypatch.setattr(stage_chemical.live_base, "find_clear_area", find_clear)

    result = stage_chemical._find_plastic_site(
        object(), "nauvis", (-237.0, -91.0), (-340.0, 20.0),
    )

    assert result == (-305.0, -45.0)
    assert captured["near"] == (-288.5, -35.5)
    assert (captured["width"], captured["height"]) == (12, 12)


def test_existing_coal_mine_on_selected_patch_is_reused(monkeypatch) -> None:
    mine = extraction_state.ResourceMine(
        (-335.5, 11.5), 4, belt_y=11.5, first_column_x=-329.5,
        haul_head=(-323.5, 11.5), growth_direction=-1,
    )
    patch = resource_patches.ResourcePatch(
        (-329.5, 10.5), (-369.5, 1.5), (-325.5, 43.5), 16_281_288,
    )
    messages: list[str] = []
    monkeypatch.setattr(stage_chemical, "retire_depleted_mines", lambda *_a: None)
    monkeypatch.setattr(
        stage_chemical.extraction_state, "find_resource_mine",
        lambda *_a: mine,
    )
    monkeypatch.setattr(
        stage_chemical.resource_patches, "nearest_viable_patch",
        lambda *_a: patch,
    )
    monkeypatch.setattr(
        stage_chemical.live_base, "transport_belt_direction_at",
        lambda _client, _surface, position: (
            "east" if position == mine.haul_head else "west"
        ),
    )

    client = type("Client", (), {"command": lambda self, _text: "NONE"})()
    source = stage_chemical.ensure_coal_mine(
        client, object(), "nauvis", "player", (-192.5, -91.0),
        lambda *_a, **_k: None, messages.append,
        prefer_nearest_patch=True,
    )

    assert source == mine.haul_head
    assert any("reusing owned belt mine" in message for message in messages)


def test_live_belt_direction_observation_uses_factorio_cardinals() -> None:
    class Client:
        command_text = ""

        def command(self, text: str) -> str:
            self.command_text = text
            return "east"

    client = Client()

    assert live_base.transport_belt_direction_at(
        client, "nauvis", (-323.5, 11.5),
    ) == "east"
    assert "defines.direction.east" in client.command_text
    assert "type='transport-belt'" in client.command_text


def test_new_local_coal_mine_flows_toward_plastic(monkeypatch) -> None:
    patch = resource_patches.ResourcePatch(
        (-329.5, 10.5), (-369.5, 1.5), (-325.5, 43.5), 16_281_288,
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(stage_chemical, "retire_depleted_mines", lambda *_a: None)
    monkeypatch.setattr(
        stage_chemical.extraction_state, "find_resource_mine",
        lambda *_a: None,
    )
    monkeypatch.setattr(
        stage_chemical.resource_patches, "nearest_viable_patch",
        lambda *_a: patch,
    )
    monkeypatch.setattr(
        stage_chemical, "_coal_compatible_mining_origins",
        lambda *_a: [(-350.0, 12.0)],
    )
    monkeypatch.setattr(
        stage_chemical, "choose_mining_origin",
        lambda *_a, **_k: ((-350.0, 12.0), 2),
    )

    def direct_plan(origin, count, **kwargs):
        captured.update(origin=origin, count=count, kwargs=kwargs)
        return {"phases": []}, (-342.5, 12.5)

    monkeypatch.setattr(stage_chemical, "direct_mine_plan", direct_plan)
    monkeypatch.setattr(stage_chemical, "strip_local_power", lambda plan, **_k: plan)
    monkeypatch.setattr(stage_chemical, "_publish_output_chest", lambda _plan: None)
    monkeypatch.setattr(stage_chemical, "_submit", lambda *_a: None)
    monkeypatch.setattr(
        stage_chemical, "existing_mine_service_geometry",
        lambda *_a, **_k: ((0.0, 0.0), ((-1.0, -1.0), (1.0, 1.0)),
                            (0.0, 0.0), [(-348.5, 10.5)]),
    )

    result = stage_chemical.ensure_coal_mine(
        object(), object(), "nauvis", "player", (-192.5, -91.0),
        lambda *_a, **_k: None, lambda _message: None,
        prefer_nearest_patch=True,
    )

    assert result is None
    assert captured["origin"] == (-350.0, 12.0)
    assert captured["count"] == 2
    assert captured["kwargs"]["output_side"] == "east"
    assert captured["kwargs"]["continuation_tiles"] == 0


def test_oil_cell_uses_local_belt_coal_and_no_requester(monkeypatch) -> None:
    calls: dict[str, object] = {}
    submitted: dict[str, object] = {}
    monkeypatch.setattr(stage_chemical, "_existing_outputs", lambda *_a: None)
    monkeypatch.setattr(
        stage_chemical.live_base, "nearest_resource",
        lambda *_a: ((-268.5, -98.5), 1_000_000),
    )
    monkeypatch.setattr(
        stage_chemical, "_find_oil_cell_site", lambda *_a: (-258.0, -108.0),
    )

    def local_coal(*_args, **kwargs):
        calls["coal_reference"] = _args[4]
        calls["prefer_nearest_patch"] = kwargs["prefer_nearest_patch"]
        return (-340.0, 20.0)

    monkeypatch.setattr(stage_chemical, "ensure_coal_mine", local_coal)

    def plastic_site(_client, _surface, refinery_centre, coal_output):
        calls["plastic_inputs"] = (refinery_centre, coal_output)
        return (-305.0, -45.0)

    monkeypatch.setattr(stage_chemical, "_find_plastic_site", plastic_site)
    monkeypatch.setattr(
        stage_chemical.chemical_survey, "nearest_offshore_pump_site",
        lambda *_a: {
            "position": (-97.5, 15.5), "output": (-98, 14),
            "resource": "water", "direction": "south",
        },
    )
    monkeypatch.setattr(stage_chemical.live_base, "occupied_tiles", lambda *_a, **_k: set())
    monkeypatch.setattr(stage_chemical.live_base, "water_tiles", lambda *_a, **_k: set())
    monkeypatch.setattr(
        stage_chemical, "_route_oil_fluid_link",
        lambda *_a, **_k: ({"phases": [{"name": "fluid", "actions": []}]}, [], False),
    )

    def preflight(*args, **kwargs):
        calls["preflight"] = (args, kwargs)
        return ([{
            "action_type": "place_ghost", "entity": "transport-belt",
            "position": {"x": -320.5, "y": -20.5}, "direction": "east",
        }], "transport-belt")

    monkeypatch.setattr(stage_chemical, "preflight_ingredient_transport", preflight)

    def submit(
        _client, _bridge, _surface, _force, packets, _emit, *, after_packet,
    ):
        submitted["packets"] = packets
        submitted["after_packet"] = after_packet

    monkeypatch.setattr(stage_chemical, "_submit_oil_cell_packets", submit)
    monkeypatch.setattr(stage_chemical, "_connect_oil_cell_power", lambda *_a: None)
    monkeypatch.setattr(stage_chemical, "_diagnose_machines", lambda *_a, **_k: [])

    result = stage_chemical.ensure_oil_cell(
        object(), object(), "nauvis", "player", (3.0, -1.0),
        lambda *_a, **_k: None, lambda _m: None,
        target_output="plastic-bar",
    )

    assert set(result) == {"plastic-bar"}
    assert calls["coal_reference"] == (-237.0, -91.0)
    assert calls["prefer_nearest_patch"] is True
    assert calls["plastic_inputs"] == ((-237.0, -91.0), (-340.0, 20.0))
    args, kwargs = calls["preflight"]
    assert args[5:7] == ((-340.0, 20.0), (-306.5, -44.5))
    assert kwargs["mode"] == "belt"
    assert kwargs["destination_is_belt"] is True
    packets = submitted["packets"]
    assert [name for name, _plan in packets] == [
        "chemical_coal_belt",
        "chemical_power_backbone",
        "chemical_refinery_and_plastic_machines",
        "chemical_crude_pipeline",
        "chemical_plastic_petroleum_pipeline",
    ]
    all_actions = [
        action for _name, plan in packets
        for phase in plan["phases"] for action in phase["actions"]
    ]
    assert not any(action.get("entity") == "requester-chest" for action in all_actions)
    assert any(
        action.get("entity") == "transport-belt"
        and action.get("position") == {"x": -320.5, "y": -20.5}
        for action in all_actions
    )


def test_sulfur_extends_the_healthy_plastic_district(monkeypatch) -> None:
    plastic = {"plastic-bar": (-304.5, -37.5)}
    monkeypatch.setattr(stage_chemical, "_existing_outputs", lambda *_a: plastic)
    calls = []
    monkeypatch.setattr(
        stage_chemical, "_extend_sulfur_stage",
        lambda *_args: calls.append(_args[-1]) or {
            **plastic, "sulfur": (-234.5, -80.5),
        },
    )

    result = stage_chemical.ensure_oil_cell(
        object(), object(), "nauvis", "player", (0.0, 0.0),
        lambda *_a, **_k: None, lambda _message: None,
        target_output="sulfur",
    )

    assert result["sulfur"] == (-234.5, -80.5)
    assert calls == [plastic]


def test_offshore_survey_requires_straight_shore_and_adjacent_output() -> None:
    class Client:
        command_text = ""

        def command(self, text: str) -> str:
            self.command_text = text
            return "NONE"

    client = Client()
    assert stage_chemical.chemical_survey.nearest_offshore_pump_site(
        client, "nauvis", (0.0, 0.0),
    ) is None

    assert "for side=-1,1" in client.command_text
    assert "{'west',1,0,1.5,0.5,2,0}" in client.command_text


def test_landfill_is_separated_from_dependent_pipe_ghosts() -> None:
    link = {"phases": [{"name": "fluid_link_water", "actions": [
        {"action_type": "place_tile_ghost", "tile": "landfill", "position": {"x": 8, "y": 4}},
        {"action_type": "place_ghost", "entity": "pipe-to-ground", "position": {"x": 8.5, "y": 4.5}},
    ]}]}

    foundation, fluid = stage_chemical._separate_landfill_ghosts(link)

    assert foundation == {"phases": [{"name": "oil_landfill_foundation", "actions": [
        {"action_type": "place_tile_ghost", "tile": "landfill", "position": {"x": 8, "y": 4}},
    ]}]}
    assert fluid["phases"][0]["actions"] == [
        {"action_type": "place_ghost", "entity": "pipe-to-ground", "position": {"x": 8.5, "y": 4.5}},
    ]
    assert _ghost_materials(foundation) == {"landfill": 1}


def test_oil_route_tries_land_before_requesting_landfill(monkeypatch) -> None:
    tunnel_choices = []

    def link(*_args, **kwargs):
        tunnel_choices.append(kwargs["allow_terrain_tunnels"])
        return {"phases": [{"name": "fluid", "actions": []}]}

    monkeypatch.setattr(stage_chemical, "generate_shortest_fluid_chain_link", link)
    monkeypatch.setattr(
        stage_chemical, "shortest_fluid_chain_segments",
        lambda *_args, **kwargs: [{
            "fluid": "water", "tiles": [],
            "used_tunnels": kwargs["allow_terrain_tunnels"],
        }],
    )

    _link, segments, crossed_water = stage_chemical._route_oil_fluid_link(
        (0.5, 0.5), [(5.5, 0.5)], "water", foreign=[], hard=set(),
        terrain_water={(2, 0)}, existing_tiles=[],
    )

    assert tunnel_choices == [False]
    assert not crossed_water
    assert segments[0]["used_tunnels"] is False


def test_oil_route_uses_landfill_only_when_land_route_is_impossible(monkeypatch) -> None:
    choices = []

    def link(*_args, **kwargs):
        allow = kwargs["allow_terrain_tunnels"]
        choices.append(allow)
        if not allow:
            raise ValueError("no land detour")
        return {"phases": [{"name": "fluid", "actions": []}]}

    monkeypatch.setattr(stage_chemical, "generate_shortest_fluid_chain_link", link)
    monkeypatch.setattr(
        stage_chemical, "shortest_fluid_chain_segments",
        lambda *_args, **_kwargs: [],
    )

    _link, _segments, crossed_water = stage_chemical._route_oil_fluid_link(
        (0.5, 0.5), [(5.5, 0.5)], "water", foreign=[], hard=set(),
        terrain_water={(2, 0)}, existing_tiles=[],
    )

    assert choices == [False, True]
    assert crossed_water


def test_chemical_coverage_targets_uncovered_positions_not_box_corners(monkeypatch) -> None:
    chained: list[Point] = []
    reservations: list[set[tuple[int, int]]] = []
    ports: list[list[Point]] = [[]]
    monkeypatch.setattr(
        stage_chemical.live_base, "roboport_positions",
        lambda *_args: list(ports[0]),
    )

    def fake_extend(
        _client, _bridge, _surface, _force, target, _emit, *, reserved_tiles,
    ):
        chained.append(target)
        reservations.append(reserved_tiles)
        ports[0].append((target[0], target[1] - 10))
        return True

    monkeypatch.setattr(stage_chemical, "extend_roboport_coverage", fake_extend)
    plan = {"phases": [{"name": "route", "actions": [
        {"action_type": "place_tile_ghost", "tile": "landfill", "position": {"x": -5, "y": 3}},
        {"action_type": "place_ghost", "entity": "pipe", "position": {"x": 12.5, "y": 18.5}},
    ]}]}

    stage_chemical._ensure_plan_construction_coverage(
        type("Client", (), {"command": object()})(), object(), "nauvis", "player", plan, lambda _line: None,
    )

    # One chain covers both actions, so the second needs no port of its own --
    # and the two bounding-box corners (-5, 18.5) / (12.5, 3) are demanded by
    # nobody: they were how roboports got strung across empty map.
    assert chained == [(-5.0, 3)]
    assert (12, 18) in reservations[0]


def test_chemical_coverage_finishes_each_leg_of_a_noncollinear_pipe_route(
    monkeypatch,
) -> None:
    chained: list[Point] = []
    ports: list[Point] = [(0.0, 0.0)]
    monkeypatch.setattr(
        stage_chemical.live_base, "roboport_positions",
        lambda *_args: list(ports),
    )

    def fake_extend(
        _client, _bridge, _surface, _force, target, _emit, *, reserved_tiles,
    ):
        chained.append(target)
        ports.append(target)
        return True

    monkeypatch.setattr(stage_chemical, "extend_roboport_coverage", fake_extend)
    plan = {"phases": [{"name": "fluid", "actions": [
        {"action_type": "place_ghost", "entity": "pipe",
         "position": {"x": -200.5, "y": 0.5}},
        {"action_type": "place_ghost", "entity": "pipe",
         "position": {"x": 0.5, "y": -200.5}},
    ]}]}

    stage_chemical._ensure_plan_construction_coverage(
        type("Client", (), {"command": object()})(), object(),
        "nauvis", "player", plan, lambda _line: None,
    )

    assert chained == [(-200.5, 0.5), (0.5, -200.5)]


def test_split_oil_submission_reserves_later_pipe_footprints(monkeypatch) -> None:
    captured: list[set[tuple[int, int]]] = []
    monkeypatch.setattr(
        stage_chemical, "_ensure_plan_construction_coverage",
        lambda *_args, reserved_tiles, **_kwargs:
            captured.append(reserved_tiles),
    )
    monkeypatch.setattr(
        stage_chemical, "_submit",
        lambda *_args, stage_coverage, **_kwargs: stage_coverage() or {"ok": True},
    )
    monkeypatch.setattr(stage_chemical, "_wait_for_ghosts", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        stage_chemical.live_base, "available_items",
        lambda *_args: {"chemical-plant": 1, "landfill": 1, "pipe": 1},
    )
    machine = {"phases": [{"name": "machine", "actions": [{
        "action_type": "place_ghost", "entity": "chemical-plant",
        "position": {"x": 1.5, "y": 1.5},
    }]}]}
    link = {"phases": [{"name": "fluid", "actions": [
        {"action_type": "place_tile_ghost", "tile": "landfill",
         "position": {"x": 8, "y": 4}},
        {"action_type": "place_ghost", "entity": "pipe",
         "position": {"x": 8.5, "y": 4.5}},
    ]}]}

    stage_chemical._submit_oil_cell_packets(
        object(), object(), "nauvis", "player",
        [("chemical_machines", machine), ("chemical_pipeline", link)],
        lambda _message: None,
    )

    assert len(captured) == 3
    assert all((8, 4) in reserved for reserved in captured)


def test_oil_packets_refuse_partial_submission_when_later_supply_is_missing(
    monkeypatch,
) -> None:
    submitted: list[str] = []
    packets = [
        ("chemical_power_backbone", {"phases": [{"name": "power", "actions": [{
            "action_type": "place_ghost", "entity": "substation",
            "position": {"x": 0.0, "y": 0.0},
        }]}]}),
        ("chemical_water_pipeline", {"phases": [{"name": "water", "actions": [{
            "action_type": "place_ghost", "entity": "pipe",
            "position": {"x": 1.5, "y": 0.5},
        }]}]}),
    ]
    monkeypatch.setattr(
        stage_chemical.live_base, "available_items",
        lambda *_args: {"substation": 1, "pipe": 0},
    )
    monkeypatch.setattr(
        stage_chemical, "construction_supply_chain_is_scheduled",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        stage_chemical, "_submit",
        lambda *_args, **_kwargs: submitted.append(_args[4]),
    )

    with pytest.raises(stage_chemical.MaterialShortage):
        stage_chemical._submit_oil_cell_packets(
            object(), object(), "nauvis", "player", packets,
            lambda _message: None,
        )

    assert submitted == []


def test_oil_power_connects_before_later_packets(monkeypatch) -> None:
    events: list[str] = []
    packets = [
        ("chemical_power_backbone", {"phases": [{"name": "power", "actions": [{
            "action_type": "place_ghost", "entity": "substation",
            "position": {"x": 0.0, "y": 0.0},
        }]}]}),
        ("chemical_machines", {"phases": [{"name": "machines", "actions": [{
            "action_type": "place_ghost", "entity": "chemical-plant",
            "position": {"x": 4.5, "y": 0.5},
        }]}]}),
    ]
    monkeypatch.setattr(
        stage_chemical.live_base, "available_items",
        lambda *_args: {"substation": 1, "chemical-plant": 1},
    )
    monkeypatch.setattr(
        stage_chemical, "_submit",
        lambda *_args, **_kwargs: events.append(_args[4]) or {"ok": True},
    )

    stage_chemical._submit_oil_cell_packets(
        object(), object(), "nauvis", "player", packets,
        lambda _message: None,
        after_packet=lambda name: events.append(f"connected:{name}"),
    )

    assert events == [
        "chemical_power_backbone",
        "connected:chemical_power_backbone",
        "chemical_machines",
        "connected:chemical_machines",
    ]


def test_oil_power_scaffolds_connect_before_stage_waits(monkeypatch) -> None:
    connected = []
    monkeypatch.setattr(
        stage_chemical, "extend_power",
        lambda _c, _b, _s, _f, position, _emit:
            connected.append(position) or True,
    )
    plan = {"phases": [{"name": "power", "actions": [
        {"action_type": "place_ghost", "entity": "substation",
         "position": {"x": -10.0, "y": 4.0}},
    ]}]}

    stage_chemical._connect_oil_cell_power(
        object(), object(), "nauvis", "player", [plan, plan],
        lambda _message: None,
    )

    assert connected == [(-10.0, 4.0)]


def test_occupied_tiles_can_leave_water_for_fluid_routing() -> None:
    class Client:
        command_text = ""

        def command(self, text: str) -> str:
            self.command_text = text
            return ""

    client = Client()
    live_base.occupied_tiles(client, "nauvis", (0, 0), (2, 2), include_water=False)

    assert "find_tiles_filtered" not in client.command_text
    assert "find_entities_filtered" in client.command_text


def test_partial_oil_cell_defers_instead_of_killing_the_run(monkeypatch) -> None:
    """Live run 31 (2026-08-22): the guard that refuses a SECOND oil cell
    while one is half-built raised StuckError and ended the whole mission --
    guaranteeing the partial cell could never converge. Refusing to duplicate
    is right; killing the mission is not. It must defer instead."""
    from orchestrator.autonomous_builder import ProductionPrerequisiteDeferred

    class _Line:
        machine_positions = [(1.0, 1.0), (4.0, 1.0)]
        working_count = 0

    monkeypatch.setattr(
        stage_chemical.live_base, "find_line",
        lambda *_a, **_k: _Line(),
    )
    monkeypatch.setattr(
        stage_chemical.live_base, "nearest_container",
        lambda *_a, **_k: None,
    )

    with pytest.raises(ProductionPrerequisiteDeferred):
        stage_chemical._existing_outputs(object(), "nauvis", "player")


def test_incomplete_mall_cell_rebuilds_in_place_not_on_a_fresh_slot() -> None:
    """Live run 32 (2026-08-22): an advanced-circuit cell existed as assembler
    only; strict line repair refused the geometry and killed the run. The
    rebuild must regenerate the cell's OWN plan at its own (origin, side)."""
    from orchestrator.mall_builder import (
        _slot_position,
        locate_mall_cell,
        rebuild_incomplete_mall_cell,
    )

    origin, side = (67, 53), "right"
    machine = _slot_position(origin, side)
    located = locate_mall_cell(machine, (35.0, 21.0))
    assert located == (origin, side)
    # A machine outside every declared half is not a mall cell.
    assert locate_mall_cell((machine[0] + 0.3, machine[1]), (35.0, 21.0)) is None

    submitted: list[tuple[str, dict]] = []
    monkey = pytest.MonkeyPatch()
    monkey.setattr(
        "orchestrator.mall_builder._submit",
        lambda _c, _b, _s, plan, name, _e, **_k:
            submitted.append((name, plan)) or {"ok": True},
    )
    try:
        assert rebuild_incomplete_mall_cell(
            object(), object(), "nauvis", "player",
            "advanced-circuit", machine, (35.0, 21.0), lambda _m: None,
        )
    finally:
        monkey.undo()
    name, plan = submitted[0]
    assert name == "repaired_mall_advanced-circuit"
    actions = [a for phase in plan["phases"] for a in phase["actions"]]
    placed_machines = [
        a for a in actions
        if a["entity"] == "assembling-machine-2"
        and (a["position"]["x"], a["position"]["y"]) == machine
    ]
    assert placed_machines, "the rebuilt plan must target the existing machine tile"


def test_present_empty_feed_chest_waits_instead_of_structural_rebuild(monkeypatch) -> None:
    """An existing empty chest normally means upstream starvation. Rebuilding
    the complete cell cannot create supply and only produces zero-action churn;
    a missing chest remains the structural rebuild case."""
    from orchestrator import live_base
    from orchestrator.mall_builder import (
        _slot_position,
        locate_mall_cell,
        mall_cell_needs_rebuild,
    )

    origin, side = (67, 53), "left"
    machine = _slot_position(origin, side)
    assert locate_mall_cell(machine, (35.0, 21.0)) == (origin, side)

    replies = iter([-1])  # missing declared feed chest
    monkeypatch.setattr(
        live_base, "chest_stored_items",
        lambda *_a, **_k: next(replies, 7),
    )
    kwargs = dict(
        surface="nauvis", recipe="engine-unit",
        machine_position=machine, reference_point=(35.0, 21.0),
    )
    assert mall_cell_needs_rebuild(object(), **kwargs)

    # A present but empty chest is a supply wait, not structural damage.
    monkeypatch.setattr(
        live_base, "chest_stored_items", lambda *_a, **_k: 0,
    )
    assert not mall_cell_needs_rebuild(object(), **kwargs)

    # Fully-labelled chests mean the cell is healthy: keep waiting normally.
    monkeypatch.setattr(
        live_base, "chest_stored_items", lambda *_a, **_k: 7,
    )
    assert not mall_cell_needs_rebuild(object(), **kwargs)

    # Machines outside any declared half are somebody else's problem.
    assert not mall_cell_needs_rebuild(
        object(), surface="nauvis", recipe="engine-unit",
        machine_position=(machine[0] + 0.3, machine[1]),
        reference_point=(35.0, 21.0),
    )


def test_chest_content_query_reads_held_items() -> None:
    from orchestrator import live_base

    class _Client:
        def __init__(self):
            self.commands = []

        def command(self, text):
            self.commands.append(text)
            return "3"

    client = _Client()
    assert live_base.chest_stored_items(client, "nauvis", (1.5, 2.5)) == 3
    lua = client.commands[0]
    assert "get_contents" in lua
    assert "requester-chest" in lua



def test_chest_content_query_reads_held_items() -> None:
    from orchestrator import live_base

    class _Client:
        def __init__(self):
            self.commands = []

        def command(self, text):
            self.commands.append(text)
            return "3"

    client = _Client()
    assert live_base.chest_stored_items(client, "nauvis", (1.5, 2.5)) == 3
    lua = client.commands[0]
    assert "get_contents" in lua
    assert "requester-chest" in lua
