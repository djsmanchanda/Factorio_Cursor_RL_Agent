# Path: tests/test_chain_telemetry.py
# Purpose: Verify measure_line/measure_chain/rate_tracker parsing and query budget with a fake RCON bridge (no live game).

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.bottleneck_diagnosis import diagnose_line
from orchestrator.chain_telemetry import (
    _build_line_query,
    measure_chain,
    measure_line,
    rate_tracker,
)


class FakeBridge:
    """Canned-response stand-in for GameBridge: no socket, no game."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    def command(self, text: str) -> str:
        self.sent.append(text)
        return self.responses.pop(0)


SCIENCE_LINE = {
    "name": "science_line",
    "origin": (28, 132),
    "machines": 4,
    "machine_name": "assembling-machine-2",
    "output_item": "automation-science-pack",
    "machine_rate": 0.4,
    "belt_capacity_per_tile": 40,
}

LAB_ROW = {
    "name": "lab_row",
    "origin": (45, 148),
    "labs": 4,
    "machine_name": "lab",
}


def test_measure_line_parses_realistic_response_into_diagnosis_shape():
    raw = (
        "machines=4 input_belt_start=30 input_belt_end=3 output_belt=6 "
        "produced=1200 collectors_full=0 status_working=3 status_item_ingredient_shortage=1"
    )
    bridge = FakeBridge([raw])
    measurement = measure_line(bridge, SCIENCE_LINE)

    assert measurement["status_counts"] == {"working": 3, "item_ingredient_shortage": 1}
    assert measurement["input_belt_start_count"] == 30
    assert measurement["input_belt_start_capacity"] == 40.0
    assert measurement["input_belt_end_count"] == 3
    assert measurement["input_belt_end_capacity"] == 40.0
    assert measurement["output_belt_count"] == 6
    assert measurement["output_belt_capacity"] == 40.0
    assert measurement["collectors_full"] is False
    assert measurement["produced_count"] == 1200.0
    assert measurement["theoretical_capacity_per_s"] == 1.6
    # measured_output_per_s cannot be known from one snapshot - see docstring.
    assert measurement["measured_output_per_s"] is None

    # The shape must be directly consumable by diagnose_line (with a rate
    # merged in by the caller, as documented) without raising or KeyError-ing.
    measurement_with_rate = dict(measurement, measured_output_per_s=1.5)
    result = diagnose_line({}, measurement_with_rate)
    assert result["verdict"] in {
        "machine_limited", "feed_limited", "drain_limited", "input_starved",
        "power_limited", "resource_depleted", "healthy_ramping", "unknown",
    }


def test_measure_line_degrades_gracefully_on_missing_and_zero_fields():
    # Nothing built yet: no machines found, no belts, no chests, no production
    # statistics entry (produced left at the query's -1 sentinel -> None).
    raw = "machines=0 input_belt_start=0 input_belt_end=0 output_belt=0 produced=-1 collectors_full=1"
    bridge = FakeBridge([raw])
    measurement = measure_line(bridge, SCIENCE_LINE)

    assert measurement["status_counts"] == {}
    assert measurement["input_belt_start_count"] == 0
    assert measurement["output_belt_count"] == 0
    assert measurement["collectors_full"] is True
    assert measurement["produced_count"] is None
    assert measurement["measured_output_per_s"] is None

    # Must not raise when fed straight into diagnose_line.
    result = diagnose_line({}, measurement)
    assert result["verdict"] == "unknown"


def test_measure_line_without_machine_rate_omits_theoretical_capacity():
    line = dict(SCIENCE_LINE)
    del line["machine_rate"]
    raw = "machines=4 input_belt_start=0 input_belt_end=0 output_belt=0 produced=-1 collectors_full=1"
    bridge = FakeBridge([raw])
    measurement = measure_line(bridge, line)
    assert "theoretical_capacity_per_s" not in measurement


def test_measure_line_query_stays_under_1500_chars_single_round_trip():
    query = _build_line_query(SCIENCE_LINE)
    assert len(query) < 1500
    assert query.startswith("/sc ")

    bridge = FakeBridge(["machines=4 input_belt_start=0 input_belt_end=0 output_belt=0 produced=-1 collectors_full=1"])
    measure_line(bridge, SCIENCE_LINE)
    # Exactly one RCON round trip per line.
    assert len(bridge.sent) == 1
    assert len(bridge.sent[0]) < 1500


def test_measure_line_lab_row_defaults_machine_name_to_lab():
    raw = "machines=4 input_belt_start=0 input_belt_end=0 output_belt=0 produced=-1 collectors_full=1 status_working=4"
    bridge = FakeBridge([raw])
    lab_only = {"name": "lab_row", "origin": (45, 148), "labs": 4}
    measurement = measure_line(bridge, lab_only)
    assert "name='lab'" in bridge.sent[0]
    assert measurement["status_counts"] == {"working": 4}


def test_measure_chain_aggregates_lines_and_global_state():
    science_raw = (
        "machines=4 input_belt_start=30 input_belt_end=3 output_belt=6 "
        "produced=1200 collectors_full=0 status_working=3 status_item_ingredient_shortage=1"
    )
    lab_raw = "machines=4 input_belt_start=10 input_belt_end=1 output_belt=0 produced=-1 collectors_full=1 status_working=3 status_no_power=1"
    global_raw = "current_research=automation-3 research_progress=0.42"
    bridge = FakeBridge([science_raw, lab_raw, global_raw])

    chain = measure_chain(bridge, [SCIENCE_LINE, LAB_ROW])

    assert set(chain.keys()) == {"science_line", "lab_row", "_global"}
    assert chain["science_line"]["produced_count"] == 1200.0
    assert chain["lab_row"]["status_counts"]["working"] == 3

    glob = chain["_global"]
    assert glob["current_research"] == "automation-3"
    assert glob["research_progress"] == 0.42
    assert glob["labs_working"] == 3
    assert glob["labs_total"] == 4  # 3 working + 1 no_power


def test_rate_tracker_computes_rate_from_produced_count():
    previous = {"science_line": {"produced_count": 1000.0}}
    current = {"science_line": {"produced_count": 1030.0}}
    rates = rate_tracker(previous, current, elapsed_s=15.0)
    assert rates["science_line"]["output_rate_per_s"] == 2.0


def test_rate_tracker_returns_none_without_produced_count_rather_than_inventing_a_rate():
    # No output_item was ever given for this line, so produced_count is None
    # on both snapshots - rate_tracker must not fabricate a rate from belt
    # levels (output_belt_count) or anything else.
    previous = {"lab_row": {"produced_count": None, "output_belt_count": 2}}
    current = {"lab_row": {"produced_count": None, "output_belt_count": 9}}
    rates = rate_tracker(previous, current, elapsed_s=15.0)
    assert rates["lab_row"]["output_rate_per_s"] is None


def test_rate_tracker_returns_none_on_missing_previous_snapshot():
    current = {"science_line": {"produced_count": 500.0}}
    rates = rate_tracker({}, current, elapsed_s=10.0)
    assert rates["science_line"]["output_rate_per_s"] is None


def test_rate_tracker_returns_none_on_zero_or_negative_elapsed():
    previous = {"science_line": {"produced_count": 100.0}}
    current = {"science_line": {"produced_count": 200.0}}
    assert rate_tracker(previous, current, elapsed_s=0.0)["science_line"]["output_rate_per_s"] is None
    assert rate_tracker(previous, current, elapsed_s=-5.0)["science_line"]["output_rate_per_s"] is None


def test_rate_tracker_treats_backwards_counter_as_none_not_negative_rate():
    # A negative delta (force stats reset / reload) must not be reported as a
    # negative rate - that would be nonsense, not a real measurement.
    previous = {"science_line": {"produced_count": 500.0}}
    current = {"science_line": {"produced_count": 100.0}}
    rates = rate_tracker(previous, current, elapsed_s=10.0)
    assert rates["science_line"]["output_rate_per_s"] is None


def test_rate_tracker_ignores_global_key():
    previous = {"_global": {"research_progress": 0.1}, "line_a": {"produced_count": 10.0}}
    current = {"_global": {"research_progress": 0.2}, "line_a": {"produced_count": 20.0}}
    rates = rate_tracker(previous, current, elapsed_s=5.0)
    assert "_global" not in rates
    assert rates["line_a"]["output_rate_per_s"] == 2.0
