# Path: tests/test_belt_survey_and_tiers.py
# Purpose: Prove refinery routes survey their detours and share the affordable early-belt policy.

from __future__ import annotations

import inspect
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator import extraction_transport, stage_transport  # noqa: E402
from orchestrator.parts_mall import MaterialShortage  # noqa: E402
from orchestrator.stage_services import StuckError, _BRIDGE_SURVEY_MARGIN  # noqa: E402
from orchestrator.stage_extraction import direct_mine_plan  # noqa: E402
from orchestrator.stage_transport import (  # noqa: E402
    _BELT_TIERS_CHEAPEST_FIRST,
    _choose_route_belt_tier,
    _mixed_belt_actions,
    _raw_belt_exit_direction,
    _route_belt_tiers,
)
from planners.belt_bridge import _ROUTE_SEARCH_MARGIN  # noqa: E402

_PLAN = inspect.getsource(stage_transport._plan_belt_transport)
_PREFLIGHT = inspect.getsource(extraction_transport.preflight_ingredient_transport)


def test_mixed_route_uses_fast_belts_only_for_regular_shortfall() -> None:
    actions = [
        {"action_type": "place_ghost", "entity": "transport-belt", "position": {"x": index, "y": 0}}
        for index in range(4)
    ]
    actions.append({"action_type": "place_ghost", "entity": "inserter", "position": {"x": 0, "y": 1}})

    mixed = _mixed_belt_actions(
        actions, {"transport-belt": 2, "fast-transport-belt": 2},
    )

    assert [action["entity"] for action in mixed] == [
        "transport-belt", "transport-belt", "fast-transport-belt",
        "fast-transport-belt", "inserter",
    ]


def test_mixed_route_does_not_replace_without_fast_stock() -> None:
    actions = [{"action_type": "place_ghost", "entity": "transport-belt"}]
    assert _mixed_belt_actions(actions, {"transport-belt": 0}) == actions

def test_refinery_routes_ignore_stocked_advanced_belts() -> None:
    stock = {
        "transport-belt": 0,
        "fast-transport-belt": 0,
        "express-transport-belt": 4000,
        "turbo-transport-belt": 4000,
    }

    assert _choose_route_belt_tier(
        stock, 100, destination_is_belt=True,
    ) in {"transport-belt", "fast-transport-belt"}
    assert _choose_route_belt_tier(
        stock, 100, destination_is_belt=False,
    ) == _route_belt_tiers(False)[0]


def test_the_survey_covers_everywhere_the_router_may_go() -> None:
    assert _BRIDGE_SURVEY_MARGIN >= _ROUTE_SEARCH_MARGIN


def test_the_two_margins_cannot_drift_apart() -> None:
    source = inspect.getsource(sys.modules["orchestrator.stage_services"])
    line = next(
        line for line in source.splitlines()
        if line.startswith("_BRIDGE_SURVEY_MARGIN =")
    )

    assert "_ROUTE_SEARCH_MARGIN" in line


def test_modular_refinery_preflight_uses_the_shared_affordability_router() -> None:
    assert "_plan_belt_transport(" in _PREFLIGHT
    assert "_route_belt_tiers(destination_is_belt)" in _PLAN
    assert "for tier in ordered:" in _PLAN
    assert "defer_required_tier_affordability" in _PLAN


def test_refinery_preflight_reserves_template_belts_before_routing(monkeypatch) -> None:
    captured = {}

    def route(*_args, **kwargs):
        captured.update(kwargs)
        return [], "transport-belt", False

    monkeypatch.setattr(extraction_transport, "_plan_belt_transport", route)
    extraction_transport.preflight_ingredient_transport(
        object(), "nauvis", "player", "iron-plate", "iron-ore",
        (10.5, 0.5), (0.5, 10.5), 6, max_belt_route_tiles=100,
        mode="belt", destination_is_belt=True, reserved_transport_belts=7,
    )

    assert captured["reserved_transport_belts"] == 7


def test_foundation_preflight_forwards_one_required_tier(monkeypatch) -> None:
    captured = {}

    def route(*_args, **kwargs):
        captured.update(kwargs)
        return [], "transport-belt", False

    monkeypatch.setattr(extraction_transport, "_plan_belt_transport", route)
    extraction_transport.preflight_ingredient_transport(
        object(), "nauvis", "player", "copper-plate", "copper-ore",
        (10.5, 0.5), (30.5, 0.5), 6, max_belt_route_tiles=100,
        mode="belt", destination_is_belt=True,
        required_belt_type="transport-belt",
        defer_required_tier_affordability=True,
    )

    assert captured["required_belt_type"] == "transport-belt"
    assert captured["defer_required_tier_affordability"] is True


