# Path: orchestrator/transport_energy.py | Purpose: Compare observed-route robot transport with a conservative direct-line electricity estimate.
from __future__ import annotations

from dataclasses import dataclass
import json
import math


@dataclass(frozen=True)
class TransportEnergy:
    robot_watts: float
    added_line_watts: float

    def __post_init__(self) -> None:
        if any(not math.isfinite(value) or value < 0
               for value in (self.robot_watts, self.added_line_watts)):
            raise ValueError("transport energy must be finite nonnegative watts")


def prefer_direct_transport(
    evidence: TransportEnergy | None, *, demand: float, existing_capacity: float,
) -> bool | None:
    """Compare electricity only for a known sustained flow; capacity is mandatory.

    None leaves the existing capacity/backlog policy in control. A cost estimate
    never certifies route legality or authorizes construction.
    """
    if not all(math.isfinite(value) and value >= 0
               for value in (demand, existing_capacity)):
        raise ValueError("transport rates must be finite and nonnegative")
    if demand > existing_capacity:
        return True
    if demand == 0 or evidence is None:
        return None
    return evidence.robot_watts > evidence.added_line_watts


def estimate_transport_energy(
    observations: dict, *, ingredient_rates: dict[str, float],
    added_machines: int, proposed_machines: int,
) -> TransportEnergy | None:
    """Price full round trips at live prototype costs; unknown facts stay unknown.

    The direct-line bound charges every added assembler at full power and all
    input/output electric inserters per proposed machine. Belts use no power.
    This deliberately requires substantial savings before optional promotion;
    it is a planning estimate, not measured network consumption.
    """
    if added_machines < 0 or proposed_machines <= 0:
        raise ValueError("invalid proposed machine counts")
    try:
        move = float(observations["joules_per_tile"])
        tick = float(observations["joules_per_tick"])
        speed = float(observations["tiles_per_second"])
        payload = float(observations["payload"])
        machine = float(observations["machine_watts"])
        inserter = float(observations["inserter_watts"])
        values = (move, tick, speed, payload, machine, inserter)
        if any(not math.isfinite(v) or v < 0 for v in values):
            return None
        if min(speed, payload, machine, inserter) <= 0 or move + tick <= 0:
            return None
        watts = 0.0
        for item, rate in ingredient_rates.items():
            distance = float(observations["distances"][item])
            if not math.isfinite(distance) or distance < 0 or not math.isfinite(rate) or rate < 0:
                return None
            watts += rate / payload * 2 * distance * (move + tick * 60 / speed)
        inserter_count = (len(ingredient_rates) + 1) * proposed_machines
        return TransportEnergy(watts, added_machines * machine + inserter_count * inserter)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def observe_transport_energy(
    client, surface: str, force: str, *, machine: str, destination: tuple[float, float],
    ingredient_rates: dict[str, float], added_machines: int, proposed_machines: int,
) -> TransportEnergy | None:
    """One read-only query; unsupported prototype properties disable cost override.

    Distances use the nearest real provider/storage chest containing each input
    in the destination's logistic network. No stock in another network is priced
    as accessible supply. Flight/charging contention and future geometry are not
    measured, so this remains a conservative optional-planning estimate.
    """
    if not hasattr(client, "command") or not ingredient_rates:
        return None
    if len(destination) != 2 or not all(math.isfinite(v) for v in destination):
        return None
    # Encode untrusted names as Lua-compatible JSON string literals. Names in
    # this contract are ASCII prototype/surface identifiers.
    def literal(value: str) -> str:
        if not value.isascii():
            raise ValueError("non-ASCII identifier")
        return json.dumps(value)
    try:
        items = "{" + ",".join(literal(name) for name in ingredient_rates) + "}"
        query = (
            "/sc local ok,result=pcall(function() "
            f"local s=game.surfaces[{literal(surface)}];local f=game.forces[{literal(force)}];"
            f"local dest={{x={destination[0]},y={destination[1]}}};"
            "if not s or not f then return nil end;"
            "local net=s.find_logistic_network_by_position(dest,f);if not net then return nil end;"
            "local p=prototypes.entity['logistic-robot'];"
            f"local m=prototypes.entity[{literal(machine)}];"
            "if not p or not m then return nil end;local inserter_watts=0;"
            "for _,name in ipairs({'inserter','fast-inserter','bulk-inserter'}) do "
            "local i=prototypes.entity[name];if i then inserter_watts=math.max(inserter_watts,i.get_max_energy_usage()*60) end end;"
            "local result={joules_per_tile=p.energy_per_move,joules_per_tick=p.energy_per_tick,"
            "tiles_per_second=p.speed*60*(1+f.worker_robots_speed_modifier),"
            "payload=p.max_payload_size+f.worker_robots_storage_bonus,"
            "machine_watts=m.get_max_energy_usage()*60,inserter_watts=inserter_watts,distances={}};"
            f"for _,item in pairs({items}) do local best=nil;"
            "for _,e in pairs(s.find_entities_filtered{type='logistic-container',force=f}) do "
            "local mode=e.prototype.logistic_mode;"
            "if (mode=='passive-provider' or mode=='storage' or mode=='active-provider') and e.logistic_network==net then "
            "local inv=e.get_inventory(defines.inventory.chest);if inv and inv.get_item_count(item)>0 then "
            "local dx=e.position.x-dest.x;local dy=e.position.y-dest.y;local d=math.sqrt(dx*dx+dy*dy);"
            "if not best or d<best then best=d end end end end;"
            "if not best then return nil end;result.distances[item]=best end;return result end);"
            "rcon.print(ok and result and helpers.table_to_json(result) or '{}')"
        )
        observation = json.loads(client.command(query).strip())
    except (ValueError, TypeError, AttributeError):
        return None
    return estimate_transport_energy(
        observation, ingredient_rates=ingredient_rates,
        added_machines=added_machines, proposed_machines=proposed_machines,
    )
