# Path: tests/test_belt_to_belt_feed.py
# Purpose: Prove a belt-fed stage is BUILT the way the preflight approved -- one continuous belt with no inserter spliced into it -- and that running short of belt queues more rather than ending the run.

from __future__ import annotations

import inspect

import pytest
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator import live_base, stage_transport  # noqa: E402
from orchestrator.parts_mall import MaterialShortage  # noqa: E402
from planners.belt_bridge import (  # noqa: E402
    bridge_belt_to_belt,
    bridge_belt_to_chest,
    bridge_chest_to_belt,
    bridge_chest_to_chest,
)

_PLAN = inspect.getsource(stage_transport._plan_belt_transport)
_ROUTE = inspect.getsource(stage_transport._route_belt_actions)


def _shape(fn, **kwargs) -> Counter:
    return Counter(
        action["entity"]
        for action in fn((0.5, 0.5), (20.5, 0.5), belt_type="transport-belt", **kwargs)
    )


def test_a_belt_to_belt_join_needs_no_inserter() -> None:
    """The join a mine belt makes with a furnace row's input belt is one
    continuous belt. An inserter spliced into it is a throughput cap and an
    extra hop for no reason."""
    assert _shape(bridge_belt_to_belt, entry_direction="west")["inserter"] == 0

def test_refinery_bus_requires_an_inline_upstream_approach() -> None:
    assert stage_transport._direct_belt_entry((20.5, 0.5), set(), "west") == "east"
    assert stage_transport._direct_belt_entry((20.5, 0.5), set(), "east") == "west"


def test_refinery_bus_rejects_a_side_merge_when_its_upstream_end_is_blocked() -> None:
    with pytest.raises(Exception, match="inline direct-belt approach"):
        stage_transport._direct_belt_entry((20.5, 0.5), {(21, 0)}, "west")

def test_refinery_bridge_reaches_the_inline_bus_end_without_a_t_merge() -> None:
    bus = {(x, 31) for x in range(24)} - {(23, 31)}
    actions = bridge_belt_to_belt(
        (13.5, -1.5), (23.5, 31.5),
        entry_direction="east", exit_direction="west",
        destination_direction="west", belt_type="transport-belt",
        blocked_tiles=bus,
    )
    endpoints = [
        action for action in actions
        if action["position"] in ({"x": 24.5, "y": 31.5}, {"x": 23.5, "y": 31.5})
    ]

    # The existing inline bus tile is authoritative; only the upstream
    # approach may be planned.
    assert [action["direction"] for action in endpoints] == ["west"]
    positions = [
        (action["position"]["x"], action["position"]["y"])
        for action in actions
    ]
    assert len(positions) == len(set(positions))

def test_iron_mine_to_smelter_is_one_direct_unbuffered_belt() -> None:
    actions = bridge_belt_to_belt(
        (13.5, -1.5), (13.5, -31.5),
        entry_direction="south", exit_direction="west",
        destination_direction="west",
        belt_type="fast-transport-belt",
    )
    assert not any(
        action["entity"].endswith(("chest", "inserter")) for action in actions
    )
    positions = [
        (action["position"]["x"], action["position"]["y"])
        for action in actions
    ]
    assert len(positions) == len(set(positions))
    final = next(
        action for action in actions
        if action["position"] == {"x": 12.5, "y": -30.5}
    )
    assert final["direction"] == "north"


def test_pending_mine_ghosts_are_recognized_as_a_through_belt(
    monkeypatch,
) -> None:
    entities = {
        (12.5, -2.5): {
            "name": "entity-ghost", "type": "entity-ghost",
            "force": "player", "ghost_name": "inserter",
        },
        (12.5, -1.5): {
            "name": "entity-ghost", "type": "entity-ghost",
            "force": "player", "ghost_name": "fast-transport-belt",
        },
        (13.5, -1.5): {
            "name": "entity-ghost", "type": "entity-ghost",
            "force": "player", "ghost_name": "fast-transport-belt",
        },
    }
    monkeypatch.setattr(live_base, "entity_at", lambda _c, _s, p: entities.get(p))

    assert stage_transport._through_belt_source(
        object(), "nauvis", "iron-ore", (12.5, -3.5)
    ) == (13.5, -1.5)
def test_direct_mine_belt_is_used_without_a_side_tap(monkeypatch) -> None:
    entities = {
        (7.5, 20.5): {
            "type": "entity-ghost", "ghost_name": "fast-transport-belt",
        },
    }
    monkeypatch.setattr(live_base, "entity_at", lambda _c, _s, p: entities.get(p))
    assert stage_transport._through_belt_source(
        object(), "nauvis", "iron-ore", (7.5, 20.5)
    ) == (8.5, 20.5)

def test_shifted_mine_side_tap_leaves_the_turn_column_clear(monkeypatch) -> None:
    entities = {
        (14.5, -2.5): {"type": "entity-ghost", "ghost_name": "inserter"},
        (12.5, -1.5): {
            "type": "entity-ghost", "ghost_name": "fast-transport-belt",
        },
        (13.5, -1.5): {
            "type": "entity-ghost", "ghost_name": "fast-transport-belt",
        },
    }
    monkeypatch.setattr(live_base, "entity_at", lambda _c, _s, p: entities.get(p))
    assert stage_transport._through_belt_source(
        object(), "nauvis", "iron-ore", (14.5, -3.5)
    ) == (13.5, -1.5)

