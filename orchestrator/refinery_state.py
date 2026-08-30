# Path: orchestrator/refinery_state.py
# Purpose: Recover planner-owned modular refineries from live furnace geometry and authorize only exact End/output removals.

from __future__ import annotations

from dataclasses import dataclass, replace

from orchestrator import live_base
from planners.plan_validation import actions as plan_actions
from planners.smelter_block import (
    BlockShape,
    RefineryInterfaces,
    block_shape,
    generate_managed_refinery_plan,
    refinery_interfaces,
)
from tools.rcon_client import RconClient

Point = tuple[float, float]
_DIRECTIONS = {"north": 0, "east": 4, "south": 8, "west": 12}


@dataclass(frozen=True)
class ManagedRefineryState:
    recipe: str
    origin: Point
    shape: BlockShape
    machine_positions: tuple[Point, ...]
    interfaces: RefineryInterfaces
    variant: str = "standard"
    vertical_mirror: bool = False
    owned_actions: tuple[dict, ...] = ()

    @property
    def furnace_count(self) -> int:
        return len(self.machine_positions)


def infer_refinery_state(
    recipe: str, positions: tuple[Point, ...], *, variant: str = "standard",
    vertical_mirror: bool = False,
) -> ManagedRefineryState:
    """Infer only the exact rectangular furnace lattice produced by the templates."""
    if not positions:
        raise ValueError(f"{recipe} modular refinery has no furnaces")
    unique = tuple(sorted(set(positions)))
    xs = sorted({x for x, _y in unique})
    ys = sorted({y for _x, y in unique})
    if len(xs) % 2 or len(ys) % 3:
        raise ValueError(f"{recipe} furnaces do not form complete six-furnace modules")
    columns, rows = len(xs) // 2, len(ys) // 3
    origin = (
        xs[0] - 3.5,
        ys[-1] + 4.5 if vertical_mirror else ys[0] - 4.5,
    )
    expected_xs = [origin[0] + 3.5 + index * 6 for index in range(columns * 2)]
    y_step = -3 if vertical_mirror else 3
    expected_ys = [origin[1] + (-4.5 if vertical_mirror else 4.5) + index * y_step
                   for index in range(rows * 3)]
    expected = {(x, y) for x in expected_xs for y in expected_ys}
    if set(unique) != expected:
        raise ValueError(f"{recipe} furnaces are not one contiguous managed block")
    shape = block_shape(len(unique))
    if (shape.columns, shape.rows) != (columns, rows):
        raise ValueError(f"{recipe} furnace lattice does not match phased block growth")
    return ManagedRefineryState(
        recipe, origin, shape, unique,
        refinery_interfaces(
            len(unique), origin_x=origin[0], origin_y=origin[1], variant=variant,
            vertical_mirror=vertical_mirror,
        ),
        variant, vertical_mirror,
    )


def _action_position(action: dict) -> Point:
    return action["position"]["x"], action["position"]["y"]


def _matches_signature(action: dict, actual: dict | None) -> bool:
    if actual is None or actual.get("name") != action["entity"]:
        return False
    if "direction" in action and actual.get("direction") != _DIRECTIONS[action["direction"]]:
        return False
    return all(
        actual.get(field) == action[field]
        for field in ("input_priority", "output_priority") if field in action
    )


def _assert_live_actions(
    client: RconClient, surface: str, force: str, expected: list[dict], context: str,
) -> None:
    actual = live_base.entity_signatures_at(
        client, surface, force, [_action_position(action) for action in expected],
    )
    mismatches = [
        action for action in expected
        if not _matches_signature(action, actual.get(_action_position(action)))
    ]
    if mismatches:
        first = mismatches[0]
        raise ValueError(
            f"{context} is not the planner-owned template at "
            f"{_action_position(first)}: expected {first['entity']}"
        )


