# Path: tools/autonomous_run.py
# Purpose: Drive one real-base production or research target without hiding unsupported production-rate policy.

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
from collections import Counter
from copy import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.autonomous_builder import StuckError, run
from orchestrator.game_bridge import GameBridge, load_json
from orchestrator.mission_state import BOOTSTRAP_PROFILES, MissionStateLedger
from orchestrator.research_queue import ResearchQueueError, load_queue, update_item
from helper_agent.cli import launch_processor as launch_helper_agent_processor
from helper_agent.packet_builder import build_case_packet, write_packet
from tools.opencode_helper_agent import launch_helper as launch_opencode_helper
from tools.runner_log_retention import archive_runner_sessions
from tools.runner_process import runner_pid_record


_TRACEBACK_FRAME_LIMIT = 24
_REPETITION_SUMMARY_LIMIT = 8
_TRANSIENT_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.-])-?\d+(?:\.\d+)?(?:%|s|kW|MJ|ticks?)?"
)
_POSITION = re.compile(r"\(-?\d+(?:\.\d+)?,\s*-?\d+(?:\.\d+)?\)")


class _RunLogger:
    """Flush every mission event to both the terminal and a durable file."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._file = path.open("a", encoding="utf-8", buffering=1)
        self.events_path = path.with_name("deterministic-events.jsonl")
        self._events = self.events_path.open("a", encoding="utf-8", buffering=1)
        self._started = time.monotonic()
        self._sequence = 0
        self._message_counts: Counter[str] = Counter()
        self._signature_counts: Counter[str] = Counter()
        self._signature_latest: dict[str, str] = {}
        self._suppressed_messages = 0
        self._lock = threading.Lock()

    @staticmethod
    def _event_type(message: str) -> str:
        prefix = message.lstrip().split(":", 1)[0].lower().replace(" ", "_")
        known = {
            "run_start", "run_end", "run_heartbeat", "priority", "blocker",
            "stuck", "error", "goal_met", "survey_start", "survey_end",
            "material_project", "mall_demand", "chemical_ladder",
            "rationed_mall", "core_mall_ready",
        }
        return prefix if prefix in known else "controller_event"

    @staticmethod
    def _message_signature(message: str) -> str:
        """Collapse changing counters while preserving item and position identity."""
        protected: list[str] = []

        def hold(match: re.Match[str]) -> str:
            protected.append(match.group(0))
            return f"@POSITION{len(protected) - 1}@"

        signature = _POSITION.sub(hold, message)

        def normalize_number(match: re.Match[str]) -> str:
            numeric = re.match(r"-?\d+(?:\.\d+)?", match.group(0))
            return "0" if numeric and float(numeric.group(0)) == 0 else "#"

        signature = _TRANSIENT_NUMBER.sub(normalize_number, signature)
        for index, value in enumerate(protected):
            signature = signature.replace(f"@POSITION{index}@", value)
        return signature

    def emit(self, message: str) -> None:
        observed = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._lock:
            self._sequence += 1
            elapsed = int(time.monotonic() - self._started)
            self._message_counts[message] += 1
            occurrence = self._message_counts[message]
            signature = self._message_signature(message)
            self._signature_counts[signature] += 1
            signature_occurrence = self._signature_counts[signature]
            self._signature_latest[signature] = message
            # Retries with only counters changing carry the same decision.
            # Keep exponentially spaced samples with their newest values.
            write_message = (
                signature_occurrence & (signature_occurrence - 1) == 0
            )
            if not write_message:
                self._suppressed_messages += 1
                return
            displayed_message = (
                message if signature_occurrence == 1
                else f"LOG REPEAT x{signature_occurrence}: {message}"
                if occurrence == signature_occurrence
                else f"LOG UPDATE x{signature_occurrence}: {message}"
            )
            if self._sequence == 1 and message.startswith("RUN START:"):
                line = f"RUN START: ts={observed}{message.removeprefix('RUN START:')}"
            else:
                line = f"+{elapsed}s {displayed_message}"
            event = {
                "v": 2,
                "seq": self._sequence,
                "dt": elapsed,
                "type": self._event_type(message),
                "message": message,
                "occurrence": occurrence,
                "signature": signature,
                "signature_occurrence": signature_occurrence,
            }
            if self._sequence == 1:
                event["ts"] = observed
            print(line, flush=True)
            print(line, file=self._file, flush=True)
            print(
                json.dumps(event, sort_keys=True, separators=(",", ":")),
                file=self._events, flush=True,
            )

    def flush_compaction(self) -> None:
        """Record exact totals for the top repeated templates before RUN END."""
        with self._lock:
            if self._suppressed_messages <= 0:
                return
            repeated = [
                (count, signature, self._signature_latest[signature])
                for signature, count in self._signature_counts.items()
                if count > 1
            ]
            repeated.sort(key=lambda item: (-item[0], item[1]))
            top = repeated[:_REPETITION_SUMMARY_LIMIT]
            compact_top = " | ".join(
                f"{count}x {message[:120]}" for count, _signature, message in top
            )
            message = (
                f"LOG COMPACTION: suppressed {self._suppressed_messages} "
                f"semantically repeated event(s) across {len(repeated)} "
                f"template(s); top={compact_top}"
            )
            self._sequence += 1
            elapsed = int(time.monotonic() - self._started)
            event = {
                "v": 2,
                "seq": self._sequence,
                "dt": elapsed,
                "type": "log_compaction",
                "message": message,
                "repetition_counts": [
                    {
                        "count": count, "signature": signature,
                        "message": original,
                    }
                    for count, signature, original in top
                ],
            }
            line = f"+{elapsed}s {message}"
            print(line, flush=True)
            print(line, file=self._file, flush=True)
            print(
                json.dumps(event, sort_keys=True, separators=(",", ":")),
                file=self._events, flush=True,
            )
            self._suppressed_messages = 0

    def exception(self) -> None:
        error = sys.exception()
        if error is None:
            rendered = "No active exception\n"
        else:
            frame_count = sum(1 for _frame, _line in traceback.walk_tb(error.__traceback__))
            rendered = "".join(traceback.format_exception(
                type(error), error, error.__traceback__,
                limit=-_TRACEBACK_FRAME_LIMIT, chain=True,
            ))
            omitted = max(0, frame_count - _TRACEBACK_FRAME_LIMIT)
            if omitted:
                rendered = rendered.replace(
                    "Traceback (most recent call last):",
                    "Traceback (most recent call last; "
                    f"{omitted} earlier frame(s) omitted):",
                    1,
                )
        print(rendered, end="", file=sys.stderr, flush=True)
        print(rendered, end="", file=self._file, flush=True)
        self._file.flush()

    def close(self) -> None:
        self._file.close()
        self._events.close()


def _write_runner_heartbeat(path: Path) -> None:
    """Overwrite one last-seen record without growing the durable run log."""
    payload = {
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _queue_helper_agent_review(
    *, log_path: Path, mission_state_path: Path,
    blocker_events_path: Path | None, episode_manifest_path: Path | None,
    emit: Callable[[str], None], data_root: Path | None = None,
    structured_events_path: Path | None = None,
) -> Path:
    """Queue one bounded packet and start its asynchronous review worker."""
    packet = build_case_packet(
        log_path=log_path,
        mission_state_path=mission_state_path,
        blocker_events_path=blocker_events_path,
        episode_manifest_path=episode_manifest_path,
        structured_events_path=structured_events_path,
    )
    helper_root = data_root or Path.home() / ".local/share/factorio-rl/helper_agent"
    packet_path = write_packet(packet, helper_root / "inbox")
    emit(f"HELPER AGENT: queued post-run review packet {packet_path}")
    try:
        processor_identity = launch_helper_agent_processor(helper_root)
    except (OSError, subprocess.SubprocessError) as error:
        emit(
            "HELPER AGENT: packet queued but processor launch failed: "
            f"{type(error).__name__}: {error}"
        )
    else:
        emit(f"HELPER AGENT: started post-run processor {processor_identity}")
    return packet_path


def _start_opencode_helper(
    args: argparse.Namespace, *, log_path: Path, emit: Callable[[str], None],
) -> str | None:
    """Start the permanent read-only observer for this runner invocation."""
    if getattr(args, "no_opencode_helper", False):
        return None
    run_id = getattr(args, "episode_id", None) or (
        "direct-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    try:
        identity = launch_opencode_helper(
            run_id=run_id,
            log_path=log_path,
            manifest_path=getattr(args, "episode_manifest", None),
            data_root=getattr(args, "opencode_helper_data_root", None),
            report_root=getattr(args, "opencode_helper_report_root", None),
        )
    except (OSError, subprocess.SubprocessError) as error:
        emit(
            "OPENCODE HELPER: could not start observer: "
            f"{type(error).__name__}: {error}"
        )
        return None
    emit(f"OPENCODE HELPER: started read-only observer {identity} run={run_id}")
    return identity


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


def _validate_episode_manifest(path: Path | None) -> str | None:
    """Refuse an unverified baseline and return its stable episode identity."""
    if path is None:
        return None
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
    episode_id = manifest["episode_id"]
    if not isinstance(episode_id, str) or not episode_id:
        raise StuckError("episode manifest has an invalid episode_id")
    return episode_id


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


def _episode_metadata(path: Path | None) -> dict[str, object]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _bootstrap_profile(args: argparse.Namespace) -> str:
    explicit = getattr(args, "bootstrap_profile", None)
    manifest = _episode_metadata(getattr(args, "episode_manifest", None))
    profile = explicit or manifest.get("bootstrap_profile") or "reduced-v1"
    if profile not in BOOTSTRAP_PROFILES:
        raise StuckError(
            f"Unknown bootstrap profile {profile!r}",
            code="invalid_bootstrap_profile",
        )
    return str(profile)


def _save_provenance(path: Path | None) -> dict[str, object]:
    manifest = _episode_metadata(path)
    return {
        key: manifest[key]
        for key in (
            "source_save", "source_save_sha256", "isolated_save",
            "isolated_save_sha256", "baseline_world_fingerprint",
        )
        if key in manifest
    }


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
    parser.add_argument(
        "--bootstrap-profile", choices=BOOTSTRAP_PROFILES,
        help="Supply contract for this run (default: manifest value, then reduced-v1).",
    )
    parser.add_argument(
        "--mission-state-file", type=Path,
        help="Atomic mission ledger (default: beside the autonomous run log).",
    )
    parser.add_argument(
        "--blocker-events-file", type=Path,
        help="Append-only typed blocker JSONL (default: beside the autonomous run log).",
    )
    helper = parser.add_mutually_exclusive_group()
    helper.add_argument(
        "--helper-agent-data-root", type=Path,
        help="Write Helper Agent packets to this isolated data root.",
    )
    helper.add_argument(
        "--no-helper-agent-review", action="store_true",
        help="Deprecated legacy Helper Agent switch; post-run packet reviews are no longer launched.",
    )
    parser.add_argument(
        "--opencode-helper-data-root", type=Path,
        help="Write permanent OpenCode Helper state outside the repository.",
    )
    parser.add_argument(
        "--opencode-helper-report-root", type=Path,
        help="Write one OpenCode Helper findings directory per deterministic run.",
    )
    parser.add_argument(
        "--no-opencode-helper", action="store_true",
        help="Do not start the read-only OpenCode Helper observer (intended for tests).",
    )


def _run_item(
    args: argparse.Namespace, item: str, emit: Callable[[str], None],
) -> dict:
    ledger = getattr(args, "mission_ledger", None)
    if ledger is not None:
        ledger.controller_started(item)
    try:
        result = run(
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
            episode_id=getattr(args, "episode_id", None),
            bootstrap_profile=getattr(args, "bootstrap_profile", "reduced-v1"),
        )
    except Exception:
        if ledger is not None:
            ledger.controller_finished(item, "failed")
        raise
    if ledger is not None:
        ledger.controller_finished(item, "completed")
    return result


def _research(args: argparse.Namespace, emit: Callable[[str], None]) -> int:
    bridge = GameBridge(
        script_output=args.script_output,
        host=args.rcon_host,
        port=args.rcon_port,
        password=args.rcon_password,
        command_timeout=30.0,
    )
    try:
        ledger = getattr(args, "mission_ledger", None)
        if ledger is not None:
            ledger.transition("research_preflight", current_target=args.technology)
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

        if ledger is not None:
            ledger.transition("queueing_research", current_target=args.technology)
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
        if ledger is not None:
            ledger.transition("research_queued", current_target=args.technology)
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
    produce.add_argument("--acceptance-seconds", type=int, default=0,
                         help="Require sustained output for this many game seconds (multiple of 10).")
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
    if args.command == "produce" and (args.acceptance_seconds < 0 or args.acceptance_seconds % 10 or args.acceptance_seconds > 600):
        parser.error("--acceptance-seconds must be 0 or a multiple of 10 up to 600")
    if args.rcon_secret_file is not None:
        try:
            args.rcon_password = _load_rcon_secret(args.rcon_secret_file)
        except ValueError as error:
            parser.error(str(error))
    args.episode_id = _validate_episode_manifest(
        getattr(args, "episode_manifest", None)
    )
    args.bootstrap_profile = _bootstrap_profile(args)
    log_path = args.log_file or args.script_output.parent / "logs" / "autonomous-run.log"
    archived = archive_runner_sessions(log_path, keep=2)
    pid_path = log_path.with_name("autonomous-run.pid")
    heartbeat_path = log_path.with_name("autonomous-run.heartbeat.json")
    termination_reason = "completed"
    mission_status = "completed"
    mission_state_path: Path | None = None
    blocker_events_path: Path | None = None
    with runner_pid_record(pid_path):
        logger = _RunLogger(log_path)
        mission_ledger: MissionStateLedger | None = None
        heartbeat_stop = threading.Event()

        def _emit_heartbeat() -> None:
            while not heartbeat_stop.wait(10.0):
                _write_runner_heartbeat(heartbeat_path)

        heartbeat_thread = threading.Thread(
            target=_emit_heartbeat, name="runner-heartbeat", daemon=True,
        )
        logger.emit(
            f"RUN START: command={args.command} "
            f"target={getattr(args, 'item', getattr(args, 'technology', 'research-queue'))} "
            f"surface={args.surface} force={args.force} "
            f"bootstrap_profile={args.bootstrap_profile} log={log_path}"
        )
        _write_runner_heartbeat(heartbeat_path)
        heartbeat_thread.start()
        _patch_episode_manifest(
            getattr(args, "episode_manifest", None),
            started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            bootstrap_profile=args.bootstrap_profile,
        )
        _start_opencode_helper(args, log_path=log_path, emit=logger.emit)
        if archived is not None:
            logger.emit(
                f"LOG RETENTION: archived {archived.session_count} older run(s) "
                f"to {archived.path}"
            )
        try:
            target = getattr(
                args, "item", getattr(args, "technology", "research-queue"),
            )
            manifest = _episode_metadata(getattr(args, "episode_manifest", None))
            mission_state_path = (
                args.mission_state_file
                or log_path.with_name("deterministic-mission-state.json")
            )
            blocker_events_path = (
                args.blocker_events_file
                or log_path.with_name("deterministic-blockers.jsonl")
            )
            mission_ledger = MissionStateLedger(
                mission_state_path,
                blocker_events_path,
                episode_id=args.episode_id,
                bootstrap_profile=args.bootstrap_profile,
                command=args.command,
                target=target,
                surface=args.surface,
                force=args.force,
                repository_revision=(
                    str(manifest["repository_revision"])
                    if manifest.get("repository_revision") else None
                ),
                save_provenance=_save_provenance(
                    getattr(args, "episode_manifest", None)
                ),
            )
            args.mission_ledger = mission_ledger
            logger.emit(
                f"MISSION STATE: profile={args.bootstrap_profile} "
                f"ledger={mission_state_path} blockers={blocker_events_path}"
            )
            if args.command == "produce":
                result = _run_item(args, args.item, logger.emit)
                if args.acceptance_seconds:
                    from orchestrator.production_acceptance import monitor, read_sample
                    from tools.rcon_client import RconClient
                    client = RconClient(args.rcon_host, args.rcon_port, args.rcon_password)
                    try:
                        acceptance = monitor(
                            read=lambda: read_sample(client, surface=args.surface, force=args.force,
                                                     target=args.item, output_position=result["output_position"]),
                            path=log_path.with_name("production-acceptance.json"),
                            episode_id=args.episode_id, provenance=manifest,
                            target=args.item, surface=args.surface, force=args.force,
                            seconds=args.acceptance_seconds,
                        )
                    finally:
                        client.close()
                    logger.emit(
                        f"PRODUCTION ACCEPTANCE: target={args.item} result={acceptance['result']} "
                        f"samples={len(acceptance['samples'])} "
                        f"ticks={acceptance['start_tick']}..{acceptance['end_tick']} "
                        f"report={log_path.with_name('production-acceptance.json')}"
                    )
                    if not acceptance["ok"]:
                        raise StuckError("sustained production acceptance failed",
                                         code=acceptance["result"], details={
                                             "target": args.item,
                                             "report": str(log_path.with_name("production-acceptance.json")),
                                         })
                return 0
            if args.command == "research-queue":
                return _research_queue(args, logger.emit)
            return _research(args, logger.emit)
        except StuckError as error:
            logger.emit(f"STUCK: {error}")
            termination_reason = str(error)
            mission_status = "stuck"
            if mission_ledger is not None:
                try:
                    blocker = mission_ledger.record_blocker(error)
                    logger.emit(
                        "BLOCKER: "
                        + json.dumps(blocker, sort_keys=True, separators=(",", ":"))
                    )
                except Exception as telemetry_error:
                    logger.emit(
                        f"TELEMETRY ERROR: could not record blocker: {telemetry_error}"
                    )
            return 2
        except Exception as error:
            logger.emit(f"ERROR: {type(error).__name__}: {error}")
            logger.exception()
            termination_reason = f"{type(error).__name__}: {error}"
            mission_status = "error"
            if mission_ledger is not None:
                try:
                    blocker = mission_ledger.record_blocker(
                        error,
                        code="unhandled_exception",
                        classification="bug",
                        state="failed",
                        details={"exception_type": type(error).__name__},
                    )
                    logger.emit(
                        "BLOCKER: "
                        + json.dumps(blocker, sort_keys=True, separators=(",", ":"))
                    )
                except Exception as telemetry_error:
                    logger.emit(
                        f"TELEMETRY ERROR: could not record blocker: {telemetry_error}"
                    )
            return 1
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1.0)
            if mission_ledger is not None:
                try:
                    mission_ledger.finish(mission_status)
                except Exception as telemetry_error:
                    logger.emit(
                        f"TELEMETRY ERROR: could not finish mission ledger: {telemetry_error}"
                    )
            _patch_episode_manifest(
                getattr(args, "episode_manifest", None),
                ended_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                termination_reason=termination_reason,
            )
            logger.flush_compaction()
            logger.emit("RUN END")
            logger.close()
            heartbeat_path.unlink(missing_ok=True)


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
