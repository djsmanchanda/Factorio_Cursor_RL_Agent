# Path: orchestrator/recoverable_retirement.py
# Purpose: Retire exact planner-owned entities through construction bots so their items are recovered.

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone

from orchestrator import live_base
from orchestrator.game_bridge import GameBridge, load_json
from tools.rcon_client import RconClient

Point = tuple[float, float]


class RecoverableRetirementError(RuntimeError):
    """An exact owned teardown could not be completed through construction bots."""


def _remove_actions(plan: dict) -> tuple[dict, ...]:
    actions = tuple(
        action
        for phase in plan.get("phases", ())
        for action in phase.get("actions", ())
    )
    if not actions or any(
        action.get("action_type") != "remove_entity" for action in actions
    ):
        raise RecoverableRetirementError(
            "Recoverable retirement requires a non-empty removal-only plan"
        )
    return actions


def _matching_actions(
    client: RconClient,
    surface: str,
    force: str,
    actions: tuple[dict, ...],
) -> tuple[dict, ...]:
    positions = tuple(
        (float(action["position"]["x"]), float(action["position"]["y"]))
        for action in actions
    )
    signatures = live_base.entity_signatures_at(
        client, surface, force, positions,
    )
    matching: list[dict] = []
    for action, position in zip(actions, positions, strict=True):
        observed = signatures.get(position, {"name": "NONE"}).get("name")
        if observed == action["entity"]:
            matching.append(action)
        elif observed != "NONE":
            raise RecoverableRetirementError(
                f"Retirement target at {position} is {observed}, expected "
                f"planner-owned {action['entity']}"
            )
    return tuple(matching)


def retire_entities_via_bots(
    client: RconClient,
    bridge: GameBridge,
    surface: str,
    force: str,
    plan: dict,
    label: str,
    emit: Callable[[str], None],
    *,
    timeout: float = 120.0,
    poll_seconds: float = 2.0,
) -> int:
    """Order exact removals and wait until bots have recovered every entity."""
    actions = _remove_actions(plan)
    matching = _matching_actions(client, surface, force, actions)
    if not matching:
        return 0

    block = f"recoverable_{label}"
    deconstruction_actions = [
        {
            "action": "deconstruct_entity",
            "name": action["entity"],
            "position": {
                "x": float(action["position"]["x"]),
                "y": float(action["position"]["y"]),
            },
            "block": block,
        }
        for action in matching
    ]
    authorization = {
        "approved_actions": ["apply_deconstruction"],
        "denied_actions": [],
        "scope_limits": {
            "max_count": len(deconstruction_actions),
            "block_filter": [block],
        },
        "authorization_timestamp": datetime.now(timezone.utc).isoformat(),
        "authorization_source": "policy",
    }
    report = load_json(bridge.execute_deconstruction(
        authorization, {"actions": deconstruction_actions},
        surface=surface, force=force,
    ))
    emit(
        f"RECOVERABLE RETIREMENT: {label} ordered {len(matching)} exact "
        "entity deconstruction(s); waiting for construction bots"
    )

    failed_orders = tuple(
        action for action in report.get("actions", ())
        if action.get("status") != "success"
    )
    if failed_orders:
        remaining = _matching_actions(client, surface, force, matching)
        if remaining:
            raise RecoverableRetirementError(
                f"Bot deconstruction rejected {len(failed_orders)} {label} "
                f"order(s); {len(remaining)} exact entity(s) remain"
            )
        return len(matching)

    deadline = time.monotonic() + timeout
    while True:
        remaining = _matching_actions(client, surface, force, matching)
        if not remaining:
            return len(matching)
        if time.monotonic() >= deadline:
            raise RecoverableRetirementError(
                f"Timed out waiting for construction bots to recover {label}; "
                f"{len(remaining)} exact entity(s) remain"
            )
        time.sleep(poll_seconds)
