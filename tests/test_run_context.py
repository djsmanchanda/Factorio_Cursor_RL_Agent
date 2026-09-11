# Path: tests/test_run_context.py
# Purpose: Keep diagnostic packets bounded and prevent evidence crossing run boundaries.

import json
from pathlib import Path

from tools.run_context import MAX_CONTEXT_CHARS, build_context


START = "2026-09-11T05:10:03+05:30"


def test_latest_run_isolated_and_unfinished(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text("RUN START: ts=2026-09-10T00:00:00+00:00 target=old\n"
                   "+3s BLOCKER: old-failure\n+3s RUN END\n"
                   f"RUN START: ts={START} target=new\n+1s PRIORITY: splitter\n"
                   "+2s LOG COMPACTION: suppressed 8; top=4x PRIORITY: wrong-task\n")
    packet = build_context(log)
    assert "old-failure" not in packet
    assert "target=old" not in packet
    assert "partial (RUN END absent)" in packet
    assert f"{log}:4: RUN START: ts={START} target=new" in packet
    assert "PRIORITY: splitter" in packet
    priority = packet.split("Latest selected priority:\n")[1].split("\n\n")[0]
    assert "splitter" in priority and "wrong-task" not in priority


def test_terminal_and_verdict_survive_repeated_long_lines(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    long = "x" * 20_000
    log.write_text(f"RUN START: ts={START}\n"
                   "+1s NO PROGRESS VERDICT: miss=electronic-circuit\n"
                   "+2s STUCK: same work\n+2s BLOCKER: terminal-collision\n"
                   "Traceback (most recent call last):\nValueError: overlapping pipe\n"
                   + (f"+3s PRIORITY DEFERRED: {long}\n" * 30)
                   + "+4s RUN END\n")
    packet = build_context(log)
    assert len(packet) <= MAX_CONTEXT_CHARS
    assert "terminal-collision" in packet
    assert "ValueError: overlapping pipe" in packet
    assert "miss=electronic-circuit" in packet
    assert "completed (RUN END observed; not mission success)" in packet
    assert "PRIORITY DEFERRED=30" in packet
    assert "excerpt truncated" in packet


def test_inventory_matches_timestamp_and_uses_last_distinct_ticks(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text(f"RUN START: ts={START}\n")
    history = tmp_path / "inventory.json"
    history.write_text(json.dumps({"schema_version": "1.0.0", "runs": [
        {"started_at": "2026-09-10T00:00:00+00:00", "samples": [
            {"tick": 10, "items": {"iron-plate": 99999}}]},
        {"started_at": "2026-09-10T23:40:03+00:00", "samples": [
            {"tick": 100, "items": {"iron-plate": 10}},
            {"tick": 200, "items": {"iron-plate": 12}},
            {"tick": 200, "items": {"iron-plate": 13, "copper-plate": 7}}]},
    ]}))
    packet = build_context(log, history)
    assert "ticks 100→200" in packet
    assert "iron-plate: 10→13 (+3)" in packet
    assert "NET STOCK CHANGE; not production rate" in packet
    assert "1 missing/invalid item readings UNKNOWN (not zero)" in packet
    assert "99999" not in packet


def test_inventory_mismatch_and_missing_samples_are_unknown(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text(f"RUN START: ts={START}\n")
    history = tmp_path / "inventory.json"
    payload = {"schema_version": "1.0.0", "runs": [{"started_at": "2026-09-10T00:00:00+00:00", "samples": []}]}
    history.write_text(json.dumps(payload))
    assert "started_at mismatch" in build_context(log, history)
    payload["runs"][0]["started_at"] = START
    history.write_text(json.dumps(payload))
    assert "UNKNOWN: no inventory samples" in build_context(log, history)
    payload["runs"][0]["samples"] = [{"tick": 5, "items": {}}]
    history.write_text(json.dumps(payload))
    assert "UNKNOWN: need two increasing distinct inventory ticks" in build_context(log, history)


def test_legacy_run_and_missing_boundary(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text("unscoped BLOCKER: old\n")
    assert "refusing to combine unscoped evidence" in build_context(log)
    log.write_text(f"{START} RUN START: target=legacy\n{START} RUN END\n")
    assert "completed (RUN END observed" in build_context(log)


def test_missing_log_is_unknown(tmp_path: Path) -> None:
    assert "UNKNOWN: cannot read runner log" in build_context(tmp_path / "absent.log")


def test_completed_run_excludes_post_run_inventory(tmp_path: Path) -> None:
    log, history = tmp_path / "run.log", tmp_path / "inventory.json"
    log.write_text(f"RUN START: ts={START}\n+20s RUN END\n")
    history.write_text(json.dumps({"schema_version": "1.0.0", "runs": [
        {"started_at": START, "samples": [
            {"tick": 100, "elapsed_seconds": 5, "items": {"iron-plate": 10}},
            {"tick": 200, "elapsed_seconds": 15, "items": {"iron-plate": 12}},
            {"tick": 300, "elapsed_seconds": 25, "items": {"iron-plate": 999}},
        ]}]}))
    packet = build_context(log, history)
    assert "iron-plate: 10→12 (+2)" in packet
    assert "Excluded 1 post-run/unknown-time samples" in packet
    assert "999" not in packet


def test_quoted_terminal_is_not_completion(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(f"RUN START: ts={START}\n+1s OPENCODE HELPER: awaiting RUN END\n")
    assert "partial (RUN END absent)" in build_context(log)
