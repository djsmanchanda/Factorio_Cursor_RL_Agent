# Path: planners/bootstrap_smelting.py
# Purpose: Deterministic beltless plate-smelting layout for construction bootstrap.

from __future__ import annotations


def generate_logistic_smelter(
    recipe: str,
    ore: str,
    origin: tuple[int, int],
) -> dict:
    """Build two furnaces around one shared requester without any belts."""
    if recipe not in {"iron-plate", "copper-plate"}:
        raise ValueError(f"Logistic smelter does not support {recipe!r}")

    ox, oy = origin
    x = ox + 1.5
    actions: list[dict] = [{
        "action_type": "place_entity",
        "entity": "substation",
        "position": {"x": ox - 4.0, "y": oy + 3.5},
    }, {
        "action_type": "place_entity",
        "entity": "requester-chest",
        "position": {"x": x, "y": oy + 3.5},
        "logistic_request": {"name": ore, "count": 50},
    }]
    for furnace_y, input_y, output_y, provider_y, direction in (
        (oy + 0.5, oy + 2.5, oy - 1.5, oy - 2.5, "south"),
        (oy + 6.5, oy + 4.5, oy + 8.5, oy + 9.5, "north"),
    ):
        actions.extend([
            {"action_type": "place_entity", "entity": "fast-inserter",
             "position": {"x": x, "y": input_y}, "direction": direction},
            {"action_type": "place_ghost", "entity": "electric-furnace",
             "position": {"x": x, "y": furnace_y}},
            {"action_type": "place_entity", "entity": "fast-inserter",
             "position": {"x": x, "y": output_y}, "direction": direction},
            {"action_type": "place_entity", "entity": "passive-provider-chest",
             "position": {"x": x, "y": provider_y}},
        ])
    return {"phases": [{"name": f"logistic_{recipe}_bootstrap", "actions": actions}]}

def logistic_smelter_origin(
    machine_positions: tuple[tuple[float, float], ...],
) -> tuple[int, int] | None:
    """Recognize the two-furnace bootstrap geometry."""
    if len(machine_positions) != 2:
        return None
    ordered = sorted(machine_positions, key=lambda point: point[1])
    if ordered[0][0] != ordered[1][0] or ordered[1][1] - ordered[0][1] != 6:
        return None
    return round(ordered[0][0] - 1.5), round(ordered[0][1] - 0.5)


def retire_logistic_smelter_plan(recipe: str, ore: str, origin: tuple[int, int]) -> dict:
    """Remove only the entities belonging to a recognized bootstrap cell."""
    plan = generate_logistic_smelter(recipe, ore, origin)
    return {"phases": [{
        "name": f"retire_logistic_{recipe}_bootstrap",
        "actions": [
            {
                "action_type": "remove_entity",
                "entity": action["entity"],
                "position": action["position"],
            }
            for action in plan["phases"][0]["actions"]
        ],
    }]}