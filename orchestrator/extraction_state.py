# Path: orchestrator/extraction_state.py
# Purpose: Read-only live observations needed to reconcile and size local resource extraction.

from __future__ import annotations

import math
from dataclasses import dataclass

from tools.rcon_client import RconClient

Point = tuple[float, float]


def _sc(client: RconClient, lua: str) -> str:
    return client.command("/sc " + lua).strip()


@dataclass(frozen=True)
class ResourceMine:
    output: Point
    drill_count: int
    pending: bool = False


@dataclass(frozen=True)
class ExtractionEntity:
    kind: str
    position: Point
    built: bool


def mining_productivity_bonus(
    client: RconClient, force: str,
) -> float:
    """Read the force bonus used by newly placed electric mining drills."""
    raw = _sc(
        client,
        "local f=game.forces['" + force + "'];"
        "if not f then rcon.print('NONE') else "
        "rcon.print(f.mining_drill_productivity_bonus) end",
    )
    if raw == "NONE":
        raise ValueError(f"Unknown force {force!r}")
    bonus = float(raw)
    if not math.isfinite(bonus) or bonus < 0:
        raise ValueError(f"Invalid mining productivity bonus: {raw!r}")
    return bonus



def pending_plate_smelter(
    client: RconClient, surface: str, force: str, ore: str, near: Point,
) -> bool:
    """Whether an incomplete deterministic furnace row requests this ore."""
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local area={{" + str(near[0] - 320) + "," + str(near[1] - 320) + "},{"
        + str(near[0] + 320) + "," + str(near[1] + 320) + "}};"
        "local machines=s.find_entities_filtered{name='electric-furnace',force=f,area=area};"
        "for _,g in pairs(s.find_entities_filtered{type='entity-ghost',force=f,area=area}) do "
        "if g.ghost_name=='electric-furnace' then table.insert(machines,g) end end;"
        "local function has_machine(x,y) for _,e in pairs(machines) do if "
        "math.abs(e.position.x-x)<0.1 and math.abs(e.position.y-y)<0.1 then return true end end "
        "return false end;local function find_at(x,y) return "
        "s.find_entities_filtered{force=f,position={x,y},radius=0.2} end;"
        "local function output_at(x,y) for _,e in pairs(find_at(x,y)) do "
        "local n=e.type=='entity-ghost' and e.ghost_name or e.name;if "
        "n=='passive-provider-chest' or n=='steel-chest' then return true end end return false end;"
        "local function requests(x,y) for _,e in pairs(find_at(x,y)) do local ok,name=pcall(function() "
        "local sec=e.get_logistic_sections();local slot=sec.sections[1] and sec.sections[1].get_slot(1);"
        "return slot and slot.value and (slot.value.name or slot.value) end);"
        "if ok and name=='" + ore + "' then return true end end return false end;"
        "for _,e in pairs(machines) do local x,y=e.position.x,e.position.y;"
        "if not has_machine(x-3,y) then local n=1;local pending=e.type=='entity-ghost';"
        "local ok,r=pcall(function() return e.get_recipe() end);if not ok or not r then pending=true end;"
        "while has_machine(x+3*n,y) do for _,m in pairs(machines) do if "
        "math.abs(m.position.x-(x+3*n))<0.1 and math.abs(m.position.y-y)<0.1 then "
        "local rok,rr=pcall(function() return m.get_recipe() end);"
        "if m.type=='entity-ghost' or not rok or not rr then pending=true end end end;n=n+1 end;"
        "if pending and requests(x-3,y-5) and output_at(x+3*n,y+3) then "
        "rcon.print('1');return end end end;rcon.print('0')"
    )
    return _sc(client, lua) == "1"
def _parse_entities(raw: str) -> list[ExtractionEntity]:
    if not raw:
        return []
    entities = []
    for record in raw.split(";"):
        kind, x, y, built = record.split(",")
        entities.append(ExtractionEntity(kind, (float(x), float(y)), built == "1"))
    return entities


def _classify_direct_mine(
    entities: list[ExtractionEntity], near: Point,
) -> ResourceMine | None:
    """Classify complete and interrupted direct-mine geometry deterministically."""
    by_kind: dict[str, dict[Point, bool]] = {}
    for entity in entities:
        states = by_kind.setdefault(entity.kind, {})
        states[entity.position] = states.get(entity.position, False) or entity.built

    drills = by_kind.get("drill", {})
    belts = by_kind.get("belt", {})
    inserters = by_kind.get("inserter", {})
    candidates: list[tuple[float, ResourceMine]] = []
    for chest, chest_built in by_kind.get("chest", {}).items():
        inserter = (chest[0] - 1, chest[1])
        endpoint = (chest[0] - 2, chest[1])
        if inserter not in inserters or endpoint not in belts:
            continue
        row = sorted(
            (position, built) for position, built in drills.items()
            if abs(chest[1] - (position[1] + 2)) < 0.1
            and 4 <= chest[0] - position[0] <= 20
            and abs(((chest[0] - position[0]) - 4) % 3) < 0.1
        )
        if not row:
            continue
        expected_count = round((chest[0] - row[0][0][0] - 1) / 3)
        expected = {
            (chest[0] - 4 - 3 * index, chest[1] - 2)
            for index in range(expected_count)
        }
        row_states = dict(row)
        first_x = min(position[0] for position in expected)
        belt_xs = [first_x + step for step in range(round(chest[0] - 2 - first_x) + 1)]
        complete = (
            chest_built
            and inserters[inserter]
            and expected == set(row_states)
            and all(row_states.get(position, False) for position in expected)
            and all(belts.get((x, chest[1]), False) for x in belt_xs)
        )
        mine = ResourceMine(chest, expected_count, pending=not complete)
        candidates.append(((chest[0] - near[0]) ** 2 + (chest[1] - near[1]) ** 2, mine))
    if candidates:
        return min(candidates, key=lambda candidate: candidate[0])[1]

    pending_drills = list(drills)
    if pending_drills:
        drill = min(
            pending_drills,
            key=lambda p: (p[0] - near[0]) ** 2 + (p[1] - near[1]) ** 2,
        )
        return ResourceMine((drill[0] + 4, drill[1] + 2), 1, pending=True)
    return None


def find_resource_mine(
    client: RconClient,
    surface: str,
    force: str,
    resource: str,
    near: Point,
) -> ResourceMine | None:
    """Find this planner's complete or partially submitted extraction row."""
    lua = (
        "local s=game.surfaces['" + surface + "'];"
        "local f=game.forces['" + force + "'];local out={};"
        "local function add(k,e,b) table.insert(out,k..','..e.position.x..','.."
        "e.position.y..','..(b and '1' or '0')) end;"
        "local function ghosts(kind,p) local found={};for _,g in pairs("
        "s.find_entities_filtered{type='entity-ghost',force=f,position=p,radius=0.2}"
        ") do if g.ghost_type==kind then table.insert(found,g) end end return found end;"
        "local drills=s.find_entities_filtered{name='electric-mining-drill',force=f};"
        "for _,d in pairs(drills) do local t=d.mining_target;if t and "
        "t.name=='" + resource + "' then add('drill',d,true) end end;"
        "for _,g in pairs(s.find_entities_filtered{type='entity-ghost',force=f}) do "
        "if g.ghost_name=='electric-mining-drill' and "
        "#s.find_entities_filtered{type='resource',name='" + resource + "',"
        "area={{g.position.x-1.5,g.position.y-1.5},"
        "{g.position.x+1.5,g.position.y+1.5}},limit=1}>0 then "
        "add('drill',g,false) end end;"
        "local chests=s.find_entities_filtered{name={'passive-provider-chest','steel-chest'},force=f};"
        "for _,g in pairs(s.find_entities_filtered{type='entity-ghost',force=f}) do "
        "if g.ghost_name=='passive-provider-chest' or g.ghost_name=='steel-chest' "
        "then table.insert(chests,g) end end;"
        "for _,c in pairs(chests) do local cp=c.position;local ins={};"
        "for _,e in pairs(s.find_entities_filtered{type='inserter',force=f,"
        "position={cp.x-1,cp.y},radius=0.2}) do table.insert(ins,e) end;"
        "for _,g in pairs(ghosts('inserter',{cp.x-1,cp.y})) do table.insert(ins,g) end;"
        "local endbelt=s.find_entities_filtered{type='transport-belt',force=f,"
        "position={cp.x-2,cp.y},radius=0.2};"
        "for _,g in pairs(ghosts('transport-belt',{cp.x-2,cp.y})) do "
        "table.insert(endbelt,g) end;if #ins>0 and #endbelt>0 then "
        "add('chest',c,c.type~='entity-ghost');"
        "for _,e in pairs(ins) do add('inserter',e,e.type~='entity-ghost') end;"
        "for x=cp.x-20,cp.x-2,1 do for _,e in pairs("
        "s.find_entities_filtered{type='transport-belt',force=f,"
        "position={x,cp.y},radius=0.2}) do add('belt',e,true) end;"
        "for _,g in pairs(ghosts('transport-belt',{x,cp.y})) do "
        "add('belt',g,false) end end end end;"
        "rcon.print(table.concat(out,';'))"
    )
    return _classify_direct_mine(_parse_entities(_sc(client, lua)), near)
