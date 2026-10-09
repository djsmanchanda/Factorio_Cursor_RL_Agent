#!/usr/bin/env python3
# Path: tools/laya_helper_agent.py
# Purpose: After every clean Laya run, have a read-only OpenCode Muse helper compare it with prior Laya and selected deterministic evidence.

"""Laya post-run helper.

Started by tools/run_laya_clean.py when a run ends.  It builds one bounded
evidence packet (current Laya run, the latest comparable earlier Laya run,
and the selected deterministic reference), then asks one OpenCode
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
from collections.abc import Sequence
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

KNOWN_WRONG_CLAIMS = """Known wrong claims to avoid (seen in recent helper reports; do not repeat):
- Claiming "no C1 pass" while the run summary lists accepted=C0,C1. accepted/latched is the monotonic predicate pass, not a proxy.
- Comparing against the archived deterministic reference when the paired prior run is supplied as a comparator.
- Treating experiment labels alone as proof that an explicitly supplied live run is not comparable.
- Reporting a game-time duration (minutes) as wall run length."""

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


def previous_run(current: dict, runs: list[dict], comparators: Sequence[dict] = ()) -> dict | None:
    """Latest earlier comparable Laya run from a different commit, else the latest earlier run.

    ``comparators`` are run records the caller supplies explicitly; they may live
    outside this repository's isolated run index (for example the paired prior
    live run). Experiment labels exclude indexed runs by default unless a
    comparable window is declared; explicitly supplied comparators remain eligible.
    """
    explicit = {r.get("run_id") for r in comparators}
    merged, seen = [], set()
    # Caller-provided records win on duplicate run ids so their external root
    # and declared comparison metadata remain authoritative.
    for run in [*comparators, *runs]:
        run_id = run.get("run_id")
        if run_id in seen or run_id == current.get("run_id") or run.get("kind") == "deterministic":
            continue
        seen.add(run_id)
        merged.append(run)
    earlier = [r for r in merged
               if (r.get("run_id") in explicit or r.get("ended", "") <= current.get("ended", ""))
               and (r.get("run_id") in explicit or not r.get("experiment") or r.get("comparable_window"))]
    other_commit = [r for r in earlier if r.get("commit") != current.get("commit")]
    pool = other_commit or earlier
    return max(pool, key=lambda r: r.get("ended") or "") if pool else None


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
            # Ops decision rows carry a summary but no status (training/laya_ops_runner.py).
            "statuses": dict(Counter(r.get("status", "recorded") for r in done)),
            "top_choices": dict(Counter(r["summary"] for r in done if "summary" in r).most_common(15)),
            "phases_reached": [r["phase"] for r in rows if r.get("event") == "phase_reached"],
            "failure_messages": [r.get("error", "")[:200] for r in rows if r.get("event") == "failure"][-5:],
            "final_board": last.get("board"), "final_blocked": last.get("blocked"),
            "final_measured_per_s": last.get("measured_per_s"),
            "summary": next((r for r in rows if r.get("event") == "summary"), None)}


def _seconds(run: dict) -> int | None:
    """Wall-clock seconds between the indexed start and end, or None when unreadable."""
    try:
        return int((datetime.fromisoformat(to_local(run["ended"])) - datetime.fromisoformat(to_local(run["started"]))).total_seconds())
    except (KeyError, TypeError, ValueError):
        return None


def run_summary(run: dict) -> dict:
    summary = run.get("summary")
    return summary if isinstance(summary, dict) else {}


def elapsed_times(run: dict, log_lines: list[str]) -> dict:
    """Wall seconds from the index and game seconds from the runner's ELAPSED line."""
    wall, game, source = _seconds(run), None, None
    for line in reversed(log_lines):
        if "ELAPSED:" not in line:
            continue
        fields = {}
        for part in line.split("ELAPSED:", 1)[1].split():
            key, sep, value = part.partition("=")
            if sep:
                fields[key] = value
        try:
            wall = int(fields.get("wall_seconds", wall))
        except (TypeError, ValueError):
            pass
        raw = fields.get("game_seconds")
        if raw and raw != "unknown":
            try:
                game, source = float(raw), "runner ELAPSED line"
            except ValueError:
                pass
        break
    return {"wall_seconds": wall, "game_seconds": game, "game_source": source}


