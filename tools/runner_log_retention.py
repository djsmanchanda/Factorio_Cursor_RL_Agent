# Path: tools/runner_log_retention.py
# Purpose: Keep recent runner sessions live while moving older output into a durable archive.

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_RUN_START_MARKER = b" RUN START:"


@dataclass(frozen=True)
class ArchiveResult:
    path: Path
    session_count: int
    byte_count: int


def archive_runner_sessions(log_path: Path, *, keep: int = 3) -> ArchiveResult | None:
    """Retain the newest complete/live sessions and archive the older prefix."""
    if keep < 0:
        raise ValueError("keep must be non-negative")
    try:
        data = log_path.read_bytes()
    except FileNotFoundError:
        return None

    starts = _session_starts(data)
    if len(starts) <= keep:
        return None
    archive_end = starts[-keep] if keep else len(data)
    archived_data = data[:archive_end]
    retained_data = data[archive_end:]
    archive_path = _new_archive_path(log_path)
    archive_path.write_bytes(archived_data)
    _replace_bytes(log_path, retained_data)
    return ArchiveResult(archive_path, len(starts) - keep, len(archived_data))


def archive_stale_runner_files(log_path: Path) -> list[Path]:
    """Move legacy per-run log files out of the live dashboard log directory."""
    if not log_path.parent.exists():
        return []
    archive_dir = log_path.parent / "archive"
    moved: list[Path] = []
    for candidate in sorted(log_path.parent.glob("autonomous-run*.log")):
        if candidate == log_path or not candidate.is_file():
            continue
        archive_dir.mkdir(parents=True, exist_ok=True)
        destination = _available_destination(archive_dir / candidate.name)
        shutil.move(str(candidate), destination)
        moved.append(destination)
    return moved


def _session_starts(data: bytes) -> list[int]:
    starts: list[int] = []
    offset = 0
    for line in data.splitlines(keepends=True):
        if _RUN_START_MARKER in line:
            starts.append(offset)
        offset += len(line)
    return starts


def _new_archive_path(log_path: Path) -> Path:
    archive_dir = log_path.parent / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    return archive_dir / f"{log_path.stem}-{stamp}{log_path.suffix}"


def _available_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    return path.with_name(f"{path.stem}-{stamp}{path.suffix}")


def _replace_bytes(path: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
