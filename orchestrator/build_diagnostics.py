# Path: orchestrator/build_diagnostics.py
# Purpose: Read a stalled stage and say what is actually wrong with it, and sample a plate line's real output tile, without deciding or building anything.

from __future__ import annotations

import math
from collections.abc import Sequence

from orchestrator import live_base
from orchestrator.stage_services import (
    StuckError,
    _DEFAULT_BELT,
    _ROBOPORT_CONSTRUCTION_RADIUS,
    _ROBOPORT_LOGISTIC_RADIUS,
    service_distance,
)
from tools.rcon_client import RconClient

Point = tuple[float, float]

def _side_sample_plate_output(
    plan: dict, origin: Point, machine_count: int,
    belt_type: str = _DEFAULT_BELT, flow_direction: str = "east",
    tap_inserter_type: str = "inserter",
) -> Point:
    """Keep one powered side tap while the output belt stays continuous."""
    if flow_direction not in {"east", "west"}:
        raise ValueError("Plate output flow must be east or west")
    ox, oy = origin
    terminal_chests = [
        action for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == "steel-chest"
        and action["position"]["y"] == oy + 6.5
    ]
    if len(terminal_chests) != 1:
        raise StuckError("plate layout has no unique terminal collector")
    terminal_chest = terminal_chests[0]
    chest_x = terminal_chest["position"]["x"]
    step = 1 if flow_direction == "east" else -1
    inserter_x = chest_x - step
    terminal_inserter = next((
        action for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity", "").endswith("inserter")
        and action["position"] == {"x": inserter_x, "y": oy + 6.5}
    ), None)
    if terminal_inserter is None:
        raise StuckError("plate layout terminal collector has no inserter")

    # Generic high-throughput lines add drain chests below the output belt.
    # Plates keep one low-priority sample only; the continuous belt is primary.
    for phase in plan["phases"]:
        phase["actions"] = [
            action for action in phase["actions"]
            if action is terminal_chest or action is terminal_inserter or not (
                action.get("entity") == "steel-chest"
                and action["position"]["y"] == oy + 8.5
            ) and not (
                action.get("entity", "").endswith("inserter")
                and action["position"]["y"] == oy + 7.5
            )
        ]

    provider = (chest_x, oy + 8.5)
    terminal_chest["position"] = {"x": provider[0], "y": provider[1]}
    terminal_inserter.update({
        "entity": tap_inserter_type,
        "position": {"x": provider[0], "y": provider[1] - 1},
        "direction": "north",
    })
    collector_phase = next(
        phase for phase in plan["phases"] if terminal_chest in phase["actions"]
    )
    collector_phase["actions"].extend([
        {
            "action_type": "place_ghost", "entity": belt_type,
            "position": {"x": inserter_x, "y": oy + 6.5},
            "direction": flow_direction,
        },
        {
            "action_type": "place_ghost", "entity": belt_type,
            "position": {"x": chest_x, "y": oy + 6.5},
            "direction": flow_direction,
        },
        {
            "action_type": "place_ghost", "entity": belt_type,
            "position": {"x": chest_x + step, "y": oy + 6.5},
            "direction": flow_direction,
        },
        {
            "action_type": "place_ghost", "entity": "medium-electric-pole",
            "position": {"x": provider[0], "y": provider[1] + 2},
        },
    ])
    return provider


