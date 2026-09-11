# Path: tools/run_log_format.py
# Purpose: Parse compact and legacy deterministic runner timestamp prefixes.

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta


_COMPACT_START = re.compile(
    r"^RUN START: ts=(?P<timestamp>\S+)(?P<fields>(?: .*)?)$"
)
_RELATIVE = re.compile(r"^\+(?P<seconds>\d+)s (?P<message>.*)$")
_LEGACY = re.compile(r"^(?P<timestamp>\S+) (?P<message>.*)$")


@dataclass(frozen=True)
class TimedRunLogLine:
    timestamp: datetime
    message: str


def parse_timed_run_log_line(
    line: str, *, run_started_at: datetime | None
) -> TimedRunLogLine | None:
    """Parse compact output while retaining compatibility with archived logs."""
    if match := _COMPACT_START.match(line):
        try:
            timestamp = datetime.fromisoformat(match.group("timestamp"))
        except ValueError:
            return None
        return TimedRunLogLine(timestamp, "RUN START:" + match.group("fields"))

    if match := _RELATIVE.match(line):
        if run_started_at is None:
            return None
        timestamp = run_started_at + timedelta(seconds=int(match.group("seconds")))
        return TimedRunLogLine(timestamp, match.group("message"))

    if match := _LEGACY.match(line):
        try:
            timestamp = datetime.fromisoformat(match.group("timestamp"))
        except ValueError:
            return None
        return TimedRunLogLine(timestamp, match.group("message"))
    return None


def is_run_start_line(line: str | bytes) -> bool:
    """Recognize compact live and legacy archived run boundaries."""
    if isinstance(line, bytes):
        return line.startswith(b"RUN START: ts=") or b" RUN START:" in line
    return line.startswith("RUN START: ts=") or " RUN START:" in line


def is_run_end_line(line: str | bytes) -> bool:
    """Recognize actual terminal records, never quoted helper/prompt text."""
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    stripped = line.rstrip()
    if stripped == "RUN END" or re.fullmatch(r"\+\d+s RUN END", stripped):
        return True
    parsed = parse_timed_run_log_line(stripped, run_started_at=None)
    return parsed is not None and parsed.message == "RUN END"


def is_helper_agent_line(line: str | bytes) -> bool:
    """Recognize legacy and OpenCode Helper handoffs in either time format."""
    if isinstance(line, bytes):
        return (
            line.startswith(b"HELPER AGENT:") or b" HELPER AGENT:" in line
            or line.startswith(b"OPENCODE HELPER:") or b" OPENCODE HELPER:" in line
        )
    return (
        line.startswith("HELPER AGENT:") or " HELPER AGENT:" in line
        or line.startswith("OPENCODE HELPER:") or " OPENCODE HELPER:" in line
    )
