# Path: tests/test_laya_cohort_performance.py | Purpose: Keep every planned job in the per-arm cohort table.
import json

import pytest

from tools.laya_cohort_performance import cohort_rows, markdown

pytestmark = pytest.mark.fast


def test_rows_count_every_planned_job_and_label_the_c6_predicate(tmp_path):
    cohort = tmp_path / "laya-c99-c0-demo-2x-20261109"
    cohort.mkdir()
    (cohort / "regression.json").write_text(json.dumps({"game_speed": 2.0, "arms": {
        "reference": {"revision": "b1ae062"}, "variant": {"revision": "9d9b279"}}}))
    jobs = [{"arm": "reference", "status": "completed", "accepted_ever": ["C0", "C2", "C3", "C4"], "server_seconds": 100},
            {"arm": "reference", "status": "infrastructure_failure", "accepted_ever": ["C0"]},
            {"arm": "variant", "status": "completed", "accepted_ever": "['C0', 'C2', 'C6']", "elapsed_s": "300"},
            {"arm": "variant", "status": "queued"}]
    (cohort / "job-status.json").write_text(json.dumps({"jobs": jobs}))
    rows = {row["arm"]: row for row in cohort_rows(tmp_path)}
    assert (rows["reference"]["completed"], rows["reference"]["planned"]) == (1, 2)
    assert rows["reference"]["ever"] == {"C2": 1, "C3": 1, "C4": 1, "C6": 0}
    assert rows["reference"]["c6"] == "c6-v2" and rows["variant"]["c6"] == "c6-v1"
    assert rows["variant"]["ever"]["C6"] == 1 and rows["variant"]["median_s"] == 300
    assert rows["variant"]["state"] == "running"
    assert "| 1/2 | 1 | 1 | 1 | 0 (c6-v2) | 100 | running |" in markdown(list(rows.values()), "2026-11-09 22:00 IST")
