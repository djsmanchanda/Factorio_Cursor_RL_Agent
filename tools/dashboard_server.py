# Path: tools/dashboard_server.py
# Purpose: Local web dashboard for the loop daemon: serves the viewer page and a JSON API over runs/loop_* status files and runs/expansion_* decision-step files.

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_HTML = REPO_ROOT / "tools" / "dashboard.html"

# Default HTTP port. Avoids 8765, which commonly collides with desktop apps.
DEFAULT_PORT = 9137


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


def _latest_expansion_dir(runs_dir: Path) -> Path | None:
    candidates = sorted(runs_dir.glob("expansion_*"), key=lambda p: p.name)
    return candidates[-1] if candidates else None


def build_expansion_state(runs_dir: Path) -> dict:
    """Newest runs/expansion_*/step_*.json trail: the RL/greedy decision
    layer's per-step trace (research rate, per-line diagnosis, candidate
    action catalog, chosen action, explanation, execution result). Written by
    the expansion daemon (out of scope here); this only reads it back."""
    expansion_dir = _latest_expansion_dir(runs_dir)
    if expansion_dir is None:
        return {"run": None, "steps": [], "latest": None, "message": "No expansion runs found yet."}

    steps = []
    for step_path in sorted(expansion_dir.glob("step_*.json")):
        try:
            with step_path.open("r", encoding="utf-8") as handle:
                steps.append(json.load(handle))
        except (json.JSONDecodeError, OSError):
            continue

    return {
        "run": expansion_dir.name,
        "steps": steps,
        "latest": steps[-1] if steps else None,
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
        elif self.path == "/api/expansion":
            body = json.dumps(build_expansion_state(self.runs_dir)).encode("utf-8")
            self._send(200, "application/json", body)
        else:
            self._send(404, "text/plain", b"not found")

    def log_message(self, format: str, *args) -> None:  # silence request spam
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the loop daemon dashboard.")
    # 8765 collides with common desktop apps; 9137 is the project default.
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--runs-dir", default=str(REPO_ROOT / "runs"))
    args = parser.parse_args()

    DashboardHandler.runs_dir = Path(args.runs_dir)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    except OSError as exc:
        print(f"Cannot bind port {args.port}: {exc}\nPass --port to pick another.", file=sys.stderr)
        return 1
    print(f"Dashboard at http://127.0.0.1:{args.port}/ (runs dir: {DashboardHandler.runs_dir})")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
