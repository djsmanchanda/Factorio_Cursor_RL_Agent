# Path: tests/test_run_history.py
# Purpose: Prove SQLite history deduplicates runs and bounds cross-run retrieval.

import sqlite3
from pathlib import Path

from tools.run_history import index_log, search


def _run(hour: int, task: str = "splitter", *, ended: bool = True) -> str:
    return (f"RUN START: ts=2026-09-11T{hour:02d}:00:00+00:00 episode=episode-{hour}\n"
            '+1s OPENCODE HELPER: prompt says RUN START: target=fake RUN END\n'
            f'+2s BLOCKER: {{"code":"no_progress","details":{{"selected_task":"{task}"}}}}\n'
            + ("+2s RUN END\n" if ended else ""))


def test_index_is_idempotent_and_archive_updates_without_duplicate(tmp_path: Path) -> None:
    log, archive, db = tmp_path / "live.log", tmp_path / "archive.log", tmp_path / "history.sqlite"
    log.write_text(_run(1, ended=False))
    assert index_log(db, log) == 1
    assert index_log(db, log) == 0
    assert "status=partial" in search(db, "splitter")
    log.write_text(_run(1))
    assert index_log(db, log) == 1
    archive.write_text(log.read_text())
    assert index_log(db, archive) == 1
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert connection.execute("SELECT status,source FROM runs").fetchone() == ("completed", str(archive))
    stale = tmp_path / "stale.log"
    stale.write_text(_run(1, ended=False))
    index_log(db, stale)
    assert "status=completed" in search(db, "splitter")


def test_mixed_runs_preserve_separate_terminals_and_original_line_refs(tmp_path: Path) -> None:
    log, db = tmp_path / "runs.log", tmp_path / "history.sqlite"
    log.write_text(_run(1, "splitter") + _run(2, "electric-furnace"))
    assert index_log(db, log) == 2
    result = search(db, "electric-furnace")
    assert "episode=episode-2" in result
    assert f"source={log}:5" in result
    assert "task=electric-furnace" in result
    assert "episode=episode-1" not in result
    assert "episode=episode-1" in search(db, "splitter")


def test_search_bound_and_queries_are_data(tmp_path: Path) -> None:
    log, db = tmp_path / "runs.log", tmp_path / "history.sqlite"
    log.write_text("".join(_run(hour, "splitter" + "x" * 15_000) for hour in range(20)))
    index_log(db, log)
    assert len(search(db, "no_progress", limit=10000)) <= 12_000
    search(db, "' OR 1=1; DROP TABLE runs; --")
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 20
    assert "No searchable terms" in search(db, "!!!")


def test_missing_history_is_unknown_and_read_does_not_create_it(tmp_path: Path) -> None:
    db = tmp_path / "missing.sqlite"
    assert "UNKNOWN" in search(db, "splitter")
    assert not db.exists()


def test_history_finds_early_evidence_omitted_from_packet(tmp_path):
    log, db = tmp_path / "run.log", tmp_path / "history.sqlite"
    log.write_text("RUN START: ts=2026-09-11T00:00:00Z\n+1s routing unusual-alloy\n" +
                   "\n".join(f"+{i}s PRIORITY: iron" for i in range(2, 100)) + "\n+100s RUN END\n")
    index_log(db, log)
    result = search(db, "unusual-alloy")
    assert "run.log:2:" in result and "routing unusual-alloy" in result
