# Path: tools/inventory_history.py
# Purpose: Persist per-run logistic-inventory snapshots for the Operations Console chart.

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.run_log_format import parse_timed_run_log_line


SCHEMA_VERSION = "1.0.0"
MAX_RETAINED_RUNS = 5


class InventoryHistory:
    """Keep raw inventory observations partitioned by deterministic runner run."""

    def __init__(self, path: Path, runner_log: Path) -> None:
        self.path = path
        self.runner_log = runner_log
        self._lock = threading.Lock()

    def record(self, report: dict[str, Any]) -> dict[str, Any]:
        """Append one new game-tick observation to the active run, if any."""
        run = self._current_run()
        if run is None:
            return self.view()
        items = report.get("total_items")
        tick = report.get("tick")
        if not isinstance(items, dict) or not isinstance(tick, int):
            return self.view()
        counts = {
            str(name): count for name, count in items.items()
            if isinstance(name, str) and isinstance(count, (int, float)) and count >= 0
        }
        captured_at = datetime.now(timezone.utc)
        elapsed_seconds = max(0, round((captured_at - run["started_at"]).total_seconds()))
        sample = {
            "tick": tick,
            "elapsed_seconds": elapsed_seconds,
            "captured_at": captured_at.isoformat(timespec="seconds"),
            "items": counts,
        }
        with self._lock:
            payload = self._load()
            runs = payload["runs"]
            active = next((entry for entry in runs if entry["id"] == run["id"]), None)
            if active is None:
                active = {
                    "id": run["id"],
                    "started_at": run["started_at"].isoformat(),
                    "label": run["label"],
                    "samples": [],
                }
                runs.append(active)
            samples = active["samples"]
            if not samples or samples[-1].get("tick") != tick:
                samples.append(sample)
                payload["runs"] = runs[-MAX_RETAINED_RUNS:]
                self._save(payload)
            return payload

    def view(self) -> dict[str, Any]:
        with self._lock:
            return self._load()

    def _current_run(self) -> dict[str, Any] | None:
        try:
            lines = self.runner_log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None
        run_started_at: datetime | None = None
        latest: tuple[datetime, str] | None = None
        for line in lines:
            timed = parse_timed_run_log_line(line, run_started_at=run_started_at)
            if timed is None:
                continue
            if "RUN START:" in timed.message:
                run_started_at = timed.timestamp
                latest = (timed.timestamp, timed.message)
        if latest is None:
            return None
        started_at, header = latest
        return {
            "id": started_at.isoformat(),
            "started_at": started_at,
            "label": header.removeprefix("RUN START:").strip(),
        }

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": SCHEMA_VERSION, "runs": []}
        if not isinstance(payload, dict) or not isinstance(payload.get("runs"), list):
            return {"schema_version": SCHEMA_VERSION, "runs": []}
        runs = [run for run in payload["runs"] if self._valid_run(run)]
        return {"schema_version": SCHEMA_VERSION, "runs": runs[-MAX_RETAINED_RUNS:]}

    @staticmethod
    def _valid_run(run: Any) -> bool:
        return (
            isinstance(run, dict)
            and isinstance(run.get("id"), str)
            and isinstance(run.get("started_at"), str)
            and isinstance(run.get("label"), str)
            and isinstance(run.get("samples"), list)
        )

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, self.path)
