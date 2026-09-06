# Path: tests/test_fluid_layout_search.py
# Purpose: Verify rotated, mirrored, obstacle-scored fluid layouts and compact growth.

from __future__ import annotations

import math
from collections import Counter

import pytest

from planners.fluid_layout_search import (
    CARDINALS,
    MIRRORS,
    compact_paired_fluid_block,
    fluid_block_candidates,
    oriented_compact_paired_fluid_block,
    oriented_fluid_machine_row,
    oriented_fluid_network_segments,
    oriented_fluid_row_candidates,
    recover_oriented_fluid_row,
)
from planners.fluid_layouts import generate_fluid_machine_row
from planners.infrastructure import POLE_SPECS
from planners.plan_validation import actions, occupied_tile_indices
from orchestrator.stage_transport import _direct_single_belt_feed


@pytest.mark.parametrize("direction", CARDINALS)
@pytest.mark.parametrize("mirror", MIRRORS)
@pytest.mark.parametrize("recipe", ["basic-oil-processing", "plastic-bar", "sulfur"])
def test_every_fluid_row_symmetry_preserves_verified_networks(
    recipe: str, direction: str, mirror: str | None,
) -> None:
    plan = oriented_fluid_machine_row(
        recipe, 3, (17.0, -9.0), direction=direction, mirror=mirror,
    )
    pipe_tiles = {
        (math.floor(action["position"]["x"]), math.floor(action["position"]["y"]))
        for action in actions(plan)
        if action.get("entity") in {"pipe", "pipe-to-ground"}
    }
    network_tiles = {
        tuple(tile)
        for segment in oriented_fluid_network_segments(
            recipe, 3, (17.0, -9.0), direction=direction, mirror=mirror,
        )
        for tile in segment["tiles"]
    }

    assert pipe_tiles == network_tiles


def test_layout_search_uses_rotation_and_mirror_to_shorten_external_routes() -> None:
    candidates = oriented_fluid_row_candidates(
        "basic-oil-processing", 4, (0.0, 0.0),
        {"crude-oil": [(-20.0, 0.0)], "petroleum-gas": [(20.0, 0.0)]},
    )

    assert len(candidates) == 8
    assert candidates[0].direction == "east"
    assert candidates[0].mirror == "horizontal"
    assert candidates[0].pipe_tiles < next(
        candidate.pipe_tiles for candidate in candidates
        if candidate.direction == "west" and candidate.mirror == "horizontal"
    )


def test_layout_search_rejects_a_blocked_candidate_and_uses_an_alternative() -> None:
    unblocked = oriented_fluid_row_candidates(
        "plastic-bar", 2, (0.0, 0.0),
        {"petroleum-gas": [(-15.0, 0.0)]},
    )
    footprints = [
        occupied_tile_indices([("candidate", candidate.plan)])
        for candidate in unblocked
    ]
    winner_unique = min(
        footprints[0],
        key=lambda tile: sum(tile in footprint for footprint in footprints),
    )
    blocked = {winner_unique}
    alternatives = oriented_fluid_row_candidates(
        "plastic-bar", 2, (0.0, 0.0),
        {"petroleum-gas": [(-15.0, 0.0)]}, blocked_tiles=blocked,
    )

    assert alternatives
    assert alternatives[0].score != unblocked[0].score
    assert occupied_tile_indices([
        ("alternative", alternatives[0].plan),
    ]).isdisjoint(blocked)


def test_live_row_recovery_uses_pipes_to_break_mirror_symmetry() -> None:
    origin = (31.0, -17.0)
    plan = oriented_fluid_machine_row(
        "basic-oil-processing", 4, origin,
        direction="east", mirror="horizontal",
    )
    machines = [
        (action["position"]["x"], action["position"]["y"])
        for action in actions(plan) if action.get("entity") == "oil-refinery"
    ]
    pipe_tiles = {
        (math.floor(action["position"]["x"]), math.floor(action["position"]["y"]))
        for action in actions(plan)
        if action.get("entity") in {"pipe", "pipe-to-ground"}
    }

    assert recover_oriented_fluid_row(
        "basic-oil-processing", machines, pipe_tiles,
    ) == (origin, "east", "horizontal")


def test_rotated_plastic_row_exposes_its_real_coal_belt_end() -> None:
    plan = oriented_fluid_machine_row(
        "plastic-bar", 2, direction="east", mirror="horizontal",
    )
    feed = _direct_single_belt_feed(plan, "coal", "north")
    belts = [
        action for action in actions(plan)
        if action.get("entity", "").endswith("transport-belt")
    ]

    assert any(
        (action["position"]["x"], action["position"]["y"]) == feed
        and action["direction"] == "north"
        for action in belts
    )
    assert not any(action.get("entity") == "infinity-chest" for action in actions(plan))


def test_compact_paired_expansion_uses_less_pipe_and_land_than_a_long_row() -> None:
    paired = compact_paired_fluid_block("basic-oil-processing", 8)
    long = generate_fluid_machine_row("basic-oil-processing", 8)
    paired_counts = Counter(action["entity"] for action in actions(paired.plan))
    long_counts = Counter(action["entity"] for action in actions(long))
    paired_pipe = paired_counts["pipe"] + paired_counts["pipe-to-ground"]
    long_pipe = long_counts["pipe"] + long_counts["pipe-to-ground"]
    paired_land = len(occupied_tile_indices([("paired", paired.plan)]))
    long_land = len(occupied_tile_indices([("long", long)]))

    assert paired_counts["oil-refinery"] == 8
    assert paired_pipe < long_pipe
    assert paired_counts["medium-electric-pole"] < long_counts["medium-electric-pole"]
    assert paired_land < long_land
    poles = [
        (action["position"]["x"], action["position"]["y"])
        for action in actions(paired.plan)
        if action["entity"] == "medium-electric-pole"
    ]
    reached = {poles[0]}
    while True:
        added = {
            pole for pole in poles if pole not in reached
            and any(math.dist(pole, live) <= POLE_SPECS["medium-electric-pole"]["wire"]
                    for live in reached)
        }
        if not added:
            break
        reached |= added
    assert reached == set(poles)


def test_oriented_paired_growth_keeps_the_opening_row_machine_centres() -> None:
    origin = (-120.0, 45.0)
    opening = oriented_fluid_machine_row(
        "basic-oil-processing", 4, origin,
        direction="west", mirror="horizontal",
    )
    growth = oriented_compact_paired_fluid_block(
        "basic-oil-processing", 8, origin,
        direction="west", mirror="horizontal",
    )
    opening_machines = {
        (action["position"]["x"], action["position"]["y"])
        for action in actions(opening) if action["entity"] == "oil-refinery"
    }
    growth_machines = {
        (action["position"]["x"], action["position"]["y"])
        for action in actions(growth.plan) if action["entity"] == "oil-refinery"
    }

    assert opening_machines < growth_machines


def test_expansion_search_prefers_the_compact_paired_shape_when_legal() -> None:
    candidates = fluid_block_candidates(
        "basic-oil-processing", 8, (0.0, 0.0),
        {"crude-oil": [(-30.0, 0.0)], "petroleum-gas": [(30.0, 0.0)]},
    )

    assert candidates
    assert candidates[0].shape == "paired"
    assert any(candidate.shape == "row" for candidate in candidates)
