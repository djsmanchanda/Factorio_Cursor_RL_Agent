# Path: tools/rocket_launch_evidence.py
# Purpose: Collect closed-session launch reports without writing or granting authority.

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import stat

from core.rocket_launch_evidence import LaunchSessionEvidence, evaluate_launch_session


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate event JSON field")
        result[key] = value
    return result


def _invalid_number(_value):
    raise ValueError("non-finite event JSON number")


def collect_launch_session(
    event_directory: Path, *, start: Mapping, end: Mapping, target_silo: Mapping,
    max_events: int = 10000, max_file_bytes: int = 1_048_576,
) -> LaunchSessionEvidence:
    """Read one asserted closed session; never authenticates host or writer.

    Only regular final files are accepted using O_NOFOLLOW. Parent-directory
    symlinks, same-UID writers, enumeration races, and runtime/save identity
    remain unverified; a closed scope is a caller-supplied assertion.
    """
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("safe final-file reading requires O_NOFOLLOW")
    if type(max_events) is not int or not 0 <= max_events <= 10000:
        raise ValueError("max_events must be in 0..10000")
    if type(max_file_bytes) is not int or not 1 <= max_file_bytes <= 1_048_576:
        raise ValueError("max_file_bytes must be in 1..1048576")
    if not isinstance(start, Mapping) or not isinstance(end, Mapping):
        raise ValueError("start and end reports are required")
    session = start.get("session_id")
    if not isinstance(session, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session) is None:
        raise ValueError("session identity is not filename-safe")
    count = end.get("recorded_launches")
    if type(count) is not int or not 0 <= count <= max_events:
        raise ValueError("closing event count exceeds the collection bound")
    pattern = re.compile(rf"rocket_launch_{re.escape(session)}_\d+_\d+\.json\Z")
    directory = Path(event_directory)
    try:
        with os.scandir(directory) as entries:
            names = sorted(entry.name for entry in entries if pattern.fullmatch(entry.name))
    except OSError as error:
        raise ValueError("launch event directory is unavailable") from error
    if len(names) != count:
        raise ValueError("event file count does not match the closed session")
    events = []
    for name in names:
        try:
            descriptor = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise ValueError("event is not a regular file")
                raw = handle.read(max_file_bytes + 1)
            if len(raw) > max_file_bytes:
                raise ValueError("event exceeds the file size bound")
            event = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_number)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("event file is not safely readable JSON") from error
        events.append((name, event))
    return evaluate_launch_session(start, end, events, target_silo=target_silo)
