# Path: tests/test_line_chaining.py
# Purpose: Deterministic tests for sideload feeding and producer->consumer chain links in LocalLayoutPlanner.

from __future__ import annotations

import math
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from planners.local_layout_planner import (
    LocalLayoutPlanner,
    SIDELOAD_NORTH_COL,
    SIDELOAD_SOUTH_COL,
    _reject_fuel_entities,
)

# Footprints in tiles. Only 1x1 and 3x3 entities are collision-checked (the task
# scopes the collision guarantee to those); 2x2 power scaffolding is excluded.
THREE_BY_THREE = {"assembling-machine-2", "electric-furnace", "electric-mining-drill"}
TWO_BY_TWO = {"substation", "electric-energy-interface"}


def _tiles_for(action: dict) -> set:
    entity = action["entity"]
    pos = action["position"]
    x, y = pos["x"], pos["y"]
    if entity in THREE_BY_THREE:
        left = int(round(x - 1.5))
        top = int(round(y - 1.5))
        return {(left + dx, top + dy) for dx in range(3) for dy in range(3)}
    return {(math.floor(x), math.floor(y))}


def _all_actions(plan: dict) -> list:
    return [a for phase in plan["phases"] for a in phase["actions"]]


# --------------------------------------------------------------------------- #
# Sideload feeding
# --------------------------------------------------------------------------- #

def test_sideload_plan_is_deterministic():
    planner = LocalLayoutPlanner()
    a = planner.generate_line_layout("electronic-circuit", 6, 0, 0, feed_style="sideload", belt_type="turbo-transport-belt")
    b = planner.generate_line_layout("electronic-circuit", 6, 0, 0, feed_style="sideload", belt_type="turbo-transport-belt")
    assert a == b


def test_chest_mode_unchanged_by_new_param():
    # Backward compatibility: the default path must produce the original plan.
    planner = LocalLayoutPlanner()
    default = planner.generate_line_layout("iron-gear-wheel", 8, 0, 40)
    explicit = planner.generate_line_layout("iron-gear-wheel", 8, 0, 40, feed_style="chest")
    assert default == explicit


def test_sideload_has_no_tile_collisions():
    planner = LocalLayoutPlanner()
    # Two ingredients, several feed points per side -> a busy west region.
    plan = planner.generate_line_layout("electronic-circuit", 6, 0, 0, feed_style="sideload", belt_type="turbo-transport-belt")

    seen: dict = {}
    for action in _all_actions(plan):
        if action["action_type"] == "remove_entity":
            continue
        if action["entity"] in TWO_BY_TWO:
            continue
        for tile in _tiles_for(action):
            assert tile not in seen, (
                f"tile collision at {tile}: {seen.get(tile)} vs {action['entity']}"
            )
            seen[tile] = action["entity"]


def test_sideload_feeder_belts_are_adjacent_to_input_belt():
    planner = LocalLayoutPlanner()
    plan = planner.generate_line_layout("electronic-circuit", 6, 0, 0, feed_style="sideload", belt_type="turbo-transport-belt")

    belts = [
        a for a in _all_actions(plan)
        if a["action_type"] == "place_ghost" and a["entity"].endswith("transport-belt")
    ]
    input_tiles = {
        (math.floor(b["position"]["x"]), math.floor(b["position"]["y"]))
        for b in belts
        if b.get("direction") == "east" and math.floor(b["position"]["y"]) == 0
    }
    assert input_tiles, "expected an eastbound input belt at row 0"

    # North feeder runs south; its junction tile's south neighbour is input belt.
    south_belts = [b for b in belts if b.get("direction") == "south"]
    assert south_belts, "expected a north-side feeder belt running south"
    assert any(
        (math.floor(b["position"]["x"]), math.floor(b["position"]["y"]) + 1) in input_tiles
        for b in south_belts
    ), "north feeder belt does not sideload the input belt"

    # South feeder runs north; its junction tile's north neighbour is input belt.
    north_belts = [b for b in belts if b.get("direction") == "north"]
    assert north_belts, "expected a south-side feeder belt running north"
    assert any(
        (math.floor(b["position"]["x"]), math.floor(b["position"]["y"]) - 1) in input_tiles
        for b in north_belts
    ), "south feeder belt does not sideload the input belt"

    # Feeder belt columns sit strictly west of x=0 (clear of machines/inserters).
    assert all(b["position"]["x"] < 0 for b in south_belts + north_belts)


