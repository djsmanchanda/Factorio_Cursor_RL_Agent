# Path: training/laya_run_log.py
# Purpose: Human runner log and heartbeat for the clean Laya runner, in the deterministic runner's compact format.

"""Runner log in the deterministic format (see tools/run_log_format.py).

``RUN START: ts=<iso> key=value...`` once, then ``+<seconds>s message`` lines,
then ``RUN END``.  A heartbeat sidecar is overwritten every few seconds so the
console can tell a live runner from a hung one; liveness never enters the log.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class RunLog:
    def __init__(self, path: Path, heartbeat: Path, heartbeat_seconds: float = 10.0) -> None:
        self.path = path
        self.heartbeat = heartbeat
        self.interval = heartbeat_seconds
        self.started = time.monotonic()
        self.fields: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, line: str) -> None:
        with self._lock, self.path.open("a", encoding="utf-8") as out:
            out.write(line + "\n")

    def start(self, **fields: Any) -> None:
        self.started = time.monotonic()
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        self._write("RUN START: ts=" + stamp + "".join(f" {k}={v}" for k, v in fields.items()))
        self._thread = threading.Thread(target=self._beat, daemon=True)
        self._thread.start()

    def __call__(self, message: str) -> None:
        elapsed = int(time.monotonic() - self.started)
        for line in str(message).splitlines() or [""]:
            self._write(f"+{elapsed}s {line}")

    def update(self, **fields: Any) -> None:
        """Fields shown in the heartbeat (decision index, phase, tick)."""
        self.fields.update(fields)

    def _beat(self) -> None:
        while not self._stop.is_set():
            payload = {"pid": os.getpid(), "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                       "elapsed_s": int(time.monotonic() - self.started), **self.fields}
            tmp = self.heartbeat.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            tmp.replace(self.heartbeat)
            self._stop.wait(self.interval)

    def end(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self("RUN END")
