# Path: planners/smelter_block.py
# Purpose: Compose the approved Start/Middle/End refinery templates and generate ownership-bounded expansion deltas.

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Iterable

from planners.plan_validation import actions as plan_actions
from planners.plan_validation import validate_build_plan
from planners.refinery_blueprints import template_actions, template_name
from planners.recipe_data import LINE_RECIPES, MACHINE_SPEEDS, inserter_for_demand


FURNACES_PER_MODULE = 6
COLUMN_PITCH = 12
MIDDLE_PITCH = 9
START_DEPTH = 3
END_DEPTH = 11
PREFERRED_COLUMNS = 5

# Capacity is deliberately policy data, not an emergent consequence of the
# current drill count. A refinery generation expands through these checkpoints
# before a later generation is opened elsewhere.
REFINERY_GENERATION_1_CAPACITIES = tuple(range(6, 49, 6))
REFINERY_GENERATION_2_CAPACITIES = tuple(range(48, 289, 6))
REFINERY_GENERATION_3_CAPACITIES = tuple(range(36, 577, 6))
REFINERY_GENERATION_4_CAPACITIES = tuple(range(144, 577, 6))
REFINERY_GENERATION_5_CAPACITIES = (576,)
REFINERY_CAPACITY_SCHEDULES = (
    REFINERY_GENERATION_1_CAPACITIES,
    REFINERY_GENERATION_2_CAPACITIES,
    REFINERY_GENERATION_3_CAPACITIES,
    REFINERY_GENERATION_4_CAPACITIES,
    REFINERY_GENERATION_5_CAPACITIES,
)

# Scheduled capacities need exact lattice shapes. The legacy fallback below
# remains for arbitrary demand targets and for recovering older deployments.
_SCHEDULED_BLOCK_DIMENSIONS = {
    6: (1, 1), 12: (2, 1), 24: (4, 1), 36: (6, 1), 48: (4, 2),
    72: (6, 2), 96: (4, 4), 144: (6, 4), 192: (4, 8),
    288: (4, 12), 576: (3, 32),
}


@dataclass(frozen=True)
class BlockShape:
    """Stable modular shape: widen through five columns, then deepen by rows."""

    requested_furnaces: int
    columns: int
    middle_rows: int

    @property
    def rows(self) -> int:
        return self.middle_rows + 1

    @property
    def capacity(self) -> int:
        return FURNACES_PER_MODULE * self.columns * self.rows

    @property
    def width(self) -> int:
        return COLUMN_PITCH * self.columns + 3

    @property
    def depth(self) -> int:
        return START_DEPTH + END_DEPTH + MIDDLE_PITCH * self.middle_rows

@dataclass(frozen=True)
class RefineryInterfaces:
    """Stable belt, provider, and power interfaces around one composed block."""

    ore_inputs: tuple[tuple[float, float], tuple[float, float]]
    plate_outputs: tuple[tuple[float, float], tuple[float, float]]
    provider: tuple[float, float]
    power_anchor: tuple[float, float]


def block_shape(furnaces: int) -> BlockShape:
    """Choose a monotonic shape so an established column never moves sideways."""
    if furnaces < 1:
        raise ValueError(f"A smelting block needs at least one furnace, got {furnaces}")
    scheduled = _SCHEDULED_BLOCK_DIMENSIONS.get(furnaces)
    if scheduled is not None:
        columns, rows = scheduled
        return BlockShape(furnaces, columns, rows - 1)
    if furnaces % FURNACES_PER_MODULE == 0:
        modules = furnaces // FURNACES_PER_MODULE
        columns = max(
            candidate for candidate in range(1, min(PREFERRED_COLUMNS, modules) + 1)
            if modules % candidate == 0
        )
        rows = modules // columns
        return BlockShape(furnaces, columns, rows - 1)
    columns = min(PREFERRED_COLUMNS, math.ceil(furnaces / FURNACES_PER_MODULE))
    rows = math.ceil(furnaces / (FURNACES_PER_MODULE * columns))
    return BlockShape(furnaces, columns, rows - 1)


