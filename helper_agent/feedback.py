# Path: helper_agent/feedback.py
# Purpose: Append user feedback without rewriting original model reports.

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Mapping

import jsonschema

from helper_agent import casebook, config

_REVIEW_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "Helper_Agent" / "schemas" / "review_report.schema.json").read_text("utf-8"),
)
_FEEDBACK_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "Helper_Agent" / "schemas" / "feedback.schema.json").read_text("utf-8"),
)
_NOW = lambda: datetime.now().astimezone().isoformat(timespec="seconds")
_SLUG = re.compile(r"[^A-Za-z0-9_.-]+")


def record_feedback(data_root: Path, payload: Mapping[str, object]) -> tuple[Path, list[str]]:
    directories = config.ensure_runtime(data_root)
    feedback = dict(payload)
    feedback.setdefault("created_at", _NOW())
    jsonschema.validate(feedback, _FEEDBACK_SCHEMA)
    run_id = str(feedback["run_id"])
    safe_run_id = _SLUG.sub("_", run_id)
    report_path = directories["reports"] / f"{safe_run_id}.json"
    if not report_path.is_file():
        raise FileNotFoundError(f"No report exists for run {run_id}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    jsonschema.validate(report, _REVIEW_SCHEMA)

    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")
    serialized = json.dumps(feedback, indent=2, sort_keys=True) + "\n"
    for collision in range(1000):
        suffix = f"-{collision}" if collision else ""
        path = directories["feedback"] / f"{timestamp}{suffix}-{safe_run_id}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(serialized)
            break
        except FileExistsError:
            continue
    else:
        raise OSError(f"Could not allocate an append-only feedback path for {run_id}")
    changed_skills = casebook.update_skills_from_feedback(
        directories["skills"].parent, feedback.get("skill_actions", []),
    )
    incident_path = directories["incidents"] / f"{safe_run_id}.md"
    if incident_path.is_file():
        lines = [
            "",
            "## User Feedback",
            "",
            f"- Created at: {feedback['created_at']}",
            f"- Verdict: {feedback['verdict']}",
            f"- Comment: {feedback.get('comment', '')}",
            f"- Skills changed: {', '.join(changed_skills) or 'none'}",
        ]
        with incident_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return path, changed_skills


def latest_feedback(data_root: Path, run_id: str) -> dict | None:
    directories = config.runtime_directories(data_root)
    candidates = []
    for path in directories["feedback"].glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("run_id") == run_id:
            candidates.append(payload)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item.get("created_at", ""))[-1]
