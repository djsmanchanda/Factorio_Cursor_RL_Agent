# Path: tools/campaign_loop_log.py
# Purpose: Durable campaign loop records, fix ledger and hypothesis ledger.
"""Append-only campaign history under `<state-root>/logs/campaign-loop/`.

Conversation provides reasoning continuity; these files are the workflow truth:
- `loop.jsonl`: one record per completed cycle (never rewritten).
- `fixes.json`: one entry per fix attempt with review/verification results.
- `hypotheses.json`: durable hypothesis ledger keyed by hypothesis_id.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.reliability_state import atomic_json


def loop_root(state_root: Path) -> Path:
    return Path(state_root) / "logs" / "campaign-loop"


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def append_cycle(state_root: Path, record: dict) -> None:
    """Append one completed-cycle record; prior records are never modified."""
    path = loop_root(state_root) / "loop.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def cycles(state_root: Path) -> list[dict]:
    path = loop_root(state_root) / "loop.jsonl"
    records = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return records


def cycle_record(state_root: Path, campaign_id: str, cycle_id: int) -> dict | None:
    for record in cycles(state_root):
        if record.get("campaign_id") == campaign_id and record.get("cycle_id") == cycle_id:
            return record
    return None


def record_fix(state_root: Path, fix: dict) -> dict:
    """Insert or update one fix entry keyed by fix_id."""
    path = loop_root(state_root) / "fixes.json"
    ledger = _read_json(path)
    fixes = ledger.setdefault("fixes", {})
    existing = fixes.get(fix["fix_id"], {})
    existing.update(fix)
    fixes[fix["fix_id"]] = existing
    atomic_json(path, ledger)
    return existing


def fixes(state_root: Path) -> dict[str, dict]:
    return _read_json(loop_root(state_root) / "fixes.json").get("fixes", {})


def record_hypothesis(state_root: Path, hypothesis: dict) -> dict:
    """Insert or update one hypothesis entry keyed by hypothesis_id."""
    path = loop_root(state_root) / "hypotheses.json"
    ledger = _read_json(path)
    hypotheses = ledger.setdefault("hypotheses", {})
    existing = hypotheses.get(hypothesis["hypothesis_id"], {})
    for key, value in hypothesis.items():
        if key in {"supporting_runs", "contradicting_runs", "related_fixes"}:
            merged = list(existing.get(key, []))
            if value not in merged:
                merged.append(value)
            existing[key] = merged
        else:
            existing[key] = value
    hypotheses[hypothesis["hypothesis_id"]] = existing
    atomic_json(path, ledger)
    return existing


def hypotheses(state_root: Path) -> dict[str, dict]:
    return _read_json(loop_root(state_root) / "hypotheses.json").get("hypotheses", {})


def previous_fix(state_root: Path, before_cycle: int) -> dict | None:
    """Most recent fix introduced in an earlier cycle, newest first."""
    candidates = [
        fix for fix in fixes(state_root).values()
        if isinstance(fix.get("introduced_cycle"), int) and fix["introduced_cycle"] < before_cycle
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda fix: fix["introduced_cycle"])[-1]


def next_fix_id(state_root: Path) -> str:
    return f"FIX-{len(fixes(state_root)) + 1:04d}"


def next_hypothesis_id(state_root: Path) -> str:
    return f"H-{len(hypotheses(state_root)) + 1:03d}"
