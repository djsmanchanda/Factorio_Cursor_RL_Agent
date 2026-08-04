# Path: tools/dashboard_runtime.py
# Purpose: Run the dashboard's fixed local Factorio operations and expose their status safely.

from __future__ import annotations

import filecmp
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.rcon_client import RconClient, RconError
from tools.runner_log_retention import archive_runner_sessions, archive_stale_runner_files
from tools.runner_process import clear_runner_pid, running_runner_pid


@dataclass(frozen=True)
class DashboardConfig:
    server_data: Path
    source_save: Path
    rcon_password: str
    technology: str = "mining-productivity-4"
    game_port: int = 34199
    rcon_port: int = 27017

    @property
    def script_output(self) -> Path:
        return self.server_data / "script-output"

    @property
    def runner_log(self) -> Path:
        return self.server_data / "logs" / "autonomous-run.log"

    @property
    def runner_pid_file(self) -> Path:
        return self.server_data / "logs" / "autonomous-run.pid"

    @property
    def priority_file(self) -> Path:
        return self.server_data / "logs" / "autonomous-priorities.json"

    @property
    def server_save(self) -> Path:
        return self.server_data / "saves" / "mod_playground.zip"


class OperationError(RuntimeError):
    pass


class OperationManager:
    ACTIONS = {
        "deploy_mod", "restart_runner", "stop_runner", "restart_server",
        "restore_save", "full_refresh",
    }

    def __init__(self, config: DashboardConfig):
        self.config = config
        self._lock = threading.Lock()
        self._active: str | None = None
        self._last_action: str | None = None
        self._last_result = "No dashboard action has run yet."
        self._started_at: str | None = None
        self._runner: subprocess.Popen[bytes] | None = None
        self.control_log = config.server_data / "logs" / "dashboard-control.log"
        if not self._runner_pids():
            try:
                self._archive_runner_logs()
            except OSError as error:
                self._last_result = f"Runner log retention skipped: {error}"

    @property
    def logs(self) -> dict[str, Path]:
        return {
            "runner": self.config.runner_log,
            "server": self.config.server_data / "logs" / "factorio-stdout.log",
            "errors": self.config.server_data / "logs" / "factorio-stderr.log",
            "control": self.control_log,
        }

    def status(self) -> dict:
        runner_pids = self._runner_pids()
        return {
            "server": {
                "game": self._port_open(self.config.game_port),
                "rcon": self._port_open(self.config.rcon_port),
            },
            "runner": {"running": bool(runner_pids), "pids": runner_pids},
            "operation": {
                "active": self._active,
                "last_action": self._last_action,
                "last_result": self._last_result,
                "started_at": self._started_at,
            },
            "technology": self.config.technology,
        }

    def start(self, action: str, confirmation: str = "") -> None:
        if action not in self.ACTIONS:
            raise OperationError(f"Unknown action: {action}")
        if not self._lock.acquire(blocking=False):
            raise OperationError(f"Another action is already running: {self._active}")
        self._active = action
        self._started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        threading.Thread(target=self._run_action, args=(action,), daemon=True).start()

    def read_log(self, name: str, offset: int) -> dict:
        path = self.logs.get(name)
        if path is None:
            raise OperationError(f"Unknown log: {name}")
        if not path.exists():
            return {"text": "", "offset": 0, "reset": offset > 0}
        size = path.stat().st_size
        reset = offset < 0 or offset > size
        start = 0 if reset else offset
        with path.open("rb") as handle:
            handle.seek(start)
            data = handle.read(256_000)
            new_offset = handle.tell()
        return {"text": data.decode("utf-8", errors="replace"), "offset": new_offset, "reset": reset}

    def priorities(self) -> dict:
        path = self.config.priority_file
        if not path.exists():
            return {"version": "1.0.0", "tick": None, "items": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            raise OperationError(f"Cannot read priority queue: {error}") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise OperationError("Priority queue has an invalid structure.")
        tick = int(payload.get("tick") or 0)
        for item in payload["items"]:
            if not isinstance(item, dict):
                raise OperationError("Priority queue contains an invalid task.")
            elapsed = max(0, tick - int(item.get("created_tick", tick)))
            item.setdefault("elapsed_ticks", elapsed)
            age_bonus = min(20, elapsed // 18_000)
            item.setdefault("rating", min(100, int(item.get("base_rating", 0)) + age_bonus))
            item.setdefault("progress_percent", 0)
            item.setdefault("status", "ready")
            item.setdefault("reason", "")
        return payload

    def _run_action(self, action: str) -> None:
        try:
            self._write(f"START {action}")
            getattr(self, f"_{action}")()
            result = f"{action} completed"
            self._write(f"OK {result}")
        except Exception as exc:
            result = f"{action} failed: {type(exc).__name__}: {exc}"
            self._write(f"ERROR {result}")
        finally:
            self._last_action = action
            self._last_result = result
            self._active = None
            self._lock.release()

    def _write(self, message: str) -> None:
        self.control_log.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.control_log.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")

    def _deploy_mod(self) -> None:
        self._run_checked([
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(REPO_ROOT / "scripts" / "deploy_mod.ps1"),
            "-IncludeDedicated", "-DedicatedServerData", str(self.config.server_data),
        ])

    def _restart_runner(self) -> None:
        if not self._port_open(self.config.rcon_port):
            raise OperationError("RCON is offline; start the server first.")
        self._stop_runner()
        command = [
            sys.executable, "-u", str(REPO_ROOT / "tools" / "autonomous_run.py"),
            "research", self.config.technology,
            "--surface", "nauvis", "--force", "player",
            "--rcon-host", "127.0.0.1", "--rcon-port", str(self.config.rcon_port),
            "--rcon-password", self.config.rcon_password,
            "--script-output", str(self.config.script_output),
            "--reference-point", "3", "-1", "--max-iterations", "100",
        ]
        self._write("RUN " + subprocess.list2cmdline(command))
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._runner = subprocess.Popen(
            command, cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        self._write(f"Runner started as PID {self._runner.pid}")

    def _stop_runner(self) -> None:
        pids = self._runner_pids()
        if not pids:
            self._write("Runner is already stopped")
            return
        for pid in pids:
            subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/T", "/F"], check=False,
                capture_output=True, text=True,
            )
        self._runner = None
        for pid in pids:
            clear_runner_pid(self.config.runner_pid_file, pid)
        self._write(f"Stopped runner PID(s): {', '.join(map(str, pids))}")

    def _restart_server(self) -> None:
        self._stop_runner()
        self._stop_server()
        self._launch_server()
        self._wait_for_port(self.config.rcon_port, True, 90)

    def _restore_save(self) -> None:
        self._stop_runner()
        self._stop_server()
        if not self.config.source_save.is_file():
            raise OperationError(f"Starting save not found: {self.config.source_save}")
        self.config.server_save.parent.mkdir(parents=True, exist_ok=True)
        if self.config.server_save.exists():
            backup_dir = self.config.server_save.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = backup_dir / f"mod_playground-{stamp}.zip"
            shutil.copy2(self.config.server_save, backup)
            self._write(f"Backed up current save to {backup}")
        shutil.copy2(self.config.source_save, self.config.server_save)
        if not filecmp.cmp(
            self.config.source_save, self.config.server_save, shallow=False,
        ):
            raise OperationError("Restored save does not match the configured starting save.")
        self._write(f"Restored and verified starting save from {self.config.source_save}")
        self._launch_server(visible_admin_shell=True)
        self._wait_for_port(self.config.rcon_port, True, 90)

    def _full_refresh(self) -> None:
        self._stop_runner()
        self._stop_server()
        self._deploy_mod()
        self._launch_server()
        self._wait_for_port(self.config.rcon_port, True, 90)
        self._restart_runner()

    def _stop_server(self) -> None:
        if self._port_open(self.config.rcon_port):
            try:
                client = RconClient(
                    "127.0.0.1", self.config.rcon_port, self.config.rcon_password,
                    timeout=3,
                )
                try:
                    client.command("/quit")
                finally:
                    client.close()
                self._wait_for_port(self.config.rcon_port, False, 20)
                self._write("Server stopped through RCON")
            except (OSError, TimeoutError, RconError, OperationError) as error:
                self._write(f"Graceful shutdown did not finish: {error}")
        # Always clean up the exact configured process and launcher. A closed
        # RCON port does not prove that either one has exited.
        self._run_elevated_script(
            REPO_ROOT / "scripts" / "stop_dedicated_server.ps1",
            "-ServerData", str(self.config.server_data),
            "-RconPort", str(self.config.rcon_port),
        )
        self._wait_for_port(self.config.rcon_port, False, 15)
        self._write("Dedicated server process and launcher shell stopped")

    def _launch_server(self, *, visible_admin_shell: bool = False) -> None:
        launcher = REPO_ROOT / "scripts" / "launch_dedicated_server.ps1"
        self._write("Requesting elevated server launch; accept the Windows UAC prompt")
        if visible_admin_shell:
            self._run_visible_elevated_script(
                launcher, "-ServerData", str(self.config.server_data),
            )
            return
        self._run_elevated_script(
            launcher, "-ServerData", str(self.config.server_data),
        )

    def _run_visible_elevated_script(self, script: Path, *arguments: str) -> None:
        values = [
            "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(script), *arguments,
        ]
        escaped = ",".join(
            "'" + value.replace("'", "''") + "'" for value in values
        )
        command = (
            "$p=Start-Process powershell.exe -Verb RunAs -PassThru "
            f"-ArgumentList @({escaped});$p.Id"
        )
        self._run_checked(["powershell.exe", "-NoProfile", "-Command", command])

    def _run_elevated_script(self, script: Path, *arguments: str) -> None:
        values = [
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
            *arguments,
        ]
        escaped = ",".join(
            "'" + value.replace("'", "''") + "'" for value in values
        )
        command = (
            "$p=Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden "
            f"-Wait -PassThru -ArgumentList @({escaped});exit $p.ExitCode"
        )
        self._run_checked(["powershell.exe", "-NoProfile", "-Command", command])
    def _run_checked(self, command: list[str]) -> None:
        completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, timeout=120)
        if completed.stdout.strip():
            self._write(completed.stdout.strip())
        if completed.stderr.strip():
            self._write(completed.stderr.strip())
        if completed.returncode:
            raise OperationError(f"Command exited {completed.returncode}")

    def _archive_runner_logs(self) -> None:
        archived = archive_runner_sessions(self.config.runner_log, keep=3)
        moved = archive_stale_runner_files(self.config.runner_log)
        if archived is not None:
            self._write(
                f"Archived {archived.session_count} old runner session(s) to "
                f"{archived.path}"
            )
        if moved:
            self._write(f"Archived {len(moved)} legacy runner log file(s)")

    def _runner_pids(self) -> list[int]:
        if self._runner is not None:
            if self._runner.poll() is None:
                return [self._runner.pid]
            self._runner = None
        pid = running_runner_pid(self.config.runner_pid_file)
        return [pid] if pid is not None else []

    @staticmethod
    def _port_open(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return True
        except OSError:
            return False

    def _wait_for_port(self, port: int, wanted: bool, timeout: int) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._port_open(port) is wanted:
                return
            time.sleep(1)
        state = "open" if wanted else "close"
        raise OperationError(f"Timed out waiting for port {port} to {state}")
