# Path: core/baseline_policy.py
# Purpose: Deterministic greedy bottleneck-relief baseline policy over the
#   action catalog (docs/rl/README.md, "Start with a deterministic
#   baseline policy ... RL must beat it to earn trust"). Picks WHICH catalog
#   action to take; never plans or builds. RL later replaces/overrides this
#   choice but must clear this bar first.

from __future__ import annotations

from typing import Dict, List, Optional

# Layering: core/ must not import planners/. The one permitted core-to-core
# import is the diagnosis this policy walks the chain with.
from core.bottleneck_diagnosis import diagnose_line

# Verdicts that do NOT count as "the binding constraint" during the
# backward walk from the target product: machine_limited just means a line
# is healthily maxed out at its current size (a growth opportunity, not a
# problem), and healthy_ramping means it is fine / still warming up. Any
# other verdict (feed_limited, drain_limited, input_starved,
# resource_depleted, power_limited, unknown) represents something actually
# broken upstream, and that is what the baseline should fix first.
_NON_BINDING_VERDICTS = ("machine_limited", "healthy_ramping")


def _target_product(state: dict) -> Optional[str]:
    if state.get("target_product"):
        return state["target_product"]
    return ((state.get("measurements") or {}).get("global") or {}).get("target_product")


def _line_measurements(state: dict, name: str) -> dict:
    return ((state.get("measurements") or {}).get("lines") or {}).get(name, {})


def _terminal_line_name(chain: List[dict], target_product: Optional[str]) -> Optional[str]:
    """The line producing the target product is the terminal line of the
    chain. Falls back to a line with no consumers (nothing downstream of it
    in the chain) if no line's recipe matches the target product by name,
    and finally to the alphabetically-first line name so the function is
    total over any non-empty chain. Ties are broken alphabetically for
    determinism.
    """
    for spec in chain:
        if spec.get("recipe") == target_product:
            return spec.get("name")

    terminal_candidates = sorted(spec.get("name") for spec in chain if not spec.get("consumers"))
    if terminal_candidates:
        return terminal_candidates[0]

    all_names = sorted(spec.get("name") for spec in chain if spec.get("name"))
    return all_names[0] if all_names else None


def _line_verdict(chain: List[dict], state: dict, name: Optional[str]) -> str:
    by_name = {spec.get("name"): spec for spec in chain}
    spec = by_name.get(name)
    if spec is None:
        return "unknown"
    return diagnose_line(spec, _line_measurements(state, name))["verdict"]


def _find_binding_line(chain: List[dict], state: dict, target_product: Optional[str]) -> Optional[str]:
    """Walk the chain from the target product backwards (BFS over
    "producers" links, terminal line first) and return the first line whose
    verdict is not machine_limited/healthy_ramping — the binding constraint.
    Returns None if the walk is exhausted without finding one (nothing is
    binding; everything along the chain is either healthy or just
    capacity-bound). Multiple producers at the same level are visited in
    alphabetical order for determinism; each line is visited at most once.
    """
    by_name = {spec.get("name"): spec for spec in chain}
    terminal = _terminal_line_name(chain, target_product)
    if terminal is None:
        return None

    visited: set = set()
    frontier = [terminal]
    while frontier:
        level = sorted({n for n in frontier if n and n not in visited})
        if not level:
            break
        next_frontier: List[str] = []
        for name in level:
            visited.add(name)
            spec = by_name.get(name)
            if spec is None:
                continue
            diagnosis = diagnose_line(spec, _line_measurements(state, name))
            if diagnosis["verdict"] not in _NON_BINDING_VERDICTS:
                return name
            next_frontier.extend(spec.get("producers") or [])
        frontier = next_frontier
    return None


def _pick_best(candidates: List[dict]) -> dict:
    """Highest predicted delta wins; ties broken deterministically by
    (action name, then target_line) — same ordering build_catalog sorts by.
    """
    return sorted(
        candidates,
        key=lambda e: (-e["predicted_effect"]["delta"], e["action"], e["target_line"]),
    )[0]


def choose_action(catalog: List[dict], state: dict) -> Optional[dict]:
    """Greedy bottleneck-relief baseline the RL policy must beat.

    state: {"chain": <list of line specs>, "measurements": <same shape
        build_catalog takes>, "target_product": str (optional; falls back to
        state["measurements"]["global"]["target_product"])}.

    Selection order:
      1. Find the binding line: walk the chain backwards from the line that
         produces the target product; the first non-machine_limited,
         non-healthy_ramping line encountered is the constraint most
         relevant to the target product.
      2. If that line has any catalog candidates, pick the highest-delta one
         (ties: action name, then target_line).
      3. Otherwise (nothing binding, or the binding line has no catalog
         entry), fall back to the highest-delta extend_line_x candidate on
         the terminal (target-product) line — grow the thing that matters
         when nothing is actually broken.
      4. If the catalog is empty, return None.
    """
    if not catalog:
        return None

    chain = state.get("chain") or []
    target_product = _target_product(state)

    binding_line = _find_binding_line(chain, state, target_product)
    if binding_line is not None:
        candidates = [entry for entry in catalog if entry["target_line"] == binding_line]
        if candidates:
            return _pick_best(candidates)

    terminal = _terminal_line_name(chain, target_product)
    fallback = [
        entry for entry in catalog
        if entry["target_line"] == terminal and entry["action"] == "extend_line_x"
    ]
    if fallback:
        return _pick_best(fallback)

    return None


def explain(action: Optional[dict], catalog: List[dict], state: dict) -> str:
    """One plain sentence naming the constraint, the choice, and the
    predicted effect — shown on the live dashboard alongside the diagnosis
    trace, so it must read naturally on its own.
    """
    if action is None:
        return "No catalog action was chosen: the catalog is empty, nothing to act on."

    chain = state.get("chain") or []
    line = action.get("target_line")
    verdict = _line_verdict(chain, state, line)
    effect = action.get("predicted_effect", {})
    delta = effect.get("delta", 0.0)
    metric = effect.get("metric", "output_items_per_s")
    confidence = effect.get("confidence", "low")

    return (
        f"{line} is the binding constraint ({verdict}), so the baseline policy chose "
        f"{action.get('action')}, predicted to add {delta:.2f} {metric} "
        f"({confidence} confidence)."
    )
