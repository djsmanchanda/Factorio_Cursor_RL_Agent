# Path: orchestrator/resource_patches.py
# Purpose: Measure contiguous live resource patches and apply extraction land-value thresholds.

from __future__ import annotations

from dataclasses import dataclass

from tools.rcon_client import RconClient

Point = tuple[float, float]

MINIMUM_NEW_PATCH_RESOURCE = 200_000
RETIRE_ACTIVE_PATCH_RESOURCE = 100_000


@dataclass(frozen=True)
class ResourcePatch:
    nearest: Point
    minimum: Point
    maximum: Point
    amount: int


def nearest_patch(
    client: RconClient,
    surface: str,
    resource: str,
    near: Point,
    *,
    search_radius: float = 400.0,
    minimum_amount: int = 0,
) -> ResourcePatch | None:
    """Return the nearest eight-way contiguous patch meeting the amount floor."""
    if minimum_amount < 0:
        raise ValueError("minimum_amount cannot be negative")
    min_x, min_y = near[0] - search_radius, near[1] - search_radius
    max_x, max_y = near[0] + search_radius, near[1] + search_radius
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local es=s.find_entities_filtered{name='" + resource + "',type='resource',"
        "area={{" + str(min_x) + "," + str(min_y) + "},{" + str(max_x) + "," + str(max_y) + "}}};"
        "local grid={};for i,e in ipairs(es) do "
        "grid[math.floor(e.position.x)..':'..math.floor(e.position.y)]=i end;"
        "local seen={};local best=nil;local bestd=1e18;"
        "for start=1,#es do if not seen[start] then local q={start};local head=1;"
        "seen[start]=true;local amount=0;local nearest=nil;local nd=1e18;"
        "local minx,miny,maxx,maxy=1e18,1e18,-1e18,-1e18;"
        "while head<=#q do local i=q[head];head=head+1;local e=es[i];"
        "local x,y=math.floor(e.position.x),math.floor(e.position.y);"
        "amount=amount+e.amount;minx=math.min(minx,e.position.x);"
        "miny=math.min(miny,e.position.y);maxx=math.max(maxx,e.position.x);"
        "maxy=math.max(maxy,e.position.y);"
        "local d=(e.position.x-(" + str(near[0]) + "))^2+"
        "(e.position.y-(" + str(near[1]) + "))^2;"
        "if d<nd then nd=d;nearest=e.position end;"
        "for dx=-1,1 do for dy=-1,1 do if dx~=0 or dy~=0 then "
        "local j=grid[(x+dx)..':'..(y+dy)];if j and not seen[j] then "
        "seen[j]=true;q[#q+1]=j end end end end end;"
        "if amount>=" + str(minimum_amount) + " and nd<bestd then bestd=nd;"
        "best={nearest.x,nearest.y,minx,miny,maxx,maxy,amount} end end end;"
        "if not best then rcon.print('NONE') else rcon.print(table.concat(best,' ')) end"
    )
    raw = client.command("/sc " + lua).strip()
    if raw == "NONE":
        return None
    fields = raw.split()
    if len(fields) != 7:
        raise ValueError(f"Resource patch survey returned an invalid response: {raw}")
    x, y, minx, miny, maxx, maxy, amount = fields
    return ResourcePatch(
        (float(x), float(y)),
        (float(minx), float(miny)),
        (float(maxx), float(maxy)),
        int(float(amount)),
    )


def patch_for_extraction(
    client: RconClient, surface: str, resource: str, near: Point, *, active: bool,
) -> ResourcePatch | None:
    """Keep an active patch until retirement; apply 200k only to new sites."""
    if active:
        return nearest_patch(client, surface, resource, near)
    return nearest_viable_patch(client, surface, resource, near)

def nearest_viable_patch(
    client: RconClient, surface: str, resource: str, near: Point,
) -> ResourcePatch | None:
    return nearest_patch(
        client, surface, resource, near,
        minimum_amount=MINIMUM_NEW_PATCH_RESOURCE,
    )

def resource_tiles(
    client: RconClient, surface: str, minimum: Point, maximum: Point,
) -> set[tuple[int, int]]:
    """Tile indices covered by any resource entity in the box.

    A cheap region-wide prefilter for box_has_reserved_patch, which is
    expensive per call: it floods a 800x800 area in Lua once per distinct
    resource name it finds. A box holding no resource at all provably touches
    no patch, so surveying the whole search region once lets a caller skip that
    work for every ore-free candidate without weakening the reservation rule.
    """
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};"
        "for _,e in pairs(s.find_entities_filtered{type='resource',area={{"
        + str(minimum[0]) + "," + str(minimum[1]) + "},{" + str(maximum[0]) + ","
        + str(maximum[1]) + "}}}) do "
        "out[#out+1]=math.floor(e.position.x)..','..math.floor(e.position.y) end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = client.command("/sc " + lua).strip()
    tiles: set[tuple[int, int]] = set()
    for record in raw.split(";"):
        if not record:
            continue
        x, _, y = record.partition(",")
        tiles.add((int(x), int(y)))
    return tiles


def box_has_reserved_patch(
    client: RconClient, surface: str, minimum: Point, maximum: Point,
) -> bool:
    """Whether a box touches a patch still worth reserving for extraction."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};local seen={};"
        "for _,e in pairs(s.find_entities_filtered{type='resource',area={{"
        + str(minimum[0]) + "," + str(minimum[1]) + "},{" + str(maximum[0]) + ","
        + str(maximum[1]) + "}}}) do if not seen[e.name] then seen[e.name]=true;"
        "out[#out+1]=e.name..','..e.position.x..','..e.position.y end end;"
        "rcon.print(table.concat(out,';'))"
    )
    raw = client.command("/sc " + lua).strip()
    for record in raw.split(";"):
        if not record:
            continue
        name, x, y = record.split(",")
        patch = nearest_patch(client, surface, name, (float(x), float(y)))
        if patch is None:
            continue
        if patch.amount >= MINIMUM_NEW_PATCH_RESOURCE:
            return True
    return False
