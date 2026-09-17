# Path: tests/test_campaign_loop_log.py
# Purpose: Durable loop records, fix ledger and hypothesis ledger contracts.
import json

from tools import campaign_loop_log as loop_log


def test_cycle_records_append_and_never_rewrite(tmp_path):
    first = {"campaign_id": "c1", "cycle_id": 1, "episode_id": "e1", "outcome": "committed"}
    second = {"campaign_id": "c1", "cycle_id": 2, "episode_id": "e2", "outcome": "no-change"}
    loop_log.append_cycle(tmp_path, first)
    loop_log.append_cycle(tmp_path, second)
    records = loop_log.cycles(tmp_path)
    assert records == [first, second]
    assert loop_log.cycle_record(tmp_path, "c1", 1)["episode_id"] == "e1"
    assert loop_log.cycle_record(tmp_path, "c1", 3) is None


def test_fix_ledger_merges_updates_per_fix_id(tmp_path):
    loop_log.record_fix(tmp_path, {"fix_id": "FIX-0001", "introduced_cycle": 1, "status": "committed"})
    loop_log.record_fix(tmp_path, {"fix_id": "FIX-0001", "commit": "abc123"})
    fixes = loop_log.fixes(tmp_path)
    assert fixes["FIX-0001"] == {"fix_id": "FIX-0001", "introduced_cycle": 1,
                                 "status": "committed", "commit": "abc123"}
    assert loop_log.next_fix_id(tmp_path) == "FIX-0002"


def test_hypothesis_ledger_accumulates_runs_and_statuses(tmp_path):
    loop_log.record_hypothesis(tmp_path, {"hypothesis_id": "H-001", "description": "power bridge", "first_cycle": 1, "status": "OPEN"})
    loop_log.record_hypothesis(tmp_path, {"hypothesis_id": "H-001", "supporting_runs": "1/e1"})
    loop_log.record_hypothesis(tmp_path, {"hypothesis_id": "H-001", "supporting_runs": "1/e1", "status": "FALSIFIED"})
    entry = loop_log.hypotheses(tmp_path)["H-001"]
    assert entry["description"] == "power bridge"
    assert entry["supporting_runs"] == ["1/e1"]
    assert entry["status"] == "FALSIFIED"
    assert loop_log.next_hypothesis_id(tmp_path) == "H-002"


def test_previous_fix_returns_newest_earlier_cycle(tmp_path):
    loop_log.record_fix(tmp_path, {"fix_id": "FIX-0001", "introduced_cycle": 1})
    loop_log.record_fix(tmp_path, {"fix_id": "FIX-0002", "introduced_cycle": 3})
    assert loop_log.previous_fix(tmp_path, 4)["fix_id"] == "FIX-0002"
    assert loop_log.previous_fix(tmp_path, 2)["fix_id"] == "FIX-0001"
    assert loop_log.previous_fix(tmp_path, 1) is None


def test_ledger_files_are_valid_json(tmp_path):
    loop_log.record_fix(tmp_path, {"fix_id": "FIX-0001", "status": "committed"})
    payload = json.loads((loop_log.loop_root(tmp_path) / "fixes.json").read_text())
    assert payload["fixes"]["FIX-0001"]["status"] == "committed"