def recover_managed_refinery(
    client: RconClient, surface: str, force: str, recipe: str,
    machine_positions: tuple[Point, ...], *, owned_actions: tuple[dict, ...] = (),
) -> ManagedRefineryState:
    """Recover a block from its furnace lattice and stable splitter signature.

    Retained belts are repairable transport, not proof of ownership. Requiring
    every one to remain identical prevented a working six-furnace line from
    expanding after one belt was missing. Removals remain exact-checked by
    ``assert_refinery_removals_owned`` before a delta is submitted.
    """
    candidates = []
    first_error: ValueError | None = None
    for variant in ("standard", "basic"):
        for vertical_mirror in (False, True):
            state = infer_refinery_state(
                recipe, machine_positions, variant=variant,
                vertical_mirror=vertical_mirror,
            )
            plan = generate_managed_refinery_plan(
                recipe, state.furnace_count,
                origin_x=state.origin[0], origin_y=state.origin[1],
                variant=variant, vertical_mirror=vertical_mirror,
            )
            signature = [
                action for action in plan_actions(plan)
                if action["action_type"] in {"place_entity", "place_ghost"}
                and action.get("entity", "").endswith("splitter")
            ]
            try:
                _assert_live_actions(
                    client, surface, force, signature, f"{recipe} refinery",
                )
            except (KeyError, ValueError) as error:
                normalized = (
                    error if isinstance(error, ValueError)
                    else ValueError(str(error))
                )
                first_error = first_error or normalized
                candidates.append(normalized)
                continue
            return replace(state, owned_actions=owned_actions)
    raise first_error or (candidates[-1] if candidates else ValueError(
        f"{recipe} refinery has no approved template variant"
    ))


def assert_refinery_removals_owned(
    client: RconClient, surface: str, force: str,
    state: ManagedRefineryState, delta: dict,
) -> None:
    """Authorize a delta only when every removal still matches the old template."""
    if state.owned_actions:
        source_actions = state.owned_actions
    else:
        full = generate_managed_refinery_plan(
            state.recipe, state.furnace_count,
            origin_x=state.origin[0], origin_y=state.origin[1], variant=state.variant,
            vertical_mirror=state.vertical_mirror,
        )
        source_actions = tuple(plan_actions(full))
    owned = {
        (action["entity"], _action_position(action)): action
        for action in source_actions
    }
    removals = [
        action for action in plan_actions(delta)
        if action["action_type"] == "remove_entity"
    ]
    expected = []
    for removal in removals:
        action = owned.get((removal["entity"], _action_position(removal)))
        if action is None:
            raise ValueError("Refinery delta attempts to remove an unowned placement")
        expected.append(action)
    actual = live_base.entity_signatures_at(
        client, surface, force, [_action_position(action) for action in expected],
    )
    mismatches = [
        action for action in expected
        # The executor removes only an exact same-name entity or ghost.  If an
        # old End/output action is already absent, its removal is a no-op and
        # the replacement plan can safely recreate the complete adapter.  A
        # different live occupant (or changed orientation/configuration) must
        # still fail closed: that could be somebody else's infrastructure.
        if actual.get(_action_position(action), {}).get("name") != "NONE"
        and not _matches_signature(action, actual.get(_action_position(action)))
    ]
    if mismatches:
        first = mismatches[0]
        raise ValueError(
            f"{state.recipe} removable End is not the planner-owned template at "
            f"{_action_position(first)}: expected {first['entity']}"
        )


def live_refinery_placements(
    client: RconClient, surface: str, force: str, state: ManagedRefineryState,
) -> set[tuple[str, float, float]]:
    """Return only live entities that still match the recovered old plan."""
    if not hasattr(client, "command"):
        return set()
    if state.owned_actions:
        source_actions = state.owned_actions
    else:
        full = generate_managed_refinery_plan(
            state.recipe, state.furnace_count,
            origin_x=state.origin[0], origin_y=state.origin[1], variant=state.variant,
            vertical_mirror=state.vertical_mirror,
        )
        source_actions = tuple(plan_actions(full))
    expected = [
        action for action in source_actions
        if action["action_type"] in {"place_entity", "place_ghost"}
    ]
    actual = live_base.entity_signatures_at(
        client, surface, force, [_action_position(action) for action in expected],
    )
    return {
        (action["entity"], *_action_position(action))
        for action in expected
        if _matches_signature(action, actual.get(_action_position(action)))
    }
