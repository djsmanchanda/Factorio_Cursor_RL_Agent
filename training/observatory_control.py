# Path: training/observatory_control.py
# Purpose: Safely start and stop isolated WSL RL training from the Observatory.

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TrainingControlError(RuntimeError):
    """A bounded Observatory training-control operation could not be completed."""


@dataclass(frozen=True)
class TrainingStartRequest:
    server_count: int
    runner_limit: int
    initial_runners: int
    start_seed: int
    episodes_per_policy: int = 100
    policy_rounds: int = 1

    @classmethod
    def from_payload(cls, payload: Any) -> "TrainingStartRequest":
        required = {"server_count", "runner_limit", "initial_runners", "episodes_per_policy", "policy_rounds", "start_seed"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise ValueError("training start request shape is invalid")

        def integer(name: str, minimum: int, maximum: int) -> int:
            value = payload[name]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
            return value

        server_count = integer("server_count", 1, 50)
        runner_limit = integer("runner_limit", 1, 80)
        initial_runners = integer("initial_runners", 1, runner_limit)
        episodes_per_policy = integer("episodes_per_policy", 1, 10_000)
        policy_rounds = integer("policy_rounds", 1, 1_000)
        start_seed = integer("start_seed", 0, 2_147_483_647)
        return cls(server_count, runner_limit, initial_runners, start_seed, episodes_per_policy, policy_rounds)


class TrainingSupervisor:
    """Own one explicitly configured, training-only controller process."""

    def __init__(self, repo_root: Path, state_file: Path | None = None):
        self.repo_root = repo_root.resolve()
        self.state_file = state_file or self.repo_root / "data" / "observatory-training-control.json"
        self.native_linux = sys.platform.startswith("linux")
        self.worker_config = self.repo_root / (
            "training-workers-linux.json" if self.native_linux else "training-workers-wsl.json"
        )
        self.manage_script = self.repo_root / "scripts" / (
            "manage_linux_training_worker.sh" if self.native_linux else "manage_wsl_training_worker.ps1"
        )
        self.run_script = self.repo_root / "tools" / "run_adaptive_training_batch.py"
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._operation: threading.Thread | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            if state["status"] == "running" and self._process is not None:
                return {**state, "controller_alive": self._process.poll() is None}
            return {**state, "controller_alive": self._pid_alive(state.get("controller_pid"))}

    def start(self, request: TrainingStartRequest) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            if state["status"] in {"starting", "running", "stopping"}:
                raise TrainingControlError("training is already active or changing state")
            if self._active_worker_ports():
                raise TrainingControlError(
                    "training workers are already listening; stop the current run before starting another"
                )
            run_id = f"training-observatory-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
            run_root = self.repo_root / "data" / run_id
            new_state = {
                "status": "starting", "message": "Preparing isolated training workers…",
                "operation_id": uuid.uuid4().hex, "server_count": request.server_count,
                "runner_limit": request.runner_limit, "initial_runners": request.initial_runners,
                "start_seed": request.start_seed, "episodes_per_policy": request.episodes_per_policy,
                "policy_rounds": request.policy_rounds, "run_id": run_id,
                "database": str(run_root / "experience.db"),
                "live_directory": str(run_root / "live"),
                "controller_pid": None, "started_utc": datetime.now(timezone.utc).isoformat(),
            }
            self._write_state(new_state)
            self._operation = threading.Thread(
                target=self._start_worker, args=(new_state, request),
                name="observatory-training-start", daemon=True,
            )
            self._operation.start()
            return {**new_state, "controller_alive": False}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            if state["status"] in {"starting", "stopping"}:
                raise TrainingControlError("training is already changing state")
            server_count = int(state.get("server_count") or self._configured_server_count() or 0)
            if state["status"] == "stopped" and not self._active_worker_ports():
                return {**state, "message": "Training is already stopped.", "controller_alive": False}
            stopping = {**state, "status": "stopping", "message": "Stopping the controller and isolated workers…"}
            self._write_state(stopping)
            self._operation = threading.Thread(
                target=self._stop_worker, args=(stopping, server_count),
                name="observatory-training-stop", daemon=True,
            )
            self._operation.start()
            return {**stopping, "controller_alive": True}

    def _start_worker(self, state: dict[str, Any], request: TrainingStartRequest) -> None:
        try:
            self._run_manage("configure", request.server_count, request.runner_limit)
            self._run_manage("start", request.server_count, request.runner_limit)
            run_root = self.repo_root / "data" / state["run_id"]
            run_root.mkdir(parents=True, exist_ok=True)
            log_handle = (run_root / "controller.log").open("a", encoding="utf-8")
            command = [
                "--count", "100", "--start-seed", str(request.start_seed),
                "--episodes-per-policy", str(request.episodes_per_policy),
                "--policy-rounds", str(request.policy_rounds),
                "--initial-slots", str(request.initial_runners), "--minimum-slots", "1",
                "--maximum-slots", str(request.runner_limit), "--database", state["database"],
                "--live-directory", state["live_directory"],
            ]
            process = subprocess.Popen(
                self._runner_command(command),
                cwd=self.repo_root, stdout=log_handle, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            log_handle.close()
            with self._lock:
                self._process = process
                running = {**self._read_state(), "status": "running",
                           "message": "Training is running.", "controller_pid": process.pid}
                self._write_state(running)
            return_code = process.wait()
            with self._lock:
                current = self._read_state()
                if current.get("operation_id") == state.get("operation_id") and current["status"] == "running":
                    self._write_state({**current, "status": "completed" if return_code == 0 else "error",
                                       "message": "Training completed." if return_code == 0
                                       else f"Training controller exited with code {return_code}.",
                                       "controller_pid": process.pid})
        except Exception as exc:
            with self._lock:
                self._write_state({**self._read_state(), "status": "error", "message": str(exc)})
            try:
                self._run_manage("stop", request.server_count, request.runner_limit)
            except Exception:
                pass

    def _stop_worker(self, state: dict[str, Any], server_count: int) -> None:
        try:
            with self._lock:
                process = self._process
            pid = process.pid if process is not None else state.get("controller_pid")
            if self._pid_alive(pid):
                self._terminate_tree(int(pid))
            if server_count:
                self._run_manage("stop", server_count, int(state.get("runner_limit") or 1))
            with self._lock:
                self._write_state({**self._read_state(), "status": "stopped",
                                   "message": "Training is stopped.", "controller_pid": None})
        except Exception as exc:
            with self._lock:
                self._write_state({**self._read_state(), "status": "error", "message": str(exc)})

    def _run_manage(self, action: str, server_count: int, runner_limit: int) -> None:
        if self.native_linux:
            result = subprocess.run(
                ["bash", str(self.manage_script), action, "--worker-count", str(server_count),
                 "--slots-per-worker", str(runner_limit), "--stagger-seconds", "2"],
                cwd=self.repo_root, capture_output=True, text=True, timeout=1800,
            )
            if result.returncode:
                output = (result.stdout + "\n" + result.stderr).strip()[-2_000:]
                raise TrainingControlError(output or f"native worker manager exited with code {result.returncode}")
            return
        args = ["-File", str(self.manage_script), "-Action", action,
                "-WorkerCount", str(server_count), "-SlotsPerWorker", str(runner_limit),
                "-StaggerSeconds", "2"]
        self._run_powershell(args, timeout=1800)

    def _runner_command(self, arguments: list[str]) -> list[str]:
        if self.native_linux:
            secret = Path.home() / ".local" / "share" / "factorio-rl" / "training" / "01" / "worker" / "rcon-password"
            return [sys.executable, str(self.run_script), "--workers", str(self.worker_config),
                    "--rcon-secret-file", str(secret), *arguments]
        return [self._powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(self.repo_root / "scripts" / "run_wsl_adaptive_training_batch.ps1"), *arguments]

    def _run_powershell(self, args: list[str], timeout: float) -> None:
        result = subprocess.run(
            [self._powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
            cwd=self.repo_root, capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode:
            output = (result.stdout + "\n" + result.stderr).strip()[-2_000:]
            raise TrainingControlError(output or f"PowerShell exited with code {result.returncode}")

    def _powershell(self) -> str:
        return shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"

    def _configured_server_count(self) -> int | None:
        try:
            payload = json.loads(self.worker_config.read_text(encoding="utf-8"))
            instances = {str(item["instance_id"]) for item in payload.get("workers", [])}
            return len(instances) or None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _active_worker_ports(self) -> bool:
        try:
            payload = json.loads(self.worker_config.read_text(encoding="utf-8"))
            ports = {int(item["rcon_port"]) for item in payload.get("workers", [])}
        except (OSError, ValueError, KeyError, TypeError):
            return False
        for port in ports:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.15):
                    return True
            except OSError:
                continue
        return False

    def _terminate_tree(self, pid: int) -> None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, check=False)
        else:
            os.kill(pid, signal.SIGTERM)

    @staticmethod
    def _pid_alive(pid: Any) -> bool:
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        return True

    def _read_state(self) -> dict[str, Any]:
        if not self.state_file.is_file():
            return {"status": "stopped", "message": "No Observatory-launched training run.",
                    "controller_pid": None, "controller_alive": False}
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else {"status": "error", "message": "invalid control state"}
        except (OSError, ValueError):
            return {"status": "error", "message": "training control state is unreadable", "controller_pid": None}

    def _write_state(self, state: dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.state_file)
