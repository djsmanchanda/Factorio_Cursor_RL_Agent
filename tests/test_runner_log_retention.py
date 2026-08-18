# Path: tests/test_runner_log_retention.py
# Purpose: Verify bounded live runner logs and durable archival of older output.

import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from tools.runner_log_retention import archive_runner_sessions, archive_stale_runner_files
from tools.runner_process import runner_pid_record, running_runner_pid


@pytest.fixture
def dashboard_tmp_path() -> Iterator[Path]:
    path = Path("runs") / f"dashboard-test-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path)


def _session(number: int) -> bytes:
    return (
        f"2026-08-0{number}T10:00:00+05:30 RUN START: command=produce target=item-{number}\n"
        f"2026-08-0{number}T10:00:01+05:30 event {number}\n"
        f"2026-08-0{number}T10:00:02+05:30 RUN END\n"
    ).encode()


def test_archives_older_sessions_and_keeps_latest_three(dashboard_tmp_path: Path) -> None:
    log_path = dashboard_tmp_path / "autonomous-run.log"
    sessions = [_session(number) for number in range(1, 6)]
    log_path.write_bytes(b"".join(sessions))

    result = archive_runner_sessions(log_path, keep=3)

    assert result is not None
    assert result.session_count == 2
    assert result.path.read_bytes() == b"".join(sessions[:2])
    assert log_path.read_bytes() == b"".join(sessions[2:])


def test_runner_start_can_reserve_one_of_three_slots(dashboard_tmp_path: Path) -> None:
    log_path = dashboard_tmp_path / "autonomous-run.log"
    sessions = [_session(number) for number in range(1, 5)]
    log_path.write_bytes(b"".join(sessions))

    result = archive_runner_sessions(log_path, keep=2)

    assert result is not None
    assert result.session_count == 2
    assert log_path.read_bytes() == b"".join(sessions[2:])


def test_log_without_session_markers_is_left_untouched(dashboard_tmp_path: Path) -> None:
    log_path = dashboard_tmp_path / "autonomous-run.log"
    log_path.write_text("diagnostic preamble\n", encoding="utf-8")

    assert archive_runner_sessions(log_path, keep=3) is None
    assert log_path.read_text(encoding="utf-8") == "diagnostic preamble\n"


def test_moves_legacy_runner_files_to_archive(dashboard_tmp_path: Path) -> None:
    current = dashboard_tmp_path / "autonomous-run.log"
    legacy = dashboard_tmp_path / "autonomous-run-20260728.log"
    errors = dashboard_tmp_path / "autonomous-run-20260728.err.log"
    current.write_text("current", encoding="utf-8")
    legacy.write_text("legacy", encoding="utf-8")
    errors.write_text("errors", encoding="utf-8")

    moved = archive_stale_runner_files(current)

    assert {path.name for path in moved} == {legacy.name, errors.name}
    assert current.exists()
    assert not legacy.exists()
    assert not errors.exists()


def test_runner_pid_record_tracks_current_process(dashboard_tmp_path: Path) -> None:
    path = dashboard_tmp_path / "autonomous-run.pid"

    with runner_pid_record(path):
        assert running_runner_pid(path) == os.getpid()

    assert running_runner_pid(path) is None
