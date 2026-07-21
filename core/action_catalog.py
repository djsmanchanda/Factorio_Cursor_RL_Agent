# Path: core/action_catalog.py
# Purpose: Compile per-line bottleneck diagnoses into an ACTION CATALOG of
#   executable expansion options with predicted effects (docs/22_rl_decision_layer.md).
#   Deterministic planners/diagnosis own "what could be done"; a policy
#   (core/baseline_policy.py now, RL later) owns "which one to do".
#   This module is pure catalog compilation: it does not plan, build, or
#   decide WHEN to act.

from __future__ import annotations

import math
from typing import Dict, List, Optional

# Layering: core/ must not import planners/. All line context arrives as
# plain dicts (chain specs, measurements, limits) — no planner objects, no
# game-state coupling. The one permitted core-to-core import is the
# diagnosis this catalog is built from.
from core.bottleneck_diagnosis import diagnose_line

# --- Cost-model constants (documented placeholders) ---------------------
# These are coarse, deterministic ORDERING estimates used only to populate
# "materials_estimate" for cost-aware ranking later (e.g. by RL or a human
# reviewer). They are NOT a real Factorio bill-of-materials. Escalated: no
# real material-cost model exists yet; refine these against actual recipe
# costs once one does.
MATERIALS_PER_MACHINE = 8
TIER_UPGRADE_MATERIALS_PER_MACHINE = 3
FEED_POINT_MATERIALS = 6
COLLECTOR_MATERIALS = 6
NEW_MINE_MATERIALS = 40
INPUT_SOURCE_MATERIALS = 20
POWER_FIX_MATERIALS = 4

# Disruption model: "brief" for actions that touch/replace parts of the
# already-running line (extending its belt, swapping belt/inserter tier,
# rewiring power); "none" for purely additive infrastructure built alongside
# the running line (parallel lines, new mines, new feed/collector taps,
# new input connections). Documented assumption, not measured in-game.
ACTION_DISRUPTION = {
    "extend_line_x": "brief",
    "add_parallel_line": "none",
    "upgrade_belt_tier": "brief",
    "upgrade_inserter_tier": "brief",
    "add_feed_points": "none",
    "add_collectors": "none",
    "open_new_mine": "none",
    "connect_input_source": "none",
    "fix_power": "brief",
}

_SHORTFALL_MATERIALS = {
    "add_feed_points": FEED_POINT_MATERIALS,
    "add_collectors": COLLECTOR_MATERIALS,
    "open_new_mine": NEW_MINE_MATERIALS,
    "connect_input_source": INPUT_SOURCE_MATERIALS,
    "fix_power": POWER_FIX_MATERIALS,
}


def _machines(spec: dict) -> int:
    return int(spec.get("machines") or 0)


def _measured(m: dict) -> float:
    return float(m.get("measured_output_per_s") or 0.0)


def _capacity(m: dict) -> float:
    return float(m.get("theoretical_capacity_per_s") or 0.0)


def _extend_line_x_entry(spec: dict, m: dict, diagnosis: dict, limits: dict) -> dict:
    """Rule 2 (extend_line_x): add k machines, k chosen so the new machine
    count stays within FEED headroom of the current count when possible
    (limits["feed_headroom"], default 1.25 — the feed/belt system is
    provisioned to support up to headroom x the currently built machine
    count before it becomes the bottleneck itself; see docs/21 "25% capacity
    headroom on feed and drain provisioning"). If headroom would round down
    to 0 extra machines (e.g. a 1-machine line), k is floored at 1 — you can
    always extend by at least one machine.

    predicted delta = per-machine rate * k. Per-machine rate is measured
    output / current machines when both are available (line has run and
    produced something); otherwise limits["machine_rate"] is used as a
    fallback (e.g. a brand-new line with no measured history yet).
    """
    n = _machines(spec)
    measured = _measured(m)
    if n > 0 and measured > 0.0:
        per_machine_rate = measured / n
    else:
        per_machine_rate = float(limits.get("machine_rate") or 0.0)

    headroom = float(limits.get("feed_headroom", 1.25))
    max_machines_in_headroom = math.floor(n * headroom)
    k = max_machines_in_headroom - n
    if k < 1:
        k = 1
    new_machines = n + k
    delta = per_machine_rate * k

    name = spec.get("name")
    return {
        "action": "extend_line_x",
        "target_line": name,
        "params": {"new_machines": new_machines},
        "predicted_effect": {
            "metric": "output_items_per_s",
            "delta": delta,
            "confidence": diagnosis["confidence"],
        },
        "rationale": (
            f"{name} is machine_limited ({diagnosis['rationale']}); adding {k} "
            f"machine(s) (staying within {headroom:.2f}x feed headroom) is "
            f"predicted to add {delta:.2f} items/s."
        ),
        "cost": {
            "materials_estimate": k * MATERIALS_PER_MACHINE,
            "disruption": ACTION_DISRUPTION["extend_line_x"],
        },
    }


