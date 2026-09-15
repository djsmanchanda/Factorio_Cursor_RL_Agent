# Path: planners/quad_mall_layout.py
# Purpose: Four-machine bootstrap mall module sharing one requester and two output providers.

from __future__ import annotations

from planners.mall_layout import (
    _inventory_limit,
    compact_input_inserter,
    compact_output_inserter,
    recipe_group_name,
    recipe_group_requests,
)
from planners.stock_gating import stock_gate


QUAD_MALL_MACHINE_OFFSETS = {
    "north": (0.0, -5.0),
    "west": (-3.0, 0.0),
    "east": (3.0, 0.0),
    "south": (0.0, 5.0),
}
QUAD_MALL_PROVIDER_OFFSETS = {"top": (0.0, -1.0), "bottom": (0.0, 1.0)}
QUAD_MALL_GROUP_MACHINES = {
    "top": ("north", "west"),
    "bottom": ("east", "south"),
}


def _position(center: tuple[float, float], offset: tuple[float, float]) -> dict:
    return {"x": center[0] + offset[0], "y": center[1] + offset[1]}


def generate_quad_mall_layout(
    recipe: str, machine: str, ingredients: list[str], amounts: list[float],
    center: tuple[float, float], group: str, *, stock_target: int = 1,
    product_amount: float = 1, craft_time: float, stock_gate_target: int | None = None,
    fill_chest: bool = False, set_recipe: bool = True,
    request_multiplier_override: int | None = None,
    inserter_type: str | None = None,
) -> dict:
    """Build one two-machine half of the four-machine cross module.

    The top and bottom halves each share one passive provider. The central
    requester carries one labelled section per half, so four assemblers consume
    one requester while outputs remain separated by recipe.
    """
    if group not in QUAD_MALL_GROUP_MACHINES:
        raise ValueError("Quad mall group must be 'top' or 'bottom'")
    section = {
        "group": recipe_group_name(recipe, group),
        "requests": recipe_group_requests(ingredients, amounts),
        "multiplier": request_multiplier_override or max(
            1, round(15.0 * (0.5 / craft_time))
        ),
    }
    actions: list[dict] = []
    if group == "top":
        actions.append({
            "action_type": "place_entity", "entity": "substation",
            "position": _position(center, (5.0, 5.0)),
        })
        actions.append({
            "action_type": "place_entity", "entity": "requester-chest",
            "position": _position(center, (0.0, 0.0)),
            "logistic_sections": [section],
        })
    provider = _position(center, QUAD_MALL_PROVIDER_OFFSETS[group])
    actions.append({
        "action_type": "place_entity", "entity": "passive-provider-chest",
        "position": provider,
        "inventory_limit": _inventory_limit(recipe, stock_target, fill_chest=fill_chest),
    })
    input_inserter = inserter_type or compact_input_inserter(
        machine, ingredients, amounts, craft_time,
    )
    output_inserter = inserter_type or compact_output_inserter(
        machine, product_amount, craft_time,
    )
    for index, side in enumerate(QUAD_MALL_GROUP_MACHINES[group]):
        machine_position = _position(center, QUAD_MALL_MACHINE_OFFSETS[side])
        machine_action = {
            "action_type": "place_ghost", "entity": machine,
            "position": machine_position,
        }
        if set_recipe:
            machine_action["recipe"] = recipe
        if stock_gate_target is not None:
            machine_action["logistic_condition"] = stock_gate(recipe, stock_gate_target)
        actions.append(machine_action)
        if side in {"north", "south"}:
            input_position = _position(center, (0.0, -3.0 if side == "north" else 3.0))
            output_position = _position(center, (0.0, -2.0 if side == "north" else 2.0))
            direction = "south" if side == "north" else "north"
            actions.extend([
                {"action_type": "place_entity", "entity": "long-handed-inserter",
                 "position": input_position, "direction": direction},
                {"action_type": "place_entity", "entity": "long-handed-inserter",
                 "position": output_position, "direction": direction},
            ])
        else:
            x = -1.0 if side == "west" else 1.0
            direction = "east" if side == "west" else "west"
            actions.extend([
                {"action_type": "place_entity", "entity": input_inserter,
                 "position": _position(center, (x, 0.0)),
                 "direction": direction},
                {"action_type": "place_entity", "entity": output_inserter,
                 "position": _position(center, (x, -1.0 if side == "west" else 1.0)),
                 "direction": "west" if side == "west" else "east"},
            ])
    return {"phases": [{"name": f"quad_mall_{recipe}_{group}", "actions": actions}]}