def test_sideload_feeder_counts_match_demand():
    planner = LocalLayoutPlanner()
    plan = planner.generate_line_layout("electronic-circuit", 6, 0, 0, feed_style="sideload", belt_type="turbo-transport-belt")
    actions = _all_actions(plan)
    # Demand with 25% headroom: 27*1.25=33.75 cables/s -> 9 fast feeders,
    # 9*1.25=11.25 plates/s -> 3.
    cable_chests = [a for a in actions if a.get("infinity_filter") == "copper-cable"]
    plate_chests = [a for a in actions if a.get("infinity_filter") == "iron-plate"]
    assert len(cable_chests) == 9
    assert len(plate_chests) == 3
    # Every loading inserter faces west (picks from chest, drops east onto belt).
    loaders = [
        a for a in actions
        if a["action_type"] == "place_entity" and a["entity"] == "fast-inserter"
        and a["position"]["x"] < 0
    ]
    assert loaders and all(a["direction"] == "west" for a in loaders)


def test_sideload_single_ingredient_has_only_north_feeder():
    planner = LocalLayoutPlanner()
    plan = planner.generate_line_layout("iron-gear-wheel", 4, 0, 0, feed_style="sideload", belt_type="express-transport-belt")
    belts = [
        a for a in _all_actions(plan)
        if a["action_type"] == "place_ghost" and a["entity"].endswith("transport-belt")
    ]
    assert any(b.get("direction") == "south" for b in belts)   # north feeder present
    assert not any(b.get("direction") == "north" for b in belts)  # no south feeder


def test_sideload_uses_belt_and_inserter_tiers():
    planner = LocalLayoutPlanner()
    plan = planner.generate_line_layout(
        "electronic-circuit", 4, 0, 0, feed_style="sideload",
        belt_type="express-transport-belt", inserter_type="stack-inserter",
    )
    actions = _all_actions(plan)
    feeder_belts = [
        a for a in actions
        if a["action_type"] == "place_ghost" and a["entity"] == "express-transport-belt"
        and a.get("direction") in {"north", "south"}
    ]
    assert feeder_belts  # feeder belt columns exist and use the belt tier
    loaders = [
        a for a in actions
        if a["action_type"] == "place_entity" and a["position"]["x"] < 0
        and a["entity"] in {"stack-inserter"}
    ]
    assert loaders  # loading inserters honour inserter_type


# --------------------------------------------------------------------------- #
# Chain link
# --------------------------------------------------------------------------- #

def _consumer_x(planner, producer_x, producer_machines, consumer_recipe,
                consumer_machines, consumer_feed_style="chest", inserter_type="fast-inserter"):
    belt_west = planner._belt_west(
        consumer_recipe, consumer_machines, consumer_feed_style, inserter_type, False
    )
    turn_col = producer_x + producer_machines * 3 + 1
    return turn_col + 1 - belt_west


def test_chain_link_is_deterministic():
    planner = LocalLayoutPlanner()
    cx = _consumer_x(planner, 0, 4, "iron-gear-wheel", 4)
    a = planner.generate_chain_link((0, 0), "iron-gear-wheel", 4, (cx, 20), "iron-gear-wheel", 4)
    b = planner.generate_chain_link((0, 0), "iron-gear-wheel", 4, (cx, 20), "iron-gear-wheel", 4)
    assert a == b


