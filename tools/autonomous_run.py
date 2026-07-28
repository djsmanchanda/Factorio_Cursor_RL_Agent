# Path: tools/autonomous_run.py
# Purpose: Drive one real-base production or research target without hiding unsupported production-rate policy.

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.autonomous_builder import StuckError, run
from orchestrator.game_bridge import GameBridge, load_json


class _RunLogger:
    """Flush every mission event to both the terminal and a durable file."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._file = path.open("a", encoding="utf-8", buffering=1)

    def emit(self, message: str) -> None:
        line = f"{datetime.now().astimezone().isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        print(line, file=self._file, flush=True)

    def exception(self) -> None:
        traceback.print_exc()
        traceback.print_exc(file=self._file)
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def _add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--surface", default="nauvis")
    parser.add_argument("--force", default="player")
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27017)
    parser.add_argument("--rcon-password", required=True)
    parser.add_argument("--script-output", required=True, type=Path)
    parser.add_argument("--reference-point", type=float, nargs=2, metavar=("X", "Y"), default=(0.0, 0.0))
    parser.add_argument("--max-iterations", type=int, default=20)
    parser.add_argument(
        "--log-file", type=Path,
        help="Append mission output here (default: <server-data>/logs/autonomous-run.log)",
    )


def _run_item(
    args: argparse.Namespace, item: str, emit: Callable[[str], None],
) -> dict:
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
        emit=emit,
        mission_items=tuple(getattr(args, "mission_items", (item,))),
    )


def _research(args: argparse.Namespace, emit: Callable[[str], None]) -> int:
    bridge = GameBridge(
        script_output=args.script_output,
        host=args.rcon_host,
        port=args.rcon_port,
        password=args.rcon_password,
        command_timeout=30.0,
    )
    try:
        emit(f"RESEARCH START: {args.technology} on {args.surface}/{args.force}")
        status = load_json(bridge.research_status(force=args.force, technology=args.technology))
        if not status.get("ok"):
            raise StuckError(f"Research preflight failed: {status.get('error', status)}")
        technology = status.get("technology")
        if not technology:
            raise StuckError(f"Research preflight returned no state for {args.technology!r}")
        target_completed = technology.get(
            "target_completed", technology.get("completed", technology.get("researched", False))
        )
        if target_completed:
            emit(f"GOAL MET: {args.technology} is already researched on force {args.force}")
            return 0
        if technology.get("state") == "future":
            raise StuckError(
                f"{args.technology} is a future repeatable level; "
                f"current level is {technology.get('current_level')}"
            )
        if not technology["enabled"]:
            raise StuckError(
                f"{args.technology} is not enabled on force {args.force}; prerequisite policy is not implemented"
            )
        science_packs = tuple(sorted(technology["science_packs"]))
        args.mission_items = science_packs
        emit(
            "RESEARCH READINESS: stocking construction machines for "
            + ", ".join(science_packs)
        )
        for science_pack in science_packs:
            _run_item(args, science_pack, emit)

        queued = load_json(bridge.set_research(args.technology, force=args.force))
        if not queued.get("ok"):
            raise StuckError(f"Could not queue {args.technology}: {queued.get('error', queued)}")
        final_status = load_json(bridge.research_status(force=args.force, technology=args.technology))
        if not final_status.get("ok"):
            raise StuckError(f"Could not report research state: {final_status.get('error', final_status)}")
        emit(
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
    log_path = args.log_file or args.script_output.parent / "logs" / "autonomous-run.log"
    logger = _RunLogger(log_path)
    logger.emit(
        f"RUN START: command={args.command} "
        f"target={getattr(args, 'item', getattr(args, 'technology', 'unknown'))} "
        f"surface={args.surface} force={args.force} log={log_path}"
    )
    try:
        if args.command == "produce":
            _run_item(args, args.item, logger.emit)
            return 0
        return _research(args, logger.emit)
    except StuckError as error:
        logger.emit(f"STUCK: {error}")
        return 2
    except Exception as error:
        logger.emit(f"ERROR: {type(error).__name__}: {error}")
        logger.exception()
        return 1
    finally:
        logger.emit("RUN END")
        logger.close()


if __name__ == "__main__":
    raise SystemExit(main())
