# Path: tests/test_resource_layouts.py
# Purpose: Verify real resource layouts use supplied coordinates without scripted sources.

from __future__ import annotations

import pytest

from planners.plan_validation import actions, assert_no_production_infinity, validate_no_collisions
from planners.plan_validation import occupied_tile_indices
from planners.resource_layouts import (
    generate_direct_mining_to_chest,
    generate_coal_mine,
    generate_offshore_pump_source,
    generate_pumpjack_source,
)


def test_coal_mine_drops_to_declared_output_row_deterministically() -> None:
    kwargs = {"drill_positions": [(1.5, 10.5), (4.5, 10.5)],
              "output_y": 12.5, "output_x": 8.5}
    plan = generate_coal_mine(**kwargs)

    assert plan == generate_coal_mine(**kwargs)
    drills = [action for action in actions(plan) if action["entity"] == "electric-mining-drill"]
    assert [action["position"] for action in drills] == [
        {"x": 1.5, "y": 10.5}, {"x": 4.5, "y": 10.5},
    ]
    assert_no_production_infinity([("coal", plan)])
    validate_no_collisions([("coal", plan)])


def test_coal_layout_defaults_to_an_early_game_belt() -> None:
    plan = generate_coal_mine(
        [(1.5, 10.5), (4.5, 10.5)], 12.5, 8.5,
    )

    assert {
        action["entity"] for action in actions(plan)
        if action["entity"].endswith("transport-belt")
    } == {"transport-belt"}

def test_direct_mining_to_chest_is_real_electric_output_primitive() -> None:
    plan = generate_direct_mining_to_chest(
        [(1.5, 10.5), (4.5, 10.5)], (8.5, 12.5),
    )

    assert plan == generate_direct_mining_to_chest(
        [(4.5, 10.5), (1.5, 10.5)], (8.5, 12.5),
    )
    actions_by_entity = {
        entity: [action for action in actions(plan) if action["entity"] == entity]
        for entity in {action["entity"] for action in actions(plan)}
    }
    assert [action["position"] for action in actions_by_entity["electric-mining-drill"]] == [
        {"x": 1.5, "y": 10.5}, {"x": 4.5, "y": 10.5},
    ]
    assert [action["position"] for action in actions_by_entity["fast-transport-belt"]] == [
        {"x": -2.5, "y": 12.5}, {"x": -1.5, "y": 12.5},
        {"x": -0.5, "y": 12.5}, {"x": 0.5, "y": 12.5},
        {"x": 1.5, "y": 12.5}, {"x": 2.5, "y": 12.5},
        {"x": 3.5, "y": 12.5}, {"x": 4.5, "y": 12.5},
        {"x": 5.5, "y": 12.5}, {"x": 6.5, "y": 12.5},
        {"x": 7.5, "y": 12.5}, {"x": 8.5, "y": 12.5},
    ]
    assert actions_by_entity["fast-inserter"] == [{
        "action_type": "place_ghost", "entity": "fast-inserter",
        "position": {"x": 8.5, "y": 11.5}, "direction": "south",
    }]
    assert actions_by_entity["steel-chest"] == [{
        "action_type": "place_ghost", "entity": "steel-chest",
        "position": {"x": 8.5, "y": 10.5},
    }]
    assert "electric-energy-interface" not in actions_by_entity
    assert_no_production_infinity([("raw", plan)])
    validate_no_collisions([("raw", plan)])


@pytest.mark.parametrize(
    ("drills", "chest", "message"),
    [
        ([], (8.5, 12.5), "needs supplied"),
        ([(1.5, 10.5)], (8.5, 13.5), "south output row"),
        ([(1.5, 10.5), (4.5, 10.5)], (3.5, 12.5), "east of every drill"),
    ],
)
def test_direct_mining_to_chest_rejects_invalid_geometry(
    drills: list[tuple[float, float]], chest: tuple[float, float], message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        generate_direct_mining_to_chest(drills, chest)


def test_fluid_resources_preserve_supplied_entity_and_output_coordinates() -> None:
    crude = generate_pumpjack_source(
        [{"position": (18.5, 20.5), "output": (16, 21), "direction": "west"}],
        [(16, 21), (15, 21)],
    )
    water = generate_offshore_pump_source(
        [{"position": (5.5, 30.5), "output": (7, 30), "direction": "west"}],
        [(7, 30), (8, 30)],
    )

    assert any(action["entity"] == "pumpjack" for action in actions(crude))
    assert any(action["entity"] == "offshore-pump" for action in actions(water))
    assert_no_production_infinity([("crude", crude), ("water", water)])


def test_west_pumpjack_requires_its_live_verified_output_tile() -> None:
    site = {"position": (18.5, -43.5), "output": (16, -43), "direction": "west"}
    plan = generate_pumpjack_source([site], [(16, -43)])

    pumpjack = next(action for action in actions(plan) if action["entity"] == "pumpjack")
    pipe = next(action for action in actions(plan) if action["entity"] == "pipe")
    assert pumpjack == {
        "action_type": "place_ghost", "entity": "pumpjack",
        "position": {"x": 18.5, "y": -43.5}, "direction": "west",
    }
    assert pipe["position"] == {"x": 16.5, "y": -42.5}
    validate_no_collisions([("crude", plan)])

    site["output"] = (20, -47)
    with pytest.raises(ValueError, match="rotated connector tile"):
        generate_pumpjack_source([site], [(20, -47)])


def test_fluid_sources_reject_the_old_overlapping_output_tiles() -> None:
    with pytest.raises(ValueError, match="rotated connector tile"):
        generate_pumpjack_source(
            [{"position": (-268.5, -98.5), "output": (-268, -100),
              "direction": "east"}],
            [(-268, -100)],
        )
    with pytest.raises(ValueError, match="adjacent land-side tile"):
        generate_offshore_pump_source(
            [{"position": (-97.5, 15.5), "output": (-98, 14),
              "direction": "south"}],
            [(-98, 14)],
        )


def test_offshore_power_scaffold_has_no_row_pole_on_its_water_pipe() -> None:
    plan = generate_offshore_pump_source(
        [{"position": (20.5, 83.5), "output": (20, 81), "direction": "south"}],
        [(20, 81)],
    )
    assert not any(action["entity"] == "medium-electric-pole" for action in actions(plan))
    assert not any(action["entity"] == "substation" for action in actions(plan))
    assert (17, 80) not in occupied_tile_indices([("water", plan)])


@pytest.mark.parametrize(
    ("direction", "output"),
    [
        ("north", (17, 19)), ("east", (20, 20)),
        ("south", (19, 23)), ("west", (16, 22)),
    ],
)
def test_pumpjack_connector_rotates_with_the_machine(
    direction: str, output: tuple[int, int],
) -> None:
    site = {"position": (18.5, 21.5), "output": output, "direction": direction}

    validate_no_collisions([
        ("crude", generate_pumpjack_source([site], [output])),
    ])
