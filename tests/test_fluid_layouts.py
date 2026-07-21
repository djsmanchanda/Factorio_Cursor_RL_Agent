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

from core.fluid_systems import validate_network_purity
from planners.fluid_layouts import (
    FLUID_RECIPES,
    MACHINE_FOOTPRINTS,
    VERIFIED_PIPE_TILES,
    _pitch,
    _validate,
    fluid_network_segments,
    generate_fluid_machine_row,
    generate_fluid_source,
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
