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
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.local_time import local_stamp, run_length, to_local, zone_label
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
    # Windowed experiment runs (tools/laya_experiment.py) are not full runs to compare against.
    earlier = [r for r in runs if r["run_id"] != current["run_id"] and r.get("ended", "") <= current.get("ended", "")
               and not r.get("experiment")]
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


def _seconds(run: dict) -> int | None:
    try:
        return int((datetime.fromisoformat(to_local(run["ended"])) - datetime.fromisoformat(to_local(run["started"]))).total_seconds())
    except (KeyError, TypeError, ValueError):
        return None


def laya_section(title: str, run: dict | None) -> str:
    if run is None:
        return f"## {title}\n\nNo earlier Laya run is recorded.\n"
    root = Path(run["root"])
    lines = run_log_slice(root / "logs/laya-runner.log", run["run_id"])
    return (f"## {title}\n\nrun_id={run['run_id']} commit={run.get('commit')} root={root} mode={run.get('mode')}\n"
            f"started={to_local(run.get('started'))} ended={to_local(run.get('ended'))} (local time) "
            f"run_length={run_length(_seconds(run))}\n\n### Journal summary\n```json\n"
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
        f"# Laya post-run evidence packet — {current['run_id']}\n",
        f"Generated {local_stamp()}. All times are local time ({zone_label()}); log '+Ns' prefixes are seconds since RUN START.\n",
        WORKFLOW_NOTE, "",
        laya_section("Current Laya run", current),
        laya_section("Previous Laya run (latest earlier commit)", previous),
        deterministic_section(config["deterministic_reference"]),
    ])


def prompt(current: dict, previous: dict | None, packet: Path, report: Path, commits: str) -> str:
    return f"""You are the read-only Laya Helper Agent for the Factorio Laya project. Work in {REPO_ROOT}.
Read the evidence packet at {packet}. It contains the run that just ended ({current['run_id']}, commit {current.get('commit')}),
the latest Laya run from an earlier commit ({previous['run_id'] if previous else 'none'}), and a reference deterministic run.
All times are local time ({zone_label()}); report times in local time and run lengths as h/m/s.
The deterministic and Laya systems are different workflows (the packet explains both); compare outcomes, milestones,
time to each milestone, failure modes and causes, not log formats. Read the cited repository code and docs as needed
(docs/rl/Laya_Clean_Runner.md, docs/rl/Laya_Strategy_Guidelines.md, training/laya_clean_runner.py and the modules it uses).

Recent commits:
```text
{commits}
```

Write {report} with these sections:
1. Outcome: run length, mission phases reached (with time from run start), decisions, failures, stop reason, measured production, for this run vs the previous Laya run vs the deterministic reference.
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


def _ask(config: dict, work: Path, message: str, session: str | None, title: str, retry: str,
         timeout: int = 1800) -> str:
    """One OpenCode turn (new or resumed session), resumed up to twice on failure; returns the session id."""
    for attempt in range(3):
        # OpenCode v2 takes the reasoning variant inside the model id and the directory from cwd.
        command = [OPENCODE[0], "run", "--format", "json", "--model", f"{config['model']}#{config['variant']}", "--auto"]
        command += ["--session", session] if session else ["--title", title]
        result = subprocess.run(command + [message], cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout)
        stamp = len(list(work.glob("opencode-*.jsonl")))
        (work / f"opencode-{stamp:03d}.jsonl").write_text(result.stdout + result.stderr)
        session = session or _session_id(result.stdout + result.stderr)
        if result.returncode == 0 and session:
            return session
        message = retry
    raise RuntimeError(f"OpenCode failed three times; transcripts in {work}")


OPENCODE = ["opencode"]
OBSERVE_INTERVAL_S = 600


def _paths(run_id: str) -> tuple[Path, Path]:
    report = REPORT_ROOT / run_id / "findings.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    work = DATA_ROOT / run_id
    work.mkdir(parents=True, exist_ok=True)
    return report, work


def _header(report: Path, run_id: str, note: str) -> None:
    if not report.exists():
        report.write_text(f"<!-- Path: {report.relative_to(REPO_ROOT)} | Purpose: Read-only Laya Helper findings for {run_id}. -->\n\n"
                          f"# Laya Helper findings — {run_id}\n\n_{note} (started {local_stamp()})._\n")


def final_review(run_id: str, session: str | None = None) -> Path:
    """Full post-run comparison, in the observer's session when there is one."""
    config = load_config()
    runs = run_index()
    current = next(r for r in runs if r["run_id"] == run_id)
    previous = previous_run(current, runs)
    report, work = _paths(run_id)
    _header(report, run_id, "Review in progress")
    packet = work / "evidence-packet.md"
    packet.write_text(build_packet(current, previous, config))
    message = prompt(current, previous, packet, report, recent_commits())
    if session:
        message += ("\nKeep the '## Live observations' section you wrote during the run; add the six sections after it "
                    "and say which live observations the final evidence confirmed or refuted.")
    _ask(config, work, message, session, f"Laya Helper {run_id}",
         "Continue the same read-only Laya helper review from where it stopped; finish the findings "
         f"document at {report}. Do not restart or create another report.")
    return report


