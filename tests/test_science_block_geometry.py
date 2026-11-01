# Path: tests/test_science_block_geometry.py
# Purpose: Prove the offline science-block adapter delegates geometry, strips
# only local test power, rejects scripted sources, bills direct actions, and
# never turns an explicit line specification into a player-force build claim.

from __future__ import annotations

import json

import pytest

from planners.infrastructure import strip_local_power
from planners.local_layout_planner import LocalLayoutPlanner
from planners.plan_validation import validate_build_plan
from planners.science_block_geometry import bill_plan, build_science_block_geometry


def _spec(**overrides):
    spec = {
        "recipe": "iron-plate",
        "machine_count": 3,
        "origin_x": 10,
        "origin_y": 20,
        "belt_type": "transport-belt",
        "inserter_type": "fast-inserter",
        "mining_feed": True,
        "feed_style": "chest",
        "direct_bus_ingredients": [],
        "chained_ingredients": [],
        "terminal_collector": True,
    }
    spec.update(overrides)
    return spec


def _actions(plan):
    return [action for phase in plan["phases"] for action in phase["actions"]]


def test_a_mined_line_is_validated_and_powered_only_by_its_caller():
    result = build_science_block_geometry(_spec())

    assert result["counts"] == {
        "ghost_placements": 36,
        "direct_placements": 2,
        "removals": 0,
    }
    assert result["ghost_entities"] == {
        "electric-furnace": 3,
        "electric-mining-drill": 3,
        "fast-inserter": 6,
        "medium-electric-pole": 4,
        "transport-belt": 20,
    }
    assert result["direct_entities"] == {"fast-inserter": 1, "steel-chest": 1}
    assert result["removed_entities"] == {}
    assert {
        action.get("entity") for action in _actions(result["plan"])
    }.isdisjoint({"electric-energy-interface", "substation", "infinity-chest", "infinity-pipe"})
    validate_build_plan(result["plan"])


def test_direct_collector_actions_are_marked_non_executable():
    result = build_science_block_geometry(_spec())

    assert result["has_direct_actions"] is True
    assert result["non_executable_marker"].startswith("NON_EXECUTABLE_OFFLINE_GEOMETRY")
    assert "2 direct entity placement(s)" in result["non_executable_marker"]
    assert result["player_force_build_claim"] is False
    assert set(result["unknowns"]) == {"site", "power", "capacity"}
    assert all(result["unknowns"].values())


def test_a_fully_chained_line_has_no_direct_actions_and_no_marker():
    result = build_science_block_geometry(_spec(
        recipe="electronic-circuit",
        machine_count=2,
        origin_x=0,
        origin_y=0,
        belt_type="express-transport-belt",
        feed_style="chained",
        mining_feed=False,
        chained_ingredients=[0, 1],
        terminal_collector=False,
    ))

    assert result["counts"] == {
        "ghost_placements": 25,
        "direct_placements": 0,
        "removals": 0,
    }
    assert result["has_direct_actions"] is False
    assert result["non_executable_marker"] is None
    assert result["player_force_build_claim"] is False
    validate_build_plan(result["plan"])


def test_remaining_plan_is_the_generator_plan_minus_only_local_power():
    spec = _spec()
    result = build_science_block_geometry(spec)

    raw = LocalLayoutPlanner().generate_line_layout(
        recipe=spec["recipe"],
        machine_count=spec["machine_count"],
        origin_x=spec["origin_x"],
        origin_y=spec["origin_y"],
        mining_feed=spec["mining_feed"],
        belt_type=spec["belt_type"],
        inserter_type=spec["inserter_type"],
        feed_style=spec["feed_style"],
        direct_bus_ingredients=spec["direct_bus_ingredients"],
        chained_ingredients=spec["chained_ingredients"],
        terminal_collector=spec["terminal_collector"],
    )

    assert result["plan"] == strip_local_power(raw)


def test_a_chest_fed_line_is_rejected_for_its_scripted_sources():
    with pytest.raises(ValueError, match="scripted sources"):
        build_science_block_geometry(_spec(
            recipe="iron-gear-wheel",
            machine_count=1,
            mining_feed=False,
            feed_style="chest",
            terminal_collector=False,
        ))


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"machine_count": 0}, "positive integer"),
        ({"machine_count": -2}, "positive integer"),
        ({"machine_count": True}, "positive integer"),
        ({"recipe": "not-a-recipe"}, "No line recipe knowledge"),
        ({"belt_type": "not-a-belt"}, "Unknown belt tier"),
        ({"inserter_type": "burner-inserter"}, "Unknown inserter tier"),
        ({"feed_style": "pipe"}, "Unknown feed style"),
        ({"mining_feed": "yes"}, "explicit boolean"),
        ({"terminal_collector": 1}, "explicit boolean"),
        ({"origin_x": "10"}, "finite number"),
        ({"origin_y": float("nan")}, "finite number"),
        (
            {"recipe": "electronic-circuit", "feed_style": "sideload",
             "direct_bus_ingredients": ["iron-gear-wheel"]},
            "no direct-bus ingredient",
        ),
        (
            {"recipe": "electronic-circuit", "feed_style": "chest",
             "direct_bus_ingredients": ["iron-plate"]},
            "require feed_style='sideload'",
        ),
        (
            {"recipe": "electronic-circuit", "feed_style": "chained",
             "chained_ingredients": []},
            "requires explicit chained ingredient",
        ),
        (
            {"recipe": "electronic-circuit", "feed_style": "chest",
             "chained_ingredients": [0]},
            "only valid with feed_style='chained'",
        ),
        (
            {"recipe": "electronic-circuit", "feed_style": "chained",
             "chained_ingredients": [9]},
            "out of range",
        ),
        (
            {"recipe": "electronic-circuit", "feed_style": "chained",
             "chained_ingredients": [True]},
            "non-negative integers",
        ),
        (
            {"recipe": "electronic-circuit", "feed_style": "chained",
             "chained_ingredients": [0, 0]},
            "must be unique",
        ),
        ({"direct_bus_ingredients": "iron-plate"}, "list or tuple"),
    ],
)
def test_invalid_specs_are_rejected_before_geometry(overrides, message):
    with pytest.raises(ValueError, match=message):
        build_science_block_geometry(_spec(**overrides))


