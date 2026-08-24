# Path: orchestrator/chemical_survey.py
# Purpose: Read-only live shoreline selection for the Nauvis chemical oil cell.

from __future__ import annotations

from orchestrator.live_base import Point, _sc
from tools.rcon_client import RconClient


def nearest_offshore_pump_site(
    client: RconClient, surface: str, near: Point, *, search_radius: float = 400.0,
) -> dict | None:
    """Nearest shoreline where an offshore pump and land pipe can exist."""
    min_x, min_y = near[0] - search_radius, near[1] - search_radius
    max_x, max_y = near[0] + search_radius, near[1] + search_radius
    lua = (
        "local s=game.surfaces['" + surface + "'];local nx,ny=" + str(near[0]) + "," + str(near[1]) + ";"
        "local specs={{'north',0,-1,0.5,-0.5,0,-2},{'east',1,0,1.5,0.5,2,0},"
        "{'south',0,1,0.5,1.5,0,2},{'west',-1,0,-0.5,0.5,-2,0}};"
        "local best=nil;local bd=1e18;"
        "for _,t in pairs(s.find_tiles_filtered{name={'water','deepwater'},area={{" +
        str(min_x) + "," + str(min_y) + "},{" + str(max_x) + "," + str(max_y) + "}}}) do "
        "local x,y=t.position.x,t.position.y;for _,p in ipairs(specs) do "
        "local n=s.get_tile(x+p[2],y+p[3]).name;"
        "if n~='water' and n~='deepwater' then local px,py=x+p[4],y+p[5];"
        "local ox,oy=x+p[6],y+p[7];local clear=true;"
        "local qx,qy=-p[3],p[2];for side=-1,1 do "
        "local wn=s.get_tile(x+qx*side,y+qy*side).name;"
        "local ln=s.get_tile(x+p[2]+qx*side,y+p[3]+qy*side).name;"
        "if (wn~='water' and wn~='deepwater') or ln=='water' or ln=='deepwater' then clear=false end end;"
        "for step=1,4 do local tx=x+p[2]*step;local ty=y+p[3]*step;"
        "local tn=s.get_tile(tx,ty).name;if tn=='water' or tn=='deepwater' then clear=false end end;"
        "local dir=defines.direction[p[1]];if clear and s.can_place_entity{name='offshore-pump',"
        "position={px,py},direction=dir,force='neutral'} then local d=(px-nx)^2+(py-ny)^2;"
        "if d<bd then bd=d;best={px,py,ox,oy,p[1]} end end end end end;"
        "if not best then rcon.print('NONE') else rcon.print(table.concat(best,' ')) end"
    )
    raw = _sc(client, lua)
    if raw == "NONE":
        return None
    px, py, ox, oy, direction = raw.split()
    return {
        "position": (float(px), float(py)), "output": (float(ox), float(oy)),
        "resource": "water", "direction": direction,
    }
