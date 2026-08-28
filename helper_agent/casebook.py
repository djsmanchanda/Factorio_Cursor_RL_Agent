# Path: helper_agent/casebook.py
# Purpose: Maintain Helper Agent's inspectable incident and provisional-skill memory.

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping


_NOW = lambda: datetime.now().astimezone().isoformat(timespec="seconds")
_SLUG = re.compile(r"[^a-z0-9-]+")


def _safe_id(value: str) -> str:
    normalized = _SLUG.sub("-", value.strip().lower()).strip("-")
    return normalized or "unknown-skill"


def _parse_skill(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines or lines[0].strip() != "---":
        return {"path": path}
    metadata: dict[str, object] = {}
    current_list: list[str] | None = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("  - ") and current_list is not None:
            current_list.append(line[4:].strip())
        elif ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            if key in {"tags", "source_runs"}:
                current_list = metadata.setdefault(key, [])
                if not isinstance(current_list, list):
                    current_list = []
                    metadata[key] = current_list
            else:
                current_list = None
                value = value.strip()
                if key == "confidence":
                    try:
                        metadata[key] = float(value)
                    except ValueError:
                        metadata[key] = 0.0
                else:
                    metadata[key] = value
    metadata["path"] = path
    metadata.setdefault("tags", [])
    metadata.setdefault("source_runs", [])
    return metadata


def list_skills(casebook: Path) -> list[dict]:
    skills = casebook / "skills"
    if not skills.exists():
        return []
    return [_parse_skill(path) for path in sorted(skills.glob("*.md"))]


def _update_skill_frontmatter(path: Path, **updates: object) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    current_list: list[str] | None = None
    output: list[str] = []
    section = "frontmatter"
    for line in lines:
        if section == "frontmatter" and line.strip() == "---" and output:
            section = "body"
        if section == "frontmatter" and ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            if key in updates:
                value = updates.pop(key)
                if isinstance(value, list):
                    output.append(f"{key}:")
                    current_list = output
                    for item in value:
                        output.append(f"  - {item}")
                else:
                    output.append(f"{key}: {value}")
                    current_list = None
                continue
        elif section == "frontmatter" and line.startswith("  - ") and current_list is not None:
            continue
        output.append(line)
    for key, value in updates.items():
        if isinstance(value, list):
            output.append(f"{key}:")
            output.extend(f"  - {item}" for item in value)
        else:
            output.append(f"{key}: {value}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def upsert_skill(casebook: Path, moment: Mapping[str, object], packet: Mapping[str, object]) -> Path | None:
    """Create or strengthen a provisional diagnostic skill from observed evidence."""
    code = packet.get("target") or "run"
    raw_blockers = packet.get("blockers") or []
    if raw_blockers and isinstance(raw_blockers[-1], dict):
        code = raw_blockers[-1].get("code") or code
    title = str(moment.get("title", "run signature"))
    skill_id = _safe_id(f"blocker-{code}" if raw_blockers else title)
    path = casebook / "skills" / f"{skill_id}.md"
    existing = next((skill for skill in list_skills(casebook) if skill.get("id") == skill_id), None)
    source_runs = list(packet.get("run_id", [])) if isinstance(packet.get("run_id"), list) else [str(packet.get("run_id", "unknown"))]
    if existing:
        prior_runs = [str(item) for item in existing.get("source_runs", [])]
        source_runs = list(dict.fromkeys(prior_runs + source_runs))
        confidence = min(0.9, float(existing.get("confidence", 0.6)) + 0.05)
        _update_skill_frontmatter(
            Path(existing["path"]), source_runs=source_runs, confidence=round(confidence, 2),
        )
        return path
    tags = {skill_id, str(packet.get("target", "")), str(packet.get("terminal_class", ""))}
    if raw_blockers and isinstance(raw_blockers[-1], dict):
        tags.add(str(raw_blockers[-1].get("code", "")))
    path.write_text(
        "\n".join([
            "---",
            f"id: {skill_id}",
            "kind: diagnostic",
            "status: provisional",
            "confidence: 0.6",
            "source_runs:",
            *(f"  - {run_id}" for run_id in source_runs),
            "tags:",
            *(f"  - {tag}" for tag in sorted(tag for tag in tags if tag)),
            "---",
            "",
            f"# {title}",
            "",
            "## Signature",
            "",
            str(moment.get("what_happened", "No bounded signature recorded.")),
            "",
            "## Likely Cause",
            "",
            str(moment.get("cause", "Insufficient evidence.")),
            "",
            "## What To Observe Next",
            "",
            "Collect typed tick telemetry and the exact planner stage around the failure.",
            "",
            "## Rough Fix Direction",
            "",
            str(moment.get("fix_direction", "Use a focused observation or validator before code changes.")),
            "",
        ]),
        encoding="utf-8",
    )
    return path


def write_incident(casebook: Path, packet: Mapping[str, object], report: Mapping[str, object]) -> Path:
    safe_run_id = _safe_id(str(packet.get("run_id", "run")))
    path = casebook / "incidents" / f"{safe_run_id}.md"
    blockers = packet.get("blockers") or []
    lines = [
        f"# Incident: {packet.get('run_id', 'unknown')}",
        "",
        f"- Terminal outcome: {report.get('terminal_outcome', 'unknown')}",
        f"- Mission stage: {report.get('mission_stage', 'unknown')}",
        f"- Confidence: {report.get('confidence', 'low')}",
        f"- Packet: {report.get('packet_path', 'unknown')}",
        f"- Source log: {packet.get('source_log', 'unknown')}",
        "",
        "## Notable Moments",
        "",
    ]
    for moment in report.get("notable_moments", []):
        if isinstance(moment, Mapping):
            lines.append(f"- {moment.get('title', 'Untitled')}: {moment.get('what_happened', '')}")
    lines.extend([
        "",
        "## Typed Blockers",
        "",
    ])
    for blocker in blockers:
        if isinstance(blocker, Mapping):
            lines.append(
                f"- {blocker.get('code', 'unknown')} / {blocker.get('classification', 'bug')}: "
                f"{blocker.get('message', '')}"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def update_skills_from_feedback(
    casebook: Path, skill_actions: Iterable[Mapping[str, object]],
) -> list[str]:
    changed: list[str] = []
    status_map = {"promote": "confirmed", "demote": "provisional", "reject": "rejected"}
    confidence_map = {"promote": 0.9, "demote": 0.35, "reject": 0.1}
    for action in skill_actions:
        skill_id = str(action.get("skill_id", ""))
        skill = next((item for item in list_skills(casebook) if item.get("id") == skill_id), None)
        if skill is None:
            continue
        action_name = str(action.get("action", ""))
        _update_skill_frontmatter(
            Path(skill["path"]),
            status=status_map.get(action_name, skill.get("status", "provisional")),
            confidence=confidence_map.get(action_name, skill.get("confidence", 0.5)),
        )
        changed.append(skill_id)
    return changed
