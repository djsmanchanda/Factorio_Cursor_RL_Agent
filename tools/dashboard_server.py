# Path: tools/dashboard_server.py
# Purpose: Serve the loopback-only Factorio operations dashboard, live logs, and fixed control actions.

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.dashboard_runtime import DashboardConfig, OperationError, OperationManager

TOOLS_DIR = REPO_ROOT / "tools"
DEFAULT_PORT = 9137
STATIC_FILES = {
    "/dashboard.css": (TOOLS_DIR / "dashboard.css", "text/css; charset=utf-8"),
    "/dashboard.js": (TOOLS_DIR / "dashboard.js", "text/javascript; charset=utf-8"),
}


def _latest_dir(runs_dir: Path, prefix: str) -> Path | None:
    candidates = sorted(runs_dir.glob(f"{prefix}_*"), key=lambda path: path.name)
    return candidates[-1] if candidates else None


def _json_files(directory: Path | None, pattern: str) -> list[dict]:
    if directory is None:
        return []
    results = []
    for path in sorted(directory.glob(pattern)):
        try:
            results.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return results


def build_state(runs_dir: Path) -> dict:
    loop_dir = _latest_dir(runs_dir, "loop")
    iterations = _json_files(loop_dir, "iter_*/status.json")
    return {
        "loop": loop_dir.name if loop_dir else None,
        "iterations": iterations,
        "latest": iterations[-1] if iterations else None,
    }


def build_expansion_state(runs_dir: Path) -> dict:
    expansion_dir = _latest_dir(runs_dir, "expansion")
    steps = _json_files(expansion_dir, "step_*.json")
    return {
        "run": expansion_dir.name if expansion_dir else None,
        "steps": steps,
        "latest": steps[-1] if steps else None,
    }


def _rcon_password(secret_file: Path | None, password: str) -> str:
    if secret_file is None:
        return password
    try:
        value = secret_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"RCON secret file is unavailable: {secret_file}") from exc
    if not value:
        raise ValueError(f"RCON secret file is empty: {secret_file}")
    return value


