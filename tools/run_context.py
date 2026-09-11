# Path: tools/run_context.py
# Purpose: Build a bounded offline evidence packet from the latest runner episode.

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_log_format import (
    is_helper_agent_line, is_run_end_line, is_run_start_line, parse_timed_run_log_line,
)

MAX_CONTEXT_CHARS = 12_000


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit - 22] + " … [excerpt truncated]"


def _inventory(path: Path | None, started_at: datetime | None, ended_after: float | None = None) -> str:
    if path is None:
        return "UNKNOWN: no inventory history supplied."
    source = _clip(str(path), 400)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != "1.0.0":
            return f"UNKNOWN: unsupported inventory schema ({source})."
        runs = payload.get("runs")
        if not isinstance(runs, list):
            return f"UNKNOWN: invalid inventory runs ({source})."
        matches = []
        for run in runs:
            if not isinstance(run, dict):
                continue
            try:
                timestamp = datetime.fromisoformat(run.get("started_at", ""))
            except (TypeError, ValueError):
                continue
            if started_at is not None and timestamp == started_at:
                matches.append(run)
        if len(matches) != 1:
            return f"UNKNOWN: inventory started_at mismatch or ambiguous match; no cross-run pairing ({source})."
        samples = matches[0].get("samples")
        if not isinstance(samples, list) or not samples:
            return f"UNKNOWN: no inventory samples for this run ({source})."
        excluded = 0
        if ended_after is not None:
            retained = []
            for sample in samples:
                elapsed = sample.get("elapsed_seconds") if isinstance(sample, dict) else None
                if type(elapsed) in (int, float) and 0 <= elapsed <= ended_after:
                    retained.append(sample)
                else:
                    excluded += 1
            samples = retained
            if not samples:
                return f"UNKNOWN: no in-run inventory samples; excluded {excluded} post-run/unknown-time samples ({source})."
        latest = samples[-1]
        if not isinstance(latest, dict) or type(latest.get("tick")) is not int:
            return f"UNKNOWN: invalid latest inventory tick ({source})."
        previous = next((sample for sample in reversed(samples[:-1])
                         if isinstance(sample, dict) and type(sample.get("tick")) is int
                         and sample["tick"] != latest["tick"]), None)
        if previous is None or previous["tick"] >= latest["tick"]:
            return f"UNKNOWN: need two increasing distinct inventory ticks; excluded {excluded} post-run/unknown-time samples ({source})."
        before, after = previous.get("items"), latest.get("items")
        if not isinstance(before, dict) or not isinstance(after, dict):
            return f"UNKNOWN: invalid inventory items ({source})."
        changes = []
        unknown = 0
        for item in before.keys() | after.keys():
            a, b = before.get(item), after.get(item)
            if (type(a) not in (int, float) or type(b) not in (int, float)
                    or not 0 <= a < float("inf") or not 0 <= b < float("inf")):
                unknown += 1
                continue
            changes.append((abs(b - a), str(item), a, b))
        changes.sort(key=lambda row: (-row[0], row[1]))
        rows = [f"{source}: run started_at={started_at.isoformat()}; ticks {previous['tick']}→{latest['tick']}",
                "NET STOCK CHANGE; not production rate or proof of transferable supply.",
                f"Excluded {excluded} post-run/unknown-time samples."]
        rows.extend(f"{_clip(item, 80)}: {a:g}→{b:g} ({b-a:+g})" for _, item, a, b in changes[:12])
        rows.append(f"{max(0, len(changes)-12)} items omitted; {unknown} missing/invalid item readings UNKNOWN (not zero).")
        return "\n".join(rows)
    except (OSError, ValueError, TypeError) as exc:
        return f"UNKNOWN: cannot read inventory history ({source}; {type(exc).__name__})."


def build_context(log_path: Path, inventory_path: Path | None = None) -> str:
    """Return excerpts, never diagnoses; retain exact source lines in the raw log."""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"RUN CONTEXT\nUNKNOWN: cannot read runner log ({type(exc).__name__}).\n"
    return _build_context_lines(lines, log_path, inventory_path)


