# Path: tests/test_stage_chemical.py
# Purpose: Verify chemical construction stages keep landfill and pipe ghosts ordered.

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
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


def test_pumpjack_blueprint_rotation_and_mirror_map_to_real_connectors() -> None:
    """User-supplied blueprints: default, 90° clockwise, then mirrored.

    The exported defaults are north, east, and south respectively; connector
    tiles must rotate with the 3x3 body, not remain at the default location.
    """
    position = (-286.5, -98.5)
    assert stage_chemical.verified_pumpjack_output_tile({
        "position": position, "direction": "north",
    }) == (-288, -101)
    assert stage_chemical.verified_pumpjack_output_tile({
        "position": position, "direction": "east",
    }) == (-285, -100)
    assert stage_chemical.verified_pumpjack_output_tile({
        "position": position, "direction": "south",
    }) == (-286, -97)


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

    coal_plan = {"phases": [{"name": "coal", "actions": [{
        "action_type": "place_entity", "entity": "substation",
        "position": {"x": -329.0, "y": 7.0},
    }]}]}

    def direct_plan(origin, count, **kwargs):
        captured.update(origin=origin, count=count, kwargs=kwargs)
        return coal_plan, (-342.5, 12.5)

    monkeypatch.setattr(stage_chemical, "direct_mine_plan", direct_plan)
    monkeypatch.setattr(stage_chemical, "strip_local_power", lambda plan, **_k: plan)
    monkeypatch.setattr(stage_chemical, "_publish_output_chest", lambda _plan: None)
    events: list[str] = []

    def cover(_client, _bridge, _surface, _force, plan, _emit):
        assert plan is coal_plan
        events.append("coverage")

    def submit(_client, _bridge, _surface, plan, name, _emit, *, stage_coverage):
        assert plan is coal_plan
        assert name == "mining_coal"
        events.append("affordable")
        stage_coverage()
        events.append("submitted")

    monkeypatch.setattr(stage_chemical, "_ensure_plan_construction_coverage", cover)
    monkeypatch.setattr(stage_chemical, "_submit", submit)
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
    assert events == ["affordable", "coverage", "submitted"]


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
    monkeypatch.setattr(stage_chemical, "_crude_patch_tiles", lambda *_a: [])
    monkeypatch.setattr(
        stage_chemical, "ensure_logistic_coverage", lambda *_a, **_k: False,
    )

    result = stage_chemical.ensure_oil_cell(
        object(), object(), "nauvis", "player", (3.0, -1.0),
        lambda *_a, **_k: None, lambda _m: None,
        target_output="plastic-bar",
    )

    assert set(result) == {"plastic-bar"}
    assert calls["coal_reference"] == (-226.0, -86.0)
    assert calls["prefer_nearest_patch"] is True
    assert calls["plastic_inputs"] == ((-226.0, -86.0), (-340.0, 20.0))
    args, kwargs = calls["preflight"]
    assert args[5] == (-340.0, 20.0)
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
    machine_plan = dict(packets)["chemical_refinery_and_plastic_machines"]
    assert machine_plan.get("reserved_tiles"), (
        "opening refinery must reserve its compact mirrored growth footprint"
    )
    assert any(
        action.get("entity", "").endswith("transport-belt")
        and (action["position"]["x"], action["position"]["y"]) == args[6]
        and action.get("direction") == kwargs["destination_belt_direction"]
        for phase in machine_plan["phases"] for action in phase["actions"]
    )
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
    """Dives are tried before landfill: surface, then pipe-to-ground spans,
    then water crossings (2026-09-05: dives hop blockers like the killer
    pole without paying for landfill)."""
    choices = []

    def link(*_args, **kwargs):
        choices.append(
            (kwargs["allow_dives"], kwargs["allow_terrain_tunnels"]),
        )
        if not kwargs["allow_terrain_tunnels"]:
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

    assert choices == [(False, False), (True, False), (True, True)]
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
        lambda _c, _b, _s, _f, position, _emit, **_kwargs:
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


