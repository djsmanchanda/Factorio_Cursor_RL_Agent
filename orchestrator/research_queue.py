# Path: orchestrator/research_queue.py
# Purpose: Persist and validate the deterministic runner's ordered research queue.

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"
MAX_QUEUE_ITEMS = 32
TECHNOLOGY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


class ResearchQueueError(ValueError):
    """Raised when a persisted research queue is malformed or unsafe."""


def _technology(value: Any) -> str:
    if not isinstance(value, str) or not TECHNOLOGY_PATTERN.fullmatch(value):
        raise ResearchQueueError(f"invalid technology name: {value!r}")
    return value


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_queue(technologies: list[str], *, surface: str = "nauvis", force: str = "player") -> dict:
    if not isinstance(surface, str) or not surface or not isinstance(force, str) or not force:
        raise ResearchQueueError("surface and force must be non-empty strings")
    if not isinstance(technologies, list) or not technologies:
        raise ResearchQueueError("research queue must contain at least one technology")
    if len(technologies) > MAX_QUEUE_ITEMS:
        raise ResearchQueueError(f"research queue cannot exceed {MAX_QUEUE_ITEMS} items")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in technologies:
        technology = _technology(value)
        if technology in seen:
            raise ResearchQueueError(f"research queue contains a duplicate: {technology}")
        seen.add(technology)
        items.append({"technology": technology, "status": "pending"})
    return {
        "schema_version": SCHEMA_VERSION,
        "surface": surface,
        "force": force,
        "items": items,
        "updated_at": _timestamp(),
        "last_error": None,
    }


def validate_queue(payload: Any) -> dict:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ResearchQueueError("research queue schema_version is invalid")
    allowed = {"schema_version", "surface", "force", "items", "updated_at", "last_error"}
    unknown = set(payload) - allowed
    if unknown:
        raise ResearchQueueError(f"research queue has unknown fields: {sorted(unknown)}")
    surface, force = payload.get("surface"), payload.get("force")
    if not isinstance(surface, str) or not surface or not isinstance(force, str) or not force:
        raise ResearchQueueError("research queue surface and force are required")
    updated_at = payload.get("updated_at")
    if not isinstance(updated_at, str) or not updated_at:
        raise ResearchQueueError("research queue updated_at is required")
    last_error = payload.get("last_error")
    if last_error is not None and not isinstance(last_error, str):
        raise ResearchQueueError("research queue last_error must be a string or null")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or len(raw_items) > MAX_QUEUE_ITEMS:
        raise ResearchQueueError("research queue items are invalid")
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ResearchQueueError("research queue item must be an object")
        if set(raw) - {"technology", "status", "error"}:
            raise ResearchQueueError("research queue item has unknown fields")
        technology = _technology(raw.get("technology"))
        status = raw.get("status")
        if status not in {"pending", "running", "completed", "failed"}:
            raise ResearchQueueError(f"invalid research queue status: {status!r}")
        if technology in seen:
            raise ResearchQueueError(f"research queue contains a duplicate: {technology}")
        seen.add(technology)
        item = {"technology": technology, "status": status}
        if raw.get("error") is not None:
            if not isinstance(raw["error"], str):
                raise ResearchQueueError("research queue error must be a string")
            item["error"] = raw["error"]
        items.append(item)
    return {
        "schema_version": SCHEMA_VERSION,
        "surface": surface,
        "force": force,
        "items": items,
        "updated_at": updated_at,
        "last_error": last_error,
    }


def load_queue(path: Path) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ResearchQueueError(f"cannot read research queue {path}: {error}") from error
    return validate_queue(payload)


def write_queue(path: Path, payload: dict) -> dict:
    validated = validate_queue(payload)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(validated, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    return validated


def update_item(path: Path, technology: str, status: str, *, error: str | None = None) -> dict:
    payload = load_queue(path)
    found = False
    for item in payload["items"]:
        if item["technology"] == technology:
            item["status"] = status
            item.pop("error", None)
            if error:
                item["error"] = error
            found = True
            break
    if not found:
        raise ResearchQueueError(f"technology is not present in research queue: {technology}")
    payload["last_error"] = error
    payload["updated_at"] = _timestamp()
    return write_queue(path, payload)


def merge_queue(path: Path, technologies: list[str], *, mode: str) -> dict:
    if mode not in {"replace", "append"}:
        raise ResearchQueueError("research queue mode must be replace or append")
    if mode == "replace" or not Path(path).exists():
        return write_queue(path, new_queue(technologies))
    current = load_queue(path)
    if not isinstance(technologies, list) or not technologies:
        raise ResearchQueueError("research queue must contain at least one technology")
    if len(current["items"]) + len(technologies) > MAX_QUEUE_ITEMS:
        raise ResearchQueueError(f"research queue cannot exceed {MAX_QUEUE_ITEMS} items")
    existing = {item["technology"] for item in current["items"]}
    additions: list[dict[str, Any]] = []
    for value in technologies:
        technology = _technology(value)
        if technology in existing:
            raise ResearchQueueError(f"research queue contains a duplicate: {technology}")
        existing.add(technology)
        additions.append({"technology": technology, "status": "pending"})
    current["items"].extend(additions)
    current["updated_at"] = _timestamp()
    current["last_error"] = None
    return write_queue(path, current)
