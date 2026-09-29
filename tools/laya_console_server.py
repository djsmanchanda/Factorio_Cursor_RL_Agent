# Path: tools/laya_console_server.py
# Purpose: Serve the loopback-only Laya training operations console: status, results, logs, and start/stop controls.

from __future__ import annotations

import argparse
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import laya_console_runtime as runtime
from tools.laya_console_runtime import OperationError, StartRequest

TOOLS_DIR = REPO_ROOT / "tools"
DEFAULT_PORT = 9138
STATIC_FILES = {
    "/laya_console.css": (TOOLS_DIR / "laya_console.css", "text/css; charset=utf-8"),
    "/laya_console.js": (TOOLS_DIR / "laya_console.js", "text/javascript; charset=utf-8"),
}
NOT_STOPPED_EXCLUDE = {"workers.log"}


def _current_round_log(run_dir: Path) -> Path | None:
    if not run_dir.is_dir():
        return None
    candidates = [
        path for path in run_dir.glob("*.log")
        if not path.name.startswith("driver-") and path.name not in NOT_STOPPED_EXCLUDE
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def read_log(run_name: str, name: str, offset: int) -> dict:
    run_dir = runtime.OUT_DIR / run_name
    if name == "summary":
        path = run_dir / "summary.log"
    elif name == "round":
        path = _current_round_log(run_dir)
    else:
        raise OperationError(f"Unknown log: {name}")
    if path is None or not path.exists():
        return {"text": "", "offset": 0, "reset": offset > 0, "source": None}
    size = path.stat().st_size
    reset = offset < 0 or offset > size
    start = 0 if reset else offset
    with path.open("rb") as handle:
        handle.seek(start)
        data = handle.read(256_000)
        new_offset = handle.tell()
    return {"text": data.decode("utf-8", errors="replace"), "offset": new_offset, "reset": reset, "source": path.name}


class LayaConsoleHandler(BaseHTTPRequestHandler):
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
        query = parse_qs(request.query)
        run = query.get("run", [None])[0]

        if request.path in {"/", "/index.html"}:
            page = (TOOLS_DIR / "laya_console.html").read_text(encoding="utf-8")
            page = page.replace("__ACTION_TOKEN__", self.action_token)
            self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
            return
        static = STATIC_FILES.get(request.path)
        if static:
            path, content_type = static
            self._send(200, content_type, path.read_bytes())
            return
        if request.path == "/api/status":
            try:
                self._json(200, runtime.build_status(run))
            except OSError as exc:
                self._json(500, {"error": str(exc)})
            return
        if request.path == "/api/results":
            try:
                self._json(200, runtime.build_results(run))
            except OSError as exc:
                self._json(500, {"error": str(exc)})
            return
        if request.path == "/api/logs":
            name = query.get("name", ["summary"])[0]
            resolved_run, _ = runtime.resolve_run_name(run)
            try:
                offset = int(query.get("offset", ["0"])[0])
                self._json(200, read_log(resolved_run, name, offset))
            except (ValueError, OperationError) as exc:
                self._json(400, {"error": str(exc)})
            return
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.headers.get("X-Action-Token") != self.action_token:
            self._json(403, {"error": "Invalid console action token."})
            return
        request = urlsplit(self.path)
        prefix = "/api/actions/"
        if not request.path.startswith(prefix):
            self._json(404, {"error": "Unknown endpoint."})
            return
        action = request.path[len(prefix):]
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 8192:
                raise OperationError("Request body is too large.")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise OperationError("Request body must be an object.")
            if action == "start":
                if payload.get("confirmation") != "START_LAYA_RUN":
                    raise OperationError("Starting a Laya run requires confirmation.")
                request_obj = StartRequest(
                    hours=float(payload.get("hours", 6.0)),
                    instances=(payload.get("instances") or "").split() or None,
                    procs=int(payload.get("procs", 3)),
                    speed=float(payload.get("speed", 32.0)),
                    seeds_per_round=int(payload.get("seeds_per_round", runtime.DEFAULT_SEEDS_PER_ROUND)),
                    min_case_round=(int(payload["min_case_round"]) if payload.get("min_case_round") not in (None, "") else None),
                    finetune_first=bool(payload.get("finetune_first", False)),
                    run_name=str(payload.get("run_name") or "overnight"),
                    prefix=str(payload.get("prefix") or "ov"),
                    auto_resume=bool(payload.get("auto_resume", True)),
                    resume_round=(int(payload["resume_round"]) if payload.get("resume_round") not in (None, "") else None),
                    start_policy=(payload.get("start_policy") or None),
                    l1_seed=(int(payload["l1_seed"]) if payload.get("l1_seed") not in (None, "") else None),
                    l2_seed=(int(payload["l2_seed"]) if payload.get("l2_seed") not in (None, "") else None),
                )
                result = runtime.start_driver(request_obj)
                self._json(202, {"accepted": True, "action": action, "result": result})
                return
            if action == "stop_after_round":
                if payload.get("confirmation") != "STOP_LAYA_RUN_AFTER_ROUND":
                    raise OperationError("Requesting a graceful stop requires confirmation.")
                result = runtime.request_stop_after_round(payload.get("run_name") or None)
                self._json(202, {"accepted": True, "action": action, "result": result})
                return
            if action == "stop_now":
                if payload.get("confirmation") != "STOP_LAYA_RUN_NOW":
                    raise OperationError("Stopping the run immediately requires confirmation.")
                result = runtime.stop_now(payload.get("run_name") or None)
                self._json(202, {"accepted": True, "action": action, "result": result})
                return
            self._json(404, {"error": "Unknown endpoint."})
        except (json.JSONDecodeError, OperationError, ValueError, TypeError) as exc:
            self._json(409, {"error": str(exc)})

    def log_message(self, format: str, *args) -> None:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the local Laya training operations console.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    LayaConsoleHandler.action_token = secrets.token_urlsafe(24)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), LayaConsoleHandler)
    except OSError as exc:
        print(f"Cannot bind port {args.port}: {exc}\nPass --port to pick another.", file=sys.stderr)
        return 1
    print(f"Laya operations console: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
