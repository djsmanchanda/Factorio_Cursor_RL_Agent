# Path: tests/test_dashboard_runtime.py
# Purpose: Verify dashboard polling uses the validated runner PID record instead of WMI.

from pathlib import Path
from types import SimpleNamespace

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

    manager._deploy_mod()
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
