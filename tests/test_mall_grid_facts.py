# Path: tests/test_mall_grid_facts.py
# Purpose: Protect the shared mall-grid facts that keep new cells adjacent, keep
#          coverage/power off planned slots, and reject genuinely occupied ones.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator import mall_builder
from orchestrator.mall_builder import (
    _cell_footprint_tiles, _cell_origins, _MALL_SLOT_SERVICE_RANGE,
    _rank_empty_origins, assert_mall_cell_footprint_free, mall_grid_reserved_tiles,
    mall_cell_footprint_conflicts, powered_reserved_substation_neighbor,
    reserved_substation_position,
)
from planners.infrastructure import ROBOPORT_SIZE
from planners.infrastructure_geometry import footprint_tile_indices
from planners.plan_validation import occupied_tile_indices
from planners.recipe_data import LINE_RECIPES
from planners.roboport_coverage import footprint_tiles, plan_coverage_roboports

_REFERENCE = (3.0, -1.0)


def _states(origins, committed):
    states = {origin: ("-", "-", "-") for origin in origins}
    for origin in committed:
        states[origin] = ("iron-gear-wheel", "copper-cable", "requester-chest")
    return states


def test_new_cells_are_chosen_adjacent_to_the_committed_bank(monkeypatch) -> None:
    """A growing bank must fill the site nearest it, not the next arbitrary one.

    Live 2026-10-10: the long-handed-inserter cell was assigned to the far
    (46,49) site while nearer sites were free, so it needed its own coverage
    and a long pole corridor. The old list order picked (57,31) here; the
    adjacency ranking picks the site beneath the seed bank.
    """
    origins = _cell_origins(_REFERENCE)
    committed = origins[:2]
    states = _states(origins, committed)
    monkeypatch.setattr(mall_builder, "_district_state", lambda *_a: states)
    monkeypatch.setattr(mall_builder, "_side_clear", lambda *_a: True)
    monkeypatch.setattr(
        mall_builder.live_base, "area_clear", lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        mall_builder.resource_patches, "box_has_reserved_patch",
        lambda *_a, **_k: False,
    )

    origin, side = mall_builder._choose_slot(
        object(), "nauvis", "electronic-circuit", _REFERENCE,
    )

    assert (origin, side) == ((47, 41), "left")
    assert origin not in committed
    # Nearest-open ranking still works within the supplied cross lattice.
    first_uncommitted = next(o for o in origins if o not in committed)
    assert first_uncommitted == (32, 41)


def test_adjacency_ranking_keeps_the_seed_order_when_nothing_is_committed() -> None:
    origins = _cell_origins(_REFERENCE)
    empties = [origin for origin in origins if origin not in origins[:2]][:5]

    assert _rank_empty_origins(empties, [], _REFERENCE) == empties


def test_mall_grid_reserved_tiles_cover_planned_slots_dense_bank_and_substation() -> None:
    reserved = mall_grid_reserved_tiles(_REFERENCE)

    # Every paired-cell slot, whether or not it has been built.
    for origin in _cell_origins(_REFERENCE):
        assert _cell_footprint_tiles(origin) <= reserved
    # The dense 4->12 bank's vertical machines (top quad lane).
    assert (39, 27) in reserved
    assert (36, 47) in reserved
    # The shared seed substation.
    assert (43, 35) in reserved


def test_coverage_roboports_avoid_planned_mall_slots() -> None:
    """A coverage chain crossing the mall must not land on a planned slot.

    This is the (40,26) failure in miniature: coverage for a target beyond the
    mall used to place a roboport on a not-yet-built slot footprint. The shared
    grid reservation keeps the chain out of every planned slot.
    """
    reserved = mall_grid_reserved_tiles(_REFERENCE)
    source, target = (-40.0, 40.0), (80.0, 40.0)

    unguarded = plan_coverage_roboports([source], [target])
    assert any(
        footprint_tiles(port, ROBOPORT_SIZE) & reserved for port in unguarded
    ), "the unguarded chain is expected to cross a planned slot"

    guarded = plan_coverage_roboports([source], [target], obstacles=reserved)
    assert guarded, "the chain still has to reach the target"
    assert not any(
        footprint_tiles(port, ROBOPORT_SIZE) & reserved for port in guarded
    )


