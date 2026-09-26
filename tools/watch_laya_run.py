#!/usr/bin/env python3
# Path: tools/watch_laya_run.py
# Purpose: Follow Laya run journals live and print a readable decision-by-decision feed.

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]


def follow(path: Path, *, poll: float, stop_at_end: bool) -> Iterator[dict[str, Any]]:
    """Yield journal events as they are appended; stop after termination/cleanup."""
    with path.open(encoding="utf-8") as handle:
        buffer = ""
        while True:
            chunk = handle.readline()
            if not chunk:
                if stop_at_end:
                    return
                time.sleep(poll)
                continue
            buffer += chunk
            if not buffer.endswith("\n"):
                continue
            event = json.loads(buffer)
            buffer = ""
            yield event
            if event.get("type") == "cleanup":
                return


def _rate(observation: dict[str, Any]) -> str:
    metrics = observation.get("metrics") or {}
    objective = observation.get("objective") or {}
    return (
        f"rate {float(metrics.get('rate_per_tick') or 0):.4f}/{float(objective.get('target_rate_per_tick') or 0):.4f}"
        f" sustained {metrics.get('sustained_ticks', 0)}/{objective.get('sustain_ticks', 0)}"
        f" delivered {metrics.get('delivered_items', 0)}"
    )


def render(event: dict[str, Any], pending: dict[str, Any]) -> str | None:
    kind = event.get("type")
    if kind == "run_started":
        authority = (event.get("authority") or {}).get("kind", "?")
        return f"== {event['run_id']}  {event['scenario_id']}  authority={authority}  model={event['model']['model_id']}"
    if kind == "observer":
        return f"   observer {event.get('status')}: {event.get('result') or event.get('error')}"
    if kind == "decision":
        pending[event["decision_id"]] = [option["id"] if "id" in option else option.get("option_id") for option in event.get("options", [])]
        return None
    if kind == "decision_result":
        answer = next(iter((event.get("response") or {}).get("answers", {}).values()), {})
        probabilities = answer.get("probabilities") or {}
        options = pending.pop(event["decision_id"], list(probabilities))
        selection = event.get("selection") or {}
        source = selection.get("source", "model")
        lines = [f"-> {event['decision_kind']:<16} tick {event.get('tick')}  chose {event['selected_option_id']}  [{source}]"]
        for option in options:
            marker = "*" if option == event["selected_option_id"] else " "
            model = " (model pick)" if option == selection.get("model_choice") and source != "model" else ""
            lines.append(f"     {marker} {probabilities.get(option, 0.0):6.3f}  {option}{model}")
        return "\n".join(lines)
    if kind == "tool_result":
        execution = event.get("execution") or {}
        placed = f"  placed {execution.get('succeeded_placements')} failed {execution.get('failed_placements')}" if execution else ""
        return f"   {event['kind']:<16}{placed}  {_rate(event.get('observation') or {})}"
    if kind == "failure_packet":
        return f"   FAILURE {event.get('cause')}/{event.get('failure_kind')}  {event.get('path')}"
    if kind == "termination":
        return f"== terminated: {event['status']}  {event.get('reason') or ''}  decisions={event.get('decisions')}"
    if kind == "cleanup":
        return f"   cleanup {event.get('status')}"
    return None


def newest_journal(directory: Path, after: float) -> Path | None:
    journals = [path for path in directory.glob("*.jsonl") if path.stat().st_mtime > after]
    return max(journals, key=lambda path: path.stat().st_mtime) if journals else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", nargs="?", type=Path, help="Journal to follow; omit to follow each new run")
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "data" / "laya-exploration" / "runs")
    parser.add_argument("--poll", type=float, default=0.5)
    parser.add_argument("--once", action="store_true", help="Print an existing journal and exit")
    args = parser.parse_args(argv)
    if args.journal:
        pending: dict[str, Any] = {}
        for event in follow(args.journal, poll=args.poll, stop_at_end=args.once):
            line = render(event, pending)
            if line:
                print(line, flush=True)
        return 0
    # Follow mode: wait for each journal created after start, one run at a time.
    seen = time.time()
    print(f"waiting for new runs in {args.runs_dir} (Ctrl+C to stop)", flush=True)
    try:
        while True:
            journal = newest_journal(args.runs_dir, seen)
            if journal is None:
                time.sleep(args.poll)
                continue
            seen = journal.stat().st_mtime
            pending = {}
            for event in follow(journal, poll=args.poll, stop_at_end=False):
                line = render(event, pending)
                if line:
                    print(line, flush=True)
            seen = max(seen, journal.stat().st_mtime)
            print(flush=True)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