def test_required_foundation_tier_does_not_promote_to_stocked_fast(
    monkeypatch,
) -> None:
    actions = [
        {"action_type": "place_ghost", "entity": "transport-belt",
         "position": {"x": index + 0.5, "y": 0.5}, "direction": "east"}
        for index in range(4)
    ]
    monkeypatch.setattr(
        stage_transport, "_survey_belt_route",
        lambda *_args, **_kwargs: (
            None, (0.5, 0.5), set(), "west", "east",
        ),
    )
    monkeypatch.setattr(
        stage_transport, "_route_belt_actions",
        lambda *_args, **_kwargs: [dict(action) for action in actions],
    )
    monkeypatch.setattr(
        stage_transport.live_base, "available_items",
        lambda *_args: {"transport-belt": 0, "fast-transport-belt": 400},
    )

    planned, tier, _reused = stage_transport._plan_belt_transport(
        object(), "nauvis", "player", "copper-ore",
        (0.5, 0.5), (4.5, 0.5), reuse_existing=True,
        max_belt_route_tiles=100, destination_is_belt=True,
        required_belt_type="transport-belt",
        defer_required_tier_affordability=True,
    )

    assert tier == "transport-belt"
    assert {action["entity"] for action in planned} == {"transport-belt"}

def test_direct_refinery_shortage_remains_recoverable(monkeypatch) -> None:
    shortage = MaterialShortage(
        "belt bridge for copper-ore", {"transport-belt": 40}, {"transport-belt": 8},
    )
    monkeypatch.setattr(
        extraction_transport, "_plan_belt_transport",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(shortage),
    )

    with pytest.raises(MaterialShortage) as raised:
        extraction_transport.preflight_ingredient_transport(
            object(), "nauvis", "player", "copper-plate", "copper-ore",
            (77.5, -39.5), (80.5, -18.5), 6,
            max_belt_route_tiles=100, mode="belt", destination_is_belt=True,
        )

    assert raised.value is shortage


@pytest.mark.parametrize(
    ("belt_source", "terminal", "expected"),
    [
        ((13.5, -1.5), (12.5, -1.5), "west"),
        ((11.5, -1.5), (12.5, -1.5), "east"),
        ((12.5, 0.5), (12.5, -0.5), "north"),
        ((12.5, -1.5), (12.5, -0.5), "south"),
    ],
)
def test_raw_ore_continues_toward_the_mine_terminal(
    belt_source: tuple[float, float], terminal: tuple[float, float], expected: str,
) -> None:
    assert _raw_belt_exit_direction("iron-ore", belt_source, terminal) == expected


def test_non_raw_routes_do_not_override_the_router_exit() -> None:
    assert _raw_belt_exit_direction(
        "iron-plate", (13.5, -1.5), (12.5, -1.5),
    ) is None


def test_unowned_direct_ore_terminal_is_not_reoriented_or_removed(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        stage_transport, "_through_belt_source",
        lambda *_args, **_kwargs: (13.5, -1.5),
    )
    monkeypatch.setattr(
        stage_transport.live_base, "available_items",
        lambda *_args: {"transport-belt": 200},
    )
    monkeypatch.setattr(
        stage_transport.live_base, "occupied_tiles",
        lambda *_args, **kwargs: set() if kwargs.get("ignore_names") else {(12, -2)},
    )
    monkeypatch.setattr(
        stage_transport.live_base, "entity_at",
        lambda _client, _surface, position: (
            {"type": "transport-belt", "name": "transport-belt"}
            if position == (13.5, -1.5) else None
        ),
    )

    with pytest.raises(StuckError, match="no belt route"):
        stage_transport._plan_belt_transport(
            object(), "nauvis", "player", "iron-ore",
            (12.5, -1.5), (0.5, 10.5), reuse_existing=True,
            max_belt_route_tiles=100, destination_is_belt=True,
            destination_belt_direction="east",
        )


