# Path: core/local_time.py
# Purpose: One time convention for human-facing run times: the machine's local time zone (IST here).

from __future__ import annotations

from datetime import datetime


def now_local() -> datetime:
    return datetime.now().astimezone()


def local_stamp() -> str:
    """Current local time as ISO 8601 with its UTC offset, to the second."""
    return now_local().isoformat(timespec="seconds")


def zone_label() -> str:
    """Local zone name and offset, e.g. ``IST, UTC+05:30``."""
    now = now_local()
    offset = now.strftime("%z")
    return f"{now.tzname()}, UTC{offset[:3]}:{offset[3:]}"


def to_local(value: str | datetime | None) -> str | None:
    """Normalize an ISO time (``Z``, any offset, or naive = already local) to local time."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.astimezone()  # Naive values are local wall-clock times.
    return value.astimezone().isoformat(timespec="seconds")


def run_length(seconds: float | int | None) -> str | None:
    """Readable duration, e.g. ``1h 35m 26s``."""
    if seconds is None:
        return None
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s" if hours else f"{minutes}m {secs:02d}s"