def test_chain_link_connector_is_contiguous_path():
    planner = LocalLayoutPlanner()
    px, py, producer_machines = 0, 0, 4
    cy = py + 20
    cx = _consumer_x(planner, px, producer_machines, "iron-gear-wheel", 4)
    plan = planner.generate_chain_link(
        (px, py), "iron-gear-wheel", producer_machines, (cx, cy), "iron-gear-wheel", 4
    )

    belt_tiles = {
        (math.floor(a["position"]["x"]), math.floor(a["position"]["y"]))
        for a in _all_actions(plan)
        if a["action_type"] == "place_ghost"
    }
    out_row = py + 6
    in_row = cy

    # The path must touch both the producer output row and the consumer input row.
    assert any(y == out_row for (_, y) in belt_tiles)
    assert any(y == in_row for (_, y) in belt_tiles)

    # Walk the tiles: the whole connector must be one 4-connected component,
    # reachable from the tile that leaves the producer's output belt.
    start = (px + producer_machines * 3, out_row)
    assert start in belt_tiles
    reached = {start}
    frontier = [start]
    while frontier:
        x, y = frontier.pop()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (nx, ny) in belt_tiles and (nx, ny) not in reached:
                reached.add((nx, ny))
                frontier.append((nx, ny))
    assert reached == belt_tiles, "connector belts are not a single contiguous path"


def test_chain_link_lands_on_consumer_input_west_extension():
    planner = LocalLayoutPlanner()
    px, producer_machines = 0, 4
    cx = _consumer_x(planner, px, producer_machines, "electronic-circuit", 6)
    plan = planner.generate_chain_link(
        (px, 0), "iron-gear-wheel", producer_machines, (cx, 20), "electronic-circuit", 6
    )
    turn_col = px + producer_machines * 3 + 1
    consumer_belt_west = planner._belt_west("electronic-circuit", 6, "chest", "fast-inserter", False)
    # The connector corners east one tile west of the consumer input belt west end.
    assert cx + consumer_belt_west == turn_col + 1
    # The final connector tile is the east-cornering tile at the consumer input row.
    corner = [
        a for a in _all_actions(plan)
        if a["action_type"] == "place_ghost" and a.get("direction") == "east"
        and math.floor(a["position"]["y"]) == 20
    ]
    assert len(corner) == 1
    assert math.floor(corner[0]["position"]["x"]) == turn_col


def test_chain_link_removes_producer_collector():
    planner = LocalLayoutPlanner()
    cx = _consumer_x(planner, 0, 4, "iron-gear-wheel", 4)
    plan = planner.generate_chain_link((0, 0), "iron-gear-wheel", 4, (cx, 20), "iron-gear-wheel", 4)
    removes = [a for a in _all_actions(plan) if a["action_type"] == "remove_entity"]
    entities = {a["entity"] for a in removes}
    assert entities == {"fast-inserter", "steel-chest"}


def test_chain_link_rejects_shallow_consumer():
    planner = LocalLayoutPlanner()
    cx = _consumer_x(planner, 0, 4, "iron-gear-wheel", 4)
    with pytest.raises(ValueError):
        # cy below producer_y + LINE_PITCH_Y is illegal.
        planner.generate_chain_link((0, 0), "iron-gear-wheel", 4, (cx, 10), "iron-gear-wheel", 4)


def test_chain_link_rejects_misplaced_consumer_x():
    planner = LocalLayoutPlanner()
    cx = _consumer_x(planner, 0, 4, "iron-gear-wheel", 4)
    with pytest.raises(ValueError):
        planner.generate_chain_link((0, 0), "iron-gear-wheel", 4, (cx + 1, 20), "iron-gear-wheel", 4)


# --------------------------------------------------------------------------- #
# Schema + invariant guards
# --------------------------------------------------------------------------- #

def test_generated_plans_pass_schema():
    # generate_* methods validate internally; returning without raising proves it.
    planner = LocalLayoutPlanner()
    assert planner.generate_line_layout("electronic-circuit", 6, 0, 0, feed_style="sideload", belt_type="turbo-transport-belt")
    cx = _consumer_x(planner, 0, 4, "iron-gear-wheel", 4)
    assert planner.generate_chain_link((0, 0), "iron-gear-wheel", 4, (cx, 20), "iron-gear-wheel", 4)


def test_fuel_guard_fires_on_doctored_plan():
    planner = LocalLayoutPlanner()
    plan = planner.generate_line_layout("electronic-circuit", 4, 0, 0, feed_style="sideload", belt_type="express-transport-belt")
    plan["phases"][0]["actions"].append(
        {"action_type": "place_entity", "entity": "stone-furnace", "position": {"x": 0, "y": 0}}
    )
    with pytest.raises(ValueError, match="Electric-only invariant"):
        _reject_fuel_entities(plan)


