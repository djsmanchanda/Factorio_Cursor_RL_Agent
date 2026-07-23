# Path: tests/test_electronics_bundle_preflight.py
# Purpose: Regression cover for the two live-build defects M6 preflight found in
# the REAL composed electronics bundle: an unpowered far drill in the coal mining
# row, and route ghosts outside every roboport's construction radius.

from __future__ import annotations

from pathlib import Path

import pytest

from planners.electronics_block import build_electronics_block
from planners.electronics_world import load_electronics_world_spec
from planners.infrastructure import (
    POLE_SPECS, ROBOPORT_CONSTRUCTION_RADIUS, ROBOPORT_ENTITY, ROBOPORT_LINK_DISTANCE,
)
from planners.infrastructure_geometry import boxes_overlap, chebyshev_distance
from planners.plan_validation import actions
from planners.preflight import preflight
from planners.resource_layouts import generate_coal_mine
from planners.roboport_coverage import roboport_ghost_targets, uncovered_targets

_FIXTURE = Path(__file__).parent / "fixtures" / "electronics_world_spec.json"


@pytest.fixture(scope="module")
def bundle():
    world = load_electronics_world_spec(_FIXTURE)
    return build_electronics_block(include_processing=True, world=world)


def _placements(bundle):
    return [
        (name, action)
        for name, plan in bundle["infrastructure"] + bundle["plans"]
        for action in actions(plan)
        if action.get("action_type") in {"place_entity", "place_ghost"}
    ]


def test_real_electronics_bundle_passes_preflight(bundle):
    """The whole point of M6: this bundle is what goes to a live game."""
    result = preflight(bundle)
    assert result["ok"], "\n".join(
        f"[{f['check']}] {f['detail']}" for f in result["failures"]
    )
    assert result["skipped"] == []


def test_every_coal_drill_sits_in_a_pole_supply_area(bundle):
    """Defect 1: the row emitted ONE medium pole, so the third drill at x=26.5
    was 8 tiles past its 3.5-tile supply radius and silently unpowered."""
    placements = _placements(bundle)
    poles = [(a["entity"], (a["position"]["x"], a["position"]["y"]))
             for _, a in placements if a["entity"] in POLE_SPECS]
    drills = [(name, (a["position"]["x"], a["position"]["y"]))
              for name, a in placements if a["entity"] == "electric-mining-drill"]
    assert drills
    for name, position in drills:
        assert any(
            boxes_overlap(position, 3, pole_position, 2 * POLE_SPECS[entity]["supply"])
            for entity, pole_position in poles
        ), f"{name} drill at {position} is outside every pole's supply area"


def test_coal_row_pole_pitch_scales_with_row_width():
    """A wider row must emit more poles, not the same single anchor pole."""
    narrow = generate_coal_mine([(20.5, 0.5)], 2.5, 40.5)
    wide = generate_coal_mine(
        [(20.5, 0.5), (23.5, 0.5), (26.5, 0.5), (29.5, 0.5), (32.5, 0.5)], 2.5, 40.5,
    )
    count = lambda plan: sum(
        1 for a in actions(plan) if a["entity"] == "medium-electric-pole"
    )
    assert count(narrow) == 1
    assert count(wide) > count(narrow)


def test_every_ghost_is_inside_a_roboport_construction_radius(bundle):
    """Defect 2: 284 route ghosts (petroleum-gas, water, sulfuric-acid, ec_to_ac)
    fell outside the anchor-tree roboports, so bots could never build them."""
    named = bundle["infrastructure"] + bundle["plans"]
    ports = [(a["position"]["x"], a["position"]["y"])
             for _, plan in named for a in actions(plan) if a["entity"] == ROBOPORT_ENTITY]
    assert ports
    assert uncovered_targets(roboport_ghost_targets(named), ports) == []


def test_roboports_remain_one_logistic_network(bundle):
    """Coverage roboports are worthless if they form a second network."""
    ports = [(a["position"]["x"], a["position"]["y"])
             for _, plan in bundle["infrastructure"] + bundle["plans"]
             for a in actions(plan) if a["entity"] == ROBOPORT_ENTITY]
    reached, frontier = {0}, [0]
    while frontier:
        current = frontier.pop()
        for index, position in enumerate(ports):
            if index not in reached and chebyshev_distance(ports[current], position) <= ROBOPORT_LINK_DISTANCE:
                reached.add(index)
                frontier.append(index)
    assert len(reached) == len(ports)
    assert ROBOPORT_CONSTRUCTION_RADIUS > ROBOPORT_LINK_DISTANCE


def test_block_composition_is_deterministic():
    world = load_electronics_world_spec(_FIXTURE)
    left = build_electronics_block(include_processing=True, world=world)
    right = build_electronics_block(include_processing=True, world=world)
    assert left["infrastructure"] == right["infrastructure"]
    assert left["plans"] == right["plans"]
