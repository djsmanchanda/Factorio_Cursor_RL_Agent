# Path: training/laya_decision_pacing.py | Purpose: Bound optional wait acceleration without claiming to accelerate synchronous operations.
from __future__ import annotations

import math


def validate(settings):
    if type(settings.decision_wait_schedule) is not str or settings.decision_wait_schedule not in {"off", "linear", "exponential", "sqrt"}:
        raise ValueError("decision_wait_schedule must be off, linear, exponential or sqrt")
    for name, minimum in (("decision_wait_growth_per_minute", 0),
                          ("decision_wait_sqrt_minutes", 0), ("decision_wait_max_multiplier", 1)):
        value = getattr(settings, name)
        if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
            raise ValueError(f"{name} must be finite and >= {minimum}")
    if settings.decision_wait_sqrt_minutes <= 0:
        raise ValueError("decision_wait_sqrt_minutes must be positive")
    if type(settings.decision_wait_min_ticks) is not int or not 1 <= settings.decision_wait_min_ticks <= 300:
        raise ValueError("decision_wait_min_ticks must be 1..300")
    if type(settings.mall_loan_reconciliation) is not bool:
        raise ValueError("mall_loan_reconciliation must be boolean")


def multiplier(settings, elapsed_ticks):
    if settings.decision_wait_schedule == "off":
        return 1.0
    if type(elapsed_ticks) not in (int, float) or not math.isfinite(elapsed_ticks) or elapsed_ticks < 0:
        raise ValueError("decision pacing requires finite nonnegative elapsed game ticks")
    minutes = elapsed_ticks / 3600
    cap = settings.decision_wait_max_multiplier
    if settings.decision_wait_schedule == "linear":
        return min(cap, 1 + minutes * settings.decision_wait_growth_per_minute)
    if settings.decision_wait_schedule == "exponential":
        return min(cap, math.exp(min(math.log(cap), minutes * math.log1p(settings.decision_wait_growth_per_minute))))
    return min(cap, math.sqrt(1 + minutes / settings.decision_wait_sqrt_minutes))


def apply_waits(menu, settings, elapsed_ticks):
    """Shorten only selectable waits; calls and world predicates keep their contracts."""
    if settings.decision_wait_schedule == "off":
        return menu
    scale = multiplier(settings, elapsed_ticks)
    options = []
    for option in menu["options"]:
        if option["family"] == "wait":
            base = option["wait_ticks"]
            ticks = min(base, max(settings.decision_wait_min_ticks, math.ceil(base / scale)))
            option = {**option, "option_id": f"wait.{ticks}", "base_wait_ticks": base, "wait_ticks": ticks,
                      "summary": f"Wait {ticks / 60:g} game seconds (nominal {base / 60:g}, ramp x{scale:.3g})"}
        options.append(option)
    # A floor can merge wait lengths. Give the resulting action one option,
    # rather than accidentally multiplying its sampling mass.
    options = list({o["option_id"]: o for o in options}.values())
    needs = [{**need, "options": [o["option_id"] for o in options if o["need_id"] == need["need_id"]]}
             for need in menu["needs"]]
    return {**menu, "options": options, "needs": needs}