def test_chest_source_tile_stays_blocked_against_its_own_bridge(monkeypatch) -> None:
    """Live run of 2026-08-24 18:47: the recorded provider chest's tile was
    released from the occupancy survey, so the pipe bridge routed belts over
    the chest and its feed inserter and died on execution. A chest bridge
    attaches beside its source (+1 inserter, +2 belt); only belt endpoints
    own their tile."""
    monkeypatch.setattr(
        stage_transport, "_through_belt_source", lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        stage_transport.live_base, "available_items",
        lambda *_args: {"transport-belt": 400},
    )
    monkeypatch.setattr(
        stage_transport.live_base, "occupied_tiles",
        lambda *_args, **_kwargs: {(110, 29)},
    )
    monkeypatch.setattr(
        stage_transport.live_base, "entity_at", lambda *_args: None,
    )

    _source, _route, blocked, _entry, _exit = stage_transport._survey_belt_route(
        object(), "nauvis", "player", "iron-plate",
        (110.5, 29.5), (120.5, 39.5),
        reuse_existing=False, additional_blocked=None, upstream_shift=1,
        destination_is_belt=False, destination_belt_direction="east",
        planned_belt_source=None,
    )

    assert (110, 29) in blocked


def test_source_belt_replacement_requires_exact_owned_signature(monkeypatch) -> None:
    source = (13.5, -1.5)
    actions = [{
        "action_type": "place_ghost",
        "entity": "transport-belt",
        "position": {"x": source[0], "y": source[1]},
        "direction": "west",
    }]
    monkeypatch.setattr(
        stage_transport.live_base,
        "entity_at",
        lambda *_a: {
            "name": "transport-belt", "type": "transport-belt",
            "direction": 4, "force": "player",
        },
    )

    assert stage_transport._replace_existing_source_belt(
        object(), "nauvis", source, actions,
    ) == actions

    replaced = stage_transport._replace_existing_source_belt(
        object(), "nauvis", source, actions,
        owned_source=("transport-belt", source[0], source[1], "east"),
    )
    assert [action["action_type"] for action in replaced] == [
        "remove_entity", "place_ghost",
    ]


def test_planned_direct_mine_handoff_is_not_treated_as_an_obstacle(monkeypatch) -> None:
    mine_plan, ore_output = direct_mine_plan(
        (100.0, 100.0), 6, belt_type="transport-belt",
        inserter_type="inserter", reserved_pair_columns=0,
    )
    planned = extraction_transport.planned_footprint_tiles(mine_plan)
    planned -= {
        (int(ore_output[0]), int(ore_output[1])),
        (int(ore_output[0] + 1), int(ore_output[1])),
    }
    monkeypatch.setattr(
        stage_transport, "_through_belt_source",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        stage_transport.live_base, "occupied_tiles",
        lambda *_args, **_kwargs: set(),
    )
    monkeypatch.setattr(
        stage_transport.live_base, "entity_at", lambda *_args: None,
    )

    source, route_source, _blocked, _entry, exit_direction = (
        stage_transport._survey_belt_route(
            object(), "nauvis", "player", "iron-ore", ore_output,
            (100.0, 130.0), reuse_existing=True,
            additional_blocked=planned, upstream_shift=1,
            destination_is_belt=True, destination_belt_direction="east",
            planned_belt_source=(ore_output[0] + 1, ore_output[1]),
        )
    )

    assert source == (ore_output[0] + 1, ore_output[1])
    assert route_source == source
    assert exit_direction == "west"


def test_blocked_raw_mine_exit_falls_back_to_the_router_exit(monkeypatch) -> None:
    mine_plan, ore_output = direct_mine_plan(
        (100.0, 100.0), 6, belt_type="transport-belt",
        inserter_type="inserter", reserved_pair_columns=0,
    )
    planned = extraction_transport.planned_footprint_tiles(mine_plan)
    planned -= {
        (int(ore_output[0]), int(ore_output[1])),
        (int(ore_output[0] + 1), int(ore_output[1])),
    }
    belt_source = (ore_output[0] + 1, ore_output[1])
    blocked_west_tile = (
        math.floor(belt_source[0] - 1), math.floor(belt_source[1]),
    )

    monkeypatch.setattr(
        stage_transport, "_through_belt_source",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        stage_transport.live_base, "occupied_tiles",
        lambda *_args, **_kwargs: {blocked_west_tile},
    )
    monkeypatch.setattr(
        stage_transport.live_base, "entity_at", lambda *_args: None,
    )

    source, route_source, _blocked, _entry, exit_direction = (
        stage_transport._survey_belt_route(
            object(), "nauvis", "player", "iron-ore", ore_output,
            (100.0, 130.0), reuse_existing=True,
            additional_blocked=planned, upstream_shift=1,
            destination_is_belt=True, destination_belt_direction="east",
            planned_belt_source=belt_source,
        )
    )

    assert source == route_source == belt_source
    assert exit_direction != "west"
    assert exit_direction == "south"


