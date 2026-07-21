# Path: tools/build_processing_units.py
# Purpose: Build the fluid half of the processing-unit chain (oil -> gas -> sulfur/plastic/acid) and verify each stage live.

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.execution_authorizer import authorize_execution
from orchestrator.game_bridge import GameBridge, load_json
from planners.fluid_layouts import generate_fluid_machine_row, generate_fluid_source
from planners.local_layout_planner import LocalLayoutPlanner

# Stages are spaced far apart on empty sandbox ground; each fluid row carries
# its own headers, so the only cross-stage coupling is the fluid a downstream
# row consumes. Anchors sit inside every stage's construction radius (55).
STAGES = [
    ("crude_source", {"kind": "crude-oil", "origin": (200, 200), "run": 24}),
    ("refinery", {"recipe": "basic-oil-processing", "machines": 2, "origin": (200, 216)}),
    ("water_source", {"kind": "water", "origin": (200, 244), "run": 24}),
    ("sulfur", {"recipe": "sulfur", "machines": 2, "origin": (200, 260)}),
    ("plastic", {"recipe": "plastic-bar", "machines": 2, "origin": (200, 288)}),
    ("sulfuric_acid", {"recipe": "sulfuric-acid", "machines": 2, "origin": (200, 316)}),
]
ANCHORS = [(196, 196), (196, 250), (196, 310)]


def _authorization() -> dict:
    return authorize_execution(
        proposal={"allowed_actions": ["project_more_ghosts"], "blocked_actions": [],
                  "requires_human_approval": False, "next_recommended_step": "project_more_ghosts"},
        approved_actions=["project_more_ghosts"],
        authorization_source="policy",
    ).to_dict()


def build_plans() -> list:
    plans = []
    for name, spec in STAGES:
        if "kind" in spec:
            plan = generate_fluid_source(spec["kind"], spec["origin"][0], spec["origin"][1],
                                         run_length=spec["run"])
        else:
            plan = generate_fluid_machine_row(
                spec["recipe"], spec["machines"], spec["origin"][0], spec["origin"][1],
                belt_type="express-transport-belt", inserter_type="stack-inserter")
        plans.append((name, plan))
    return plans


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the fluid stages of the processing-unit chain.")
    parser.add_argument("--script-output", required=True)
    parser.add_argument("--rcon-password", required=True)
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27015)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--settle-seconds", type=float, default=90.0)
    args = parser.parse_args()

    planner = LocalLayoutPlanner()
    plans = build_plans()
    materials: dict = {}
    for _, plan in plans:
        for item, count in planner.material_requirements(plan).items():
            materials[item] = materials.get(item, 0) + count
    materials = {item: count * 3 for item, count in materials.items()}
    print(f"Stages: {len(plans)}; materials: {materials}")
    if args.plan_only:
        return 0

    bridge = GameBridge(script_output=Path(args.script_output), host=args.rcon_host,
                        port=args.rcon_port, password=args.rcon_password)
    try:
        bridge.ensure_scaffolding({
            "anchors": [{"x": x, "y": y, "materials": materials} for x, y in ANCHORS],
            "bots_per_roboport": 50,
        })
        print(f"Scaffolding provisioned at {ANCHORS}.")

        authorization = _authorization()
        for name, plan in plans:
            report = load_json(bridge.build_layout(authorization, plan))
            if not report.get("ok"):
                print(f"STAGE {name} FAILED: {report.get('error')}", file=sys.stderr)
                return 1
            print(f"  {name}: {report['placed_ghosts']} ghosts, {report['placed_entities']} entities")

        print(f"Waiting {args.settle_seconds:.0f}s for bots to build and fluids to flow...")
        time.sleep(args.settle_seconds)

        # Verify: every fluid machine should hold its input fluid and be working.
        query = (
            "/sc local s=game.surfaces['planner-sandbox'] local out={} "
            "for _,n in pairs({'oil-refinery','chemical-plant'}) do "
            "for _,e in pairs(s.find_entities_filtered{name=n, area={{190,190},{280,340}}}) do "
            "local st='?' for k,v in pairs(defines.entity_status) do if v==e.status then st=k end end "
            "local f={} for i=1,#e.fluidbox do local c=e.fluidbox[i] "
            "if c then f[#f+1]=c.name..':'..math.floor(c.amount) end end "
            "out[#out+1]=(e.get_recipe() and e.get_recipe().name or n)..'@'..math.floor(e.position.x)..','"
            "..math.floor(e.position.y)..'='..st..'['..table.concat(f,',')..']' end end "
            "rcon.print(table.concat(out,' | '))"
        )
        print(bridge.command(query).strip())
        return 0
    finally:
        bridge.close()


if __name__ == "__main__":
    sys.exit(main())