review = final_review


def _run_slice_since(log: Path, run_id: str, offset_lines: int) -> tuple[list[str], int]:
    lines = run_log_slice(log, run_id)
    return lines[offset_lines:], len(lines)


END_POLL_S = 15


def _wait_for_checkpoint_or_end(log: Path, run_id: str, seen: int, pid_record: Path, interval: int) -> None:
    """Sleep until the next checkpoint, waking early when the run ends or the runner disappears."""
    deadline = time.monotonic() + interval
    while time.monotonic() < deadline:
        time.sleep(min(END_POLL_S, max(0.0, deadline - time.monotonic())))
        new, _ = _run_slice_since(log, run_id, seen)
        if any(line.rstrip().endswith("RUN END") for line in new) or not pid_record.exists():
            return


def observe(run_id: str, log: Path, root: Path, interval: int = OBSERVE_INTERVAL_S, max_hours: float = 12) -> Path:
    """Live observer: checkpoint notes every ``interval`` seconds, then the final review in the same session."""
    config = load_config()
    report, work = _paths(run_id)
    _header(report, run_id, "Live observation in progress")
    status = root / "evidence/latest-status.json"
    pid_record = root / "laya-runner.pid.json"
    session, seen, deadline, checkpoint = None, 0, time.monotonic() + max_hours * 3600, 0
    intro = f"""You are the read-only live observer for Laya run {run_id} (repository {REPO_ROOT}).
All times are local time ({zone_label()}). {WORKFLOW_NOTE}
You will receive a checkpoint every {interval // 60} minutes. At each checkpoint append at most 10 lines under a
'## Live observations' heading in {report}: timestamped (local time) notes on stalls, wasted decisions, blocked needs,
suspected root causes with the evidence line, and one falsifiable signal to watch next. Do not repeat unchanged state.
Strategy rules Laya reads: docs/rl/Laya_Strategy_Guidelines.md and docs/rl/laya_mission_guidelines.json (prefer entries
steer the guided policy). Remain read-only: only write {report}; no code edits, commits, lifecycle actions or RCON."""
    while time.monotonic() < deadline:
        new, seen_now = _run_slice_since(log, run_id, seen)
        ended = any(line.rstrip().endswith("RUN END") for line in new)
        runner_alive = pid_record.exists()
        if new or session is None:
            checkpoint += 1
            packet = work / f"checkpoint-{checkpoint:03d}.md"
            latest = status.read_text()[:20000] if status.is_file() else "{}"
            packet.write_text(f"# Checkpoint {checkpoint} — {local_stamp()}\n\n## New runner log lines\n```text\n"
                              f"{compact_log(new, budget=12000)}\n```\n## Latest decision status (JSON)\n```json\n{latest}\n```\n")
            message = (intro if session is None else "") + f"\nCheckpoint {checkpoint}: read {packet} and append to {report}."
            try:
                session = _ask(config, work, message, session, f"Laya Observer {run_id}",
                               f"Continue the read-only live observation; process {packet} and append to {report}.", timeout=900)
            except Exception as error:  # A failed checkpoint is noted; the final review still runs.
                with report.open("a") as out:
                    out.write(f"\n_Checkpoint {checkpoint} failed: {type(error).__name__}: {str(error)[:200]}_\n")
            seen = seen_now
        if ended or not runner_alive:
            break
        _wait_for_checkpoint_or_end(log, run_id, seen, pid_record, interval)
    for _ in range(60):  # The runner indexes the run right after RUN END.
        if any(r["run_id"] == run_id for r in run_index()):
            break
        time.sleep(2)
    return final_review(run_id, session)


def launch(run_id: str, log: Path, root: Path | None = None) -> str:
    """Start the helper detached from the runner; with ``root`` it observes live from RUN START."""
    out = DATA_ROOT / run_id / "helper.log"
    out.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(Path(__file__)), "--run-id", run_id, "--log", str(log)]
    if root is not None:
        command += ["--observe-root", str(root)]
    with out.open("ab") as handle:
        process = subprocess.Popen(command, cwd=REPO_ROOT, stdin=subprocess.DEVNULL, stdout=handle,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    return f"pid={process.pid} log={out}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--log", type=Path, help="Runner log (observed live, and the completion line is appended)")
    parser.add_argument("--observe-root", type=Path, help="Episode root: observe live from RUN START, then review")
    parser.add_argument("--interval", type=int, default=OBSERVE_INTERVAL_S)
    parser.add_argument("--opencode-bin", default=shutil.which("opencode") or "opencode")
    args = parser.parse_args(argv)
    OPENCODE[0] = args.opencode_bin
    try:
        if args.observe_root and args.log:
            report = observe(args.run_id, args.log, args.observe_root, args.interval)
        else:
            report = final_review(args.run_id)
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
