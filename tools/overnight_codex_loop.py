"""Run fresh deterministic episodes with read-only OpenCode observation and Codex fixes."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THREAD = "01a07686-c131-7d93-81f5-bdb2a9e4f426"


def run(command: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)


def report_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    sha = run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    return ROOT / "docs/deterministic/campaign_runs" / f"{stamp}_{sha}.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=12)
    parser.add_argument("--technology", default="mining-productivity-4")
    parser.add_argument("--codefix-thread", default=THREAD)
    args = parser.parse_args(argv)
    deadline = time.monotonic() + args.hours * 3600
    while time.monotonic() < deadline:
        report = report_path()
        report.parent.mkdir(parents=True, exist_ok=True)
        fresh = run([
            "scripts/manage_linux_deterministic_campaign.sh", "fresh",
            "--source-save", str(Path.home() / ".factorio/saves/mod_playground.zip"),
            "--python", str(ROOT / ".venv/bin/python"), "--technology", args.technology,
        ], timeout=900)
        if fresh.returncode:
            print(fresh.stdout + fresh.stderr, file=sys.stderr)
            return fresh.returncode
        prompt = f"""Observe this one isolated deterministic Factorio run until RUN END. You are read-only: do not edit code, commit, deploy, restart, reset, or mutate the factory. Every two minutes inspect only new runner log output, read-only server state, and http://127.0.0.1:9137/api/logistic-inventory. Write the complete evidence, bottlenecks, mall targets/inventory, code correlations, and final comparison with the preceding report to {report}. End with a concise structured handoff for the Codex code-fix task. This report is the only report for this run."""
        observed = run([
            "opencode", "run", "--dir", str(ROOT), "--format", "json", "--model",
            "opencode-go/muse-spark-1.3-contributor", "--variant", "xhigh", "--auto", prompt,
        ], timeout=4 * 3600)
        (report.with_suffix(".opencode.jsonl")).write_text(observed.stdout + observed.stderr, encoding="utf-8")
        if observed.returncode:
            print(f"OpenCode observer failed; report retained: {report}", file=sys.stderr)
            return observed.returncode
        fix_prompt = f"""Read the completed observer report {report}. Implement exactly one focused, reusable fix backed by its evidence; add focused tests, commit the change, and end your response with `continue`, commit ID, validation, and the fresh-run command. Preserve unrelated worktree changes. Do not mutate a live Factorio server."""
        fixed = run([
            "codex", "exec", "resume", args.codefix_thread, fix_prompt,
            "--output-last-message", str(report.with_suffix(".codefix.txt")),
            "--json",
        ], timeout=2 * 3600)
        if fixed.returncode or "continue" not in (fixed.stdout + fixed.stderr).lower():
            print(f"Code-fix handoff did not continue; report retained: {report}", file=sys.stderr)
            return fixed.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
