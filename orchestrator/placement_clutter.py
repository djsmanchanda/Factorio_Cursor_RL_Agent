# Path: orchestrator/placement_clutter.py
# Purpose: Clear only neutral trees and rocks that overlap an already-approved plan footprint.

from __future__ import annotations

import math
from collections.abc import Callable

from planners.infrastructure_geometry import footprint_tile_indices
from planners.plan_validation import ENTITY_FOOTPRINTS
from tools.rcon_client import RconClient

Point = tuple[float, float]


def _plan_tiles(plan: dict) -> set[tuple[int, int]]:
    tiles: set[tuple[int, int]] = set()
    for phase in plan["phases"]:
        for action in phase["actions"]:
            entity = action.get("entity")
            if "position" not in action or not entity:
                continue
            position = (action["position"]["x"], action["position"]["y"])
            tiles |= footprint_tile_indices(
                position, ENTITY_FOOTPRINTS.get(entity, 1),
            )
    return tiles


def clear_plan_clutter(
    client: RconClient,
    surface: str,
    plan: dict,
    emit: Callable[[str], None],
) -> int:
    """Destroy neutral removable clutter only where this plan will build."""
    tiles = _plan_tiles(plan)
    if not tiles:
        return 0
    min_x, min_y = min(x for x, _ in tiles), min(y for _, y in tiles)
    max_x, max_y = max(x for x, _ in tiles) + 1, max(y for _, y in tiles) + 1
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};"
        "for _,e in pairs(s.find_entities_filtered{type={'tree','simple-entity'},"
        "area={{" + str(min_x) + "," + str(min_y) + "},{" +
        str(max_x) + "," + str(max_y) + "}}}) do "
        "if e.force and e.force.name=='neutral' then local b=e.bounding_box;"
        "out[#out+1]=e.name..','..e.type..','..e.position.x..','..e.position.y..','.."
        "b.left_top.x..','..b.left_top.y..','..b.right_bottom.x..','..b.right_bottom.y "
        "end end;rcon.print(table.concat(out,';'))"
    )
    raw = client.command("/sc " + lua).strip()
    cleared = 0
    for record in raw.split(";"):
        if not record:
            continue
        name, kind, x, y, left, top, right, bottom = record.split(",")
        occupied = {
            (tx, ty)
            for tx in range(math.floor(float(left)), math.ceil(float(right)))
            for ty in range(math.floor(float(top)), math.ceil(float(bottom)))
        }
        if not occupied & tiles:
            continue
        remove = (
            "local s=game.surfaces['" + surface + "'];local e="
            "s.find_entities_filtered{name='" + name + "',type='" + kind + "',"
            "position={" + x + "," + y + "},radius=0.1,force='neutral',limit=1}[1];"
            "if e then e.destroy();rcon.print('OK') else rcon.print('NONE') end"
        )
        if client.command("/sc " + remove).strip() == "OK":
            emit(f"  clearing {name} at ({float(x)}, {float(y)}) (map clutter)")
            cleared += 1
    return cleared