def scheduled_refinery_target(
    current_furnaces: int, required_furnaces: int, *, generation: int = 1,
) -> int | None:
    """Return the next policy checkpoint that can satisfy current demand.

    ``None`` means this refinery reached its generation cap; the caller must
    open the next refinery instead of silently overbuilding this footprint.
    """
    if current_furnaces < 1 or required_furnaces < 1:
        raise ValueError("Refinery furnace counts must be positive")
    if required_furnaces <= current_furnaces:
        return current_furnaces
    try:
        schedule = REFINERY_CAPACITY_SCHEDULES[generation - 1]
    except IndexError as error:
        raise ValueError(f"Unknown refinery generation {generation}") from error
    for capacity in schedule:
        if capacity > current_furnaces and capacity >= required_furnaces:
            return capacity
    return None


def _deduplicate(actions: Iterable[dict]) -> list[dict]:
    """Collapse intentional X-overlap while rejecting incompatible occupants."""
    by_position: dict[tuple[float, float], dict] = {}
    for action in actions:
        position = action["position"]
        slot = (position["x"], position["y"])
        existing = by_position.get(slot)
        if existing is None:
            by_position[slot] = action
        elif existing != action:
            raise ValueError(
                f"Refinery templates conflict at {slot}: {existing} versus {action}"
            )
    return list(by_position.values())


def _repeated_actions(
    template: str, columns: int, *, origin_x: float, origin_y: float, recipe: str,
    variant: str = "standard",
) -> list[dict]:
    return _deduplicate(
        action
        for column in range(columns)
        for action in template_actions(
            template_name(template, variant),
            origin_x=origin_x + column * COLUMN_PITCH,
            origin_y=origin_y,
            recipe=recipe,
        )
    )


def _phase(name: str, actions: list[dict]) -> dict:
    return {"name": name, "actions": actions}


def _vertical_mirror_actions(actions: Iterable[dict], origin_y: float) -> list[dict]:
    """Reflect a refinery north/south while keeping its growth anchor fixed."""
    mirrored: list[dict] = []
    direction_flip = {"north": "south", "south": "north"}
    priority_flip = {"left": "right", "right": "left"}
    for original in actions:
        action = json.loads(json.dumps(original))
        position = action.get("position")
        if position is not None:
            position["y"] = 2 * origin_y - position["y"]
        if action.get("direction") in direction_flip:
            action["direction"] = direction_flip[action["direction"]]
        for field in ("input_priority", "output_priority"):
            if action.get(field) in priority_flip:
                action[field] = priority_flip[action[field]]
        mirrored.append(action)
    return mirrored


def generate_refinery_plan(
    recipe: str,
    furnaces: int,
    *,
    origin_x: float = 0,
    origin_y: float = 0,
    variant: str = "standard",
    start_variant: str | None = None,
    middle_variant: str | None = None,
    end_variant: str | None = None,
    vertical_mirror: bool = False,
) -> dict:
    """Build Start, zero or more Middle rows, then one End merge."""
    shape = block_shape(furnaces)
    start_variant = start_variant or variant
    middle_variant = middle_variant or variant
    end_variant = end_variant or variant
    phases = [
        _phase(
            f"refinery_start_{recipe}",
            _repeated_actions(
                "start", shape.columns, origin_x=origin_x, origin_y=origin_y, recipe=recipe, variant=start_variant,
            ),
        )
    ]
    for row in range(shape.middle_rows):
        row_y = origin_y + START_DEPTH + row * MIDDLE_PITCH
        phases.append(
            _phase(
                f"refinery_middle_{recipe}_{row + 1}",
                _repeated_actions(
                    "middle", shape.columns, origin_x=origin_x, origin_y=row_y, recipe=recipe, variant=middle_variant,
                ),
            )
        )
    end_y = origin_y + START_DEPTH + shape.middle_rows * MIDDLE_PITCH
    phases.append(
        _phase(
            f"refinery_end_{recipe}",
            _repeated_actions(
                "end", shape.columns, origin_x=origin_x, origin_y=end_y, recipe=recipe, variant=end_variant,
            ),
        )
    )
    if vertical_mirror:
        phases = [
            _phase(phase["name"], _vertical_mirror_actions(phase["actions"], origin_y))
            for phase in phases
        ]
    plan = {"phases": phases}
    validate_build_plan(plan)
    return plan


def _action_key(action: dict) -> str:
    return json.dumps(action, sort_keys=True, separators=(",", ":"))


def _removal_action(action: dict) -> dict:
    return {
        "action_type": "remove_entity",
        "entity": action["entity"],
        "position": dict(action["position"]),
    }


