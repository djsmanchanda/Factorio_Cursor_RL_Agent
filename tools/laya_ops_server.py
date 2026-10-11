#!/usr/bin/env python3
# Path: tools/laya_ops_server.py
# Purpose: Serve the loopback-only Laya Operations Console: live status board, decisions, logs and fixed runner/server controls.

"""Laya Operations Console, modelled on the deterministic console (tools/dashboard_server.py).

One clean-runner episode is current at a time (pointer file under
data/laya-saved-world/).  Buttons run fixed local operations only; there is
no command input.  The deterministic console, runtime and saves are untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.run_laya_clean import runner_paths
from core.local_time import local_stamp, now_local
from tools.run_ledger import LEDGER
from tools.runner_process import running_runner_pid
from training.laya_mission import PHASES

TOOLS = REPO_ROOT / "tools"
WORLDS = REPO_ROOT / "data/laya-saved-world"
POINTER = WORLDS / "laya-ops-current.json"
ACTIONS_LOG = WORLDS / "laya-ops-actions.log"
GUI_MODS = Path.home() / ".factorio/mods"
HELPER_REPORTS = REPO_ROOT / "docs/rl/laya_helper_runs"
PROJECT_MODS = ("factorio_cursor_rl_agent", "factorio_training_lab")
DEFAULT_PORT = 9140
LOG_CHUNK = 200_000
STATIC = {
    "/dashboard.css": (TOOLS / "dashboard.css", "text/css; charset=utf-8"),
    "/laya_ops.css": (TOOLS / "laya_ops.css", "text/css; charset=utf-8"),
    "/laya_ops.js": (TOOLS / "laya_ops.js", "text/javascript; charset=utf-8"),
}


class OperationError(RuntimeError):
    pass


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _pid_alive(path: Path) -> int | None:
    try:
        pid = int(path.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


class Operations:
    """Fixed local operations on the current clean-runner episode."""

    def __init__(self, python_bin: Path, speed: float) -> None:
        self.python = str(python_bin)
        self.speed = speed
        self.lock = threading.Lock()
        self.state = {"name": None, "status": "ready", "message": "No console action has run yet."}

    # -- episode -----------------------------------------------------------
    def root(self) -> Path | None:
        pointer = _read_json(POINTER)
        if pointer and Path(pointer["root"]).is_dir():
            return Path(pointer["root"])
        roots = sorted((p for p in WORLDS.glob("clean-*") if (p / "manifest.json").is_file()),
                       key=lambda p: p.stat().st_mtime)
        return roots[-1] if roots else None

    def _log(self, message: str) -> None:
        WORLDS.mkdir(parents=True, exist_ok=True)
        stamp = local_stamp()
        with ACTIONS_LOG.open("a", encoding="utf-8") as out:
            out.write(f"{stamp} {message}\n")

    # -- status ------------------------------------------------------------
    def status(self) -> dict:
        root = self.root()
        payload = {"episode": None, "operation": dict(self.state), "phases": PHASES}
        if root is None:
            return payload
        manifest = _read_json(root / "manifest.json") or {}
        paths = runner_paths(root)
        heartbeat = _read_json(paths["heartbeat"])
        runner_pid = running_runner_pid(paths["pid"])
        age = None
        if heartbeat:
            try:
                age = int(time.time() - datetime.fromisoformat(heartbeat["ts"]).timestamp())
            except (KeyError, ValueError):
                age = None
        summary = None
        if paths["journal"].is_file():
            for line in reversed(paths["journal"].read_text().splitlines()[-400:]):
                if '"event": "summary"' in line:
                    summary = json.loads(line)
                    break
        payload.update(episode={
            "root": str(root), "name": root.name,
            "game_address": f"127.0.0.1:{manifest.get('game_port', '?')}",
            "game": "running" if _pid_alive(root / "server/factorio.pid") else "stopped",
            "rcon": "listening" if manifest.get("rcon_port") and _listening(manifest["rcon_port"]) else "closed",
            "runner": "running" if runner_pid else "stopped", "runner_pid": runner_pid,
            "heartbeat": heartbeat, "heartbeat_age_s": age, "last_summary": summary,
        })
        return payload

    def latest(self) -> dict:
        root = self.root()
        return (_read_json(runner_paths(root)["status"]) or {}) if root else {}

    def decisions(self, limit: int = 40) -> list[dict]:
        root = self.root()
        path = runner_paths(root)["journal"] if root else None
        if not path or not path.is_file():
            return []
        rows: "OrderedDict[tuple, dict]" = OrderedDict()
        for line in path.read_text().splitlines()[-2000:]:
            record = json.loads(line)
            event = record.get("event")
            if event == "decision":
                key = (record["run_id"], record["index"])
                rows.pop(key, None)
                rows[key] = {k: record.get(k) for k in ("run_id", "index", "tick", "phase", "status", "summary",
                                                        "measured_per_s", "authority")} | {
                    "need": record["choice"]["need_id"], "p": record["choice"]["probability"]}
            elif event in ("failure", "phase_reached", "summary"):
                rows[(record.get("run_id"), event, len(rows))] = record
        return list(rows.values())[-limit:]

    def helper(self) -> dict:
        """Newest Laya Helper findings (docs/rl/laya_helper_runs/<run_id>/findings.md)."""
        reports = sorted(HELPER_REPORTS.glob("*/findings.md"), key=lambda p: p.stat().st_mtime)
        if not reports:
            return {"run_id": None, "path": None, "text": "No Laya Helper review yet; one starts after every run ends."}
        latest = reports[-1]
        text = latest.read_text(errors="replace")
        return {"run_id": latest.parent.name, "path": str(latest.relative_to(REPO_ROOT)), "text": text[-60000:],
                "in_progress": "in progress (started" in text}

    def ledger(self, limit: int = 20) -> list[dict]:
        """Newest rows of the shared run ledger (both systems, local times)."""
        if not LEDGER.is_file():
            return []
        rows = [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]
        return [{k: r.get(k) for k in ("system", "run_id", "started", "run_length", "commit", "highest_milestone",
                                       "outcome", "failure_cause", "limiting_factor", "log_file")} for r in rows[-limit:]][::-1]

    def read_log(self, name: str, offset: int) -> dict:
        root = self.root()
        files = {"actions": ACTIONS_LOG}
        if root:
            paths = runner_paths(root)
            files.update(runner=paths["log"], journal=paths["journal"],
                         server=root / "server/factorio-current.log",
                         stdout=root / "logs/laya-runner.stdout.log")
        path = files.get(name)
        if path is None:
            raise OperationError(f"Unknown log {name!r}.")
        if not path.is_file():
            return {"name": name, "text": "", "offset": 0, "size": 0}
        size = path.stat().st_size
        if offset > size or offset < 0:
            offset = 0
        start = max(offset, size - LOG_CHUNK) if offset == 0 else offset
        with path.open("rb") as handle:
            handle.seek(start)
            text = handle.read(min(LOG_CHUNK, size - start)).decode("utf-8", errors="replace")
        return {"name": name, "text": text, "offset": start + len(text.encode("utf-8")), "size": size,
                "truncated": start > 0 and offset == 0}

    # -- actions -----------------------------------------------------------
    def start(self, action: str, payload: dict) -> None:
        handlers = {"start_runner": self._start_runner, "stop_runner": self._stop_runner,
                    "fresh_episode": self._fresh_episode, "start_server": self._start_server,
                    "stop_server": self._stop_server, "sync_gui_mods": self._sync_gui_mods}
        if action not in handlers:
            raise OperationError(f"Unknown action {action!r}.")
        confirm = {"fresh_episode": "FRESH_EPISODE", "stop_server": "STOP_SERVER", "sync_gui_mods": "SYNC_GUI_MODS"}
        if action in confirm and payload.get("confirmation") != confirm[action]:
            raise OperationError(f"{action} requires confirmation.")
        if not self.lock.acquire(blocking=False):
            raise OperationError(f"Another action is running: {self.state['name']}.")
        self.state = {"name": action, "status": "running", "message": f"{action} started."}
        self._log(f"ACTION START {action}")

        def work() -> None:
            try:
                message = handlers[action]()
                self.state = {"name": action, "status": "done", "message": message}
                self._log(f"ACTION DONE {action}: {message}")
            except Exception as exc:  # Reported to the console, never raised into the server.
                self.state = {"name": action, "status": "failed", "message": str(exc)[:500]}
                self._log(f"ACTION FAILED {action}: {exc}")
            finally:
                self.lock.release()

        threading.Thread(target=work, daemon=True).start()

    def _require_root(self) -> Path:
        root = self.root()
        if root is None:
            raise OperationError("No Laya episode exists yet; start a fresh episode.")
        return root

    def _cli(self, *args: str, timeout: int = 600) -> str:
        result = subprocess.run([self.python, str(TOOLS / "run_laya_clean.py"), *args], cwd=REPO_ROOT,
                                capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise OperationError((result.stderr or result.stdout).strip()[-500:])
        return result.stdout.strip()

    def _start_runner(self) -> str:
        root = self._require_root()
        paths = runner_paths(root)
        if running_runner_pid(paths["pid"]):
            raise OperationError("The Laya runner is already running.")
        if not _pid_alive(root / "server/factorio.pid"):
            raise OperationError("Factorio is not running for this episode; start the server first.")
        if (_read_json(root / "manifest.json") or {}).get("persistent_server"):
            from tools.persistent_laya_run import launch
            return launch(root, self.python, self.speed)
        stdout = (root / "logs/laya-runner.stdout.log")
        stdout.parent.mkdir(parents=True, exist_ok=True)
        with stdout.open("a") as out:
            process = subprocess.Popen([self.python, str(TOOLS / "run_laya_clean.py"), "run", "--root", str(root),
                                        "--decisions", "0", "--speed", str(self.speed),
                                        "--seed", str(int(time.time()) % 100000)],
                                       cwd=REPO_ROOT, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        return f"Live Laya runner started (PID {process.pid}) on {root.name}."

    def _stop_runner(self, wait: float = 120) -> str:
        root = self._require_root()
        pid = running_runner_pid(runner_paths(root)["pid"])
        if not pid:
            return "The Laya runner was not running."
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if not running_runner_pid(runner_paths(root)["pid"]):
                return f"Laya runner {pid} stopped after its current decision."
            time.sleep(1)
        raise OperationError(f"Laya runner {pid} did not stop within {wait:g}s; it finishes its current decision first.")

    def _start_server(self) -> str:
        root = self._require_root()
        if _pid_alive(root / "server/factorio.pid"):
            return "Factorio is already running."
        manifest = _read_json(root / "manifest.json")
        if manifest.get("persistent_server"):
            from tools.persistent_laya_run import profile_for_world, service
            _, profile, _ = profile_for_world(root)
            service(profile, "start")
            return f"Persistent Factorio started for {root.name}."
        subprocess.run(["bash", str(root / "frozen/scripts/manage_linux_deterministic_server.sh"), "start",
                        "--root", str(root / "server"), "--fleet-mode", "--bootstrap-profile", "supplied-v1",
                        "--game-port", str(manifest["game_port"]), "--rcon-port", str(manifest["rcon_port"]),
                        "--source-save", str(root / "baseline.zip")], check=True, capture_output=True, text=True)
        return f"Factorio started for {root.name}."

    def _stop_server(self) -> str:
        root = self._require_root()
        runner = self._stop_runner()
        if not _pid_alive(root / "server/factorio.pid"):
            return runner + " Factorio was not running."
        if (_read_json(root / "manifest.json") or {}).get("persistent_server"):
            from tools.persistent_laya_run import profile_for_world, service
            _, profile, _ = profile_for_world(root)
            service(profile, "stop")
            return runner + " Persistent Factorio server stopped."
        return runner + " " + self._cli("stop", "--root", str(root))

    def _fresh_episode(self) -> str:
        current = self.root()
        if current is not None and (_read_json(current / "manifest.json") or {}).get("persistent_server"):
            from tools.persistent_laya_run import fresh_world
            self._stop_runner()
            backup = fresh_world(current)
            return f"Previous world archived at {backup}. " + self._start_runner()
        if current is not None:
            self._stop_runner()
            if _pid_alive(current / "server/factorio.pid"):
                self._cli("stop", "--root", str(current))
        root = WORLDS / ("clean-" + now_local().strftime("%Y%m%d-%H%M%S"))
        extra = ["--mods-source", str(current / "server/mods")] if current is not None else []
        self._cli("prepare", "--root", str(root), *extra)
        POINTER.write_text(json.dumps({"root": str(root)}) + "\n")
        return "Fresh episode prepared from saves/mod_playground.zip. " + self._start_runner()

    def _sync_gui_mods(self) -> str:
        root = self._require_root()
        backup = GUI_MODS.parent / ("mods-backup-" + now_local().strftime("%Y%m%d-%H%M%S"))
        backup.mkdir(parents=True)
        for name in PROJECT_MODS:
            source, target = root / "server/mods" / name, GUI_MODS / name
            if target.exists():
                shutil.copytree(target, backup / name)
                shutil.rmtree(target)
            shutil.copytree(source, target)
        return f"GUI mods now match {root.name}; previous copies in {backup}. Restart the Factorio client."


class Handler(BaseHTTPRequestHandler):
    ops: Operations
    token: str

    def _send(self, code: int, kind: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload) -> None:
        self._send(code, "application/json; charset=utf-8", json.dumps(payload).encode())

    def do_GET(self) -> None:  # noqa: N802
        request = urlsplit(self.path)
        query = parse_qs(request.query)
        if request.path in {"/", "/index.html"}:
            page = (TOOLS / "laya_ops.html").read_text(encoding="utf-8").replace("__ACTION_TOKEN__", self.token)
            self._send(200, "text/html; charset=utf-8", page.encode())
        elif request.path in STATIC:
            path, kind = STATIC[request.path]
            self._send(200, kind, path.read_bytes())
        elif request.path == "/api/status":
            self._json(200, self.ops.status())
        elif request.path == "/api/latest":
            self._json(200, self.ops.latest())
        elif request.path == "/api/ledger":
            self._json(200, self.ops.ledger())
        elif request.path == "/api/helper":
            self._json(200, self.ops.helper())
        elif request.path == "/api/decisions":
            self._json(200, self.ops.decisions(min(200, int(query.get("limit", ["40"])[0]))))
        elif request.path == "/api/logs":
            try:
                self._json(200, self.ops.read_log(query.get("name", ["runner"])[0], int(query.get("offset", ["0"])[0])))
            except (ValueError, OperationError) as exc:
                self._json(400, {"error": str(exc)})
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.headers.get("X-Action-Token") != self.token:
            self._json(403, {"error": "Invalid console action token."})
            return
        prefix = "/api/actions/"
        path = urlsplit(self.path).path
        if not path.startswith(prefix):
            self._json(404, {"error": "Unknown endpoint."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 4096:
                raise OperationError("Request body is too large.")
            payload = json.loads(self.rfile.read(length) or b"{}")
            action = path[len(prefix):]
            if action == "stop_console":
                if payload.get("confirmation") != "STOP_CONSOLE":
                    raise OperationError("Stopping the console requires confirmation.")
                self._json(202, {"accepted": True, "action": action})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self.ops.start(action, payload)
            self._json(202, {"accepted": True, "action": action})
        except (ValueError, OperationError) as exc:
            self._json(409, {"error": str(exc)})

    def log_message(self, format: str, *args) -> None:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the local Laya Operations Console.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--python-bin", type=Path, default=Path(sys.executable))
    parser.add_argument("--speed", type=float, default=1.0, help="Live game speed for runners started here")
    args = parser.parse_args()
    Handler.ops = Operations(args.python_bin, args.speed)
    Handler.token = secrets.token_urlsafe(24)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        print(f"Cannot bind port {args.port}: {exc}", file=sys.stderr)
        return 1
    print(f"Laya Operations Console: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
