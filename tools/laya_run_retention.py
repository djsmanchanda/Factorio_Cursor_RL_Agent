# Path: tools/laya_run_retention.py | Purpose: Keep recent and exemplar Laya cohort runs whole; condense older runs to their fine-tuning data.
"""Run retention over a cohort registry (normally data/laya-experiments).

A run is one started job world of a cohort. The newest `keep_recent_runs` runs
on the host stay whole, as do each cohort's best and median runs. Every other
run of a terminal cohort is condensed: its decision records, evidence and
outcomes are copied, zstd-compressed and hash-verified, into the fine-tuning
corpus, then the world directory is replaced by a CONDENSED.json pointer.
Cohorts with queued, running, reserved or resumable (interrupted) jobs are never touched.

The `zstd` command must be on PATH (the Python 3.11 campaign venv has no
`compression.zstd` module); `apply` preflights it before copying anything.
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
    strict = job.get("accepted_final")
    if strict is None:  # unknown final only; an explicit [] is a known regression and never falls back
        strict = job.get("accepted") or []
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


def _load_jobs(status_path: Path) -> list[dict]:
    """Read either {"jobs": [...]} or the legacy bare list; skip malformed entries and missing ids."""
    try:
        data = json.loads(status_path.read_text())
    except (OSError, ValueError):
        return []
    jobs = data.get("jobs") if isinstance(data, dict) else data
    if not isinstance(jobs, list):
        return []
    return [job for job in jobs if isinstance(job, dict) and isinstance(job.get("id"), str) and job["id"]]


def run_world_ok(cohort: Path, job: dict) -> bool:
    """True only for this job's canonical run world under the cohort's arm code.

    The only accepted shape is
    `<cohort>/arms/<arm>/code/data/laya-saved-world/<cohort>/<job-id>`
    (or its resume rerun `<job-id>-r<attempt>`). Rejects ancestors such as the
    cohort root or arm code, anything outside the cohort, and any tree that
    holds cohort state (`job-status.json`/`batch.json`) so a stale or malicious
    status entry can never select a non-run tree for deletion.
    """
    raw = job.get("world")
    if not isinstance(raw, str) or not raw:
        return False
    world = Path(raw)
    if world.is_symlink():
        return False
    try:
        world, cohort = world.resolve(), cohort.resolve()
        parts = world.relative_to(cohort).parts
    except (OSError, ValueError):
        return False
    expected = {job["id"]}
    if job.get("attempt"):
        expected.add(f"{job['id']}-r{job['attempt']}")
    if (len(parts) != 7 or parts[0] != "arms" or parts[2] != "code"
            or parts[3:6] != ("data", "laya-saved-world", cohort.name) or parts[6] not in expected):
        return False
    if not world.is_dir() or (world / MARKER).exists() or _holds_cohort_state(world):
        return False
    for parent in world.parents:
        if parent == cohort:
            break
        if (parent / "job-status.json").exists() or (parent / "batch.json").exists():
            return False
    return True


def plan(registry: Path, keep_recent_runs: int = DEFAULT_KEEP_RECENT_RUNS) -> list[dict]:
    """Runs to condense: older than the newest `keep_recent_runs`, not an exemplar, in a terminal cohort."""
    if keep_recent_runs < 0:
        raise ValueError("keep_recent_runs must be >= 0")
    runs = []
    for status_path in sorted(registry.glob("*/job-status.json")):
        cohort = status_path.parent
        if cohort.name.startswith("_"):
            continue
        jobs = _load_jobs(status_path)
        terminal = not any(j.get("status") in ACTIVE or j.get("admission_reserved") for j in jobs)
        protected = set(exemplars(jobs).values())
        for job in started_runs(jobs):
            runs.append({"cohort": cohort, "job": job, "terminal": terminal, "exemplar": job["id"] in protected})
    runs.sort(key=lambda r: _finished(r["job"]), reverse=True)
    selected = []
    for run in runs[keep_recent_runs:]:
        if run["terminal"] and not run["exemplar"] and run_world_ok(run["cohort"], run["job"]):
            selected.append(run)
    return selected


def _still_selected(run: dict) -> bool:
    """Re-read the whole cohort status: fail closed if it, the chosen run, or the exemplars changed."""
    jobs = _load_jobs(run["cohort"] / "job-status.json")
    if any(j.get("status") in ACTIVE or j.get("admission_reserved") for j in jobs):
        return False
    current = next((j for j in jobs if j.get("id") == run["job"]["id"]), None)
    if (current is None or not current.get("started") or current.get("world") != run["job"].get("world")
            or current.get("status") in ACTIVE or current.get("admission_reserved")):
        return False
    return current["id"] not in set(exemplars(jobs).values())


def _require_zstd() -> None:
    if shutil.which("zstd") is None:
        raise RuntimeError("run retention requires the zstd CLI on PATH (install zstd; Python 3.11 lacks compression.zstd)")


def _publish(path: Path, data: bytes) -> None:
    """Write `data` durably: temp file, fsync, atomic replace, then fsync the directory."""
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def _fsync_dir(path: Path) -> None:
    """Make a directory entry durable; failure is fatal before any source deletion."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _mkdir(path: Path) -> None:
    """Create `path` and durably fsync every parent whose entry this creates."""
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    path.mkdir(parents=True, exist_ok=True)
    for created in reversed(missing):
        _fsync_dir(created.parent)