def test_sideload_constants_are_west_of_origin():
    assert SIDELOAD_NORTH_COL < 0
    assert SIDELOAD_SOUTH_COL < 0


# --- chained feeds, lane junctions, and lab rows (science chain wave) ---

def _all_actions(plan):
    return [a for phase in plan["phases"] for a in phase["actions"]]


def _tiles_of(actions, entity_names, footprint=1):
    """Occupied tiles for the named entities (footprint 1 or 3 per side)."""
    tiles = set()
    for action in actions:
        if action["entity"] not in entity_names:
            continue
        px, py = action["position"]["x"], action["position"]["y"]
        if footprint == 1:
            tiles.add((px, py))
        else:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    tiles.add((px + dx, py + dy))
    return tiles


def test_chained_line_emits_no_feeders_but_keeps_belt_extension():
    from planners.local_layout_planner import CHAINED_BELT_WEST

    planner = LocalLayoutPlanner()
    plan = planner.generate_line_layout(
        "automation-science-pack", 6, 0, 0, feed_style="chained",
        belt_type="express-transport-belt", inserter_type="stack-inserter",
    )
    actions = _all_actions(plan)
    assert not [a for a in actions if a["entity"] == "infinity-chest"]
    input_belt_x = {a["position"]["x"] for a in actions
                    if a["entity"] == "express-transport-belt" and a["position"]["y"] == 0.5}
    assert min(input_belt_x) == CHAINED_BELT_WEST + 0.5


def test_chained_line_keeps_feeders_for_unchained_ingredient():
    planner = LocalLayoutPlanner()
    # Only ingredient 1 (iron-gear-wheel) arrives by chain; copper stays local.
    plan = planner.generate_line_layout(
        "automation-science-pack", 6, 0, 0, feed_style="chained", chained_ingredients={1},
        belt_type="express-transport-belt", inserter_type="stack-inserter",
    )
    filters = {a.get("infinity_filter") for a in _all_actions(plan) if a["entity"] == "infinity-chest"}
    assert filters == {"copper-plate"}


def test_chained_line_rejects_bad_ingredient_indices():
    planner = LocalLayoutPlanner()
    with pytest.raises(ValueError):
        planner.generate_line_layout("iron-gear-wheel", 4, 0, 0, feed_style="chained",
                                     chained_ingredients={5})
    with pytest.raises(ValueError):
        planner.generate_line_layout("iron-gear-wheel", 4, 0, 0, feed_style="chained",
                                     chained_ingredients=set())


def test_two_producers_enter_consumer_on_opposite_lanes():
    from planners.local_layout_planner import (
        CHAIN_NORTH_JUNCTION_COL, CHAIN_SOUTH_APPROACH_COL, CHAIN_SOUTH_JUNCTION_COL,
    )

    planner = LocalLayoutPlanner()
    turn_col = 0 + 6 * 3 + 1
    kwargs = dict(belt_type="express-transport-belt", inserter_type="stack-inserter",
                  consumer_feed_style="chained")

    north_cx = turn_col - CHAIN_NORTH_JUNCTION_COL
    north = planner.generate_chain_link((0, 0), "copper-plate", 6, (north_cx, 16),
                                        "automation-science-pack", 6, junction_side="north", **kwargs)
    last = [a for a in north["phases"][0]["actions"] if a["action_type"] == "place_ghost"][-1]
    # Final tile sits one row ABOVE the consumer input belt, pushing south into it.
    assert last["direction"] == "south"
    assert last["position"] == {"x": north_cx + CHAIN_NORTH_JUNCTION_COL + 0.5, "y": 15.5}

    south_cx = turn_col - CHAIN_SOUTH_APPROACH_COL
    south = planner.generate_chain_link((0, 0), "iron-gear-wheel", 6, (south_cx, 16),
                                        "automation-science-pack", 6, junction_side="south", **kwargs)
    last = [a for a in south["phases"][0]["actions"] if a["action_type"] == "place_ghost"][-1]
    # Final tile sits one row BELOW, pushing north into the other lane.
    assert last["direction"] == "north"
    assert last["position"] == {"x": south_cx + CHAIN_SOUTH_JUNCTION_COL + 0.5, "y": 17.5}


