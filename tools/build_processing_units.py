# Path: tools/build_processing_units.py
# Purpose: Build the unified fluid half of the processing-unit chain and verify it live.

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.fluid_systems import validate_network_purity
from orchestrator.game_bridge import GameBridge, load_json
from planners.electronics_block import build_electronics_block
from planners.electronics_world import load_electronics_world_spec
from planners.fluid_layouts import (
    fluid_chain_link_segments,
    fluid_chain_link_trunk,
    fluid_network_segments,
    fluid_source_segment,
    generate_fluid_chain_link,
    generate_fluid_machine_row,
    generate_fluid_source,
    header_attachment,
    source_attachment,
)
from planners.infrastructure import (
    POLE_SPECS,
    local_power_anchors,
    plan_power_network,
    plan_roboport_network,
    power_poles,
    roboport_positions,
    strip_local_power,
    validate_power_connectivity,
    validate_roboport_network,
)
from planners.local_layout_planner import LocalLayoutPlanner
from planners.sandbox_infrastructure import (
    CANONICAL_POWER_SOURCE,
    CANONICAL_ROBOPORT_HUB,
    build_layout_authorization,
    topology_is_compatible,
    roboport_power_sites,
)

STAGES = [
    ("crude_source", {"kind": "crude-oil", "origin": (200, 200), "run": 24}),
    ("refinery", {"recipe": "basic-oil-processing", "machines": 2, "origin": (200, 216)}),
    ("water_source", {"kind": "water", "origin": (200, 244), "run": 24}),
    ("sulfur", {"recipe": "sulfur", "machines": 2, "origin": (200, 260)}),
    ("plastic", {"recipe": "plastic-bar", "machines": 2, "origin": (200, 288)}),
    ("sulfuric_acid", {"recipe": "sulfuric-acid", "machines": 2, "origin": (200, 316)}),
]

LINKS = [
    ("crude-oil", "crude_source", ["refinery"]),
    ("petroleum-gas", "refinery", ["sulfur", "plastic"]),
    ("water", "water_source", ["sulfur", "sulfuric_acid"]),
]

# Reserved western corridors keep infrastructure out of the fluid geometry.
TRUNK_COLUMNS = {"water": 193, "petroleum-gas": 190, "crude-oil": 187}
POWER_SOURCE = CANONICAL_POWER_SOURCE
POWER_SPINE_X = 185
ROBOPORT_SITES = [
    {"name": "canonical_hub", "position": CANONICAL_ROBOPORT_HUB},
    {"name": "north", "position": (196, 196), "extent": ((175, 191), (225, 227))},
    {"name": "middle", "position": (196, 250), "extent": ((187, 239), (225, 297))},
    {"name": "south", "position": (196, 310), "extent": ((187, 298), (210, 326))},
]
ENTITY_SIZES = {
    "electric-energy-interface": 2,
    "big-electric-pole": 2,
    "substation": 2,
    "roboport": 4,
    "oil-refinery": 5,
    "chemical-plant": 3,
    "assembling-machine-2": 3,
    "storage-tank": 3,
    "passive-provider-chest": 1,
    "storage-chest": 1,
}


def _authorization(plans: list) -> dict:
    return build_layout_authorization(plans)


def _raw_stage_plans() -> list:
    plans = []
    for name, spec in STAGES:
        if "kind" in spec:
            plan = generate_fluid_source(
                spec["kind"], spec["origin"][0], spec["origin"][1], run_length=spec["run"]
            )
        else:
            plan = generate_fluid_machine_row(
                spec["recipe"], spec["machines"], spec["origin"][0], spec["origin"][1],
                belt_type="express-transport-belt", inserter_type="stack-inserter",
            )
        plans.append((name, plan))
    return plans


def build_plans() -> list:
    """Production stages with row-local power removed.

    Their medium poles remain. The factory-wide power plan reuses each row's
    original substation anchor, preserving the row generator's supply geometry.
    """
    return [(name, strip_local_power(plan)) for name, plan in _raw_stage_plans()]


