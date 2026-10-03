# Path: core/ist_time.py
# Purpose: One timezone for human-facing run times: IST (Asia/Kolkata, UTC+05:30), independent of the process's TZ.

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST)


def ist_stamp() -> str:
    """Current time as ISO 8601 with the +05:30 offset, to the second."""
    return now_ist().isoformat(timespec="seconds")


def to_ist(value: str | datetime | None) -> str | None:
    """Normalize an ISO time (``Z``, any offset, or naive = already IST) to IST."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    return value.astimezone(IST).isoformat(timespec="seconds")


def run_length(seconds: float | int | None) -> str | None:
    """Readable duration, e.g. ``1h 35m 26s``."""
    if seconds is None:
        return None
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s" if hours else f"{minutes}m {secs:02d}s"