def test_selected_entity_tile_survey_includes_live_and_ghost_pipes() -> None:
    class _Client:
        command_text = ""

        def command(self, text):
            self.command_text = text
            return "-3,-2;4,5"

    client = _Client()
    assert live_base.entity_tile_indices(
        client, "nauvis", ("pipe", "pipe-to-ground"), (-10, -10), (10, 10),
    ) == {(-3, -2), (4, 5)}
    assert "ghost_name" in client.command_text
    assert "pipe-to-ground" in client.command_text
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


def test_extra_pumpjack_spots_fill_the_patch_without_overlap() -> None:
    """2026-09-04: one 9/s well left the 20/s refinery (and both plastic
    plants) idle. Extra 3x3 spots cover crude tiles at footprint pitch."""
    tiles = [
        (-291.0, -110.0), (-292.0, -81.0), (-269.0, -99.0),
        (-285.0, -89.0), (-280.0, -85.0), (-279.0, -91.0),
        (-277.0, -95.0), (-276.0, -92.0), (-277.0, -85.0),
    ]
    spots = stage_chemical._extra_pumpjack_spots(
        tiles, (-269.0, -99.0), (-237.0, -91.0),
    )

    assert 1 <= len(spots) <= 2
    positions = [(-269.0, -99.0)] + [tuple(s["position"]) for s in spots]
    for index, first in enumerate(positions):
        for second in positions[index + 1:]:
            assert (
                abs(first[0] - second[0]) >= 3.0
                or abs(first[1] - second[1]) >= 3.0
            ), "footprints must not overlap"
    assert all(s.get("output") for s in spots)


def test_extra_pumpjack_spots_respect_the_draw_ceiling() -> None:
    """A dense field still caps at refinery draw, not at tile count."""
    tiles = [(float(x), float(y)) for x in range(-300, -240) for y in range(-110, -80, 3)]
    spots = stage_chemical._extra_pumpjack_spots(
        tiles, (-269.0, -99.0), (-237.0, -91.0),
    )

    assert len(spots) == 2
    assert stage_chemical._extra_pumpjack_spots(
        [], (-269.0, -99.0), (-237.0, -91.0),
    ) == []


def test_patch_pumpjack_selection_skips_blocked_footprints() -> None:
    """A closer well is not legal when its 3x3 body hits infrastructure."""
    blocked = stage_chemical.footprint_tile_indices((8.5, 0.5), 3)
    sites = stage_chemical._pumpjack_sites_for_patch(
        [(4.5, 0.5), (8.5, 0.5)], (0.5, 0.5), (12.0, 0.0),
        blocked_tiles=blocked, draw_per_second=8.0, max_jacks=1,
    )

    assert [site["position"] for site in sites] == [(4.5, 0.5)]
    assert stage_chemical.footprint_tile_indices(
        sites[0]["position"], 3,
    ).isdisjoint(blocked)


def test_existing_oil_output_rechecks_logistic_coverage(monkeypatch) -> None:
    """2026-09-04: the plastic provider sat 28 tiles from its port while
    construction coverage reported fine. Every visit re-verifies."""
    monkeypatch.setattr(
        stage_chemical, "_existing_outputs",
        lambda *_a: {"plastic-bar": (1.0, 2.0)},
    )
    covered: list[list] = []
    monkeypatch.setattr(
        stage_chemical, "ensure_logistic_coverage",
        lambda _c, _b, _s, _f, positions, _e: covered.append(list(positions)),
    )

    result = stage_chemical.ensure_oil_cell(
        object(), object(), "nauvis", "player", (0.0, 0.0), None,
        lambda _message: None, target_output="plastic-bar",
    )

    assert result == {"plastic-bar": (1.0, 2.0)}
    assert covered == [[(1.0, 2.0)]]