def _extension_additions(new_plan: dict, old_keys: set[str]) -> tuple[list[dict], list[dict]]:
    body: list[dict] = []
    end: list[dict] = []
    last_phase = len(new_plan["phases"]) - 1
    for index, phase in enumerate(new_plan["phases"]):
        additions = [action for action in phase["actions"] if _action_key(action) not in old_keys]
        (end if index == last_phase else body).extend(additions)
    return body, end


def generate_refinery_extension_plan(
    recipe: str,
    current_furnaces: int,
    new_furnaces: int,
    *,
    origin_x: float = 0,
    origin_y: float = 0,
    current_variant: str = "standard",
    target_variant: str | None = None,
    vertical_mirror: bool = False,
) -> dict:
    """Retire the old End, or replace a bootstrap variant before expanding."""
    if new_furnaces <= current_furnaces:
        raise ValueError("Refinery extension must increase furnace capacity")
    target_variant = target_variant or current_variant
    old_plan = generate_refinery_plan(
        recipe, current_furnaces, origin_x=origin_x, origin_y=origin_y,
        variant=current_variant, vertical_mirror=vertical_mirror,
    )
    new_plan = generate_refinery_plan(
        recipe, new_furnaces, origin_x=origin_x, origin_y=origin_y,
        variant=target_variant, vertical_mirror=vertical_mirror,
    )
    old = list(plan_actions(old_plan))
    new = list(plan_actions(new_plan))
    if current_variant != target_variant:
        old_keys = {_action_key(action) for action in old}
        new_keys = {_action_key(action) for action in new}
        removals = [
            _removal_action(action) for action in old
            if _action_key(action) not in new_keys
        ]
        additions = [
            action for action in new if _action_key(action) not in old_keys
        ]
        phases = [
            _phase(f"retire_refinery_{current_variant}_{recipe}", removals),
            _phase(f"replace_refinery_{recipe}", additions),
        ]
        plan = {"phases": [phase for phase in phases if phase["actions"]]}
        validate_build_plan(plan)
        return plan
    old_keys = {_action_key(action) for action in old}
    new_keys = {_action_key(action) for action in new}
    missing_old_keys = old_keys - new_keys
    old_end = old_plan["phases"][-1]["actions"]
    old_end_keys = {_action_key(action) for action in old_end}
    outside_end = missing_old_keys - old_end_keys
    if outside_end:
        raise ValueError("Refinery extension would retire a non-End placement")
    removals = [
        _removal_action(action)
        for action in old_end
        if _action_key(action) in missing_old_keys
    ]
    body, end = _extension_additions(new_plan, old_keys)
    phases = [
        _phase(f"retire_refinery_end_{recipe}", removals),
        _phase(f"extend_refinery_{recipe}", body),
        _phase(f"finish_refinery_end_{recipe}", end),
    ]
    plan = {"phases": [phase for phase in phases if phase["actions"]]}
    validate_build_plan(plan)
    return plan

def refinery_interfaces(
    furnaces: int, *, origin_x: float = 0, origin_y: float = 0,
    variant: str = "standard", vertical_mirror: bool = False,
) -> RefineryInterfaces:
    """Return the exact external positions defined by one template variant."""
    shape = block_shape(furnaces)
    end_y = origin_y + START_DEPTH + shape.middle_rows * MIDDLE_PITCH
    terminal_x = origin_x + COLUMN_PITCH * shape.columns + (
        2.5 if variant == "basic" else 3.5
    )
    if variant == "basic":
        ore_inputs = ((origin_x - 0.5, origin_y + 1.5),)
        plate_outputs = ((terminal_x, end_y + 9.5),)
    elif variant == "standard":
        ore_inputs = ((origin_x - 0.5, origin_y + 0.5),
                      (origin_x - 0.5, origin_y + 1.5))
        plate_outputs = ((terminal_x, end_y + 9.5),
                         (terminal_x, end_y + 10.5))
    else:
        raise ValueError(f"Unknown refinery variant {variant!r}")
    interfaces = RefineryInterfaces(
        ore_inputs=ore_inputs, plate_outputs=plate_outputs,
        provider=(
            terminal_x + 1,
            end_y + (11.5 if variant == "basic" else 12.5),
        ),
        power_anchor=(terminal_x - 2, end_y + 12.5),
    )
    if not vertical_mirror:
        return interfaces
    mirror = lambda point: (point[0], 2 * origin_y - point[1])
    return RefineryInterfaces(
        ore_inputs=tuple(mirror(point) for point in interfaces.ore_inputs),
        plate_outputs=tuple(mirror(point) for point in interfaces.plate_outputs),
        provider=mirror(interfaces.provider),
        power_anchor=mirror(interfaces.power_anchor),
    )


