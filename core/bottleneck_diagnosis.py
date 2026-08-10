# Path: core/bottleneck_diagnosis.py
# Purpose: Deterministic per-line bottleneck verdict from live measurements — the
#   core observation feeding the future RL decision layer (see docs/rl/README.md).
#   This module is pure diagnosis: it does not plan, build, or decide WHEN to act.

from __future__ import annotations

from typing import Dict, Optional

# Layering: core/ must not import planners/. All line context arrives as plain
# dicts (spec, measurements) — no planner objects, no game-state coupling.

# --- Thresholds (documented, not tuned against live data yet) ---
MAJORITY_THRESHOLD = 0.5       # fraction of machines a status must exceed to be "majority"
SATURATION_THRESHOLD = 0.85    # belt/collector fill ratio counted as "saturated"/"full"
EMPTY_THRESHOLD = 0.15         # belt fill ratio counted as "near-empty"
CAPACITY_HEALTHY_RATIO = 0.90  # measured/theoretical ratio counted as "at capacity"
RESOURCE_DEPLETED_THRESHOLD = 0.05  # remaining-patch ratio counted as "depleted"

VERDICTS = {
    "machine_limited",
    "feed_limited",
    "drain_limited",
    "input_starved",
    "power_limited",
    "resource_depleted",
    "healthy_ramping",
    "unknown",
}


def _result(verdict: str, confidence: str, rationale: str, recommended_action: str) -> Dict[str, object]:
    return {
        "verdict": verdict,
        "confidence": confidence,
        "rationale": rationale,
        "recommended_action": recommended_action,
    }


def _status_ratio(status_counts: Dict[str, int], total: int, *keys: str) -> float:
    if total <= 0:
        return 0.0
    return sum(int(status_counts.get(key, 0)) for key in keys) / total


def _belt_ratio(measurements: dict, count_key: str, capacity_key: str) -> Optional[float]:
    count = measurements.get(count_key)
    capacity = measurements.get(capacity_key)
    if count is None or capacity is None:
        return None
    capacity = float(capacity)
    if capacity <= 0:
        return None
    return max(0.0, min(1.0, float(count) / capacity))