def timing_section(run: dict, log_lines: list[str]) -> str:
    times = elapsed_times(run, log_lines)
    wall, game = times["wall_seconds"], times["game_seconds"]
    wall_text = f"{wall} ({run_length(wall)})" if wall is not None else "unknown"
    game_text = f"{game:g} ({run_length(round(game))} of game time)" if game is not None else "unknown"
    source = f" [{times['game_source']}]" if times["game_source"] else ""
    return ("TIME:\n"
            f"  wall_seconds={wall_text}\n"
            f"  game_seconds={game_text}{source}\n"
            "  NOTE: game_seconds are Factorio ticks/60 (game time); wall_seconds are real clock seconds. "
            "Never compare a game-time figure to the wall run length.\n")


def acceptance_vector(run: dict) -> dict:
    """Per-checkpoint current+latched status, predicate version and acceptance flag."""
    summary = run_summary(run)
    details = summary.get("acceptance_details") or {}
    results = details.get("results") or {}
    accepted = set(summary.get("accepted") or [])
    statuses = summary.get("acceptance")
    vector: dict = {}
    if isinstance(statuses, dict) and "error" not in statuses:
        for checkpoint, status in statuses.items():
            entry = results.get(checkpoint) or {}
            vector[checkpoint] = {"current": entry.get("status", "unknown"),
                                  "latched": entry.get("latched_status") or status,
                                  "predicate_version": entry.get("predicate_version"),
                                  "accepted": checkpoint in accepted, "missing": entry.get("missing") or [],
                                  "working_mall_count": entry.get("working_mall_count")}
    elif accepted:
        for checkpoint in sorted(accepted):
            vector[checkpoint] = {"current": "unknown", "latched": "passed", "predicate_version": None,
                                  "accepted": True, "missing": [], "working_mall_count": None}
    return vector


def acceptance_section(run: dict) -> str:
    """Versioned status vector; accepted/latched is the monotonic pass, never a proxy."""
    summary = run_summary(run)
    if not summary:
        return "No acceptance summary was recorded for this run.\n"
    accepted = sorted(summary.get("accepted") or [])
    vector = acceptance_vector(run)
    comparisons = (summary.get("acceptance_details") or {}).get("predicate_comparisons") or {}
    lines = ["ACCEPTED (latched passes, monotonic): " + (", ".join(accepted) or "none")]
    statuses = summary.get("acceptance")
    if isinstance(statuses, dict) and statuses.get("error"):
        lines.append(f"ACCEPTANCE ERROR: {statuses['error']}")
    if vector:
        lines.append("STATUS VECTOR (current = fresh evaluation, latched = monotonic reached; accepted means latched pass):")
        for checkpoint in sorted(vector):
            entry = vector[checkpoint]
            count = (f" working_mall_count={entry['working_mall_count']}"
                     if entry.get("working_mall_count") is not None else "")
            lines.append(f"  {checkpoint} current={entry['current']} latched={entry['latched']} "
                         f"predicate={entry['predicate_version'] or 'unknown'} "
                         f"accepted={'yes' if entry['accepted'] else 'no'} missing={entry['missing']}{count}")
    if comparisons:
        lines.append("COMPARABLE PREDICATE VERSIONS (retired, historical comparison only; the active predicate is unchanged):")
        for checkpoint in sorted(comparisons):
            for version in sorted(comparisons[checkpoint]):
                value = comparisons[checkpoint][version]
                extra = value.get("evidence", {})
                extra_text = " ".join(f"{key}={item}" for key, item in sorted(extra.items()))
                lines.append(f"  {checkpoint} {version} current={value.get('status')} checks={value.get('checks', {})}"
                             + (f" {extra_text}" if extra_text else ""))
    return "\n".join(lines) + "\n"


