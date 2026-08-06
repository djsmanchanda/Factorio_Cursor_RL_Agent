# Path: planners/smelter_block.py
# Purpose: Compose the approved Start/Middle/End refinery templates and generate ownership-bounded expansion deltas.

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Iterable

from planners.plan_validation import actions as plan_actions
from planners.plan_validation import validate_build_plan
from planners.refinery_blueprints import template_actions


FURNACES_PER_MODULE = 6
COLUMN_PITCH = 12
MIDDLE_PITCH = 9
START_DEPTH = 3
END_DEPTH = 11
PREFERRED_COLUMNS = 5


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
    columns = min(PREFERRED_COLUMNS, math.ceil(furnaces / FURNACES_PER_MODULE))
    rows = math.ceil(furnaces / (FURNACES_PER_MODULE * columns))
    return BlockShape(furnaces, columns, rows - 1)


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
) -> list[dict]:
    return _deduplicate(
        action
        for column in range(columns)
        for action in template_actions(
            template,
            origin_x=origin_x + column * COLUMN_PITCH,
            origin_y=origin_y,
            recipe=recipe,
        )
    )


def _phase(name: str, actions: list[dict]) -> dict:
    return {"name": name, "actions": actions}


def generate_refinery_plan(
    recipe: str,
    furnaces: int,
    *,
    origin_x: float = 0,
    origin_y: float = 0,
) -> dict:
    """Build Start, zero or more Middle rows, then one End merge."""
    shape = block_shape(furnaces)
    phases = [
        _phase(
            f"refinery_start_{recipe}",
            _repeated_actions(
                "start", shape.columns, origin_x=origin_x, origin_y=origin_y, recipe=recipe,
            ),
        )
    ]
    for row in range(shape.middle_rows):
        row_y = origin_y + START_DEPTH + row * MIDDLE_PITCH
        phases.append(
            _phase(
                f"refinery_middle_{recipe}_{row + 1}",
                _repeated_actions(
                    "middle", shape.columns, origin_x=origin_x, origin_y=row_y, recipe=recipe,
                ),
            )
        )
    end_y = origin_y + START_DEPTH + shape.middle_rows * MIDDLE_PITCH
    phases.append(
        _phase(
            f"refinery_end_{recipe}",
            _repeated_actions(
                "end", shape.columns, origin_x=origin_x, origin_y=end_y, recipe=recipe,
            ),
        )
    )
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
) -> dict:
    """Retire only the old End delta, extend, and cap the new tail with End."""
    if new_furnaces <= current_furnaces:
        raise ValueError("Refinery extension must increase furnace capacity")
    old_plan = generate_refinery_plan(recipe, current_furnaces, origin_x=origin_x, origin_y=origin_y)
    new_plan = generate_refinery_plan(recipe, new_furnaces, origin_x=origin_x, origin_y=origin_y)
    old = list(plan_actions(old_plan))
    new = list(plan_actions(new_plan))
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
) -> RefineryInterfaces:
    """Return the exact external positions defined by the composed templates."""
    shape = block_shape(furnaces)
    end_y = origin_y + START_DEPTH + shape.middle_rows * MIDDLE_PITCH
    terminal_x = origin_x + COLUMN_PITCH * shape.columns + 3.5
    return RefineryInterfaces(
        ore_inputs=((origin_x - 0.5, origin_y + 0.5),
                    (origin_x - 0.5, origin_y + 1.5)),
        plate_outputs=((terminal_x, end_y + 9.5),
                       (terminal_x, end_y + 10.5)),
        provider=(terminal_x + 1, end_y + 12.5),
        power_anchor=(terminal_x - 2, end_y + 12.5),
    )


def _output_adapter_actions(
    furnaces: int, *, origin_x: float, origin_y: float,
) -> list[dict]:
    interface = refinery_interfaces(furnaces, origin_x=origin_x, origin_y=origin_y)
    upper, lower = interface.plate_outputs
    tap_x, tap_y = lower[0] + 1, lower[1]
    return [
        {"action_type": "place_ghost", "entity": "fast-transport-belt",
         "position": {"x": upper[0], "y": upper[1]}, "direction": "east"},
        {"action_type": "place_ghost", "entity": "fast-transport-belt",
         "position": {"x": lower[0], "y": lower[1]}, "direction": "east"},
        {"action_type": "place_ghost", "entity": "fast-transport-belt",
         "position": {"x": tap_x, "y": tap_y}, "direction": "east"},
        {"action_type": "place_ghost", "entity": "fast-inserter",
         "position": {"x": tap_x, "y": tap_y + 1}, "direction": "north"},
        {"action_type": "place_ghost", "entity": "passive-provider-chest",
         "position": {"x": interface.provider[0], "y": interface.provider[1]}},
        {"action_type": "place_ghost", "entity": "medium-electric-pole",
         "position": {"x": interface.power_anchor[0], "y": interface.power_anchor[1]}},
    ]


def generate_managed_refinery_plan(
    recipe: str, furnaces: int, *, origin_x: float = 0, origin_y: float = 0,
) -> dict:
    """Add a non-blocking provider side tap to the approved refinery block."""
    plan = generate_refinery_plan(
        recipe, furnaces, origin_x=origin_x, origin_y=origin_y,
    )
    plan["phases"].append(_phase(
        f"refinery_output_{recipe}",
        _output_adapter_actions(furnaces, origin_x=origin_x, origin_y=origin_y),
    ))
    validate_build_plan(plan)
    return plan


def generate_managed_refinery_extension_plan(
    recipe: str, current_furnaces: int, new_furnaces: int, *,
    origin_x: float = 0, origin_y: float = 0,
) -> dict:
    """Move the planner-owned provider tap together with the End migration."""
    plan = generate_refinery_extension_plan(
        recipe, current_furnaces, new_furnaces,
        origin_x=origin_x, origin_y=origin_y,
    )
    old = _output_adapter_actions(
        current_furnaces, origin_x=origin_x, origin_y=origin_y,
    )
    new = _output_adapter_actions(
        new_furnaces, origin_x=origin_x, origin_y=origin_y,
    )
    old_keys, new_keys = {_action_key(a) for a in old}, {_action_key(a) for a in new}
    removals = [_removal_action(a) for a in old if _action_key(a) not in new_keys]
    additions = [a for a in new if _action_key(a) not in old_keys]
    plan["phases"].insert(0, _phase(f"retire_refinery_output_{recipe}", removals))
    plan["phases"].append(_phase(f"finish_refinery_output_{recipe}", additions))
    plan["phases"] = [phase for phase in plan["phases"] if phase["actions"]]
    validate_build_plan(plan)
    return plan
