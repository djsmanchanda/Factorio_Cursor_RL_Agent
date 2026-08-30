# Path: tests/test_dashboard_runtime.py
# Purpose: Verify dashboard polling uses the validated runner PID record instead of WMI.

from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from tools import dashboard_runtime
from tools.dashboard_runtime import OperationManager


def test_runner_pid_probe_uses_pid_record_without_process_scan(monkeypatch) -> None:
    manager = object.__new__(OperationManager)
    manager._runner = None
    manager.config = SimpleNamespace(runner_pid_file=Path("runner.pid"))
    calls: list[Path] = []

    def fake_running_pid(path: Path) -> int:
        calls.append(path)
        return 1234

    def reject_process_scan(*_args, **_kwargs):
        raise AssertionError("dashboard status must not launch a process scan")

    monkeypatch.setattr(dashboard_runtime, "running_runner_pid", fake_running_pid)
    monkeypatch.setattr(dashboard_runtime.subprocess, "run", reject_process_scan)

    assert manager._runner_pids() == [1234]
    assert manager._runner_pids() == [1234]
    assert calls == [Path("runner.pid"), Path("runner.pid")]


def test_finished_managed_runner_falls_back_to_pid_record(monkeypatch) -> None:
    manager = object.__new__(OperationManager)
    manager._runner = SimpleNamespace(poll=lambda: 1)
    manager.config = SimpleNamespace(runner_pid_file=Path("runner.pid"))
    monkeypatch.setattr(dashboard_runtime, "running_runner_pid", lambda _path: 5678)

    assert manager._runner_pids() == [5678]
    assert manager._runner is None


def test_native_status_uses_udp_for_factorio_game_port(monkeypatch) -> None:
    manager = object.__new__(OperationManager)
    manager.config = SimpleNamespace(
        game_port=34199,
        rcon_port=27017,
        server_manager=Path("/native/manage-server"),
        technology="mining-productivity-4",
    )
    manager._active = None
    manager._last_action = None
    manager._last_result = "idle"
    manager._started_at = None
    manager._runner_pids = lambda: []
    manager._port_open = lambda port: port == 27017
    manager._udp_port_bound = lambda port: port == 34199

    assert manager.status()["server"] == {
        "host": "127.0.0.1",
        "game_port": 34199,
        "game_address": "127.0.0.1:34199",
        "game": True,
        "rcon": True,
    }


def test_restart_server_uses_visible_elevation_path(monkeypatch) -> None:
    manager = object.__new__(OperationManager)
    manager._stop_runner = lambda: None
    manager._stop_server = lambda: None
    launched: list[bool] = []
    waited: list[tuple[int, bool, int]] = []
    manager._launch_server = lambda *, visible_admin_shell=False: launched.append(visible_admin_shell)
    manager._wait_for_port = lambda port, wanted, timeout: waited.append((port, wanted, timeout))
    manager.config = SimpleNamespace(rcon_port=27017)

    manager._restart_server()

    assert launched == [True]
    assert waited == [(27017, True, 90)]


def test_native_manager_dispatches_server_lifecycle_without_powershell(monkeypatch) -> None:
    manager = object.__new__(OperationManager)
    manager.config = SimpleNamespace(
        server_manager=Path("/native/manage-server"),
        server_data=Path("/native/deterministic"),
        game_port=34199,
        rcon_port=27017,
        gui_mods=Path("/native/gui-mods"),
    )
    commands: list[list[str]] = []
    manager._run_checked = lambda command: commands.append(command)
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    monkeypatch.setattr(dashboard_runtime.os, "access", lambda *_args: True)

    manager._run_native_server_manager("deploy")
    manager._run_native_server_manager("reset", source_save=Path("/source/mod_playground.zip"))
    manager._launch_server(visible_admin_shell=True)

    assert commands == [
        [
            "/native/manage-server", "deploy", "--root", "/native/deterministic",
            "--game-port", "34199", "--rcon-port", "27017",
            "--gui-mods", "/native/gui-mods",
        ],
        [
            "/native/manage-server", "reset", "--root", "/native/deterministic",
            "--game-port", "34199", "--rcon-port", "27017",
            "--source-save", "/source/mod_playground.zip",
        ],
        [
            "/native/manage-server", "start", "--root", "/native/deterministic",
            "--game-port", "34199", "--rcon-port", "27017",
        ],
    ]