def laya_section(title: str, run: dict | None) -> str:
    if run is None:
        return f"## {title}\n\nNo earlier comparable Laya run is recorded.\n"
    root = Path(run["root"])
    lines = run_log_slice(root / "logs/laya-runner.log", run["run_id"])
    return (f"## {title}\n\nrun_id={run['run_id']} revision={run.get('commit')} root={root} mode={run.get('mode')}\n"
            f"started={to_local(run.get('started'))} ended={to_local(run.get('ended'))} (local time) "
            f"run_length={run_length(_seconds(run))}\n"
            f"variant={run.get('variant_id')} variant_hash={run.get('variant_hash')} experiment={run.get('experiment')}\n\n"
            f"### Time\n{timing_section(run, lines)}\n"
            f"### Acceptance (versioned; accepted/latched = monotonic pass)\n```text\n{acceptance_section(run)}```\n"
            f"### Journal summary\n```json\n"
            f"{json.dumps(journal_summary(root / 'evidence/clean-decisions.jsonl', run['run_id']), indent=1)[:12000]}\n```\n"
            f"### Runner log (compacted)\n```text\n{compact_log(lines)}\n```\n")


def provided_comparators(records: Sequence[dict]) -> str:
    """Explicit comparator roots the caller supplied, even when outside the isolated index."""
    if not records:
        return ""
    lines = ["## Provided comparator runs", "",
             "Explicit roots supplied by the caller; these can live outside this repository's isolated run index.", ""]
    for run in records:
        lines.append(f"- run_id={run.get('run_id')} revision={run.get('commit')} kind={run.get('kind', 'laya')} "
                     f"root={run.get('root')} started={to_local(run.get('started'))} ended={to_local(run.get('ended'))}")
    return "\n".join(lines) + "\n"


def deterministic_section(reference: dict) -> str:
    log = Path(os.path.expanduser(reference["log"]))
    lines = log.read_text(errors="replace").splitlines() if log.is_file() else []
    return (f"## Deterministic reference: {reference['label']}\n\n{reference['note']}\n\nlog={log}\n"
            f"helper findings for that run: {reference['findings']}\n\n"
            f"### Runner log (compacted)\n```text\n{compact_log(lines)}\n```\n")


def selected_deterministic_reference(config: dict, comparators: Sequence[dict]) -> dict:
    """Use an explicitly supplied deterministic root/log before the configured fallback."""
    supplied = [run for run in comparators if run.get("kind") == "deterministic"
                and (run.get("log") or run.get("log_path") or run.get("root"))]
    if not supplied:
        return config["deterministic_reference"]
    record = supplied[-1]
    root_value = record.get("root")
    root = Path(os.path.expanduser(str(root_value))) if root_value else None
    log_value = record.get("log") or record.get("log_path")
    if log_value:
        log = Path(os.path.expanduser(str(log_value)))
    elif root is not None:
        candidates = [root / "server/logs/autonomous-run.log", root / "logs/autonomous-run.log"]
        log = next((path for path in candidates if path.is_file()), candidates[0])
    else:
        return config["deterministic_reference"]
    return {"label": record.get("label") or record.get("run_id") or "explicit deterministic comparator",
            "note": record.get("note") or "Explicit deterministic comparator supplied for this review.",
            "log": str(log), "findings": record.get("findings") or "not supplied",
            "root": str(root) if root is not None else None}


def build_packet(current: dict, previous: dict | None, config: dict, comparators: Sequence[dict] = ()) -> str:
    return "\n".join([
        f"# Laya post-run evidence packet — {current['run_id']}\n",
        f"Generated {local_stamp()}. All times are local time ({zone_label()}); log '+Ns' prefixes are seconds since RUN START.\n",
        WORKFLOW_NOTE, "", KNOWN_WRONG_CLAIMS, "",
        laya_section("Current Laya run", current),
        laya_section("Previous Laya run (latest earlier comparable run)", previous),
        provided_comparators(comparators),
        deterministic_section(selected_deterministic_reference(config, comparators)),
    ])


