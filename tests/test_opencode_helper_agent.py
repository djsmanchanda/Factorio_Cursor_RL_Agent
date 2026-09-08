"""Verify the permanent OpenCode Helper's per-run observation contract."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tools import opencode_helper_agent as helper
from tools import autonomous_run
from tools.run_log_format import is_helper_agent_line


def _config(tmp_path: Path, log: Path) -> helper.Config:
    return helper.Config(
        run_id="episode-test", log_path=log, manifest_path=None,
        data_root=tmp_path / "state", report_root=tmp_path / "reports",
        dashboard_url="http://127.0.0.1:9137/api/logistic-inventory",
        interval_seconds=120, model="model", variant="xhigh", opencode_bin="opencode",
    )


def test_session_reader_accepts_nested_opencode_event() -> None:
    output = '{"part":{"sessionID":"session-1"}}\n'

    assert helper._session_id(output) == "session-1"


def test_completed_run_wraps_findings_and_appends_runner_pointer(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "autonomous-run.log"
    log.write_text(
        "RUN START: ts=2026-09-08T00:00:00+00:00 command=research\n"
        "+2s STUCK: steel-plate is blocked\n"
        "+3s RUN END\n",
        encoding="utf-8",
    )
    prompts: list[str] = []

    def fake_ask(config: helper.Config, message: str, state: helper.State) -> None:
        prompts.append(message)
        state.session_id = state.session_id or "session-1"

    monkeypatch.setattr(helper, "_ask", fake_ask)
    config = _config(tmp_path, log)

    assert helper.run_helper(config) == 0
    report = config.report_root / "episode-test" / "findings.md"
    assert report.is_file()
    assert len(prompts) == 2
    assert "RUN END for episode-test" in prompts[-1]
    assert "OPENCODE HELPER: findings complete" in log.read_text(encoding="utf-8")
    assert helper._load_state(config.data_root / "runs/episode-test/state.json").completed


def test_helper_rejects_sub_two_minute_observation_intervals(tmp_path: Path) -> None:
    config = _config(tmp_path, tmp_path / "run.log")
    config = helper.Config(**{**config.__dict__, "interval_seconds": 119})

    try:
        helper.run_helper(config)
    except ValueError as error:
        assert "at least 120" in str(error)
    else:
        raise AssertionError("short interval was accepted")


def test_log_parser_recognizes_final_opencode_helper_pointer() -> None:
    assert is_helper_agent_line(
        "OPENCODE HELPER: findings complete /tmp/findings.md"
    )


def test_runner_starts_the_permanent_helper_at_run_start(monkeypatch, tmp_path: Path) -> None:
    messages: list[str] = []
    launched: dict[str, object] = {}
    args = SimpleNamespace(
        episode_id="episode-test", episode_manifest=tmp_path / "manifest.json",
        opencode_helper_data_root=tmp_path / "state",
        opencode_helper_report_root=tmp_path / "reports", no_opencode_helper=False,
    )

    def fake_launch(**kwargs: object) -> str:
        launched.update(kwargs)
        return "pid=123"

    monkeypatch.setattr(autonomous_run, "launch_opencode_helper", fake_launch)

    assert autonomous_run._start_opencode_helper(
        args, log_path=tmp_path / "autonomous-run.log", emit=messages.append,
    ) == "pid=123"
    assert launched["run_id"] == "episode-test"
    assert messages == [
        "OPENCODE HELPER: started read-only observer pid=123 run=episode-test",
    ]
