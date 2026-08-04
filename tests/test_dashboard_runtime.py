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
