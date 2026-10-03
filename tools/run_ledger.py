#!/usr/bin/env python3
# Path: tools/run_ledger.py
# Purpose: One automatic, LLM-free ledger of every deterministic and Laya run: milestones, failure cause, log file and commit.

"""Run ledger shared by the deterministic runner and the clean Laya runner.

Each finished run appends one JSON line to ``run-ledger.jsonl`` and the CSV
view ``run-ledger.csv`` is regenerated from it.  Entries are keyed by
(system, run_id); recording a run twice replaces its row.

Milestones:
* Laya: phases measured by the runner (exact, from its decision journal).
* Deterministic: first appearance of marker lines in the run's own log
  (``milestone_source=log_markers_v1``); a marker means the runner reported
  that step, not that a C-predicate passed.

``python tools/run_ledger.py backfill`` indexes archived deterministic logs and
the Laya run index once; it is idempotent.
"""

from __future__ import annotations

import csv
import fcntl
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.run_log_format import parse_timed_run_log_line

LEDGER = Path(os.environ.get("FACTORIO_RL_RUN_LEDGER", Path.home() / ".local/share/factorio-rl/run-ledger.jsonl"))
CSV_COLUMNS = ("system", "run_id", "started", "ended", "duration_s", "commit", "target", "highest_milestone",
               "milestones", "outcome", "failure_code", "failure_cause", "limiting_factor", "decisions",
               "log_file", "root")
# Ordered deterministic markers: (milestone, pattern).  Order is the mission order.
DETERMINISTIC_MARKERS = (
    ("iron_starter", r"PLATE STARTER: iron-plate"),
    ("copper_starter", r"PLATE STARTER: copper-plate"),
    ("stone_starter", r"PLATE STARTER: stone-brick"),
    ("automation_science", r"GOAL MET: automation-science-pack"),
    ("iron_foundation", r"\[modular refinery for iron-plate\] RESOLVED"),
    ("copper_foundation", r"\[modular refinery for copper-plate\] RESOLVED"),
    ("stone_foundation", r"\[modular refinery for stone-brick\] RESOLVED"),
    ("logistic_science", r"GOAL MET: logistic-science-pack"),
    ("plastic_ladder", r"CHEMICAL LADDER: plastic-bar"),
    ("plastic", r"GOAL MET: plastic-bar|PRODUCTION ACCEPTANCE: target=plastic-bar result=pass"),
    ("advanced_circuit", r"GOAL MET: advanced-circuit"),
    ("chemical_science", r"GOAL MET: chemical-science-pack"),
    ("production_science", r"GOAL MET: production-science-pack"),
    ("research_queued", r"RESEARCH QUEUED"),
)


