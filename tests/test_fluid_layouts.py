# Path: tests/test_fluid_layouts.py
# Purpose: Deterministic tests for planners/fluid_layouts.py -- machine-row
# geometry (pitch, tile collisions across 1x1/3x3/5x5 footprints), the
# live-verified pipe connection tiles, fluid network disjointness and the
# anti-mixing guard, the infinity-pipe fluid sources, schema validation and the
# electric-only fuel guard.

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.fluid_systems import MAX_UNDERGROUND_SPAN, validate_network_purity
from planners.fluid_layouts import (
    FLUID_RECIPES,
    LINK_TUNNEL_CLEARANCE,
    MACHINE_FOOTPRINTS,
    VERIFIED_PIPE_TILES,
    _pitch,
    _validate,
    fluid_chain_link_segments,
    fluid_chain_link_trunk,
    fluid_network_segments,
    generate_fluid_chain_link,
    generate_fluid_machine_row,
    generate_fluid_source,
    header_attachment,
    header_row,
    source_attachment,
)

# Footprints that the collision guarantee covers. 2x2 power scaffolding is
# excluded, exactly as tests/test_line_chaining.py excludes it.
FIVE_BY_FIVE = {"oil-refinery"}
THREE_BY_THREE = {"chemical-plant", "assembling-machine-2"}
TWO_BY_TWO = {"substation", "electric-energy-interface"}


def _tiles_for(action: dict) -> set:
    entity = action["entity"]
    x, y = action["position"]["x"], action["position"]["y"]
    if entity in FIVE_BY_FIVE:
        left, top = int(round(x - 2.5)), int(round(y - 2.5))
        return {(left + dx, top + dy) for dx in range(5) for dy in range(5)}
    if entity in THREE_BY_THREE:
        left, top = int(round(x - 1.5)), int(round(y - 1.5))
        return {(left + dx, top + dy) for dx in range(3) for dy in range(3)}
    return {(math.floor(x), math.floor(y))}


def _actions(plan: dict) -> list:
    return [a for phase in plan["phases"] for a in phase["actions"]]


def _at(plan: dict, tile: tuple) -> list:
    """Every action whose 1x1 body sits on `tile`."""
    return [a for a in _actions(plan)
            if a["entity"] not in TWO_BY_TWO and _tiles_for(a) == {tile}]


# --- determinism -------------------------------------------------------------

@pytest.mark.parametrize("recipe", sorted(FLUID_RECIPES))
def test_machine_row_is_deterministic(recipe):
    assert generate_fluid_machine_row(recipe, 3) == generate_fluid_machine_row(recipe, 3)


@pytest.mark.parametrize("recipe", sorted(FLUID_RECIPES))
def test_machine_row_translates_rigidly(recipe):
    here = _actions(generate_fluid_machine_row(recipe, 2, 0, 0))
    there = _actions(generate_fluid_machine_row(recipe, 2, 40, 25))
    assert len(here) == len(there)
    for a, b in zip(here, there):
        assert a["entity"] == b["entity"]
        assert b["position"]["x"] - a["position"]["x"] == 40
        assert b["position"]["y"] - a["position"]["y"] == 25


def test_fluid_source_is_deterministic():
    assert generate_fluid_source("water", 5, 5, 4) == generate_fluid_source("water", 5, 5, 4)


# --- tile collisions ---------------------------------------------------------

@pytest.mark.parametrize("recipe", sorted(FLUID_RECIPES))
@pytest.mark.parametrize("count", [1, 2, 5])
def test_no_tile_is_claimed_twice(recipe, count):
    occupied: dict = {}
    for action in _actions(generate_fluid_machine_row(recipe, count)):
        if action["entity"] in TWO_BY_TWO:
            continue
        for tile in _tiles_for(action):
            assert tile not in occupied, (
                f"{recipe}: {action['entity']} collides with {occupied.get(tile)} at {tile}"
            )
            occupied[tile] = action["entity"]


def test_refinery_five_by_five_bodies_do_not_overlap():
    plan = generate_fluid_machine_row("basic-oil-processing", 3)
    bodies = [_tiles_for(a) for a in _actions(plan) if a["entity"] == "oil-refinery"]
    assert len(bodies) == 3
    assert len(set().union(*bodies)) == 3 * 25


# --- the live-verified connection tiles --------------------------------------

