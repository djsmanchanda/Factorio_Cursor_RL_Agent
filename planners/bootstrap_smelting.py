# Path: planners/bootstrap_smelting.py
# Purpose: Direct plate starter geometry plus legacy requester-cell retirement.

from __future__ import annotations


_DIRECTION_VECTORS = {
    "north": (0.0, -1.0),
    "east": (1.0, 0.0),
    "south": (0.0, 1.0),
    "west": (-1.0, 0.0),
}
_OPPOSITE_DIRECTION = {
    "north": "south",
    "east": "west",
    "south": "north",
    "west": "east",
}


def direct_smelter_positions(
    drill_position: tuple[float, float],
    output_direction: str,
    *,
    pole_side: int = 1,
    drill_count: int = 1,
    furnace_count: int = 1,
) -> dict[str, tuple[float, float]]:
    """Exact direct starter geometry, aligned to the primary drill output.

    The drill outputs directly into the furnace. One inserter publishes plates
    to a provider chest. ``pole_side`` selects either side of the drill/furnace
    axis so site selection can route around a local obstacle. A two-drill
    starter places its second drill perpendicular to the furnace, opposite the
    first pole, leaving the furnace output axis clear.
    """
    if output_direction not in _DIRECTION_VECTORS:
        raise ValueError(f"Unsupported starter direction {output_direction!r}")
    if pole_side not in {-1, 1}:
        raise ValueError("pole_side must be -1 or 1")
    if drill_count not in {1, 2} or furnace_count not in {1, 2}:
        raise ValueError("direct starter supports one or two drills")
    if furnace_count > drill_count:
        raise ValueError("direct starter cannot have more furnaces than drills")
    x, y = drill_position
    dx, dy = _DIRECTION_VECTORS[output_direction]
    # Perpendicular to the output axis. For a north-facing drill, pole_side=1
    # reproduces the live reference pole two tiles west of the drill.
    px, py = dy * pole_side, -dx * pole_side
    positions = {
        "drill": (x, y),
        "furnace": (x + 3 * dx, y + 3 * dy),
        "inserter": (x + 5 * dx, y + 5 * dy),
        "provider": (x + 6 * dx, y + 6 * dy),
        "power": (x + 2 * dx + 2 * px, y + 2 * dy + 2 * py),
    }
    if drill_count == 2:
        furnace_x, furnace_y = positions["furnace"]
        positions["secondary_drill"] = (
            furnace_x - 3 * px, furnace_y - 3 * py,
        )
        positions["secondary_power"] = (
            furnace_x - 4 * px + 3 * dx,
            furnace_y - 4 * py + 3 * dy,
        )
    if furnace_count == 2:
        qx, qy = dx, dy
        provider_x, provider_y = positions["provider"]
        secondary_drill = (provider_x + 6 * qx, provider_y + 6 * qy)
        positions.update({
            "secondary_inserter": (provider_x + qx, provider_y + qy),
            "secondary_furnace": (provider_x + 3 * qx, provider_y + 3 * qy),
            "secondary_drill": secondary_drill,
            "secondary_power": (
                secondary_drill[0] + 2 * px + 2 * py * pole_side,
                secondary_drill[1] + 2 * py - 2 * px * pole_side,
            ),
        })
    return positions


def _direction_for_vector(dx: float, dy: float) -> str:
    return next(
        direction for direction, vector in _DIRECTION_VECTORS.items()
        if vector == (dx, dy)
    )