def build_infrastructure_plans() -> list:
    """Exactly one source/grid and one connected robot network for all stages."""
    robots = plan_roboport_network(ROBOPORT_SITES)
    power_sites = []
    for name, plan in _raw_stage_plans():
        for index, anchor in enumerate(local_power_anchors(plan)):
            power_sites.append({
                "name": f"{name}_{index}",
                "substation": anchor,
                "pole_anchor": (POWER_SPINE_X, anchor[1]),
            })
    power_sites.extend(roboport_power_sites(roboport_positions(robots)))

    power = plan_power_network(power_sites, POWER_SOURCE)
    return [("unified_power", power), ("unified_roboports", robots)]


def stage_segments() -> list:
    """Purity segments of every generated stage."""
    segments = []
    for _, spec in STAGES:
        ox, oy = spec["origin"]
        if "kind" in spec:
            segments.append(fluid_source_segment(spec["kind"], ox, oy, spec["run"]))
        else:
            segments.extend(fluid_network_segments(spec["recipe"], spec["machines"], ox, oy))
    return segments


def _attachment(stage: str, fluid: str) -> tuple:
    spec = dict(STAGES)[stage]
    ox, oy = spec["origin"]
    if "kind" in spec:
        return source_attachment(ox, oy)
    return header_attachment(spec["recipe"], fluid, spec["machines"], ox, oy)["attach"]


def link_routes() -> dict:
    return {
        fluid: (
            _attachment(producer, fluid),
            [_attachment(consumer, fluid) for consumer in consumers],
            TRUNK_COLUMNS[fluid],
        )
        for fluid, producer, consumers in LINKS
    }


def build_link_plans() -> list:
    """Build one deterministic, purity-validated chain link per fluid."""
    routes = link_routes()
    trunks = {
        fluid: {
            "fluid": fluid,
            "separated_by_pump": False,
            "tiles": fluid_chain_link_trunk(*route),
        }
        for fluid, route in routes.items()
    }
    stages = stage_segments()

    plans, segments = [], []
    for fluid, (from_point, to_points, trunk_x) in routes.items():
        foreign = stages + [trunks[other] for other in trunks if other != fluid]
        plans.append((
            f"link_{fluid}",
            generate_fluid_chain_link(from_point, to_points, fluid, trunk_x, foreign),
        ))
        segments += fluid_chain_link_segments(from_point, to_points, fluid, trunk_x, foreign)

    validate_network_purity(stages + segments)
    claimed: dict = {}
    for segment in stages + segments:
        for tile in segment["tiles"]:
            if claimed.setdefault(tuple(tile), segment["fluid"]) != segment["fluid"]:
                raise ValueError(f"Tile {tile} claimed by two fluids")
    return plans


def _plan_actions(named_plans: list) -> list:
    return [
        (plan_name, phase["name"], action)
        for plan_name, plan in named_plans
        for phase in plan["phases"]
        for action in phase["actions"]
    ]