def test_reserved_substation_sits_outside_every_planned_slot_band() -> None:
    origins = _cell_origins(_REFERENCE)
    for origin in origins[2:]:
        position = reserved_substation_position(_REFERENCE, origin)
        tiles = footprint_tiles(position, 2)
        # The inter-module corner clears all four machine footprints.
        for side in ("left", "right", "north", "south"):
            machine = (mall_builder._vertical_slot_position(origin, side)
                       if side in {"north", "south"} else mall_builder._slot_position(origin, side))
            assert not tiles & footprint_tiles(machine, 3)
        # It must still be inside the substation's own supply reach of the cell.
        for side in ("left", "right", "north", "south"):
            machine = (mall_builder._vertical_slot_position(origin, side)
                       if side in {"north", "south"} else mall_builder._slot_position(origin, side))
            assert max(
                abs(position[0] - machine[0]), abs(position[1] - machine[1]),
            ) <= _MALL_SLOT_SERVICE_RANGE


def test_seed_cells_share_one_reserved_substation_between_them() -> None:
    origin, other = _cell_origins(_REFERENCE)[:2]
    assert reserved_substation_position(
        _REFERENCE, origin,
    ) == reserved_substation_position(_REFERENCE, other)


def test_each_later_paired_cell_funds_its_reserved_substation() -> None:
    first, second, later = _cell_origins(_REFERENCE)[:3]

    assert mall_builder.seed_cell_includes_substation(_REFERENCE, first, "left")
    assert not mall_builder.seed_cell_includes_substation(_REFERENCE, second, "left")
    assert mall_builder.seed_cell_includes_substation(_REFERENCE, later, "left")
    assert not mall_builder.seed_cell_includes_substation(_REFERENCE, later, "right")


def test_powered_adjacent_reserved_substation_avoids_another_pole_bridge(monkeypatch) -> None:
    target_origin = _cell_origins(_REFERENCE)[2]
    target = reserved_substation_position(_REFERENCE, target_origin)
    neighbor = reserved_substation_position(_REFERENCE, _cell_origins(_REFERENCE)[1])
    monkeypatch.setattr(
        mall_builder.live_base, "area_entity_records",
        lambda *_a, **_k: [{
            "name": "substation", "type": "electric-pole",
            "position": neighbor, "force": "player", "is_ghost": False,
        }],
    )
    monkeypatch.setattr(
        mall_builder.live_base, "pole_network_id", lambda *_a: 7,
    )
    monkeypatch.setattr(
        mall_builder.live_base, "network_generation_kw", lambda *_a: 5000.0,
    )

    assert powered_reserved_substation_neighbor(
        SimpleNamespace(command=lambda *_a: ""),
        "nauvis", "player", _REFERENCE, target,
    ) == neighbor


def test_unpowered_or_ghost_substation_does_not_claim_auto_connection(monkeypatch) -> None:
    target = reserved_substation_position(_REFERENCE, _cell_origins(_REFERENCE)[2])
    neighbor = reserved_substation_position(_REFERENCE, _cell_origins(_REFERENCE)[1])
    monkeypatch.setattr(
        mall_builder.live_base, "area_entity_records",
        lambda *_a, **_k: [{
            "name": "substation", "type": "entity-ghost",
            "position": neighbor, "force": "player", "is_ghost": True,
        }],
    )

    assert powered_reserved_substation_neighbor(
        SimpleNamespace(command=lambda *_a: ""),
        "nauvis", "player", _REFERENCE, target,
    ) is None


def test_cell_footprint_validation_flags_foreign_same_name_chest(monkeypatch) -> None:
    spec = LINE_RECIPES["electronic-circuit"]
    plan = mall_builder.generate_paired_mall_layout(
        "electronic-circuit", spec["machine"], spec["ingredients"],
        spec["amounts"], (35, 31), "left", craft_time=spec["craft_time"],
    )
    requester = next(
        action for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == "requester-chest"
    )
    intended = requester["position"]
    foreign = next(
        tile for tile in occupied_tile_indices([("cell", plan)])
        if tile not in footprint_tiles(
            (intended["x"], intended["y"]), 1,
        )
    )
    foreign_centre = (foreign[0] + 0.5, foreign[1] + 0.5)
    monkeypatch.setattr(
        mall_builder.live_base, "occupied_tile_owners",
        lambda *_a, **_k: {foreign: ("requester-chest", *foreign_centre)},
    )
    monkeypatch.setattr(
        mall_builder.live_base, "area_entity_records",
        lambda *_a, **_k: [{
            "name": "requester-chest", "type": "logistic-container",
            "position": foreign_centre, "ghost_name": None, "is_ghost": False,
        }],
    )
    monkeypatch.setattr(
        mall_builder.live_base, "water_tiles", lambda *_a, **_k: set(),
    )
    client = SimpleNamespace(command=lambda *_a: "")

    assert mall_cell_footprint_conflicts(client, "nauvis", plan) == (foreign,)
    with pytest.raises(mall_builder.StuckError) as raised:
        assert_mall_cell_footprint_free(
            client, "nauvis", plan, "electronic-circuit", (35, 31), "left",
        )

    assert raised.value.code == "mall_slot_occupied"
    assert raised.value.details["origin"] == [35, 31]


