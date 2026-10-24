# Path: tests/test_laya_cohort_metrics.py | Purpose: Preserve end-only attainment, truthful missing times, and log-only release failures.
import json

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
