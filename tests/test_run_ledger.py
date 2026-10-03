# Path: tests/test_run_ledger.py | Purpose: Protect the LLM-free run ledger: deterministic markers and causes, Laya phases, dedupe and CSV view.

import csv
import json

from tools import run_ledger

COMPACT = """RUN START: ts=2026-09-18T05:02:19+05:30 command=research target=mining-productivity-4 surface=nauvis
+1s PLATE STARTER: iron-plate begins with 2 drill(s) feeding 2 furnace(s) directly at (17.5, -2.5)
+40s PLATE STARTER: copper-plate begins with 2 drill(s)
+1878s   [modular refinery for iron-plate] RESOLVED after 2 round(s)
+4599s LOG REPEAT x2:   CHEMICAL LADDER: plastic-bar waits at pipe (6/10 rungs required)
+5726s STUCK: No progress in 12 passes: medium-electric-pole has been at 0%
+5726s BLOCKER: {"code":"no_progress","classification":"bug","message":"No progress in 12 passes"}
+5726s RUN END
"""
LEGACY = """2026-08-21T14:50:00+05:30 RUN START: command=produce target=automation-science-pack
2026-08-21T14:55:12+05:30 GOAL MET: automation-science-pack is producing at (-1.5, -11.5)
2026-08-21T14:55:13+05:30 RUN END
"""


def test_deterministic_runs_record_markers_cause_duration_and_commit(tmp_path):
    log = tmp_path / "autonomous-run.log"
    log.write_text(LEGACY + COMPACT)
    ledger = tmp_path / "ledger.jsonl"
    runs = run_ledger.deterministic_runs(log)
    assert len(runs) == 2
    first = run_ledger.record(run_ledger.deterministic_entry(runs[0], log), ledger)
    assert first["outcome"] == "completed" and first["highest_milestone"] == "automation_science" and first["duration_s"] == 313
    entry = run_ledger.record(run_ledger.deterministic_entry(runs[1], log, run_id="ep-1", commit="abc123"), ledger)
    assert [m["id"] for m in entry["milestones"]] == ["iron_starter", "copper_starter", "iron_foundation", "plastic_ladder"]
    assert entry["highest_milestone"] == "plastic_ladder" and entry["outcome"] == "stuck"
    assert entry["failure_code"] == "no_progress" and entry["duration_s"] == 5726 and entry["commit"] == "abc123"
    run_ledger.record({**entry, "outcome": "stuck-again"}, ledger)  # Same key replaces, never duplicates.
    rows = [json.loads(l) for l in ledger.read_text().splitlines()]
    assert [r["run_id"] for r in rows].count("ep-1") == 1 and len(rows) == 2
    table = list(csv.DictReader(ledger.with_suffix(".csv").open()))
    assert table[-1]["run_id"] == "ep-1" and table[-1]["milestones"].startswith("iron_starter@1s;")


def test_laya_entry_uses_measured_phases_and_reports_the_limiting_factor(tmp_path):
    root = tmp_path / "clean-x"
    (root / "evidence").mkdir(parents=True)
    rows = [{"event": "phase_reached", "run_id": "r1", "phase": "C0", "tick": 9},
            {"event": "decision", "run_id": "r1", "status": "observed", "phase": "C1",
             "board": {"bottleneck": ["iron-gear-wheel:unbuilt <- iron-plate:short"]}},
            {"event": "failure", "run_id": "r1", "error_type": "SavedWorldError", "error": "ghost submission failed"}]
    (root / "evidence/clean-decisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    index = {"run_id": "r1", "root": str(root), "commit": "c7fe65e", "started": "2026-10-03T20:00:00+05:30",
             "ended": "2026-10-03T20:10:00+05:30", "summary": {"stop": "safe_stop_after_repeated_failures"}}
    entry = run_ledger.laya_entry(index)
    assert entry["highest_milestone"] == "C0" and entry["outcome"] == "safe_stop" and entry["duration_s"] == 600
    assert entry["failure_cause"] == "ghost submission failed" and entry["limiting_factor"].startswith("iron-gear-wheel")
    assert entry["log_file"].endswith("logs/laya-runner.log") and entry["decisions"] == 1


def test_times_are_ist_and_run_length_is_readable(tmp_path):
    from core.ist_time import run_length, to_ist
    assert to_ist("2026-10-03T14:30:00Z") == "2026-10-03T20:00:00+05:30"
    assert to_ist("2026-10-03T20:00:00") == "2026-10-03T20:00:00+05:30"  # Naive local values are IST.
    assert run_length(5726) == "1h 35m 26s" and run_length(75) == "1m 15s"
    entry = run_ledger.record({"system": "laya", "run_id": "z", "started": "2026-10-03T14:30:00Z",
                               "ended": "2026-10-03T14:40:00+00:00", "duration_s": 600, "milestones": []},
                              tmp_path / "l.jsonl")
    assert entry["started"].endswith("+05:30") and entry["ended"] == "2026-10-03T20:10:00+05:30"
    assert entry["run_length"] == "10m 00s"
    assert list(csv.DictReader((tmp_path / "l.csv").open()))[0]["run_length"] == "10m 00s"