def test_exact_planned_live_entity_and_ghost_are_allowed(monkeypatch) -> None:
    spec = LINE_RECIPES["electronic-circuit"]
    plan = mall_builder.generate_paired_mall_layout(
        "electronic-circuit", spec["machine"], spec["ingredients"],
        spec["amounts"], (35, 31), "left", craft_time=spec["craft_time"],
    )
    machine = next(
        action for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity", "").startswith("assembling-machine")
    )
    ghost_action = next(
        action for phase in plan["phases"] for action in phase["actions"]
        if action.get("action_type") == "place_ghost"
    )
    live_pos = (machine["position"]["x"], machine["position"]["y"])
    ghost_pos = (ghost_action["position"]["x"], ghost_action["position"]["y"])
    live_tiles = footprint_tile_indices(live_pos, 3)
    ghost_tiles = footprint_tile_indices(ghost_pos, 1)
    owner_map = {
        tile: (machine["entity"], *live_pos) for tile in live_tiles
    }
    owner_map.update({tile: ("entity-ghost", *ghost_pos) for tile in ghost_tiles})
    records = [
        {"name": machine["entity"], "position": live_pos,
         "ghost_name": None, "is_ghost": False},
        {"name": "entity-ghost", "position": ghost_pos,
         "ghost_name": ghost_action["entity"], "is_ghost": True},
    ]
    monkeypatch.setattr(
        mall_builder.live_base, "occupied_tile_owners",
        lambda *_a, **_k: owner_map,
    )
    monkeypatch.setattr(
        mall_builder.live_base, "area_entity_records",
        lambda *_a, **_k: records,
    )
    monkeypatch.setattr(
        mall_builder.live_base, "water_tiles", lambda *_a, **_k: set(),
    )
    fake = SimpleNamespace(command=lambda *_a: "")

    assert mall_cell_footprint_conflicts(fake, "nauvis", plan) == ()


def test_foreign_overlap_is_not_hidden_by_an_exact_planned_ghost(monkeypatch) -> None:
    spec = LINE_RECIPES["electronic-circuit"]
    plan = mall_builder.generate_paired_mall_layout(
        "electronic-circuit", spec["machine"], spec["ingredients"],
        spec["amounts"], (35, 31), "left", craft_time=spec["craft_time"],
    )
    ghost_action = next(
        action for phase in plan["phases"] for action in phase["actions"]
        if action.get("action_type") == "place_ghost"
    )
    position = (
        ghost_action["position"]["x"], ghost_action["position"]["y"],
    )
    overlap = next(iter(footprint_tile_indices(position, 1)))
    owner_map = {overlap: ("entity-ghost", *position)}
    records = [
        {"name": "entity-ghost", "position": position,
         "ghost_name": ghost_action["entity"], "is_ghost": True,
         "type": "entity-ghost", "force": "player"},
        {"name": "medium-electric-pole", "position": (overlap[0] + 0.5, overlap[1] + 0.5),
         "ghost_name": None, "is_ghost": False,
         "type": "electric-pole", "force": "player"},
    ]
    monkeypatch.setattr(
        mall_builder.live_base, "occupied_tile_owners",
        lambda *_a, **_k: owner_map,
    )
    monkeypatch.setattr(
        mall_builder.live_base, "area_entity_records",
        lambda *_a, **_k: records,
    )
    monkeypatch.setattr(
        mall_builder.live_base, "water_tiles", lambda *_a, **_k: set(),
    )

    assert overlap in mall_cell_footprint_conflicts(
        SimpleNamespace(command=lambda *_a: ""), "nauvis", plan,
    )


def test_water_in_the_planned_cell_is_always_a_conflict(monkeypatch) -> None:
    spec = LINE_RECIPES["electronic-circuit"]
    plan = mall_builder.generate_paired_mall_layout(
        "electronic-circuit", spec["machine"], spec["ingredients"],
        spec["amounts"], (35, 31), "left", craft_time=spec["craft_time"],
    )
    water = next(iter(occupied_tile_indices([("cell", plan)])))
    monkeypatch.setattr(
        mall_builder.live_base, "occupied_tile_owners", lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        mall_builder.live_base, "area_entity_records", lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        mall_builder.live_base, "water_tiles", lambda *_a, **_k: {water},
    )

    assert mall_cell_footprint_conflicts(
        SimpleNamespace(command=lambda *_a: ""), "nauvis", plan,
    ) == (water,)


