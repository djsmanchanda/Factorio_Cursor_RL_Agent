# Path: tools/survey_electronics_world.py
# Purpose: Compile an offline or live snapshot into a validated electronics WorldSpec.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.game_bridge import BridgeError, GameBridge, load_json
from planners.resource_survey import allocate_electronics_world


def load_survey_snapshot(args: argparse.Namespace) -> dict:
    """Read the exact offline artifact or request the live planner surface snapshot."""
    if args.input:
        return json.loads(args.input.read_text(encoding="utf-8"))
    bridge = GameBridge(
        script_output=Path(args.script_output),
        host=args.rcon_host,
        port=args.rcon_port,
        password=args.rcon_password,
    )
    try:
        return load_json(
            bridge.request_snapshot(timeout=args.snapshot_timeout_seconds, surface=args.surface)
        )
    finally:
        bridge.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile a file or live planner snapshot into electronics site allocation"
    )
    parser.add_argument(
        "input", type=Path, nargs="?", help="Offline snapshot JSON or world_observation JSON"
    )
    parser.add_argument("--output", type=Path, help="Write ElectronicsWorldSpec JSON")
    parser.add_argument("--surface", choices=["planner-sandbox"], default="planner-sandbox")
    parser.add_argument("--script-output")
    parser.add_argument("--rcon-password")
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27015)
    parser.add_argument("--snapshot-timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--block-bounds", nargs=4, type=float, required=True,
        metavar=("X1", "Y1", "X2", "Y2"),
        help="Explicit local macro-block bounds; resources outside require CityPlanner rail",
    )
    parser.add_argument(
        "--single-macro-block", action="store_true", required=True,
        help="Acknowledge that these bounds are one self-contained local block",
    )
    parser.add_argument("--tick", type=int, default=0)
    args = parser.parse_args()

    live_requested = bool(args.script_output or args.rcon_password)
    if args.input and live_requested:
        parser.error("choose either an offline input file or live bridge arguments")
    if not args.input and (not args.script_output or not args.rcon_password):
        parser.error("provide an input file or both --script-output and --rcon-password")

    try:
        payload = load_survey_snapshot(args)
    except (BridgeError, OSError, json.JSONDecodeError) as error:
        parser.error(str(error))
    if "world_observation" in payload:
        snapshot = payload
    else:
        snapshot = {
            "tick": args.tick,
            "surface": args.surface,
            "entities": [],
            "world_observation": payload,
        }
    x1, y1, x2, y2 = args.block_bounds
    try:
        result = allocate_electronics_world(
            snapshot, block_bounds={"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        )
    except ValueError as error:
        parser.error(str(error))
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())