def test_native_deploy_stops_and_restarts_running_server_and_runner(monkeypatch) -> None:
    manager = object.__new__(OperationManager)
    manager.config = SimpleNamespace(
        server_manager=Path("/native/manage-server"),
        server_data=Path("/native/deterministic"),
        game_port=34199,
        rcon_port=27017,
        gui_mods=Path("/native/gui-mods"),
    )
    events: list[object] = []
    manager._runner_pids = lambda: [1234]
    manager._stop_runner = lambda: events.append("stop-runner")
    manager._stop_server = lambda: events.append("stop-server")
    manager._launch_server = lambda: events.append("start-server")
    manager._wait_for_port = lambda *args: events.append(("wait", args))
    manager._restart_runner = lambda: events.append("restart-runner")
    manager._run_native_server_manager = lambda action, **_kwargs: events.append(action)

    manager._deploy_mod()

    assert events == [
        "stop-runner", "stop-server", "deploy", "start-server",
        ("wait", (27017, True, 90)), "restart-runner",
    ]


def test_native_server_manager_commands_get_extended_timeout(monkeypatch) -> None:
    manager = object.__new__(OperationManager)
    manager.config = SimpleNamespace(server_manager=Path("/native/manage-server"))
    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen.update(kwargs)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(dashboard_runtime.subprocess, "run", fake_run)
    manager._run_checked(["/native/manage-server", "start"])

    assert seen["timeout"] == 300


def test_native_runner_manager_dispatches_start_and_stop_without_windows_tools(monkeypatch) -> None:
    manager = object.__new__(OperationManager)
    manager.config = SimpleNamespace(
        runner_manager=Path("/native/manage-runner"),
        server_data=Path("/native/deterministic"),
        rcon_port=27017,
        technology="mining-productivity-4",
    )
    commands: list[list[str]] = []
    manager._run_checked = lambda command: commands.append(command)
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    monkeypatch.setattr(dashboard_runtime.os, "access", lambda *_args: True)

    manager._run_native_runner_manager("stop")
    manager._run_native_runner_manager("restart")

    assert commands == [
        [
            "/native/manage-runner", "stop", "--root", "/native/deterministic",
            "--rcon-port", "27017", "--technology", "mining-productivity-4",
            "--python", dashboard_runtime.sys.executable,
        ],
        [
            "/native/manage-runner", "restart", "--root", "/native/deterministic",
            "--rcon-port", "27017", "--technology", "mining-productivity-4",
            "--python", dashboard_runtime.sys.executable,
        ],
    ]


def test_native_runner_manager_can_dispatch_a_persisted_queue(monkeypatch) -> None:
    manager = object.__new__(OperationManager)
    manager.config = SimpleNamespace(
        runner_manager=Path("/native/manage-runner"),
        server_data=Path("/native/deterministic"),
        rcon_port=27017,
        technology="mining-productivity-4",
    )
    commands: list[list[str]] = []
    manager._run_checked = lambda command: commands.append(command)
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    monkeypatch.setattr(dashboard_runtime.os, "access", lambda *_args: True)

    manager._run_native_runner_manager("start", queue_file=Path("/native/research-queue.json"))

    assert commands == [[
        "/native/manage-runner", "start", "--root", "/native/deterministic",
        "--rcon-port", "27017", "--technology", "mining-productivity-4",
        "--python", dashboard_runtime.sys.executable,
        "--queue-file", "/native/research-queue.json",
    ]]