def test_unknown_footprint_survey_defers_safely_with_diagnostic(monkeypatch) -> None:
    from orchestrator.autonomous_builder import ProductionPrerequisiteDeferred

    spec = LINE_RECIPES["electronic-circuit"]
    plan = mall_builder.generate_paired_mall_layout(
        "electronic-circuit", spec["machine"], spec["ingredients"],
        spec["amounts"], (35, 31), "left", craft_time=spec["craft_time"],
    )
    monkeypatch.setattr(
        mall_builder.live_base, "occupied_tile_owners",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("malformed survey")),
    )
    fake = SimpleNamespace(command=lambda *_a: "")

    with pytest.raises(ProductionPrerequisiteDeferred) as raised:
        assert_mall_cell_footprint_free(
            fake, "nauvis", plan, "electronic-circuit", (35, 31), "left",
        )

    assert raised.value.code == "mall_slot_survey_unavailable"
    assert raised.value.state == "planned"
    assert "malformed survey" in str(raised.value)


@pytest.mark.parametrize("robot_type", ["logistic-robot", "construction-robot", "combat-robot"])
def test_flying_robot_does_not_occupy_a_mall_half_or_hide_a_real_blocker(monkeypatch, robot_type):
    spec = LINE_RECIPES["electronic-circuit"]
    plan = mall_builder.generate_paired_mall_layout(
        "electronic-circuit", spec["machine"], spec["ingredients"], spec["amounts"],
        (32, 32), "right", craft_time=spec["craft_time"], include_substation=False)
    tile, centre = (40, 34), (40.5, 34.5)
    records = [{"name": robot_type, "type": robot_type, "force": "player",
                "position": centre, "is_ghost": False}]
    monkeypatch.setattr(mall_builder.live_base, "occupied_tile_owners",
                        lambda *_: {tile: (robot_type, *centre)})
    monkeypatch.setattr(mall_builder.live_base, "area_entity_records", lambda *_: records)
    monkeypatch.setattr(mall_builder.live_base, "water_tiles", lambda *_: set())
    client = SimpleNamespace(command=lambda _: "")
    assert mall_cell_footprint_conflicts(client, "nauvis", plan) == ()
    records.append({"name": "medium-electric-pole", "type": "electric-pole", "force": "player",
                    "position": centre, "is_ghost": False})
    assert mall_cell_footprint_conflicts(client, "nauvis", plan) == (tile,)


@pytest.mark.lua
def test_live_placement_surveys_ignore_flying_robots_but_keep_a_real_chest():
    import shutil
    import subprocess
    from orchestrator import live_base
    lua = shutil.which("lua")
    if not lua:
        pytest.skip("Lua interpreter unavailable")

    class LuaClient:
        blocker = False

        def command(self, command):
            world = '''
local box={left_top={x=40.1,y=34.1},right_bottom={x=40.9,y=34.9}}
local entities={}
for _,kind in ipairs({'logistic-robot','construction-robot','combat-robot'}) do
  entities[#entities+1]={name=kind,type=kind,force={name='player'},position={x=40.5,y=34.5},bounding_box=box}
end
if BLOCKER then entities[#entities+1]={name='passive-provider-chest',type='logistic-container',
  force={name='player'},position={x=40.5,y=34.5},bounding_box=box} end
game={surfaces={nauvis={find_entities_filtered=function(_) return entities end,
  find_tiles_filtered=function(_) return {} end,
  get_tile=function() return {valid=true,name='grass-1'} end}}}
rcon={print=function(value) print(value) end}
'''.replace("BLOCKER", "true" if self.blocker else "false")
            return subprocess.run([lua, "-"], input=world + command.removeprefix("/sc "),
                                  text=True, capture_output=True, check=True).stdout

    client = LuaClient()
    for blocked in (False, True):
        client.blocker = blocked
        assert live_base.area_clear(client, "nauvis", (38, 32), (42, 36)) is not blocked
        assert live_base.occupied_tiles(client, "nauvis", (38, 32), (42, 36)) == ({(40, 34)} if blocked else set())
        assert live_base.occupied_tile_owners(client, "nauvis", (38, 32), (42, 36)) == (
            {(40, 34): ("passive-provider-chest", 40.5, 34.5)} if blocked else {})
