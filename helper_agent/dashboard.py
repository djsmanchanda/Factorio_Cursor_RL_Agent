# Path: helper_agent/dashboard.py
# Purpose: Provide read-only runtime snapshots and feedback submission for the console.

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import jsonschema

from helper_agent import casebook, config, feedback as feedback_store


def _load_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def helper_view(data_root: Path) -> dict:
    directories = config.ensure_runtime(data_root)
    reports: list[dict] = []
    for path in directories["reports"].glob("*.json"):
        payload = _load_json(path)
        if payload is not None:
            reports.append(payload)
    reports.sort(key=lambda item: str(item.get("created_at", "")))
    latest = reports[-1] if reports else None
    latest_feedback = None
    if latest:
        latest_feedback = feedback_store.latest_feedback(data_root, str(latest.get("run_id", "")))
    skills = [
        {key: value for key, value in skill.items() if key != "path"}
        for skill in casebook.list_skills(directories["skills"].parent)
    ]
    return {
        "reports": reports[-10:],
        "latest": latest,
        "latest_feedback": latest_feedback,
        "review_status": (
            latest_feedback.get("verdict")
            if latest_feedback else "unreviewed"
        ),
        "skills": skills,
    }


def submit_feedback(data_root: Path, payload: Mapping[str, object]) -> dict:
    try:
        path, changed = feedback_store.record_feedback(data_root, payload)
    except (OSError, jsonschema.ValidationError) as error:
        raise ValueError(str(error)) from error
    return {"accepted": True, "feedback_path": str(path), "skills_changed": changed}
