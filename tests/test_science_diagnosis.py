# Path: tests/test_science_diagnosis.py
# Purpose: Replay ScienceStatus windows through pure, ordered research diagnosis.

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from orchestrator.science_diagnosis import (
    LAB_CAPACITY_LIMITED,
    LAB_POWER_LIMITED,
    RESEARCH_NOT_SELECTED,
    RESEARCH_PROGRESSING,
    RESEARCH_PROGRESS_STALLED,
    SCIENCE_DELIVERY_OR_SUPPLY_MISSING,
    SCIENCE_OBSERVATION_INCONCLUSIVE,
    diagnose_science_status,
)


FIXTURES = Path(__file__).parent / "fixtures" / "science_status"


def _report(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_science_shortage_replay_requests_supply_delivery_trace() -> None:
    result = diagnose_science_status(_report("science_shortage.json"))

    assert result["diagnosis"] == SCIENCE_DELIVERY_OR_SUPPLY_MISSING
    assert result["planner_handoff"] == "trace_science_supply_delivery_inserter"
    assert result["input_report_identities"] == [{
        "schema_version": "1.0.0", "tick": 100, "surface": "nauvis", "force": "player",
    }]


def test_power_fault_replay_has_priority_when_packs_are_buffered() -> None:
    result = diagnose_science_status(_report("science_power_limited.json"))

    assert result["diagnosis"] == LAB_POWER_LIMITED
    assert result["planner_handoff"] == "run_power_diagnosis"


def test_stalled_progress_requires_a_measured_comparison_window() -> None:
    start = _report("science_stalled_start.json")
    end = _report("science_stalled_end.json")

    assert diagnose_science_status(end)["diagnosis"] == SCIENCE_OBSERVATION_INCONCLUSIVE

    result = diagnose_science_status(end, [start])
    assert result["diagnosis"] == RESEARCH_PROGRESS_STALLED
    assert result["comparison"] == {
        "start_tick": 100,
        "end_tick": 200,
        "ticks_elapsed": 100,
        "research_progress_delta": 0.0,
    }


def test_progressing_replay_records_a_positive_progress_delta() -> None:
    start = _report("science_progressing_start.json")
    end = _report("science_progressing_end.json")

    result = diagnose_science_status(end, [start])

    assert result["diagnosis"] == RESEARCH_PROGRESSING
    assert result["planner_handoff"] == "measure_consumption_rate"
    assert result["comparison"]["research_progress_delta"] == pytest.approx(0.25)


def test_no_current_research_requests_an_authorized_selection() -> None:
    report = _report("science_shortage.json")
    report["research"] = {
        "current": None,
        "progress": 0.0,
        "queue": [],
        "current_science_packs": {},
        "current_research_unit_count": 0,
    }

    result = diagnose_science_status(report)

    assert result["diagnosis"] == RESEARCH_NOT_SELECTED
    assert result["planner_handoff"] == "request_authorized_selection"


def test_explicit_lab_target_is_considered_only_after_higher_priority_faults() -> None:
    start = _report("science_progressing_start.json")
    end = _report("science_progressing_end.json")

    result = diagnose_science_status(end, [start], target_working_labs=2)
    assert result["diagnosis"] == LAB_CAPACITY_LIMITED

    shortage = _report("science_shortage.json")
    result = diagnose_science_status(shortage, target_working_labs=3)
    assert result["diagnosis"] == SCIENCE_DELIVERY_OR_SUPPLY_MISSING


def test_comparison_reports_must_be_same_target_and_strictly_earlier() -> None:
    start = _report("science_progressing_start.json")
    end = _report("science_progressing_end.json")

    with pytest.raises(ValueError, match="earlier"):
        diagnose_science_status(start, [end])

    other_force = copy.deepcopy(start)
    other_force["force"] = "enemy"
    with pytest.raises(ValueError, match="same schema_version, surface, and force"):
        diagnose_science_status(end, [other_force])


def test_diagnosis_is_deterministic_even_when_prior_window_order_is_not() -> None:
    first = _report("science_progressing_start.json")
    second = copy.deepcopy(first)
    second["tick"] = 150
    second["research"]["progress"] = 0.5
    end = _report("science_progressing_end.json")

    ordered = diagnose_science_status(end, [first, second])
    unordered = diagnose_science_status(end, [second, first])

    assert ordered == unordered
    assert [identity["tick"] for identity in ordered["input_report_identities"]] == [100, 150, 200]
