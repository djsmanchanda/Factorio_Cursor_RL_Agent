#!/usr/bin/env python3
# Path: tools/laya_helper_agent.py
# Purpose: After every clean Laya run, have a read-only OpenCode Muse helper compare it with the previous commit's Laya run and the reference deterministic run.

"""Laya post-run helper.

Started by tools/run_laya_clean.py when a run ends.  It builds one bounded
evidence packet (current Laya run, the latest Laya run from an earlier commit,
and the configured deterministic reference run), then asks one OpenCode
session to write ``docs/rl/laya_helper_runs/<run_id>/findings.md``.  The helper
is read-only: no code edits, commits, lifecycle actions or RCON.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.opencode_helper_agent import _session_id

CONFIG = REPO_ROOT / "docs/rl/laya_helper.json"
RUN_INDEX = REPO_ROOT / "data/laya-saved-world/laya-runs.jsonl"
REPORT_ROOT = REPO_ROOT / "docs/rl/laya_helper_runs"
DATA_ROOT = Path.home() / ".local/share/factorio-rl/laya_helper"
LOG_BUDGET = 24_000  # characters of runner log per run in the packet
KEY_EVENTS = ("RUN START", "PLAN", "PHASE REACHED", "FAILURE", "SAFE STOP", "SUMMARY", "STOP REQUESTED", "RUN END",
              "STUCK", "BLOCKER", "GOAL MET", "LOG COMPACTION", "CHEMICAL LADDER")

WORKFLOW_NOTE = """The two systems are different workflows; compare outcomes and causes, not line formats.
- Deterministic runner (tools/autonomous_run.py -> orchestrator/autonomous_builder.py): a hand-written
  cascade that picks the deepest missing stage, builds belt-fed mines/furnace lines, a rotating mall and
  the oil/plastic chain with stage builders, and logs SURVEY/DEMAND/BLOCKER/STUCK lines.
