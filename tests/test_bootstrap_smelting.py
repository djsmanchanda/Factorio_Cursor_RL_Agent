# Path: tests/test_bootstrap_smelting.py
# Purpose: Verify the plate bootstrap has no circular belt dependency.

import pytest

from orchestrator import autonomous_builder as builder
from orchestrator import live_base
from planners.bootstrap_smelting import (
    direct_smelter_positions,
    generate_direct_smelter,
    logistic_smelter_origin,
    retire_direct_smelter_plan,
    retire_logistic_smelter_plan,
)
from planners.plan_validation import validate_no_collisions


def test_direct_starter_matches_the_live_reference_stack() -> None:
    positions = direct_smelter_positions((61.5, 24.5), "north")

    assert positions == {
        "drill": (61.5, 24.5),
        "furnace": (61.5, 21.5),
        "inserter": (61.5, 19.5),
        "provider": (61.5, 18.5),
        "power": (59.5, 22.5),
    }
    plan = generate_direct_smelter(
        "copper-plate", "copper-ore", (61.5, 24.5), "north",
    )
    actions = plan["phases"][0]["actions"]
    assert [action["entity"] for action in actions] == [
        "medium-electric-pole",
        "electric-mining-drill",
        "electric-furnace",
        "inserter",
        "passive-provider-chest",
    ]
    assert not any("transport-belt" in action["entity"] for action in actions)
    assert not any("requester" in action["entity"] for action in actions)


def test_stone_starter_uses_two_drills_feeding_one_furnace() -> None:
    positions = direct_smelter_positions(
        (54.5, -64.5), "north", drill_count=2,
    )
    assert positions["secondary_drill"] == (57.5, -67.5)
    plan = generate_direct_smelter(
        "stone-brick", "stone", (54.5, -64.5), "north",
    )
    actions = plan["phases"][0]["actions"]

    drills = [
        action for action in actions
        if action["entity"] == "electric-mining-drill"
    ]
    assert [(action["position"], action["direction"]) for action in drills] == [
        ({"x": 57.5, "y": -67.5}, "west"),
        ({"x": 54.5, "y": -64.5}, "north"),
    ]
    assert sum(action["entity"] == "electric-furnace" for action in actions) == 1
    assert sum(action["entity"] == "medium-electric-pole" for action in actions) == 2
    assert not any("transport-belt" in action["entity"] for action in actions)
    assert not any("requester" in action["entity"] for action in actions)


def test_iron_starter_uses_two_direct_lanes_with_one_provider() -> None:
    positions = direct_smelter_positions(
        (61.5, 24.5), "north", drill_count=2, furnace_count=2,
    )
    plan = generate_direct_smelter(
        "iron-plate", "iron-ore", (61.5, 24.5), "north",
    )
    actions = plan["phases"][0]["actions"]

    assert positions["secondary_drill"] == (61.5, 12.5)
    assert positions["secondary_furnace"] == (61.5, 15.5)
    assert positions["secondary_inserter"] == (61.5, 17.5)
    assert sum(action["entity"] == "electric-mining-drill" for action in actions) == 2
    assert sum(action["entity"] == "electric-furnace" for action in actions) == 2
    assert sum(action["entity"] == "passive-provider-chest" for action in actions) == 1
    secondary = next(
        action for action in actions
        if action["entity"] == "electric-mining-drill"
        and action["position"] == {"x": 61.5, "y": 12.5}
    )
    assert secondary["direction"] == "south"


@pytest.mark.parametrize("direction", ["north", "east", "south", "west"])
@pytest.mark.parametrize("pole_side", [-1, 1])
def test_two_lane_iron_starter_is_collision_free_in_every_orientation(
    direction: str, pole_side: int,
) -> None:
    plan = generate_direct_smelter(
        "iron-plate", "iron-ore", (61.5, 24.5), direction,
        pole_side=pole_side,
    )

    validate_no_collisions([("iron-starter", plan)])


@pytest.mark.parametrize("direction", ["north", "east", "south", "west"])
@pytest.mark.parametrize("pole_side", [-1, 1])
def test_two_drill_starter_is_collision_free_in_every_orientation(
    direction: str, pole_side: int,
) -> None:
    plan = generate_direct_smelter(
        "stone-brick", "stone", (54.5, -64.5), direction,
        pole_side=pole_side,
    )

    validate_no_collisions([("stone-brick-starter", plan)])


def test_stone_starter_retirement_removes_both_drills() -> None:
    retirement = retire_direct_smelter_plan(
        "stone-brick", "stone", (54.5, -64.5), "north",
    )
    actions = retirement["phases"][0]["actions"]

    assert sum(action["entity"] == "electric-mining-drill" for action in actions) == 2
    assert not any(action["entity"] == "medium-electric-pole" for action in actions)


def test_direct_starter_retirement_keeps_only_its_shared_power_pole() -> None:
    retirement = retire_direct_smelter_plan(
        "copper-plate", "copper-ore", (61.5, 24.5), "north",
    )
    actions = retirement["phases"][0]["actions"]

    assert {action["entity"] for action in actions} == {
        "electric-mining-drill",
        "electric-furnace",
        "inserter",
        "passive-provider-chest",
    }
    assert all(action["action_type"] == "remove_entity" for action in actions)


