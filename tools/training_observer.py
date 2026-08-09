# Path: tools/training_observer.py
# Purpose: Serve a loopback training dashboard and one bounded spectator-view control.

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from training.observation import build_training_snapshot
from training.observer_control import ObserverControlError, TrainingSurfaceViewer
from training.store import TrainingStore

_ASSETS = Path(__file__).resolve().parent
_ASSET_MAP = {
    "/": ("training_observer.html", "text/html; charset=utf-8"),
    "/observer.js": ("training_observer.js", "text/javascript; charset=utf-8"),
    "/observer.css": ("training_observer.css", "text/css; charset=utf-8"),
}
_MAX_VIEW_REQUEST_BYTES = 512


def _log(level: str, message: str, request_id: str, **fields) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(), "level": level,
        "service": "training-observer", "trace_id": request_id,
        "span_id": request_id[:16], "request_id": request_id,
        "message": message, **fields,
    }
    print(json.dumps(payload, sort_keys=True), flush=True)


def _view_state(viewer: TrainingSurfaceViewer | None, reason: str | None) -> dict[str, object]:
    worker_ids = sorted(viewer.workers) if viewer is not None else []
    return {"enabled": viewer is not None, "reason": reason, "worker_ids": worker_ids}


def _handler(
    database: Path, live_directory: Path, viewer: TrainingSurfaceViewer | None = None,
    view_reason: str | None = None,
):
    class ObserverHandler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: dict[str, object]) -> None:
            self._send(status, json.dumps(payload, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8")

        def _view_request(self) -> tuple[str, str]:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > _MAX_VIEW_REQUEST_BYTES:
                raise ValueError("invalid view request length")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("view request must be JSON")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or set(payload) != {"worker_id", "episode_id"}:
                raise ValueError("view request shape is invalid")
            worker_id, episode_id = payload["worker_id"], payload["episode_id"]
            if not isinstance(worker_id, str) or not isinstance(episode_id, str):
                raise ValueError("view request values are invalid")
            return worker_id, episode_id

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            request_id, status = uuid.uuid4().hex, 500
            try:
                if self.path == "/api/snapshot":
                    snapshot = build_training_snapshot(database, live_directory)
                    snapshot["view_control"] = _view_state(viewer, view_reason)
                    self._send_json(200, snapshot)
                    status = 200
                elif self.path in _ASSET_MAP:
                    name, content_type = _ASSET_MAP[self.path]
                    self._send(200, (_ASSETS / name).read_bytes(), content_type)
                    status = 200
                else:
                    self._send_json(404, {"error": "not found"})
                    status = 404
                _log("INFO", "observer.request", request_id, path=self.path, status=status)
            except Exception as exc:
                _log("ERROR", "observer.request_failed", request_id, path=self.path, error=str(exc))
                if status == 500:
                    self._send_json(500, {"error": "snapshot failed"})

        def do_POST(self) -> None:  # noqa: N802 - controlled local observer action
            request_id, status = uuid.uuid4().hex, 500
            try:
                if self.path != "/api/view":
                    self._send_json(405, {"error": "dashboard is read-only"})
                    status = 405
                elif viewer is None:
                    self._send_json(503, {"error": "training view control is unavailable"})
                    status = 503
                else:
                    worker_id, episode_id = self._view_request()
                    result = viewer.focus(worker_id, episode_id)
                    self._send_json(200, {"ok": True, **result})
                    status = 200
                _log("INFO", "observer.view_request", request_id, path=self.path, status=status)
            except ValueError:
                self._send_json(400, {"error": "invalid training view request"})
                _log("WARNING", "observer.view_rejected", request_id, path=self.path, status=400)
            except ObserverControlError as exc:
                self._send_json(409, {"error": str(exc)})
                _log("WARNING", "observer.view_failed", request_id, path=self.path, status=409)
            except Exception as exc:
                _log("ERROR", "observer.view_failed", request_id, path=self.path, error=str(exc))
                self._send_json(502, {"error": "training view request failed"})

        def log_message(self, _format: str, *_args) -> None:
            return

    return ObserverHandler

def _load_viewer(args: argparse.Namespace) -> tuple[TrainingSurfaceViewer | None, str | None]:
    try:
        return TrainingSurfaceViewer.from_files(
            args.workers, args.rcon_secret_file, args.observer_player,
        ), None
    except (OSError, ValueError):
        return None, "Unavailable until the configured isolated training worker is ready."


def _serve(args: argparse.Namespace) -> int:
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("training observer must bind to loopback")
    viewer, view_reason = _load_viewer(args)
    server = ThreadingHTTPServer(
        (args.host, args.port), _handler(args.database, args.live_directory, viewer, view_reason),
    )
    request_id = uuid.uuid4().hex
    _log("INFO", "observer.started", request_id, url=f"http://{args.host}:{args.port}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        _log("INFO", "observer.stopped", request_id)
    finally:
        server.server_close()
    return 0


def _nudge(args: argparse.Namespace) -> int:
    guidance_id = f"guidance-{uuid.uuid4().hex}"
    with TrainingStore(args.database) as store:
        store.save_guidance(
            guidance_id, args.focus, args.message, args.expires_generation,
        )
    print(json.dumps({"guidance_id": guidance_id, "status": "active"}, indent=2))
    return 0


def _dismiss(args: argparse.Namespace) -> int:
    with TrainingStore(args.database) as store:
        store.dismiss_guidance(args.guidance_id)
    print(json.dumps({"guidance_id": args.guidance_id, "status": "dismissed"}, indent=2))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Observe and guide isolated RL training.")
    parser.add_argument("--database", type=Path, default=Path("data/training/experience.db"))
    parser.add_argument("--live-directory", type=Path, default=Path("data/training/live"))
    parser.add_argument("--workers", type=Path, default=REPO_ROOT / "training-workers-wsl.json")
    parser.add_argument(
        "--rcon-secret-file", type=Path,
        default=Path(os.environ.get("LOCALAPPDATA", ".")) / "Factorio-training-wsl-01" / "rcon-password",
    )
    parser.add_argument("--observer-player", default="main")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Serve the local training observatory")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    commands.add_parser("snapshot", help="Print one JSON snapshot")
    nudge = commands.add_parser("nudge", help="Record bounded guidance for autoresearch")
    nudge.add_argument("--focus", choices=(
        "throughput", "reliability", "efficiency", "exploration", "general",
    ), default="general")
    nudge.add_argument("--message", required=True)
    nudge.add_argument("--expires-generation", type=int)
    dismiss = commands.add_parser("dismiss", help="Deactivate one guidance record")
    dismiss.add_argument("guidance_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve":
        return _serve(args)
    if args.command == "nudge":
        return _nudge(args)
    if args.command == "dismiss":
        return _dismiss(args)
    print(json.dumps(
        build_training_snapshot(args.database, args.live_directory), indent=2, allow_nan=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