def _build_context_lines(lines: list[str], log_path: Path, inventory_path: Path | None = None, *, first_line: int = 1) -> str:
    starts = [index for index, line in enumerate(lines)
              if is_run_start_line(line)
              and (header := parse_timed_run_log_line(line, run_started_at=None)) is not None
              and header.message.startswith("RUN START:")]
    if not starts:
        return "RUN CONTEXT\nUNKNOWN: no RUN START boundary; refusing to combine unscoped evidence.\n"
    start = starts[-1]
    rows = list(enumerate(lines[start:], start + first_line))
    parsed = parse_timed_run_log_line(rows[0][1], run_started_at=None)
    started_at = parsed.timestamp if parsed else None
    source = str(log_path)
    evidence_rows = [row for row in rows if "LOG COMPACTION:" not in row[1]
                     and not is_helper_agent_line(row[1])]

    def excerpt(row: tuple[int, str], limit: int) -> str:
        number, line = row
        return _clip(f"{source}:{number}: ", 450) + _clip(line, limit)

    def latest(pattern: str) -> tuple[int, str] | None:
        return next((row for row in reversed(evidence_rows) if re.search(pattern, row[1])), None)

    end_rows = [line for _, line in rows if is_run_end_line(line)
                and (line.strip() == "RUN END" or
                     ((timed_end := parse_timed_run_log_line(line, run_started_at=started_at))
                      is not None and timed_end.message == "RUN END"))]
    ended = bool(end_rows)
    ended_after = None
    for line in end_rows:
        if is_run_end_line(line):
            end = parse_timed_run_log_line(line, run_started_at=started_at)
            if end is not None and started_at is not None:
                ended_after = (end.timestamp - started_at).total_seconds()
            else:
                ended_after = -1  # Untimed end cannot establish an inventory cutoff.
            break
    sections = ["RUN CONTEXT — offline evidence; no live inspection",
                f"Status: {'completed (RUN END observed; not mission success)' if ended else 'partial (RUN END absent)'}",
                "Run header:\n" + excerpt(rows[0], 1000)]
    terminals = [latest(pattern) for pattern in (r"\bBLOCKER:", r"\bSTUCK:", r"(?:\w+Error|Exception):|^Traceback ")]
    terminal_rows = sorted({row for row in terminals if row is not None})
    sections.append("Last terminal evidence:\n" + ("\n".join(excerpt(row, 1000) for row in terminal_rows) or "UNKNOWN: no terminal failure observed."))
    verdict = latest(r"NO PROGRESS VERDICT:")
    sections.append("Last no-progress verdict:\n" + (excerpt(verdict, 2000) if verdict else "Not observed."))
    priority = latest(r"(?:^|\s)PRIORITY:")
    sections.append("Latest selected priority:\n" + (excerpt(priority, 450) if priority else "Not observed."))
    decisions = [row for row in evidence_rows if re.search(r"LOAN YIELD DECISION:|PRIORITY DEFERRED:|LAGGING BUILD:", row[1])]
    sections.append("Recent decision evidence (last 3):\n" + ("\n".join(excerpt(row, 500) for row in decisions[-3:]) or "Not observed."))
    milestones = [row for row in evidence_rows if re.search(r"BOOTSTRAP SWAP:|\bRESOLVED\b|\bREADY\b|\bVERIFIED\b|research.*completed", row[1], re.I)]
    sections.append(f"Milestone excerpts (last 5 of {len(milestones)}; claims require outcome verification):\n" + ("\n".join(excerpt(row, 220) for row in milestones[-5:]) or "Not observed."))
    categories: Counter[str] = Counter()
    for _, line in rows[1:]:
        timed = parse_timed_run_log_line(line, run_started_at=started_at)
        message = timed.message if timed else line
        message = re.sub(r"^LOG (?:REPEAT|UPDATE) x\d+:\s*", "", message).strip()
        match = re.match(r"([A-Z][A-Z /-]{1,50}):", message)
        if match:
            categories[match[1]] += 1
    sections.append("Repeated categories (printed lines only; compacted xN counters are not additive):\n" + ", ".join(f"{key}={count}" for key, count in categories.most_common(8)))
    compaction = next((row for row in reversed(rows) if "LOG COMPACTION:" in row[1]), None)
    if compaction:
        sections.append("Runner suppression summary:\n" + excerpt(compaction, 450))
    sections.append("Inventory:\n" + _clip(_inventory(inventory_path, started_at, ended_after), 1700))
    sections.append("Selection: newest RUN START only; bounded latest evidence, milestones and inventory items. Other lines omitted; long excerpts explicitly truncated. Raw sources remain unchanged. Absence from this packet is not evidence of absence; follow path:line references before diagnosis.")
    footer = "\n\n" + sections.pop() + "\n"
    return _clip("\n\n".join(sections), MAX_CONTEXT_CHARS - len(footer)) + footer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--inventory-history", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.resolve() in {args.log.resolve(), args.inventory_history.resolve() if args.inventory_history else None}:
        parser.error("output must not overwrite an input source")
    packet = build_context(args.log, args.inventory_history)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(packet, encoding="utf-8")
    else:
        print(packet, end="")


if __name__ == "__main__":
    main()
