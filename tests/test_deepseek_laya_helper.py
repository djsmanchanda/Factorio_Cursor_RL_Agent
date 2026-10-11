# Path: tests/test_deepseek_laya_helper.py | Purpose: Protect DeepSeek defaults, real session continuity and external helper evidence.
import json
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest

from tools import laya_helper_agent as helper

pytestmark = pytest.mark.fast


def response(session="ses-real"):
    return json.dumps({"sessionID": session, "type": "text", "part": {
        "text": "FINDINGS_REPORT_BEGIN\nRead-only observations.\nFINDINGS_REPORT_END"}}) + "\n"


@pytest.fixture
def config(tmp_path):
    return {"model": "opencode-go/deepseek-v4.1-flash", "variant": "high", "report_path": str(tmp_path / "findings.md")}


def test_default_helper_is_exact_deepseek_go_high():
    config = helper.load_config()
    assert (config["model"], config["variant"]) == ("opencode-go/deepseek-v4.1-flash", "high")


def test_hi_captures_session_before_instructions_and_restart_reuses_it(config, tmp_path, monkeypatch):
    calls = []
    def execute(command, **kwargs):
        calls.append(command)
        permissions = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])["permission"]
        assert permissions["*"] == "deny" and permissions["edit"] == "deny" and permissions["bash"] == "deny"
        assert command[command.index("--model") + 1] == config["model"]
        assert command[command.index("--variant") + 1] == "high"
        if len(calls) == 1:
            assert command[-1] == "Hi" and "--session" not in command
        else:
            saved = json.loads((tmp_path / "helper-session.json").read_text())
            assert saved["session_id"] == "ses-real"
            assert command[command.index("--session") + 1] == "ses-real"
        return CompletedProcess(command, 0, response(), "")
    monkeypatch.setattr(helper.subprocess, "run", execute)
    assert helper._ask(config, tmp_path, "Observer instructions", None, "test", "continue") == "ses-real"
    assert helper._ask(config, tmp_path, "Completion comparison", None, "test", "continue") == "ses-real"
    assert [command[-1] for command in calls] == ["Hi", "Observer instructions", "Completion comparison"]
    assert len(list(tmp_path.glob("opencode-*.jsonl"))) == 3


def test_saved_session_cannot_be_misrouted(config, tmp_path, monkeypatch):
    (tmp_path / "helper-session.json").write_text(json.dumps({**config, "session_id": "ses-saved"}))
    monkeypatch.setattr(helper.subprocess, "run", lambda *_a, **_k: pytest.fail("misrouted session sent"))
    with pytest.raises(RuntimeError, match="continuity"):
        helper._ask(config, tmp_path, "Observe", "ses-other", "test", "continue")


def test_transport_cannot_replace_saved_session(config, tmp_path, monkeypatch):
    (tmp_path / "helper-session.json").write_text(json.dumps({**config, "session_id": "ses-saved"}))
    monkeypatch.setattr(helper.subprocess, "run", lambda command, **_k: CompletedProcess(command, 0, response("ses-other"), ""))
    with pytest.raises(RuntimeError, match="different helper session"):
        helper._ask(config, tmp_path, "Observe", None, "test", "continue")
    assert json.loads((tmp_path / "helper-session.json").read_text())["session_id"] == "ses-saved"


def test_timeout_preserves_real_returned_session_and_transcript(config, tmp_path, monkeypatch):
    def execute(command, **_kwargs):
        raise TimeoutExpired(command, 1, output=response("ses-timeout").encode())
    monkeypatch.setattr(helper.subprocess, "run", execute)
    with pytest.raises(TimeoutExpired):
        helper._ask(config, tmp_path, "Observe", None, "test", "continue")
    assert json.loads((tmp_path / "helper-session.json").read_text())["session_id"] == "ses-timeout"
    assert "ses-timeout" in (tmp_path / "opencode-000.jsonl").read_text()


def test_launch_passes_mutable_roots_and_shared_index(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(helper.subprocess, "run", lambda command, **_kwargs: calls.append(command))
    helper.launch("run", tmp_path / "world/logs/run.log", tmp_path / "world",
                  report_root=tmp_path / "reports", data_root=tmp_path / "state", run_index_path=tmp_path / "runs.jsonl")
    command = calls[0]
    assert command[:2] == ["systemd-run", "--user"]
    assert "Restart=no" in command
    for flag, value in [("--report-root", tmp_path / "reports"), ("--data-root", tmp_path / "state"),
                        ("--run-index", tmp_path / "runs.jsonl")]:
        assert command[command.index(flag) + 1] == str(value)
