# Path: tests/test_rocket_launch_evidence.py
# Purpose: Verify closed rocket event sessions without claiming live provenance.

import copy
import json
from pathlib import Path

import pytest

from core.rocket_launch_evidence import evaluate_launch_session
from tests.test_rocket_launch_observation import _launch_event, _scope_success
from tools.rocket_launch_evidence import collect_launch_session


def _case():
    start = _scope_success()
    end = {**start, "request_id": "close42", "tick": 1200,
           "enabled": False, "session_started": False, "recorded_launches": 1}
    event = _launch_event()
    target = copy.deepcopy(event["rocket_silo"])
    filename = f"rocket_launch_{event['session_id']}_{event['tick']}_{event['sequence']}.json"
    return start, end, event, target, filename


def test_matching_closed_session_does_not_claim_live_or_earned_launch():
    start, end, event, target, name = _case()
    result = evaluate_launch_session(start, end, [(name, event)], target_silo=target)
    assert result.status == "matching_event_found"
    assert result.matching_sequences == (1,)
    assert any("not authenticated" in note for note in result.notes)
    assert any("not prove" in note and "earned" in note for note in result.notes)


@pytest.mark.parametrize("damage", ["start_disabled", "start_not_fresh", "start_count", "start_tick", "legacy_session",
                                    "end_enabled", "end_session", "end_scope", "end_request", "end_time", "end_count"])
def test_invalid_or_incomplete_observation_windows_fail_closed(damage):
    start, end, event, target, name = _case()
    if damage == "start_disabled":
        start["enabled"] = False
    elif damage == "start_not_fresh":
        start["session_started"] = False
    elif damage == "start_count":
        start["recorded_launches"] = 1
    elif damage == "start_tick":
        start["scope_since_tick"] -= 1
    elif damage == "legacy_session":
        start["session_id"] = "unidentified"
    elif damage == "end_enabled":
        end["enabled"] = True
    elif damage == "end_session":
        end["session_id"] = "other"
    elif damage == "end_scope":
        end["force"] = "other"
    elif damage == "end_request":
        end["request_id"] = start["request_id"]
    elif damage == "end_time":
        end["tick"] = 999
    else:
        end["recorded_launches"] = 2
    with pytest.raises(ValueError):
        evaluate_launch_session(start, end, [(name, event)], target_silo=target)


@pytest.mark.parametrize("damage", ["filename", "session", "force", "early", "late", "type", "nan", "same_unit_other_location"])
def test_wrong_events_do_not_count(damage):
    start, end, event, target, name = _case()
    if damage == "filename":
        name = "rocket_launch_other.json"
    elif damage == "session":
        event["session_id"] = "other"
    elif damage == "force":
        event["force"] = "other"
    elif damage == "early":
        event["tick"] = 999
    elif damage == "late":
        event["tick"] = 1201
    elif damage == "type":
        event["event"] = "on_rocket_launch_ordered"
    elif damage == "nan":
        event["rocket"]["position"]["x"] = float("nan")
    else:
        event["rocket_silo"]["position"]["x"] += 1
    with pytest.raises(ValueError):
        evaluate_launch_session(start, end, [(name, event)], target_silo=target)


def test_unidentified_silo_is_not_attributed_to_the_target():
    start, end, event, target, name = _case()
    del event["rocket_silo"]
    result = evaluate_launch_session(start, end, [(name, event)], target_silo=target)
    assert result.status == "indeterminate" and result.unidentified_sequences == (1,)


def test_other_silo_and_empty_session_are_not_target_launches():
    start, end, event, target, name = _case()
    event["rocket_silo"]["unit_number"] = 8
    result = evaluate_launch_session(start, end, [(name, event)], target_silo=target)
    assert result.status == "no_matching_event" and result.other_sequences == (1,)
    end["recorded_launches"] = 0
    assert evaluate_launch_session(start, end, [], target_silo=target).status == "no_matching_event"


def test_multiple_events_require_contiguous_monotone_sequence():
    start, end, event, target, name = _case()
    end["recorded_launches"] = 2
    second = copy.deepcopy(event)
    second.update(sequence=2, tick=1150)
    second_name = "rocket_launch_session42_1150_2.json"
    result = evaluate_launch_session(start, end, [(second_name, second), (name, event)], target_silo=target)
    assert result.matching_sequences == (1, 2)
    second["sequence"] = 3
    with pytest.raises(ValueError, match="contiguous"):
        evaluate_launch_session(start, end, [(name, event), ("rocket_launch_session42_1150_3.json", second)], target_silo=target)
    second.update(sequence=2, tick=1050)
    with pytest.raises(ValueError, match="chronological"):
        evaluate_launch_session(start, end, [(name, event), ("rocket_launch_session42_1050_2.json", second)], target_silo=target)


def test_collector_reads_only_target_session_without_mutation(tmp_path: Path):
    start, end, event, target, name = _case()
    (tmp_path / name).write_text(json.dumps(event))
    (tmp_path / "rocket_launch_other_1_1.json").write_text("unrelated")
    result = collect_launch_session(tmp_path, start=start, end=end, target_silo=target)
    assert result.status == "matching_event_found"
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted([name, "rocket_launch_other_1_1.json"])


@pytest.mark.parametrize("damage", ["missing", "symlink", "duplicate_field", "nonfinite", "oversize", "wrong_name"])
def test_collector_rejects_missing_unsafe_or_bad_files(tmp_path: Path, damage):
    start, end, event, target, name = _case()
    path = tmp_path / name
    if damage == "symlink":
        target_path = tmp_path / "target"
        target_path.write_text(json.dumps(event))
        path.symlink_to(target_path)
    elif damage == "duplicate_field":
        path.write_text('{"kind":"rocket_launch","kind":"rocket_launch"}')
    elif damage == "nonfinite":
        path.write_text('{"x":NaN}')
    elif damage == "oversize":
        path.write_text("x" * 20)
    elif damage == "wrong_name":
        (tmp_path / "rocket_launch_session42_1100_9.json").write_text(json.dumps(event))
    elif damage != "missing":
        path.write_text(json.dumps(event))
    with pytest.raises(ValueError):
        collect_launch_session(tmp_path, start=start, end=end, target_silo=target,
                               max_file_bytes=10 if damage == "oversize" else 1_048_576)
