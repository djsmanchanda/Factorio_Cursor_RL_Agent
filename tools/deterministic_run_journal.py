# Path: tools/deterministic_run_journal.py
# Purpose: Summarize and compare the latest deterministic autonomous runs.

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.run_log_format import parse_timed_run_log_line

_RUN_START = "RUN START:"
_FIELD = re.compile(
    r"\b(?P<name>command|target|surface|force|bootstrap_profile)=(?P<value>\S+)"
)
_PRIORITY = re.compile(r"PRIORITY: (?P<item>\S+).*completion=(?P<percent>\d+)%")
_PLATE_SOURCE = re.compile(r"PLATE SOURCE: recorded (?P<item>\S+) provider")
_GOAL = re.compile(r"GOAL MET: (?P<item>\S+)")
_MALL_READY = re.compile(r"MALL READY: (?P<item>\S+) stock reached")
_RESEARCH_QUEUED = re.compile(r"RESEARCH QUEUED: (?P<item>\S+)")


@dataclass(frozen=True)
class Run:
    run_id: str
    started: datetime
    ended: datetime
    fields: Mapping[str, str]
    messages: tuple[str, ...]

    @property
    def duration_seconds(self) -> int:
        return max(0, round((self.ended - self.started).total_seconds()))


@dataclass(frozen=True)
class Summary:
    run: Run
    result: str
    terminal: str
    milestones: tuple[str, ...]
    score: int
    priority: str


def collect_runs(log_path: Path, archive_dir: Path) -> list[Run]:
    """Read all available run blocks and return deduplicated chronological runs."""
    paths = sorted(archive_dir.glob("autonomous-run*.log")) if archive_dir.exists() else []
    if log_path.exists():
        paths.append(log_path)
    found: dict[str, Run] = {}
    for path in paths:
        for run in _parse_runs(path.read_text(encoding="utf-8", errors="replace")):
            previous = found.get(run.run_id)
            if previous is None or len(run.messages) > len(previous.messages):
                found[run.run_id] = run
    return sorted(found.values(), key=lambda run: run.started)


def _parse_runs(text: str) -> list[Run]:
    parsed: list[tuple[datetime, str]] = []
    run_started_at: datetime | None = None
    for line in text.splitlines():
        timed = parse_timed_run_log_line(line, run_started_at=run_started_at)
        if timed is None:
            continue
        if _RUN_START in timed.message:
            run_started_at = timed.timestamp
        parsed.append((timed.timestamp, timed.message))

    runs: list[Run] = []
    current: list[tuple[datetime, str]] = []
    for timestamp, message in parsed:
        if _RUN_START in message:
            if current:
                runs.append(_make_run(current))
            current = [(timestamp, message)]
        elif current:
            current.append((timestamp, message))
    if current:
        runs.append(_make_run(current))
    return runs


def _make_run(lines: list[tuple[datetime, str]]) -> Run:
    started, header = lines[0]
    fields = {match.group("name"): match.group("value") for match in _FIELD.finditer(header)}
    run_id = started.isoformat()
    return Run(
        run_id=run_id,
        started=started,
        ended=lines[-1][0],
        fields=fields,
        messages=tuple(message for _, message in lines),
    )