def prompt(current: dict, previous: dict | None, packet: Path, report: Path, commits: str,
           comparators: Sequence[dict] = ()) -> str:
    comparator_ids = ", ".join(r.get("run_id", "?") for r in comparators) or "none"
    return f"""You are the read-only Laya Helper Agent for the Factorio Laya project. Work in {REPO_ROOT}.
Read the evidence packet at {packet}. It contains the run that just ended ({current['run_id']}, revision {current.get('commit')}),
the latest comparable Laya run ({previous['run_id'] if previous else 'none'}), explicit comparator run ids ({comparator_ids}),
and the deterministic reference selected for this comparison. All times are local time ({zone_label()}); report times in local time as h/m/s.

Evidence rules — do not reinterpret:
- In the status vector, accepted/latched is the checkpoint predicate's monotonic pass. If a checkpoint id is in accepted,
  it PASSED; never write "no C1 pass" for an accepted checkpoint and never call it a proxy. Current is unknown when no fresh
  per-checkpoint result exists; do not infer current=passed from a latched summary.
- "PHASE REACHED: Cn" is a rate proxy, not acceptance. Report proxy phases and accepted checkpoints side by side, not merged.
- c1-v1 and c1-v2 are both evaluated on the same snapshot; c1-v2 is the active predicate and c1-v1 is retired for
  historical comparison only. Report both counts (e.g. working_mall_count) rather than one.
- game_seconds are Factorio ticks/60 (game time); wall_seconds are real clock seconds. Never compare a game-time
  figure to the wall run length, and never report a game duration as minutes of wall time.
- Compare only to the comparator runs named with their exact run_id/revision/root; do not substitute an archived
  deterministic reference when an explicit prior pair is provided.

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
        command = [OPENCODE[0], "run", "--dir", str(REPO_ROOT), "--format", "json", "--model", config["model"],
                   "--variant", config["variant"], "--auto"]
        command += ["--session", session] if session else ["--title", title]
        report = str(Path(config["report_path"]).resolve())
        env = dict(os.environ)
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps({"permission": {
            "bash": "deny", "task": "deny", "external_directory": "allow",
            "edit": {"*": "deny", report: "allow"},
            "write": {"*": "deny", report: "allow"},
        }})
        result = subprocess.run(command + [message], cwd=REPO_ROOT, capture_output=True, text=True,
                                timeout=timeout, env=env)
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
        report.write_text(f"<!-- Path: {report} | Purpose: Read-only Laya Helper findings for {run_id}. -->\n\n"
                          f"# Laya Helper findings — {run_id}\n\n_{note} (started {local_stamp()})._\n")


def _write_record(run_id: str, status: str, *, report: Path | None = None,
                  comparators: Sequence[dict] = (), error: str | None = None) -> Path:
    """Machine-readable helper lifecycle record: running, complete or failed."""
    work = DATA_ROOT / run_id
    work.mkdir(parents=True, exist_ok=True)
    path = work / "helper-state.json"
    payload = {"run_id": run_id, "status": status,
               "report": str(report) if report is not None else None,
               "comparators": [run.get("run_id") for run in comparators],
               "error": error, "updated": local_stamp()}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)
    return path


def load_comparators(paths: Path | Sequence[Path] | None) -> list[dict]:
    """Resolve explicit JSON records or prior world roots across frozen copies."""
    if paths is None:
        return []
    paths = [paths] if isinstance(paths, (str, Path)) else paths
    records = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            index = path.parent / "laya-runs.jsonl"
            candidates = []
            if index.is_file():
                with index.open(encoding="utf-8") as stream:
                    for line in stream:
                        if not line.strip():
                            continue
                        run = json.loads(line)
                        if Path(run.get("root", "")).resolve() == path.resolve():
                            candidates.append(run)
            if not candidates:
                raise ValueError(f"no indexed Laya run for comparator root {path}")
            records.append(max(candidates, key=lambda r: r.get("ended") or ""))
        else:
            payload = json.loads(path.read_text())
            if isinstance(payload, dict):
                payload = payload.get("comparators", [payload] if payload.get("run_id") else [])
            records.extend(dict(item) for item in payload)
    return records


def final_review(run_id: str, session: str | None = None, comparators: Sequence[dict] = ()) -> Path:
    """Full post-run comparison, in the observer's session when there is one."""
    config = load_config()
    runs = run_index()
    current = next(r for r in runs if r["run_id"] == run_id)
    previous = previous_run(current, runs, comparators)
    report, work = _paths(run_id)
    config["report_path"] = str(report.resolve())
    _header(report, run_id, "Review in progress")
    _write_record(run_id, "running", report=report, comparators=comparators)
    report_before = report.read_bytes()
    packet = work / "evidence-packet.md"
    packet.write_text(build_packet(current, previous, config, comparators))
    message = prompt(current, previous, packet, report, recent_commits(), comparators)
    if session:
        message += ("\nKeep the '## Live observations' section you wrote during the run; add the six sections after it "
                    "and say which live observations the final evidence confirmed or refuted.")
    try:
        _ask(config, work, message, session, f"Laya Helper {run_id}",
             "Continue the same read-only Laya helper review from where it stopped; finish the findings "
             f"document at {report}. Do not restart or create another report.")
        if not report.is_file() or report.read_bytes() == report_before:
            raise RuntimeError("findings document was not updated by the observer")
    except Exception as error:
        _write_record(run_id, "failed", report=report, comparators=comparators,
                      error=f"{type(error).__name__}: {str(error)[:300]}")
        raise
    _write_record(run_id, "complete", report=report, comparators=comparators)
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


