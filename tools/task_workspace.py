# Path: tools/task_workspace.py | Purpose: Inspect Git state and create isolated task worktrees.
"""Small local-only helper; run from any directory inside the source worktree."""

import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    # Status must not refresh the index; missing objects must not trigger a fetch.
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"}
    return subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", *args],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check,
    )


def output(*args: str) -> str:
    return os.fsdecode(git(*args).stdout).strip()


def status() -> dict:
    head = output("rev-parse", "--verify", "HEAD")
    branch_result = git("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    branch = os.fsdecode(branch_result.stdout).strip() if branch_result.returncode == 0 else None
    records = iter(git("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout.split(b"\0"))
    dirty_paths = set()
    for record in records:
        if not record:
            continue
        dirty_paths.add(os.fsdecode(record[3:]))
        if b"R" in record[:2] or b"C" in record[:2]:
            dirty_paths.add(os.fsdecode(next(records)))
    upstream_result = git("rev-parse", "--symbolic-full-name", "@{upstream}", check=False)
    upstream = None
    ahead = behind = None
    if upstream_result.returncode == 0:
        upstream = os.fsdecode(upstream_result.stdout).strip()
        counts = git("rev-list", "--left-right", "--count", "HEAD...@{upstream}", check=False)
        if counts.returncode == 0:
            ahead, behind = map(int, counts.stdout.split())
    return {"hostname": socket.gethostname(), "branch": branch, "head": head,
            "dirty_paths": sorted(dirty_paths), "upstream": upstream,
            "ahead": ahead, "behind": behind}


def label(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise argparse.ArgumentTypeError("use lowercase letters/digits separated by single hyphens")
    return value


def create(base: str, machine: str, task: str, path: str) -> dict:
    machine, task = label(machine), label(task)
    branch = f"work/{machine}/{task}"
    commit = output("rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}")
    # Git permits existing empty directories; this helper deliberately refuses them.
    target = Path(os.path.abspath(os.path.expanduser(path)))
    if os.path.lexists(target):
        raise ValueError(f"target already exists: {target}")
    git("check-ref-format", "--branch", branch)
    # -b (never -B/--force) lets Git atomically reject an existing branch.
    git("worktree", "add", "-b", branch, "--", str(target), commit)
    return {"branch": branch, "head": commit, "path": str(target)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="print read-only local Git status as JSON")
    new = commands.add_parser("create", help="create a task branch/worktree at an explicit base")
    new.add_argument("--base", required=True)
    new.add_argument("--machine", required=True, type=label)
    new.add_argument("--task", required=True, type=label)
    new.add_argument("--path", required=True)
    args = parser.parse_args()
    try:
        result = status() if args.command == "status" else create(args.base, args.machine, args.task, args.path)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        message = os.fsdecode(exc.stderr).strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        parser.exit(1, f"task_workspace: {message}\n")
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
