# Path: training/laya_receipts.py | Purpose: One cached reader for the runner's transaction receipts, parsing each file once per change.

"""Transaction receipts, read once.

Each ``evidence/transaction-*.json`` embeds the full before/after facts (megabytes),
yet the runner's helpers need only the candidate, status and submission.  They used
to re-parse every file every decision: live 2026-11-08 kit-B-s1 had 100 receipts,
182 MB, ~1 s per pass and about six passes per decision, so the time between
decisions grew with every build.  Here each file is parsed once per (mtime, size)
and only the slim fields are kept; the files stay the evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

_CACHE: dict[Path, tuple[tuple[int, int], dict]] = {}


def _slim(receipt: dict) -> dict:
    submission = receipt.get("submission") or {}
    return {"status": receipt.get("status"), "reason": receipt.get("reason"),
            "candidate": receipt.get("candidate") or {},
            "submission": {"ok": submission.get("ok"), "error": submission.get("error")} if submission else {},
            "before_tick": (receipt.get("before") or {}).get("tick")}


def receipts(root: Path | str | None) -> list[tuple[Path, dict]]:
    """(path, slim receipt) for every readable receipt, oldest first (by mtime, as the old readers sorted)."""
    if root is None:
        return []
    rows = []
    for path in Path(root, "evidence").glob("transaction-*.json"):
        try:
            stat = path.stat()
        except OSError:
            continue
        key = (stat.st_mtime_ns, stat.st_size)
        cached = _CACHE.get(path)
        if cached is None or cached[0] != key:
            try:
                cached = (key, _slim(json.loads(path.read_text())))
            except (OSError, ValueError):
                continue  # Mid-write or unreadable: skipped, as before; read again once it changes.
            _CACHE[path] = cached
        rows.append((stat.st_mtime_ns, path, cached[1]))
    return [(path, slim) for _, path, slim in sorted(rows, key=lambda r: (r[0], r[1].name))]
