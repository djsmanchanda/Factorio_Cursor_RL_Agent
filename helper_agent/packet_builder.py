# Path: helper_agent/packet_builder.py
# Purpose: Build a bounded, schema-valid case packet from the newest runner block.

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Mapping

import jsonschema

from tools.run_log_format import parse_timed_run_log_line

_SIDEcar_ROOT = Path(__file__).resolve().parents[1] / "Helper_Agent"
CASE_PACKET_SCHEMA = json.loads(
    (_SIDEcar_ROOT / "schemas" / "case_packet.schema.json").read_text("utf-8"),
)


_FIELD = re.compile(
    r"\b(?P<name>command|target|surface|force|bootstrap_profile)=(?P<value>\S+)"
)
_MATCHERS = tuple(
    re.compile(pattern) for pattern in (
        r"\bSTUCK:", r"\bERROR:", r"\bBLOCKER:", r"\bMISSION STATE:",
        r"\bRESEARCH READINESS:", r"\bCONTROLLER", r"\bPRIORITY:",
        r"\bSUPPLY", r"\bPOWER", r"\bCOVERAGE", r"\bGOAL MET:",
        r"\bSURVEY (?:START|END):", r"\bSMELTER", r"\bBLUEPRINT",
        r"\bMALL DEMAND:", r"\bMATERIAL PROJECT", r"\bCHEMICAL LADDER:",
        r"\bRATIONED MALL:", r"\bMALL BOOTSTRAP LOAN",
        r"\bRESEARCH QUEUED:", r"\bRUN HEARTBEAT",
        r"^Traceback \(most recent call last\):", r'^\s+File "',
        r"^[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception):",
    )
)
_MAX_LOG_LINES = 160
_MAX_BLOCKERS = 40


