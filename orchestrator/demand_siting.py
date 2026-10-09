# Path: orchestrator/demand_siting.py | Purpose: Read where an item is consumed on the live base so refinery siting can weigh output distance to that demand.
"""Demand is read from BUILT requester chests, never from ghosts.

A requester chest's request sections name the items it wants and how many
(``slot.min``). The demand point is the request-weighted centroid of every
built requester chest asking for the item. With no requester asking for it,
the caller's fallback (the reference point) is returned, which leaves refinery
siting exactly as it was before demand was read live.
"""

from __future__ import annotations

from tools.rcon_client import RconClient

Point = tuple[float, float]
last_error: str | None = None  # why the latest read fell back, if it failed


def demand_point(
    client: RconClient, surface: str, force: str, item: str, fallback: Point,
) -> Point:
    """Request-weighted centroid of the built requester chests wanting ``item``."""
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local f=game.forces['" + force + "'];local out={};"
        "for _,c in pairs(s.find_entities_filtered{name='requester-chest',force=f}) do "
        "local ok,sections=pcall(function() return c.get_logistic_sections() end);"
        "if ok and sections then local want=0;"
        "for _,section in pairs(sections.sections) do "
        "for i=1,section.filters_count do local slot=section.get_slot(i);"
        "if slot and slot.value then local name=slot.value.name or slot.value;"
        "if name=='" + item + "' then want=want+(tonumber(slot.min) or 0) end end end end;"
        "if want>0 then out[#out+1]=c.position.x..' '..c.position.y..' '..want end end end;"
        "rcon.print(#out>0 and table.concat(out,';') or 'NONE')"
    )
    # Siting must never fail on this read: any RCON/Lua/parse problem keeps the
    # fallback, and ``last_error`` lets the caller log why demand was not used.
    global last_error
    last_error = None
    try:
        raw = client.command("/sc " + lua).strip()
        if not raw or raw == "NONE":
            return fallback
        total = sum_x = sum_y = 0.0
        for record in raw.split(";"):
            x, y, want = (float(part) for part in record.split())
            sum_x += x * want
            sum_y += y * want
            total += want
        return (sum_x / total, sum_y / total) if total > 0 else fallback
    except Exception as error:  # noqa: BLE001 -- diagnostic read only
        last_error = f"{type(error).__name__}: {str(error)[:120]}"
        return fallback


def consumer_distance(point: Point, demand: Point) -> float:
    """Manhattan distance, the same metric siting uses for belt tiles."""
    return abs(point[0] - demand[0]) + abs(point[1] - demand[1])
