# Path: tests/test_pipe_bridge.py
# Purpose: Offline contract tests for real-base, purity-safe pipe connections.

import pytest

from planners.pipe_bridge import bridge_pipe_to_pipe


def _actions(plan: dict) -> list[dict]:
    return [action for phase in plan["phases"] for action in phase["actions"]]


def test_bridge_pipe_to_pipe_emits_a_deterministic_connected_route():
    # Arrange
    source, destination = (0, 0), (4, 3)

    # Act
    first = bridge_pipe_to_pipe(source, destination, fluid="water")
    second = bridge_pipe_to_pipe(source, destination, fluid="water")

    # Assert
    actions = _actions(first)
    tiles = {(action["position"]["x"] - 0.5, action["position"]["y"] - 0.5)
             for action in actions}
    assert first == second
    assert {source, destination} <= tiles
    assert {action["entity"] for action in actions} <= {"pipe", "pipe-to-ground"}


@pytest.mark.parametrize("source,destination", [
    ((0.5, 0), (4, 0)),
    ((0, 0), (4, 0.25)),
])
def test_bridge_pipe_to_pipe_rejects_non_integer_endpoint_tiles(source, destination):
    with pytest.raises(ValueError, match="integer pipe tile"):
        bridge_pipe_to_pipe(source, destination, fluid="water")


def test_bridge_pipe_to_pipe_rejects_a_route_longer_than_320_tiles():
    with pytest.raises(ValueError, match="320"):
        bridge_pipe_to_pipe((0, 0), (321, 0), fluid="water")


def test_bridge_pipe_to_pipe_routes_around_a_foreign_fluid_network():
    # Arrange
    foreign = [{"fluid": "petroleum-gas", "separated_by_pump": False,
                "tiles": [(2, 0)]}]

    # Act
    plan = bridge_pipe_to_pipe((0, 0), (4, 0), fluid="water", foreign_segments=foreign)

    # Assert
    tiles = {(action["position"]["x"] - 0.5, action["position"]["y"] - 0.5)
             for action in _actions(plan)}
    assert (2, 0) not in tiles
