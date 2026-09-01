# Path: orchestrator/stage_recovery.py
# Purpose: Bounded recovery of deterministic existing stages whose declared ingredient transport is missing or broken.

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from orchestrator import live_base
from orchestrator.game_bridge import GameBridge
from orchestrator.stage_services import StuckError, _diagnose_machines
from orchestrator.stage_transport import (
    _swap_infinity_chests,
    _transport_mode,
    ensure_ingredient_transport,
)
from planners.local_layout_planner import LocalLayoutPlanner
from planners.recipe_data import LINE_RECIPES
from tools.rcon_client import RconClient

Point = tuple[float, float]


def _existing_stage_feed_positions(
    recipe: str, machine_positions: Sequence[Point],
) -> tuple[dict[str, Point], dict[str, str]]:
    """Reconstruct declared feed chests from the immutable line geometry."""
    if not machine_positions:
        raise StuckError(f"existing {recipe} stage has no machine positions")
    ordered = sorted(machine_positions)
    y = ordered[0][1]
    expected = [(ordered[0][0] + 3 * index, y) for index in range(len(ordered))]
    if list(ordered) != expected:
        raise StuckError(
            f"existing {recipe} machines do not match the deterministic line geometry; "
            "ingredient transport cannot be repaired safely"
        )
    origin_x = ordered[0][0] - 1.5
    origin_y = y - 3.5
    if not origin_x.is_integer() or not origin_y.is_integer():
        raise StuckError(
            f"existing {recipe} stage origin is not tile-aligned; "
            "ingredient transport cannot be reconstructed safely"
        )
    modes = {
        ingredient: _transport_mode(recipe, ingredient, len(ordered))
        for ingredient in LINE_RECIPES[recipe]["ingredients"]
    }
    plan = LocalLayoutPlanner().generate_line_layout(
        recipe, len(ordered), int(origin_x), int(origin_y),
        feed_style="chest", terminal_collector=True,
    )
    return _swap_infinity_chests(plan, modes), modes


def repair_existing_ingredient_transport(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, machine_positions: Sequence[Point],
    ensure_source: Callable[[str], Point | None], emit: Callable[[str], None],
) -> bool:
    """Repair one existing starved stage without duplicating its machines.

    Returns False when an upstream stage was built/repaired this iteration and
    the caller should re-survey. A completed repair is verified by machine
    status after a bounded first-item transit window.
    """
    feeds, modes = _existing_stage_feed_positions(recipe, machine_positions)
    for ingredient, position in sorted(feeds.items()):
        entity = live_base.entity_at(client, surface, position)
        # Every normal line feeder is converted from the sandbox's
        # ``infinity-chest`` into a requester, even when its steady-state
        # transport mode is a belt. Recovery must validate that real build
        # contract rather than the intended transport mode; expecting a plain
        # steel chest here rejected the exact requester layout it had built.
        expected = "requester-chest"
        if entity is None or entity["name"] != expected:
            found = "nothing" if entity is None else entity["name"]
            raise StuckError(
                f"existing {recipe} feed for {ingredient} expected {expected} at "
                f"{position}, found {found}; refusing to guess a different endpoint"
            )

    sources: dict[str, Point] = {}
    for ingredient in sorted(feeds):
        source = ensure_source(ingredient)
        if source is None:
            return False
        if math.dist(source, feeds[ingredient]) == 0:
            raise StuckError(
                f"existing {recipe} feed for {ingredient} resolves to its own source chest"
            )
        sources[ingredient] = source

    grace = 0.0
    for ingredient in sorted(feeds):
        grace = max(
            grace,
            ensure_ingredient_transport(
                client, bridge, surface, force, recipe, ingredient,
                sources[ingredient], feeds[ingredient], len(machine_positions), emit,
                reuse_existing=True,
            ),
        )
    stuck = _diagnose_machines(
        client, surface, list(machine_positions), emit, grace_seconds=grace,
        bridge=bridge, force=force,
    )
    if stuck:
        empty = [
            ingredient for ingredient, position in sorted(feeds.items())
            if live_base.chest_contents(client, surface, position).get(ingredient, 0) == 0
        ]
        detail = f"; empty feeds: {empty}" if empty else ""
        raise StuckError(
            f"existing {recipe} transport repair did not restore production: {stuck}{detail}"
        )
    emit(f"existing {recipe} ingredient transport recovered")
    return True