def test_research_queue_action_stops_runner_and_starts_queue_mode() -> None:
    manager = object.__new__(OperationManager)
    manager.config = SimpleNamespace(research_queue_file=Path("/native/research-queue.json"))
    manager._lock = threading.Lock()
    assert manager._lock.acquire(blocking=False)
    manager._active = "set_research"
    manager._started_at = "now"
    manager._last_action = None
    manager._last_result = "idle"
    manager._write = lambda _message: None
    events: list[object] = []
    manager._stop_runner = lambda: events.append("stop-runner")
    manager._run_native_runner_manager = lambda action, **kwargs: events.append((action, kwargs))
    queue = {"items": [{"technology": "automation"}]}

    manager._run_research_queue_action("set_research", queue)

    assert events == [
        "stop-runner", ("start", {"queue_file": Path("/native/research-queue.json")})
    ]
    assert manager._last_result == "set_research accepted"
    assert manager._active is None


class _FakeLiveResearchBridge:
    def __init__(self, reports: dict[str | None, dict]) -> None:
        self.reports = reports
        self.closed = False

    def research_status(self, *, force: str, technology: str | None = None):
        return self.reports[technology]

    def close(self) -> None:
        self.closed = True


def test_future_repeatable_target_requires_predecessor_in_queue(monkeypatch) -> None:
    manager = object.__new__(OperationManager)
    bridge = _FakeLiveResearchBridge({
        "mining-productivity-5": {"ok": True, "technology": {
            "enabled": True, "researched": True, "state": "future", "current_level": 3,
            "requested_level": 5, "target_completed": False,
        }},
    })
    monkeypatch.setattr(manager, "_research_bridge", lambda: bridge)
    monkeypatch.setattr(dashboard_runtime, "load_json", lambda value: value)

    with pytest.raises(dashboard_runtime.OperationError, match="mining-productivity-4"):
        manager._validate_live_research_targets(
            ["mining-productivity-5"], ["mining-productivity-5"], set(),
        )

    manager._validate_live_research_targets(
        ["mining-productivity-5"], ["mining-productivity-4", "mining-productivity-5"], {"mining-productivity-4"},
    )
    assert bridge.closed


def test_unresearched_next_repeatable_level_requires_current_level_to_be_active(monkeypatch) -> None:
    manager = object.__new__(OperationManager)
    bridge = _FakeLiveResearchBridge({
        "mining-productivity-5": {"ok": True, "technology": {
            "enabled": True, "researched": False, "state": "available",
            "current_level": 4, "requested_level": 5, "target_completed": False,
        }},
    })
    monkeypatch.setattr(manager, "_research_bridge", lambda: bridge)
    monkeypatch.setattr(dashboard_runtime, "load_json", lambda value: value)

    with pytest.raises(dashboard_runtime.OperationError, match="mining-productivity-4"):
        manager._validate_live_research_targets(
            ["mining-productivity-5"], ["mining-productivity-5"], set(),
        )

    manager._validate_live_research_targets(
        ["mining-productivity-5"], ["mining-productivity-4", "mining-productivity-5"], {"mining-productivity-4"},
    )


def test_native_restore_uses_manager_reset_then_starts_server() -> None:
    manager = object.__new__(OperationManager)
    manager.config = SimpleNamespace(
        source_save=Path("/source/mod_playground.zip"),
        rcon_port=27017,
        server_manager=Path("/native/manage-server"),
    )
    calls: list[object] = []
    manager._stop_runner = lambda: calls.append("stop-runner")
    manager._stop_server = lambda: calls.append("stop-server")
    manager._run_native_server_manager = lambda action, **kwargs: calls.append((action, kwargs))
    manager._launch_server = lambda **_kwargs: calls.append("start-server")
    manager._wait_for_port = lambda *args: calls.append(("wait", args))

    manager._restore_save()

    assert calls == [
        "stop-runner",
        "stop-server",
        ("reset", {"source_save": Path("/source/mod_playground.zip")} ),
        "start-server",
        ("wait", (27017, True, 90)),
    ]


