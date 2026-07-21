# Path: tests/test_action_catalog.py
# Purpose: Tests for core/action_catalog.py (catalog compilation) and
#   core/baseline_policy.py (greedy bottleneck-relief baseline over that
#   catalog) — the two halves of the N1 action-catalog + baseline-policy
#   slice from docs/22_rl_decision_layer.md. Bounded to one test file per
#   task scope; policy tests live here alongside catalog tests.

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.action_catalog import build_catalog
from core.baseline_policy import choose_action, explain


def _measurements(**lines_by_name):
    return {"lines": lines_by_name, "global": {}}


# --------------------------------------------------------------------------
# build_catalog
# --------------------------------------------------------------------------

def test_catalog_emits_nothing_for_healthy_line():
    chain = [{"name": "gear_line", "recipe": "iron-gear-wheel", "machines": 4}]
    measurements = _measurements(
        gear_line={
            "status_counts": {"working": 4},
            "measured_output_per_s": 3.0,
            "theoretical_capacity_per_s": 9.0,
        }
    )
    assert build_catalog(chain, measurements) == []


def test_catalog_emits_nothing_for_unknown_wait_line():
    chain = [{"name": "mystery_line", "recipe": "widget", "machines": 2}]
    measurements = _measurements(mystery_line={})
    assert build_catalog(chain, measurements) == []


def test_feed_limited_line_yields_add_feed_points_with_positive_delta():
    chain = [{"name": "gear_line", "recipe": "iron-gear-wheel", "machines": 6}]
    measurements = _measurements(
        gear_line={
            "status_counts": {"working": 2, "item_ingredient_shortage": 4},
            "measured_output_per_s": 2.0,
            "theoretical_capacity_per_s": 9.0,
            "input_belt_start_count": 95,
            "input_belt_start_capacity": 100,
            "input_belt_end_count": 2,
            "input_belt_end_capacity": 100,
        }
    )
    catalog = build_catalog(chain, measurements)
    assert len(catalog) == 1
    entry = catalog[0]
    assert entry["action"] == "add_feed_points"
    assert entry["target_line"] == "gear_line"
    assert entry["predicted_effect"]["delta"] == 7.0  # 9.0 capacity - 2.0 measured
    assert entry["predicted_effect"]["confidence"] == "high"
    assert entry["predicted_effect"]["metric"] == "output_items_per_s"
    assert "materials_estimate" in entry["cost"] and entry["cost"]["disruption"] in ("none", "brief")


def test_extend_line_x_params_and_delta():
    chain = [{"name": "gear_line", "recipe": "iron-gear-wheel", "machines": 4}]
    measurements = _measurements(
        gear_line={
            "status_counts": {"working": 4},
            "measured_output_per_s": 8.7,
            "theoretical_capacity_per_s": 9.0,
        }
    )
    catalog = build_catalog(chain, measurements)
    assert len(catalog) == 1
    entry = catalog[0]
    assert entry["action"] == "extend_line_x"
    # headroom default 1.25: floor(4*1.25) = 5 -> k=1 -> new_machines=5
    assert entry["params"]["new_machines"] == 5
    per_machine_rate = 8.7 / 4
    assert abs(entry["predicted_effect"]["delta"] - per_machine_rate) < 1e-9


def test_add_parallel_line_at_max_length():
    chain = [{"name": "gear_line", "recipe": "iron-gear-wheel", "machines": 6, "at_max_length": True}]
    measurements = _measurements(
        gear_line={
            "status_counts": {"working": 6},
            "measured_output_per_s": 9.0,
            "theoretical_capacity_per_s": 9.0,
        }
    )
    catalog = build_catalog(chain, measurements)
    assert len(catalog) == 1
    entry = catalog[0]
    assert entry["action"] == "add_parallel_line"
    assert entry["params"]["new_machines"] == 6
    assert entry["predicted_effect"]["delta"] == 9.0