def _machine_centres(recipe: str, count: int, ox: int = 0, oy: int = 0) -> list:
    width = MACHINE_FOOTPRINTS[FLUID_RECIPES[recipe]["machine"]]
    pitch = _pitch(recipe)
    return [(ox + i * pitch + width / 2, oy + 2 + width / 2) for i in range(count)]


@pytest.mark.parametrize("recipe", sorted(FLUID_RECIPES))
def test_every_connection_tile_holds_a_pipe_of_the_right_network(recipe):
    count, ox, oy = 3, 11, -4
    plan = generate_fluid_machine_row(recipe, count, ox, oy)
    segments = {s["fluid"]: set(s["tiles"])
                for s in fluid_network_segments(recipe, count, ox, oy)}
    tiles = VERIFIED_PIPE_TILES[recipe]
    for role in ("inputs", "outputs"):
        for fluid, (dx, dy) in tiles[role].items():
            for cx, cy in _machine_centres(recipe, count, ox, oy):
                tile = (math.floor(cx + dx), math.floor(cy + dy))
                placed = _at(plan, tile)
                assert [a["entity"] for a in placed] == ["pipe-to-ground"], (
                    f"{recipe}/{fluid}: expected a pipe-to-ground on {tile}, got {placed}"
                )
                assert tile in segments[fluid]
                for other, other_tiles in segments.items():
                    if other != fluid:
                        assert tile not in other_tiles


def test_processing_unit_acid_tile_is_the_centre_column_and_inserter_steps_aside():
    # Live-verified: an assembling machine's fluid box sits on its CENTRE
    # column, unlike the chemical plant's corner boxes -- so the item inserter
    # cannot use the centre and must take the column west of it.
    plan = generate_fluid_machine_row("processing-unit", 2)
    (cx, cy), _ = _machine_centres("processing-unit", 2)
    assert [a["entity"] for a in _at(plan, (math.floor(cx), math.floor(cy - 2)))] == ["pipe-to-ground"]
    assert [a["entity"] for a in _at(plan, (math.floor(cx - 1), math.floor(cy - 2)))] == ["fast-inserter"]


def test_chemical_plant_row_keeps_the_tight_pitch_but_two_fluids_spread_it():
    assert _pitch("plastic-bar") == 3
    assert _pitch("sulfuric-acid") == 3
    assert _pitch("sulfur") == 4  # water and gas would otherwise touch
    assert _pitch("basic-oil-processing") == 5


# --- network purity ----------------------------------------------------------

@pytest.mark.parametrize("recipe", sorted(FLUID_RECIPES))
@pytest.mark.parametrize("count", [1, 4])
def test_generated_rows_pass_the_purity_guard(recipe, count):
    validate_network_purity(fluid_network_segments(recipe, count))


def test_two_fluid_recipe_yields_two_disjoint_networks():
    segments = fluid_network_segments("sulfur", 4)
    assert sorted(s["fluid"] for s in segments) == ["petroleum-gas", "water"]
    water, gas = (set(s["tiles"]) for s in sorted(segments, key=lambda s: s["fluid"] != "water"))
    assert not water & gas
    # Disjoint is not enough: no tile of one may even touch a tile of the other.
    assert not any((x + dx, y + dy) in gas
                   for x, y in water for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))


def test_input_and_output_fluids_get_separate_networks():
    segments = fluid_network_segments("sulfuric-acid", 3)
    assert sorted(s["fluid"] for s in segments) == ["sulfuric-acid", "water"]
    validate_network_purity(segments)


def test_doctored_row_that_moves_two_fluids_together_raises():
    segments = fluid_network_segments("sulfur", 3)
    water = next(s for s in segments if s["fluid"] == "water")
    gas = next(s for s in segments if s["fluid"] == "petroleum-gas")
    # Slide the whole gas network one tile west: its stubs land directly beside
    # the water stubs, which is the unrecoverable mixing case from docs/23.
    gas["tiles"] = [(x - 1, y) for x, y in gas["tiles"]]
    with pytest.raises(ValueError, match="Fluid mixing"):
        validate_network_purity([water, gas])


def test_doctored_row_that_drops_a_header_onto_the_other_row_raises():
    segments = fluid_network_segments("sulfur", 3)
    gas = next(s for s in segments if s["fluid"] == "petroleum-gas")
    gas["tiles"] = [(x, y + 3) for x, y in gas["tiles"]]  # slot 1 pushed onto slot 0
    with pytest.raises(ValueError, match="Fluid mixing"):
        validate_network_purity(segments)