- Clean Laya runner (tools/run_laya_clean.py -> training/laya_clean_runner.py): live, unlimited, on a
  disposable copy of saves/mod_playground.zip. Each decision: status board (goal demand, measured supply,
  machine conditions, bottleneck chain) -> need-tagged legal options (site plan reservations, power rules,
  live legality/funding checks) -> policy choice -> saved-world adapter builds funded ghosts -> measured
  outcome. The policy is seeded exploration until a compatible Laya checkpoint exists, so poor choices may
  be the policy, not the tooling. Mission ladder: C0 iron/copper starter, C1 starter mall, C2/C3 rollouts,
  C4 plastic, C6 advanced circuits, S1 science. Design: docs/rl/Laya_Clean_Runner.md; strategy rules:
  docs/rl/Laya_Strategy_Guidelines.md."""


def load_config() -> dict:
    return json.loads(CONFIG.read_text())


def run_index() -> list[dict]:
    if not RUN_INDEX.is_file():
        return []
    return [json.loads(line) for line in RUN_INDEX.read_text().splitlines() if line.strip()]


def previous_run(current: dict, runs: list[dict]) -> dict | None:
    """Latest earlier Laya run from a different commit, else the latest earlier run."""
    earlier = [r for r in runs if r["run_id"] != current["run_id"] and r.get("ended", "") <= current.get("ended", "")]
    other_commit = [r for r in earlier if r.get("commit") != current.get("commit")]
    pool = other_commit or earlier
    return pool[-1] if pool else None


def run_log_slice(log: Path, run_id: str) -> list[str]:
    """Lines of one run: from its RUN START (which names the run id) to the next RUN END."""
    if not log.is_file():
        return []
    lines, inside = [], False
    for line in log.read_text(errors="replace").splitlines():
        if line.startswith("RUN START:"):
            inside = f"run_id={run_id}" in line
        if inside:
            lines.append(line)
            if line.rstrip().endswith("RUN END"):
                break
    return lines


def compact_log(lines: list[str], budget: int = LOG_BUDGET) -> str:
    """Key events always; repeated STATUS/BLOCKED lines once each; head and tail within budget."""
    kept, seen = [], set()
    for line in lines:
        body = line.split(" ", 1)[1] if line.startswith("+") and " " in line else line
        if body.strip().startswith(("STATUS:", "BLOCKED:", "BOTTLENECK:")):
            if body in seen:
                continue
            seen.add(body)
        kept.append(line)
    text = "\n".join(kept)
    if len(text) <= budget:
        return text
    key = "\n".join(line for line in kept if any(event in line for event in KEY_EVENTS))
    room = max(0, budget - len(key) - 200) // 2
    return f"{text[:room]}\n... [middle omitted] ...\n{text[-room:]}\n--- key events ---\n{key[:budget // 3]}"


def journal_summary(journal: Path, run_id: str) -> dict:
    rows = [json.loads(l) for l in journal.read_text().splitlines()] if journal.is_file() else []
    rows = [r for r in rows if r.get("run_id") == run_id]
    done = [r for r in rows if r.get("event") == "decision" and r.get("status") != "pending"]
    last = done[-1] if done else {}
    return {"decisions": len(done), "failures": sum(r.get("event") == "failure" for r in rows),
            "needs_chosen": dict(Counter(r["choice"]["need_id"] for r in done if "choice" in r)),
            "statuses": dict(Counter(r["status"] for r in done)),
            "phases_reached": [r["phase"] for r in rows if r.get("event") == "phase_reached"],
            "failure_messages": [r.get("error", "")[:200] for r in rows if r.get("event") == "failure"][-5:],
            "final_board": last.get("board"), "final_blocked": last.get("blocked"),
            "final_measured_per_s": last.get("measured_per_s"),
            "summary": next((r for r in rows if r.get("event") == "summary"), None)}


def laya_section(title: str, run: dict | None) -> str:
    if run is None:
        return f"## {title}\n\nNo earlier Laya run is recorded.\n"
    root = Path(run["root"])
    lines = run_log_slice(root / "logs/laya-runner.log", run["run_id"])
    return (f"## {title}\n\nrun_id={run['run_id']} commit={run.get('commit')} root={root} mode={run.get('mode')}\n"
            f"started={run.get('started')} ended={run.get('ended')}\n\n### Journal summary\n```json\n"
            f"{json.dumps(journal_summary(root / 'evidence/clean-decisions.jsonl', run['run_id']), indent=1)[:12000]}\n```\n"
            f"### Runner log (compacted)\n```text\n{compact_log(lines)}\n```\n")


def deterministic_section(reference: dict) -> str:
    log = Path(os.path.expanduser(reference["log"]))
    lines = log.read_text(errors="replace").splitlines() if log.is_file() else []
    return (f"## Deterministic reference: {reference['label']}\n\n{reference['note']}\n\nlog={log}\n"
            f"helper findings for that run: {reference['findings']}\n\n"
            f"### Runner log (compacted)\n```text\n{compact_log(lines)}\n```\n")


def build_packet(current: dict, previous: dict | None, config: dict) -> str:
    return "\n".join([
        f"# Laya post-run evidence packet — {current['run_id']}\n", WORKFLOW_NOTE, "",
        laya_section("Current Laya run", current),
        laya_section("Previous Laya run (latest earlier commit)", previous),
        deterministic_section(config["deterministic_reference"]),
    ])


def prompt(current: dict, previous: dict | None, packet: Path, report: Path, commits: str) -> str:
    return f"""You are the read-only Laya Helper Agent for the Factorio Laya project. Work in {REPO_ROOT}.
Read the evidence packet at {packet}. It contains the run that just ended ({current['run_id']}, commit {current.get('commit')}),
the latest Laya run from an earlier commit ({previous['run_id'] if previous else 'none'}), and a reference deterministic run.
The deterministic and Laya systems are different workflows (the packet explains both); compare outcomes, milestones,
time to each milestone, failure modes and causes, not log formats. Read the cited repository code and docs as needed
(docs/rl/Laya_Clean_Runner.md, docs/rl/Laya_Strategy_Guidelines.md, training/laya_clean_runner.py and the modules it uses).