def test_oil_cell_builds_extra_pumpjacks_to_saturate_the_refinery(monkeypatch) -> None:
    """2026-09-04: one 9/s well left the 20/s refinery idle and plastic
    crawled. Extra patch spots join the build with their own crude links."""
    monkeypatch.setattr(stage_chemical, "_existing_outputs", lambda *_a: None)
    monkeypatch.setattr(
        stage_chemical.live_base, "nearest_resource",
        lambda *_a: ((-268.5, -98.5), 1_000_000),
    )
    monkeypatch.setattr(
        stage_chemical, "_find_oil_cell_site", lambda *_a: (-258.0, -108.0),
    )
    monkeypatch.setattr(
        stage_chemical, "ensure_coal_mine", lambda *_a, **_k: (-340.0, 20.0),
    )
    monkeypatch.setattr(
        stage_chemical, "_find_plastic_site", lambda *_a: (-305.0, -45.0),
    )
    monkeypatch.setattr(
        stage_chemical.chemical_survey, "nearest_offshore_pump_site",
        lambda *_a: {
            "position": (-97.5, 15.5), "output": (-98, 14),
            "resource": "water", "direction": "south",
        },
    )
    monkeypatch.setattr(
        stage_chemical, "_crude_patch_tiles",
        lambda *_a: [(-260.0, -95.0), (-250.0, -85.0)],
    )
    monkeypatch.setattr(stage_chemical.live_base, "occupied_tiles", lambda *_a, **_k: set())
    monkeypatch.setattr(stage_chemical.live_base, "water_tiles", lambda *_a, **_k: set())
    monkeypatch.setattr(
        stage_chemical, "_route_oil_fluid_link",
        lambda *_a, **_k: ({"phases": [{"name": "fluid", "actions": []}]}, [], False),
    )
    monkeypatch.setattr(
        stage_chemical, "preflight_ingredient_transport",
        lambda *_a, **_k: ([], "transport-belt"),
    )
    submitted: dict[str, object] = {}
    monkeypatch.setattr(
        stage_chemical, "_submit_oil_cell_packets",
        lambda _c, _b, _s, _f, packets, _e, **_k: submitted.setdefault("packets", packets),
    )
    monkeypatch.setattr(stage_chemical, "_connect_oil_cell_power", lambda *_a: None)
    monkeypatch.setattr(stage_chemical, "_diagnose_machines", lambda *_a, **_k: [])
    monkeypatch.setattr(
        stage_chemical, "ensure_logistic_coverage", lambda *_a, **_k: False,
    )

    stage_chemical.ensure_oil_cell(
        object(), object(), "nauvis", "player", (3.0, -1.0),
        lambda *_a, **_k: None, lambda _m: None,
        target_output="plastic-bar",
    )

    packets = submitted["packets"]
    names = [name for name, _plan in packets]
    assert "chemical_crude_pipeline_2" in names
    jacks = [
        action for _name, plan in packets
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == "pumpjack"
    ]
    assert len(jacks) == 3


