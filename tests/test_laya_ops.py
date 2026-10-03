# Path: tests/test_laya_ops.py | Purpose: Protect the Laya console's episode status, decision view, log tailing, action guards and runner log format.

import json
import os

import pytest

import tools.laya_ops_server as ops_server
from tools.run_log_format import is_run_end_line, is_run_start_line, parse_timed_run_log_line
from training.laya_run_log import RunLog


@pytest.fixture
def episode(tmp_path, monkeypatch):
    worlds = tmp_path / "worlds"
    root = worlds / "clean-test"
    (root / "evidence").mkdir(parents=True)
    (root / "server").mkdir()
    (root / "manifest.json").write_text(json.dumps({"game_port": 34230, "rcon_port": 1}))
    (root / "server/factorio.pid").write_text(str(os.getpid()))
    journal = [
        {"event": "decision", "run_id": "r", "index": 0, "status": "pending", "summary": "Mine", "tick": 1, "phase": "C1",
         "choice": {"need_id": "item:iron-ore", "probability": 0.1}, "authority": "exploration"},
        {"event": "decision", "run_id": "r", "index": 0, "status": "observed", "summary": "Mine", "tick": 1, "phase": "C1",
         "choice": {"need_id": "item:iron-ore", "probability": 0.1}, "authority": "exploration", "measured_per_s": {"iron-ore": 0.5}},
        {"event": "failure", "run_id": "r", "index": 1, "error_type": "RuntimeError", "error": "x"},
        {"event": "summary", "run_id": "r", "stop": "stopped", "decisions": 1, "failures": 1},
    ]
    (root / "evidence/clean-decisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in journal))
    monkeypatch.setattr(ops_server, "WORLDS", worlds)
    monkeypatch.setattr(ops_server, "POINTER", worlds / "laya-ops-current.json")
    monkeypatch.setattr(ops_server, "ACTIONS_LOG", worlds / "actions.log")
    return root


def test_status_decisions_and_log_tail(episode):
    ops = ops_server.Operations(python_bin="python", speed=1.0)
    status = ops.status()
    assert status["episode"]["name"] == "clean-test" and status["episode"]["game"] == "running"
    assert status["episode"]["runner"] == "stopped" and status["episode"]["last_summary"]["stop"] == "stopped"
    rows = ops.decisions()
    assert [r.get("status") or r["event"] for r in rows] == ["observed", "failure", "summary"]  # pending row replaced
    log = episode / "logs/laya-runner.log"
    log.parent.mkdir()
    log.write_text("RUN START: ts=2026-10-03T20:00:00+05:30 command=run\n+1s DECISION 0\n")
    first = ops.read_log("runner", 0)
    assert "DECISION 0" in first["text"]
    with log.open("a") as out:
        out.write("+2s DECISION 1\n")
    assert ops.read_log("runner", first["offset"])["text"] == "+2s DECISION 1\n"
    with pytest.raises(ops_server.OperationError):
        ops.read_log("../../etc/passwd", 0)


def test_actions_require_known_names_and_confirmation(episode):
    ops = ops_server.Operations(python_bin="python", speed=1.0)
    with pytest.raises(ops_server.OperationError, match="Unknown action"):
        ops.start("rm_rf", {})
    with pytest.raises(ops_server.OperationError, match="confirmation"):
        ops.start("fresh_episode", {})
    with pytest.raises(ops_server.OperationError, match="confirmation"):
        ops.start("sync_gui_mods", {"confirmation": "nope"})


def test_runner_log_uses_the_deterministic_format(tmp_path):
    log = RunLog(tmp_path / "runner.log", tmp_path / "hb.json", heartbeat_seconds=0.05)
    log.start(command="run", runner="laya-clean")
    log("DECISION 0 phase=C1")
    log.update(index=0)
    log.end()
    lines = (tmp_path / "runner.log").read_text().splitlines()
    assert is_run_start_line(lines[0]) and is_run_end_line(lines[-1])
    start = parse_timed_run_log_line(lines[0], run_started_at=None)
    assert parse_timed_run_log_line(lines[1], run_started_at=start.timestamp).message == "DECISION 0 phase=C1"
    assert json.loads((tmp_path / "hb.json").read_text())["pid"] == os.getpid()
