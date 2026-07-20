# Path: tests/test_bottleneck_diagnosis.py
# Purpose: One test per diagnose_line verdict path, plus a determinism check.

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.bottleneck_diagnosis import diagnose_line


def test_power_limited():
    spec = {}
    measurements = {
        "status_counts": {"no_power": 5, "working": 1},
        "measured_output_per_s": 0.0,
        "theoretical_capacity_per_s": 9.0,
    }
    result = diagnose_line(spec, measurements)
    assert result["verdict"] == "power_limited"
    assert result["confidence"] == "high"
    assert result["recommended_action"] == "fix_power"
    assert "no_power" in result["rationale"] or "power" in result["rationale"]


def test_feed_limited_real_case_add_feed_points():
    # 6-machine circuit line, capacity 9/s, measured 2/s: 2 working + 4
    # item_ingredient_shortage; input belt saturated at start, empty at end.
    spec = {}
    measurements = {
        "status_counts": {"working": 2, "item_ingredient_shortage": 4},
        "measured_output_per_s": 2.0,
        "theoretical_capacity_per_s": 9.0,
        "input_belt_start_count": 95,
        "input_belt_start_capacity": 100,
        "input_belt_end_count": 2,
        "input_belt_end_capacity": 100,
    }
    result = diagnose_line(spec, measurements)
    assert result["verdict"] == "feed_limited"
    assert result["confidence"] == "high"
    assert result["recommended_action"] == "add_feed_points"
    assert "ingredient_shortage" in result["rationale"]


def test_feed_limited_partial_start_upgrades_belt_tier():
    # Ingredient shortage majority, but the start belt is only partially
    # filled (neither saturated nor empty) while the end is empty: belt vs.
    # inserter throughput is ambiguous, so this defaults to upgrade_belt_tier
    # at low confidence.
    spec = {}
    measurements = {
        "status_counts": {"working": 1, "item_ingredient_shortage": 5},
        "measured_output_per_s": 1.0,
        "theoretical_capacity_per_s": 9.0,
        "input_belt_start_count": 40,
        "input_belt_start_capacity": 100,
        "input_belt_end_count": 2,
        "input_belt_end_capacity": 100,
    }
    result = diagnose_line(spec, measurements)
    assert result["verdict"] == "feed_limited"
    assert result["confidence"] == "low"
    assert result["recommended_action"] == "upgrade_belt_tier"


def test_input_starved_connects_external_source():
    spec = {}
    measurements = {
        "status_counts": {"working": 1, "item_ingredient_shortage": 5},
        "measured_output_per_s": 0.5,
        "theoretical_capacity_per_s": 9.0,
        "input_belt_start_count": 2,
        "input_belt_start_capacity": 100,
        "input_belt_end_count": 0,
        "input_belt_end_capacity": 100,
    }
    result = diagnose_line(spec, measurements)
    assert result["verdict"] == "input_starved"
    assert result["confidence"] == "high"
    assert result["recommended_action"] == "connect_input_source"


def test_resource_depleted_opens_new_mine():
    spec = {"mining_feed": True}
    measurements = {
        "status_counts": {"working": 1, "item_ingredient_shortage": 5},
        "measured_output_per_s": 0.3,
        "theoretical_capacity_per_s": 9.0,
        "input_belt_start_count": 0,
        "input_belt_start_capacity": 100,
        "input_belt_end_count": 0,
        "input_belt_end_capacity": 100,
        "resource_remaining_ratio": 0.02,
    }
    result = diagnose_line(spec, measurements)
    assert result["verdict"] == "resource_depleted"
    assert result["confidence"] == "high"
    assert result["recommended_action"] == "open_new_mine"


def test_drain_limited_adds_collectors():
    spec = {}
    measurements = {
        "status_counts": {"working": 1, "full_output": 5},
        "measured_output_per_s": 9.0,
        "theoretical_capacity_per_s": 9.0,
        "collectors_full": True,
    }
    result = diagnose_line(spec, measurements)
    assert result["verdict"] == "drain_limited"
    assert result["confidence"] == "high"
    assert result["recommended_action"] == "add_collectors"


def test_machine_limited_extends_line():
    spec = {}
    measurements = {
        "status_counts": {"working": 6},
        "measured_output_per_s": 8.7,
        "theoretical_capacity_per_s": 9.0,
    }
    result = diagnose_line(spec, measurements)
    assert result["verdict"] == "machine_limited"
    assert result["confidence"] == "high"
    assert result["recommended_action"] == "extend_line_x"


def test_machine_limited_at_max_length_adds_parallel_line():
    spec = {"at_max_length": True}
    measurements = {
        "status_counts": {"working": 6},
        "measured_output_per_s": 9.0,
        "theoretical_capacity_per_s": 9.0,
    }
    result = diagnose_line(spec, measurements)
    assert result["verdict"] == "machine_limited"
    assert result["recommended_action"] == "add_parallel_line"


def test_healthy_ramping_waits():
    spec = {}
    measurements = {
        "status_counts": {"working": 6},
        "measured_output_per_s": 3.0,
        "theoretical_capacity_per_s": 9.0,
    }
    result = diagnose_line(spec, measurements)
    assert result["verdict"] == "healthy_ramping"
    assert result["confidence"] == "low"
    assert result["recommended_action"] == "wait"


def test_unknown_when_no_decisive_status_majority():
    spec = {}
    measurements = {
        "status_counts": {"working": 2, "item_ingredient_shortage": 2, "full_output": 2},
        "measured_output_per_s": 4.0,
        "theoretical_capacity_per_s": 9.0,
    }
    result = diagnose_line(spec, measurements)
    assert result["verdict"] == "unknown"
    assert result["confidence"] == "low"
    assert result["recommended_action"] == "wait"


def test_unknown_when_empty_measurements():
    result = diagnose_line({}, {})
    assert result["verdict"] == "unknown"
    assert result["recommended_action"] == "wait"


def test_diagnosis_is_deterministic():
    spec = {"at_max_length": False, "mining_feed": True}
    measurements = {
        "status_counts": {"working": 2, "item_ingredient_shortage": 4},
        "measured_output_per_s": 2.0,
        "theoretical_capacity_per_s": 9.0,
        "input_belt_start_count": 95,
        "input_belt_start_capacity": 100,
        "input_belt_end_count": 2,
        "input_belt_end_capacity": 100,
        "resource_remaining_ratio": 0.5,
    }
    first = diagnose_line(spec, measurements)
    second = diagnose_line(spec, measurements)
    assert first == second


def test_result_is_json_serializable():
    import json

    spec = {}
    measurements = {
        "status_counts": {"working": 6},
        "measured_output_per_s": 8.7,
        "theoretical_capacity_per_s": 9.0,
    }
    result = diagnose_line(spec, measurements)
    encoded = json.dumps(result)
    assert json.loads(encoded) == result