def _oil_cell_world(monkeypatch, routed) -> None:
    """Drive ensure_oil_cell with routing captured instead of executed."""
    monkeypatch.setattr(stage_chemical, "_existing_outputs", lambda *_a: None)
    monkeypatch.setattr(
        stage_chemical.live_base, "nearest_resource",
        lambda *_a: ((-268.5, -98.5), 1_000_000),
    )
    monkeypatch.setattr(
        stage_chemical, "_find_oil_cell_site", lambda *_a: (-258.0, -108.0),
    )
    monkeypatch.setattr(
        stage_chemical, "ensure_coal_mine", lambda *_a, **_k: (-340.0, 20.0),
    )
    monkeypatch.setattr(
        stage_chemical, "_find_plastic_site", lambda *_a: (-305.0, -45.0),
    )
    monkeypatch.setattr(
        stage_chemical.chemical_survey, "nearest_offshore_pump_site",
        lambda *_a: {
            "position": (-97.5, 15.5), "output": (-98, 14),
            "resource": "water", "direction": "south",
        },
    )
    monkeypatch.setattr(
        stage_chemical, "_crude_patch_tiles",
        lambda *_a: [(-260.0, -95.0), (-250.0, -85.0)],
    )
    monkeypatch.setattr(stage_chemical.live_base, "occupied_tiles", lambda *_a, **_k: set())
    monkeypatch.setattr(stage_chemical.live_base, "water_tiles", lambda *_a, **_k: set())
    def _capture(source, targets, fluid, **kwargs):
        routed.append({
            "source": source, "targets": list(targets), "fluid": fluid,
            "foreign": list(kwargs.get("foreign", ())),
        })
        return {"phases": [{"name": "fluid", "actions": []}]}, [], False
    monkeypatch.setattr(stage_chemical, "_route_oil_fluid_link", _capture)
    monkeypatch.setattr(
        stage_chemical, "preflight_ingredient_transport",
        lambda *_a, **_k: ([], "transport-belt"),
    )
    monkeypatch.setattr(
        stage_chemical, "_submit_oil_cell_packets", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(stage_chemical, "_connect_oil_cell_power", lambda *_a: None)
    monkeypatch.setattr(stage_chemical, "_diagnose_machines", lambda *_a, **_k: [])
    monkeypatch.setattr(
        stage_chemical, "ensure_logistic_coverage", lambda *_a, **_k: False,
    )


def test_oil_cell_reserves_all_four_refinery_headers(monkeypatch) -> None:
    """2026-09-05: keepout described a 1-machine refinery while the row
    builds 4, so gas routed through the invisible eastern crude header and
    merged. Every built header tile must be reserved before links route."""
    routed: list[dict] = []
    _oil_cell_world(monkeypatch, routed)

    stage_chemical.ensure_oil_cell(
        object(), object(), "nauvis", "player", (3.0, -1.0),
        lambda *_a, **_k: None, lambda _m: None,
        target_output="plastic-bar",
    )

    gas = next(call for call in routed if call["fluid"] == "petroleum-gas")
    refinery_crude = next(
        segment for segment in gas["foreign"]
        if segment.get("fluid") == "crude-oil"
        and len(segment.get("tunnel_endpoints", ()))
        == stage_chemical.OPENING_REFINERY_COUNT
    )
    expected = {tuple(tile) for tile in refinery_crude["tiles"]}
    reserved = {
        tuple(tile) for segment in gas["foreign"]
        if segment.get("fluid") == "crude-oil"
        for tile in segment.get("tiles", ())
    }
    assert expected and expected <= reserved


def test_extra_pumpjacks_tap_the_trunk_not_the_refinery(monkeypatch) -> None:
    """2026-09-05: every jack ran its own full-length pipeline; two jacks
    sat on lone stubs when their packets died. Taps target trunk tiles."""
    routed: list[dict] = []
    _oil_cell_world(monkeypatch, routed)

    stage_chemical.ensure_oil_cell(
        object(), object(), "nauvis", "player", (3.0, -1.0),
        lambda *_a, **_k: None, lambda _m: None,
        target_output="plastic-bar",
    )

    crude_calls = [call for call in routed if call["fluid"] == "crude-oil"]
    assert len(crude_calls) >= 2
    crude_to = crude_calls[0]["targets"][0]
    refinery_crude = next(
        segment for segment in crude_calls[0]["foreign"]
        if segment.get("fluid") == "crude-oil"
        and len(segment.get("tunnel_endpoints", ()))
        == stage_chemical.OPENING_REFINERY_COUNT
    )
    header = {tuple(tile) for tile in refinery_crude["tiles"]}
    for tap in crude_calls[1:]:
        assert tap["targets"] != [crude_to]
        assert tap["targets"][0] in header


def test_nearest_crude_tile_prefers_the_closest_trunk_tile() -> None:
    foreign = [
        {"fluid": "crude-oil", "tiles": [(0, 0), (10, 0), (20, 0)]},
        {"fluid": "water", "tiles": [(5, 5)]},
    ]
    assert stage_chemical._nearest_crude_tile(foreign, (11, 2)) == (10, 0)
    with pytest.raises(stage_chemical.StuckError):
        stage_chemical._nearest_crude_tile([], (0, 0))


def test_multi_pumpjack_power_keeps_a_substation_when_default_collides() -> None:
    """2026-09-05: the shared scaffold's substation was dropped onto a
    later jack and two pumpjacks stayed dark. The anchor moves instead."""
    from planners.infrastructure_geometry import footprint_tile_indices
    from planners.resource_layouts import (
        generate_pumpjack_source, verified_pumpjack_output_tile,
    )

    def _site(x: float, y: float) -> dict:
        site = {
            "position": (x, y), "resource": "crude-oil", "direction": "east",
        }
        site["output"] = verified_pumpjack_output_tile(site)
        return site

    first = _site(-268.5, -98.5)
    from planners.resource_layouts import even_size_center
    blocker_at = even_size_center(first["position"][0] - 7, first["position"][1] + 3)
    # A second jack straddling the default substation footprint forces the
    # scaffold off its first choice.
    sites = [first, _site(blocker_at["x"] + 0.5, blocker_at["y"] + 0.5)]
    plan = generate_pumpjack_source(
        sites, [site["output"] for site in sites],
    )

    subs = [
        action for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == "substation"
    ]
    assert len(subs) == 1
    jack_tiles: set[tuple[int, int]] = set()
    for site in sites:
        jack_tiles.update(footprint_tile_indices(site["position"], 3))
    pos = subs[0]["position"]
    assert footprint_tile_indices((pos["x"], pos["y"]), 2).isdisjoint(jack_tiles)


def test_multi_pumpjack_power_avoids_jack_footprints() -> None:
    """2026-09-04: the shared power scaffold landed a substation on a new
    jack and plan validation killed the run. Colliding power placements are
    dropped (extend_power bridges the rest); jacks and pipes stand."""
    from planners.infrastructure_geometry import footprint_tile_indices
    from planners.resource_layouts import (
        generate_pumpjack_source, verified_pumpjack_output_tile,
    )

    def _site(x: float, y: float) -> dict:
        site = {
            "position": (x, y), "resource": "crude-oil", "direction": "east",
        }
        site["output"] = verified_pumpjack_output_tile(site)
        return site

    sites = [_site(-268.5, -98.5), _site(-276.5, -94.5)]
    plan = generate_pumpjack_source(
        sites, [site["output"] for site in sites],
    )

    jacks = [
        action for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == "pumpjack"
    ]
    assert len(jacks) == 2
    jack_tiles: set[tuple[int, int]] = set()
    for site in sites:
        jack_tiles.update(footprint_tile_indices(site["position"], 3))
    for phase in plan["phases"]:
        for action in phase["actions"]:
            entity = action.get("entity", "")
            if entity in {"substation", "electric-energy-interface"}:
                pos = action["position"]
                tiles = footprint_tile_indices((pos["x"], pos["y"]), 2)
                assert tiles.isdisjoint(jack_tiles), action


def test_remote_patch_picker_prefers_big_unused_cells() -> None:
    grid = {(0, 0): 2, (5, 5): 18, (9, 9): 9}
    assert stage_chemical._pick_remote_crude_cell(
        grid, (0.0, 0.0), {(5, 5)},
    ) == (9, 9)
    assert stage_chemical._pick_remote_crude_cell(
        grid, (0.0, 0.0), {(5, 5), (9, 9)},
    ) == (0, 0)
    assert stage_chemical._pick_remote_crude_cell(
        {}, (0.0, 0.0), set(),
    ) is None


def _expansion_world(monkeypatch, *, plastic_working: int):
    plastic = SimpleNamespace(
        machine_count=2, working_count=plastic_working,
        machine_positions=[(-281.0, -37.0)], produced_count=10,
    )
    refinery = SimpleNamespace(
        machine_count=1, working_count=0,
        machine_positions=[(-256.0, -104.0)], produced_count=5,
    )
    def _find(_c, _s, _f, recipe, _m, **_k):
        if recipe == "plastic-bar":
            return plastic
        if recipe in {"basic-oil-processing", "advanced-oil-processing"}:
            return refinery
        return None
    monkeypatch.setattr(stage_chemical.live_base, "find_line", _find)
    monkeypatch.setattr(
        stage_chemical, "_existing_outputs",
        lambda *_a: {"plastic-bar": (-279.0, -37.0)},
    )
    monkeypatch.setattr(
        stage_chemical, "ensure_logistic_coverage", lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        stage_chemical, "_district_pumpjacks", lambda *_a: ([(-269.0, -99.0)], 0),
    )
    monkeypatch.setattr(
        stage_chemical, "_crude_patch_grid",
        lambda *_a: {(20, 20): 12, (-6, -2): 9},
    )
    monkeypatch.setattr(
        stage_chemical, "_crude_patch_tiles",
        lambda *_a, **_k: [(1001.5, 1001.5), (1005.5, 1001.5)],
    )
    monkeypatch.setattr(
        stage_chemical, "_route_oil_fluid_link",
        lambda *_a, **_k: ({"phases": [{"name": "fluid", "actions": []}]}, [], False),
    )
    monkeypatch.setattr(stage_chemical.live_base, "occupied_tiles", lambda *_a, **_k: set())
    monkeypatch.setattr(stage_chemical.live_base, "water_tiles", lambda *_a, **_k: set())


def test_remote_crude_expands_on_idle_plastic(monkeypatch) -> None:
    """2026-09-04: one 9/s well against 40/s of plant draw. Idle plants +
    uncovered draw add the next patch; flowing plants never trigger."""
    _expansion_world(monkeypatch, plastic_working=0)
    submitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        stage_chemical, "_submit_oil_cell_packets",
        lambda _c, _b, _s, _f, packets, _e, **_k: submitted.extend(packets),
    )
    serviced: list[str] = []
    monkeypatch.setattr(
        stage_chemical, "live_base", stage_chemical.live_base,
    )
    calls: dict[str, object] = {}
    def _service(_c, _b, _s, _f, name, *_a, **_k):
        serviced.append(str(name))
    result = stage_chemical.ensure_oil_cell(
        object(), object(), "nauvis", "player", (3.0, -1.0),
        _service, lambda _m: None, target_output="plastic-bar",
    )

    assert result == {"plastic-bar": (-279.0, -37.0)}
    names = [name for name, _plan in submitted]
    assert any(name.startswith("chemical_crude_expansion_") for name in names)
    assert any("remote crude" in name for name in serviced)
    jacks = [
        action for _name, plan in submitted
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == "pumpjack"
    ]
    assert len(jacks) >= 1