def test_chain_link_rejects_mismatched_consumer_x_per_side():
    planner = LocalLayoutPlanner()
    with pytest.raises(ValueError, match="north"):
        planner.generate_chain_link((0, 0), "copper-plate", 6, (999, 16),
                                    "automation-science-pack", 6, junction_side="north",
                                    consumer_feed_style="chained")
    with pytest.raises(ValueError, match="Unknown junction side"):
        planner.generate_chain_link((0, 0), "copper-plate", 6, (20, 16),
                                    "automation-science-pack", 6, junction_side="sideways")


def test_south_connector_clears_consumer_input_belt_column():
    from planners.local_layout_planner import CHAIN_SOUTH_APPROACH_COL, CHAINED_BELT_WEST

    planner = LocalLayoutPlanner()
    turn_col = 0 + 6 * 3 + 1
    cx = turn_col - CHAIN_SOUTH_APPROACH_COL
    link = planner.generate_chain_link((0, 0), "iron-gear-wheel", 6, (cx, 16),
                                       "automation-science-pack", 6, junction_side="south",
                                       consumer_feed_style="chained")
    consumer = planner.generate_line_layout("automation-science-pack", 6, cx, 16,
                                            feed_style="chained")
    link_tiles = _tiles_of(link["phases"][0]["actions"], {"transport-belt"})
    consumer_small = _tiles_of(_all_actions(consumer),
                               {"transport-belt", "fast-inserter", "infinity-chest", "steel-chest",
                                "medium-electric-pole"})
    assert not (link_tiles & consumer_small)
    # The descending column passes west of the consumer's westmost belt tile.
    assert cx + CHAIN_SOUTH_APPROACH_COL < cx + CHAINED_BELT_WEST


def test_lab_row_geometry_and_determinism():
    planner = LocalLayoutPlanner()
    plan = planner.generate_lab_row(6, 0, 32, belt_type="express-transport-belt",
                                    inserter_type="stack-inserter")
    assert plan == planner.generate_lab_row(6, 0, 32, belt_type="express-transport-belt",
                                            inserter_type="stack-inserter")
    actions = _all_actions(plan)
    labs = [a for a in actions if a["entity"] == "lab"]
    assert len(labs) == 6
    assert all("recipe" not in lab for lab in labs)

    # One inserter per lab, all feeding south into their lab.
    inserters = [a for a in actions if a["entity"] == "stack-inserter"]
    assert len(inserters) == 6
    assert all(i["direction"] == "north" for i in inserters)

    # Input belt only: no output row, no collectors.
    belt_rows = {a["position"]["y"] for a in actions if a["entity"] == "express-transport-belt"}
    assert belt_rows == {32.5}
    assert not [a for a in actions if a["entity"] == "steel-chest"]

    # No tile collisions between 1x1 entities and the 3x3 labs.
    small = _tiles_of(actions, {"express-transport-belt", "stack-inserter", "medium-electric-pole"})
    lab_tiles = _tiles_of(actions, {"lab"}, footprint=3)
    assert not (small & lab_tiles)


def test_lab_row_input_belt_reaches_chain_junctions():
    from planners.local_layout_planner import CHAINED_BELT_WEST, CHAIN_SOUTH_JUNCTION_COL

    planner = LocalLayoutPlanner()
    plan = planner.generate_lab_row(4, 100, 60)
    belt_x = {a["position"]["x"] for a in _all_actions(plan) if a["entity"] == "transport-belt"}
    assert min(belt_x) == 100 + CHAINED_BELT_WEST + 0.5
    # Both junction columns exist on the belt so either side can feed labs.
    assert 100 + CHAIN_SOUTH_JUNCTION_COL + 0.5 in belt_x


