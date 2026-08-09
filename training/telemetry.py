# Path: training/telemetry.py
# Purpose: Publish one atomic, low-cardinality live-state record per training worker.

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

_WORKER_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkerTelemetry:
    """Atomically replace a worker's latest state without retaining noisy samples."""

    def __init__(self, directory: Path | str, worker_id: str):
        if not _WORKER_ID.fullmatch(worker_id):
            raise ValueError("worker_id must be filesystem-safe")
        self.directory = Path(directory)
        self.worker_id = worker_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / f"{worker_id}.json"

    def publish(self, event: Mapping) -> dict:
        payload = {
            **dict(event), "version": "1.0.0", "worker_id": self.worker_id,
            "updated_utc": _now(),
        }
        encoded = json.dumps(payload, sort_keys=True, allow_nan=False, indent=2) + "\n"
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(self.path)
        return payload


def publish_best_effort(publisher, event: Mapping) -> bool:
    """Keep an observability filesystem failure from changing an experiment result."""
    if publisher is None:
        return False
    try:
        publisher.publish(event)
    except (OSError, TypeError, ValueError):
        return False
    return True

def read_live_workers(directory: Path | str) -> list[dict]:
    root = Path(directory)
    if not root.is_dir():
        return []
    workers = []
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("worker_id"):
            workers.append(payload)
    return workers