def validate_processing_bundle(infrastructure: list, production: list) -> None:
    """Validate topology and collisions across the final composed plan bundle."""
    infrastructure_by_name = dict(infrastructure)
    power = infrastructure_by_name["unified_power"]
    robots = infrastructure_by_name["unified_roboports"]
    validate_roboport_network(robots, ROBOPORT_SITES)

    all_actions = _plan_actions(infrastructure + production)
    composed_power_plan = {
        "phases": [
            {"name": f"{plan_name}/{phase['name']}", "actions": phase["actions"]}
            for plan_name, plan in infrastructure + production
            for phase in plan["phases"]
        ]
    }
    validate_power_connectivity(composed_power_plan)

    poles = power_poles(composed_power_plan)
    for roboport in roboport_positions(robots):
        if not any(
            abs(roboport[0] - position[0]) <= POLE_SPECS[name]["supply"] + 2
            and abs(roboport[1] - position[1]) <= POLE_SPECS[name]["supply"] + 2
            for name, position in poles
        ):
            raise ValueError(f"Roboport at {roboport} is outside every pole supply area")

    sources = [
        action for _, _, action in all_actions
        if action.get("action_type") in {"place_entity", "place_ghost"}
        and action.get("entity") == "electric-energy-interface"
    ]
    if len(sources) != 1:
        raise ValueError(f"Processing bundle needs exactly one power source, found {len(sources)}")

    placements = [
        (plan_name, phase_name, action)
        for plan_name, phase_name, action in all_actions
        if action.get("action_type") in {"place_entity", "place_ghost"}
    ]
    for index, (left_plan, left_phase, left) in enumerate(placements):
        left_position = left["position"]
        left_size = ENTITY_SIZES.get(left["entity"], 1)
        for right_plan, right_phase, right in placements[index + 1:]:
            right_position = right["position"]
            right_size = ENTITY_SIZES.get(right["entity"], 1)
            if (
                abs(left_position["x"] - right_position["x"]) < (left_size + right_size) / 2
                and abs(left_position["y"] - right_position["y"]) < (left_size + right_size) / 2
            ):
                raise ValueError(
                    f"Composed plan collision: {left_plan}/{left_phase} {left['entity']} at "
                    f"{left_position} overlaps {right_plan}/{right_phase} {right['entity']} "
                    f"at {right_position}"
                )


def wait_for_game_ticks(
    bridge: GameBridge,
    tick_count: int,
    *,
    timeout_seconds: float = 180.0,
    max_stagnant_polls: int = 20,
    poll_seconds: float = 0.25,
) -> None:
    """Wait on game ticks and fail on wall-clock timeout or a stalled simulation."""
    if tick_count < 0:
        raise ValueError("settle ticks must be non-negative")
    start_tick = int(bridge.command("/sc rcon.print(game.tick)").strip())
    target_tick = start_tick + tick_count
    deadline = time.monotonic() + timeout_seconds
    last_tick = start_tick
    stagnant_polls = 0
    while last_tick < target_tick:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Factorio did not reach tick {target_tick} before timeout")
        time.sleep(poll_seconds)
        current_tick = int(bridge.command("/sc rcon.print(game.tick)").strip())
        if current_tick <= last_tick:
            stagnant_polls += 1
            if stagnant_polls >= max_stagnant_polls:
                raise TimeoutError(f"Factorio tick stalled at {current_tick}")
        else:
            stagnant_polls = 0
        last_tick = current_tick


def prepare_existing_topology(bridge: GameBridge, mode: str) -> dict:
    """Refuse implicit mutation; reset/reconcile only after an explicit CLI choice."""
    topology = load_json(bridge.inspect_sandbox_topology())
    existing = (
        int(topology.get("planner_factory_entities", 0))
        + int(topology.get("player_factory_entities", 0))
    ) > 0
    if not existing:
        return topology
    if mode == "refuse":
        raise RuntimeError(
            "planner-sandbox already contains factory topology; rerun with "
            "--existing-topology reconcile or reset after inspecting it"
        )

    result = load_json(bridge.reconcile_sandbox_topology(mode, confirm=True))
    if not result.get("ok"):
        raise RuntimeError(f"Topology {mode} failed: {result.get('error')}")
    updated = load_json(bridge.inspect_sandbox_topology())
    if mode == "reconcile" and not topology_is_compatible(updated):
        raise RuntimeError(
            "Reconciled topology does not own the exact canonical source and hub; explicit reset is required"
        )
    return updated

def managed_scaffolding_payload(materials: dict) -> dict:
    """Provision the already-planned network without creating infrastructure."""
    robot_plan = dict(build_infrastructure_plans())["unified_roboports"]
    anchors = [{"x": x, "y": y} for x, y in roboport_positions(robot_plan)]
    anchors[0].update({
        "materials": materials,
        "provider_position": {"x": CANONICAL_ROBOPORT_HUB[0] + 4, "y": CANONICAL_ROBOPORT_HUB[1] - 5},
        "storage_position": {"x": CANONICAL_ROBOPORT_HUB[0] + 6, "y": CANONICAL_ROBOPORT_HUB[1] - 5},
    })
    return {
        "managed_infrastructure": True,
        "anchors": anchors,
        "bots_per_roboport": 50,
    }