def test_public_actions_separate_fresh_campaign_from_controller_resume() -> None:
    assert dashboard_runtime.OperationManager.ACTIONS == {
        "deploy_mod", "restart_server", "stop_runner",
        "fresh_campaign", "resume_runner", "stop_factorio",
    }
    html = (dashboard_runtime.REPO_ROOT / "tools" / "dashboard.html").read_text()
    javascript = (dashboard_runtime.REPO_ROOT / "tools" / "dashboard.js").read_text()
    assert 'data-action="fresh_campaign"' in html
    assert 'Start fresh campaign' in html
    assert 'data-action="resume_runner"' in html
    assert 'current world' in html.lower()
    assert 'without restoring the source save' in javascript
    assert 'START_FRESH_CAMPAIGN' in javascript
    assert 'id="copy-last-run"' in html
    assert 'data-action="stop_factorio"' in html
    assert 'data-action="stop_console"' in html
    assert 'STOP_FACTORIO_SERVER' in javascript
    assert 'STOP_OPERATIONS_CONSOLE' in javascript
    assert 'Restart the operations console to load Copy last run' in javascript
    assert 'data-action="restore_save"' not in html
    assert 'data-action="full_refresh"' not in html


def test_last_runner_run_returns_newest_complete_boundary_block(tmp_path: Path) -> None:
    log = tmp_path / "autonomous-run.log"
    log.write_text(
        "RUN START: ts=2026-08-28T10:00:00+05:30 command=research target=one\n"
        "+1s first\n"
        "+2s RUN END\n"
        "RUN START: ts=2026-08-28T11:00:00+05:30 command=research target=two\n"
        "+1s second\n"
        "+2s RUN END\n"
        "RUN START: ts=2026-08-28T12:00:00+05:30 command=research target=active\n",
        encoding="utf-8",
    )
    manager = object.__new__(OperationManager)
    manager.config = SimpleNamespace(runner_log=log)

    copied = manager.last_runner_run()["text"]

    assert "target=two" in copied
    assert "second" in copied
    assert copied.startswith("RUN START: ts=2026-08-28T11:00:00+05:30")
    assert copied.endswith("+2s RUN END\n")
    assert "target=one" not in copied
    assert "target=active" not in copied


def test_last_runner_run_rejects_log_without_complete_run(tmp_path: Path) -> None:
    log = tmp_path / "autonomous-run.log"
    log.write_text(
        "RUN START: ts=2026-08-28T12:00:00+05:30 command=research\n",
        encoding="utf-8",
    )
    manager = object.__new__(OperationManager)
    manager.config = SimpleNamespace(runner_log=log)

    with pytest.raises(dashboard_runtime.OperationError, match="No completed"):
        manager.last_runner_run()


def test_stop_factorio_stops_runner_before_server() -> None:
    manager = object.__new__(OperationManager)
    events: list[str] = []
    manager._stop_runner = lambda: events.append("runner")
    manager._stop_server = lambda: events.append("server")

    manager._stop_factorio()

    assert events == ["runner", "server"]


def test_stop_factorio_action_requires_explicit_confirmation() -> None:
    manager = object.__new__(OperationManager)

    with pytest.raises(dashboard_runtime.OperationError, match="requires confirmation"):
        manager.start("stop_factorio")


def test_fresh_campaign_invokes_atomic_native_campaign_manager(monkeypatch) -> None:
    manager = object.__new__(OperationManager)
    manager.config = SimpleNamespace(
        source_save=Path("/source/mod_playground.zip"),
        server_data=Path("/native/deterministic"),
        runtime_root=Path("/native/runtime"),
        gui_mods=Path("/native/gui-mods"),
        python_bin=Path("/native/python"),
        technology="mining-productivity-4",
        game_port=34199,
        rcon_port=27017,
        campaign_manager=Path("/native/manage-campaign"),
    )
    commands: list[list[str]] = []
    manager._run_checked = lambda command: commands.append(command)
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    monkeypatch.setattr(dashboard_runtime.os, "access", lambda *_args: True)

    manager._fresh_campaign()

    assert commands == [[
        "/native/manage-campaign", "fresh",
        "--source-save", "/source/mod_playground.zip",
        "--root", "/native/deterministic",
        "--runtime-root", "/native/runtime",
        "--gui-mods", "/native/gui-mods",
        "--python", "/native/python",
        "--technology", "mining-productivity-4",
        "--game-port", "34199",
        "--rcon-port", "27017",
    ]]