def _read_json(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_jsonl(path: Path | None) -> list[dict]:
    if path is None or not path.is_file():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _latest_complete_run(log_path: Path) -> tuple[list[tuple[datetime, str, str]], bool]:
    """Return the newest complete run block, including raw traceback lines."""
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    timestamped: list[tuple[int, datetime, str, str]] = []
    parsed_by_index: dict[int, tuple[datetime, str]] = {}
    run_started_at: datetime | None = None
    for index, line in enumerate(lines):
        parsed = parse_timed_run_log_line(line, run_started_at=run_started_at)
        if parsed is None:
            continue
        if "RUN START:" in parsed.message:
            run_started_at = parsed.timestamp
        parsed_by_index[index] = (parsed.timestamp, parsed.message)
        timestamped.append((index, parsed.timestamp, parsed.message, line))
    end_index = next(
        (index for index in range(len(timestamped) - 1, -1, -1)
         if timestamped[index][2] == "RUN END"),
        None,
    )
    if end_index is None:
        return [], False
    start_index = next(
        (index for index in range(end_index, -1, -1)
         if "RUN START:" in timestamped[index][2]),
        None,
    )
    if start_index is None:
        return [], False
    raw_start = timestamped[start_index][0]
    raw_end = timestamped[end_index][0]
    current_timestamp = timestamped[start_index][1]
    run: list[tuple[datetime, str, str]] = []
    for index in range(raw_start, raw_end + 1):
        line = lines[index]
        if index in parsed_by_index:
            current_timestamp, message = parsed_by_index[index]
            run.append((current_timestamp, message, line))
            continue
        # Python tracebacks are intentionally unprefixed by _RunLogger. Keep
        # them inside the bounded run excerpt instead of silently discarding
        # the only source location for an unhandled exception.
        run.append((current_timestamp, line, line))
    return run, True


def _bounded(lines: list[str]) -> dict[str, list[str]]:
    head = lines[:_MAX_LOG_LINES // 2]
    tail = lines[-(_MAX_LOG_LINES // 2):] if len(lines) > len(head) else []
    matched = [
        line for line in lines
        if any(pattern.search(line) for pattern in _MATCHERS)
    ]
    return {
        "head_lines": head,
        "tail_lines": tail,
        "matched_pattern_lines": matched[-_MAX_LOG_LINES:],
    }


def _normalized_blocker(raw: Mapping[str, object]) -> dict:
    details = raw.get("details")
    return {
        "blocker_id": str(raw.get("blocker_id", "blocker")),
        "code": str(raw.get("code", "untyped_stuck")),
        "classification": (
            "intended_difficulty"
            if raw.get("classification") == "intended_difficulty" else "bug"
        ),
        "state": str(raw.get("state", "failed")),
        "stage": str(raw.get("stage", "unknown")),
        "target": str(raw.get("target", "unknown")),
        "first_seen_tick": None,
        "last_seen_tick": None,
        "observed_at": str(raw.get("observed_at", "")),
        "message": str(raw.get("message", "")),
        "details": details if isinstance(details, dict) else {},
    }


def _current_run_blockers(
    records: object, *, mission_id: object, attempt: object,
) -> list[Mapping[str, object]]:
    """Keep blocker evidence scoped to the mission attempt being reviewed."""
    if not isinstance(records, list):
        return []
    blockers = [record for record in records if isinstance(record, Mapping)]
    if mission_id is not None:
        blockers = [
            record for record in blockers
            if record.get("mission_id") == mission_id
        ]
    if isinstance(attempt, int):
        blockers = [
            record for record in blockers
            if record.get("attempt") == attempt
        ]
    return blockers


def _repository_revision() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1], text=True,
            stderr=subprocess.DEVNULL,
        ).strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _decision_summary(events: list[dict]) -> dict:
    """Compact semantic signals for deterministic fallback review."""
    messages = [str(event.get("message", "")) for event in events]
    priorities = [
        message.split("PRIORITY:", 1)[1].strip().split()[0]
        for message in messages if "PRIORITY:" in message
    ]
    survey_seconds: list[tuple[float, str]] = []
    for message in messages:
        if "SURVEY END:" not in message or "elapsed=" not in message:
            continue
        try:
            elapsed = float(message.rsplit("elapsed=", 1)[1].rstrip("s"))
        except ValueError:
            continue
        survey_seconds.append((elapsed, message))
    repeated = Counter(
        message for message in messages
        if not message.startswith("RUN HEARTBEAT")
    )
    return {
        "event_count": len(events),
        "priority_counts": dict(sorted(Counter(priorities).items())),
        "priority_tail": priorities[-12:],
        "alternating_priority_cycle": (
            len(priorities[-8:]) == 8
            and len(set(priorities[-8:])) == 2
            and all(
                priorities[-8:][index] == priorities[-8:][index % 2]
                for index in range(8)
            )
        ),
        "slowest_surveys": [
            {"elapsed_seconds": elapsed, "message": message}
            for elapsed, message in sorted(survey_seconds, reverse=True)[:5]
        ],
        "mall_loan_tail": [
            message for message in messages
            if "MALL BOOTSTRAP LOAN" in message
        ][-8:],
        "zero_placement_reports": sum(
            "placed 0 actions" in message for message in messages
        ),
        "repeated_messages": [
            {"count": count, "message": message}
            for message, count in repeated.most_common(8) if count > 1
        ],
    }


def build_case_packet(
    *,
    log_path: Path,
    mission_state_path: Path | None = None,
    blocker_events_path: Path | None = None,
    episode_manifest_path: Path | None = None,
    structured_events_path: Path | None = None,
    created_at: str | None = None,
) -> dict:
    """Build the newest complete run's bounded post-run evidence packet."""
    run, ended = _latest_complete_run(log_path)
    if not run:
        raise ValueError(f"No complete RUN START/RUN END block in {log_path}")

    started, header_message, _ = run[0]
    ended_at, _terminal_message, _ = run[-1]
    fields = {match.group("name"): match.group("value") for match in _FIELD.finditer(header_message)}
    manifest = _read_json(episode_manifest_path)
    mission = _read_json(mission_state_path)
    mission_id = mission.get("mission_id")
    attempt = mission.get("attempt")
    blocker_records = _read_jsonl(blocker_events_path)
    structured_events = _read_jsonl(structured_events_path)
    blocker_source = blocker_records or mission.get("blockers") or []
    raw_blockers = _current_run_blockers(
        blocker_source, mission_id=mission_id, attempt=attempt,
    )

    episode_id = manifest.get("episode_id")
    run_id = episode_id or mission_id or started.isoformat()
    if isinstance(attempt, int):
        run_id = f"{run_id}-attempt-{attempt:03d}"
    terminal_map = {
        "completed": "completed",
        "stuck": "stuck",
        "error": "error",
    }
    terminal_class = terminal_map.get(
        str(mission.get("status")), "aborted" if mission else "completed",
    )
    packet = {
        "schema_version": 1,
        "run_id": str(run_id),
        "created_at": created_at or ended_at.isoformat(timespec="seconds"),
        "commit": mission.get("repository_revision")
        or manifest.get("repository_revision") or _repository_revision(),
        "save_hash": manifest.get("isolated_save_sha256")
        or manifest.get("source_save_sha256"),
        "mod_tree_hash": manifest.get("deployed_factorio_mod_sha256"),
        "bootstrap_profile": fields.get("bootstrap_profile", "unknown"),
        "surface": fields.get("surface", "nauvis"),
        "force": fields.get("force", "player"),
        "target": fields.get("target", "unknown"),
        "mission_stage": str(mission.get("stage", "unknown")),
        "start_tick": (
            int(manifest["initial_game_tick"])
            if isinstance(manifest.get("initial_game_tick"), int) else None
        ),
        "end_tick": None,
        "duration_seconds": max(0, round((ended_at - started).total_seconds())),
        "terminal_class": terminal_class,
        "blockers": [_normalized_blocker(raw) for raw in raw_blockers[-_MAX_BLOCKERS:]],
        "telemetry": {
            "mission": {
                key: mission.get(key) for key in (
                    "mission_id", "attempt", "status", "stage",
                    "current_target", "started_at", "ended_at", "controllers",
                )
            },
            "episode": {
                key: manifest.get(key) for key in (
                    "episode_id", "bootstrap_profile", "started_at", "ended_at",
                    "termination_reason", "baseline_verified",
                ) if manifest.get(key) is not None
            },
            "decision_summary": _decision_summary(structured_events),
            "structured_events": str(structured_events_path.resolve())
            if structured_events_path is not None else None,
        },
        "log_excerpt": _bounded([line for _, _, line in run]),
        "source_log": str(log_path.resolve()),
        "mission_state": str(mission_state_path.resolve() if mission_state_path else log_path),
        "blocker_events": str(blocker_events_path.resolve() if blocker_events_path else log_path),
    }
    jsonschema.validate(packet, CASE_PACKET_SCHEMA)
    return packet


def write_packet(packet: dict, inbox: Path) -> Path:
    inbox.mkdir(parents=True, exist_ok=True)
    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", packet["run_id"])
    path = inbox / f"{safe_run_id}.json"
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    temporary.replace(path)
    return path
