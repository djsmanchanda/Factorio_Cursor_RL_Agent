# Path: tests/test_line_chaining.py
# Purpose: Deterministic tests for sideload feeding and producer->consumer chain links in LocalLayoutPlanner.

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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
