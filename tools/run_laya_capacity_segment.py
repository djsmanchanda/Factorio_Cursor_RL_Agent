# Path: tools/run_laya_capacity_segment.py | Purpose: Admit transferred, unstarted jobs while preserving a draining predecessor and shared arm limits.
"""Use the unchanged batch runner with explicit transfer and capacity ownership."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.run_laya_batch import Batch
from tools.laya_batch_state import planned_jobs, read_records


def occupied(jobs, arm):
    return sum(j["arm"] == arm and (bool(j.get("admission_reserved"))
               or j["status"] in {"preparing", "running"}) for j in jobs)


def validate_transfer(cfg, root):
    transfer = cfg["capacity_transfer"]
    source = Path(transfer["source_registry"]).resolve()
    if source == root.resolve():
        raise ValueError("capacity segment must have a separate registry")
    current = json.loads((source / "job-status.json").read_text())
    if current.get("admission") != "drain":
        raise ValueError("source must remain drained")
    prior = {j["id"]: j for j in current["jobs"]}
    cut = next((e for e in read_records(source / "batch-events.jsonl")
                if e.get("event_id") == "request:" + transfer["request_id"]), None)
    if cut is None or cut["request"]["mode"] != "cancel_queued":
        raise ValueError("source has not acknowledged the queued transfer")
    assigned = set(transfer["job_ids"])
    if len(assigned) != len(transfer["job_ids"]) or assigned != {j["id"] for j in planned_jobs(cfg)}:
        raise ValueError("segment plan differs from transferred IDs")
    if not assigned <= set(cut["cuts"]):
        raise ValueError("transfer lacks an acknowledged source cut")
    for job_id in assigned:
        job = prior[job_id]
        if (job["status"] != "cancelled_queued" or job.get("admission_reserved")
                or any(job.get(k) for k in ("started", "preparation_started", "world", "previous_attempts"))):
            raise ValueError("only never-started released jobs may transfer: " + job_id)
    roots = [Path(p).resolve() for p in transfer["capacity_group_roots"]]
    if len(set(roots)) != len(roots) or root.resolve() not in roots or source not in roots:
        raise ValueError("capacity group must uniquely include source and segment")
    # An assignment is immutable and disjoint even before another segment starts.
    assigned_elsewhere = set()
    for other in roots:
        if other in {source, root.resolve()}:
            continue
        other_cfg = json.loads((other / "batch.json").read_text())
        for job in planned_jobs(other_cfg):
            if job["id"] in assigned or job["id"] in assigned_elsewhere:
                raise ValueError("duplicate transferred job across capacity segments")
            assigned_elsewhere.add(job["id"])


class CapacitySegment(Batch):
    def arm_available(self, job):
        if not super().arm_available(job):
            return False
        transfer = self.cfg["capacity_transfer"]
        total = occupied(self.jobs, job["arm"])
        try:
            for value in transfer["capacity_group_roots"]:
                other = Path(value).resolve()
                if other == self.root.resolve():
                    continue
                path = other / "job-status.json"
                if path.exists():
                    total += occupied(json.loads(path.read_text())["jobs"], job["arm"])
            self.capacity_error = None
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.capacity_error = str(error)
            return False
        # submit() checks this again under the existing host-wide admission lock.
        return total < transfer["per_arm_cap"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text())
    validate_transfer(cfg, args.config.parent)
    CapacitySegment(args.config).run()


if __name__ == "__main__":
    main()
