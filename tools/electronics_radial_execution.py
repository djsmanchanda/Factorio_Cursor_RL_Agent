# Path: tools/electronics_radial_execution.py
# Purpose: Execute a composed electronics bundle with atomic infrastructure and
#          deterministic center-out production ghost construction.

from __future__ import annotations

import json
from collections import defaultdict, deque
from typing import Callable, Mapping, Sequence

from orchestrator.game_bridge import GameBridge
from planners.electronics_world import ElectronicsWorldSpec
from planners.sandbox_infrastructure import CANONICAL_ROBOPORT_HUB, build_layout_authorization
from tools.electronics_execution import (
    ElectronicsExecutionError,
    _build_group,
    _load_report,
    _mutated,
    _scaffold,
    _seed_ore,
    _seed_water,
    prepare_existing_topology,
    production_materials,
    validate_layout_report,
    validate_live_report,
    wait_for_game_ticks,
)
from tools.rcon_client import RconClient
from tools.spidertron_build import (
    build_ring,
    cleanup_spidertron,
    read_construction_radius,
    spawn_spidertron,
)
from tools.spidertron_geometry import Point, concentric_rings

_DEFAULT_SURFACE = "planner-sandbox"


class RadialExecutionError(ElectronicsExecutionError):
    """A radial construction precondition or ring failed."""


def flatten_production_actions(named_plans: Sequence[tuple[str, dict]]) -> list[dict]:
    """Return production actions in bundle order, retaining every original field."""
    actions = [
        action
        for _, plan in named_plans
        for phase in plan.get("phases", [])
        for action in phase.get("actions", [])
    ]
    if not actions:
        raise ValueError("Cannot construct an electronics bundle with no production actions")
    return actions


