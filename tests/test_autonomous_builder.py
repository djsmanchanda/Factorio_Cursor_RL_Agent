# Path: tests/test_autonomous_builder.py
# Purpose: Offline deterministic tests for real-base autonomous mining placement.

import math

from orchestrator import live_base
from orchestrator.autonomous_builder import (
    _candidate_mining_origins,
    _choose_mining_origin,
    _mining_drill_positions,
)


def _overlaps_resource(resource_tiles: set[tuple[int, int]]):
    def overlaps(centres: list[tuple[float, float]]) -> bool:
        return all(any(
            (x, y) in resource_tiles
            for x in range(math.floor(cx - 1.5), math.floor(cx + 1.5))
            for y in range(math.floor(cy - 1.5), math.floor(cy + 1.5))
        ) for cx, cy in centres)
    return overlaps


def test_mining_drill_positions_match_generated_feed_geometry() -> None:
    assert _mining_drill_positions((10.0, 20.0), 2) == [(11.5, 18.5), (14.5, 18.5)]


def test_choose_mining_origin_shifts_from_irregular_patch_edge() -> None:
    selected = _choose_mining_origin(
        (0.0, 3.0), (3.0, 0.0), (8.0, 0.0), 2,
        lambda _lower, _upper: True,
        _overlaps_resource({(x, 0) for x in range(3, 9)}),
    )
    assert selected == ((1.0, 3.0), 2)


def test_choose_mining_origin_reduces_to_one_drill_when_required() -> None:
    selected = _choose_mining_origin(
        (3.0, 3.0), (3.0, 0.0), (3.0, 0.0), 2,
        lambda _lower, _upper: True,
        _overlaps_resource({(3, 0)}),
    )
    assert selected is not None
    assert selected[1] == 1


def test_choose_mining_origin_fails_closed_when_stage_area_is_blocked() -> None:
    assert _choose_mining_origin(
        (3.0, 3.0), (3.0, 0.0), (8.0, 0.0), 2,
        lambda _lower, _upper: False,
        _overlaps_resource({(x, 0) for x in range(3, 9)}),
    ) is None


def test_candidate_mining_origins_have_stable_distance_ordering() -> None:
    first = _candidate_mining_origins((4.0, 2.0), (3.0, 0.0), (8.0, 1.0), 2)
    second = _candidate_mining_origins((4.0, 2.0), (3.0, 0.0), (8.0, 1.0), 2)
    assert first == second

class _FakeRcon:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.command_text = ""

    def command(self, text: str) -> str:
        self.command_text = text
        return self.reply


def test_live_resource_probe_checks_each_exact_drill_footprint() -> None:
    client = _FakeRcon("11")
    assert live_base.drill_footprints_have_resource(
        client, "nauvis", "iron-ore", [(11.5, 18.5), (14.5, 18.5)],
    )
    assert "name='iron-ore'" in client.command_text
    assert "type='resource'" in client.command_text
    assert "{{10.0,17.0},{13.0,20.0}}" in client.command_text
    assert "{{13.0,17.0},{16.0,20.0}}" in client.command_text


def test_live_resource_probe_rejects_one_empty_drill_footprint() -> None:
    assert not live_base.drill_footprints_have_resource(
        _FakeRcon("10"), "nauvis", "copper-ore", [(11.5, 18.5), (14.5, 18.5)],
    )