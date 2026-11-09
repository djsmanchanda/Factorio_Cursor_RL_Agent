# Path: training/laya_control.py
# Purpose: Safely control the local disposable Laya fine-tune from the RL Observatory.

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LayaTrainingControlError(RuntimeError):
    """A bounded local Laya training-control operation failed."""


class LayaTrainingSupervisor:
    """Own the local Laya fine-tune/evaluation pipeline, never Nauvis authority."""

    def __init__(self, repo_root: Path, state_file: Path | None = None):
        self.repo_root = repo_root.resolve()
        self.state_file = state_file or self.repo_root / "data" / "laya-training-control.json"
        self.cases_path = Path(os.environ.get("LAYA_CASES_PATH", "/tmp/factorio-laya-cases-all.jsonl"))
        self.model_dir = Path(os.environ.get("LAYA_MODEL_DIR", str(self.repo_root / "data" / "laya-base")))
        self.checkpoint_dir = Path(os.environ.get("LAYA_CHECKPOINT_DIR", "/tmp/laya-4060-checkpoint"))
        self.log_path = Path(os.environ.get("LAYA_LOG_PATH", str(self.repo_root / "logs" / "laya-4060-training.log")))
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._monitor: threading.Thread | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            if self._process is not None and self._process.poll() is None:
                return {**state, "controller_alive": True, "pid": self._process.pid}
            external = self._external_pids()
            if external and state.get("status") not in {"completed", "error", "stopped"}:
                return {**state, "status": "running", "controller_alive": True, "pid": external[0], "external": True}
            if external:
                return {**state, "status": "running", "controller_alive": True, "pid": external[0], "external": True}
            return {**state, "controller_alive": False}

    def start(self) -> dict[str, Any]:
        with self._lock:
            current = self.snapshot()
            if current.get("controller_alive"):
                raise LayaTrainingControlError("Laya training is already running")
            for path, label in (
                (self.cases_path, "dataset"),
                (self.model_dir / "rl_agent_config.json", "base model config"),
                (self.model_dir / "model.safetensors", "base model weights"),
            ):
                if not path.is_file() or (label == "dataset" and not path.stat().st_size):
                    raise LayaTrainingControlError(f"Laya {label} is missing: {path}")
            preflight = subprocess.run(
                [sys.executable, str(self.repo_root / "tools" / "laya_preflight.py")],
                cwd=self.repo_root, capture_output=True, text=True, timeout=60,
            )
            if preflight.returncode:
                detail = (preflight.stdout or preflight.stderr).strip()[-1_000:]
                raise LayaTrainingControlError(f"Laya GPU preflight blocked: {detail}")
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            run_id = f"laya-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
            state = {
                "status": "starting", "message": "Starting disposable Laya fine-tuning…",
                "run_id": run_id, "pid": None, "external": False,
                "cases_path": str(self.cases_path), "model_dir": str(self.model_dir),
                "checkpoint_dir": str(self.checkpoint_dir), "log_path": str(self.log_path),
                "started_utc": datetime.now(timezone.utc).isoformat(),
                "authority": "disposable_training_only",
            }
            self._write_state(state)
            environment = os.environ.copy()
            environment.update({
                "LAYA_CASES_PATH": str(self.cases_path),
                "LAYA_MODEL_DIR": str(self.model_dir),
                "LAYA_CHECKPOINT_DIR": str(self.checkpoint_dir),
                "LAYA_PROGRESS_PATH": str(self.checkpoint_dir / "progress.json"),
            })
            log_handle = self.log_path.open("a", encoding="utf-8")
            try:
                self._process = subprocess.Popen(
                    [sys.executable, "-u", str(self.repo_root / "tools" / "train_laya_4060.py"),
                     "--cases", str(self.cases_path), "--model-dir", str(self.model_dir),
                     "--output-dir", str(self.checkpoint_dir)],
                    cwd=self.repo_root, env=environment, stdout=log_handle,
                    stderr=subprocess.STDOUT, start_new_session=True,
                )
            finally:
                log_handle.close()
            running = {**state, "status": "running", "message": "Laya fine-tuning is running.", "pid": self._process.pid}
            self._write_state(running)
            self._monitor = threading.Thread(target=self._wait_for_training, args=(self._process,), daemon=True)
            self._monitor.start()
            return {**running, "controller_alive": True}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            state = self.snapshot()
            pid = state.get("pid")
            if not state.get("controller_alive") or not isinstance(pid, int):
                return {**state, "status": "stopped", "message": "Laya training is not running.", "controller_alive": False}
            stopping = {**state, "status": "stopping", "message": "Stopping disposable Laya training…"}
            self._write_state(stopping)
            try:
                if self._process is not None and self._process.pid == pid:
                    os.killpg(pid, signal.SIGTERM)
                else:
                    os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            return {**stopping, "controller_alive": True}

    def _wait_for_training(self, process: subprocess.Popen) -> None:
        return_code = process.wait()
        with self._lock:
            current = self._read_state()
            if current.get("status") == "stopping":
                self._write_state({**current, "status": "stopped", "message": "Laya training stopped.", "controller_alive": False})
                return
            if return_code:
                self._write_state({**current, "status": "error", "message": f"Laya training exited with code {return_code}.", "controller_alive": False})
                return
            evaluating = {**current, "status": "evaluating", "message": "Training finished; scoring held-out and test splits…"}
            self._write_state(evaluating)
        evaluations: list[tuple[str, int]] = []
        for split in ("held_out", "test"):
            output = self.checkpoint_dir / f"evaluation-{split}.json"
            result = subprocess.run(
                [sys.executable, "tools/evaluate_laya.py", "--cases", str(self.cases_path),
                 "--checkpoint", str(self.checkpoint_dir), "--base-model", str(self.model_dir),
                 "--split", split, "--output", str(output)],
                cwd=self.repo_root, capture_output=True, text=True, timeout=21_600,
            )
            evaluations.append((split, result.returncode))
            if result.returncode:
                with self._lock:
                    self._write_state({**self._read_state(), "status": "error", "message": f"{split} evaluation failed.", "controller_alive": False})
                return
        with self._lock:
            self._write_state({**self._read_state(), "status": "completed", "message": "Laya training and evaluations completed.", "evaluations": [split for split, code in evaluations if code == 0], "controller_alive": False})

    def _external_pids(self) -> list[int]:
        if os.name != "posix":
            return []
        matches: list[tuple[int, int]] = []
        for entry in Path("/proc").glob("[0-9]*"):
            try:
                pid = int(entry.name)
                command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            except (OSError, ValueError):
                continue
            if "tools/train_laya_4060.py" in command and str(self.checkpoint_dir) in command:
                priority = 0 if ("python" in command or "python3" in command) else 1
                matches.append((priority, pid))
        return [pid for _priority, pid in sorted(matches)]

    def _read_state(self) -> dict[str, Any]:
        if not self.state_file.is_file():
            return {"status": "stopped", "message": "No Laya training run has been started from the Observatory.", "controller_alive": False}
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {"status": "error", "message": "invalid Laya control state"}
        except (OSError, ValueError):
            return {"status": "error", "message": "Laya control state is unreadable", "controller_alive": False}

    def _write_state(self, state: dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.state_file)


__all__ = ["LayaTrainingControlError", "LayaTrainingSupervisor"]