def generate_direct_smelter(
    recipe: str,
    ore: str,
    drill_position: tuple[float, float],
    output_direction: str,
    *,
    pole_side: int = 1,
) -> dict:
    """Build the removable direct plate or brick starter."""
    expected_ore = {
        "iron-plate": "iron-ore",
        "copper-plate": "copper-ore",
        "stone-brick": "stone",
    }.get(recipe)
    if expected_ore != ore:
        raise ValueError(f"Direct smelter does not support {recipe!r} from {ore!r}")
    drill_count = 2 if recipe in {"iron-plate", "stone-brick"} else 1
    furnace_count = 2 if recipe == "iron-plate" else 1
    positions = direct_smelter_positions(
        drill_position, output_direction, pole_side=pole_side,
        drill_count=drill_count,
        furnace_count=furnace_count,
    )
    actions = [
        {
            "action_type": "place_ghost",
            "entity": "medium-electric-pole",
            "position": {"x": positions["power"][0], "y": positions["power"][1]},
        },
    ]
    if drill_count == 2:
        dx, dy = _DIRECTION_VECTORS[output_direction]
        px, py = dy * pole_side, -dx * pole_side
        actions.extend([{
            "action_type": "place_ghost",
            "entity": "medium-electric-pole",
            "position": {
                "x": positions["secondary_power"][0],
                "y": positions["secondary_power"][1],
            },
        }, {
            "action_type": "place_ghost",
            "entity": "electric-mining-drill",
            "position": {
                "x": positions["secondary_drill"][0],
                "y": positions["secondary_drill"][1],
            },
                "direction": (
                    _OPPOSITE_DIRECTION[output_direction]
                    if furnace_count == 2
                    else _direction_for_vector(px, py)
                ),
        }])
    actions.extend([
        {
            "action_type": "place_ghost",
            "entity": "electric-mining-drill",
            "position": {"x": positions["drill"][0], "y": positions["drill"][1]},
            "direction": output_direction,
        },
        {
            "action_type": "place_ghost",
            "entity": "electric-furnace",
            "position": {"x": positions["furnace"][0], "y": positions["furnace"][1]},
        },
        {
            "action_type": "place_ghost",
            "entity": "fast-inserter",
            "position": {"x": positions["inserter"][0], "y": positions["inserter"][1]},
            "direction": _OPPOSITE_DIRECTION[output_direction],
        },
        {
            "action_type": "place_ghost",
            "entity": "passive-provider-chest",
            "position": {"x": positions["provider"][0], "y": positions["provider"][1]},
        },
    ])
    if furnace_count == 2:
        secondary_direction = _direction_for_vector(-dx, -dy)
        actions.extend([{
            "action_type": "place_ghost",
            "entity": "electric-furnace",
            "position": {
                "x": positions["secondary_furnace"][0],
                "y": positions["secondary_furnace"][1],
            },
        }, {
            "action_type": "place_ghost",
            "entity": "fast-inserter",
            "position": {
                "x": positions["secondary_inserter"][0],
                "y": positions["secondary_inserter"][1],
            },
            "direction": _OPPOSITE_DIRECTION[secondary_direction],
        }])
    return {
        "phases": [{"name": f"direct_{recipe}_starter", "actions": actions}],
        "starter_geometry": {
            "recipe": recipe,
            "ore": ore,
            "direction": output_direction,
            "pole_side": pole_side,
            "drill_position": [drill_position[0], drill_position[1]],
            "drill_count": drill_count,
            "furnace_count": furnace_count,
        },
    }


def retire_direct_smelter_plan(
    recipe: str,
    ore: str,
    drill_position: tuple[float, float],
    output_direction: str,
    *,
    pole_side: int = 1,
) -> dict:
    """Remove the starter's production stack after its direct replacement works.

    Its medium pole is deliberately retained: by migration time it may be part
    of the parent grid or construction coverage, while the production entities
    are uniquely owned by the recorded starter geometry.
    """
    plan = generate_direct_smelter(
        recipe, ore, drill_position, output_direction, pole_side=pole_side,
    )
    actions = [
        {
            "action_type": "remove_entity",
            "entity": action["entity"],
            "position": action["position"],
        }
        for action in plan["phases"][0]["actions"]
        if action["entity"] != "medium-electric-pole"
    ]
    return {
        "phases": [{
            "name": f"retire_direct_{recipe}_starter",
            "actions": actions,
        }],
    }


def _legacy_logistic_smelter_actions(
    recipe: str,
    ore: str,
    origin: tuple[int, int],
) -> list[dict]:
    """Describe the retired requester layout solely for exact teardown."""
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
    return actions

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
    return {"phases": [{
        "name": f"retire_logistic_{recipe}_bootstrap",
        "actions": [
            {
                "action_type": "remove_entity",
                "entity": action["entity"],
                "position": action["position"],
            }
            for action in _legacy_logistic_smelter_actions(recipe, ore, origin)
        ],
    }]}
