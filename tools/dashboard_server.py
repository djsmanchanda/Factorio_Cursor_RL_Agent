# Path: tools/dashboard_server.py
# Purpose: Local web dashboard for the loop daemon: serves the viewer page and a JSON API over runs/loop_* status files.

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_HTML = REPO_ROOT / "tools" / "dashboard.html"


def _latest_loop_dir(runs_dir: Path) -> Path | None:
    candidates = sorted(runs_dir.glob("loop_*"), key=lambda p: p.name)
    return candidates[-1] if candidates else None


def build_state(runs_dir: Path) -> dict:
    loop_dir = _latest_loop_dir(runs_dir)
    if loop_dir is None:
        return {"loop": None, "iterations": [], "message": "No loop runs found yet."}

    iterations = []
    for status_path in sorted(loop_dir.glob("iter_*/status.json")):
        try:
            with status_path.open("r", encoding="utf-8") as handle:
                iterations.append(json.load(handle))
        except (json.JSONDecodeError, OSError):
            continue

    return {
        "loop": loop_dir.name,
        "iterations": iterations,
        "latest": iterations[-1] if iterations else None,
    }


class DashboardHandler(BaseHTTPRequestHandler):
    runs_dir: Path = REPO_ROOT / "runs"

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path in {"/", "/index.html"}:
            self._send(200, "text/html; charset=utf-8", DASHBOARD_HTML.read_bytes())
        elif self.path == "/api/state":
            body = json.dumps(build_state(self.runs_dir)).encode("utf-8")
            self._send(200, "application/json", body)
        else:
            self._send(404, "text/plain", b"not found")

    def log_message(self, format: str, *args) -> None:  # silence request spam
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the loop daemon dashboard.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--runs-dir", default=str(REPO_ROOT / "runs"))
    args = parser.parse_args()

    DashboardHandler.runs_dir = Path(args.runs_dir)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    print(f"Dashboard at http://127.0.0.1:{args.port}/ (runs dir: {DashboardHandler.runs_dir})")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