# --- schema and fuel guard ---------------------------------------------------

@pytest.mark.parametrize("recipe", sorted(FLUID_RECIPES))
def test_generated_plans_validate_against_the_schema(recipe):
    _validate(generate_fluid_machine_row(recipe, 2))


def test_schema_rejects_a_doctored_action_type():
    plan = generate_fluid_machine_row("plastic-bar", 1)
    plan["phases"][1]["actions"][0]["action_type"] = "teleport_entity"
    with pytest.raises(ValueError, match="BuildPlan validation FAILED"):
        _validate(plan)


def test_fuel_guard_rejects_a_doctored_burner():
    plan = generate_fluid_machine_row("sulfur", 1)
    plan["phases"][0]["actions"].append(
        {"action_type": "place_entity", "entity": "boiler", "position": {"x": 0.5, "y": 0.5}}
    )
    with pytest.raises(ValueError, match="Electric-only invariant"):
        _validate(plan)


@pytest.mark.parametrize("recipe", sorted(FLUID_RECIPES))
def test_rows_place_no_fuel_burning_entity(recipe):
    entities = {a["entity"] for a in _actions(generate_fluid_machine_row(recipe, 3))}
    assert not entities & {"boiler", "steam-engine", "stone-furnace", "burner-inserter"}


# --- power -------------------------------------------------------------------

@pytest.mark.parametrize("recipe", sorted(FLUID_RECIPES))
def test_every_machine_has_a_pole_within_medium_supply_range(recipe):
    count = 3
    plan = generate_fluid_machine_row(recipe, count)
    poles = [a["position"] for a in _actions(plan) if a["entity"] == "medium-electric-pole"]
    assert poles
    for cx, cy in _machine_centres(recipe, count):
        # medium-electric-pole supplies a 7x7 area centred on its own tile.
        assert any(abs(p["x"] - cx) <= 3.5 and abs(p["y"] - cy) <= 3.5 for p in poles), \
            f"{recipe}: machine at {(cx, cy)} is out of every pole's supply area"


# --- fluid sources -----------------------------------------------------------

@pytest.mark.parametrize("kind", ["water", "crude-oil"])
def test_fluid_source_emits_a_filtered_infinity_pipe(kind):
    plan = generate_fluid_source(kind, 3, 7, 2)
    actions = _actions(plan)
    assert actions[0] == {"action_type": "place_entity", "entity": "infinity-pipe",
                          "position": {"x": 3.5, "y": 7.5}, "infinity_filter": kind}
    assert [a["entity"] for a in actions[1:]] == ["pipe", "pipe"]
    assert [a["position"]["x"] for a in actions[1:]] == [4.5, 5.5]


def test_fluid_source_rejects_an_unknown_kind():
    with pytest.raises(ValueError, match="Unknown fluid source kind"):
        generate_fluid_source("lava")


def test_fluid_source_rejects_a_negative_run():
    with pytest.raises(ValueError, match="run_length must be non-negative"):
        generate_fluid_source("water", run_length=-1)


# --- input guards ------------------------------------------------------------

def test_unknown_recipe_is_refused():
    with pytest.raises(ValueError, match="No fluid recipe knowledge"):
        generate_fluid_machine_row("uranium-processing", 1)


def test_zero_machines_is_refused():
    with pytest.raises(ValueError, match="machine_count must be positive"):
        generate_fluid_machine_row("sulfur", 0)


def test_unknown_tiers_are_refused():
    with pytest.raises(ValueError, match="Unknown belt tier"):
        generate_fluid_machine_row("sulfur", 1, belt_type="wooden-belt")
    with pytest.raises(ValueError, match="Unknown inserter tier"):
        generate_fluid_machine_row("sulfur", 1, inserter_type="long-handed-inserter")


# --- header rows and attachment points ---------------------------------------

