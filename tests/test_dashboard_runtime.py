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


def test_native_manager_dispatches_deploy_and_start_without_powershell(monkeypatch) -> None:
    manager = object.__new__(OperationManager)
    manager.config = SimpleNamespace(
        server_manager=Path("/native/manage-server"),
        server_data=Path("/native/deterministic"),
        game_port=34199,
        rcon_port=27017,
    )
    commands: list[list[str]] = []
    manager._run_checked = lambda command: commands.append(command)
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    monkeypatch.setattr(dashboard_runtime.os, "access", lambda *_args: True)

    manager._deploy_mod()
    manager._launch_server(visible_admin_shell=True)

    assert commands == [
        [
            "/native/manage-server", "deploy", "--root", "/native/deterministic",
            "--game-port", "34199", "--rcon-port", "27017",
        ],
        [
            "/native/manage-server", "start", "--root", "/native/deterministic",
            "--game-port", "34199", "--rcon-port", "27017",
        ],
    ]
