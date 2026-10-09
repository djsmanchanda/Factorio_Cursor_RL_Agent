# Path: tests/test_game_speed_reporting.py | Purpose: Verify runner logs report requested and observed game speed and split elapsed time into wall and game seconds, using fake bridges only.

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import tools.autonomous_run as autonomous_run
import tools.run_ledger as run_ledger
from orchestrator.game_bridge import BridgeError
from training.laya_run_log import RunLog
from training.laya_saved_world import SavedWorldAdapter, speed_status


class FakeBridge:
    """Stands in for GameBridge: one speed, a tick sequence, and optional read/set failures."""

    last: "FakeBridge | None" = None

    def __init__(self, *_args, ticks=(0, 3600), speed=1.0, read_error=None, set_error=None, **_kwargs):
        self.speed = speed
        self.ticks = list(ticks)
        self.read_error = read_error
        self.set_error = set_error
        self.set_calls: list[float] = []
        self.closed = False
        FakeBridge.last = self

    def game_speed(self) -> float:
        if self.read_error is not None:
            raise self.read_error
        return self.speed

    def set_game_speed(self, speed: float) -> float:
        if self.set_error is not None:
            raise self.set_error
        self.set_calls.append(float(speed))
        self.speed = float(speed)
        return self.speed

    def game_tick(self) -> int:
        return self.ticks.pop(0) if len(self.ticks) > 1 else self.ticks[0]

    def close(self) -> None:
        self.closed = True


def _run_main(tmp_path: Path, monkeypatch, *extra: str, speed_kwargs=None) -> tuple[int, str]:
    """Run the deterministic produce command with run() faked; return (exit code, RUN log text)."""
    monkeypatch.setattr(autonomous_run, "GameBridge", lambda *a, **k: FakeBridge(**(speed_kwargs or {})))
    monkeypatch.setattr(run_ledger, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(autonomous_run, "run", lambda *_a, **_k: None)
    log = tmp_path / "run.log"
    result = autonomous_run.main([
        "produce", "automation-science-pack", *extra,
        "--rcon-password", "test",
        "--script-output", str(tmp_path / "script-output"),
        "--log-file", str(log),
        "--mission-state-file", str(tmp_path / "mission.json"),
        "--blocker-events-file", str(tmp_path / "blockers.jsonl"),
        "--bootstrap-profile", "reduced-v1",
        "--no-helper-agent-review",
    ])
    return result, log.read_text(encoding="utf-8")


def _run_start(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("RUN START:"))


def test_run_start_reports_default_speed_and_leaves_game_untouched(tmp_path, monkeypatch):
    result, text = _run_main(tmp_path, monkeypatch)

    assert result == 0
    assert "requested_speed=default observed_speed=1 " in _run_start(text)
    assert FakeBridge.last.set_calls == []
    assert FakeBridge.last.closed


def test_speed_flag_sets_game_speed_once_and_reports_it(tmp_path, monkeypatch):
    result, text = _run_main(tmp_path, monkeypatch, "--speed", "2")

    assert result == 0
    assert FakeBridge.last.set_calls == [2.0]
    assert "requested_speed=2 observed_speed=2 " in _run_start(text)


def test_observed_speed_is_unknown_when_read_fails_without_failing_run(tmp_path, monkeypatch):
    result, text = _run_main(
        tmp_path, monkeypatch,
        speed_kwargs={"read_error": BridgeError("rcon unavailable")},
    )

    assert result == 0
    assert "requested_speed=default observed_speed=unknown " in _run_start(text)
    assert "RUN END" in text


def test_failed_explicit_speed_set_stops_the_run(tmp_path, monkeypatch):
    result, text = _run_main(
        tmp_path, monkeypatch, "--speed", "2",
        speed_kwargs={"set_error": BridgeError("set refused")},
    )

    assert result != 0
    assert "RUN END" in text


def test_elapsed_report_splits_wall_seconds_from_game_seconds(tmp_path, monkeypatch):
    result, text = _run_main(tmp_path, monkeypatch, "--speed", "2", speed_kwargs={"ticks": (0, 3600)})

    assert result == 0
    elapsed = next(line for line in text.splitlines() if "ELAPSED:" in line)
    assert re.search(r"ELAPSED: wall_seconds=\d+ game_seconds=60\.0$", elapsed)


def test_ledger_entry_carries_requested_and_observed_speed(tmp_path, monkeypatch):
    _run_main(tmp_path, monkeypatch, "--speed", "2")

    rows = [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert rows[-1]["requested_speed"] == "2" and rows[-1]["observed_speed"] == "2"


def test_run_log_elapsed_uses_tick_delta_as_game_seconds(tmp_path):
    log = RunLog(tmp_path / "run.log", tmp_path / "heartbeat.json")

    assert log.elapsed(0, 3600).endswith("game_seconds=60.0")
    assert log.elapsed(None, 3600).endswith("game_seconds=unknown")


def test_saved_world_observed_speed_falls_back_to_unknown():
    adapter = SavedWorldAdapter.__new__(SavedWorldAdapter)
    adapter.bridge = FakeBridge(read_error=BridgeError("rcon unavailable"))

    assert adapter.observe_speed() == "unknown"
    assert speed_status(adapter) == {"requested": "paused", "observed": "unknown"}


def test_saved_world_observed_speed_reports_read_value():
    adapter = SavedWorldAdapter.__new__(SavedWorldAdapter)
    adapter.bridge = FakeBridge(speed=3.0)
    adapter.speed = 3.0

    assert adapter.observe_speed() == "3"
    assert speed_status(adapter) == {"requested": 3.0, "observed": "3"}