def test_the_chest_shaped_bridges_do_need_them() -> None:
    """Which is why using one for a belt destination was the defect: the
    inserters are correct there, and wrong here."""
    assert _shape(
        bridge_belt_to_chest, entry_direction="west", inserter_type="inserter",
    )["inserter"] == 1
    assert _shape(
        bridge_chest_to_chest, exit_direction="east", entry_direction="west",
        inserter_type="inserter",
    )["inserter"] == 2


def test_chest_to_inline_belt_has_only_the_source_inserter() -> None:
    actions = bridge_chest_to_belt(
        (0.5, 0.5), (20.5, 0.5),
        exit_direction="east", entry_direction="west",
        destination_direction="east", belt_type="transport-belt",
        inserter_type="inserter",
    )

    assert sum(action["entity"].endswith("inserter") for action in actions) == 1
    assert any(
        action["position"] == {"x": 19.5, "y": 0.5}
        and action["direction"] == "east"
        for action in actions
    )
    assert not any(
        action["position"] == {"x": 20.5, "y": 0.5}
        for action in actions
    )


def test_declared_chest_source_can_feed_an_inline_belt(monkeypatch) -> None:
    monkeypatch.setattr(stage_transport, "_through_belt_source", lambda *_a, **_k: None)
    monkeypatch.setattr(live_base, "entity_at", lambda *_a, **_k: None)
    monkeypatch.setattr(live_base, "occupied_tiles", lambda *_a, **_k: set())
    monkeypatch.setattr(
        live_base, "available_items",
        lambda *_a, **_k: {"transport-belt": 100, "inserter": 10},
    )

    actions, belt_type, reused_belt = stage_transport._plan_belt_transport(
        object(), "nauvis", "player", "iron-plate",
        (0.5, 0.5), (20.5, 0.5), reuse_existing=False,
        max_belt_route_tiles=None, destination_is_belt=True,
        destination_belt_direction="east", allow_chest_source_to_belt=True,
    )

    assert belt_type == "transport-belt"
    assert reused_belt is False
    assert sum(action["entity"].endswith("inserter") for action in actions) == 1


def test_raw_refinery_feed_still_rejects_a_chest_source(monkeypatch) -> None:
    monkeypatch.setattr(stage_transport, "_through_belt_source", lambda *_a, **_k: None)

    with pytest.raises(Exception, match="refusing a chest/inserter side-feed"):
        stage_transport._survey_belt_route(
            object(), "nauvis", "player", "iron-ore",
            (0.5, 0.5), (20.5, 0.5), reuse_existing=False,
            additional_blocked=None, upstream_shift=1,
            destination_is_belt=True, destination_belt_direction="east",
            planned_belt_source=None,
        )


def test_the_build_path_knows_when_the_destination_is_a_belt() -> None:
    """It did not. The preflight took `destination_is_belt` and planned a
    belt-to-belt join; the build path had no such parameter and laid a
    chest-shaped bridge instead -- a different plan from the one approved."""
    assert "destination_is_belt" in inspect.signature(
        stage_transport._plan_belt_transport
    ).parameters
    assert "destination_belt_direction" in inspect.signature(
        stage_transport._plan_belt_transport
    ).parameters


def test_refinery_never_falls_back_to_a_chest_side_feed() -> None:
    assert "refusing a chest/inserter side-feed" in inspect.getsource(
        stage_transport._survey_belt_route
    )

def test_both_belt_ends_produce_a_belt_to_belt_bridge() -> None:
    assert "if belt_source is not None and destination_is_belt:" in _ROUTE
    assert "bridge_belt_to_belt(" in _ROUTE


def test_only_declared_conversion_feeds_may_drain_a_chest_into_a_belt() -> None:
    survey = inspect.getsource(stage_transport._survey_belt_route)
    conversion = inspect.getsource(builder._conversion_feed_plan)
    connect = inspect.getsource(builder._connect_stage_feeds)

    assert "and not allow_chest_source_to_belt" in survey
    assert 'allow_chest_source_to_belt=(recipe == "steel-plate")' in conversion
    assert 'allow_chest_source_to_belt=(recipe == "steel-plate")' in connect
    assert "bridge_chest_to_belt(" in _ROUTE


def test_a_chest_destination_still_gets_its_inserter() -> None:
    assert "if belt_source is not None:" in _ROUTE
    assert "bridge_belt_to_chest(" in _ROUTE


def test_the_conversion_stage_passes_the_flag_to_the_build_not_only_the_preflight() -> None:
    """The flag existed and was threaded to the preflight alone, which is why
    the two disagreed."""
    connect = inspect.getsource(builder._connect_stage_feeds)

    assert "destination_is_belt=direct_belt_input" in connect
    assert "destination_belt_direction=destination_belt_direction" in connect


def test_running_short_of_belt_is_recoverable() -> None:
    """Every other build path turns a shortage into a mall target and retries.
    Raising StuckError here ended whole runs on a bridge the base could have
    supplied minutes later -- 'short transport-belt by 96' while the mall held
    an unfilled 4800-belt target."""
    tail = _PLAN[_PLAN.index("if not shortfalls and route_error is not None:"):]

    assert "raise MaterialShortage(" in tail


def test_a_shortage_carries_a_real_requirement() -> None:
    shortage = MaterialShortage(
        "belt bridge for iron-ore", {"transport-belt": 120}, {"transport-belt": 24},
    )

    assert shortage.required == {"transport-belt": 120}
    assert "short 96" in str(shortage)


def test_an_unroutable_bridge_is_still_a_hard_failure() -> None:
    """No route at all is a geometry problem; more belt cannot fix it."""
    assert "no belt route is available for this bridge" in _PLAN
    assert "raise StuckError(" in _PLAN