def test_substation_reaches_first_pole_in_every_feed_style():
    """A substation further than a medium pole's wire reach silently leaves the
    whole line unpowered - found live when chained lines all read no_power."""
    from planners.local_layout_planner import LocalLayoutPlanner, MEDIUM_POLE_WIRE_REACH

    planner = LocalLayoutPlanner()
    cases = [
        planner.generate_line_layout("iron-gear-wheel", 4, 0, 0, feed_style="chest"),
        planner.generate_line_layout("iron-gear-wheel", 4, 0, 0, feed_style="sideload",
                                     belt_type="express-transport-belt"),
        planner.generate_line_layout("automation-science-pack", 4, 0, 0, feed_style="chained"),
        planner.generate_line_layout("iron-plate", 4, 0, 0, mining_feed=True),
        planner.generate_lab_row(4, 0, 0),
    ]
    for plan in cases:
        actions = _all_actions(plan)
        subs = [a for a in actions if a["entity"] == "substation"]
        poles = [a for a in actions if a["entity"] == "medium-electric-pole"]
        assert subs and poles
        first_pole = min(poles, key=lambda p: p["position"]["x"])
        best = min(
            max(abs(s["position"]["x"] - first_pole["position"]["x"]),
                abs(s["position"]["y"] - first_pole["position"]["y"]))
            for s in subs
        )
        assert best <= MEDIUM_POLE_WIRE_REACH, (
            f"substation {best} tiles from first pole exceeds medium pole reach"
        )


def test_connector_never_stacks_two_belts_on_one_tile():
    """A descent that runs through the junction row leaves a south-facing belt
    where the east corner must go - the connector then dead-ends (found live:
    every connector tile full, consumer belt empty)."""
    from planners.local_layout_planner import (
        CHAIN_NORTH_JUNCTION_COL, CHAIN_SOUTH_APPROACH_COL, CHAINED_BELT_WEST,
    )

    planner = LocalLayoutPlanner()
    turn_col = 0 + 6 * 3 + 1
    cases = [
        ("head_on", turn_col + 1 - CHAINED_BELT_WEST),
        ("north", turn_col - CHAIN_NORTH_JUNCTION_COL),
        ("south", turn_col - CHAIN_SOUTH_APPROACH_COL),
    ]
    for side, cx in cases:
        plan = planner.generate_chain_link(
            (0, 0), "iron-plate", 6, (cx, 16), "automation-science-pack", 6,
            junction_side=side, consumer_feed_style="chained",
        )
        positions = [(a["position"]["x"], a["position"]["y"])
                     for a in plan["phases"][0]["actions"] if a["action_type"] == "place_ghost"]
        assert len(positions) == len(set(positions)), f"{side}: duplicate belt tile"


# --------------------------------------------------------------------------- #
# In-place line growth (the "extend_line_x" catalog action)
# --------------------------------------------------------------------------- #

import json  # noqa: E402  (kept with the extension block it serves)

from planners.local_layout_planner import MACHINE_WIDTH  # noqa: E402


def _key(action: dict) -> str:
    return json.dumps(action, sort_keys=True)


def _placements(plan: dict) -> set:
    return {_key(a) for a in _all_actions(plan)}


def _removals(plan: dict) -> set:
    return {
        _key({"entity": a["entity"], "position": a["position"]})
        for a in _all_actions(plan) if a["action_type"] == "remove_entity"
    }


def _additions(plan: dict) -> set:
    return {_key(a) for a in _all_actions(plan) if a["action_type"] != "remove_entity"}


def _terminal_pair(machine_count, origin_x=0, origin_y=0):
    length = machine_count * MACHINE_WIDTH
    return [{"x": origin_x + length + 0.5, "y": origin_y + 6.5},
            {"x": origin_x + length + 1.5, "y": origin_y + 6.5}]


def _strip_terminal(plan: dict, machine_count, inserter_type="fast-inserter",
                    origin_x=0, origin_y=0) -> list:
    pair = _terminal_pair(machine_count, origin_x, origin_y)
    return [
        a for a in _all_actions(plan)
        if not (a["action_type"] == "place_entity"
                and a["entity"] in {inserter_type, "steel-chest"}
                and a["position"] in pair)
    ]


def test_extension_is_exactly_the_difference_of_the_two_full_layouts():
    planner = LocalLayoutPlanner()
    small = planner.generate_line_layout("iron-gear-wheel", 4, 0, 0)
    large = planner.generate_line_layout("iron-gear-wheel", 6, 0, 0)
    ext = planner.generate_line_extension("iron-gear-wheel", 4, 6)

    small_keys, large_keys = _placements(small), _placements(large)
    # Everything the extension places is in the big layout and not in the small
    # one; everything it removes is in the small layout and not in the big one.
    assert _additions(ext) == large_keys - small_keys
    removed = {
        _key({"entity": json.loads(k)["entity"], "position": json.loads(k)["position"]})
        for k in small_keys - large_keys
    }
    assert _removals(ext) == removed

    # Exactly the two new machines, with their input and output inserters.
    machines = [a for a in _all_actions(ext) if a["entity"] == "assembling-machine-2"]
    assert len(machines) == 2
    assert {a["position"]["x"] for a in machines} == {13.5, 16.5}
    assert all(a["recipe"] == "iron-gear-wheel" for a in machines)


