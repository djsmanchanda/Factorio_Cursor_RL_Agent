# Path: orchestrator/mine_retirement.py
# Purpose: Retire depleted planner-managed mines and release their exact footprint for reuse.

from __future__ import annotations

import math
import time
from collections.abc import Callable
from datetime import datetime, timezone

from orchestrator.extraction_state import ResourceMine, find_resource_mines
from orchestrator.game_bridge import GameBridge, load_json
from orchestrator.resource_patches import RETIRE_ACTIVE_PATCH_RESOURCE, nearest_patch
from tools.rcon_client import RconClient

Point = tuple[float, float]

_ENTITY_NAMES = {
    "electric-mining-drill", "transport-belt", "fast-transport-belt",
    "express-transport-belt", "inserter", "fast-inserter", "bulk-inserter",
    "stack-inserter", "steel-chest", "passive-provider-chest",
    "medium-electric-pole", "substation",
}


def _candidate_positions(mine: ResourceMine) -> tuple[Point, ...]:
    """Exact positions belonging to the deterministic paired-mine geometry."""
    cx, belt_y = mine.output
    step = mine.expansion_step
    columns = max(mine.drill_count, mine.row_capacity)
    first_x = cx + 4 * step
    last_belt_x = cx + step * (6 + 3 * (columns - 1))
    belt_steps = round(abs(last_belt_x - (cx + 2 * step)))
    points = {
        mine.output,
        (cx + step, belt_y),
        *((cx + step * (2 + offset), belt_y) for offset in range(belt_steps + 1)),
    }
    for index in range(columns):
        x = cx + step * (4 + 3 * index)
        points.update({
            (x, belt_y - 2), (x, belt_y + 2),
            (x, belt_y - 4), (x, belt_y + 4),
        })
    if step == 1:
        anchor_x = first_x - 2
        for x in range(round(anchor_x), round(first_x + 3 * columns), 7):
            points.update({(float(x), belt_y - 4), (float(x), belt_y + 2)})
        points.update({
            (anchor_x - 4, belt_y - 4),
            (anchor_x - 4, belt_y + 2),
            (cx + step, belt_y + 2),
        })
    return tuple(sorted(points))


def _managed_entities(
    client: RconClient, surface: str, force: str, mine: ResourceMine,
) -> list[dict]:
    positions = _candidate_positions(mine)
    lua_positions = "{" + ",".join(
        "{{" + str(x) + "," + str(y) + "}}" for x, y in positions
    ) + "}"
    allowed = "{" + ",".join("['" + name + "']=true" for name in _ENTITY_NAMES) + "}"
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local allowed=" + allowed + ";local out={};local seen={};"
        "for _,p in ipairs(" + lua_positions + ") do for _,e in pairs("
        "s.find_entities_filtered{position=p,radius=0.2,force=f}) do "
        "local name=e.type=='entity-ghost' and e.ghost_name or e.name;"
        "local key=name..':'..e.position.x..':'..e.position.y;"
        "if allowed[name] and not seen[key] then seen[key]=true;"
        "out[#out+1]=name..','..e.position.x..','..e.position.y end end end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = client.command("/sc " + lua).strip()
    entities = []
    for record in raw.split(";"):
        if record:
            name, x, y = record.split(",")
            entities.append({"name": name, "position": {"x": float(x), "y": float(y)}})
    return entities


def retire_depleted_mines(
    client: RconClient,
    bridge: GameBridge,
    surface: str,
    force: str,
    resource: str,
    near: Point,
    emit: Callable[[str], None],
    *,
    timeout: float = 120.0,
) -> int:
    """Order and await bounded teardown of managed mines below the reserve floor."""
    retired = 0
    for mine in find_resource_mines(client, surface, force, resource, near):
        if mine.pending:
            continue
        drill = (mine.output[0] + 4 * mine.expansion_step, mine.shared_belt_y - 2)
        patch = nearest_patch(client, surface, resource, drill)
        remaining = patch.amount if patch is not None and math.dist(patch.nearest, drill) <= 4 else 0
        if remaining >= RETIRE_ACTIVE_PATCH_RESOURCE:
            continue
        entities = _managed_entities(client, surface, force, mine)
        if not entities:
            continue
        actions = [
            {"action": "deconstruct_entity", **entity, "block": "managed_mine"}
            for entity in entities
        ]
        authorization = {
            "approved_actions": ["apply_deconstruction"],
            "denied_actions": [],
            "scope_limits": {
                "max_count": len(actions),
                "block_filter": ["managed_mine"],
            },
            "authorization_timestamp": datetime.now(timezone.utc).isoformat(),
            "authorization_source": "policy",
        }
        report = load_json(bridge.execute_deconstruction(
            authorization, {"actions": actions}, surface=surface, force=force,
        ))
        failed = [
            action for action in report.get("actions", [])
            if action.get("status") != "success"
        ]
        if failed:
            raise RuntimeError(f"Mine retirement rejected {len(failed)} managed entities")
        emit(
            f"MINE RETIREMENT: {resource} patch at {drill} has {remaining:,} left; "
            f"deconstructing {len(actions)} managed mine entities"
        )
        deadline = time.monotonic() + timeout
        while _managed_entities(client, surface, force, mine):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Timed out waiting for construction bots to retire {resource} mine at {drill}"
                )
            time.sleep(2.0)
        retired += 1
    return retired