def test_header_row_matches_the_rows_the_generator_actually_pipes():
    # The rule and the placed pipes must agree for every fluid of every recipe;
    # a chain link that trusted header_row() but missed by a row would connect
    # to nothing, which is exactly the failure this whole feature exists to fix.
    for recipe in sorted(FLUID_RECIPES):
        count, ox, oy = 3, 60, 30
        plan = generate_fluid_machine_row(recipe, count, ox, oy)
        width = MACHINE_FOOTPRINTS[FLUID_RECIPES[recipe]["machine"]]
        piped_rows = {int(a["position"]["y"] - 0.5)
                      for a in _actions(plan) if a["entity"] == "pipe"}
        for fluid in list(VERIFIED_PIPE_TILES[recipe]["inputs"]) + \
                list(VERIFIED_PIPE_TILES[recipe]["outputs"]):
            spot = header_attachment(recipe, fluid, count, ox, oy)
            assert spot["row"] == header_row(oy, width, spot["side"], spot["slot"])
            assert spot["row"] in piped_rows
            assert spot["west"] == (ox - 1, spot["row"])
            # The attachment sits one row further from the machines than the
            # header, and that row carries no pipe of this row at all.
            assert abs(spot["attach"][1] - spot["row"]) == 1
            assert spot["attach"][1] not in piped_rows


def test_attachment_touches_the_header_and_nothing_else_in_the_row():
    count, ox, oy = 2, 0, 0
    for recipe in sorted(FLUID_RECIPES):
        segments = {s["fluid"]: set(s["tiles"])
                    for s in fluid_network_segments(recipe, count, ox, oy)}
        for fluid, tiles in segments.items():
            attach = header_attachment(recipe, fluid, count, ox, oy)["attach"]
            neighbours = {(attach[0] + dx, attach[1] + dy)
                          for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))}
            assert neighbours & tiles, f"{recipe}/{fluid}: attachment touches no header tile"
            for other, other_tiles in segments.items():
                if other != fluid:
                    assert not neighbours & other_tiles
                    assert attach not in other_tiles


def test_header_row_rejects_a_bad_side_or_slot():
    with pytest.raises(ValueError, match="Unknown header side"):
        header_row(0, 3, "east", 0)
    with pytest.raises(ValueError, match="slot must be non-negative"):
        header_row(0, 3, "north", -1)


def test_attachment_refuses_a_fluid_the_recipe_does_not_use():
    with pytest.raises(ValueError, match="no 'lubricant' header"):
        header_attachment("sulfur", "lubricant", 2)


def test_source_attachment_is_west_of_the_infinity_pipe():
    # generate_fluid_source runs its pipes EAST, so the west end is always free.
    assert source_attachment(200, 244) == (199, 244)


# --- chain link routing ------------------------------------------------------

def _link_tiles(plan: dict) -> dict:
    return {(int(a["position"]["x"] - 0.5), int(a["position"]["y"] - 0.5)): a
            for a in _actions(plan)}


def test_chain_link_is_deterministic_and_z_shaped():
    args = ((20, 5), [(20, 40)], "water", 10)
    assert generate_fluid_chain_link(*args) == generate_fluid_chain_link(*args)
    tiles = _link_tiles(generate_fluid_chain_link(*args))
    assert set(tiles) == (
        {(x, 5) for x in range(10, 21)}
        | {(10, y) for y in range(5, 41)}
        | {(x, 40) for x in range(10, 21)}
    )
    assert {a["entity"] for a in tiles.values()} == {"pipe"}


def test_chain_link_branches_once_per_consumer_on_a_single_trunk():
    plan = generate_fluid_chain_link((20, 5), [(20, 20), (20, 40)], "water", 10)
    tiles = set(_link_tiles(plan))
    assert {(10, y) for y in range(5, 41)} <= tiles       # one shared trunk
    for row in (5, 20, 40):
        assert {(x, row) for x in range(10, 21)} <= tiles  # one leg each
    assert not any(y for _, y in tiles if y > 40)


def test_chain_link_dives_under_a_foreign_fluid_and_leaves_it_a_clear_tile():
    foreign = [{"fluid": "petroleum-gas", "separated_by_pump": False,
                "tiles": [(15, y) for y in range(0, 50)]}]
    plan = generate_fluid_chain_link((20, 5), [(20, 40)], "water", 10, foreign)
    tiles = _link_tiles(plan)
    clear = LINK_TUNNEL_CLEARANCE
    for row in (5, 40):
        for offset in range(-clear + 1, clear):
            assert (15 + offset, row) not in tiles, "link ran straight over the foreign fluid"
        west, east = tiles[(15 - clear, row)], tiles[(15 + clear, row)]
        assert west["entity"] == east["entity"] == "pipe-to-ground"
        # `direction` is where the NORMAL end points, so the two ends of one
        # tunnel face away from each other and only mate underground.
        assert (west["direction"], east["direction"]) == ("west", "east")
    assert 2 * clear <= MAX_UNDERGROUND_SPAN
    # And the emitted set is still pure against the fluid it crossed.
    validate_network_purity(
        fluid_chain_link_segments((20, 5), [(20, 40)], "water", 10, foreign) + foreign
    )