def observe(run_id: str, log: Path, root: Path, interval: int = OBSERVE_INTERVAL_S, max_hours: float = 12,
            comparators: Sequence[dict] = ()) -> Path:
    """Live observer: checkpoint notes every ``interval`` seconds, then the final review in the same session."""
    config = load_config()
    report, work = _paths(run_id)
    config["report_path"] = str(report.resolve())
    _header(report, run_id, "Live observation in progress")
    _write_record(run_id, "running", report=report, comparators=comparators)
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
    return final_review(run_id, session, comparators)


def launch(run_id: str, log: Path, root: Path | None = None, comparators_path: Path | None = None) -> str:
    """Start the helper detached from the runner; with ``root`` it observes live from RUN START."""
    out = DATA_ROOT / run_id / "helper.log"
    out.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(Path(__file__)), "--run-id", run_id, "--log", str(log)]
    if root is not None:
        command += ["--observe-root", str(root)]
    if comparators_path is not None:
        command += ["--comparators", str(comparators_path)]
    with out.open("ab") as handle:
        process = subprocess.Popen(command, cwd=REPO_ROOT, stdin=subprocess.DEVNULL, stdout=handle,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    return f"pid={process.pid} log={out}"


def main(argv=None) -> int:
    global CONFIG, REPORT_ROOT, DATA_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--log", type=Path, help="Runner log (observed live, and the completion line is appended)")
    parser.add_argument("--config", type=Path, help="Explicit helper model/reference configuration")
    parser.add_argument("--report-root", type=Path, help="Campaign-owned findings directory")
    parser.add_argument("--data-root", type=Path, help="Campaign-owned helper state directory")
    parser.add_argument("--observe-root", type=Path, help="Episode root: observe live from RUN START, then review")
    parser.add_argument("--comparators", type=Path, action="append",
                        help="JSON list of explicit comparator run records (run_id/commit/root/kind)")
    parser.add_argument("--interval", type=int, default=OBSERVE_INTERVAL_S)
    parser.add_argument("--opencode-bin", default=shutil.which("opencode") or "opencode")
    args = parser.parse_args(argv)
    if args.config is not None:
        CONFIG = args.config.resolve()
    if args.report_root is not None:
        REPORT_ROOT = args.report_root.resolve()
    if args.data_root is not None:
        DATA_ROOT = args.data_root.resolve()
    OPENCODE[0] = args.opencode_bin
    try:
        comparators = load_comparators(args.comparators)
    except (OSError, ValueError, TypeError) as error:
        comparators = []
        print(f"laya helper: ignoring unreadable comparators: {error}", file=sys.stderr)
    try:
        if args.observe_root and args.log:
            report = observe(args.run_id, args.log, args.observe_root, args.interval, comparators=comparators)
        else:
            report = final_review(args.run_id, comparators=comparators)
        line = f"LAYA HELPER: findings complete {report}"
    except Exception as error:  # Reported in the runner log; never retried silently.
        try:
            _write_record(args.run_id, "failed", comparators=comparators,
                          error=f"{type(error).__name__}: {str(error)[:300]}")
        except OSError:
            pass
        line = f"LAYA HELPER: stopped after failure: {type(error).__name__}: {str(error)[:300]}"
    if args.log:
        with args.log.open("a") as out:
            out.write(line + "\n")
    print(line)
    return 0 if "complete" in line else 1


if __name__ == "__main__":
    raise SystemExit(main())