def _build_checked(bridge: GameBridge, authorization: dict, name: str, plan: dict) -> bool:
    report = load_json(bridge.build_layout(authorization, plan))
    if not report.get("ok"):
        print(f"STAGE {name} FAILED: {report.get('error')}", file=sys.stderr)
        return False
    print(f"  {name}: {report['placed_ghosts']} ghosts, {report['placed_entities']} entities")
    return True


PROCESSING_VERIFICATION_QUERY = (
    "/sc local s=game.surfaces['planner-sandbox'] local f=game.forces['planner'] "
    "local names={} for k,v in pairs(defines.entity_status) do names[v]=k end "
    "local machines={} for _,n in pairs({'oil-refinery','chemical-plant'}) do "
    "for _,e in pairs(s.find_entities_filtered{name=n,force=f,area={{190,190},{280,340}}}) do "
    "local fluids={} for i=1,#e.fluidbox do local c=e.fluidbox[i] "
    "if c and c.amount>0 then fluids[c.name]=true end end local fluid_names={} "
    "for fluid in pairs(fluids) do fluid_names[#fluid_names+1]=fluid end table.sort(fluid_names) "
    "machines[#machines+1]={recipe=e.get_recipe() and e.get_recipe().name or n,"
    "status=names[e.status] or 'unknown',fluids=fluid_names} end end "
    "table.sort(machines,function(a,b) if a.recipe~=b.recipe then return a.recipe<b.recipe end "
    "return table.concat(a.fluids,',')<table.concat(b.fluids,',') end) "
    "local electric={} for _,e in pairs(s.find_entities_filtered{"
    "type={'electric-pole','electric-energy-interface'},force=f}) do "
    "if e.electric_network_id then electric[tostring(e.electric_network_id)]=true end end "
    "local electric_count=0 for _ in pairs(electric) do electric_count=electric_count+1 end "
    "local logistic={} for _,r in pairs(s.find_entities_filtered{name='roboport',force=f}) do "
    "if r.logistic_network then logistic[tostring(r.logistic_network.network_id)]=true end end "
    "local logistic_count=0 for _ in pairs(logistic) do logistic_count=logistic_count+1 end "
    "local out={machines=machines,power_sources=#s.find_entities_filtered{"
    "name='electric-energy-interface',force=f},electric_networks=electric_count,"
    "logistic_networks=logistic_count} rcon.print(helpers.table_to_json(out))"
)

EXPECTED_MACHINE_COUNTS = {
    "basic-oil-processing": 2,
    "sulfur": 2,
    "plastic-bar": 2,
    "sulfuric-acid": 2,
}
EXPECTED_INPUT_FLUIDS = {
    "basic-oil-processing": {"crude-oil"},
    "sulfur": {"water", "petroleum-gas"},
    "plastic-bar": {"petroleum-gas"},
    "sulfuric-acid": {"water"},
}
BAD_MACHINE_STATUSES = {"no_power", "not_connected", "disabled", "broken"}


def verify_processing_snapshot(raw: str) -> dict:
    """Parse the live report and assert stage, fluid, power, and robot topology."""
    try:
        report = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"Processing verification returned invalid JSON: {raw!r}") from error
    machines = report.get("machines")
    if not isinstance(machines, list):
        raise ValueError("Processing verification is missing machines array")
    counts = Counter(machine.get("recipe") for machine in machines)
    if counts != Counter(EXPECTED_MACHINE_COUNTS):
        raise ValueError(f"Unexpected processing machine counts: {dict(counts)}")
    for machine in machines:
        recipe = machine["recipe"]
        status = machine.get("status")
        if not status or status in BAD_MACHINE_STATUSES:
            raise ValueError(f"{recipe} has invalid live status {status!r}")
        present = set(machine.get("fluids") or [])
        missing = EXPECTED_INPUT_FLUIDS[recipe] - present
        if missing:
            raise ValueError(f"{recipe} is missing live input fluids {sorted(missing)}")
    for field in ("power_sources", "electric_networks", "logistic_networks"):
        if report.get(field) != 1:
            raise ValueError(f"Expected one {field}, got {report.get(field)!r}")
    return report

