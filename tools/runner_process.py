# Path: tools/runner_process.py
# Purpose: Track the autonomous runner without repeated PowerShell or WMI process scans.

from __future__ import annotations

import ctypes
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ctypes import wintypes

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _FileTime(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


@contextmanager
def runner_pid_record(path: Path) -> Iterator[None]:
    """Publish this process identity for dashboards and remove it on clean exit."""
    pid = os.getpid()
    token = _process_creation_token(pid)
    boot_id = _linux_boot_id()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"pid": pid, "creation_token": token}
    if boot_id is not None:
        record["boot_id"] = boot_id
    path.write_text(
        json.dumps(record) + "\n",
        encoding="utf-8",
    )
    try:
        yield
    finally:
        clear_runner_pid(path, pid)


def running_runner_pid(path: Path) -> int | None:
    """Return the recorded PID only while the same OS process still exists."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pid = int(payload["pid"])
        expected = payload["creation_token"]
    except (FileNotFoundError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    actual = _process_creation_token(pid)
    if "boot_id" in payload:
        boot_id = _linux_boot_id()
        if boot_id is None or payload["boot_id"] != boot_id:
            return None
    return pid if actual is not None and actual == expected else None


def clear_runner_pid(path: Path, pid: int) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(payload.get("pid", -1)) == pid:
            path.unlink(missing_ok=True)
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return


def _linux_boot_id() -> str | None:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        return str(uuid.UUID(value))
    except (OSError, UnicodeError, ValueError):
        return None


def _process_creation_token(pid: int) -> int | None:
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        creation, exit_time, kernel_time, user_time = (_FileTime() for _ in range(4))
        try:
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return None
            return (creation.high << 32) | creation.low
        finally:
            kernel32.CloseHandle(handle)
    try:
        # Linux /proc field 22 is process start time. Keeping it alongside the
        # PID protects dashboard stop/status actions from PID reuse.
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = stat[stat.rfind(")") + 2 :].split()
        return int(fields[19])
    except (FileNotFoundError, OSError, ValueError, IndexError):
        return None