def test_upgrade_belt_tier_delta_bounded_by_next_tier_and_capacity():
    chain = [{"name": "gear_line", "recipe": "iron-gear-wheel", "machines": 6, "belt_type": "transport-belt"}]
    measurements = _measurements(
        gear_line={
            "status_counts": {"working": 1, "item_ingredient_shortage": 5},
            "measured_output_per_s": 1.0,
            "theoretical_capacity_per_s": 9.0,
            "input_belt_start_count": 40,
            "input_belt_start_capacity": 100,
            "input_belt_end_count": 2,
            "input_belt_end_capacity": 100,
        }
    )
    limits = {
        "belt_tiers": {"transport-belt": 15, "fast-transport-belt": 30, "express-transport-belt": 45},
    }
    catalog = build_catalog(chain, measurements, limits)
    assert len(catalog) == 1
    entry = catalog[0]
    assert entry["action"] == "upgrade_belt_tier"
    assert entry["params"]["new_belt_type"] == "fast-transport-belt"
    # ceiling = min(next tier 30, theoretical capacity 9.0) = 9.0; delta = 9.0 - 1.0
    assert entry["predicted_effect"]["delta"] == 8.0
    assert entry["predicted_effect"]["confidence"] == "low"


def test_upgrade_belt_tier_skipped_when_already_at_top_tier():
    chain = [{"name": "gear_line", "recipe": "iron-gear-wheel", "machines": 6, "belt_type": "express-transport-belt"}]
    measurements = _measurements(
        gear_line={
            "status_counts": {"working": 1, "item_ingredient_shortage": 5},
            "measured_output_per_s": 1.0,
            "theoretical_capacity_per_s": 9.0,
            "input_belt_start_count": 40,
            "input_belt_start_capacity": 100,
            "input_belt_end_count": 2,
            "input_belt_end_capacity": 100,
        }
    )
    limits = {
        "belt_tiers": {"transport-belt": 15, "fast-transport-belt": 30, "express-transport-belt": 45},
    }
    assert build_catalog(chain, measurements, limits) == []


def test_deterministic_ordering_ties_broken_by_action_then_target_line():
    # Two lines whose diagnoses land on an equal 3.0/s shortfall but through
    # different verdicts/actions: add_collectors ("line_a") must sort before
    # add_feed_points ("line_b") on the tie.
    chain = [
        {"name": "line_a", "recipe": "product_a", "machines": 2},
        {"name": "line_b", "recipe": "product_b", "machines": 2},
    ]
    measurements = _measurements(
        line_a={
            "status_counts": {"working": 1, "full_output": 5},
            "measured_output_per_s": 2.0,
            "theoretical_capacity_per_s": 5.0,
            "collectors_full": True,
        },
        line_b={
            "status_counts": {"item_ingredient_shortage": 6},
            "measured_output_per_s": 2.0,
            "theoretical_capacity_per_s": 5.0,
        },
    )
    catalog = build_catalog(chain, measurements)
    assert [(e["action"], e["target_line"]) for e in catalog] == [
        ("add_collectors", "line_a"),
        ("add_feed_points", "line_b"),
    ]
    assert catalog[0]["predicted_effect"]["delta"] == catalog[1]["predicted_effect"]["delta"] == 3.0


def test_build_catalog_is_deterministic_across_repeat_calls():
    chain = [
        {"name": "line_a", "recipe": "product_a", "machines": 2},
        {"name": "line_b", "recipe": "product_b", "machines": 2},
    ]
    measurements = _measurements(
        line_a={
            "status_counts": {"working": 1, "full_output": 5},
            "measured_output_per_s": 2.0,
            "theoretical_capacity_per_s": 5.0,
            "collectors_full": True,
        },
        line_b={
            "status_counts": {"item_ingredient_shortage": 6},
            "measured_output_per_s": 2.0,
            "theoretical_capacity_per_s": 5.0,
        },
    )
    first = build_catalog(chain, measurements)
    second = build_catalog(chain, measurements)
    assert first == second


def test_catalog_result_is_json_serializable():
    import json

    chain = [{"name": "gear_line", "recipe": "iron-gear-wheel", "machines": 4}]
    measurements = _measurements(
        gear_line={
            "status_counts": {"working": 4},
            "measured_output_per_s": 8.7,
            "theoretical_capacity_per_s": 9.0,
        }
    )
    catalog = build_catalog(chain, measurements)
    encoded = json.dumps(catalog)
    assert json.loads(encoded) == catalog


