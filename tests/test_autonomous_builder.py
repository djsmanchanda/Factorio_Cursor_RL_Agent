# Path: tests/test_autonomous_builder.py
# Purpose: Offline deterministic tests for real-base autonomous mining placement.

import math
from unittest.mock import ANY

import pytest

from orchestrator import live_base
from orchestrator.autonomous_builder import (
    _candidate_mining_origins,
    _choose_mining_origin,
    _mining_drill_positions,
)
from orchestrator.stage_recovery import (
    _existing_stage_feed_positions,
    repair_existing_ingredient_transport,
)
from orchestrator.stage_transport import transport_grace_seconds


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
        _overlaps_resource({(x, y) for x in range(3, 9) for y in range(7)}),
    )
    assert selected == ((1.0, 3.0), 2)


def test_choose_mining_origin_refuses_to_shrink_below_requested_capacity() -> None:
    assert _choose_mining_origin(
        (3.0, 3.0), (3.0, 0.0), (3.0, 0.0), 2,
        lambda _lower, _upper: True,
        _overlaps_resource({(3, 0)}),
    ) is None

def test_choose_mining_origin_fails_closed_when_stage_area_is_blocked() -> None:
    assert _choose_mining_origin(
        (3.0, 3.0), (3.0, 0.0), (8.0, 0.0), 2,
        lambda _lower, _upper: False,
        _overlaps_resource({(x, y) for x in range(3, 9) for y in range(7)}),
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
    # Reply is "<target count>,<foreign ore>" per centre, joined by ";".
    client = _FakeRcon("4,;4,")
    assert live_base.drill_footprints_have_resource(
        client, "nauvis", "iron-ore", [(11.5, 18.5), (14.5, 18.5)],
    )
    assert "name='iron-ore'" in client.command_text
    assert "type='resource'" in client.command_text
    assert "{{10.0,17.0},{13.0,20.0}}" in client.command_text
    assert "{{13.0,17.0},{16.0,20.0}}" in client.command_text


def test_live_resource_probe_rejects_one_empty_drill_footprint() -> None:
    assert not live_base.drill_footprints_have_resource(
        _FakeRcon("4,;0,"), "nauvis", "copper-ore", [(11.5, 18.5), (14.5, 18.5)],
    )


def test_delayed_belt_arrival_extends_the_machine_health_window() -> None:
    assert transport_grace_seconds("transport-belt", 60) == pytest.approx(68.0)


def test_existing_starved_stage_repairs_declared_transport(monkeypatch) -> None:
    machines = [(-25.5, -22.5), (-22.5, -22.5)]
    feeds, modes = _existing_stage_feed_positions("iron-gear-wheel", machines)
    assert feeds == {"iron-plate": (-29.5, -27.5)}
    assert modes == {"iron-plate": "belt"}

    calls = []
    monkeypatch.setattr(
        live_base, "entity_at",
        lambda _client, _surface, position: (
            {"name": "requester-chest", "type": "container", "force": "player"}
            if position == feeds["iron-plate"] else None
        ),
    )
    monkeypatch.setattr(
        "orchestrator.stage_recovery.ensure_ingredient_transport",
        lambda *args, **kwargs: calls.append((args[4:10], kwargs)) or 68.0,
    )
    monkeypatch.setattr(
        "orchestrator.stage_recovery._diagnose_machines",
        lambda *_args, **kwargs: [] if kwargs["grace_seconds"] == 68.0 else [("bad", "grace")],
    )

    repaired = repair_existing_ingredient_transport(
        object(), object(), "nauvis", "player", "iron-gear-wheel", machines,
        lambda ingredient: (-10.5, -10.5) if ingredient == "iron-plate" else None,
        lambda _message: None,
    )

    assert repaired
    assert calls == [((
        "iron-gear-wheel", "iron-plate", (-10.5, -10.5),
        (-29.5, -27.5), 2, ANY,
    ), {"reuse_existing": True})]


def test_existing_starved_stage_waits_for_missing_upstream(monkeypatch) -> None:
    machines = [(-25.5, -22.5), (-22.5, -22.5)]
    monkeypatch.setattr(
        live_base, "entity_at",
        lambda *_args: {"name": "requester-chest", "type": "container", "force": "player"},
    )
    monkeypatch.setattr(
        "orchestrator.stage_recovery.ensure_ingredient_transport",
        lambda *_args: pytest.fail("transport must not be submitted without a source"),
    )

    assert not repair_existing_ingredient_transport(
        object(), object(), "nauvis", "player", "iron-gear-wheel", machines,
        lambda _ingredient: None, lambda _message: None,
    )