def test_reuse_survey_does_not_hide_foreign_belt_families(monkeypatch) -> None:
    captured: dict[str, tuple[str, ...]] = {}
    monkeypatch.setattr(
        stage_transport, "_through_belt_source", lambda *_a, **_k: None,
    )

    def occupied(*_args, **kwargs):
        ignored = tuple(kwargs.get("ignore_names", ()))
        captured["ignored"] = ignored
        return set() if "transport-belt" in ignored else {(4, 0)}

    monkeypatch.setattr(stage_transport.live_base, "occupied_tiles", occupied)
    monkeypatch.setattr(
        stage_transport.live_base, "entity_at", lambda *_args: None,
    )

    _source, _route_source, blocked, _entry, _exit = (
        stage_transport._survey_belt_route(
            object(), "nauvis", "player", "iron-plate",
            (0.5, 0.5), (10.5, 0.5), reuse_existing=True,
            additional_blocked=None, upstream_shift=1,
            destination_is_belt=False, destination_belt_direction="east",
            planned_belt_source=None,
        )
    )

    assert "transport-belt" not in captured["ignored"]
    assert (4, 0) in blocked


def test_provisioning_retry_reuses_its_live_inline_approach(monkeypatch) -> None:
    """The 2026-08-29 copper retry found its own eastbound route at the
    refinery entrance and rejected it as occupied.  A persisted reservation
    plus matching live direction is enough ownership evidence to reuse it."""
    source = (130.5, -76.5)
    feed = (143.5, -76.5)
    approach = {(141, -77), (142, -77)}
    monkeypatch.setattr(
        stage_transport, "_through_belt_source", lambda *_a, **_k: source,
    )
    monkeypatch.setattr(
        stage_transport.live_base, "occupied_tiles",
        lambda *_a, **_k: set(approach),
    )
    monkeypatch.setattr(
        stage_transport.live_base, "entity_at",
        lambda _c, _s, position: (
            {"type": "transport-belt", "name": "transport-belt"}
            if position == source else None
        ),
    )
    monkeypatch.setattr(
        stage_transport.live_base, "transport_belt_direction_at",
        lambda _c, _s, position: (
            "east" if (math.floor(position[0]), math.floor(position[1])) in approach
            else None
        ),
    )

    _belt_source, _route, blocked, entry, _exit = (
        stage_transport._survey_belt_route(
            object(), "nauvis", "player", "copper-ore", source, feed,
            reuse_existing=True, additional_blocked=None, upstream_shift=1,
            destination_is_belt=True, destination_belt_direction="east",
            planned_belt_source=None, through_flow_direction="east",
            owned_transport_tiles=approach,
        )
    )

    assert entry == "west"
    assert not approach & blocked


@pytest.mark.parametrize(
    ("owned", "live_direction"),
    [
        (set(), "east"),
        ({(141, -77), (142, -77)}, "west"),
    ],
)
def test_inline_approach_reuse_fails_closed_without_exact_ownership_and_flow(
    monkeypatch, owned, live_direction,
) -> None:
    source = (130.5, -76.5)
    feed = (143.5, -76.5)
    approach = {(141, -77), (142, -77)}
    monkeypatch.setattr(
        stage_transport, "_through_belt_source", lambda *_a, **_k: source,
    )
    monkeypatch.setattr(
        stage_transport.live_base, "occupied_tiles",
        lambda *_a, **_k: set(approach),
    )
    monkeypatch.setattr(
        stage_transport.live_base, "entity_at", lambda *_a: None,
    )
    monkeypatch.setattr(
        stage_transport.live_base, "transport_belt_direction_at",
        lambda *_a: live_direction,
    )

    from orchestrator import autonomous_builder

    with pytest.raises(autonomous_builder.ProductionPrerequisiteDeferred) as failure:
        stage_transport._survey_belt_route(
            object(), "nauvis", "player", "copper-ore", source, feed,
            reuse_existing=True, additional_blocked=None, upstream_shift=1,
            destination_is_belt=True, destination_belt_direction="east",
            planned_belt_source=None, through_flow_direction="east",
            owned_transport_tiles=owned,
        )
    assert failure.value.code == "direct_belt_approach_wait"
    assert failure.value.state == "constructing"
    assert failure.value.details["blocked_approach_tiles"]


