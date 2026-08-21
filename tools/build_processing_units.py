# Path: tools/build_processing_units.py
# Purpose: Plan or execute the surveyed raw-resource processing-unit factory.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.game_bridge import BridgeError, GameBridge
from planners.electronics_block import build_electronics_block
from planners.electronics_world import load_electronics_world_spec
from tools.electronics_execution import ElectronicsExecutionError, execute_electronics_bundle
from tools.electronics_radial_execution import execute_electronics_bundle_radial
from tools.rcon_client import RconClient, RconError


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan or execute the surveyed raw-resource processing-unit factory."
    )
    parser.add_argument("--world-spec", type=Path, required=True)
    parser.add_argument("--script-output")
    parser.add_argument("--rcon-password")
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27015)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--settle-ticks", type=int, default=5400)
    parser.add_argument("--settle-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--construction-mode", choices=["atomic", "radial"], default="atomic")
    parser.add_argument(
        "--ring-width", type=float,
        help="Radial shell width in tiles (default: measured spidertron construction radius)",
    )
    parser.add_argument("--spidertron-roboports", type=int, default=4)
    parser.add_argument("--spidertron-batteries", type=int, default=2)
    parser.add_argument("--spidertron-bots", type=int, default=50)
    parser.add_argument(
        "--existing-topology", choices=["refuse", "reconcile", "reset"], default="refuse"
    )
    args = parser.parse_args()
    if not args.plan_only and (not args.script_output or not args.rcon_password):
        parser.error("live execution needs --script-output and --rcon-password")

    try:
        world = load_electronics_world_spec(args.world_spec)
        bundle = build_electronics_block(include_processing=True, world=world)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))
    print(
        f"Managed plans: {len(bundle['infrastructure'])}; "
        f"production plans: {len(bundle['plans'])}; "
        f"mode: {'plan-only' if args.plan_only else 'live'}"
    )
    if args.plan_only:
        return 0

    try:
        bridge = GameBridge(
            script_output=Path(args.script_output),
            host=args.rcon_host,
            port=args.rcon_port,
            password=args.rcon_password,
        )
    except BridgeError as error:
        print(f"BRIDGE FAILED: {error}", file=sys.stderr)
        return 1
    rcon_client = None
    try:
        if args.construction_mode == "radial":
            rcon_client = RconClient(args.rcon_host, args.rcon_port, args.rcon_password)
            result = execute_electronics_bundle_radial(
                bridge,
                rcon_client,
                bundle,
                world=world,
                existing_topology=args.existing_topology,
                settle_ticks=args.settle_ticks,
                settle_timeout_seconds=args.settle_timeout_seconds,
                ring_width=args.ring_width,
                roboports=args.spidertron_roboports,
                batteries=args.spidertron_batteries,
                bots=args.spidertron_bots,
            )
        else:
            result = execute_electronics_bundle(
                bridge,
                bundle,
                world=world,
                existing_topology=args.existing_topology,
                settle_ticks=args.settle_ticks,
                settle_timeout_seconds=args.settle_timeout_seconds,
            )
        print(json.dumps(result["live"], sort_keys=True))
        return 0
    except (BridgeError, ElectronicsExecutionError, RconError, OSError, ValueError) as error:
        print(f"EXECUTION FAILED: {error}", file=sys.stderr)
        if isinstance(error, ElectronicsExecutionError) and error.report is not None:
            print(json.dumps(error.report, sort_keys=True), file=sys.stderr)
        return 1
    finally:
        if rcon_client is not None:
            rcon_client.close()
        bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
