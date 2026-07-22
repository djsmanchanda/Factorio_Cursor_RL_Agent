# Path: tools/build_science_chain.py
# Purpose: Build and verify the full ore-to-research chain: mines -> smelters -> gears -> science packs -> labs.

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.game_bridge import GameBridge, load_json
from planners.local_layout_planner import LocalLayoutPlanner
from planners.sandbox_infrastructure import (
    build_layout_authorization,
    compose_managed_sandbox,
    require_compatible_topology,
)

# Stage layout. Every x is PINNED by generate_chain_link's junction math -
# see the comment on each stage for the equation it satisfies. Machine counts
# are sized so each stage over-supplies the next (science needs only 0.6/s).
IRON_SMELTER = {"recipe": "iron-plate", "machines": 4, "origin": (0, 100)}
COPPER_SMELTER = {"recipe": "copper-plate", "machines": 2, "origin": (20, 100)}
# head_on from iron: cx = turn_col(13) + 1 - belt_west(-3) = 17
GEAR_LINE = {"recipe": "iron-gear-wheel", "machines": 2, "origin": (17, 116)}
# north from copper: cx = turn_col(27) + 1 = 28; south from gears: cx = turn_col(24) + 4 = 28
SCIENCE_LINE = {"recipe": "automation-science-pack", "machines": 4, "origin": (28, 132)}
# head_on from science: cx = turn_col(41) + 1 - belt_west(-3) = 45
LAB_ROW = {"labs": 4, "origin": (45, 148)}

# Roboport anchors covering the whole region (construction radius ~55 tiles).
ANCHORS = [(10, 92), (40, 158)]
ORE_PATCHES = [
    {"item": "iron-ore", "x1": -1, "y1": 95, "x2": 12, "y2": 99, "amount": 500000},
    {"item": "copper-ore", "x1": 19, "y1": 95, "x2": 26, "y2": 99, "amount": 500000},
]


# Chain telemetry. Global research progress is NOT proof on a megabase save:
# the base's own nauvis labs research everything instantly. What proves this
# chain works is items moving through OUR stages and OUR labs consuming packs.
TELEMETRY_QUERY = (
    "/sc local s=game.surfaces['planner-sandbox'] "
    "local area={{-20,90},{70,165}} "
    "local function belt(n) local t=0 "
    "for _,b in pairs(s.find_entities_filtered{type='transport-belt', force='planner', area=area}) do "
    "t=t+b.get_transport_line(1).get_item_count(n)+b.get_transport_line(2).get_item_count(n) end "
    "return t end "
    "local packs,working,total=0,0,0 "
    "for _,l in pairs(s.find_entities_filtered{name='lab', force='planner', area=area}) do total=total+1 "
    "packs=packs+l.get_inventory(defines.inventory.lab_input).get_item_count('automation-science-pack') "
    "if l.status==defines.entity_status.working then working=working+1 end end "
    "rcon.print('ore='..belt('iron-ore')+belt('copper-ore')..' plates='..belt('iron-plate')+belt('copper-plate')"
    "..' gears='..belt('iron-gear-wheel')..' packs_belt='..belt('automation-science-pack')"
    "..' packs_in_labs='..packs..' labs_working='..working..'/'..total)"
)


def _telemetry(bridge: GameBridge) -> dict:
    raw = bridge.command(TELEMETRY_QUERY).strip()
    fields: dict = {"raw": raw}
    for part in raw.split():
        if "=" in part:
            key, value = part.split("=", 1)
            fields[key] = value
    return fields


