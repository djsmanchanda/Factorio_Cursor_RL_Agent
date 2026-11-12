# Path: tests/test_run_trackb_arm.py | Purpose: Protect the Track B arm adapter: scheduler contract parsing and sidecar rows in collect_result's shape.
from __future__ import annotations

import pytest

from tools.run_trackb_arm import journal_rows, parse

pytestmark = pytest.mark.fast


def test_scheduler_run_arguments_parse_and_unsupported_options_fail_loudly(tmp_path):
    args = parse(["run", "--root", str(tmp_path), "--actions", "deterministic", "--no-helper", "--seed", "7",
                  "--max-game-ticks", "540000", "--speed", "2"])
    assert (args.seed, args.max_game_ticks, args.speed) == (7, 540000, 2.0)
    assert parse(["run", "--root", str(tmp_path), "--strict-target", "C9"]).strict_target == "C9"
    with pytest.raises(SystemExit):
        parse(["run", "--root", str(tmp_path), "--research-target", "mining-productivity-4"])


def test_sidecar_rows_latch_acceptance_and_carry_the_baseline_as_loaded_tick():
    summary = {"baseline_tick": 100, "strict_current": [
        {"at": "sample 0", "tick": 100, "statuses": {"C0": True, "C4": False, "C5": None}},
        {"at": "sample 1", "tick": 200, "statuses": {"C0": True, "C4": True, "C5": None}},
        {"at": "sample 2", "tick": 300, "statuses": {"C0": True, "C4": False, "C5": None}}]}
    rows = journal_rows(summary, "run-1")
    current = [r for r in rows if r["event"] == "strict_current"]
    acceptance = [r for r in rows if r["event"] == "acceptance"]
    assert all(r["loaded_tick"] == 100 and r["source"] == "strict_sidecar" for r in rows)
    assert current[-1]["statuses"]["C4"] is False  # fresh status at the end
    assert acceptance[-1]["statuses"] == {"C0": "passed", "C4": "passed", "C5": "unknown"}  # latched


def test_strict_target_is_read_from_the_sidecar_summary(tmp_path):
    import json
    from tools.run_trackb_arm import target_reached
    out = tmp_path / "strict-sidecar.json"
    assert not target_reached(out, "C9")  # no sample yet
    out.write_text(json.dumps({"strict_reached": {"C0": 1, "C4": 9}}))
    assert target_reached(out, "C4") and not target_reached(out, "C9")
