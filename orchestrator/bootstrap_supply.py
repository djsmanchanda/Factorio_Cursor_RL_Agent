# Path: orchestrator/bootstrap_supply.py
# Purpose: Apply one finite bootstrap-profile seed to an existing connected supply chest.

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from orchestrator import live_base
from tools.rcon_client import RconClient

Point = tuple[float, float]


class BootstrapSupplyError(RuntimeError):
    """The declared bootstrap seed could not be placed in its existing network."""


@dataclass(frozen=True)
class BootstrapSupplyResult:
    targets: Mapping[str, int]
    before: Mapping[str, int]
    inserted: Mapping[str, int]


def ensure_bootstrap_supply(
    client: RconClient,
    surface: str,
    force: str,
    targets: Mapping[str, int],
    reference_point: Point,
) -> BootstrapSupplyResult:
    """Ensure the finite profile seed exists, without placing infrastructure."""
    normalized = {
        str(item): int(count) for item, count in targets.items() if int(count) > 0
    }
    before_all = live_base.available_items(client, surface, force)
    before = {item: int(before_all.get(item, 0)) for item in normalized}
    deficits = {
        item: target - before[item]
        for item, target in normalized.items()
        if before[item] < target
    }
    if not deficits:
        return BootstrapSupplyResult(normalized, before, {})

    entries = ",".join(
        "{'" + item + "'," + str(count) + "}"
        for item, count in sorted(deficits.items())
    )
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local rx,ry=" + str(reference_point[0]) + "," + str(reference_point[1]) + ";"
        "local chests={};for _,c in pairs(s.find_entities_filtered{"
        "name={'passive-provider-chest','storage-chest'},force=f}) do "
        "if s.find_logistic_network_by_position(c.position,f) then "
        "chests[#chests+1]=c end end;"
        "table.sort(chests,function(a,b) local ad=(a.position.x-rx)^2+(a.position.y-ry)^2;"
        "local bd=(b.position.x-rx)^2+(b.position.y-ry)^2;"
        "if ad==bd then return (a.unit_number or 0)<(b.unit_number or 0) end;return ad<bd end);"
        "if #chests==0 then rcon.print('ERROR:no_connected_supply_chest') return end;"
        "for _,spec in ipairs({" + entries + "}) do local remaining=spec[2];"
        "if not prototypes.item[spec[1]] then rcon.print('ERROR:unknown_item:'..spec[1]) return end;"
        "for _,chest in ipairs(chests) do if remaining<=0 then break end;"
        "remaining=remaining-chest.insert{name=spec[1],count=remaining} end;"
        "if remaining>0 then rcon.print('ERROR:no_capacity:'..spec[1]..':'..remaining) return end end;"
        "rcon.print('OK')"
    )
    reply = client.command("/sc " + lua).strip()
    if reply != "OK":
        raise BootstrapSupplyError(
            f"Bootstrap supply insertion failed: {reply or 'empty RCON reply'}"
        )
    after = live_base.available_items(client, surface, force)
    missing = {
        item: target
        for item, target in normalized.items()
        if int(after.get(item, 0)) < target
    }
    if missing:
        raise BootstrapSupplyError(
            "Bootstrap supply remained below target after insertion: "
            + ", ".join(
                f"{item}={after.get(item, 0)}/{target}"
                for item, target in sorted(missing.items())
            )
        )
    inserted = {
        item: int(after.get(item, 0)) - before[item]
        for item in normalized
        if int(after.get(item, 0)) > before[item]
    }
    return BootstrapSupplyResult(normalized, before, inserted)
