"""Run fresh deterministic episodes with read-only OpenCode observation and Codex fixes."""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THREAD = "01a07686-c131-7d93-81f5-bdb2a9e4f426"
CODEX = Path.home() / ".local/share/mise/installs/codex/latest/bin/codex"


def run(command: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)


def report_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    sha = run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    return ROOT / "docs/deterministic/campaign_runs" / f"{stamp}_{sha}.md"


def transcript(report: Path, name: str, result: subprocess.CompletedProcess[str]) -> str:
    """Persist every agent handoff, including failures, beside its run report."""
    output = result.stdout + result.stderr
    report.with_suffix(name).write_text(output, encoding="utf-8")
    return output


def revision() -> str:
    return run(["git", "rev-parse", "HEAD"]).stdout.strip()


def codefix(report: Path, thread: str) -> bool:
    """Resume the focused-fix task and require either its handoff or a commit."""
    prompt = f"""Read the completed observer report {report}. Implement exactly one focused, reusable fix backed by its evidence; add focused tests, commit the change, and end your response with `continue`, commit ID, validation, and the fresh-run command. Preserve unrelated worktree changes. Do not mutate a live Factorio server."""
    before = revision()
    fixed = run([
        str(CODEX), "exec", "resume", "--json",
        "--output-last-message", str(report.with_suffix(".codefix-last.txt")),
        thread, prompt,
    ], timeout=2 * 3600)
    output = transcript(report, ".codefix.jsonl", fixed)
    after = revision()
    if fixed.returncode == 0 and (after != before or "continue" in output.lower()):
        print(f"Code-fix handoff completed for {report.name}; revision={after[:12]}.")
        return True
    print(
        f"Code-fix execution did not complete (exit={fixed.returncode}, "
        f"revision_changed={after != before}); retaining {report.name}.",
        file=sys.stderr,
    )
    return False


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
        transcript(report, ".opencode.jsonl", observed)
        if observed.returncode:
            print(f"OpenCode observer failed; retrying fresh run after retained report: {report}", file=sys.stderr)
            time.sleep(60)
            continue
        if not codefix(report, args.codefix_thread):
            print(f"Code-fix handoff exhausted retries; retaining report and retrying it before any new run.", file=sys.stderr)
            time.sleep(300)
            while not codefix(report, args.codefix_thread):
                print(f"Code-fix handoff still unavailable; keeping this run queued.", file=sys.stderr)
                time.sleep(300)
            continue
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