def _add_parallel_line_entry(spec: dict, m: dict, diagnosis: dict, limits: dict) -> dict:
    """add_parallel_line (machine_limited AND at_max_length: the line cannot
    grow along X anymore). Not covered by an explicit delta rule in the
    task spec; documented assumption — a new parallel line is sized as a
    duplicate of the current line (same machine count), so its predicted
    delta is the current line's own measured output (it replicates what the
    existing line already achieves). Escalated: this sizing/costing choice
    should be reviewed once a real parallel-line planner exists.
    """
    n = _machines(spec)
    delta = _measured(m)
    name = spec.get("name")
    return {
        "action": "add_parallel_line",
        "target_line": name,
        "params": {"new_machines": n},
        "predicted_effect": {
            "metric": "output_items_per_s",
            "delta": delta,
            "confidence": diagnosis["confidence"],
        },
        "rationale": (
            f"{name} is machine_limited and at_max_length ({diagnosis['rationale']}); "
            f"a duplicate parallel line of {n} machine(s) is predicted to add "
            f"{delta:.2f} items/s."
        ),
        "cost": {
            "materials_estimate": n * MATERIALS_PER_MACHINE,
            "disruption": ACTION_DISRUPTION["add_parallel_line"],
        },
    }


def _tier_upgrade_entry(spec: dict, m: dict, diagnosis: dict, limits: dict, kind: str) -> Optional[dict]:
    """upgrade_belt_tier / upgrade_inserter_tier: predicted delta =
    min(next tier lane capacity, machine capacity) - current measured
    output. Tier tables (limits["belt_tiers"] / limits["inserter_rates"])
    must be ordered mappings of tier_name -> capacity_per_s in ascending
    tier order (current tier's dict position determines "next"). If the
    line's current tier is unknown to the table, or is already the last
    (fastest) tier, or the computed delta is not positive, no upgrade
    candidate is emitted for this line (nothing useful to upgrade to).

    Note: bottleneck_diagnosis never actually recommends
    "upgrade_inserter_tier" today (it cannot distinguish belt vs. inserter
    throughput limits from belt-fill measurements alone — see its
    docstring), so this branch is currently unreachable from live
    diagnosis and exists for forward-compatibility / direct testing.
    """
    if kind == "belt":
        action = "upgrade_belt_tier"
        tiers = limits.get("belt_tiers") or {}
        current_tier = spec.get("belt_type")
        param_key = "new_belt_type"
    else:
        action = "upgrade_inserter_tier"
        tiers = limits.get("inserter_rates") or {}
        current_tier = spec.get("inserter_type")
        param_key = "new_inserter_type"

    tier_names = list(tiers.keys())
    if current_tier not in tier_names:
        return None
    idx = tier_names.index(current_tier)
    if idx + 1 >= len(tier_names):
        return None
    next_tier = tier_names[idx + 1]
    next_capacity = float(tiers[next_tier])

    capacity = _capacity(m)
    ceiling = min(next_capacity, capacity) if capacity > 0 else next_capacity
    measured = _measured(m)
    delta = ceiling - measured
    if delta <= 0:
        return None

    name = spec.get("name")
    return {
        "action": action,
        "target_line": name,
        "params": {param_key: next_tier},
        "predicted_effect": {
            "metric": "output_items_per_s",
            "delta": delta,
            "confidence": diagnosis["confidence"],
        },
        "rationale": (
            f"{name}: {diagnosis['rationale']} Upgrading to {next_tier} raises the "
            f"achievable ceiling to {ceiling:.2f} items/s (+{delta:.2f}/s)."
        ),
        "cost": {
            "materials_estimate": _machines(spec) * TIER_UPGRADE_MATERIALS_PER_MACHINE,
            "disruption": ACTION_DISRUPTION[action],
        },
    }


