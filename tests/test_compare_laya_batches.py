# Path: tests/test_compare_laya_batches.py | Purpose: Reject incomplete lab comparisons and expose completion regressions.

from copy import deepcopy
import json

import pytest

from tools.compare_laya_batches import compare, load, main


def rows():
    return {seed: {"seed": seed, "status": "completed", "elapsed_ticks": 6000,
                   "material_cost": 10, "decisions": 5, "safety_violations": 0,
                   "comparison_conditions": {"scenario_hash": f"scenario-{seed}",
                       "request_version": "v2", "guideline_hash": "rules", "max_len": 2048,
                       "max_decisions": 60, "max_no_progress": 12, "stepped": True, "game_speed": 32}}
            for seed in (1, 2, 3)}


def test_cheaper_failed_pair_is_not_a_completion_win():
    baseline = rows()
    baseline[1].update(status="timed_out", material_cost=1000)
    challenger = deepcopy(baseline)
    challenger[1]["material_cost"] = 1
    report = compare(baseline, challenger, {1, 2, 3}, (36000, 10, 60))
    assert report["lower_cost_pairs"] == 1
    assert report["completed_lower_cost_pairs"] == report["completion_wins"] == 0
    assert report["mean_cost_ticks"] > report["median_cost_ticks"]
    challenger[2]["status"] = "timed_out"
    report = compare(baseline, challenger, {1, 2, 3}, (36000, 10, 60))
    assert report["status"] == "blocked" and report["completion_losses"] == 1


def test_missing_pairs_on_both_sides_cannot_shrink_the_suite():
    with pytest.raises(ValueError, match="every expected seed"):
        compare({1: rows()[1]}, {1: rows()[1]}, {1, 2, 3}, (36000, 10, 60))


@pytest.mark.parametrize("field", ["scenario_hash", "guideline_hash", "stepped", "max_decisions"])
def test_mismatched_execution_conditions_block(field):
    baseline, challenger = rows(), rows()
    challenger[1]["comparison_conditions"][field] = "different"
    with pytest.raises(ValueError, match="conditions differ"):
        compare(baseline, challenger, {1, 2, 3}, (36000, 10, 60))


def test_global_and_relative_sampler_cohorts_cannot_compare():
    baseline, challenger = rows(), rows()
    for seed in baseline:
        baseline[seed]['comparison_conditions']['sampling_version'] = 'global-60-v1'
        challenger[seed]['comparison_conditions']['sampling_version'] = 'episode-relative-v1'
    with pytest.raises(ValueError, match='conditions differ'):
        compare(baseline, challenger, {1, 2, 3}, (36000, 10, 60))


def test_missing_conditions_and_safety_are_not_fabricated():
    baseline, challenger = rows(), rows()
    challenger[1].pop("comparison_conditions")
    with pytest.raises(ValueError, match="missing comparison"):
        compare(baseline, challenger, {1, 2, 3}, (36000, 10, 60))
    challenger = rows()
    challenger[1].pop("safety_violations")
    with pytest.raises(ValueError, match="missing safety"):
        compare(baseline, challenger, {1, 2, 3}, (36000, 10, 60))


def test_duplicate_seed_rejected(tmp_path):
    path = tmp_path / "batch.jsonl"
    row = json.dumps(rows()[1])
    path.write_text(row + "\n" + row + "\n")
    with pytest.raises(ValueError, match="duplicate seed"):
        load(path)


def test_cli_uses_frozen_manifest_and_never_prints_partial_success(tmp_path, capsys):
    baseline, challenger = tmp_path / "base.jsonl", tmp_path / "new.jsonl"
    baseline.write_text("\n".join(json.dumps(r) for r in rows().values()))
    challenger.write_text(json.dumps(rows()[1]))
    baseline.with_suffix(".manifest.json").write_text(json.dumps({"expected_seeds": [1, 2, 3]}))
    assert main([str(baseline), str(challenger)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked" and "every expected seed" in result["reason"]