def diagnose_line(spec: dict, measurements: dict) -> dict:
    """
    Classify a production line's bottleneck. Rules, in order (first match wins):
      1. power_limited: majority no_power/low_power -> fix_power. (high)
      2. Majority item_ingredient_shortage:
         a. belt empty at start AND end: mining_feed + resource_remaining_ratio
            <= 0.05 -> resource_depleted -> open_new_mine (high); else
            input_starved -> connect_input_source (high)
         b. belt saturated at start, empty at end -> feed_limited ->
            add_feed_points (high: supply reaches the line, just not the end)
         c. belt partial (not empty, not saturated) at start, empty at end ->
            feed_limited -> upgrade_belt_tier (low: belt vs. inserter throughput
            is not distinguishable from these measurements alone — see notes)
         d. shortage with no usable belt signal -> feed_limited ->
            add_feed_points (low fallback)
      3. Majority full_output, OR output belt/collectors saturated, OR
         collectors_full -> drain_limited -> add_collectors (high)
      4. Majority working:
         - output >= 90% of theoretical capacity -> machine_limited ->
           extend_line_x (add_parallel_line if spec["at_max_length"]) (high)
         - capacity missing/<=0 -> unknown -> wait (low)
         - otherwise -> healthy_ramping -> wait (low: may be a cold start)
      5. No decisive majority -> unknown -> wait (low)

    spec (optional): at_max_length (bool), mining_feed (bool).
    measurements: status_counts (dict of Factorio entity_status name -> count),
      measured_output_per_s, theoretical_capacity_per_s (same unit as measured
      output — unit agreement is the caller's responsibility, not verified
      here), input_belt_{start,end}_{count,capacity}, output_belt_{count,
      capacity}, collectors_full (bool), resource_remaining_ratio (0..1,
      consulted only when spec["mining_feed"]).

    Returns a plain JSON-serializable dict: verdict, confidence ("high"/"low"),
    rationale (sentence citing the numbers used), recommended_action.

    Escalated ambiguity: rule 2c cannot tell a belt-throughput limit from an
    inserter-throughput limit apart with only belt fill ratios; it defaults to
    upgrade_belt_tier at low confidence rather than guessing inserter tier.
    """
    spec = spec or {}
    measurements = measurements or {}

    status_counts = measurements.get("status_counts") or {}
    total_machines = sum(int(v) for v in status_counts.values())

    power_ratio = _status_ratio(status_counts, total_machines, "no_power", "low_power")
    shortage_ratio = _status_ratio(status_counts, total_machines, "item_ingredient_shortage")
    full_output_ratio = _status_ratio(status_counts, total_machines, "full_output")
    working_ratio = _status_ratio(status_counts, total_machines, "working")

    start_ratio = _belt_ratio(measurements, "input_belt_start_count", "input_belt_start_capacity")
    end_ratio = _belt_ratio(measurements, "input_belt_end_count", "input_belt_end_capacity")
    output_ratio = _belt_ratio(measurements, "output_belt_count", "output_belt_capacity")
    collectors_full = bool(measurements.get("collectors_full", False))

    if total_machines > 0 and power_ratio > MAJORITY_THRESHOLD:
        return _result(
            "power_limited", "high",
            f"{power_ratio:.0%} of machines report no_power/low_power.",
            "fix_power",
        )

    if total_machines > 0 and shortage_ratio > MAJORITY_THRESHOLD:
        end_empty = end_ratio is not None and end_ratio <= EMPTY_THRESHOLD
        start_empty = start_ratio is not None and start_ratio <= EMPTY_THRESHOLD
        start_saturated = start_ratio is not None and start_ratio >= SATURATION_THRESHOLD

        if end_empty and start_empty:
            resource_remaining = measurements.get("resource_remaining_ratio")
            if spec.get("mining_feed") and resource_remaining is not None and float(resource_remaining) <= RESOURCE_DEPLETED_THRESHOLD:
                return _result(
                    "resource_depleted", "high",
                    f"{shortage_ratio:.0%} ingredient_shortage with mining feed at "
                    f"{float(resource_remaining):.0%} patch remaining.",
                    "open_new_mine",
                )
            return _result(
                "input_starved", "high",
                f"{shortage_ratio:.0%} ingredient_shortage and input belt empty at both "
                f"start ({start_ratio:.0%}) and end ({end_ratio:.0%}).",
                "connect_input_source",
            )

        if end_empty and start_saturated:
            return _result(
                "feed_limited", "high",
                f"{shortage_ratio:.0%} ingredient_shortage; input belt saturated at start "
                f"({start_ratio:.0%}) but empty at end ({end_ratio:.0%}).",
                "add_feed_points",
            )

        if end_empty and start_ratio is not None:
            return _result(
                "feed_limited", "low",
                f"{shortage_ratio:.0%} ingredient_shortage; input belt only {start_ratio:.0%} "
                f"full at start yet still empty at end ({end_ratio:.0%}); belt vs. inserter "
                "throughput cannot be distinguished from these measurements.",
                "upgrade_belt_tier",
            )

        return _result(
            "feed_limited", "low",
            f"{shortage_ratio:.0%} ingredient_shortage without a decisive belt-empty signal "
            "at the line end.",
            "add_feed_points",
        )

    output_saturated = output_ratio is not None and output_ratio >= SATURATION_THRESHOLD
    if total_machines > 0 and (full_output_ratio > MAJORITY_THRESHOLD or output_saturated or collectors_full):
        return _result(
            "drain_limited", "high",
            f"{full_output_ratio:.0%} full_output status, output belt fill "
            f"{'n/a' if output_ratio is None else f'{output_ratio:.0%}'}, collectors_full={collectors_full}.",
            "add_collectors",
        )

    if total_machines > 0 and working_ratio > MAJORITY_THRESHOLD:
        capacity = float(measurements.get("theoretical_capacity_per_s") or 0.0)
        measured = float(measurements.get("measured_output_per_s") or 0.0)
        if capacity <= 0:
            return _result(
                "unknown", "low",
                f"{working_ratio:.0%} working but theoretical_capacity_per_s is missing, "
                "so headroom cannot be judged.",
                "wait",
            )
        capacity_ratio = measured / capacity
        if capacity_ratio >= CAPACITY_HEALTHY_RATIO:
            action = "add_parallel_line" if spec.get("at_max_length") else "extend_line_x"
            return _result(
                "machine_limited", "high",
                f"{working_ratio:.0%} working and measured output {measured:.2f}/s is "
                f"{capacity_ratio:.0%} of theoretical capacity {capacity:.2f}/s.",
                action,
            )
        return _result(
            "healthy_ramping", "low",
            f"{working_ratio:.0%} working but measured output {measured:.2f}/s is only "
            f"{capacity_ratio:.0%} of theoretical capacity {capacity:.2f}/s; likely still ramping.",
            "wait",
        )

    return _result(
        "unknown", "low",
        "No status majority or belt signal was decisive enough to attribute a bottleneck cause.",
        "wait",
    )
