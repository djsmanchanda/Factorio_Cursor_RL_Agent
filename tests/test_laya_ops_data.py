# Path: tests/test_laya_ops_data.py | Purpose: Protect ops evidence denominators, action attribution and evaluation split isolation.
import json
from pathlib import Path

import pytest

from tools.export_laya_ops_data import export, seed_split
from tools.run_laya_batch import summarize


def _batch(tmp_path):
    root = tmp_path / "batch"
    root.mkdir()
    arms = [{"id": a, "revision": c * 40} for a, c in (("ref", "a"), ("fix", "b"))]
    cfg = {"name": "cohort", "arms": arms, "seeds": [1, 2]}
    (root / "batch.json").write_text(json.dumps(cfg))
    (root / "freeze.json").write_text(json.dumps({"source_sha256": "c" * 64, "planned_jobs": 4, "arms": arms}))
    jobs = []
    for arm in ("ref", "fix"):
        for seed in (1, 2):
            world = tmp_path / f"{arm}-{seed}"
            (world / "evidence").mkdir(parents=True)
            record = {"version": "laya-decision-record-v1", "run_id": world.name, "index": 0,
                      "state": {"brief": "observed before choice"}, "options": [{"option_id": "one"}, {"option_id": "two"}],
                      "choice": {"option_id": "one", "probability": 0.6}, "distribution": {"one": 0.6, "two": 0.4},
                      "outcome": {"ok": False, "code": "untyped_stuck", "classification": "bug"}}
            (world / "evidence/decision-states.jsonl").write_text(json.dumps(record) + "\n")
            jobs.append({"id": world.name, "arm": arm, "seed": seed, "world": str(world),
                         "status": "completed", "accepted_ever": ["C2"], "accepted_final": None})
    (root / "job-status.json").write_text(json.dumps({"jobs": jobs}))
    return root, jobs


def test_export_preserves_actions_faults_unknown_labels_and_cross_arm_seed_split(tmp_path):
    root, jobs = _batch(tmp_path)
    jobs[-1]["status"] = "not_started_deadline"
    (root / "job-status.json").write_text(json.dumps({"jobs": jobs}))
    manifest = export([root], tmp_path / "out")
    rows = [json.loads(line) for p in (tmp_path / "out").glob("*.jsonl") for line in p.read_text().splitlines()]
    assert len(rows) == 3 and len(manifest["planned_jobs"]) == 4
    assert manifest["excluded"] == {"job_not_started_deadline": 1}
    same_seed = [r for r in rows if r["provenance"]["seed"] == 1]
    assert len({r["split"] for r in same_seed}) == 1
    assert all(r["observed_outcome"]["classification"] == "bug" for r in rows)
    assert all(r["terminal_outcome"]["accepted_final"] is None for r in rows)
    assert all(r["state"] == {"brief": "observed before choice"} for r in rows)
    assert all(r["choice"]["option_id"] == "one" and len(r["options"]) == 2 for r in rows)
    assert "no counterfactual" in manifest["labels"]
    assert seed_split("c" * 64, 1, {"c" * 64}) == "test"


def test_export_rejects_lost_jobs_and_never_overwrites_an_export(tmp_path):
    root, jobs = _batch(tmp_path)
    (root / "job-status.json").write_text(json.dumps({"jobs": jobs[:-1]}))
    with pytest.raises(ValueError, match="accounting"):
        export([root], tmp_path / "out")
    assert not (tmp_path / "out").exists()
    (root / "job-status.json").write_text(json.dumps({"jobs": jobs}))
    export([root], tmp_path / "out")
    before = (tmp_path / "out/manifest.json").read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        export([root], tmp_path / "out")
    assert (tmp_path / "out/manifest.json").read_bytes() == before


def test_batch_keeps_typed_faults_separate_from_exception_count_and_c6_contract(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "evidence").mkdir()
    (tmp_path / "logs/laya-runner.log").write_text(
        "SUMMARY: stop=stalled decisions=2 failures=0 phases=C0 accepted=C0 accepted_final=C0\n")
    c6 = {"version": "laya-c6-choice-output-v2", "status": "passed"}
    (tmp_path / "evidence/clean-decisions.jsonl").write_text(json.dumps(
        {"event": "laya_acceptance", "phase": "C6", **c6}) + "\n")
    outcomes = [{"ok": False, "code": "untyped_stuck", "classification": "bug"},
                {"ok": False, "code": "material_shortage", "classification": "supply"}]
    (tmp_path / "evidence/decision-states.jsonl").write_text("\n".join(json.dumps(
        {"version": "laya-decision-record-v1", "outcome": o}) for o in outcomes) + "\n{truncated\n")
    result = summarize(tmp_path)
    assert result["failures"] == 0
    assert result["outcome_codes"] == {"untyped_stuck": 1, "material_shortage": 1}
    assert result["outcome_classifications"] == {"bug": 1, "supply": 1}
    assert result["laya_acceptance"]["C6"]["version"] == c6["version"]
    assert "C6" not in result["accepted_ever"] and result["evidence_errors"] == 1