def test_remote_crude_skips_flowing_plastic(monkeypatch) -> None:
    """Working plants mean the constraint is elsewhere: no survey storm."""
    _expansion_world(monkeypatch, plastic_working=2)
    monkeypatch.setattr(
        stage_chemical, "_submit_oil_cell_packets",
        lambda *_a, **_k: pytest.fail("flowing plastic must not expand crude"),
    )

    result = stage_chemical.ensure_oil_cell(
        object(), object(), "nauvis", "player", (3.0, -1.0),
        lambda *_a, **_k: None, lambda _m: None, target_output="plastic-bar",
    )

    assert result == {"plastic-bar": (-279.0, -37.0)}


def test_four_refinery_district_fits_without_overlap() -> None:
    """4 refineries + sulfur + plastic must tile without colliding (user:
    4 refineries to feed the plastic plants). Sulfur sits east of the
    measured row end, not at a fixed offset that a wider row overruns."""
    from planners.fluid_layouts import generate_fluid_machine_row
    from planners.infrastructure_geometry import footprint_tile_indices
    from planners.plan_validation import ENTITY_FOOTPRINTS

    refinery = generate_fluid_machine_row("basic-oil-processing", 4, 0, 0)
    east = max(
        action["position"]["x"]
        for phase in refinery["phases"] for action in phase["actions"]
    )
    sulfur = generate_fluid_machine_row("sulfur", 2, round(east) + 10, 16)
    plastic = generate_fluid_machine_row("plastic-bar", 2, -40, -40)

    def _tiles(plan: dict) -> set[tuple[int, int]]:
        cells: set[tuple[int, int]] = set()
        for phase in plan["phases"]:
            for action in phase["actions"]:
                pos = action["position"]
                size = ENTITY_FOOTPRINTS.get(action.get("entity", ""), 1)
                cells.update(
                    footprint_tile_indices((pos["x"], pos["y"]), size)
                )
        return cells

    refinery_tiles, sulfur_tiles, plastic_tiles = (
        _tiles(refinery), _tiles(sulfur), _tiles(plastic),
    )
    assert refinery_tiles.isdisjoint(sulfur_tiles)
    assert refinery_tiles.isdisjoint(plastic_tiles)
    assert sulfur_tiles.isdisjoint(plastic_tiles)
    assert east - 0.0 <= 26.0, "four-wide row must fit a buildable district"


