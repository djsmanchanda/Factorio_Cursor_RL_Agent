# Path: tools/dashboard_runtime.py
# Purpose: Run the dashboard's fixed local Factorio operations and expose their status safely.

from __future__ import annotations

import filecmp
import json
import os
import re
import shutil
import signal
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
from orchestrator.game_bridge import GameBridge, load_json
from orchestrator.research_queue import (
    ResearchQueueError, load_queue, merge_queue, validate_technology_list,
)
from tools.runner_log_retention import archive_runner_sessions, archive_stale_runner_files
from helper_agent import dashboard as helper_dashboard
from tools.runner_process import clear_runner_pid, running_runner_pid


@dataclass(frozen=True)
class DashboardConfig:
    server_data: Path
    source_save: Path
    rcon_password: str
    technology: str = "mining-productivity-4"
    game_port: int = 34199
    rcon_port: int = 27017
    runtime_root: Path | None = None
    python_bin: Path | None = None
    campaign_manager: Path | None = None
    server_manager: Path | None = None
    runner_manager: Path | None = None
    gui_mods: Path | None = None
    helper_root: Path | None = None

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
    def research_queue_file(self) -> Path:
        return self.server_data / "logs" / "research-queue.json"

    @property
    def server_save(self) -> Path:
        return self.server_data / "saves" / "mod_playground.zip"

    @property
    def helper_data_root(self) -> Path:
        return self.helper_root or Path.home() / ".local/share/factorio-rl/helper_agent"


class OperationError(RuntimeError):
    pass


