# Path: tools/build_line.py
# Purpose: Plan, authorize, and build a functioning single-recipe production line, then verify throughput.

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.game_bridge import GameBridge, load_json
from planners.local_layout_planner import LINE_RECIPES, LocalLayoutPlanner
from planners.sandbox_infrastructure import (
    build_layout_authorization,
    compose_managed_sandbox,
    require_compatible_topology,
)
from tools.rcon_client import RconClient


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a live production line on planner-sandbox.")
    parser.add_argument("--script-output", required=True)
    parser.add_argument("--rcon-password", required=True)
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27015)
    parser.add_argument("--recipe", default="iron-gear-wheel")
    parser.add_argument("--machines", type=int, default=8)
    parser.add_argument("--origin-x", type=int, default=0)
    parser.add_argument("--origin-y", type=int, default=40)
    parser.add_argument("--anchors", default="10", help="Comma-separated scaffolding anchor x positions to stock")
    parser.add_argument("--mine", action="store_true", help="Feed the line with real miners over a seeded ore patch")
    parser.add_argument("--belt", default="transport-belt", help="Belt tier entity name")
    parser.add_argument("--inserter", default="fast-inserter", help="Inserter tier entity name")
    parser.add_argument("--feed-style", default="chest", choices=["chest", "sideload"])
    parser.add_argument("--verify-seconds", type=float, default=60.0)
    args = parser.parse_args()

    planner = LocalLayoutPlanner()
    plan = planner.generate_line_layout(
        args.recipe, args.machines, args.origin_x, args.origin_y,
        mining_feed=args.mine, belt_type=args.belt, inserter_type=args.inserter,
        feed_style=args.feed_style,
    )
    materials = planner.material_requirements(plan)
    ore_patches = []
    if args.mine:
        ore = LINE_RECIPES[args.recipe]["ingredients"][0]
        ore_patches.append({
            "item": ore,
            "x1": args.origin_x - 1, "y1": args.origin_y - 5,
            "x2": args.origin_x + args.machines * 3, "y2": args.origin_y - 1,
            "amount": 100000,
        })
    composition = compose_managed_sandbox(
        [("line", plan)],
        [int(x) for x in args.anchors.split(",")],
        materials,
        bots_per_roboport=30,
        ore_patches=ore_patches,
    )
    plan = dict(composition["plans"])["line"]
    print(f"Plan: {sum(len(p['actions']) for p in plan['phases'])} actions; materials: {materials}")
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
            infrastructure_report = load_json(bridge.build_layout(authorization, infrastructure_plan))
            if not infrastructure_report.get("ok"):
                print(f"INFRASTRUCTURE {name} FAILED: {infrastructure_report.get('error')}", file=sys.stderr)
                return 1
        bridge.ensure_scaffolding(composition["scaffolding"])
        report_path = bridge.build_layout(authorization, plan)
        report = load_json(report_path)
        if not report.get("ok"):
            print(f"BUILD FAILED: {report.get('error')}", file=sys.stderr)
            return 1
        print(
            f"Layout executed: {report['placed_ghosts']} ghosts, {report['placed_entities']} scaffolding entities, "
            f"{report['recipe_failures']} recipe failures."
        )
    finally:
        bridge.close()

    # Watch the output chest fill up: proof that items flow end to end.
    chest_x = args.origin_x + args.machines * 3 + 1.5
    chest_y = args.origin_y + 6.5
    output_item = args.recipe
    query = (
        f"/sc local s=game.surfaces['planner-sandbox'] "
        f"local c=s.find_entities_filtered{{name='steel-chest',force='planner',position={{{chest_x},{chest_y}}},limit=1}}[1] "
        f"if c then rcon.print(c.get_item_count('{output_item}')) else rcon.print('-1') end"
    )

    client = RconClient(args.rcon_host, args.rcon_port, args.rcon_password, timeout=30)
    try:
        start = time.monotonic()
        first_count = None
        last_count = 0
        while time.monotonic() - start < args.verify_seconds:
            response = client.command(query).strip()
            count = int(response) if response.lstrip("-").isdigit() else -1
            if count >= 0:
                if first_count is None and count > 0:
                    first_count = count
                    print(f"[{time.monotonic()-start:5.1f}s] first output arrived: {count} {output_item}")
                last_count = count
            time.sleep(5)
        print(f"After {args.verify_seconds:.0f}s: {last_count} {output_item} collected.")
        if last_count > 0:
            rate = last_count / args.verify_seconds
            print(f"LINE IS LIVE: ~{rate:.2f} {output_item}/s reaching the output chest.")
            return 0
        print("No output produced - line is not flowing.", file=sys.stderr)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
