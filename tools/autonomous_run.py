# Path: tools/autonomous_run.py
# Purpose: Drive one real-base production or research target without hiding unsupported production-rate policy.

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.autonomous_builder import StuckError, run
from orchestrator.game_bridge import GameBridge, load_json


def _add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--surface", default="nauvis")
    parser.add_argument("--force", default="player")
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27017)
    parser.add_argument("--rcon-password", required=True)
    parser.add_argument("--script-output", required=True, type=Path)
    parser.add_argument("--reference-point", type=float, nargs=2, metavar=("X", "Y"), default=(0.0, 0.0))
    parser.add_argument("--max-iterations", type=int, default=20)


def _run_item(args: argparse.Namespace, item: str) -> dict:
    return run(
        item,
        surface=args.surface,
        force=args.force,
        rcon_host=args.rcon_host,
        rcon_port=args.rcon_port,
        rcon_password=args.rcon_password,
        script_output=args.script_output,
        reference_point=tuple(args.reference_point),
        max_iterations=args.max_iterations,
    )


def _research(args: argparse.Namespace) -> int:
    bridge = GameBridge(
        script_output=args.script_output,
        host=args.rcon_host,
        port=args.rcon_port,
        password=args.rcon_password,
    )
    try:
        status = load_json(bridge.research_status(force=args.force, technology=args.technology))
        if not status.get("ok"):
            raise StuckError(f"Research preflight failed: {status.get('error', status)}")
        technology = status.get("technology")
        if not technology:
            raise StuckError(f"Research preflight returned no state for {args.technology!r}")
        if technology["researched"]:
            print(f"GOAL MET: {args.technology} is already researched on force {args.force}")
            return 0
        if not technology["enabled"]:
            raise StuckError(
                f"{args.technology} is not enabled on force {args.force}; prerequisite policy is not implemented"
            )
        for science_pack in sorted(technology["science_packs"]):
            _run_item(args, science_pack)

        queued = load_json(bridge.set_research(args.technology, force=args.force))
        if not queued.get("ok"):
            raise StuckError(f"Could not queue {args.technology}: {queued.get('error', queued)}")
        final_status = load_json(bridge.research_status(force=args.force, technology=args.technology))
        if not final_status.get("ok"):
            raise StuckError(f"Could not report research state: {final_status.get('error', final_status)}")
        print(
            f"RESEARCH QUEUED: {args.technology} on {args.force}; "
            f"current={final_status.get('current_research')} "
            f"progress={final_status.get('research_progress')}"
        )
        return 0
    finally:
        bridge.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a real-base production or research target.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    produce = subcommands.add_parser("produce", help="Ensure one item has a working production line.")
    produce.add_argument("item")
    _add_connection_arguments(produce)
    research = subcommands.add_parser("research", help="Produce a technology's science packs, then queue it.")
    research.add_argument("technology")
    _add_connection_arguments(research)
    increase = subcommands.add_parser("increase", help="Rejected until a real-base rate policy exists.")
    increase.add_argument("item")
    increase.add_argument("rate", type=float)

    args = parser.parse_args(argv)
    if args.command == "increase":
        parser.error(
            "production-increase goals are unsupported: declare a real-base rate measurement and capacity policy first"
        )
    try:
        if args.command == "produce":
            _run_item(args, args.item)
            return 0
        return _research(args)
    except StuckError as error:
        print(f"STUCK: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())