def test_link_corridor_tiles_collects_only_pipe_tiles() -> None:
    links = [("chemical_crude_pipeline", {"phases": [
        {"name": "fluid_link_crude-oil", "actions": [
            {"action_type": "place_ghost", "entity": "pipe",
             "position": {"x": -297.5, "y": -57.5}},
            {"action_type": "place_ghost", "entity": "pipe-to-ground",
             "position": {"x": -290.5, "y": -57.5}, "direction": "west"},
            {"action_type": "place_ghost", "entity": "medium-electric-pole",
             "position": {"x": -280.5, "y": -57.5}},
        ]},
        {"name": "other", "actions": [
            {"action_type": "place_tile_ghost", "tile": "landfill",
             "position": {"x": 8, "y": 4}},
        ]},
    ]})]
    assert stage_chemical._link_corridor_tiles(links) == {(-298, -58), (-291, -58)}


def test_oil_cell_power_reserve_tiles_covers_machine_footprints() -> None:
    """2026-09-06: power hops blocked both refinery-row pipe continuity tiles.

    Link corridors plus growth reservations omitted future machine-row pipes,
    so bridges landed at (-310.5,-37.5) and (-310.5,-43.5) before the later
    refinery packet could ghost them.  Every machine footprint must be held
    for the bridge, including each pipe along the shared column.
    """
    machine = {"phases": [{"name": "row", "actions": [
        {"action_type": "place_ghost", "entity": "pipe",
         "position": {"x": -310.5, "y": -37.5}},
        {"action_type": "place_ghost", "entity": "pipe",
         "position": {"x": -310.5, "y": -43.5}},
        {"action_type": "place_ghost", "entity": "oil-refinery",
         "position": {"x": -286.0, "y": -26.0}},
    ]}], "reserved_tiles": [[-280, -30]]}
    links = [("chemical_crude_pipeline", {"phases": [{"name": "link", "actions": [
        {"action_type": "place_ghost", "entity": "pipe",
         "position": {"x": -291.0, "y": -58.0}},
    ]}]})]
    reserved = stage_chemical._oil_cell_power_reserve_tiles([machine], links)
    assert (-311, -38) in reserved  # refinery-row pipe tile
    assert (-311, -44) in reserved  # shared-column pipe continuity tile
    assert (-291, -58) in reserved  # link corridor tile
    assert (-280, -30) in reserved  # growth reservation tile
    assert any(
        tile != (-311, -38) and tile != (-291, -58) and tile != (-280, -30)
        for tile in reserved
    )  # the refinery body itself, not just the named tiles