class OperationManager:
    ACTIONS = {
        "deploy_mod", "restart_server", "stop_runner",
        "fresh_campaign", "resume_runner", "stop_factorio",
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
                "host": "127.0.0.1",
                "game_port": self.config.game_port,
                "game_address": f"127.0.0.1:{self.config.game_port}",
                "game": self._game_port_open(self.config.game_port),
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
        if action == "stop_factorio" and confirmation != "STOP_FACTORIO_SERVER":
            raise OperationError("Stopping Factorio requires confirmation.")
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

    def last_runner_run(self) -> dict:
        """Return the newest complete runner block, including both boundary lines."""
        try:
            data = self.config.runner_log.read_bytes()
        except FileNotFoundError:
            raise OperationError("The runner log does not exist yet.") from None

        lines = data.splitlines(keepends=True)
        end_index = next(
            (index for index in range(len(lines) - 1, -1, -1)
             if lines[index].rstrip().endswith(b" RUN END")),
            None,
        )
        if end_index is None:
            raise OperationError("No completed runner run is available yet.")
        start_index = next(
            (index for index in range(end_index, -1, -1)
             if b" RUN START:" in lines[index]),
            None,
        )
        if start_index is None:
            raise OperationError("The latest RUN END has no matching RUN START.")
        text = b"".join(lines[start_index:end_index + 1]).decode("utf-8", errors="replace")
        return {"text": text}

    def helper_agent(self) -> dict:
        return helper_dashboard.helper_view(self.config.helper_data_root)

    def submit_helper_feedback(self, payload: dict) -> dict:
        return helper_dashboard.submit_feedback(self.config.helper_data_root, payload)

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

    def research_queue(self) -> dict:
        path = self.config.research_queue_file
        if not path.exists():
            return {
                "schema_version": "1.0.0", "surface": "nauvis", "force": "player",
                "items": [], "updated_at": None, "last_error": None,
            }
        try:
            return load_queue(path)
        except ResearchQueueError as error:
            raise OperationError(str(error)) from error

    def _research_bridge(self) -> GameBridge:
        return GameBridge(
            script_output=self.config.script_output,
            host="127.0.0.1",
            port=self.config.rcon_port,
            password=self.config.rcon_password,
            command_timeout=30.0,
        )

    def research_options(self) -> dict:
        """Return the live force's open targets for the Linux console."""
        bridge = self._research_bridge()
        try:
            report = load_json(bridge.research_options(force="player"))
        except Exception as error:
            raise OperationError(f"Cannot read live research options: {error}") from error
        finally:
            bridge.close()
        if not report.get("ok"):
            raise OperationError(report.get("error", "Research options report failed"))
        return report

    @staticmethod
    def _level_parts(technology: str) -> tuple[str, int] | None:
        match = re.fullmatch(r"(.+)-(\d+)", technology)
        return (match.group(1), int(match.group(2))) if match else None

    def _validate_live_research_targets(
        self, technologies: list[str], candidate: list[str], active: set[str],
    ) -> None:
        bridge = self._research_bridge()
        try:
            for technology_name in technologies:
                report = load_json(
                    bridge.research_status(force="player", technology=technology_name)
                )
                if not report.get("ok"):
                    raise OperationError(
                        f"{technology_name} is not selectable: {report.get('error', report)}"
                    )
                technology = report.get("technology") or {}
                state = technology.get("state")
                if technology.get("target_completed") or state == "completed":
                    raise OperationError(f"{technology_name} is already researched")
                if not technology.get("enabled", False):
                    raise OperationError(f"{technology_name} is locked; its prerequisites are not open")
                parts = self._level_parts(technology_name)
                current_level = technology.get("current_level")
                requested_level = technology.get("requested_level")
                if state != "future" and not (
                    state == "available"
                    and not technology.get("researched", False)
                    and isinstance(current_level, int)
                    and isinstance(requested_level, int)
                    and requested_level > current_level
                ):
                    continue
                if not parts or not isinstance(current_level, int) or not isinstance(requested_level, int):
                    raise OperationError(f"{technology_name} is not open yet")
                stem, _ = parts
                index = candidate.index(technology_name)
                first_required = current_level if not technology.get("researched", False) else current_level + 1
                missing = [
                    f"{stem}-{level}"
                    for level in range(first_required, requested_level)
                    if f"{stem}-{level}" not in active
                    and f"{stem}-{level}" not in candidate[:index]
                ]
                if missing:
                    raise OperationError(
                        f"{technology_name} is locked until {', '.join(missing)} is running or queued first"
                    )
        finally:
            bridge.close()

    def queue_research(self, technologies: list[str], *, mode: str = "replace") -> None:
        """Persist a queue and restart the native runner in queue mode."""
        if not self._uses_native_runner_manager:
            raise OperationError("Research queue controls are currently Linux-only.")
        try:
            technologies = validate_technology_list(technologies)
            queue_path = self.config.research_queue_file
            existing = load_queue(queue_path) if queue_path.exists() else None
            bridge = self._research_bridge()
            try:
                scope = load_json(bridge.research_status(force="player"))
                if not scope.get("ok"):
                    raise OperationError(scope.get("error", "Research status report failed"))
                active = set(scope.get("research_queue") or [])
                if scope.get("current_research"):
                    active.add(scope["current_research"])
                remembered = scope.get("current_target")
                prefix: list[str] = []
                if mode == "append" and (existing is None or not existing["items"]):
                    if remembered:
                        prefix.append(remembered)
                    elif scope.get("current_research"):
                        fallback = getattr(self.config, "technology", None)
                        current_parts = self._level_parts(scope["current_research"])
                        fallback_parts = self._level_parts(fallback) if isinstance(fallback, str) else None
                        if (
                            current_parts and fallback_parts
                            and current_parts[0] == fallback_parts[0]
                            and fallback_parts[1] > current_parts[1]
                        ):
                            prefix.append(fallback)
                        else:
                            prefix.append(scope["current_research"])
                    prefix.extend(scope.get("research_queue") or [])
                    prefix = list(dict.fromkeys(prefix))
                merge_input = prefix + technologies
                candidate = (
                    [item["technology"] for item in existing["items"]]
                    + technologies
                    if mode == "append" and existing is not None
                    else merge_input
                )
                validation_active = active if mode == "append" else set()
                self._validate_live_research_targets(technologies, candidate, validation_active)
                queue = merge_queue(queue_path, merge_input, mode=mode)
            finally:
                bridge.close()
        except ResearchQueueError as error:
            raise OperationError(str(error)) from error
        if not self._lock.acquire(blocking=False):
            raise OperationError(f"Another action is already running: {self._active}")
        action = "set_research" if mode == "replace" else "queue_research"
        self._active = action
        self._started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        threading.Thread(
            target=self._run_research_queue_action,
            args=(action, queue),
            daemon=True,
        ).start()

    def _run_research_queue_action(self, action: str, queue: dict) -> None:
        try:
            self._write(
                f"START {action}: "
                + ",".join(item["technology"] for item in queue["items"])
            )
            self._stop_runner()
            self._run_native_runner_manager(
                "start", queue_file=self.config.research_queue_file,
            )
            result = f"{action} accepted"
            self._write(f"OK {result}")
        except Exception as exc:
            result = f"{action} failed: {type(exc).__name__}: {exc}"
            self._write(f"ERROR {result}")
        finally:
            self._last_action = action
            self._last_result = result
            self._active = None
            self._lock.release()

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
        if self._uses_native_server_manager:
            # Linux deployment is a lifecycle operation: the native manager
            # deliberately refuses to replace a loaded mod while Factorio is
            # running. Preserve whether the runner was active and restore it
            # after the server has loaded the new copies.
            runner_was_running = bool(self._runner_pids())
            self._stop_runner()
            self._stop_server()
            self._run_native_server_manager("deploy")
            self._launch_server()
            self._wait_for_port(self.config.rcon_port, True, 90)
            if runner_was_running:
                self._restart_runner()
            return
        self._run_checked([
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(REPO_ROOT / "scripts" / "deploy_mod.ps1"),
            "-IncludeDedicated", "-DedicatedServerData", str(self.config.server_data),
        ])

    def _restart_runner(self) -> None:
        if self._uses_native_runner_manager:
            self._run_native_runner_manager("restart")
            return
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
        if self._uses_native_runner_manager:
            self._run_native_runner_manager("stop")
            self._runner = None
            return
        pids = self._runner_pids()
        if not pids:
            self._write("Runner is already stopped")
            return
        if self._uses_native_server_manager:
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        else:
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
        # A hidden UAC-launched PowerShell can be terminated with
        # STATUS_CONTROL_C_EXIT (0xC000013A) before the child server starts.
        # Use the same visible, user-approvable elevation path as restore; the
        # server launch is still bounded by the RCON readiness wait below.
        self._launch_server(visible_admin_shell=True)
        self._wait_for_port(self.config.rcon_port, True, 90)

    def _restore_save(self) -> None:
        self._stop_runner()
        self._stop_server()
        if self._uses_native_server_manager:
            self._run_native_server_manager("reset", source_save=self.config.source_save)
            self._launch_server()
            self._wait_for_port(self.config.rcon_port, True, 90)
            return
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

    def _resume_runner(self) -> None:
        """Restart the controller while explicitly preserving the current world."""
        self._restart_runner()

    def _fresh_campaign(self) -> None:
        """Run the atomic stop/deploy/reset/start sequence with a new episode ID."""
        if getattr(self.config, "campaign_manager", None):
            self._run_native_campaign_manager("fresh")
            return
        raise OperationError("Fresh deterministic campaigns require the native Linux managers.")

    def _stop_factorio(self) -> None:
        """Stop the controller before terminating its Factorio server."""
        self._stop_runner()
        self._stop_server()

    def _full_refresh(self) -> None:
        self._stop_runner()
        self._stop_server()
        if self._uses_native_server_manager:
            self._run_native_server_manager("deploy")
        else:
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
        if self._uses_native_server_manager:
            self._run_native_server_manager("stop")
            self._wait_for_port(self.config.rcon_port, False, 15)
            self._write("Native deterministic server stopped")
            return
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
        if self._uses_native_server_manager:
            self._run_native_server_manager("start")
            return
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

    @property
    def _uses_native_server_manager(self) -> bool:
        return getattr(self.config, "server_manager", None) is not None

    @property
    def _uses_native_runner_manager(self) -> bool:
        return getattr(self.config, "runner_manager", None) is not None

    def _run_native_server_manager(
        self, action: str, *, source_save: Path | None = None,
    ) -> None:
        manager = Path(self.config.server_manager)
        if not manager.is_file() or not os.access(manager, os.X_OK):
            raise OperationError(f"Native server manager is unavailable or not executable: {manager}")
        command = [
            str(manager), action,
            "--root", str(self.config.server_data),
            "--game-port", str(self.config.game_port),
            "--rcon-port", str(self.config.rcon_port),
        ]
        gui_mods = getattr(self.config, "gui_mods", None)
        if action == "deploy" and gui_mods is not None:
            command.extend(["--gui-mods", str(gui_mods)])
        if action == "reset":
            if source_save is None:
                raise OperationError("Native save reset requires a configured source save.")
            command.extend(["--source-save", str(source_save)])
        self._run_checked(command)

    def _run_native_runner_manager(self, action: str, *, queue_file: Path | None = None) -> None:
        manager = Path(self.config.runner_manager)
        if not manager.is_file() or not os.access(manager, os.X_OK):
            raise OperationError(f"Native runner manager is unavailable or not executable: {manager}")
        command = [
            str(manager), action,
            "--root", str(self.config.server_data),
            "--rcon-port", str(self.config.rcon_port),
            "--technology", self.config.technology,
            "--python", sys.executable,
        ]
        if queue_file is not None:
            command.extend(["--queue-file", str(queue_file)])
        self._run_checked(command)

    def _run_native_campaign_manager(self, action: str) -> None:
        manager = Path(self.config.campaign_manager)
        if not manager.is_file() or not os.access(manager, os.X_OK):
            raise OperationError(f"Native campaign manager is unavailable or not executable: {manager}")
        command = [
            str(manager), action,
            "--source-save", str(self.config.source_save),
            "--root", str(self.config.server_data),
            "--runtime-root", str(getattr(self.config, "runtime_root", None)
                                  or Path.home() / ".local/share/factorio-rl/runtime/factorio-2.1.14"),
            "--gui-mods", str(self.config.gui_mods),
            "--python", str(getattr(self.config, "python_bin", None) or sys.executable),
            "--technology", self.config.technology,
            "--game-port", str(self.config.game_port),
            "--rcon-port", str(self.config.rcon_port),
        ]
        self._run_checked(command)

    def _run_visible_elevated_script(self, script: Path, *arguments: str) -> None:
        values = [
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
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
        # Native Factorio startup/reset can legitimately exceed the normal
        # command timeout while a large save is migrated.  Keep this separate
        # from runner and dashboard commands so Linux lifecycle actions do not
        # report a false failure while the server continues booting.
        is_native_server_command = (
            self._uses_native_server_manager
            and self.config.server_manager is not None
            and command
            and Path(command[0]) == Path(self.config.server_manager)
        )
        is_native_campaign_command = (
            command and getattr(self.config, "campaign_manager", None) is not None
            and Path(command[0]) == Path(self.config.campaign_manager)
        )
        timeout = 300 if is_native_server_command or is_native_campaign_command else 120
        completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, timeout=timeout)
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

    def _game_port_open(self, port: int) -> bool:
        if self._uses_native_server_manager:
            return self._udp_port_bound(port)
        return self._port_open(port)

    @staticmethod
    def _udp_port_bound(port: int) -> bool:
        """Factorio game traffic is UDP; RCON is intentionally checked over TCP."""
        try:
            result = subprocess.run(
                ["ss", "-lunH", f"sport = :{port}"],
                text=True, capture_output=True, timeout=1,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and bool(result.stdout.strip())

    def _wait_for_port(self, port: int, wanted: bool, timeout: int) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._port_open(port) is wanted:
                return
            time.sleep(1)
        state = "open" if wanted else "close"
        raise OperationError(f"Timed out waiting for port {port} to {state}")