def _read_index(index: Path) -> list[dict] | None:
    """Parse an existing index strictly; None means no index, corruption raises."""
    try:
        lines = index.read_text().splitlines()
    except FileNotFoundError:
        return None
    rows, seen = [], set()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            raise ValueError(f"corrupt retention index {index}: blank line {number}")
        try:
            row = json.loads(line)
        except ValueError as error:
            raise ValueError(f"corrupt retention index {index} line {number}: {error}") from error
        if not isinstance(row, dict):
            raise ValueError(f"corrupt retention index {index} line {number}: not an object")
        if (not isinstance(row.get("path"), str) or not row["path"] or Path(row["path"]).is_absolute()
                or ".." in Path(row["path"]).parts or not isinstance(row.get("stored"), str)
                or not row["stored"] or Path(row["stored"]).name != row["stored"]
                or type(row.get("bytes")) is not int or row["bytes"] < 0
                or type(row.get("stored_bytes")) is not int or row["stored_bytes"] < 0
                or not isinstance(row.get("sha256"), str) or len(row["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in row["sha256"])):
            raise ValueError(f"corrupt retention index {index} line {number}: invalid row")
        if row["path"] in seen:
            raise ValueError(f"corrupt retention index {index}: duplicate path {row['path']}")
        seen.add(row["path"])
        rows.append(row)
    return rows


def _collect_sources(world: Path, data_root: Path) -> list[tuple[dict, bytes]]:
    """Retained fine-tuning files in `world`, with their hashes and bytes."""
    sources = []
    for walk, dirs, files in os.walk(world):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            path = Path(walk) / name
            if path.is_symlink() or not path.is_file() or not keep(str(path.relative_to(world))):
                continue
            data = path.read_bytes()
            sources.append(({"path": str(path.relative_to(data_root)), "bytes": len(data),
                             "sha256": hashlib.sha256(data).hexdigest(),
                             "lines": data.count(b"\n") if name.endswith(".jsonl") else None}, data))
    return sources


def _verify_index(corpus: Path, prefix: str, rows: list[dict], sources: list[tuple[dict, bytes]]) -> None:
    """Every indexed stored file must decode to its recorded bytes/hash, and current sources must be an unchanged subset."""
    by_path = {}
    for row in rows:
        if not row["path"].startswith(prefix + "/"):
            raise ValueError(f"index row belongs to another world: {row['path']}")
        path = corpus / row["path"]
        stored = path.with_name(row["stored"])
        try:
            payload = stored.read_bytes()
        except OSError as error:
            raise RuntimeError(f"indexed corpus file missing: {stored}") from error
        if len(payload) != row["stored_bytes"]:
            raise RuntimeError(f"indexed corpus file size changed: {stored}")
        data = _zstd(payload, "-d") if row["stored"].endswith(".zst") else payload
        if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise RuntimeError(f"indexed corpus file corrupt: {stored}")
        by_path[row["path"]] = row
    for current, _data in sources:
        indexed = by_path.get(current["path"])
        if indexed is None:
            raise RuntimeError(f"new source file is not in the complete index: {current['path']}")
        if indexed["bytes"] != current["bytes"] or indexed["sha256"] != current["sha256"]:
            raise RuntimeError(f"source file changed after indexing: {current['path']}")


def _ledger_has(corpus: Path, source: str) -> bool:
    try:
        lines = (corpus / "sources.jsonl").read_text().splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            if json.loads(line).get("source") == source:
                return True
        except (ValueError, AttributeError):
            continue
    return False


def _append_source(corpus: Path, record: dict) -> None:
    """Append one ledger row under an exclusive lock, flushed to disk before any deletion."""
    path = corpus / "sources.jsonl"
    _mkdir(path.parent)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(json.dumps(record) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle, fcntl.LOCK_UN)
    _fsync_dir(path.parent)


def _zstd(data: bytes, *args: str) -> bytes:
    _require_zstd()
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


def _holds_cohort_state(tree: Path) -> bool:
    """True when a job-status or batch file exists anywhere under `tree`; symlinked descendants are not followed."""
    for walk, dirs, files in os.walk(tree):
        dirs[:] = [d for d in dirs if not (Path(walk) / d).is_symlink()]
        if "job-status.json" in files or "batch.json" in files:
            return True
    return False


def condense(world: Path, data_root: Path, corpus: Path) -> dict:
    """Copy a world's fine-tuning files into `corpus/<path under data>` and replace the world with a pointer."""
    world = Path(world)
    if world.is_symlink():
        raise ValueError(f"refusing symlinked run world: {world}")
    world, data_root = world.resolve(), data_root.resolve()
    if world.parent.parent.name != "laya-saved-world" or world.parent.parent.parent.name != "data":
        raise ValueError(f"refusing non-run world: {world}")
    if _holds_cohort_state(world):
        raise ValueError(f"refusing world holding cohort state: {world}")
    if (world / MARKER).exists():
        raise ValueError(f"already condensed: {world}")
    key = str(world.relative_to(data_root)).replace("/", "__")
    index = corpus / "index" / f"{key}.jsonl"
    # Read any previous complete index before touching the corpus. A crash may
    # have left the world only partly deleted; its survivors must be verified
    # against that index and the index reused verbatim.
    rows = _read_index(index)
    sources = _collect_sources(world, data_root)
    if rows is not None:
        _verify_index(corpus, str(world.relative_to(data_root)), rows, sources)
    else:
        rows = []
        for row, data in sources:
            out, payload = corpus / row["path"], data
            if len(data) > COMPRESS_OVER:
                payload = _zstd(data, "-12")
                if hashlib.sha256(_zstd(payload, "-d")).hexdigest() != row["sha256"]:
                    raise RuntimeError(f"zstd round trip failed for {row['path']}")
                out = out.with_name(out.name + ".zst")
            _mkdir(out.parent)
            _publish(out, payload)
            rows.append({**row, "stored": out.name, "stored_bytes": len(payload)})
        _mkdir(index.parent)
        _publish(index, "".join(json.dumps(r) + "\n" for r in rows).encode())
    pointer = {"version": "laya-run-retention-v1", "corpus": str(corpus), "index": str(index), "files": len(rows),
               "bytes": sum(r["bytes"] for r in rows),
               "decision_records": sum(r["lines"] or 0 for r in rows if r["path"].endswith("decision-states.jsonl")),
               "condensed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    # Corpus, index and ledger row are durable before the pointer; deletion follows the pointer.
    # A crash before the pointer leaves the world whole and a retry appends no duplicate ledger row.
    source = str(world)
    if not _ledger_has(corpus, source):
        _append_source(corpus, {"source": source, "kind": "run", **pointer})
    _publish(world / MARKER, (json.dumps(pointer, indent=1) + "\n").encode())
    for child in world.iterdir():
        if child.name != MARKER:
            _rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
    return pointer


def default_corpus(registry: Path) -> Path:
    return registry.resolve().parent / "laya-finetune-corpus" / socket.gethostname()


def apply(registry: Path, *, keep_recent_runs: int = DEFAULT_KEEP_RECENT_RUNS, corpus: Path | None = None,
          dry_run: bool = False) -> dict:
    """One retention pass. Holds a registry lock so concurrent cohort controllers do not race."""
    if keep_recent_runs < 0:
        raise ValueError("keep_recent_runs must be >= 0")
    registry = registry.resolve()
    corpus = corpus or default_corpus(registry)
    with (registry / ".retention.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"skipped": "another retention pass holds the lock"}
        selected = plan(registry, keep_recent_runs)
        if dry_run:
            return {"would_condense": [str(r["job"]["world"]) for r in selected]}
        if selected:
            _require_zstd()
        condensed = []
        for run in selected:
            # Re-read the whole cohort and revalidate immediately before the
            # deletion-capable step: status, chosen run or exemplars may have changed.
            if _still_selected(run) and run_world_ok(run["cohort"], run["job"]):
                condensed.append(condense(Path(run["job"]["world"]), registry.parent, corpus))
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