def test_extension_never_duplicates_existing_poles_or_belts():
    planner = LocalLayoutPlanner()
    small = planner.generate_line_layout("iron-gear-wheel", 4, 0, 0)
    ext = planner.generate_line_extension("iron-gear-wheel", 4, 6)

    reused = {"transport-belt", "medium-electric-pole"}
    old_tiles = _tiles_of([a for a in _all_actions(small) if a["entity"] in reused], reused)
    new_tiles = _tiles_of(
        [a for a in _all_actions(ext)
         if a["action_type"] == "place_ghost" and a["entity"] in reused], reused
    )
    assert new_tiles, "extension must lay new belt/pole tiles"
    assert not (old_tiles & new_tiles), "extension re-places already-built belts/poles"

    # The x=6j pole pattern continues rather than restarting: length 12 already
    # carried poles at 0/6/12, so only x=18 is new (on both pole rows).
    pole_x = {a["position"]["x"] for a in _all_actions(ext)
              if a["entity"] == "medium-electric-pole"}
    assert pole_x == {18.5}


def test_extension_moves_the_terminal_collector():
    planner = LocalLayoutPlanner()
    ext = planner.generate_line_extension("iron-gear-wheel", 4, 6)
    actions = _all_actions(ext)

    old_pair = _terminal_pair(4)
    new_pair = _terminal_pair(6)
    removed = {(a["entity"], a["position"]["x"], a["position"]["y"])
               for a in actions if a["action_type"] == "remove_entity"}
    placed = {(a["entity"], a["position"]["x"], a["position"]["y"])
              for a in actions if a["action_type"] == "place_entity"}
    assert ("fast-inserter", old_pair[0]["x"], old_pair[0]["y"]) in removed
    assert ("steel-chest", old_pair[1]["x"], old_pair[1]["y"]) in removed
    assert ("fast-inserter", new_pair[0]["x"], new_pair[0]["y"]) in placed
    assert ("steel-chest", new_pair[1]["x"], new_pair[1]["y"]) in placed

    # Reclaim runs first: the new output belt lands on the old inserter's tile.
    assert ext["phases"][0]["name"] == "extension_reclaim"
    assert any(a["entity"] == "transport-belt" and a["position"] == {"x": 12.5, "y": 6.5}
               for a in actions)


def test_extension_skips_collector_move_when_line_is_chained_onward():
    planner = LocalLayoutPlanner()
    ext = planner.generate_line_extension("iron-gear-wheel", 4, 6, has_terminal_collector=False)
    touched = {(a["position"]["x"], a["position"]["y"]) for a in _all_actions(ext)
               if a["entity"] in {"fast-inserter", "steel-chest"}
               and a["position"]["y"] == 6.5}
    assert not touched, "chained-onward line must not touch the terminal collector"

    # The rest of the delta is unchanged apart from that pair.
    with_collector = planner.generate_line_extension("iron-gear-wheel", 4, 6)
    dropped = _placements(with_collector) - _placements(ext)
    assert len(dropped) == 4  # 2 removes + 2 places, all on row 6.5
    assert planner.line_extension_cost(
        "iron-gear-wheel", 4, 6, has_terminal_collector=False)["moves_collector"] is False

    # Both variants still describe the same larger line where it matters.
    large = planner.generate_line_layout("iron-gear-wheel", 6, 0, 0)
    small = planner.generate_line_layout("iron-gear-wheel", 4, 0, 0)
    expected = {_key(a) for a in _strip_terminal(large, 6)} - {_key(a) for a in _strip_terminal(small, 4)}
    assert _additions(ext) == expected