def _shortfall_entry(spec: dict, m: dict, diagnosis: dict, limits: dict, action: str) -> dict:
    """add_feed_points / add_collectors / open_new_mine / connect_input_source
    / fix_power: predicted delta = the shortfall the diagnosis implies
    (measured capacity - measured output), confidence taken directly from
    the diagnosis. These five actions all describe "restore feed, drain, or
    power so the line reaches the capacity its machines already support" —
    fix_power/open_new_mine/connect_input_source are not given an explicit
    separate delta rule in the task spec, so the same shortfall formula is
    applied to them for consistency (documented assumption).
    """
    capacity = _capacity(m)
    measured = _measured(m)
    delta = max(0.0, capacity - measured)
    name = spec.get("name")
    return {
        "action": action,
        "target_line": name,
        "params": {},
        "predicted_effect": {
            "metric": "output_items_per_s",
            "delta": delta,
            "confidence": diagnosis["confidence"],
        },
        "rationale": (
            f"{name}: {diagnosis['rationale']} Resolving this is predicted to close a "
            f"{delta:.2f} items/s shortfall to the {capacity:.2f} items/s theoretical capacity."
        ),
        "cost": {
            "materials_estimate": _SHORTFALL_MATERIALS.get(action, FEED_POINT_MATERIALS),
            "disruption": ACTION_DISRUPTION.get(action, "none"),
        },
    }


def _build_entry(spec: dict, m: dict, diagnosis: dict, limits: dict) -> Optional[dict]:
    action = diagnosis["recommended_action"]
    if action == "extend_line_x":
        return _extend_line_x_entry(spec, m, diagnosis, limits)
    if action == "add_parallel_line":
        return _add_parallel_line_entry(spec, m, diagnosis, limits)
    if action == "upgrade_belt_tier":
        return _tier_upgrade_entry(spec, m, diagnosis, limits, "belt")
    if action == "upgrade_inserter_tier":
        return _tier_upgrade_entry(spec, m, diagnosis, limits, "inserter")
    if action in ("add_feed_points", "add_collectors", "open_new_mine", "connect_input_source", "fix_power"):
        return _shortfall_entry(spec, m, diagnosis, limits, action)
    # "wait" (healthy_ramping / unknown) is filtered by the caller before
    # this is reached; any other unrecognized action is dropped defensively
    # rather than guessed at.
    return None


def build_catalog(chain: List[dict], measurements: dict, limits: Optional[dict] = None) -> List[dict]:
    """Build the action catalog for a chain of production lines.

    Args:
        chain: list of line specs (see module docstring / docs/22 for shape).
        measurements: {"lines": {line_name: <per-line measurements dict as
            core.bottleneck_diagnosis.diagnose_line expects>}, "global":
            {"research_rate_units_per_s": float, "target_product": str}}.
            The "global" key is not consumed here (it belongs to the
            policy's binding-constraint walk) but is accepted so callers can
            pass one measurements object through both build_catalog and
            baseline_policy.choose_action.
        limits: optional tuning knobs, all plain data (never planner
            objects): "feed_headroom" (float, default 1.25),
            "machine_rate" (float fallback per-machine rate),
            "belt_tiers" (ordered {tier_name: capacity_per_s}),
            "inserter_rates" (ordered {tier_name: capacity_per_s}).

    Returns:
        A list of catalog entries, each:
        {"action": str, "target_line": str, "params": dict,
         "predicted_effect": {"metric": "output_items_per_s", "delta": float,
         "confidence": "high"|"low"}, "rationale": str,
         "cost": {"materials_estimate": int, "disruption": "none"|"brief"}}.

        Rules (see per-branch docstrings above for the exact math):
        1. Every line is diagnosed via core.bottleneck_diagnosis.diagnose_line
           and its recommended_action becomes (at most) one candidate.
        2. extend_line_x: k additional machines within feed headroom.
        3. upgrade_belt_tier / upgrade_inserter_tier: bounded by next tier
           capacity and machine capacity.
        4. add_feed_points / add_collectors / open_new_mine /
           connect_input_source / fix_power: shortfall to theoretical
           capacity.
        5. A line whose verdict is healthy_ramping, or whose recommended
           action is "wait", never gets a catalog entry.
        6. Deterministic ordering: sorted by (-predicted delta, action,
           target_line).
    """
    limits = limits or {}
    lines_measurements: Dict[str, dict] = (measurements or {}).get("lines", {})

    catalog: List[dict] = []
    for spec in chain:
        name = spec.get("name")
        line_measurements = lines_measurements.get(name, {})
        diagnosis = diagnose_line(spec, line_measurements)

        if diagnosis["verdict"] == "healthy_ramping" or diagnosis["recommended_action"] == "wait":
            continue

        entry = _build_entry(spec, line_measurements, diagnosis, limits)
        if entry is not None:
            catalog.append(entry)

    catalog.sort(key=lambda e: (-e["predicted_effect"]["delta"], e["action"], e["target_line"]))
    return catalog
