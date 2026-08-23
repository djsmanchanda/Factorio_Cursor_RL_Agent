# Path: tools/autonomous_run.py
# Purpose: Drive one real-base production or research target without hiding unsupported production-rate policy.

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import traceback
from copy import copy
from datetime import datetime
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.autonomous_builder import StuckError, run
from orchestrator.game_bridge import GameBridge, load_json
from orchestrator.research_queue import ResearchQueueError, load_queue, update_item
from tools.runner_log_retention import archive_runner_sessions
from tools.runner_process import runner_pid_record


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        # Match the manager's GNU coreutils tree contract:
        # sha256sum lines are themselves fed to a final sha256sum.
        digest.update(f"{file_digest}  ./{path.relative_to(root)}\n".encode("utf-8"))
    return digest.hexdigest()


def _validate_episode_manifest(path: Path | None) -> None:
    """Refuse to attach a controller to an unverified or changed baseline."""
    if path is None:
        return
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StuckError(f"episode manifest is unreadable: {path}: {error}") from error
    required = {
        "episode_id", "source_save", "source_save_sha256",
        "isolated_save", "isolated_save_sha256",
        "baseline_world_fingerprint", "repository_revision",
        "deployed_factorio_mod_sha256",
        "deployed_factorio_training_lab_sha256",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise StuckError(f"episode manifest is incomplete: {', '.join(missing)}")
    source = Path(manifest["source_save"])
    isolated = Path(manifest["isolated_save"])
    if not source.is_file() or not isolated.is_file():
        raise StuckError("episode source or isolated save is missing")
    source_hash = _sha256(source)
    isolated_hash = _sha256(isolated)
    if source_hash != manifest["source_save_sha256"]:
        raise StuckError("source-save SHA-256 changed after episode creation")
    if isolated_hash != manifest["isolated_save_sha256"]:
        raise StuckError("isolated save does not match the episode baseline")
    expected = manifest["baseline_world_fingerprint"]
    if expected != f"sha256:{isolated_hash}":
        raise StuckError("episode baseline fingerprint does not match the restored save")
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise StuckError(f"repository revision could not be verified: {error}") from error
    if revision != manifest["repository_revision"]:
        raise StuckError("runner repository revision differs from the episode manifest")
    deployed_hashes = {
        "deployed_factorio_mod_sha256": (
            Path(manifest["isolated_save"]).parent.parent
            / "mods" / "factorio_cursor_rl_agent"
        ),
        "deployed_factorio_training_lab_sha256": (
            Path(manifest["isolated_save"]).parent.parent
            / "mods" / "factorio_training_lab"
        ),
    }
    for field, root in deployed_hashes.items():
        if _directory_hash(root) != manifest[field]:
            raise StuckError(
                f"deployed mod hash differs from the episode manifest: {field}"
            )


def _patch_episode_manifest(path: Path | None, **fields: object) -> None:
    if path is None or not path.is_file():
        return
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    manifest.update(fields)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    temporary.replace(path)


def _add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--surface", default="nauvis")
    parser.add_argument("--force", default="player")
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27017)
    credentials = parser.add_mutually_exclusive_group(required=True)
    credentials.add_argument("--rcon-password")
    credentials.add_argument(
        "--rcon-secret-file", type=Path,
        help="Read the local RCON password from this protected file.",
    )
    parser.add_argument("--script-output", required=True, type=Path)
    parser.add_argument("--reference-point", type=float, nargs=2, metavar=("X", "Y"), default=(0.0, 0.0))
    parser.add_argument("--max-iterations", type=int, default=20)
    parser.add_argument(
        "--log-file", type=Path,
        help="Append mission output here (default: <server-data>/logs/autonomous-run.log)",
    )
    parser.add_argument(
        "--episode-manifest", type=Path,
        help="Verified episode identity required for managed deterministic campaigns.",
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
        tick = status.get("tick")
        _patch_episode_manifest(
            getattr(args, "episode_manifest", None),
            baseline_verified=True,
            initial_game_tick=int(tick) if isinstance(tick, int) else None,
        )
        emit(
            "EPISODE BASELINE VERIFIED: initial game tick="
            f"{tick if tick is not None else 'unknown'}"
        )
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


def _research_queue(args: argparse.Namespace, emit: Callable[[str], None]) -> int:
    """Process the persisted queue in order, stopping at the first failure."""
    try:
        queue = load_queue(args.queue_file)
    except ResearchQueueError as error:
        raise StuckError(str(error)) from error
    if queue["surface"] != args.surface or queue["force"] != args.force:
        raise StuckError(
            "research queue target does not match the runner scope: "
            f"{queue['surface']}/{queue['force']} != {args.surface}/{args.force}"
        )
    for item in queue["items"]:
        if item["status"] == "completed":
            continue
        technology = item["technology"]
        update_item(args.queue_file, technology, "running")
        current = copy(args)
        current.technology = technology
        try:
            _research(current, emit)
        except Exception as error:
            update_item(args.queue_file, technology, "failed", error=str(error))
            raise
        update_item(args.queue_file, technology, "completed")
        emit(f"RESEARCH QUEUE ITEM COMPLETE: {technology}")
    emit("RESEARCH QUEUE COMPLETE")
    return 0


def _load_rcon_secret(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ValueError(f"RCON secret file is unavailable: {path}") from error
    if not value:
        raise ValueError(f"RCON secret file is empty: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a real-base production or research target.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    produce = subcommands.add_parser("produce", help="Ensure one item has a working production line.")
    produce.add_argument("item")
    _add_connection_arguments(produce)
    research = subcommands.add_parser("research", help="Produce a technology's science packs, then queue it.")
    research.add_argument("technology")
    _add_connection_arguments(research)
    research_queue = subcommands.add_parser(
        "research-queue", help="Process an ordered persisted research queue.",
    )
    research_queue.add_argument("--queue-file", required=True, type=Path)
    _add_connection_arguments(research_queue)
    increase = subcommands.add_parser("increase", help="Rejected until a real-base rate policy exists.")
    increase.add_argument("item")
    increase.add_argument("rate", type=float)

    args = parser.parse_args(argv)
    if args.command == "increase":
        parser.error(
            "production-increase goals are unsupported: declare a real-base rate measurement and capacity policy first"
        )
    if args.rcon_secret_file is not None:
        try:
            args.rcon_password = _load_rcon_secret(args.rcon_secret_file)
        except ValueError as error:
            parser.error(str(error))
    _validate_episode_manifest(getattr(args, "episode_manifest", None))
    log_path = args.log_file or args.script_output.parent / "logs" / "autonomous-run.log"
    archived = archive_runner_sessions(log_path, keep=2)
    pid_path = log_path.with_name("autonomous-run.pid")
    termination_reason = "completed"
    with runner_pid_record(pid_path):
        logger = _RunLogger(log_path)
        heartbeat_stop = threading.Event()

        def _emit_heartbeat() -> None:
            while not heartbeat_stop.wait(10.0):
                logger.emit(f"RUN HEARTBEAT pid={os.getpid()} ppid={os.getppid()}")

        heartbeat_thread = threading.Thread(
            target=_emit_heartbeat, name="runner-heartbeat", daemon=True,
        )
        heartbeat_thread.start()
        logger.emit(
            f"RUN START: command={args.command} "
            f"target={getattr(args, 'item', getattr(args, 'technology', 'research-queue'))} "
            f"surface={args.surface} force={args.force} log={log_path}"
        )
        _patch_episode_manifest(
            getattr(args, "episode_manifest", None),
            started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        if archived is not None:
            logger.emit(
                f"LOG RETENTION: archived {archived.session_count} older run(s) "
                f"to {archived.path}"
            )
        try:
            if args.command == "produce":
                _run_item(args, args.item, logger.emit)
                return 0
            if args.command == "research-queue":
                return _research_queue(args, logger.emit)
            return _research(args, logger.emit)
        except StuckError as error:
            logger.emit(f"STUCK: {error}")
            termination_reason = str(error)
            return 2
        except Exception as error:
            logger.emit(f"ERROR: {type(error).__name__}: {error}")
            logger.exception()
            termination_reason = f"{type(error).__name__}: {error}"
            return 1
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1.0)
            _patch_episode_manifest(
                getattr(args, "episode_manifest", None),
                ended_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                termination_reason=termination_reason,
            )
            logger.emit("RUN END")
            logger.close()


if __name__ == "__main__":
    def _raise_termination_signal(signum: int, _frame: object) -> None:
        raise RuntimeError(f"runner terminated by signal {signum}")

    for _signal_number in (
        getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None),
        getattr(signal, "SIGHUP", None),
    ):
        if _signal_number is not None:
            signal.signal(_signal_number, _raise_termination_signal)
    libc = ctypes.CDLL(None)
    # If a lifecycle supervisor kills this Python process, turn the parent
    # death into SIGTERM so the existing signal trap can record RUN END.
    libc.prctl(1, signal.SIGTERM)
    raise SystemExit(main())
