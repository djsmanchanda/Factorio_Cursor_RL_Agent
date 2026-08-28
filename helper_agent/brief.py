# Path: helper_agent/brief.py
# Purpose: Generate a focused advisory edit brief from existing Helper Agent evidence.

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from helper_agent import config, feedback as feedback_store

_TEMPLATE = (
    Path(__file__).resolve().parents[1] / "Helper_Agent" / "templates" / "next_edit_brief.md"
).read_text("utf-8")


def generate_brief(
    data_root: Path,
    run_id: str,
    *,
    target: str | None = None,
    category: str = "validator",
) -> tuple[Path, str]:
    directories = config.ensure_runtime(data_root)
    report_path = directories["reports"] / f"{run_id}.json"
    if not report_path.is_file():
        raise FileNotFoundError(f"No Helper Agent report exists for run {run_id}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    packet_path = Path(str(report.get("packet_path", "")))
    feedback_path = feedback_store.latest_feedback(data_root, run_id)
    skill_names = list(report.get("relevant_casebook_skills", []))
    moments = []
    for moment in report.get("notable_moments", []):
        if not isinstance(moment, Mapping):
            continue
        moments.extend([
            f"- What happened: {moment.get('what_happened', '')}",
            f"- What was expected: {moment.get('what_was_expected', '')}",
            f"- Suspected cause: {moment.get('cause', '')}",
            f"- Rough fix direction: {moment.get('fix_direction', '')}",
        ])
    evidence = [
        f"- Report: {report_path}",
        f"- Packet: {packet_path if packet_path.exists() else packet_path}",
        f"- Source log: {json.loads(packet_path.read_text('utf-8')).get('source_log', 'unknown') if packet_path.exists() else 'packet unavailable'}",
    ]
    if feedback_path:
        evidence.append(f"- Latest feedback verdict: {feedback_path.get('verdict', 'unknown')}")
    evidence.extend(f"- Related skill: {name}" for name in skill_names)
    replacements = {
        "target": target or str(report.get("mission_stage", "selected run")),
        "notable_moments": "\n".join(moments),
        "evidence": "\n".join(evidence),
        "category": category,
    }
    text = _TEMPLATE
    for key, value in replacements.items():
        text = text.replace("{{" + key + "}}", value)
    output = directories["reports"] / "next-edit-brief.md"
    output.write_text(text, encoding="utf-8")
    return output, text
