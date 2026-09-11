# Path: tools/run_evidence.py
# Purpose: Retrieve bounded log and plan evidence in one offline command.

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.run_log_format import is_run_start_line, parse_timed_run_log_line


def lookup(log: Path, queries: list[str], artifacts: list[Path], position=None) -> str:
    """Literal log matches and structured action coordinates, never a diagnosis."""
    rows = []
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    start = next((i for i in range(len(lines) - 1, -1, -1) if is_run_start_line(lines[i]) and
                  (parsed := parse_timed_run_log_line(lines[i], run_started_at=None)) is not None
                  and parsed.message.startswith("RUN START:")), None)
    if start is None:
        return "UNKNOWN: log has no run boundary.\n"
    rows.append(f"Run: {lines[start][:800]}")
    matches = []
    needles = [q.casefold() for q in queries if q]
    for i in range(start, len(lines)):
        if any(q in lines[i].casefold() for q in needles):
            matches.append(i)
    # Preserve first cause and recent outcome rather than a tail alone.
    chosen = list(dict.fromkeys(matches[:6] + matches[-6:]))
    rows.append(f"Log matches: {len(matches)}; showing first/last with one context line.")
    emitted = set()
    for i in chosen:
        for j in range(max(start, i - 1), min(len(lines), i + 2)):
            if j not in emitted:
                line = lines[j]
                rows.append(f"{log}:{j+1}: {line[:650]}" + (" [truncated]" if len(line) > 650 else ""))
                emitted.add(j)
    rows.append("Artifacts below are explicitly supplied: verify their episode against this run; no automatic cross-run attribution.")
    for artifact in artifacts[:12]:
        try:
            data = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            rows.append(f"{artifact}: UNKNOWN ({type(error).__name__})")
            continue
        found = []

        def visit(value, pointer=""):
            if isinstance(value, dict):
                pos = value.get("position")
                coord = (isinstance(pos, dict) and position is not None
                         and pos.get("x") == position[0] and pos.get("y") == position[1])
                text = any(q in str(value.get(k, "")).casefold() for q in needles
                           for k in ("entity", "recipe", "name", "action_type", "error"))
                if coord or text:
                    fields = {k: value[k] for k in ("action_type", "entity", "recipe", "name", "position", "direction", "error") if k in value}
                    found.append(f"{artifact}#{pointer}: {json.dumps(fields, sort_keys=True)[:600]}")
                for k, v in value.items():
                    visit(v, pointer + "/" + str(k).replace("~", "~0").replace("/", "~1"))
            elif isinstance(value, list):
                for i, v in enumerate(value):
                    visit(v, pointer + "/" + str(i))
        visit(data)
        rows.extend(found[:12])
        rows.append(f"{artifact}: {len(found)} matching objects; {max(0, len(found)-12)} omitted.")
    if len(artifacts) > 12:
        rows.append(f"{len(artifacts)-12} artifact paths omitted (limit 12).")
    result = "\n".join(rows)
    return result[:11800] + ("\n[packet truncated; narrow the query]" if len(result) > 11800 else "") + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--artifact", action="append", type=Path, default=[])
    parser.add_argument("--position", nargs=2, type=float)
    args = parser.parse_args()
    if not args.query and args.position is None:
        parser.error("provide --query or --position")
    queries = args.query or [str(n) for n in args.position]
    print(lookup(args.log, queries, args.artifact, args.position), end="")


if __name__ == "__main__":
    main()