def test_chain_link_refuses_a_trunk_column_that_is_not_clear():
    foreign = [{"fluid": "petroleum-gas", "separated_by_pump": False,
                "tiles": [(10, 20)]}]
    with pytest.raises(ValueError, match="Trunk column 10 is not clear"):
        generate_fluid_chain_link((20, 5), [(20, 40)], "water", 10, foreign)


def test_chain_link_refuses_a_crossing_it_cannot_tunnel_under():
    # Foreign fluid one tile from the leg's end: no room for the pipe-to-ground
    # pair plus a pipe on each side, so refuse rather than emit a mixing hazard.
    foreign = [{"fluid": "petroleum-gas", "separated_by_pump": False,
                "tiles": [(19, 5)]}]
    with pytest.raises(ValueError, match="No room to tunnel under"):
        generate_fluid_chain_link((20, 5), [(20, 40)], "water", 10, foreign)


def test_chain_link_tunnels_under_a_nearby_crossing_run_once():
    foreign = [{"fluid": "petroleum-gas", "separated_by_pump": False,
                "tiles": [(14, 5), (16, 5)]}]
    plan = generate_fluid_chain_link((20, 5), [(20, 40)], "water", 10, foreign)
    tiles = _link_tiles(plan)
    assert tiles[(12, 5)]["entity"] == "pipe-to-ground"
    assert tiles[(18, 5)]["entity"] == "pipe-to-ground"


def test_chain_link_refuses_a_trunk_east_of_its_attachments():
    with pytest.raises(ValueError, match="must lie strictly WEST"):
        generate_fluid_chain_link((20, 5), [(20, 40)], "water", 25)


def test_chain_link_refuses_no_consumer():
    with pytest.raises(ValueError, match="at least one consumer"):
        generate_fluid_chain_link((20, 5), [], "water", 10)


def test_chain_link_purity_guard_bites_when_a_route_runs_beside_a_foreign_fluid():
    # A foreign fluid one tile east of the trunk is not ON the route, so the
    # router has nothing to tunnel under -- the purity guard is what catches it.
    foreign = [{"fluid": "petroleum-gas", "separated_by_pump": False,
                "tiles": [(11, y) for y in range(10, 30)]}]
    with pytest.raises(ValueError, match="Fluid mixing"):
        generate_fluid_chain_link((20, 5), [(20, 40)], "water", 10, foreign)


def test_chain_link_trunk_is_just_the_column():
    assert fluid_chain_link_trunk((20, 5), [(20, 20), (20, 40)], 10) == \
        [(10, y) for y in range(5, 41)]


def test_chain_link_plans_validate_against_the_schema():
    _validate(generate_fluid_chain_link((20, 5), [(20, 40)], "water", 10))


# --- the real processing-unit chain ------------------------------------------

def test_processing_unit_chain_links_are_pure_against_every_stage(processing_bundle):
    # The fixed STAGES/LINKS table that used to live in
    # tools/build_processing_units.py was absorbed into
    # planners/electronics_block.py's _fluid_routes(), which is exercised here
    # through the real, world-spec-driven build_electronics_block(). Building
    # the bundle already raises on any mixing hazard or double-claimed tile
    # (validate_network_purity runs inside _fluid_routes), so a bundle coming
    # back at all is itself proof the chain links are pure.
    bundle = processing_bundle

    expected_fluids = {"crude-oil", "petroleum-gas", "water", "sulfuric-acid"}
    link_names = [name for name, _ in bundle["plans"] if name.startswith("link_")]
    assert sorted(link_names) == sorted(
        f"link_{fluid.replace('-', '_')}" for fluid in expected_fluids
    )

    stages = {s["fluid"] for s in bundle["fluid_segments"]}
    assert expected_fluids <= stages
    validate_network_purity(bundle["fluid_segments"])

    # Each link plan must actually route pipe (or pipe-to-ground) actions --
    # a link that only validated purity without emitting anything would pass
    # every check above yet feed nothing.
    plans_by_name = dict(bundle["plans"])
    for fluid in expected_fluids:
        link_plan = plans_by_name[f"link_{fluid.replace('-', '_')}"]
        entities = {a["entity"] for a in _actions(link_plan)}
        assert entities <= {"pipe", "pipe-to-ground"}
        assert entities, f"link_{fluid} emitted no pipe actions"
