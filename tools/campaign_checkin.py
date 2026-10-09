# Path: tools/campaign_checkin.py | Purpose: Record bounded Laya campaign oversight and resume an interrupted loop within its original deadline.
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
MAX_JSON_BYTES = 1_000_000
MAX_EVIDENCE_FILES = 2000
EVIDENCE_NAMES = {"freeze.json", "findings.md", "heartbeat", "heartbeat.json",
                  "laya-runner.heartbeat", "laya-runner.heartbeat.json", "latest-status.json"}


def _json(path: Path) -> dict:
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _run(command: list[str], *, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def _evidence_snapshot(run_dir: Path) -> dict[str, dict]:
    snapshot: dict[str, dict] = {}
    roots = [run_dir]
    roots.extend(path for name in ("runs", "episodes", "agents", "evidence")
                 if (path := run_dir / name).is_dir())
    for root in roots:
        try:
            paths = root.rglob("*") if root != run_dir else root.iterdir()
            for path in paths:
                if len(snapshot) >= MAX_EVIDENCE_FILES:
                    return snapshot
                if path.is_file() and path.name in EVIDENCE_NAMES:
                    try:
                        stat = path.stat()
                        snapshot[str(path.relative_to(run_dir))] = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
                    except OSError:
                        continue
        except OSError:
            continue
    return snapshot


def _run_ids(run_dir: Path) -> list[str]:
    ids = set()
    for dirname in ("runs", "episodes"):
        root = run_dir / dirname
        try:
            for child in root.iterdir():
                if child.is_dir():
                    ids.add(f"{dirname}/{child.name}")
                    if len(ids) >= MAX_EVIDENCE_FILES:
                        return sorted(ids)
        except OSError:
            pass
    return sorted(ids)


def _git_state(worktree: Path, runner) -> dict:
    count = runner(["git", "-C", str(worktree), "rev-list", "--count", "HEAD"], timeout=20)
    head = runner(["git", "-C", str(worktree), "log", "-1", "--format=%h %s"], timeout=20)
    try:
        commits = int(count.stdout.strip()) if count.returncode == 0 else None
    except ValueError:
        commits = None
    return {"commit_count": commits, "head": head.stdout.strip() if head.returncode == 0 else "unavailable"}


def _memory_load(runner) -> tuple[str, str]:
    memory = runner(["free", "-g"], timeout=10)
    load = runner(["uptime"], timeout=10)
    return (memory.stdout.strip()[:1200] if memory.returncode == 0 else "unavailable",
            load.stdout.strip()[:500] if load.returncode == 0 else "unavailable")


def checkin(settings_path: Path, unit: str, run_dir: Path, queue_note: Path, worktree: Path,
            *, runner=_run, now=time.time) -> dict:
    if not unit.startswith("laya-loop-") or not unit.endswith(".service") or "/" in unit:
        raise ValueError("unit must be an exact laya-loop-*.service name")
    settings = _json(settings_path)
    if settings.get("runtime") != "laya":
        raise ValueError("campaign settings runtime must be 'laya'")
    deadline = settings.get("deadline_epoch")
    if not isinstance(deadline, (int, float)):
        raise ValueError("campaign settings must contain the original deadline_epoch")

    previous_path = run_dir / "last-checkin.json"
    previous = _json(previous_path)
    prior_evidence = previous.get("evidence", {})
    prior_runs = set(previous.get("run_ids", []))
    current_evidence = _evidence_snapshot(run_dir)
    current_runs = _run_ids(run_dir)
    changed_evidence = sorted(name for name, data in current_evidence.items()
                              if prior_evidence.get(name) != data)
    new_runs = sorted(set(current_runs) - prior_runs)

    systemd = runner(["systemctl", "--user", "is-active", unit], timeout=20)
    active = systemd.returncode == 0 and systemd.stdout.strip() == "active"
    checked_at = now()
    restarted = False
    recovery_error = ""
    if not active and checked_at < deadline:
        started = runner(["systemctl", "--user", "start", unit], timeout=30)
        if started.returncode:
            recovery_error = f"start failed: {started.stderr[-800:].strip() or 'systemctl returned nonzero'}"
        else:
            restarted = True
            verified = runner(["systemctl", "--user", "is-active", unit], timeout=20)
            active = verified.returncode == 0 and verified.stdout.strip() == "active"
            if not active:
                recovery_error = (f"start returned success but unit remained {verified.stdout.strip() or 'inactive'}"
                                  + (f": {verified.stderr[-500:].strip()}" if verified.stderr.strip() else ""))
    controller = _json(run_dir / "controller.json")
    progress = _json(run_dir / "progress.json")
    status = _json(run_dir / "status.json")
    memory, load = _memory_load(runner)
    git = _git_state(worktree, runner)
    old_commits = previous.get("commit_count")
    commits_added = (git["commit_count"] - old_commits
                     if isinstance(old_commits, int) and isinstance(git["commit_count"], int)
                     else None)
    stamp = datetime.fromtimestamp(checked_at, IST).strftime("%Y-%m-%d %H:%M:%S IST")
    phase = progress.get("phase", status.get("phase", controller.get("phase", "unknown")))
    heartbeat_count = sum("heartbeat" in name for name in current_evidence)
    finding_count = sum(name.endswith("findings.md") for name in current_evidence)
    changed_findings = sum(name.endswith("findings.md") for name in changed_evidence)
    changed_heartbeats = sum("heartbeat" in name for name in changed_evidence)
    evidence_progress = bool(changed_evidence or new_runs or (commits_added and commits_added > 0))
    note = (f"## {stamp}\n"
            f"- Unit `{unit}`: {'active' if active else 'inactive'}; phase={phase}; "
            f"deadline={'remaining' if checked_at < deadline else 'reached'}; resumed={str(restarted).lower()}.\n"
            f"- Evidence: {len(current_runs)} run dirs (+{len(new_runs)}: {', '.join(new_runs[:6]) or 'none'}); "
            f"findings={finding_count} ({changed_findings} new/updated); heartbeats={heartbeat_count} "
            f"({changed_heartbeats} new/updated); {len(changed_evidence)} evidence files changed; "
            f"commits={git['commit_count']} (+{commits_added if commits_added is not None else 'n/a'}); head={git['head']}; "
            f"evidence_progress={'yes' if evidence_progress else 'none (observe only; no stop action)'}.\n"
            f"- Controller/progress/status: {json.dumps({'controller': controller, 'progress': progress, 'status': status}, sort_keys=True)[:1800]}\n"
            f"- Recovery: {recovery_error or 'none'}.\n"
            f"- Memory/load: free -g: {memory.replace(chr(10), ' | ')}; uptime: {load}.\n")
    run_notes = run_dir / "progress-notes.md"
    run_notes.parent.mkdir(parents=True, exist_ok=True)
    queue_note.parent.mkdir(parents=True, exist_ok=True)
    for path in (run_notes, queue_note):
        with path.open("a", encoding="utf-8") as stream:
            stream.write(note + "\n")
    _atomic_json(previous_path, {"checked_at": checked_at, "evidence": current_evidence,
                                 "run_ids": current_runs, "commit_count": git["commit_count"],
                                 "active": active, "resumed": restarted, "recovery_error": recovery_error})
    return {"active": active, "resumed": restarted, "at_deadline": checked_at >= deadline,
            "new_runs": new_runs, "changed_evidence": changed_evidence,
            "commit_count": git["commit_count"], "note": note, "recovery_error": recovery_error}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Append one bounded Laya campaign oversight check-in")
    parser.add_argument("--settings", required=True, type=Path)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--queue-note", required=True, type=Path)
    parser.add_argument("--worktree", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = checkin(args.settings, args.unit, args.run_dir, args.queue_note, args.worktree)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"campaign check-in failed: {error}", file=sys.stderr)
        return 2
    print(result["note"], end="")
    return 1 if result["recovery_error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