def summarize(run: Run) -> Summary:
    milestones: list[str] = []
    score = 0
    priorities: dict[str, int] = {}
    terminal = ""
    result = "running"

    for message in run.messages:
        if match := _PLATE_SOURCE.search(message):
            _append_unique(milestones, f"plate:{match.group('item')}")
        if match := _MALL_READY.search(message):
            _append_unique(milestones, f"mall:{match.group('item')}")
        if match := _GOAL.search(message):
            _append_unique(milestones, f"goal:{match.group('item')}")
        if match := _RESEARCH_QUEUED.search(message):
            _append_unique(milestones, f"research:{match.group('item')}")
        if "RESEARCH QUEUE COMPLETE" in message:
            _append_unique(milestones, "research-queue:complete")
        if match := _PRIORITY.search(message):
            item = match.group("item")
            priorities[item] = max(priorities.get(item, 0), int(match.group("percent")))
        if message.startswith("STUCK:"):
            terminal = message.removeprefix("STUCK:").strip()
            result = "stuck"
        elif message.startswith("ERROR:"):
            terminal = message.removeprefix("ERROR:").strip()
            result = "error"
        elif message.startswith("RESEARCH QUEUED:") or "RESEARCH QUEUE COMPLETE" in message:
            result = "success"
        elif message == "RUN END" and result == "running":
            result = "ended"

    for milestone in milestones:
        if milestone.startswith("plate:"):
            score += 10
        elif milestone.startswith("mall:"):
            score += 2
        elif milestone.startswith("goal:"):
            score += 20
        elif milestone.startswith("research:"):
            score += 100
        elif milestone == "research-queue:complete":
            score += 120
    priority = max(priorities.items(), key=lambda pair: pair[1], default=("none", 0))
    return Summary(
        run=run,
        result=result,
        terminal=_clip(terminal, 150),
        milestones=tuple(milestones),
        score=score,
        priority=f"{priority[0]} {priority[1]}%" if priority[0] != "none" else "none",
    )


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def trend(current: Summary, previous: Summary | None) -> tuple[str, str]:
    """Return a verdict and evidence relative to the previous matching target."""
    if previous is None:
        return "baseline", "first retained run for this target"
    gained = [item for item in current.milestones if item not in previous.milestones]
    lost = [item for item in previous.milestones if item not in current.milestones]
    if current.result == "success" and previous.result != "success":
        return "better", "mission reached success"
    if gained and not lost:
        return "better", "new: " + ", ".join(gained[-3:])
    if lost and not gained:
        return "worse", "lost: " + ", ".join(lost[-3:])
    if current.score > previous.score:
        return "better", f"evidence score {previous.score} -> {current.score}"
    if current.score < previous.score:
        return "worse", f"evidence score {previous.score} -> {current.score}"
    if current.result == previous.result and _failure_key(current) == _failure_key(previous):
        return "flat", "same result and terminal failure class"
    return "mixed", f"result {previous.result} -> {current.result}; score unchanged"


def _failure_key(summary: Summary) -> str:
    return re.sub(r"[-+]?\d+(?:\.\d+)?", "#", summary.terminal.lower())[:100]


def render(
    summaries: Iterable[Summary], notes: Mapping[str, Mapping[str, str]], *, limit: int
) -> str:
    retained = list(summaries)[-limit:]
    previous_by_target: dict[str, Summary] = {}
    rows: list[str] = []
    for summary in retained:
        target = summary.run.fields.get("target", "unknown")
        verdict, evidence = trend(summary, previous_by_target.get(target))
        previous_by_target[target] = summary
        note = notes.get(summary.run.run_id, {})
        milestones = ", ".join(summary.milestones[-3:]) or f"priority:{summary.priority}"
        wrong = summary.terminal or ("run has not ended" if summary.result == "running" else "none recorded")
        rows.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (
                    summary.run.started.strftime("%m-%d %H:%M"),
                    target,
                    _duration(summary.run.duration_seconds),
                    summary.result,
                    milestones,
                    note.get("change", "unrecorded"),
                    evidence,
                    wrong,
                    note.get("lesson", verdict),
                )
            )
            + " |"
        )

    return "\n".join(
        [
            "# Deterministic campaign — latest runs",
            "",
            f"Generated from live/archive evidence. Retaining {len(retained)} of at most {limit} runs.",
            "",
            "| Start | Target | Duration | Result | Furthest evidence | Change tested | Improved | Went wrong | Assessment |",
            "|---|---|---:|---|---|---|---|---|---|",
            *rows,
            "",
            "Assessment is evidence-derived unless a manual lesson was recorded. "
            "A new session must read this file before restarting or editing.",
            "",
        ]
    )


def _duration(seconds: int) -> str:
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}m {remainder:02d}s"


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _read_notes(path: Path) -> dict[str, dict[str, str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("journal state must contain a JSON object")
    return {
        str(run_id): {str(key): str(value) for key, value in note.items()}
        for run_id, note in raw.items()
        if isinstance(note, dict)
    }


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--archive-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--change", help="Short description of the change tested by the latest run.")
    parser.add_argument("--lesson", help="Short human assessment for the latest run.")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 10:
        parser.error("--limit must be between 1 and 10")

    runs = collect_runs(args.log, args.archive_dir)
    notes = _read_notes(args.state)
    state_changed = not args.state.exists()
    if runs and (args.change or args.lesson):
        latest = notes.setdefault(runs[-1].run_id, {})
        if args.change:
            latest["change"] = args.change
        if args.lesson:
            latest["lesson"] = args.lesson
        state_changed = True
    if state_changed:
        _atomic_write(args.state, json.dumps(notes, indent=2, sort_keys=True) + "\n")
    summaries = [summarize(run) for run in runs]
    _atomic_write(args.output, render(summaries, notes, limit=args.limit))
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
