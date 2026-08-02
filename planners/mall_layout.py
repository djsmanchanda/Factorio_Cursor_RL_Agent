# Path: planners/mall_layout.py
# Purpose: Compact centralized mall cells with shared logistic input and tier-chain transfer.

from __future__ import annotations

import math

from planners.recipe_data import FEED_HEADROOM, MACHINE_SPEEDS, inserter_for_demand
from planners.stock_gating import stock_gate

MALL_MINIMUM_STACKS = {
    "production": 4,
    "logistics": 5,
    "intermediate-products": 10,
}

# A requester holds this many seconds of the machine's own consumption. Sizing
# the buffer in TIME rather than in finished goods keeps it correct as machines
# get faster: the same ten-second window simply resolves to a bigger number.
MALL_SUPPLY_SECONDS = 10.0


def _inventory_limit(recipe: str, stock_target: int) -> dict:
    return {
        "name": recipe,
        "count": stock_target,
        "growth_stacks": 2,
        "minimum_stacks_by_group": dict(MALL_MINIMUM_STACKS),
    }


def compact_requests(
    ingredients: list[str], amounts: list[float], stock_target: int,
    product_amount: float,
) -> list[dict]:
    """Scale one request slot per ingredient to the requested output stock."""
    if len(ingredients) != len(amounts) or not ingredients:
        raise ValueError("Compact mall ingredients and amounts must align")
    if stock_target < 1 or product_amount <= 0:
        raise ValueError("Compact mall output quantities must be positive")
    crafts = math.ceil(stock_target / product_amount)
    return [
        {"name": ingredient, "count": max(1, math.ceil(amount * crafts))}
        for ingredient, amount in zip(ingredients, amounts)
    ]


def recipe_group_name(recipe: str) -> str:
    """The label a recipe's ingredient set carries on any chest that feeds it."""
    return f"mall:{recipe}"


def machine_crafts_per_second(machine: str, craft_time: float) -> float:
    """How fast this machine actually runs the recipe, in crafts per second."""
    if craft_time <= 0:
        raise ValueError("Recipe craft time must be positive")
    speed = MACHINE_SPEEDS.get(machine)
    if speed is None:
        raise ValueError(
            f"No crafting speed known for {machine!r}; add it to MACHINE_SPEEDS "
            "so its mall cell can size its own input request"
        )
    return speed / craft_time


def recipe_group_requests(ingredients: list[str], amounts: list[float]) -> list[dict]:
    """One craft's worth of each ingredient -- the group's canonical contents.

    The group holds the PER-CRAFT amounts and each chest's section scales them
    with a multiplier, which is what makes one named group reusable across mall
    cells that stock different quantities of the same part.
    """
    if len(ingredients) != len(amounts) or not ingredients:
        raise ValueError("Compact mall ingredients and amounts must align")
    return [
        {"name": ingredient, "count": max(1, math.ceil(amount))}
        for ingredient, amount in zip(ingredients, amounts)
    ]


def request_multiplier(machine: str, craft_time: float) -> int:
    """Crafts the machine completes in MALL_SUPPLY_SECONDS, rounded up.

    Because the group holds one craft's worth of each ingredient, this scales
    the whole request to a fixed WINDOW OF RUNNING TIME rather than to a
    finished-goods target: the chest holds what the machine will actually eat
    in ten seconds. A faster machine consumes faster, so it asks for
    proportionally more, and an upgraded cell re-derives its own buffer with no
    separate retuning step.
    """
    return max(1, math.ceil(MALL_SUPPLY_SECONDS * machine_crafts_per_second(machine, craft_time)))


def recipe_logistic_section(
    recipe: str, ingredients: list[str], amounts: list[float],
    *, machine: str, craft_time: float,
) -> dict:
    """One labelled request group for the machines producing `recipe`.

    A chest feeding two machines carries one of these per machine rather than a
    single merged slot list: the game sums the sections, the label says which
    machine each set is for, and rebuilding one machine rewrites only its own
    section instead of accumulating onto whatever the chest already held.
    """
    return {
        "group": recipe_group_name(recipe),
        "requests": recipe_group_requests(ingredients, amounts),
        "multiplier": request_multiplier(machine, craft_time),
    }


def compact_input_inserter(machine: str, ingredients: list[str],
                           amounts: list[float], craft_time: float) -> str:
    """The tier that can actually feed one mall machine, from its intake rate.

    Sizing this from the requested BATCH was throughput-blind: an
    electronic-circuit cell draws 6.0 items/s and was handed a 1.4/s inserter,
    running at roughly a quarter speed however full its requester was. Eight of
    thirteen mall recipes were undersized that way.

    It also corrupted the promotion signal: a machine throttled by its inserter
    still reports as working, so the cell read as saturated and promotion built
    six more machines, every one equally throttled.
    """
    crafts = machine_crafts_per_second(machine, craft_time)
    return inserter_for_demand(sum(amounts) * crafts * FEED_HEADROOM)


def compact_output_inserter(machine: str, product_amount: float,
                            craft_time: float) -> str:
    """The tier that can clear one mall machine's output."""
    crafts = machine_crafts_per_second(machine, craft_time)
    return inserter_for_demand(product_amount * crafts * FEED_HEADROOM)