def _diagnose_blockage(
    client: RconClient, surface: str, force: str, origin: Point,
    substation_position: Point, machine_positions: list[Point],
    logistic_chest_positions: Sequence[Point] = (),
    area: tuple[Point, Point] | None = None,
) -> tuple[str, str] | None:
    """Why is this stage not finishing? Returns (issue, remedy) or None.

    Ordered by what actually blocks construction first: missing items are
    named before coverage, because the probe reports per-ghost coverage
    before global stock -- a drill ghost inside a charging network otherwise
    reads as an electricity issue while zero drills exist anywhere, and the
    run waits out charge windows instead of queuing drills. Bots cannot
    build outside coverage, and a roboport with no power provides no coverage
    at all. Remaining ghosts are then checked for their concrete
    network/material cause. Logistic coverage is checked next because its
    remedy places AND powers a roboport, which can incidentally close a power
    gap near the stage -- the reverse is never true, so diagnosing it before
    machine power lets one round fix both. Machine power is last; it is also
    the only check here that has to poll every machine's live status.
    """
    nearest = live_base.nearest_roboport(client, surface, force, origin)
    if nearest is None:
        return ("no roboport on this surface", "none")
    if math.dist(nearest, origin) > _ROBOPORT_CONSTRUCTION_RADIUS:
        return (
            f"site is {math.dist(nearest, origin):.0f} tiles from the nearest roboport "
            f"(construction radius {_ROBOPORT_CONSTRUCTION_RADIUS:.0f})",
            "coverage",
        )
    if live_base.entity_status_name(client, surface, nearest) == "no_power":
        return (f"covering roboport at {nearest} has no power", "roboport_power")
    if area is not None:
        ghosts = live_base.ghost_blockages(client, surface, force, area)
        # Materials first: ghosts are returned in probe order, so a coverage
        # ghost listed before a material-starved one used to shadow it and the
        # run waited instead of manufacturing the missing item.
        for ghost in ghosts:
            reason = str(ghost.get("reason", "pending"))
            if reason.startswith("missing_material:"):
                item = str(ghost.get("item", reason.split(":", 2)[1]))
                required = int(ghost.get("required", 1))
                available = int(ghost.get("available", 0))
                return (
                    f"ghost {ghost.get('entity', 'entity')} at {ghost['position']} needs "
                    f"{required} {item}, but its network has {available}",
                    f"materials:{item}:{required}",
                )
        stock_cache: dict[str, dict[str, int]] = {}

        def _globally_unstocked(entity: object) -> int:
            """Pending ghosts of this entity awaiting manufacture, else 0.

            A ghost inside a charging network has no network-local stock
            reading, so the probe can only report coverage there. Compare the
            ghost's placing item against force-wide transferable stock: when
            nothing spendable exists, queuing manufacture unblocks the build
            faster than waiting out the charge window. Ghost entity names
            match their placing item for everything the planners emit; an
            unmapped entity keeps its coverage verdict instead of queuing a
            bogus mall target, so only a confirmed double-zero diverts.
            """
            name = str(entity or "")
            if not name:
                return 0
            try:
                if "transferable" not in stock_cache:
                    stock_cache["transferable"] = live_base.transferable_items(
                        client, surface, force,
                    )
                    stock_cache["available"] = live_base.available_items(
                        client, surface, force,
                    )
                if int(stock_cache["transferable"].get(name, 0)) > 0:
                    return 0
                if int(stock_cache["available"].get(name, 0)) > 0:
                    return 0
            except Exception:
                return 0
            return sum(
                1 for other in ghosts
                if other.get("entity") == entity
                and str(other.get("reason", "pending")) != "pending"
            ) or 1

        for ghost in ghosts:
            reason = str(ghost.get("reason", "pending"))
            position = ghost["position"]
            if reason == "out_of_construction_range":
                covering = [
                    port for port in live_base.roboport_positions(
                        client, surface, force,
                    )
                    if service_distance(
                        port, tuple(position), square=False,
                    ) <= _ROBOPORT_CONSTRUCTION_RADIUS
                ]
                if covering:
                    nearest_covering = min(
                        covering, key=lambda port: math.dist(port, position),
                    )
                    status = live_base.entity_status_name(
                        client, surface, nearest_covering,
                    )
                    if status == "no_power":
                        return (
                            f"ghost {ghost.get('entity', 'entity')} at {position} "
                            f"is geometrically covered by unpowered roboport "
                            f"{nearest_covering}",
                            f"roboport_power_at:{nearest_covering[0]}:{nearest_covering[1]}",
                        )
                    unstocked = _globally_unstocked(ghost.get("entity"))
                    if unstocked:
                        entity = str(ghost.get("entity", "entity"))
                        return (
                            f"ghost {entity} at {position} has no transferable "
                            f"{entity} in stock ({unstocked} pending ghost(s)) -- "
                            "making more unblocks it faster than waiting on "
                            "the charging network",
                            f"materials:{entity}:{unstocked}",
                        )
                    return (
                        f"ghost {ghost.get('entity', 'entity')} at {position} is "
                        f"inside roboport {nearest_covering}'s construction area, "
                        "but that network is not active yet",
                        "coverage_charge_wait",
                    )
                return (
                    f"ghost {ghost.get('entity', 'entity')} at {position} is outside "
                    f"construction coverage ({_ROBOPORT_CONSTRUCTION_RADIUS:.0f}-tile radius)",
                    "coverage",
                )
            if reason == "no_construction_robots":
                return (
                    f"ghost {ghost.get('entity', 'entity')} at {position} has no "
                    "construction robots in its logistic network",
                    "none",
                )
            # missing_material ghosts are handled in the materials-first pass
            # above, so they never reach this loop.
    if logistic_chest_positions:
        # Only chests that are actually BUILT are judged here; ones still
        # waiting on a bot are absent from the reply and are the ghost count's
        # business, not a coverage fault.
        served = live_base.logistic_network_ids(client, surface, logistic_chest_positions)
        orphaned = [p for p, network in served.items() if network is None]
        if orphaned:
            return (
                f"{len(orphaned)} logistic chest(s) belong to no logistic network "
                f"(first at {tuple(orphaned[0])}); a chest outside every roboport's "
                f"{_ROBOPORT_LOGISTIC_RADIUS:.0f}-tile supply area can neither supply "
                "nor be supplied",
                "logistic_coverage",
            )
    statuses = live_base.entity_statuses(client, surface, machine_positions)
    unpowered = [p for p in machine_positions if statuses.get(tuple(p)) == "no_power"]
    if unpowered:
        return (f"{len(unpowered)} machine(s) unpowered", "stage_power")
    # Machines are not the whole stage. The inserter that moves the product is
    # what makes a mining row actually deliver, and it sits on the output row --
    # outside the supply area of poles positioned for the machine row. Checking
    # machines alone declared such a stage healthy while its belt backed up and
    # its provider chest stayed empty forever.
    if area is not None:
        stranded = live_base.unpowered_entities(client, surface, area)
        if stranded:
            return (
                f"{len(stranded)} support entity(ies) unpowered "
                f"(first at {stranded[0]}); the stage's machines have power but "
                "something that moves its product does not",
                "entity_power",
            )
    return None