def test_extension_adds_feed_points_only_across_a_feeder_boundary():
    planner = LocalLayoutPlanner()
    # iron-gear-wheel needs 2 plates/craft: 4 machines -> 4 feeders,
    # 6 machines -> 6, so growth crosses two boundaries.
    grow = planner.line_extension_cost("iron-gear-wheel", 4, 6)
    assert grow["adds_feeders"] == 2
    assert grow["materials"]["infinity-chest"] == 2

    # iron-plate smelting is slow: 4 and 5 furnaces both fit inside one feeder
    # (0.78 and 0.98 feed points with headroom), so nothing is added.
    flat = planner.line_extension_cost("iron-plate", 4, 5)
    assert flat["adds_feeders"] == 0
    assert "infinity-chest" not in flat["materials"]
    ext = planner.generate_line_extension("iron-plate", 4, 5)
    assert not [a for a in _all_actions(ext) if a["entity"] == "infinity-chest"]


def test_extension_adds_mining_drills_when_mining_fed():
    planner = LocalLayoutPlanner()
    ext = planner.generate_line_extension("iron-plate", 4, 6, mining_feed=True)
    drills = [a for a in _all_actions(ext) if a["entity"] == "electric-mining-drill"]
    assert len(drills) == 2
    assert {a["position"]["x"] for a in drills} == {13.5, 16.5}
    assert all(a["direction"] == "south" for a in drills)
    # Mining-fed lines have no chest feeders at either size.
    assert not [a for a in _all_actions(ext) if a["entity"] == "infinity-chest"]


def test_extension_rejects_shrinking_or_flat_growth():
    planner = LocalLayoutPlanner()
    with pytest.raises(ValueError):
        planner.generate_line_extension("iron-gear-wheel", 6, 4)
    with pytest.raises(ValueError):
        planner.generate_line_extension("iron-gear-wheel", 4, 4)
    with pytest.raises(ValueError):
        planner.generate_line_extension("iron-gear-wheel", 0, 4)


def test_extension_is_deterministic():
    planner = LocalLayoutPlanner()
    kwargs = dict(origin_x=40, origin_y=16, belt_type="express-transport-belt",
                  inserter_type="stack-inserter", feed_style="chained")
    a = planner.generate_line_extension("automation-science-pack", 4, 8, **kwargs)
    b = planner.generate_line_extension("automation-science-pack", 4, 8, **kwargs)
    assert a == b
    assert (planner.line_extension_cost("automation-science-pack", 4, 8, **kwargs)
            == planner.line_extension_cost("automation-science-pack", 4, 8, **kwargs))


def test_extension_plan_passes_build_plan_schema():
    from jsonschema import Draft7Validator

    schema_path = REPO_ROOT / "schemas" / "build_plan.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    planner = LocalLayoutPlanner()
    cases = [
        planner.generate_line_extension("iron-gear-wheel", 4, 6),
        planner.generate_line_extension("iron-plate", 4, 6, mining_feed=True),
        planner.generate_line_extension("electronic-circuit", 4, 6, feed_style="sideload",
                                        belt_type="turbo-transport-belt"),
        planner.generate_line_extension("automation-science-pack", 4, 6, feed_style="chained",
                                        has_terminal_collector=False),
    ]
    for plan in cases:
        assert not list(Draft7Validator(schema).iter_errors(plan))
        assert plan["phases"] and all(p["actions"] for p in plan["phases"])
        _reject_fuel_entities(plan)


def test_extension_cost_reports_materials_moves_and_feeders():
    planner = LocalLayoutPlanner()
    cost = planner.line_extension_cost("iron-gear-wheel", 4, 6)
    assert set(cost) == {"materials", "moves_collector", "adds_feeders"}
    assert cost["moves_collector"] is True

    ext = planner.generate_line_extension("iron-gear-wheel", 4, 6)
    ghosts = planner.material_requirements(ext)
    assert cost["materials"]["assembling-machine-2"] == ghosts["assembling-machine-2"] == 2
    assert cost["materials"]["transport-belt"] == ghosts["transport-belt"]
    # The relocated collector is reclaimed, so its inserter+chest net to zero;
    # only the extra drain collector shows up as a new steel chest.
    assert cost["materials"]["steel-chest"] == 1
    assert all(count > 0 for count in cost["materials"].values())