def test_oil_power_connection_reserves_the_pipe_corridor(monkeypatch) -> None:
    """2026-09-05: the backbone power bridge chained through the just-routed
    crude corridor and the pipeline died on its pole. The corridor rides
    along to extend_power."""
    calls: list[dict] = []
    def _extend(_c, _b, _s, _f, position, _emit, **kwargs):
        calls.append({"position": position, **kwargs})
        return True
    monkeypatch.setattr(stage_chemical, "extend_power", _extend)
    plan = {"phases": [{"name": "power", "actions": [{
        "action_type": "place_entity", "entity": "substation",
        "position": {"x": -320.0, "y": -39.0},
    }]}]}

    stage_chemical._connect_oil_cell_power(
        object(), object(), "nauvis", "player", [plan], lambda _m: None,
        reserved_tiles={(-298, -58)},
    )

    assert len(calls) == 1
    assert calls[0]["reserved_tiles"] == {(-298, -58)}


def test_oil_link_dives_under_a_pole_before_failing() -> None:
    """2026-09-05: the crude pipeline ended two runs on a mid-pass power
    pole. The oil router now spends one pipe-to-ground pair (pass 2)
    before water crossings or failure. Walls seal the margin-48 bound so
    no surface detour exists."""
    hard = {(4, 0)} | {
        (x, y) for x in range(9) for y in range(-50, 51) if y != 0
    }

    link, segments, crossed_water = stage_chemical._route_oil_fluid_link(
        (0, 0), [(8, 0)], "crude-oil", foreign=[], hard=hard,
        terrain_water=set(), existing_tiles=[(0, 0)],
    )

    assert crossed_water is False
    assert {
        (int(a["position"]["x"] - 0.5), int(a["position"]["y"] - 0.5)): a["direction"]
        for phase in link["phases"] for a in phase["actions"]
        if a.get("entity") == "pipe-to-ground"
    } == {(3, 0): "west", (5, 0): "east"}
    assert stage_chemical._link_dive_tiles(segments, hard) == {(4, 0)}