def test_new_direct_starter_stages_power_before_its_blueprint(monkeypatch) -> None:
    class _LiveClient:
        def command(self, _text: str) -> str:
            return ""

    events: list[str] = []
    starter = live_base.DirectPlateStarter((54.5, -64.5), "north", 1)
    coverage_reservations: list[set[tuple[int, int]]] = []
    monkeypatch.setattr(
        builder, "_ensure_plan_construction_coverage",
        lambda *_a, **kwargs: (
            events.append("coverage")
            or coverage_reservations.append(kwargs["reserved_tiles"])
        ),
    )
    monkeypatch.setattr(
        builder, "extend_power",
        lambda *_a, **_k: events.append("power") or True,
    )
    monkeypatch.setattr(builder, "assert_affordable", lambda *_a, **_k: None)
    monkeypatch.setattr(
        builder, "_submit",
        lambda *_a, **_k: events.append("blueprint") or {},
    )
    monkeypatch.setattr(builder, "bring_stage_up", lambda *_a, **_k: None)
    monkeypatch.setattr(builder, "_diagnose_machines", lambda *_a, **_k: [])
    monkeypatch.setattr(builder, "_record_bootstrap_pioneer", lambda *_a: None)

    builder._serve_direct_plate_starter(
        _LiveClient(), object(), "nauvis", "player", "stone-brick", "stone",
        starter, lambda _message: None, submit=True,
    )

    assert events[:3] == ["coverage", "power", "blueprint"]
    assert coverage_reservations == [
        builder.planned_footprint_tiles(
            generate_direct_smelter(
                "stone-brick", "stone", (54.5, -64.5), "north",
                pole_side=1,
            )
        )
    ]


def test_uncovered_direct_starter_never_submits_a_power_chain(monkeypatch) -> None:
    class _LiveClient:
        def command(self, _text: str) -> str:
            return ""

    starter = live_base.DirectPlateStarter((54.5, -64.5), "north", 1)
    monkeypatch.setattr(builder, "assert_affordable", lambda *_a, **_k: None)
    monkeypatch.setattr(
        builder, "_ensure_plan_construction_coverage",
        lambda *_a, **_k: (_ for _ in ()).throw(
            builder.StuckError("coverage unavailable")
        ),
    )
    monkeypatch.setattr(
        builder, "extend_power",
        lambda *_a, **_k: pytest.fail("uncovered power chain was submitted"),
    )

    with pytest.raises(builder.StuckError, match="coverage unavailable"):
        builder._serve_direct_plate_starter(
            _LiveClient(), object(), "nauvis", "player",
            "stone-brick", "stone", starter, lambda _message: None,
            submit=True,
        )


def test_retired_starter_power_prunes_only_empty_leaf_branch(monkeypatch) -> None:
    class _LiveClient:
        def command(self, _text: str) -> str:
            return ""

    active = {"starter-a", "starter-b", "bridge", "junction"}
    positions = {
        "starter-a": (0.5, 0.5),
        "starter-b": (0.5, 7.5),
        "bridge": (8.5, 0.5),
        "junction": (16.5, 0.5),
    }
    edges = {
        "starter-a": {"starter-b", "bridge"},
        "starter-b": {"starter-a"},
        "bridge": {"starter-a", "junction"},
        "junction": {"bridge"},
    }

    def context(_client, _surface, position):
        name = next((key for key, value in positions.items() if value == position), None)
        if name not in active:
            return None
        return {
            "name": "medium-electric-pole",
            "supplied": [(20.5, 0.5)] if name == "junction" else [],
            "neighbours": [
                positions[other] for other in edges[name] if other in active
            ],
        }

    removed: list[str] = []

    def retire(_client, _bridge, _surface, _force, plan, *_args, **_kwargs):
        position = plan["phases"][0]["actions"][0]["position"]
        point = (position["x"], position["y"])
        name = next(key for key, value in positions.items() if value == point)
        active.remove(name)
        removed.append(name)
        return 1

    monkeypatch.setattr(builder.live_base, "pole_context", context)
    monkeypatch.setattr(builder, "retire_entities_via_bots", retire)

    count = builder._retire_unused_starter_power_branch(
        _LiveClient(), object(), "nauvis", "player",
        [("medium-electric-pole", positions["starter-a"]),
         ("medium-electric-pole", positions["starter-b"])],
        "stone-brick", lambda _message: None,
    )

    assert count == 3
    assert set(removed) == {"starter-a", "starter-b", "bridge"}
    assert active == {"junction"}


def test_legacy_logistic_cell_remains_recognizable_for_retirement_only() -> None:
    origin = logistic_smelter_origin(((-29.5, -25.5), (-29.5, -19.5)))
    retirement = retire_logistic_smelter_plan("iron-plate", "iron-ore", origin)
    assert origin == (-31, -26)
    actions = retirement["phases"][0]["actions"]
    assert sum(a["entity"] == "requester-chest" for a in actions) == 1
    assert all(
        action["action_type"] == "remove_entity"
        for action in actions
    )
