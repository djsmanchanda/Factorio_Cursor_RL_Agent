# Path: tests/test_fluid_routing.py
# Purpose: Verify the bounded, deterministic automatic fluid-chain router.

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from planners.fluid_routing import (
    MAX_TERRAIN_TUNNEL_TILES,
    generate_shortest_fluid_chain_link,
    shortest_fluid_chain_tiles,
)


def _tiles(plan: dict) -> set[tuple[int, int]]:
    return {
        (int(action["position"]["x"] - 0.5), int(action["position"]["y"] - 0.5))
        for action in plan["phases"][0]["actions"]
    }


def test_shortest_fluid_router_is_deterministic_and_shares_a_compact_tree():
    args = ((0, 0), [(8, 0), (8, 2)])
    first = shortest_fluid_chain_tiles(*args)
    assert first == shortest_fluid_chain_tiles(*args)
    assert {(x, 0) for x in range(9)} <= set(first)
    assert {(8, 1), (8, 2)} <= set(first)
    assert len(first) == 11


def test_shortest_fluid_router_preserves_fixed_clearance_from_hard_tiles():
    hard = {(4, 0)}
    tiles = set(shortest_fluid_chain_tiles((0, 0), [(8, 0)], hard_tiles=hard))
    assert (4, 0) not in tiles
    assert not any(max(abs(x - 4), abs(y)) <= 1 for x, y in tiles)
    assert len(tiles) == 13

def test_shortest_fluid_router_treats_tunnelable_tiles_conservatively():
    tiles = set(shortest_fluid_chain_tiles((0, 0), [(8, 0)], tunnelable_tiles={(4, 0)}))
    assert (4, 0) not in tiles
    assert any(max(abs(x - 4), abs(y)) == 1 for x, y in tiles)


def test_narrow_water_uses_paired_underground_pipes() -> None:
    water = {(x, 0) for x in range(4, 4 + MAX_TERRAIN_TUNNEL_TILES)}

    plan = generate_shortest_fluid_chain_link(
        (0, 0), [(16, 0)], "water", tunnelable_tiles=water,
        clearance=0, search_margin=0, allow_terrain_tunnels=True,
    )

    underground = [
        action for action in plan["phases"][0]["actions"]
        if action["entity"] == "pipe-to-ground"
    ]
    assert len(underground) == 2
    directions = {
        (int(action["position"]["x"] - 0.5), int(action["position"]["y"] - 0.5)): action["direction"]
        for action in underground
    }
    assert directions == {(3, 0): "west", (13, 0): "east"}


def test_wide_water_uses_landfill_between_underground_spans() -> None:
    water = {(x, 0) for x in range(4, 46)}

    plan = generate_shortest_fluid_chain_link(
        (0, 0), [(50, 0)], "water", tunnelable_tiles=water,
        clearance=0, search_margin=0, allow_terrain_tunnels=True,
    )

    landfills = {
        (action["position"]["x"], action["position"]["y"])
        for action in plan["phases"][0]["actions"]
        if action["action_type"] == "place_tile_ghost"
    }
    underground = {
        (int(action["position"]["x"] - 0.5), int(action["position"]["y"] - 0.5))
        for action in plan["phases"][0]["actions"]
        if action.get("entity") == "pipe-to-ground"
    }
    surface_pipes = {
        (int(action["position"]["x"] - 0.5), int(action["position"]["y"] - 0.5))
        for action in plan["phases"][0]["actions"]
        if action.get("entity") == "pipe"
    }

    assert landfills == {(13, 0), (14, 0), (24, 0), (25, 0), (35, 0), (36, 0)}
    assert landfills <= underground
    assert not (surface_pipes & water)
    assert len(underground) == 8
    directions = {
        (int(action["position"]["x"] - 0.5), int(action["position"]["y"] - 0.5)): action["direction"]
        for action in plan["phases"][0]["actions"]
        if action.get("entity") == "pipe-to-ground"
    }
    assert directions == {
        (3, 0): "west", (13, 0): "east", (14, 0): "west", (24, 0): "east",
        (25, 0): "west", (35, 0): "east", (36, 0): "west", (46, 0): "east",
    }


def test_water_runs_with_only_one_land_tile_between_are_refused() -> None:
    water = {(x, 0) for x in range(4, 9)} | {(x, 0) for x in range(10, 15)}

    with pytest.raises(ValueError, match="two clear surface tiles"):
        generate_shortest_fluid_chain_link(
            (0, 0), [(18, 0)], "water", tunnelable_tiles=water,
            clearance=0, search_margin=0, allow_terrain_tunnels=True,
        )


def test_shortest_fluid_router_has_a_deterministic_search_bound():
    barrier = {(1, y) for y in range(-1, 2)}
    with pytest.raises(ValueError, match="bounded search area"):
        shortest_fluid_chain_tiles((0, 0), [(2, 0)], hard_tiles=barrier,
                                  clearance=0, search_margin=1)


def test_automatic_fluid_chain_plan_avoids_foreign_fluid_without_tunnelling():
    foreign = [{"fluid": "petroleum-gas", "separated_by_pump": False,
                "tiles": [(4, 0)]}]
    plan = generate_shortest_fluid_chain_link((0, 0), [(8, 0)], "water", foreign)
    assert {action["entity"] for action in plan["phases"][0]["actions"]} == {"pipe"}
    assert (4, 0) not in _tiles(plan)