# --------------------------------------------------------------------------
# baseline_policy.choose_action / explain
# --------------------------------------------------------------------------

def test_policy_picks_binding_upstream_line_over_bigger_downstream_delta():
    # gear_line (terminal, produces the target product) is machine_limited
    # with a LARGER raw predicted delta than smelt_line's shortfall.
    # smelt_line (upstream, feeds gear_line) is input_starved. The upstream
    # starvation must win even though it predicts a smaller delta.
    chain = [
        {
            "name": "gear_line", "recipe": "iron-gear-wheel", "machines": 20,
            "producers": ["smelt_line"], "consumers": [],
        },
        {
            "name": "smelt_line", "recipe": "iron-plate", "machines": 4,
            "producers": [], "consumers": ["gear_line"],
        },
    ]
    measurements = {
        "lines": {
            "gear_line": {
                "status_counts": {"working": 20},
                "measured_output_per_s": 19.5,
                "theoretical_capacity_per_s": 20.0,
            },
            "smelt_line": {
                "status_counts": {"working": 1, "item_ingredient_shortage": 5},
                "measured_output_per_s": 0.5,
                "theoretical_capacity_per_s": 1.0,
                "input_belt_start_count": 0,
                "input_belt_start_capacity": 100,
                "input_belt_end_count": 0,
                "input_belt_end_capacity": 100,
            },
        },
        "global": {"target_product": "iron-gear-wheel"},
    }
    catalog = build_catalog(chain, measurements)

    # Sanity: gear_line's raw delta really is bigger than smelt_line's.
    by_line = {e["target_line"]: e for e in catalog}
    assert by_line["gear_line"]["predicted_effect"]["delta"] > by_line["smelt_line"]["predicted_effect"]["delta"]

    state = {"chain": chain, "measurements": measurements}  # target_product via measurements["global"]
    chosen = choose_action(catalog, state)
    assert chosen["target_line"] == "smelt_line"
    assert chosen["action"] == "connect_input_source"

    sentence = explain(chosen, catalog, state)
    assert "smelt_line" in sentence
    assert "connect_input_source" in sentence


def test_policy_falls_back_to_extending_terminal_line_when_nothing_binds():
    # Both lines are machine_limited (nothing "broken"). smelt_line's own
    # extend_line_x delta is much bigger than gear_line's, but gear_line is
    # the terminal (target-product) line, so the fallback must pick
    # gear_line's extend_line_x, not smelt_line's larger one.
    chain = [
        {
            "name": "gear_line", "recipe": "iron-gear-wheel", "machines": 6,
            "producers": ["smelt_line"], "consumers": [],
        },
        {
            "name": "smelt_line", "recipe": "iron-plate", "machines": 50,
            "producers": [], "consumers": ["gear_line"],
        },
    ]
    measurements = {
        "lines": {
            "gear_line": {
                "status_counts": {"working": 6},
                "measured_output_per_s": 8.7,
                "theoretical_capacity_per_s": 9.0,
            },
            "smelt_line": {
                "status_counts": {"working": 50},
                "measured_output_per_s": 49.0,
                "theoretical_capacity_per_s": 50.0,
            },
        },
        "global": {"target_product": "iron-gear-wheel"},
    }
    catalog = build_catalog(chain, measurements)
    by_line = {e["target_line"]: e for e in catalog}
    assert by_line["smelt_line"]["predicted_effect"]["delta"] > by_line["gear_line"]["predicted_effect"]["delta"]

    state = {"chain": chain, "measurements": measurements}
    chosen = choose_action(catalog, state)
    assert chosen["target_line"] == "gear_line"
    assert chosen["action"] == "extend_line_x"


def test_policy_returns_none_for_empty_catalog():
    state = {"chain": [], "measurements": {"lines": {}, "global": {}}}
    assert choose_action([], state) is None


def test_explain_mentions_line_and_action_for_none_choice():
    state = {"chain": [], "measurements": {"lines": {}, "global": {}}}
    sentence = explain(None, [], state)
    assert isinstance(sentence, str) and len(sentence) > 0