def test_inline_approach_wait_reports_occupying_entities(monkeypatch) -> None:
    from types import SimpleNamespace
    from orchestrator import autonomous_builder

    source = (130.5, -76.5)
    feed = (143.5, -76.5)
    approach = {(141, -77), (142, -77)}
    monkeypatch.setattr(
        stage_transport, "_through_belt_source", lambda *_a, **_k: source,
    )
    monkeypatch.setattr(
        stage_transport.live_base, "occupied_tiles",
        lambda *_a, **_k: set(approach),
    )
    monkeypatch.setattr(
        stage_transport.live_base, "entity_at", lambda *_a: None,
    )
    monkeypatch.setattr(
        stage_transport.live_base, "transport_belt_direction_at",
        lambda *_a: "east",
    )
    monkeypatch.setattr(
        stage_transport.live_base, "occupied_tile_owners",
        lambda *_a, **_k: {(141, -77): ("transport-belt", 141.5, -76.5)},
    )

    with pytest.raises(autonomous_builder.ProductionPrerequisiteDeferred) as failure:
        stage_transport._survey_belt_route(
            SimpleNamespace(command=lambda _command: ""),
            "nauvis", "player", "copper-ore", source, feed,
            reuse_existing=True, additional_blocked=None, upstream_shift=1,
            destination_is_belt=True, destination_belt_direction="east",
            planned_belt_source=None, through_flow_direction="east",
            owned_transport_tiles=set(),
        )

    assert failure.value.details["occupying_entities"][0]["entity"] == "transport-belt"


def test_the_cheapest_tier_is_a_real_belt_the_agent_can_build() -> None:
    from planners.recipe_data import LINE_RECIPES

    assert _BELT_TIERS_CHEAPEST_FIRST[0] == "transport-belt"
    assert "transport-belt" in LINE_RECIPES


def test_phantom_through_source_does_not_force_a_backwards_exit(
    monkeypatch,
) -> None:
    """Live run of 2026-08-24 14:12: an east-flow head reports the EMPTY tile
    past the head as its through source; the raw-exit heuristic read that
    phantom as a west-flow terminal and drove the haul backwards into the
    head belt, cornering on a tile it can never own. A raw exit describes a
    belt that stands at the through source -- no belt, no surveyed direction."""
    monkeypatch.setattr(
        stage_transport, "_through_belt_source",
        lambda *_args, **_kwargs: (90.5, -39.5),
    )
    monkeypatch.setattr(
        stage_transport.live_base, "occupied_tiles",
        lambda *_args, **_kwargs: set(),
    )
    monkeypatch.setattr(
        stage_transport.live_base, "entity_at",
        lambda _c, _s, position: (
            {"type": "transport-belt", "name": "fast-transport-belt"}
            if position == (89.5, -39.5) else None
        ),
    )

    _belt_source, _route, _blocked, _entry, exit_direction = (
        stage_transport._survey_belt_route(
            object(), "nauvis", "player", "copper-ore",
            (89.5, -39.5), (110.5, -39.5),
            reuse_existing=True, additional_blocked=None, upstream_shift=1,
            destination_is_belt=True, destination_belt_direction="east",
            planned_belt_source=None,
        )
    )

    assert exit_direction == "east"


def test_stated_flow_direction_beats_the_position_heuristic(
    monkeypatch,
) -> None:
    """Live run of 2026-08-24 16:24: a fresh east-flow row is still ghosts,
    so the occupied survey saw no head belt; the raw heuristic read the
    planned handoff as a west terminal and the haul cornered on the head
    tile it can never own. When the caller states the collector's flow, that
    flow is the exit."""
    monkeypatch.setattr(
        stage_transport, "_through_belt_source",
        lambda *_args, **_kwargs: (90.5, -39.5),
    )
    monkeypatch.setattr(
        stage_transport.live_base, "occupied_tiles",
        lambda *_args, **_kwargs: set(),
    )
    monkeypatch.setattr(
        stage_transport.live_base, "entity_at", lambda *_args: None,
    )

    _source, _route, _blocked, _entry, exit_direction = (
        stage_transport._survey_belt_route(
            object(), "nauvis", "player", "stone",
            (89.5, -39.5), (110.5, -39.5),
            reuse_existing=True, additional_blocked=None, upstream_shift=1,
            destination_is_belt=True, destination_belt_direction="east",
            planned_belt_source=(90.5, -39.5),
            through_flow_direction="east",
        )
    )

    assert exit_direction == "east"
