# Path: tools/laya_batch_state.py | Purpose: Persist cohort plans, requests and attempt receipts without replacing earlier evidence.
"""Small file protocol for run_laya_batch; no process or scheduler policy lives here."""
from __future__ import annotations

import fcntl
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: Path, *, blocking: bool = True):
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def read_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    # A torn receipt must be reconciled, never silently discarded on recovery.
    records = [json.loads(line) for line in path.read_text().splitlines()]
    if any(not isinstance(row, dict) for row in records):
        raise ValueError(f"invalid records: {path}")
    return records


def append_record(path: Path, row: dict) -> None:
    with path.open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def request_admission(root: Path, *, request_id: str, mode: str, reason: str,
                      job_ids: list[str] | None = None) -> None:
    """Queue an idempotent operator request; only the owner changes job state."""
    if mode not in {"run", "drain", "cancel_queued"} or not request_id or not reason:
        raise ValueError("admission request requires a mode, request ID and reason")
    if job_ids is not None and (mode != "cancel_queued" or len(job_ids) != len(set(job_ids))):
        raise ValueError("unique job IDs are only valid for cancel_queued")
    row = {"request_id": request_id, "mode": mode, "reason": reason, "job_ids": job_ids}
    with file_lock(root / ".admission-requests.lock"):
        path = root / "admission-requests.jsonl"
        for previous in read_records(path):
            if previous.get("request_id") == request_id:
                if previous != row:
                    raise ValueError("request ID already used with different content")
                return
        append_record(path, row)


def planned_jobs(cfg: dict) -> list[dict]:
    arms = cfg["arms"]
    jobs = [{"id": f"{arm['id']}-s{seed}", "arm": arm["id"], "seed": seed, "status": "queued"}
            for index, seed in enumerate(cfg["seeds"])
            for arm in (arms[index % len(arms):] + arms[:index % len(arms)])]
    if not jobs or len({j["id"] for j in jobs}) != len(jobs):
        raise ValueError("cohort needs nonempty unique planned job IDs")
    return jobs


class CohortLedger:
    def __init__(self, root: Path, cfg: dict) -> None:
        self.root = root
        self.lock = threading.RLock()
        path = root / "planned-jobs.json"
        self.legacy_cuts = []
        if path.exists():
            self.plan = json.loads(path.read_text())
        else:
            jobs = planned_jobs(cfg)
            status = root / "job-status.json"
            if status.exists():
                jobs += [j for j in json.loads(status.read_text())["jobs"]
                         if all(k in j for k in ("id", "arm", "seed"))]
            # Old live cohorts shortened batch.json and kept a separate cut file.
            cuts = root / "cut-jobs.json"
            if cuts.exists():
                cut = json.loads(cuts.read_text())
                self.legacy_cuts = cut["jobs"] if isinstance(cut, dict) else cut
                jobs += self.legacy_cuts
            unique = {}
            for job in jobs:
                unique[job["id"]] = {k: job[k] for k in ("id", "arm", "seed")}
            self.plan = {"version": "laya-batch-plan-v1", "jobs": list(unique.values())}
        ids = [j["id"] for j in self.plan["jobs"]]
        if len(ids) != len(set(ids)) or not set(j["id"] for j in planned_jobs(cfg)) <= set(ids):
            raise ValueError("manifest differs from immutable planned IDs; record a new cohort")
        self.events = read_records(root / "batch-events.jsonl")
        if any(row.get("sequence") != i + 1 for i, row in enumerate(self.events)):
            raise ValueError("noncontiguous cohort ledger")
        if len({r["event_id"] for r in self.events}) != len(self.events):
            raise ValueError("duplicate cohort event IDs")

    def initialize(self) -> None:
        with self.lock:
            self._initialize()

    def _initialize(self) -> None:
        path = self.root / "planned-jobs.json"
        if not path.exists():
            with path.open("x") as handle:
                json.dump(self.plan, handle, indent=1)
                handle.flush()
                os.fsync(handle.fileno())

    def append(self, event_id: str, event: str, *, at: float, **fields) -> dict:
        with self.lock:
            return self._append(event_id, event, at=at, **fields)

    def _append(self, event_id: str, event: str, *, at: float, **fields) -> dict:
        previous = next((r for r in self.events if r["event_id"] == event_id), None)
        if previous is not None:
            return previous
        self.initialize()
        row = json.loads(json.dumps({"version": "laya-batch-event-v1", "sequence": len(self.events) + 1,
                                    "event_id": event_id, "event": event, "at": at, **fields}))
        append_record(self.root / "batch-events.jsonl", row)
        self.events.append(row)
        return row


def dependencies_ready(roots: list[str], *, own_root: Path) -> tuple[bool, str]:
    """Terminal predecessor attempts release no policy/adoption authority."""
    for value in roots:
        root = Path(value).resolve()
        if root == own_root.resolve():
            raise ValueError("cohort cannot depend on itself")
        try:
            data = json.loads((root / "job-status.json").read_text())
            jobs = data["jobs"]
            if (not jobs or any(j.get("status") in {"queued", "preparing", "running"}
                                or j.get("admission_reserved") for j in jobs)):
                return False, f"waiting_for_predecessor:{root.name}"
            # A missing/unknown status cannot be interpreted as completion.
            terminal = {"completed", "runner_error", "harness_error", "prepare_failed", "wall_cap",
                        "deadline_cap", "interrupted", "not_started_deadline",
                        "not_started_interrupted", "cancelled_queued"}
            terminal.add("incompatible_checkpoint")
            if any(j.get("status") not in terminal for j in jobs):
                return False, f"unknown_predecessor:{root.name}"
        except (OSError, ValueError, KeyError, TypeError):
            return False, f"unavailable_predecessor:{root.name}"
    return True, "ready"
