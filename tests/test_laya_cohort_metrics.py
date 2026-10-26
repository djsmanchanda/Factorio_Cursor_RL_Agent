# Path: tests/test_laya_cohort_metrics.py | Purpose: Preserve end-only attainment, truthful missing times, and log-only release failures.
import json
import sys

from tools.laya_cohort_time_to import main as time_to
from tools.laya_cohort_time_to import times
from tools.laya_release_counts import counts


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_end_only_pass_uses_actual_acceptance_foundation_tick(tmp_path):
    write_rows(tmp_path / "evidence/clean-decisions.jsonl", [
        {"event": "decision", "index": 0, "tick": 100},
        {"event": "decision", "index": 1, "tick": 1000},
        {"event": "acceptance", "at": "end", "fresh": {"C3": "passed"},
         "foundations": {"iron": {"tick": 3700}, "stone": {"tick": 3700}}},
    ])
    assert times({"world": str(tmp_path)})["strict C3"] == 1


def test_missing_end_tick_is_reached_time_unknown_not_unreached(tmp_path):
    write_rows(tmp_path / "evidence/clean-decisions.jsonl", [
        {"event": "decision", "index": 0, "tick": 100},
        {"event": "acceptance", "at": "end", "fresh": {"C3": "passed"}},
    ])
    diagnostics = {}
    assert "strict C3" not in times({"world": str(tmp_path)}, diagnostics)
    assert diagnostics["reached_time_unknown"] == {"strict C3"}


def test_new_c5_uses_retained_qualified_tick_not_late_poll(tmp_path):
    write_rows(tmp_path / "evidence/clean-decisions.jsonl", [
        {"event": "decision", "index": 0, "tick": 100},
        {"event": "acceptance", "at": "end", "tick": 10000, "fresh": {"C5": "passed"},
         "evidence": {"C5": {"predicate_version": "c5-v2", "qualified_tick": 3700}}},
    ])
    assert times({"world": str(tmp_path)})["strict C5"] == 1


def test_release_counts_include_log_only_and_do_not_sum_same_world_twice(tmp_path):
    jobs = []
    for i in range(2):
        world = tmp_path / str(i)
        write_rows(world / "evidence/decision-states.jsonl", [{"outcome": {
            "code": "mall_cell_lifecycle_conflict", "details": {"reasons": ["release_recipe_changed"]}}}] if i else [])
        (world / "logs").mkdir()
        (world / "logs/laya-runner.log").write_text("LOAN RELEASE FAILED: mall cell release refused\n")
        jobs.append({"arm": "control", "world": str(world)})
    (tmp_path / "job-status.json").write_text(json.dumps({"jobs": jobs}))
    result = counts(tmp_path)["control"]
    assert result["worlds_with_journal_refusal"] == 1
    assert result["worlds_with_refusal"] == 2
    assert result["worlds_with_any_release_issue"] == 2
    assert result["worlds_in_both_sources"] == 1


def test_release_counts_skip_malformed_rows_without_losing_other_worlds(tmp_path):
    broken, good = tmp_path / "broken", tmp_path / "good"
    journal = broken / "evidence/decision-states.jsonl"
    write_rows(journal, [{"outcome": {"code": "mall_cell_lifecycle_conflict",
                                      "details": {"blockers": [{"reason": "release_recipe_changed"}]}}}])
    with journal.open("a") as out:
        out.write("{truncated\n")
        out.write("[]\n")
        out.write(json.dumps({"outcome": "not-a-mapping"}) + "\n")
        out.write(json.dumps({"outcome": {"code": "infrastructure_collision",
                                          "details": {"blockers": "not-a-list",
                                                      "reasons": [{"nested": "mapping"}]}}}) + "\n")
    write_rows(good / "evidence/decision-states.jsonl", [{"outcome": {
        "code": "infrastructure_collision", "details": {"reasons": ["release_owner_missing"]}}}])
    (broken / "logs").mkdir()
    (good / "logs").mkdir()
    (good / "logs/laya-runner.log").write_text("LOAN RELEASE FAILED: mall cell release refused\n")
    jobs = [{"arm": "control", "world": str(world)} for world in (broken, good)]
    (tmp_path / "job-status.json").write_text(json.dumps({"jobs": jobs}))

    result = counts(tmp_path)["control"]

    assert result["worlds"] == 2 and result["malformed_journal_rows"] == 3
    assert result["journal:mall_cell_lifecycle_conflict:release_recipe_changed"] == 1
    assert result["journal:infrastructure_collision:release_owner_missing"] == 1
    assert result["worlds_with_journal_refusal"] == 2
    assert result["worlds_with_runner_release_failure"] == result["runner_release_failure_events"] == 1
    assert result["worlds_with_any_release_issue"] == 2 and result["worlds_in_both_sources"] == 1


def test_time_to_labels_rmst_completed_only_and_discloses_excluded_terminals(tmp_path, capsys, monkeypatch):
    (tmp_path / "batch.json").write_text(json.dumps({"name": "t", "arms": [{"id": "control"}, {"id": "fix"}]}))
    done = tmp_path / "control-s1"
    write_rows(done / "evidence/clean-decisions.jsonl", [
        {"event": "decision", "index": 0, "tick": 100},
        {"event": "acceptance", "at": "end", "fresh": {"C1": "passed"}, "foundations": {"iron": {"tick": 3700}}},
    ])
    jobs = [
        {"id": "control-s1", "arm": "control", "seed": 1, "status": "completed", "world": str(done)},
        {"id": "control-s2", "arm": "control", "seed": 2, "status": "wall_cap", "world": str(tmp_path / "control-s2")},
        {"id": "fix-s1", "arm": "fix", "seed": 1, "status": "runner_error", "world": str(tmp_path / "fix-s1")},
        {"id": "fix-s2", "arm": "fix", "seed": 2, "status": "queued"},
    ]
    (tmp_path / "job-status.json").write_text(json.dumps({"jobs": jobs}))
    monkeypatch.setattr(sys, "argv", ["time_to.py", str(tmp_path), "--window-ticks", "9000"])

    time_to()

    out = capsys.readouterr().out
    assert "completed with timing 1/4 planned" in out
    assert "completed 1 (0 without usable timing)" in out
    assert "RMST completed-only excludes terminal non-completed {'wall_cap': 1, 'runner_error': 1}" in out
    assert "unfinished/not started {'queued': 1}" in out
    assert "reached, median, RMST completed-only" in out
    # The completed run's 1-minute C1 is not diluted by imputing the wall cap at the 2.5-minute window.
    assert "| 1/1, 1, 1 |" in out
