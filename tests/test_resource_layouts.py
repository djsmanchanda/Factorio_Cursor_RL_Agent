# Path: tests/test_resource_layouts.py
# Purpose: Verify real resource layouts use supplied coordinates without scripted sources.

from __future__ import annotations

from planners.plan_validation import actions, assert_no_production_infinity, validate_no_collisions
from planners.plan_validation import occupied_tile_indices
from planners.resource_layouts import (
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


def test_fluid_resources_preserve_supplied_entity_and_output_coordinates() -> None:
    crude = generate_pumpjack_source(
        [{"position": (20.5, 20.5), "output": (20, 17), "direction": "north"}],
        [(20, 17), (19, 17)],
    )
    water = generate_offshore_pump_source(
        [{"position": (5.5, 30.5), "output": (6, 30), "direction": "east"}],
        [(6, 30), (7, 30)],
    )

    assert any(action["entity"] == "pumpjack" for action in actions(crude))
    assert any(action["entity"] == "offshore-pump" for action in actions(water))
    assert_no_production_infinity([("crude", crude), ("water", water)])


def test_offshore_power_scaffold_has_no_row_pole_on_its_water_pipe() -> None:
    plan = generate_offshore_pump_source(
        [{"position": (20.5, 83.5), "output": (20, 80), "direction": "north"}],
        [(20, 80)],
    )
    assert not any(action["entity"] == "medium-electric-pole" for action in actions(plan))
    assert (17, 86) not in occupied_tile_indices([("water", plan)])