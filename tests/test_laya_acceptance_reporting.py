# Path: tests/test_laya_acceptance_reporting.py | Purpose: Keep terminal checkpoint results distinct from historical passes.

import pytest

from tools.run_laya_batch import Batch, summarize
from training.laya_ops_runner import run


@pytest.mark.parametrize("final,expected", [({"C0": "passed", "C1": "failed"}, ["C0"]),
                                           ({"C0": "unknown", "C1": "failed"}, []),
                                           (RuntimeError("observation lost"), None)])
def test_runner_and_batch_preserve_regressions_and_missing_observations(tmp_path, final, expected):
    class Session:
        def __init__(self):
            self.observations = iter([{"C0": "passed", "C1": "passed"}, final])

        def acceptance(self):
            result = next(self.observations)
            if isinstance(result, Exception):
                raise result
            return result

        def close(self):
            pass

    lines = []
    summary = run(object(), object(), journal=tmp_path / "journal.jsonl", decisions=0,
                  measure_ticks=600, seed=1, run_id="acceptance", session=Session(), emit=lines.append)
    assert summary["accepted"] == summary["accepted_ever"] == ["C0", "C1"]
    assert summary["accepted_final"] == expected
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/laya-runner.log").write_text("\n".join(lines))
    parsed = summarize(tmp_path)
    assert parsed["accepted_ever"] == ["C0", "C1"]
    assert parsed["accepted_final"] == expected


def test_legacy_summary_has_unknown_final_acceptance(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/laya-runner.log").write_text(
        "SUMMARY: stop=stalled decisions=2 failures=0 phases=C0 accepted=C0\n")
    parsed = summarize(tmp_path)
    assert parsed["accepted_ever"] == ["C0"]
    assert parsed["accepted_final"] is None


def test_batch_report_discloses_final_observation_denominator(tmp_path):
    batch = object.__new__(Batch)
    batch.root = tmp_path
    batch.cfg = {"name": "acceptance", "arms": []}
    batch.jobs = [
        {"arm": "control", "status": "completed", "accepted": ["C0", "C1"], "accepted_final": ["C0"]},
        {"arm": "control", "status": "completed", "accepted": ["C0"]},
        {"arm": "control", "status": "completed", "accepted": [], "accepted_final": []},
        {"arm": "control", "status": "not_started_deadline"},
    ]
    batch.report()
    report = (tmp_path / "report.md").read_text()
    assert "{'C0': 2, 'C1': 1}" in report
    assert "{'C0': 1} (n=2/3)" in report
    assert "final observations do not prove sustained service" in report
