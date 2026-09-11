# Path: tests/test_campaign_observers.py | Purpose: Observer isolation and restart recovery contracts.
from dataclasses import dataclass
import json
from pathlib import Path
from threading import Barrier

import pytest

from tools import campaign_observers as observers


@dataclass(frozen=True)
class Config:
    opencode_log_dir: Path
    state_root: Path
    observations: Path
    model: str = "configured-model"
    variant: str = "xhigh"
    read_only: bool = False
    model_timeout_seconds: int = 1800


def test_roles_run_concurrently_and_resume_independent_sessions(tmp_path, monkeypatch):
    config = Config(tmp_path / "sessions", tmp_path, tmp_path / "notes")
    barrier = Barrier(4, timeout=5)
    calls = []

    def ask(cfg, prompt, session, sequence):
        assert cfg.read_only and cfg.model_timeout_seconds == 240
        role = cfg.opencode_log_dir.name
        calls.append((role, session, sequence, cfg.model, prompt))
        barrier.wait()
        return f"session-{role}", json.dumps({"type": "text", "part": {"text": f"Evidence for {role}"}})

    monkeypatch.setattr(observers, "_ask", ask)
    board = observers.observe_team(config, "episode-1", 1)
    observers.observe_team(config, "episode-1", 2, terminal=True)
    for role in observers.ROLES:
        role_calls = [call for call in calls if call[0] == role]
        assert [call[1] for call in role_calls] == [None, f"session-{role}"]
        assert [call[2] for call in role_calls] == [1, 2]
        assert all(call[3] == config.model for call in role_calls)
        assert "Do not edit any file" in role_calls[0][4]
        assert str(board) in role_calls[0][4]
    assert board.read_text().count("last successful checkpoint=2") == 4


def test_failed_observer_preserves_findings_and_other_roles_finish(tmp_path, monkeypatch):
    config = Config(tmp_path / "sessions", tmp_path, tmp_path / "notes")

    def ask(cfg, prompt, session, sequence):
        assert cfg.read_only and cfg.model_timeout_seconds == 240
        role = cfg.opencode_log_dir.name
        if sequence == 2 and role == "supply":
            raise TimeoutError("bounded observer timeout")
        return f"session-{role}", json.dumps({"type": "text", "part": {"text": f"Evidence {sequence}"}})

    monkeypatch.setattr(observers, "_ask", ask)
    board = observers.observe_team(config, "episode-1", 1)
    prior = (board.parent / "supply/findings.md").read_text()
    observers.observe_team(config, "episode-1", 2)
    assert (board.parent / "supply/findings.md").read_text() == prior
    state = json.loads((board.parent / "supply/state.json").read_text())
    assert state["status"] == "error"
    assert state["session_id"] == "session-supply"
    assert "last successful checkpoint=1" in board.read_text()
    assert board.read_text().count("last successful checkpoint=2") == 3
    observers.observe_team(config, "episode-1", 3)
    state = json.loads((board.parent / "supply/state.json").read_text())
    assert state["status"] == "complete"
    assert "error" not in state


def test_observer_does_not_publish_tool_output_as_findings(tmp_path, monkeypatch):
    config = Config(tmp_path, tmp_path, tmp_path / "notes")
    monkeypatch.setattr(observers, "_ask", lambda *args: ("session", '{"type":"tool","text":"success"}'))
    board = observers.observe_team(config, "episode-2", 1)
    assert board.read_text().count(": error;") == 4
    assert not (board.parent / "supply/findings.md").exists()


def test_episode_id_cannot_escape_board_root(tmp_path):
    config = Config(tmp_path, tmp_path, tmp_path / "notes")
    with pytest.raises(ValueError, match="episode"):
        observers.observe_team(config, "../other", 1)