def generate_compact_mall_request_update(
    recipe: str,
    ingredients: list[str],
    amounts: list[float],
    machine_position: tuple[float, float],
    *,
    stock_target: int,
    product_amount: float = 1,
) -> dict:
    """Reconfigure the requester of a legacy one-machine compact mall cell."""
    requests = compact_requests(ingredients, amounts, stock_target, product_amount)
    action = {
        "action_type": "place_entity", "entity": "requester-chest",
        "position": {"x": machine_position[0] - 3, "y": machine_position[1]},
        "logistic_group": f"mall:{recipe}", "logistic_requests": requests,
    }
    return {"phases": [{"name": f"compact_mall_requests_{recipe}", "actions": [action]}]}


def generate_mall_provider_limit_update(
    recipe: str, provider_position: tuple[float, float], stock_target: int,
) -> dict:
    """Resize an existing mall provider to the current construction target."""
    action = {
        "action_type": "place_entity", "entity": "passive-provider-chest",
        "position": {"x": provider_position[0], "y": provider_position[1]},
        "inventory_limit": _inventory_limit(recipe, stock_target),
    }
    return {"phases": [{"name": f"mall_provider_limit_{recipe}", "actions": [action]}]}


def generate_promoted_mall_retirement_plan(
    recipe: str,
    machine: str,
    machine_position: tuple[float, float],
    provider_position: tuple[float, float],
) -> dict:
    """Free one paired mall half after its recipe moves to a shared line.

    The shared requester and substation stay in place. Only the old machine,
    its two inserters, provider, and this recipe's requester section are
    retired, leaving the half available for a later mall assignment.
    """
    mx, my = machine_position
    px, py = provider_position
    if py == my:
        raise ValueError("Promoted mall provider must be above or below its machine")
    left = py < my
    requester = (mx + (3 if left else -3), my)
    inserter_x = mx + (2 if left else -2)
    output_y = my + (-1 if left else 1)
    actions = [{
        "action_type": "remove_entity", "entity": machine,
        "position": {"x": mx, "y": my},
    }]
    for name in ("inserter", "fast-inserter", "bulk-inserter"):
        for y in (my, output_y):
            actions.append({
                "action_type": "remove_entity", "entity": name,
                "position": {"x": inserter_x, "y": y},
            })
    actions.extend([
        {"action_type": "remove_entity", "entity": "passive-provider-chest",
         "position": {"x": px, "y": py}},
        {"action_type": "place_entity", "entity": "requester-chest",
         "position": {"x": requester[0], "y": requester[1]},
         "clear_logistic_groups": [f"mall:{recipe}"],
        },
    ])
    return {"phases": [{
        "name": f"retire_promoted_mall_{recipe}", "actions": actions,
    }]}

def generate_paired_mall_layout(
    recipe: str,
    machine: str,
    ingredients: list[str],
    amounts: list[float],
    origin: tuple[int, int],
    side: str,
    *,
    stock_target: int = 1,
    product_amount: float = 1,
    craft_time: float,
    set_recipe: bool = True,
) -> dict:
    """Fill one half of a dense two-machine cell sharing one requester.

    Only this half's own request group is declared. The executor upserts it by
    label, so the other machine's group on the same chest survives untouched
    and neither half has to know what the other one asked for.
    """
    if side not in {"left", "right"}:
        raise ValueError("Paired mall side must be left or right")
    requests = compact_requests(
        ingredients, amounts, stock_target, product_amount,
    )
    section = recipe_logistic_section(
        recipe, ingredients, amounts, machine=machine, craft_time=craft_time,
    )
    ox, oy = origin
    left = side == "left"
    machine_x = ox + (1.5 if left else 7.5)
    input_x = ox + (3.5 if left else 5.5)
    provider_y = oy + (0.5 if left else 2.5)
    output_x = ox + (3.5 if left else 5.5)
    # Inserters face the tile they pick up from. The requester is between the
    # two machines: the left machine picks up east and the right picks up west.
    # Outputs use the remaining center-column slots: each picks up from its
    # machine and drops into its own provider above/below the requester.
    input_direction = "east" if left else "west"
    output_direction = "west" if left else "east"
    machine_action = {
        "action_type": "place_ghost", "entity": machine,
        "position": {"x": machine_x, "y": oy + 1.5},
        # Stop crafting once the network already holds the target. Without this
        # the only brake was the planner noticing on a LATER pass and dropping
        # the target, which cost a pass and did nothing in the meantime -- the
        # mall spent that time consuming stock to make more of what it had.
        "logistic_condition": stock_gate(recipe, stock_target),
    }
    if set_recipe:
        machine_action["recipe"] = recipe
    actions = [
        {"action_type": "place_entity", "entity": "substation",
         "position": {"x": ox + 4.0, "y": oy + 5.0}},
        machine_action,
        {"action_type": "place_entity", "entity": "requester-chest",
         "position": {"x": ox + 4.5, "y": oy + 1.5},
         "logistic_sections": [section]},
        {"action_type": "place_entity",
         "entity": compact_input_inserter(machine, ingredients, amounts, craft_time),
         "position": {"x": input_x, "y": oy + 1.5},
         "direction": input_direction},
        {"action_type": "place_entity",
         "entity": compact_output_inserter(machine, product_amount, craft_time),
         "position": {"x": output_x, "y": provider_y},
         "direction": output_direction},
        {"action_type": "place_entity", "entity": "passive-provider-chest",
         "position": {"x": ox + 4.5, "y": provider_y},
         "inventory_limit": _inventory_limit(recipe, stock_target)},
    ]
    return {"phases": [{"name": f"paired_mall_{recipe}", "actions": actions}]}