# Path: tests/test_laya_capacity_segment.py | Purpose: Protect transfer uniqueness and aggregate admission limits across a draining predecessor.
import json
from pathlib import Path

import pytest

from tools.run_laya_capacity_segment import CapacitySegment, validate_transfer


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def transfer(tmp_path):
    source, root, other = [tmp_path / name for name in ("source", "segment", "other")]
    cfg = {"arms": [{"id": "kev", "concurrency": 4}], "seeds": [1], "concurrency": 4,
           "capacity_transfer": {"source_registry": str(source), "request_id": "transfer",
               "job_ids": ["kev-s1"], "per_arm_cap": 4,
               "capacity_group_roots": [str(source), str(root), str(other)]}}
    write(source / "job-status.json", {"admission": "drain", "jobs": [
        {"id": "kev-s1", "arm": "kev", "status": "cancelled_queued"}]})
    write(source / "batch-events.jsonl", {"event_id": "request:transfer", "request": {
        "mode": "cancel_queued"}, "cuts": ["kev-s1"]})
    write(other / "batch.json", {"arms": [{"id": "vela"}], "seeds": [1]})
    return cfg, root, source, other


def test_transfer_requires_acknowledged_never_started_source(transfer):
    cfg, root, source, _ = transfer
    validate_transfer(cfg, root)
    data = json.loads((source / "job-status.json").read_text())
    data["jobs"][0]["world"] = "/already-prepared"
    write(source / "job-status.json", data)
    with pytest.raises(ValueError, match="never-started"):
        validate_transfer(cfg, root)


def test_transfer_rejects_cross_segment_duplicate(transfer):
    cfg, root, _, other = transfer
    write(other / "batch.json", {"arms": [{"id": "kev"}], "seeds": [1]})
    with pytest.raises(ValueError, match="duplicate"):
        validate_transfer(cfg, root)


def test_transfer_requires_drain_and_cut_acknowledgement(transfer):
    cfg, root, source, _ = transfer
    write(source / "batch-events.jsonl", {"event_id": "unrelated"})
    with pytest.raises(ValueError, match="acknowledged"):
        validate_transfer(cfg, root)
    write(source / "job-status.json", {"admission": "run", "jobs": []})
    with pytest.raises(ValueError, match="drained"):
        validate_transfer(cfg, root)


def test_arm_limit_counts_predecessor_reservations_and_fails_closed(transfer):
    cfg, root, source, other = transfer
    batch = object.__new__(CapacitySegment)
    batch.cfg, batch.root = cfg, root
    batch.arms = {"kev": cfg["arms"][0]}
    batch.jobs = [{"arm": "kev", "status": "running"} for _ in range(3)]
    write(source / "job-status.json", {"jobs": [{"arm": "kev", "status": "completed",
                                                "admission_reserved": True}]})
    assert not batch.arm_available({"arm": "kev"})
    write(source / "job-status.json", {"jobs": []})
    assert batch.arm_available({"arm": "kev"})
    write(other / "job-status.json", {"jobs": None})
    assert not batch.arm_available({"arm": "kev"})
    assert batch.capacity_error