def _output_adapter_inserter(recipe: str, furnaces: int, variant: str) -> str:
    """Choose a collector tier for the complete refinery output rate."""
    if variant != "basic":
        return "fast-inserter"
    spec = LINE_RECIPES[recipe]
    crafts = furnaces * MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]
    output_rate = spec.get("product_amount", 1) * crafts
    return inserter_for_demand(output_rate)


def _output_adapter_actions(
    furnaces: int, *, origin_x: float, origin_y: float, variant: str,
    recipe: str, vertical_mirror: bool = False,
) -> list[dict]:
    interface = refinery_interfaces(
        furnaces, origin_x=origin_x, origin_y=origin_y, variant=variant,
    )
    belt = "transport-belt" if variant == "basic" else "fast-transport-belt"
    inserter = _output_adapter_inserter(recipe, furnaces, variant)
    actions = [
        {"action_type": "place_ghost", "entity": belt,
         "position": {"x": x, "y": y}, "direction": "east"}
        for x, y in interface.plate_outputs
    ]
    tap_x, tap_y = interface.plate_outputs[-1][0] + 1, interface.plate_outputs[-1][1]
    actions.extend([
        {"action_type": "place_ghost", "entity": belt,
         "position": {"x": tap_x, "y": tap_y}, "direction": "east"},
        {"action_type": "place_ghost", "entity": inserter,
         "position": {"x": tap_x, "y": tap_y + 1}, "direction": "north"},
        {"action_type": "place_ghost", "entity": "passive-provider-chest",
         "position": {"x": interface.provider[0], "y": interface.provider[1]}},
        {"action_type": "place_ghost", "entity": "medium-electric-pole",
         "position": {"x": interface.power_anchor[0], "y": interface.power_anchor[1]}},
    ])
    return (
        _vertical_mirror_actions(actions, origin_y)
        if vertical_mirror else actions
    )

def generate_managed_refinery_plan(
    recipe: str, furnaces: int, *, origin_x: float = 0, origin_y: float = 0,
    variant: str = "standard", vertical_mirror: bool = False,
) -> dict:
    """Add a non-blocking provider side tap to the approved refinery block."""
    plan = generate_refinery_plan(
        recipe, furnaces, origin_x=origin_x, origin_y=origin_y, variant=variant,
        vertical_mirror=vertical_mirror,
    )
    plan["phases"].append(_phase(
        f"refinery_output_{recipe}",
        _output_adapter_actions(
            furnaces, origin_x=origin_x, origin_y=origin_y, variant=variant,
            recipe=recipe, vertical_mirror=vertical_mirror,
        ),
    ))
    validate_build_plan(plan)
    return plan


def generate_managed_refinery_extension_plan(
    recipe: str, current_furnaces: int, new_furnaces: int, *,
    origin_x: float = 0, origin_y: float = 0,
    current_variant: str = "standard", target_variant: str | None = None,
    vertical_mirror: bool = False,
) -> dict:
    """Move the planner-owned provider tap with End or variant migration."""
    target_variant = target_variant or current_variant
    plan = generate_refinery_extension_plan(
        recipe, current_furnaces, new_furnaces,
        origin_x=origin_x, origin_y=origin_y,
        current_variant=current_variant, target_variant=target_variant,
        vertical_mirror=vertical_mirror,
    )
    old = _output_adapter_actions(
        current_furnaces, origin_x=origin_x, origin_y=origin_y,
        variant=current_variant, recipe=recipe,
        vertical_mirror=vertical_mirror,
    )
    new = _output_adapter_actions(
        new_furnaces, origin_x=origin_x, origin_y=origin_y,
        variant=target_variant, recipe=recipe,
        vertical_mirror=vertical_mirror,
    )
    old_keys, new_keys = {_action_key(a) for a in old}, {_action_key(a) for a in new}
    removals = [_removal_action(a) for a in old if _action_key(a) not in new_keys]
    additions = [a for a in new if _action_key(a) not in old_keys]
    plan["phases"].insert(0, _phase(f"retire_refinery_output_{recipe}", removals))
    plan["phases"].append(_phase(f"finish_refinery_output_{recipe}", additions))
    plan["phases"] = [phase for phase in plan["phases"] if phase["actions"]]
    validate_build_plan(plan)
    return plan