def _commit(code_dir: Path = REPO_ROOT) -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=code_dir, capture_output=True,
                              text=True, timeout=10).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _read(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def record(entry: dict, ledger: Path | None = None) -> dict:
    """Insert or replace one run (keyed by system and run_id) and regenerate the CSV."""
    ledger = ledger or LEDGER
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with (ledger.with_suffix(".lock")).open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = [r for r in _read(ledger) if (r["system"], r["run_id"]) != (entry["system"], entry["run_id"])]
        rows.append(entry)
        rows.sort(key=lambda r: r.get("started") or "")
        tmp = ledger.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
        tmp.replace(ledger)
        with ledger.with_suffix(".csv").open("w", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({**row, "milestones": ";".join(f"{m['id']}@{m.get('at_s', '')}s" for m in row["milestones"])})
    return entry


# -- deterministic ----------------------------------------------------------
def deterministic_runs(log: Path) -> list[list[str]]:
    """Split a runner log into runs, each starting at a RUN START line."""
    runs, current = [], None
    for line in log.read_text(errors="replace").splitlines():
        if "RUN START:" in line and (line.startswith("RUN START:") or " RUN START:" in line[:40]):
            current = [line]
            runs.append(current)
        elif current is not None:
            current.append(line)
    return runs


def deterministic_entry(lines: list[str], log: Path, *, run_id: str | None = None, commit: str | None = None) -> dict:
    start = parse_timed_run_log_line(lines[0], run_started_at=None)
    started = start.timestamp if start else None
    header = dict(re.findall(r"(\w+)=(\S+)", lines[0]))
    milestones, last_at, outcome, code, cause = [], 0, "unfinished", None, None
    for line in lines[1:]:
        parsed = parse_timed_run_log_line(line, run_started_at=started)
        message = parsed.message if parsed else line
        at = int((parsed.timestamp - started).total_seconds()) if parsed and started else None
        last_at = at if at is not None else last_at
        for milestone, pattern in DETERMINISTIC_MARKERS:
            if re.search(pattern, message) and milestone not in {m["id"] for m in milestones}:
                milestones.append({"id": milestone, "at_s": at, "evidence": message.strip()[:160]})
        if message.startswith("BLOCKER: "):
            try:
                blocker = json.loads(message[len("BLOCKER: "):])
                code, cause = blocker.get("code"), str(blocker.get("message", ""))[:300]
            except ValueError:
                pass
        elif message.startswith("STUCK: "):
            outcome, cause = "stuck", cause or message[len("STUCK: "):][:300]
        elif message.startswith("ERROR: "):
            outcome, cause = "error", message[len("ERROR: "):][:300]
        elif message.startswith(("GOAL MET", "RESEARCH QUEUED")) and outcome == "unfinished":
            outcome = "completed"
        elif message.strip() == "RUN END" and outcome == "unfinished":
            outcome = "ended"
    order = [m for m, _ in DETERMINISTIC_MARKERS]
    highest = max(milestones, key=lambda m: order.index(m["id"]))["id"] if milestones else None
    ended = (started + timedelta(seconds=last_at)).isoformat() if started else None
    return {"system": "deterministic", "run_id": run_id or f"det-{started.isoformat() if started else log.name}",
            "started": started.isoformat() if started else None, "ended": ended, "duration_s": last_at,
            "commit": commit or "unknown", "target": header.get("target"), "highest_milestone": highest,
            "milestones": milestones, "milestone_source": "log_markers_v1", "outcome": outcome,
            "failure_code": code, "failure_cause": cause, "limiting_factor": None, "decisions": None,
            "log_file": str(log), "root": None}


def record_deterministic_run(log: Path, *, run_id: str | None = None, commit: str | None = None) -> dict | None:
    """Record the newest run in a deterministic runner log (called after RUN END)."""
    runs = deterministic_runs(log)
    if not runs:
        return None
    return record(deterministic_entry(runs[-1], log, run_id=run_id or None, commit=commit or _commit()))


# -- laya -------------------------------------------------------------------
def laya_entry(index: dict) -> dict:
    root = Path(index["root"])
    rows = [r for r in _read(root / "evidence/clean-decisions.jsonl") if r.get("run_id") == index["run_id"]]
    started = datetime.fromisoformat(index["started"]) if index.get("started") else None
    milestones = []
    for row in rows:
        if row.get("event") == "phase_reached":
            milestones.append({"id": row["phase"], "tick": row.get("tick")})
    failures = [r for r in rows if r.get("event") == "failure"]
    done = [r for r in rows if r.get("event") == "decision" and r.get("status") not in ("pending",)]
    summary = index.get("summary") or {}
    stop = summary.get("stop")
    outcome = {"mission_complete": "completed", "stopped": "stopped", "decision_budget": "budget",
               "safe_stop_after_repeated_failures": "safe_stop", "runner_error": "error"}.get(stop, stop)
    last = done[-1] if done else {}
    bottleneck = " | ".join((last.get("board") or {}).get("bottleneck") or []) or None
    ended = index.get("ended")
    try:
        duration = int((datetime.fromisoformat(ended) - started).total_seconds()) if started and ended else None
    except (TypeError, ValueError):  # Backfilled rows may lack a timezone or an end time.
        duration = None
    return {"system": "laya", "run_id": index["run_id"], "started": index.get("started"), "ended": ended,
            "duration_s": duration, "commit": index.get("commit", "unknown"), "target": "mod-playground-mining-productivity-4",
            "highest_milestone": milestones[-1]["id"] if milestones else None, "milestones": milestones,
            "milestone_source": "measured_phases", "outcome": outcome,
            "failure_code": failures[-1].get("error_type") if failures and outcome == "safe_stop" else None,
            "failure_cause": (failures[-1].get("error") if failures and outcome == "safe_stop"
                              else f"run {outcome}; phase {last.get('phase')} unmet" if last else outcome),
            "limiting_factor": bottleneck, "decisions": len(done),
            "log_file": str(root / "logs/laya-runner.log"), "root": str(root)}


def record_laya_run(index: dict) -> dict:
    return record(laya_entry(index))


def backfill() -> int:
    """Index archived deterministic logs and the Laya run index (idempotent)."""
    count = 0
    logs = Path.home() / ".local/share/factorio-rl/deterministic/logs"
    for log in sorted(list((logs / "archive").glob("autonomous-run-*.log")) + [logs / "autonomous-run.log"]):
        if not log.is_file():
            continue
        for lines in deterministic_runs(log):
            entry = deterministic_entry(lines, log)
            if entry["started"]:
                record(entry)
                count += 1
    for index in _read(REPO_ROOT / "data/laya-saved-world/laya-runs.jsonl"):
        record_laya_run(index)
        count += 1
    return count


if __name__ == "__main__":
    if sys.argv[1:] == ["backfill"]:
        print(f"recorded {backfill()} runs into {LEDGER} and {LEDGER.with_suffix('.csv')}")
    else:
        print(__doc__)
