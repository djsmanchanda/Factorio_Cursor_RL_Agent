#!/usr/bin/env python3
# Path: tools/laya_cohort_performance.py | Purpose: Tabulate every cohort's per-arm strict milestones from local job records.
"""Write a per-arm performance table for every cohort under data/laya-experiments.

Counts are strict milestones ever reached (``accepted_ever``) among completed jobs; the
denominator is every planned job of the arm, so unstarted, cancelled and infrastructure
failures stay visible. C6 before the c6-v2 predicate (b1ae062) is the chest-satisfiable
c6-v1 latch and is labelled as such. Revisions come from the frozen spec or the arm's
detached worktree; a condensed cohort without either reports ``?``.
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import re
import statistics
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
C6_V2 = "b1ae062"
MILESTONES = ("C2", "C3", "C4", "C6")


def _literal(value):
    # Older batch ledgers store lists and dicts as their Python repr.
    if isinstance(value, str):
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value
    return value


def _json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout.strip()


def _arms(cohort: Path) -> dict[str, str | None]:
    revisions: dict[str, str | None] = {}
    spec = _json(cohort / "regression.json") or {}
    for arm, entry in (spec.get("arms") or {}).items():
        revisions[arm] = entry.get("revision")
    for name in ("batch.json", "freeze.json"):
        arms = _literal((_json(cohort / name) or {}).get("arms"))
        entries = arms.items() if isinstance(arms, dict) else ((a.get("id"), a) for a in arms or () if isinstance(a, dict))
        for arm, entry in entries:
            if arm and not revisions.get(arm) and isinstance(entry, dict):
                revisions[arm] = entry.get("revision")
    for code in sorted(cohort.glob("arms/*/code")):
        arm = code.parent.name
        if not revisions.get(arm) and (code / ".git").exists():  # condensed copies are not worktrees
            revisions[arm] = _git("rev-parse", "HEAD", cwd=code) or None
    return revisions


def _origin(cohort: Path) -> str:
    spec = _json(cohort / "regression.json") or {}
    if spec.get("design") == "mixed-start-v1":
        return "mixed"
    checkpoints = spec.get("checkpoints") or []
    if checkpoints:
        ids = {str((_json(Path(str(c)) / "checkpoint.json") or {}).get("checkpoint_id") or "?") for c in checkpoints}
        return ("mixed C0," if spec.get("include_full") else "restart ") + ",".join(sorted(ids) or ["?"])
    match = re.search(r"-(c[0-6])-", cohort.name)
    return match.group(1).upper() if match and match.group(1) != "c0" else "C0"


def _c6_label(revision: str | None) -> str:
    if not revision:
        return "c6-?"
    known = subprocess.run(["git", "merge-base", "--is-ancestor", C6_V2, revision], cwd=ROOT).returncode
    return "c6-v2" if known == 0 else "c6-v1"


def cohort_rows(registry: Path) -> list[dict]:
    rows = []
    for cohort in sorted(p for p in registry.iterdir() if p.is_dir() and not p.name.startswith("_")):
        ledger = _json(cohort / "job-status.json")
        jobs = ledger.get("jobs") if isinstance(ledger, dict) else ledger
        if not isinstance(jobs, list) or not jobs:
            continue
        revisions = _arms(cohort)
        statuses = collections.Counter(str(job.get("status")) for job in jobs)
        by_arm: dict[str, list[dict]] = collections.defaultdict(list)
        for job in jobs:
            by_arm[str(job.get("arm"))].append(job)
        spec = _json(cohort / "regression.json") or {}
        active = statuses.get("running") or statuses.get("preparing") or (statuses.get("queued") and statuses.get("completed"))
        state = "running" if active else "complete" if statuses.get("completed") else "not started"
        for arm, arm_jobs in by_arm.items():
            done = [job for job in arm_jobs if job.get("status") == "completed"]
            ever = collections.Counter(m for job in done for m in (_literal(job.get("accepted_ever")) or ()))
            seconds = [float(_literal(job.get("server_seconds") or job.get("elapsed_s"))) for job in done
                       if _literal(job.get("server_seconds") or job.get("elapsed_s")) not in (None, "", "None")]
            revision = revisions.get(arm)
            rows.append({"cohort": cohort.name, "arm": arm, "revision": (revision or "?")[:7],
                         "origin": _origin(cohort), "speed": spec.get("game_speed"),
                         "planned": len(arm_jobs), "completed": len(done),
                         "ever": {m: ever.get(m, 0) for m in MILESTONES},
                         "c6": _c6_label(revision), "median_s": round(statistics.median(seconds)) if seconds else None,
                         "state": state})
    return rows


def markdown(rows: list[dict], generated: str) -> str:
    lines = [
        "<!-- Path: docs/rl/Laya_Cohort_Performance.md | Purpose: Generated per-arm results of every recorded cohort. -->",
        "# Laya cohort performance — every recorded cohort",
        "",
        f"Generated {generated} by `tools/laya_cohort_performance.py` from the local job ledgers in",
        "`data/laya-experiments/` (not tracked). Counts are strict milestones **ever reached** by completed",
        "jobs, over every planned job of the arm. `C6` before the c6-v2 predicate counts chest consumers",
        "(c6-v1) and is not comparable with c6-v2. Restart cohorts start from a checkpoint, so milestones at",
        "or below their origin are trivially reached. Decisions live in `docs/rl/Laya_Cohort_Results_Index.md`",
        "and `README.md`; this table is evidence, not an adoption record.",
        "",
        "| Cohort | Arm | Revision | Origin | Done/planned | C2 | C3 | C4 | C6 (predicate) | Median wall s | State |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        ever = row["ever"]
        lines.append(f"| `{row['cohort']}` | {row['arm']} | `{row['revision']}` | {row['origin']}"
                     f"{' 2x' if row['speed'] == 2.0 else ''} | {row['completed']}/{row['planned']} | {ever['C2']} | {ever['C3']}"
                     f" | {ever['C4']} | {ever['C6']} ({row['c6']}) | {row['median_s'] or '—'} | {row['state']} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=ROOT / "data/laya-experiments")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/rl/Laya_Cohort_Performance.md")
    parser.add_argument("--generated", required=True, help="Absolute IST timestamp recorded in the header")
    args = parser.parse_args(argv)
    args.output.write_text(markdown(cohort_rows(args.registry), args.generated))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