def _action_position(action: Mapping) -> Point:
    position = action.get("position")
    if not isinstance(position, Mapping):
        raise ValueError("Production action needs a position object for radial construction")
    try:
        return float(position["x"]), float(position["y"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Production action position needs numeric x and y values") from exc


def partition_actions_into_rings(
    actions: Sequence[dict], center: Point, ring_width: float
) -> list[list[dict]]:
    """Bucket complete action dictionaries in deterministic Chebyshev shells.

    ``concentric_rings`` owns the geometry and sorted point order. A queue per
    position keeps duplicate-position actions in their original bundle order.
    """
    positions = [_action_position(action) for action in actions]
    by_position: dict[Point, deque[dict]] = defaultdict(deque)
    for position, action in zip(positions, actions):
        by_position[position].append(action)
    return [
        [by_position[position].popleft() for position in ring]
        for ring in concentric_rings(positions, center, ring_width)
    ]


def _network_report(client: RconClient, surface: str) -> dict:
    """Measure the placed planner backbone before any production ghosts exist."""
    lua = (
        f"local s=game.surfaces['{surface}'];"
        "if not s then rcon.print('E:no-surface') return end;"
        "local f=game.forces.planner;local electric={};"
        "local poles=s.find_entities_filtered{type='electric-pole',force=f};"
        "for _,e in pairs(poles) do local ok,id=pcall(function() return e.electric_network_id end);if ok and id then electric[id]=true end end;"
        "local roboports=s.find_entities_filtered{type='roboport',force=f};local logistic={};local missing=0;"
        "for _,e in pairs(roboports) do local n=e.logistic_network;local id=n and n.network_id or nil;"
        "if id then logistic[id]=true else missing=missing+1 end end;"
        "local ec=0;for _ in pairs(electric) do ec=ec+1 end;"
        "local lc=0;for _ in pairs(logistic) do lc=lc+1 end;"
        "rcon.print(helpers.table_to_json({electric_poles=#poles,electric_networks=ec,"
        "roboports=#roboports,roboport_networks=lc,roboports_without_network=missing}))"
    )
    raw = client.command("/sc " + lua).strip()
    if raw == "E:no-surface":
        raise RadialExecutionError(f"Infrastructure network check found no surface {surface!r}")
    try:
        report = json.loads(raw)
        return {key: int(report[key]) for key in (
            "electric_poles", "electric_networks", "roboports", "roboport_networks",
            "roboports_without_network",
        )}
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise RadialExecutionError(
            f"Infrastructure network check returned malformed data: {raw!r}"
        ) from exc


def assert_infrastructure_networks(client: RconClient, surface: str) -> dict:
    """Require one built electric and roboport network before placing production."""
    report = _network_report(client, surface)
    failures = []
    if not report["electric_poles"] or report["electric_networks"] != 1:
        failures.append(
            f"electric_poles={report['electric_poles']}, electric_networks={report['electric_networks']}"
        )
    if (
        not report["roboports"]
        or report["roboport_networks"] != 1
        or report["roboports_without_network"]
    ):
        failures.append(
            "roboports=" + str(report["roboports"])
            + ", roboport_networks=" + str(report["roboport_networks"])
            + ", roboports_without_network=" + str(report["roboports_without_network"])
        )
    if failures:
        raise RadialExecutionError(
            "Infrastructure is fragmented before production construction: " + "; ".join(failures),
            report=report,
        )
    return report


def _ring_positions(actions: Sequence[dict]) -> list[Point]:
    return [_action_position(action) for action in actions]


def execute_electronics_bundle_radial(
    bridge: GameBridge,
    rcon_client: RconClient,
    bundle: Mapping,
    *,
    world: ElectronicsWorldSpec,
    existing_topology: str,
    settle_ticks: int,
    settle_timeout_seconds: float,
    ring_width: float | None = None,
    surface: str = _DEFAULT_SURFACE,
    roboports: int = 4,
    batteries: int = 2,
    bots: int = 50,
    emit: Callable[[str], None] = print,
) -> dict:
    """Build atomic infrastructure, then build production rings from the hub outward."""
    infrastructure = list(bundle["infrastructure"])
    production = list(bundle["plans"])
    production_actions = flatten_production_actions(production)
    production_authorization = build_layout_authorization(production)
    materials = production_materials(production)
    reports: list[dict] = []
    spidertron_started = False
    ring_reports: list[dict] = []
    try:
        prepare_existing_topology(bridge, existing_topology)
        water_report = _seed_water(bridge, world, emit)
        reports.append(water_report)
        ore_report = _seed_ore(bridge, world, emit)
        reports.append(ore_report)
        infrastructure_report = _build_group(bridge, "infrastructure", infrastructure, emit)
        reports.append(infrastructure_report)
        network_report = assert_infrastructure_networks(rcon_client, surface)
        emit(
            "infrastructure networks: "
            f"electric={network_report['electric_networks']}, "
            f"roboport={network_report['roboport_networks']}"
        )

        unit = spawn_spidertron(
            rcon_client, surface, CANONICAL_ROBOPORT_HUB,
            roboports=roboports, batteries=batteries, bots=bots,
        )
        spidertron_started = True
        radius = read_construction_radius(rcon_client)
        actual_ring_width = radius if ring_width is None else ring_width
        if actual_ring_width <= 0:
            raise ValueError("ring_width must be positive")
        rings = partition_actions_into_rings(
            production_actions, CANONICAL_ROBOPORT_HUB, actual_ring_width
        )
        step = radius * 0.9
        emit(
            f"mobile hub: spidertron unit={unit} construction_radius={radius:.1f} "
            f"ring_width={actual_ring_width:.1f} rings={len(rings)}"
        )
        for index, ring_actions in enumerate(rings):
            ring_plan = {"phases": [{"name": f"ring_{index}", "actions": ring_actions}]}
            ring_report = validate_layout_report(
                _load_report(bridge.build_layout(production_authorization, ring_plan)), f"production/ring_{index}"
            )
            reports.append(ring_report)
            completed = build_ring(
                rcon_client,
                surface,
                _ring_positions(ring_actions),
                step=step,
                bots=bots,
                materials=materials,
                settle_seconds=0.6,
                stop_patience=3,
                stop_max_wait=90.0,
                park_offset=1.5,
                max_passes=3,
                emit=emit,
            )
            if not completed:
                raise RadialExecutionError(f"Production ring {index} stalled before the next ring")
            ring_reports.append(ring_report)
            emit(f"production/ring_{index}: actions={len(ring_actions)} built")

        scaffold_report = _scaffold(bridge, bundle["scaffolding"], materials, emit)
        reports.append(scaffold_report)
        emit(f"settling for {settle_ticks} Factorio ticks")
        wait_for_game_ticks(bridge, settle_ticks, timeout_seconds=settle_timeout_seconds)
        live = validate_live_report(_load_report(bridge.verify_electronics_execution()))
        return {
            "infrastructure": infrastructure_report,
            "infrastructure_networks": network_report,
            "production_rings": ring_reports,
            "scaffolding": scaffold_report,
            "live": live,
        }
    finally:
        if spidertron_started:
            cleanup_spidertron(rcon_client)
        if _mutated(*reports):
            bridge.save_game()