def main() -> int:
    parser = argparse.ArgumentParser(description="Build the unified fluid stages of processing units.")
    parser.add_argument("--script-output")
    parser.add_argument("--rcon-password")
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27015)
    parser.add_argument("--plan-only", action="store_true")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--world-spec", type=Path)
    source.add_argument(
        "--legacy-fluid-only", action="store_true",
        help="Explicitly opt into the scripted-source legacy fluid demo.",
    )
    parser.add_argument("--settle-ticks", type=int, default=5400)
    parser.add_argument("--settle-timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--existing-topology", choices=["refuse", "reconcile", "reset"], default="refuse"
    )
    args = parser.parse_args()
    if args.world_spec:
        if not args.plan_only:
            parser.error("surveyed electronics execution is disabled; inspect it with --plan-only")
        try:
            world = load_electronics_world_spec(args.world_spec)
            bundle = build_electronics_block(include_processing=True, world=world)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            parser.error(str(error))
        print(
            f"Managed plans: {len(bundle['infrastructure'])}; "
            f"production plans: {len(bundle['plans'])}; live execution: disabled"
        )
        return 0
    if not args.legacy_fluid_only:
        parser.error("--world-spec is required unless --legacy-fluid-only is explicitly selected")
    if not args.plan_only and (not args.script_output or not args.rcon_password):
        parser.error("live execution needs --script-output and --rcon-password")

    infrastructure = build_infrastructure_plans()
    production = build_plans() + build_link_plans()
    validate_processing_bundle(infrastructure, production)
    planner = LocalLayoutPlanner()
    materials: dict = {}
    for _, plan in production:
        for item, count in planner.material_requirements(plan).items():
            materials[item] = materials.get(item, 0) + count
    materials = {item: count * 3 for item, count in materials.items()}
    print(f"Infrastructure plans: {len(infrastructure)}; production plans: {len(production)}; "
          f"materials: {materials}")
    if args.plan_only:
        return 0

    bridge = GameBridge(
        script_output=Path(args.script_output), host=args.rcon_host,
        port=args.rcon_port, password=args.rcon_password,
    )
    try:
        try:
            prepare_existing_topology(bridge, args.existing_topology)
        except RuntimeError as error:
            print(f"TOPOLOGY REFUSED: {error}", file=sys.stderr)
            return 1
        authorization = _authorization(infrastructure + production)
        for name, plan in infrastructure:
            if not _build_checked(bridge, authorization, name, plan):
                return 1

        scaffold_report = load_json(bridge.ensure_scaffolding(managed_scaffolding_payload(materials)))
        if not scaffold_report.get("ok"):
            print(f"MANAGED SCAFFOLDING FAILED: {scaffold_report.get('error')}", file=sys.stderr)
            return 1
        print("Unified network provisioned with bots and materials.")

        for name, plan in production:
            if not _build_checked(bridge, authorization, name, plan):
                return 1

        print(f"Waiting {args.settle_ticks} Factorio ticks for bots and fluid flow...")
        wait_for_game_ticks(
            bridge, args.settle_ticks, timeout_seconds=args.settle_timeout_seconds
        )
        raw_verification = bridge.command(PROCESSING_VERIFICATION_QUERY).strip()
        verification = verify_processing_snapshot(raw_verification)
        print(json.dumps(verification, sort_keys=True))
        return 0
    finally:
        bridge.close()


if __name__ == "__main__":
    sys.exit(main())