class DashboardHandler(BaseHTTPRequestHandler):
    runs_dir = REPO_ROOT / "runs"
    manager: OperationManager
    action_token: str

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        request = urlsplit(self.path)
        if request.path in {"/", "/index.html"}:
            page = (TOOLS_DIR / "dashboard.html").read_text(encoding="utf-8")
            page = page.replace("__ACTION_TOKEN__", self.action_token)
            self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
            return
        static = STATIC_FILES.get(request.path)
        if static:
            path, content_type = static
            self._send(200, content_type, path.read_bytes())
            return
        if request.path == "/api/status":
            self._json(200, self.manager.status())
            return
        if request.path == "/api/priorities":
            try:
                self._json(200, self.manager.priorities())
            except OperationError as exc:
                self._json(500, {"error": str(exc)})
            return
        if request.path == "/api/research":
            try:
                self._json(200, self.manager.research_queue())
            except OperationError as exc:
                self._json(500, {"error": str(exc)})
            return
        if request.path == "/api/research/options":
            try:
                self._json(200, self.manager.research_options())
            except OperationError as exc:
                self._json(500, {"error": str(exc)})
            return
        if request.path == "/api/logs":
            query = parse_qs(request.query)
            name = query.get("name", ["runner"])[0]
            try:
                offset = int(query.get("offset", ["0"])[0])
                self._json(200, self.manager.read_log(name, offset))
            except (ValueError, OperationError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if request.path == "/api/logs/last-run":
            try:
                self._json(200, self.manager.last_runner_run())
            except OperationError as exc:
                self._json(404, {"error": str(exc)})
            return
        if request.path == "/api/helper":
            try:
                self._json(200, self.manager.helper_agent())
            except (OSError, ValueError) as exc:
                self._json(500, {"error": str(exc)})
            return
        if request.path == "/api/state":
            self._json(200, build_state(self.runs_dir))
            return
        if request.path == "/api/expansion":
            self._json(200, build_expansion_state(self.runs_dir))
            return
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.headers.get("X-Action-Token") != self.action_token:
            self._json(403, {"error": "Invalid dashboard action token."})
            return
        request = urlsplit(self.path)
        prefix = "/api/actions/"
        if not request.path.startswith(prefix):
            if request.path == "/api/research":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length > 4096:
                        raise OperationError("Request body is too large.")
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(payload, dict):
                        raise OperationError("Request body must be an object.")
                    technologies = payload.get("technologies")
                    mode = payload.get("mode", "replace")
                    if not isinstance(technologies, list):
                        raise OperationError("technologies must be an array")
                    self.manager.queue_research(technologies, mode=mode)
                    self._json(202, {"accepted": True, "mode": mode, "technologies": technologies})
                except (json.JSONDecodeError, OperationError) as exc:
                    self._json(409, {"error": str(exc)})
                return
            self._json(404, {"error": "Unknown endpoint."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 4096:
                raise OperationError("Request body is too large.")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise OperationError("Request body must be an object.")
            action = request.path[len(prefix):]
            if action == "stop_console":
                if payload.get("confirmation") != "STOP_OPERATIONS_CONSOLE":
                    raise OperationError("Stopping the operations console requires confirmation.")
                self._json(202, {"accepted": True, "action": action})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if action == "helper_feedback":
                self._json(202, self.manager.submit_helper_feedback(payload))
                return
            self.manager.start(action, str(payload.get("confirmation", "")))
            self._json(202, {"accepted": True, "action": action})
        except (json.JSONDecodeError, OperationError, ValueError) as exc:
            self._json(409, {"error": str(exc)})

    def log_message(self, format: str, *args) -> None:
        pass


def main() -> int:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    parser = argparse.ArgumentParser(description="Serve the local Factorio operations dashboard.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--runs-dir", type=Path, default=REPO_ROOT / "runs")
    parser.add_argument("--server-data", type=Path, default=local_app_data / "Factorio-server")
    parser.add_argument("--source-save", type=Path, default=roaming / "Factorio" / "saves" / "mod_playground.zip")
    parser.add_argument("--rcon-password", default="planner_test")
    parser.add_argument("--rcon-secret-file", type=Path, help="Local RCON secret file; overrides --rcon-password.")
    parser.add_argument(
        "--server-manager", type=Path,
        help="Native executable lifecycle manager for deploy/reset/start/stop actions.",
    )
    parser.add_argument(
        "--runner-manager", type=Path,
        help="Native executable lifecycle manager for deterministic runner actions.",
    )
    parser.add_argument(
        "--campaign-manager", type=Path,
        help="Native executable campaign manager for atomic fresh episodes.",
    )
    parser.add_argument(
        "--runtime-root", type=Path,
        default=Path.home() / ".local/share/factorio-rl/runtime/factorio-2.1.17",
    )
    parser.add_argument("--python-bin", type=Path, default=sys.executable)
    parser.add_argument(
        "--gui-mods", type=Path, default=Path.home() / ".factorio" / "mods",
        help="Linux GUI Factorio mods directory synchronized by native deploy.",
    )
    parser.add_argument(
        "--helper-root", type=Path,
        default=Path.home() / ".local/share/factorio-rl/helper_agent",
        help="Helper Agent mutable data root.",
    )
    parser.add_argument("--technology", default="mining-productivity-4")
    args = parser.parse_args()

    try:
        rcon_password = _rcon_password(args.rcon_secret_file, args.rcon_password)
    except ValueError as exc:
        parser.error(str(exc))

    config = DashboardConfig(
        server_data=args.server_data,
        source_save=args.source_save,
        rcon_password=rcon_password,
        technology=args.technology,
        server_manager=args.server_manager,
        runner_manager=args.runner_manager,
        campaign_manager=args.campaign_manager,
        runtime_root=args.runtime_root,
        python_bin=args.python_bin,
        gui_mods=args.gui_mods,
        helper_root=args.helper_root,
    )
    DashboardHandler.runs_dir = args.runs_dir
    DashboardHandler.manager = OperationManager(config)
    DashboardHandler.action_token = secrets.token_urlsafe(24)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    except OSError as exc:
        print(f"Cannot bind port {args.port}: {exc}\nPass --port to pick another.", file=sys.stderr)
        return 1
    print(f"Factorio operations dashboard: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
