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


def mined_seed_interfaces(plan):
    """Recognize constructor-owned geometry before attaching another feed."""
    actions = [a for p in plan["phases"] for a in p["actions"]]
    drills = [a for a in actions if a.get("entity") == "electric-mining-drill"]
    if not drills:
        return None
    if len(drills) != 1:
        raise ValueError("steel seed must have exactly one owned drill")
    drill = drills[0]
    source = (drill["position"]["x"], drill["position"]["y"])
    if not any(plan["phases"] == steel_seed(source, drill["direction"], mined=True,
                                          pole_side=side)["phases"] for side in (-1, 1)):
        raise ValueError("steel seed differs from its constructor contract")
    return {
        "drill": drill,
        "furnace": [a for a in actions if a["entity"] == "electric-furnace"][-1],
        "provider": next(a for a in actions if a["entity"] == "passive-provider-chest"),
        "poles": [a for a in actions if a["entity"] == "medium-electric-pole"],
    }


def steel_seed_intakes(plan):
    """Edge inserters for the existing furnace, inside its existing pole supply.

    The centre of a 3x3 furnace is not a chest-sized belt destination. Each
    candidate explicitly owns the outside inserter and the belt behind it.
    Live occupancy and shared belt routing decide which candidates are legal.
    """
    interfaces = mined_seed_interfaces(plan)
    if interfaces is None:
        return []
    centre = interfaces["furnace"]["position"]
    result = []
    for side, (dx, dy) in VECTORS.items():
        for lateral in (-1, 0, 1):
            x = centre["x"] + 2 * dx - lateral * dy
            y = centre["y"] + 2 * dy + lateral * dx
            # Medium poles supply a 7x7 square. Keep the full one-tile
            # inserter inside an owned pole's supply; add no power scaffolding.
            if not any(abs(x - p["position"]["x"]) <= 3
                       and abs(y - p["position"]["y"]) <= 3 for p in interfaces["poles"]):
                continue
            result.append(((x + dx, y + dy), OPPOSITE[side], {
                "action_type": "place_ghost", "entity": "inserter",
                "position": {"x": x, "y": y}, "direction": side,
            }))
    return result


def steel_seed_source_taps(source, heading):
    """Side pickups keep the live terminal's through exit free for expansion."""
    dx, dy = VECTORS[heading]
    for side in (-1, 1):
        px, py = -dy * side, dx * side
        outward = next(name for name, vector in VECTORS.items() if vector == (px, py))
        inserter = {"action_type": "place_ghost", "entity": "inserter",
            "position": {"x": source[0] + px, "y": source[1] + py}, "direction": OPPOSITE[outward]}
        belt = {"action_type": "place_ghost", "entity": "transport-belt",
            "position": {"x": source[0] + 2 * px, "y": source[1] + 2 * py}, "direction": outward}
        yield (source[0] + 2 * px, source[1] + 2 * py), outward, [inserter, belt]


def steel_seed_through_handoff(source, heading):
    """The existing district splitter and either branch retain their clear seam."""
    import math
    dx, dy = VECTORS[heading]
    return {(math.floor(source[0] + distance * dx - lateral * dy),
             math.floor(source[1] + distance * dy + lateral * dx))
            for distance in (1, 2) for lateral in (-1, 0, 1)}