def test_missing_and_unknown_spec_fields_are_rejected():
    missing = _spec()
    missing.pop("belt_type")
    with pytest.raises(ValueError, match="missing explicit field"):
        build_science_block_geometry(missing)

    unknown = _spec()
    unknown["flow_direction"] = "east"
    with pytest.raises(ValueError, match="unknown field"):
        build_science_block_geometry(unknown)


def test_a_self_colliding_generated_plan_is_rejected(monkeypatch):
    colliding = {
        "phases": [{
            "name": "bad_geometry",
            "actions": [
                {"action_type": "place_ghost", "entity": "electric-furnace",
                 "position": {"x": 1.5, "y": 3.5}},
                {"action_type": "place_ghost", "entity": "electric-furnace",
                 "position": {"x": 1.5, "y": 3.5}},
            ],
        }],
    }
    monkeypatch.setattr(
        LocalLayoutPlanner, "generate_line_layout",
        lambda self, **kwargs: colliding,
    )

    with pytest.raises(ValueError, match="collision"):
        build_science_block_geometry(_spec())


def test_billing_counts_ghosts_direct_placements_and_removals_grossly():
    plan = {
        "phases": [{
            "name": "mixed",
            "actions": [
                {"action_type": "place_ghost", "entity": "transport-belt",
                 "position": {"x": 0.5, "y": 0.5}},
                {"action_type": "place_ghost", "entity": "transport-belt",
                 "position": {"x": 1.5, "y": 0.5}},
                {"action_type": "place_tile_ghost", "tile": "landfill",
                 "position": {"x": 2, "y": 2}},
                {"action_type": "place_entity", "entity": "steel-chest",
                 "position": {"x": 3.5, "y": 6.5}},
                {"action_type": "remove_entity", "entity": "fast-inserter",
                 "position": {"x": 3.5, "y": 6.5}},
                {"action_type": "remove_entity", "entity": "steel-chest",
                 "position": {"x": 4.5, "y": 6.5}},
                {"action_type": "configure_entity", "entity": "assembling-machine-2",
                 "position": {"x": 1.5, "y": 3.5}, "recipe": "iron-gear-wheel"},
            ],
        }],
    }

    bill = bill_plan(plan)

    assert bill["ghost_placements"] == 3
    assert bill["direct_placements"] == 1
    assert bill["removals"] == 2
    assert bill["ghost_entities"] == {"landfill": 1, "transport-belt": 2}
    assert bill["direct_entities"] == {"steel-chest": 1}
    assert bill["removed_entities"] == {"fast-inserter": 1, "steel-chest": 1}


def test_output_is_byte_stable_for_the_same_explicit_spec():
    first = build_science_block_geometry(_spec())
    second = build_science_block_geometry(_spec())

    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_requester_style_emits_only_bot_buildable_ghosts_sized_from_demand():
    result = build_science_block_geometry(_spec(
        recipe="automation-science-pack", machine_count=2, mining_feed=False,
        feed_style="requester", inserter_type="inserter", machine="assembling-machine-1",
    ))

    assert result["direct_entities"] == {} and result["has_direct_actions"] is False
    entities = [a for a in _actions(result["plan"]) if a["entity"] == "requester-chest"]
    assert {a["logistic_requests"][0]["name"] for a in entities} == {"copper-plate", "iron-gear-wheel"}
    assert result["ghost_entities"]["assembling-machine-1"] == 2
    assert result["ghost_entities"]["passive-provider-chest"] == 1
    assert {"infinity-chest", "steel-chest", "assembling-machine-2"}.isdisjoint(result["ghost_entities"])
    assert result["spec"]["machine"] == "assembling-machine-1"


def test_machine_override_is_optional_and_validated():
    assert "machine" not in build_science_block_geometry(_spec())["spec"]
    with pytest.raises(ValueError, match="Unknown machine"):
        build_science_block_geometry(_spec(machine="nonexistent-machine"))
    with pytest.raises(ValueError, match="only valid between assembling machines"):
        build_science_block_geometry(_spec(machine="assembling-machine-1"))
