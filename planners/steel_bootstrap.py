# Path: planners/steel_bootstrap.py
# Purpose: Compact belt-side or direct-mining steel seeds without feed belts.
from __future__ import annotations

VECTORS = {"north": (0, -1), "east": (1, 0), "south": (0, 1), "west": (-1, 0)}
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}


def steel_seed(source, direction, *, mined=False, pole_side=1):
    dx, dy = VECTORS[direction]
    def point(distance, lateral=0):
        return (source[0] + dx * distance - dy * lateral,
                source[1] + dy * distance + dx * lateral)
    actions = []
    def place(name, position, **extra):
        actions.append({"action_type": "place_ghost", "entity": name,
                        "position": {"x": position[0], "y": position[1]}, **extra})
    offset = 4 if mined else 0
    if mined:
        place("electric-mining-drill", source, direction=direction)
        # Furnaces infer recipes from input; explicit set_recipe is invalid.
        place("electric-furnace", point(3))
        place("medium-electric-pole", point(1, 2 * pole_side))
    place("inserter", point(1 + offset), direction=OPPOSITE[direction])
    place("electric-furnace", point(3 + offset))
    place("inserter", point(5 + offset), direction=OPPOSITE[direction])
    place("passive-provider-chest", point(6 + offset))
    place("medium-electric-pole", point(3 + offset, 2 * pole_side))
    return {"phases": [{"name": "compact_steel_seed", "actions": actions}]}