Recent commits:
```text
{commits}
```

Write {report} with these sections:
1. Outcome: mission phases reached, decisions, failures, stop reason, measured production, for this run vs the previous Laya run vs the deterministic reference.
2. What went well (with evidence lines).
3. Regressions versus the previous Laya run (or "none observed"), with evidence.
4. Root cause of the most important problem: separate policy choices (exploration) from tooling, observation, planning or execution defects; give competing explanations and the evidence that would discriminate them.
5. Small improvement suggestions, each with the file it would touch and a measurable prediction for the next run.
6. Laya workflow changes so the next run is better, under three headings: context management (what the decision state/guidelines/history should carry or drop, token budget), fine-tuning (what data, labels, comparisons or checkpoints to produce from these journals), tooling (options, realizers, observations, validators).
Respect the project rules in AGENTS.md: no hand-coded situation branches as fixes; learn from outcomes.
Remain read-only: do not edit code, commit, start/stop servers or runners, send RCON or alter any world. Only write the findings document."""


def recent_commits() -> str:
    result = subprocess.run(["git", "log", "--oneline", "-10"], cwd=REPO_ROOT, capture_output=True, text=True)
    return result.stdout.strip() or "unavailable"


def review(run_id: str, opencode: str, timeout: int = 1800) -> Path:
    config = load_config()
    runs = run_index()
    current = next(r for r in runs if r["run_id"] == run_id)
    previous = previous_run(current, runs)
    report = REPORT_ROOT / run_id / "findings.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    work = DATA_ROOT / run_id
    work.mkdir(parents=True, exist_ok=True)
    packet = work / "evidence-packet.md"
    packet.write_text(build_packet(current, previous, config))
    report.write_text(f"<!-- Path: {report.relative_to(REPO_ROOT)} | Purpose: Read-only Laya Helper findings for {run_id}. -->\n\n"
                      f"# Laya Helper findings — {run_id}\n\n_Review in progress._\n")
    message = prompt(current, previous, packet, report, recent_commits())
    session = None
    for attempt in range(3):
        command = [opencode, "run", "--dir", str(REPO_ROOT), "--format", "json", "--model", config["model"],
                   "--variant", config["variant"], "--auto"]
        command += ["--session", session] if session else ["--title", f"Laya Helper {run_id}"]
        result = subprocess.run(command + [message], cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout)
        (work / f"opencode-{attempt}.jsonl").write_text(result.stdout + result.stderr)
        session = session or _session_id(result.stdout + result.stderr)
        if result.returncode == 0:
            return report
        message = ("Continue the same read-only Laya helper review from where it stopped; finish the findings "
                   f"document at {report}. Do not restart or create another report.")
    raise RuntimeError(f"OpenCode failed three times; transcripts in {work}")


def launch(run_id: str, log: Path) -> str:
    """Start the helper detached from the runner; it outlives the runner process."""
    out = DATA_ROOT / run_id / "helper.log"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("ab") as handle:
        process = subprocess.Popen([sys.executable, str(Path(__file__)), "--run-id", run_id, "--log", str(log)],
                                   cwd=REPO_ROOT, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    return f"pid={process.pid} log={out}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--log", type=Path, help="Runner log to append the completion line to")
    parser.add_argument("--opencode-bin", default=shutil.which("opencode") or "opencode")
    args = parser.parse_args(argv)
    try:
        report = review(args.run_id, args.opencode_bin)
        line = f"LAYA HELPER: findings complete {report}"
    except Exception as error:  # Reported in the runner log; never retried silently.
        line = f"LAYA HELPER: stopped after failure: {type(error).__name__}: {str(error)[:300]}"
    if args.log:
        with args.log.open("a") as out:
            out.write(line + "\n")
    print(line)
    return 0 if "complete" in line else 1


if __name__ == "__main__":
    raise SystemExit(main())
