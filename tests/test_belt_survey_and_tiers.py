# Path: tests/test_belt_survey_and_tiers.py
# Purpose: Prove refinery routes survey their detours and share the affordable early-belt policy.

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import extraction_transport, stage_transport  # noqa: E402
from orchestrator.parts_mall import MaterialShortage  # noqa: E402
from orchestrator.stage_services import _BRIDGE_SURVEY_MARGIN  # noqa: E402
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
    assert "if not short:" in _PLAN


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


def test_direct_ore_route_reorients_the_terminal_without_an_inserter(
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

    actions, tier, reused = stage_transport._plan_belt_transport(
        object(), "nauvis", "player", "iron-ore",
        (12.5, -1.5), (0.5, 10.5), reuse_existing=True,
        max_belt_route_tiles=100, destination_is_belt=True,
        destination_belt_direction="east",
    )

    assert (tier, reused) == ("transport-belt", True)
    assert actions[0]["action_type"] == "remove_entity"
    assert actions[1]["position"] == {"x": 13.5, "y": -1.5}
    assert actions[1]["direction"] == "west"
    assert not any(action["entity"].endswith("inserter") for action in actions)


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


def test_the_cheapest_tier_is_a_real_belt_the_agent_can_build() -> None:
    from planners.recipe_data import LINE_RECIPES

    assert _BELT_TIERS_CHEAPEST_FIRST[0] == "transport-belt"
    assert "transport-belt" in LINE_RECIPES
