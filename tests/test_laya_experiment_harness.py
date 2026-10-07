# Path: tests/test_laya_experiment_harness.py | Purpose: Every planned experiment run is counted once, including crashes, so no arm loses failures from its denominator.
import argparse
import json

import tools.laya_experiment as experiment


def test_unfinished_runs_are_indexed_once_and_reported(tmp_path, monkeypatch):
    index = tmp_path / "laya-runs.jsonl"
    monkeypatch.setattr(experiment, "RUN_INDEX", index)
    args = argparse.Namespace(until_phase="C2", max_game_ticks=216000, experiment="exp")
    root = tmp_path / "baseline-s1"
    # The run indexed itself: nothing is added.
    index.write_text(json.dumps({"root": str(root.resolve()), "experiment": "exp", "variant_id": "baseline",
                                 "summary": {"stop": "stalled", "phases_reached": ["C0", "C1"],
                                             "codes": {"untyped_stuck": 2}}}) + "\n")
    experiment._finalize("exp", None, 1, root, args, "unindexed", 0)
    # A crash and a failed preparation are recorded with their cause.
    experiment._finalize("exp", None, 2, tmp_path / "baseline-s2", args, "crash", 1)
    experiment._finalize("exp", None, 3, tmp_path / "baseline-s3", args, "prepare_failed", 2)
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert len(rows) == 3 and rows[1]["returncode"] == 1
    score = experiment.score(rows, "C1")
    assert score["runs"] == 3 and score["reached"] == 1 and score["untyped_stuck"] == 2
    assert score["stops"] == {"crash": 1, "prepare_failed": 1, "stalled": 1}
