# Path: tools/laya_run_retention.py | Purpose: Keep recent and exemplar Laya cohort runs whole; condense older runs to their fine-tuning data.
"""Run retention over a cohort registry (normally data/laya-experiments).

A run is one started job world of a cohort. The newest `keep_recent_runs` runs
on the host stay whole, as do each cohort's best and median runs. Every other
run of a terminal cohort is condensed: its decision records, evidence and
outcomes are copied, zstd-compressed and hash-verified, into the fine-tuning
corpus, then the world directory is replaced by a CONDENSED.json pointer.
Cohorts with queued, running, reserved or resumable (interrupted) jobs are never touched.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_KEEP_RECENT_RUNS = 300
MARKER = "CONDENSED.json"
# A cohort holding any of these can still run or be resumed in place (superset of Batch.RERUN).
ACTIVE = {"queued", "preparing", "running", "interrupted", "not_started_interrupted"}
COMPRESS_OVER = 16 * 1024
KEEP_NAMES = {
    "decision-states.jsonl", "clean-decisions.jsonl", "decisions.jsonl", "latest-status.json", "site-plan.json",
    "manifest.json", "report.json", "report.md", "freeze.json", "summary.json", "initial-state.json",
    "starter-kit-check.json", "final-accounting.json",
}
SKIP_DIRS = {"server", "mods", "frozen", "__pycache__", ".git", "saves", "script-output", "temp", ".pytest_cache"}
MILESTONE = re.compile(r"^C(\d+)$")


def keep(relative: str) -> bool:
    """Fine-tuning data inside a world: decision records, evidence, outcomes and their provenance."""
    parts = relative.strip("/").split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return False
    name = parts[-1]
    return ("evidence" in parts[:-1] or name in KEEP_NAMES or name.endswith((".jsonl", ".db"))
            or (name.startswith("failure-") and name.endswith(".json")))


def _level(name: str) -> int:
    match = MILESTONE.match(name)
    return int(match.group(1)) if match else -1


def score(job: dict) -> tuple:
    """Strict final milestones first, then proxy phases, then speed to the top phase, then fewer failures."""
    strict = job.get("accepted_final", job.get("accepted")) or []
    proxy = job.get("phases") or []
    top = max(proxy, key=_level, default=None)
    seconds = ((job.get("phase_times") or {}).get(top) or {}).get("game_seconds") if top else None
    return (max(map(_level, strict), default=-1), len(strict), max(map(_level, proxy), default=-1),
            -(seconds if seconds is not None else float("inf")), -(job.get("failures") or 0))


def started_runs(jobs: list[dict]) -> list[dict]:
    return [j for j in jobs if j.get("started") and j.get("world")]


def exemplars(jobs: list[dict]) -> dict[str, str]:
    """The cohort's best and median (lower middle) run ids under `score`."""
    ranked = sorted(started_runs(jobs), key=lambda j: (score(j), j["id"]))
    return {"best": ranked[-1]["id"], "median": ranked[(len(ranked) - 1) // 2]["id"]} if ranked else {}


def _finished(job: dict) -> float:
    return float(job.get("runner_exited") or (job.get("started") or 0) + (job.get("elapsed_s") or 0))


def plan(registry: Path, keep_recent_runs: int = DEFAULT_KEEP_RECENT_RUNS) -> list[dict]:
    """Runs to condense: older than the newest `keep_recent_runs`, not an exemplar, in a terminal cohort."""
    runs = []
    for status_path in sorted(registry.glob("*/job-status.json")):
        cohort = status_path.parent
        if cohort.name.startswith("_"):
            continue
        try:
            jobs = json.loads(status_path.read_text()).get("jobs") or []
        except (OSError, ValueError):
            continue
        terminal = not any(j.get("status") in ACTIVE or j.get("admission_reserved") for j in jobs)
        protected = set(exemplars(jobs).values())
        for job in started_runs(jobs):
            runs.append({"cohort": cohort, "job": job, "terminal": terminal, "exemplar": job["id"] in protected})
    runs.sort(key=lambda r: _finished(r["job"]), reverse=True)
    selected = []
    for run in runs[keep_recent_runs:]:
        world = Path(run["job"]["world"])
        if (run["terminal"] and not run["exemplar"] and world.is_dir() and not (world / MARKER).exists()
                and world.resolve().is_relative_to(run["cohort"].resolve())):
            selected.append(run)
    return selected


def _zstd(data: bytes, *args: str) -> bytes:
    return subprocess.run(["zstd", "-q", *args, "-c"], input=data, stdout=subprocess.PIPE, check=True).stdout


def _rmtree(path: Path) -> None:
    """Remove a tree, including read-only directories left by Factorio and frozen copies."""
    def retry(func, target, _error):
        os.chmod(os.path.dirname(target), stat.S_IRWXU)
        if os.path.isdir(target) and not os.path.islink(target):
            os.chmod(target, stat.S_IRWXU)
        func(target)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def condense(world: Path, data_root: Path, corpus: Path) -> dict:
    """Copy a world's fine-tuning files into `corpus/<path under data>` and replace the world with a pointer."""
    key = str(world.relative_to(data_root)).replace("/", "__")
    rows = []
    for walk, dirs, files in os.walk(world):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            path = Path(walk) / name
            relative = str(path.relative_to(data_root))
            if path.is_symlink() or not path.is_file() or not keep(str(path.relative_to(world))):
                continue
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            out, payload = corpus / relative, data
            if len(data) > COMPRESS_OVER:
                payload = _zstd(data, "-12")
                if hashlib.sha256(_zstd(payload, "-d")).hexdigest() != digest:
                    raise RuntimeError(f"zstd round trip failed for {relative}")
                out = out.with_name(out.name + ".zst")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(payload)
            rows.append({"path": relative, "bytes": len(data), "sha256": digest, "stored": out.name,
                         "stored_bytes": len(payload),
                         "lines": data.count(b"\n") if name.endswith(".jsonl") else None})
    index = corpus / "index" / f"{key}.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("".join(json.dumps(r) + "\n" for r in rows))
    pointer = {"version": "laya-run-retention-v1", "corpus": str(corpus), "index": str(index), "files": len(rows),
               "bytes": sum(r["bytes"] for r in rows),
               "decision_records": sum(r["lines"] or 0 for r in rows if r["path"].endswith("decision-states.jsonl")),
               "condensed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    # Pointer first: a deletion interrupted after this point is never condensed again from partial files.
    (world / MARKER).write_text(json.dumps(pointer, indent=1) + "\n")
    with (corpus / "sources.jsonl").open("a") as ledger:
        ledger.write(json.dumps({"source": str(world), "kind": "run", **pointer}) + "\n")
    for child in world.iterdir():
        if child.name != MARKER:
            _rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
    return pointer


def default_corpus(registry: Path) -> Path:
    return registry.resolve().parent / "laya-finetune-corpus" / socket.gethostname()


def apply(registry: Path, *, keep_recent_runs: int = DEFAULT_KEEP_RECENT_RUNS, corpus: Path | None = None,
          dry_run: bool = False) -> dict:
    """One retention pass. Holds a registry lock so concurrent cohort controllers do not race."""
    registry = registry.resolve()
    corpus = corpus or default_corpus(registry)
    with (registry / ".retention.lock").open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"skipped": "another retention pass holds the lock"}
        selected = plan(registry, keep_recent_runs)
        if dry_run:
            return {"would_condense": [str(r["job"]["world"]) for r in selected]}
        condensed = [condense(Path(r["job"]["world"]), registry.parent, corpus) for r in selected]
        return {"condensed": len(condensed), "corpus": str(corpus)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", type=Path, default=Path("data/laya-experiments"))
    parser.add_argument("--keep-recent-runs", type=int, default=DEFAULT_KEEP_RECENT_RUNS)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(apply(args.registry, keep_recent_runs=args.keep_recent_runs, corpus=args.corpus,
                           dry_run=args.dry_run), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
