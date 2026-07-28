# Path: orchestrator/mine_output_tap.py
# Purpose: Convert a legacy terminal mine chest into a continuous-belt side tap.

from __future__ import annotations

Point = tuple[float, float]


def legacy_output_tap_plan(
    output: Point,
    expansion_step: int,
    *,
    belt_type: str = "fast-transport-belt",
    inserter_type: str = "fast-inserter",
) -> tuple[dict, Point]:
    """Relocate the provider north and continue the mine belt through its old tile."""
    if expansion_step not in {-1, 1}:
        raise ValueError("Mine output direction must be -1 or 1")
    x, belt_y = output
    direction = "west" if expansion_step > 0 else "east"
    old_inserter = (x + expansion_step, belt_y)
    provider = (x, belt_y - 2)
    actions = [
        {"action_type": "place_ghost", "entity": "passive-provider-chest",
         "position": {"x": provider[0], "y": provider[1]}},
        {"action_type": "place_ghost", "entity": inserter_type,
         "position": {"x": x, "y": belt_y - 1}, "direction": "south"},
        {"action_type": "remove_entity", "entity": inserter_type,
         "position": {"x": old_inserter[0], "y": old_inserter[1]}},
        {"action_type": "remove_entity", "entity": "passive-provider-chest",
         "position": {"x": x, "y": belt_y}},
        {"action_type": "remove_entity", "entity": "steel-chest",
         "position": {"x": x, "y": belt_y}},
        {"action_type": "place_ghost", "entity": belt_type,
         "position": {"x": old_inserter[0], "y": old_inserter[1]},
         "direction": direction},
        {"action_type": "place_ghost", "entity": belt_type,
         "position": {"x": x, "y": belt_y}, "direction": direction},
    ]
    return {"phases": [{"name": "mine_output_side_tap", "actions": actions}]}, provider