def build_plans(belt: str, inserter: str) -> list:
    """All stage plans plus the chain links, in build order."""
    planner = LocalLayoutPlanner()
    tiers = {"belt_type": belt, "inserter_type": inserter}
    plans = []

    plans.append(("iron_smelter", planner.generate_line_layout(
        IRON_SMELTER["recipe"], IRON_SMELTER["machines"], *IRON_SMELTER["origin"],
        mining_feed=True, **tiers)))
    plans.append(("copper_smelter", planner.generate_line_layout(
        COPPER_SMELTER["recipe"], COPPER_SMELTER["machines"], *COPPER_SMELTER["origin"],
        mining_feed=True, **tiers)))
    plans.append(("gear_line", planner.generate_line_layout(
        GEAR_LINE["recipe"], GEAR_LINE["machines"], *GEAR_LINE["origin"],
        feed_style="chained", **tiers)))
    plans.append(("science_line", planner.generate_line_layout(
        SCIENCE_LINE["recipe"], SCIENCE_LINE["machines"], *SCIENCE_LINE["origin"],
        feed_style="chained", **tiers)))
    plans.append(("lab_row", planner.generate_lab_row(
        LAB_ROW["labs"], *LAB_ROW["origin"], **tiers)))

    link = dict(consumer_feed_style="chained", **tiers)
    plans.append(("link_iron_to_gears", planner.generate_chain_link(
        IRON_SMELTER["origin"], IRON_SMELTER["recipe"], IRON_SMELTER["machines"],
        GEAR_LINE["origin"], GEAR_LINE["recipe"], GEAR_LINE["machines"],
        junction_side="head_on", **link)))
    plans.append(("link_copper_to_science", planner.generate_chain_link(
        COPPER_SMELTER["origin"], COPPER_SMELTER["recipe"], COPPER_SMELTER["machines"],
        SCIENCE_LINE["origin"], SCIENCE_LINE["recipe"], SCIENCE_LINE["machines"],
        junction_side="north", **link)))
    plans.append(("link_gears_to_science", planner.generate_chain_link(
        GEAR_LINE["origin"], GEAR_LINE["recipe"], GEAR_LINE["machines"],
        SCIENCE_LINE["origin"], SCIENCE_LINE["recipe"], SCIENCE_LINE["machines"],
        junction_side="south", **link)))
    # Labs are not a LINE_RECIPES entry, but a lab row's input belt uses the
    # same CHAINED_BELT_WEST, so the chained science recipe gives the right
    # junction math for the final hop.
    plans.append(("link_science_to_labs", planner.generate_chain_link(
        SCIENCE_LINE["origin"], SCIENCE_LINE["recipe"], SCIENCE_LINE["machines"],
        LAB_ROW["origin"], SCIENCE_LINE["recipe"], LAB_ROW["labs"],
        junction_side="head_on", **link)))
    return plans


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the ore-to-research science chain.")
    parser.add_argument("--script-output", required=True)
    parser.add_argument("--rcon-password", required=True)
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27015)
    parser.add_argument("--belt", default="express-transport-belt")
    parser.add_argument("--inserter", default="stack-inserter")
    parser.add_argument("--technology", default="physical-projectile-damage-1")
    parser.add_argument("--watch-seconds", type=float, default=300.0)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    planner = LocalLayoutPlanner()
    plans = build_plans(args.belt, args.inserter)

    materials: dict = {}
    for _, plan in plans:
        for item, count in planner.material_requirements(plan).items():
            materials[item] = materials.get(item, 0) + count
    # Generous stock: bots consume exactly one item per ghost, but re-runs and
    # rebuilds should not starve.
    materials = {item: count * 3 for item, count in materials.items()}
    composition = compose_managed_sandbox(
        plans, ANCHORS, materials, bots_per_roboport=50, ore_patches=ORE_PATCHES
    )
    plans = composition["plans"]
    print(f"Stages: {len(plans)}; materials: {materials}")
    if args.plan_only:
        return 0

    bridge = GameBridge(
        script_output=Path(args.script_output),
        host=args.rcon_host,
        port=args.rcon_port,
        password=args.rcon_password,
    )
    try:
        require_compatible_topology(bridge)
        authorization = build_layout_authorization(
            composition["infrastructure"] + composition["plans"]
        )
        for name, infrastructure_plan in composition["infrastructure"]:
            report = load_json(bridge.build_layout(authorization, infrastructure_plan))
            if not report.get("ok"):
                print(f"INFRASTRUCTURE {name} FAILED: {report.get('error')}", file=sys.stderr)
                return 1
        bridge.ensure_scaffolding(composition["scaffolding"])
        print(f"Managed scaffolding + ore patches provisioned at anchors {ANCHORS}.")

        for name, plan in plans:
            report = load_json(bridge.build_layout(authorization, plan))
            if not report.get("ok"):
                print(f"STAGE {name} FAILED: {report.get('error')}", file=sys.stderr)
                return 1
            print(f"  {name}: {report['placed_ghosts']} ghosts, "
                  f"{report['placed_entities']} entities, {report.get('removed_entities', 0)} removed")

        status = load_json(bridge.set_research(args.technology))
        if not status.get("ok"):
            print(f"SET RESEARCH FAILED: {status.get('error')}", file=sys.stderr)
            return 1
        print(f"Research target set: {args.technology}")

        # Watch the chain fill stage by stage. Labs consuming packs is the
        # proof; research progress is reported but is confounded on a megabase.
        start = time.monotonic()
        first_packs_at = None
        last_line = ""
        while time.monotonic() - start < args.watch_seconds:
            time.sleep(20)
            elapsed = time.monotonic() - start
            fields = _telemetry(bridge)
            line = fields.get("raw", "")
            consumed = int(fields.get("packs_in_labs", "0") or 0)
            working = fields.get("labs_working", "0/0")
            if (consumed > 0 or working.split("/")[0] != "0") and first_packs_at is None:
                first_packs_at = elapsed
                print(f"[{elapsed:6.1f}s] SCIENCE PACKS REACHED THE LABS: {line}")
            elif line != last_line:
                print(f"[{elapsed:6.1f}s] {line}")
            last_line = line

        research = load_json(bridge.research_status())
        print(f"Final: {last_line}")
        print(f"Research state: current={research.get('current_research')} "
              f"progress={research.get('research_progress')}")
        if first_packs_at is None:
            print("No science packs reached the labs - chain is not delivering.", file=sys.stderr)
            return 2
        print(f"CHAIN IS LIVE: ore -> plates -> gears -> science packs -> labs "
              f"(first packs consumed at {first_packs_at:.0f}s).")
        return 0
    finally:
        bridge.close()


if __name__ == "__main__":
    sys.exit(main())
