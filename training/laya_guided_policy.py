# Path: training/laya_guided_policy.py
# Purpose: Doc-guided policy until a trained Laya checkpoint exists: weight options by the preferences of the rules Laya sees, keep an exploration floor, and record why.

"""Guided policy.

Behavior comes from the documents, not this code: each soft rule in
``docs/rl/laya_mission_guidelines.json`` (quoting
``docs/rl/Laya_Strategy_Guidelines.md``) may carry ``prefer`` entries::

    {"when": {"need": "stock:*", "family": ["assemble"], "in_bottleneck": true}, "weight": 2.5}

Only rules present in the decision state's ``guidelines`` apply, so the
policy acts on exactly what Laya is shown.  Weights multiply; a uniform
exploration floor keeps every legal option possible.  The full distribution,
matched rules and features of every option are returned for the journal and
later fine-tuning.
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter
from fnmatch import fnmatch
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs/rl/laya_mission_guidelines.json"
GUIDED_MODEL = {"model_id": "laya-guided-prior-v1", "model_hash": "sha256:" + "0" * 64}


def load_preferences(path: Path = CATALOG) -> dict[str, list[dict]]:
    catalog = json.loads(path.read_text())
    return {r["rule_id"]: r["prefer"] for r in catalog["rules"] if r.get("prefer")}


def option_features(state: Mapping, menu: Mapping) -> dict[str, dict]:
    """Facts a rule can condition on, per option."""
    status = (state.get("observation_digest") or {}).get("factory_status") or {}
    chains = [chain.split(" <- ") for chain in status.get("bottleneck") or []]
    in_chain = {link.split(":")[0] for chain in chains for link in chain}
    deepest = {chain[-1].split(":")[0] for chain in chains if chain}
    # Items the phase goals need (goal rate > 0), as opposed to stock-chain-only items.
    goal_items = {row["item"] for row in status.get("needs") or [] if row.get("goal", row.get("required") or 0) > 0}
    phase = (state.get("observation_digest") or {}).get("mission_phase")
    conditions = {n["need_id"]: n.get("condition") for n in menu["needs"]}
    others = any(o["family"] != "wait" for o in menu["options"])
    # Accessible stock: provider/storage items minus what pending ghosts already reserve.
    world = (state.get("observation_digest") or {}).get("saved_world") or {}
    stock: dict[str, int] = {}
    for network, items in (world.get("provider_stock") or {}).items():
        reserved = (world.get("reserved_stock") or {}).get(network, {})
        for name, count in items.items():
            stock[name] = stock.get(name, 0) + max(0, int(count) - int(reserved.get(name, 0)))
    def is_goal(option: Mapping) -> bool:
        need = option.get("need_id", "wait")
        return (need.split(":", 1)[1] if ":" in need else None) in goal_items

    goal_builds = [o for o in menu["options"] if o["family"] != "wait" and is_goal(o)]
    goal_options = bool(goal_builds)

    def spend(option: Mapping) -> dict[str, int]:
        """What a build takes from the network: its bill plus what its requester chests will pull in."""
        total = dict(option.get("bill") or {})
        for name, count in (option.get("draws") or {}).items():
            total[name] = total.get(name, 0) + count
        return total

    def starves_goal(option: Mapping) -> bool:
        """What it takes leaves too little of some item for every legal goal build to still be affordable."""
        bill = spend(option)
        if not goal_builds or is_goal(option) or not bill:
            return False
        left = {name: stock.get(name, 0) - count for name, count in bill.items()}
        return all(any(left.get(name, stock.get(name, 0)) < count for name, count in (g.get("bill") or {}).items())
                   for g in goal_builds)

    # Items being made now (measured > 0); an input neither made nor stocked would leave a requester waiting.
    made = {row["item"] for row in status.get("needs") or [] if (row.get("measured") or 0) > 0}

    features = {}
    for option in menu["options"]:
        need = option.get("need_id", "wait")
        item = need.split(":", 1)[1] if ":" in need else None
        features[option["option_id"]] = {
            "need": need, "family": option["family"], "power": option.get("power"), "product": option.get("product"),
            "condition": conditions.get(need), "phase": phase, "in_bottleneck": item in in_chain,
            "deepest_bottleneck": item in deepest, "others_available": others,
            "goal_need": item in goal_items, "goal_options_available": goal_options,
            "starves_goal": starves_goal(option),
            # Retargets of an existing mall slot (training/laya_mall_slots.py): what giving the slot away costs.
            "slot_free": bool(option.get("slot")) and (option["slot"]["idle"] or option["slot"]["stocked"]),
            "slot_permanent": bool(option.get("slot")) and option["slot"]["permanent"],
            "slot_from_goal": bool(option.get("slot")) and option["slot"]["from_goal"],
            "slot_recent": bool(option.get("slot")) and option["slot"].get("recent", False),
            # Lines: the nearest free site to the item block's anchor (training/laya_lines.next_slot).
            "next_slot": bool(option.get("next_slot")),
            # A side build that would leave less than the unbuilt goal starters need (menu["goal_reserve"]).
            "dips_goal_reserve": not is_goal(option) and any(
                stock.get(name, 0) - count < (menu.get("goal_reserve") or {}).get(name, 0)
                for name, count in spend(option).items() if name in (menu.get("goal_reserve") or {})),
            "stock_target": item in set(status.get("stock_targets") or []),
            # A bill that would use more than half of what is left of any item spends scarce stock.
            "inputs_missing": any(stock.get(name, 0) == 0 and name not in made for name in (option.get("draws") or {})),
            "scarce": any(count > 0.5 * stock.get(name, 0) for name, count in spend(option).items()),
            "bill_items": sum((option.get("bill") or {}).values())}
        dependency = (state.get('observation_digest', {}).get('dependency_context') or {}).get(option['option_id'])
        if dependency is not None:
            features[option['option_id']].update(
                blocks_other_builds=dependency['blocks_other_builds'] > 0,
                unbacked_inputs=bool(dependency['unbacked_inputs']),
                available_input_crafts=dependency['available_input_crafts'])
        outcomes=state.get('observation_digest',{}).get('action_outcomes')
        if outcomes is not None:
            previous=[h for h in outcomes if h.get('product')==option.get('product') and h.get('family')==option['family']]
            features[option['option_id']]['repeat_without_output']=bool(previous and not previous[-1].get('produced',{}).get(option.get('product'),0))
    # Options may carry their own facts (deterministic operations: plate_stage, last_code, constructing).
    for option in menu["options"]:
        features[option["option_id"]].update(option.get("features") or {})
    return features


def matches(when: Mapping, features: Mapping) -> bool:
    for key, expected in when.items():
        value = features.get(key)
        if key == "need":
            patterns = expected if isinstance(expected, list) else [expected]
            if not any(fnmatch(value or "", p) for p in patterns):
                return False
        elif isinstance(expected, dict) and "not" in expected:  # {"not": [...]}: any value except these
            if value in expected["not"]:
                return False
        elif isinstance(expected, list):
            if value not in expected:
                return False
        elif value != expected:
            return False
    return True


def _finite_number(value: float, name: str, *, allow_zero: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be a finite {'nonnegative' if allow_zero else 'positive'} number") from None
    if isinstance(value, (bool, str)) or not math.isfinite(number) or number < 0 or (not allow_zero and number == 0):
        raise ValueError(f"{name} must be a finite {'nonnegative' if allow_zero else 'positive'} number")
    return number


class GuidedPolicy:
    """Sample doc-rule weights; experimental keywords are fresh-run only until recovery identity supports them."""

    authority = "guided_exploration"
    model_ref = GUIDED_MODEL

    def __init__(self, seed: int, epsilon: float = 0.15, preferences: Mapping[str, list] | None = None, *,
                 temperature: float = 1.0, exploration_mode: str = "options", novelty_strength: float = 0.0) -> None:
        epsilon = _finite_number(epsilon, "epsilon")
        if epsilon > 1:
            raise ValueError("epsilon must be in (0, 1]")
        self.temperature = _finite_number(temperature, "temperature")
        if exploration_mode not in ("options", "families"):
            raise ValueError("exploration_mode must be 'options' or 'families'")
        self.exploration_mode = exploration_mode
        self.novelty_strength = _finite_number(novelty_strength, "novelty_strength", allow_zero=True)
        self.rng = random.Random(seed)
        self.epsilon = epsilon
        self.preferences = dict(preferences) if preferences is not None else load_preferences()
        for prefs in self.preferences.values():
            for pref in prefs:
                _finite_number(float(pref["weight"]), "preference weight", allow_zero=True)

    def distribution(self, state: Mapping, menu: Mapping) -> list[dict]:
        seen = {g["rule_id"] for g in state.get("guidelines", [])}
        active = {rule: prefs for rule, prefs in self.preferences.items() if rule in seen}
        features = option_features(state, menu)
        rows = []
        factors = []
        for option in menu["options"]:
            f = features[option["option_id"]]
            weight, hits = 1.0, []
            priors = []
            for rule, prefs in active.items():
                for pref in prefs:
                    if matches(pref["when"], f):
                        factor = _finite_number(float(pref["weight"]), "preference weight", allow_zero=True)
                        weight *= factor
                        hits.append(rule)
                        priors.append({"rule_id": rule, "weight": factor})
            rows.append({"option_id": option["option_id"], "need_id": f["need"], "weight": weight,
                         "rules": hits, "features": f})
            factors.append(priors)
        if not rows:
            raise ValueError("guided policy requires at least one legal option")
        total = sum(r["weight"] for r in rows)
        # Retain the original arithmetic and row shape for ordinary default calls: reordering
        # normalization can change seeded choices at a cumulative-probability boundary.
        defaults = self.temperature == 1 and self.exploration_mode == "options" and self.novelty_strength == 0
        representable = all(row["weight"] > 0 or any(p["weight"] == 0 for p in priors)
                            for row, priors in zip(rows, factors))
        if defaults and math.isfinite(total) and total > 0 and representable:
            for row in rows:
                row["probability"] = (1 - self.epsilon) * row["weight"] / total + self.epsilon / len(rows)
            if all(row["probability"] > 0 for row in rows):
                return rows

        prior_logs = [math.fsum(math.log(p["weight"]) for p in priors) if all(p["weight"] > 0 for p in priors)
                      else -math.inf for priors in factors]
        peak = max(prior_logs)
        if peak == -math.inf:
            raise ValueError("guided policy requires at least one positive prior weight")
        # Ops logs factual binary history on options.features.tried; absent is unknown.
        tried = [row["features"].get("tried") for row in rows]
        novelty_logs = [math.log1p(self.novelty_strength) if value is False else 0.0 for value in tried]
        # Center *before* dividing so even a subnormal temperature cannot overflow the peak.
        logits = [(prior - peak) / self.temperature + novelty for prior, novelty in zip(prior_logs, novelty_logs)]
        peak = max(logits)
        weights = [math.exp(value - peak) for value in logits]
        total = math.fsum(weights)
        families = Counter(option["family"] for option in menu["options"])
        for index, (row, option) in enumerate(zip(rows, menu["options"])):
            exploration = (1 / len(families) / families[option["family"]]
                           if self.exploration_mode == "families" else 1 / len(rows))
            exploitation = weights[index] / total
            row.update(weight=weights[index], weight_basis="max_scaled_effective",
                       prior_log_weight=prior_logs[index] if math.isfinite(prior_logs[index]) else None,
                       exploration_probability=exploration, exploitation_probability=exploitation,
                       factors={"priors": factors[index], "temperature": self.temperature,
                                "exploration_mode": self.exploration_mode,
                                "novelty_strength": self.novelty_strength,
                                "tried": tried[index] if type(tried[index]) is bool else None,
                                "novelty_source": "options.features.tried",
                                "novelty_multiplier": 1 + self.novelty_strength if tried[index] is False else 1.0})
            row["probability"] = max((1 - self.epsilon) * exploitation + self.epsilon * exploration, math.ulp(0.0))
        return rows

    def choose(self, state: Mapping, menu: Mapping) -> dict:
        rows = self.distribution(state, menu)
        pick = self.rng.choices(rows, weights=[r["probability"] for r in rows])[0]
        return {"need_id": pick["need_id"], "option_id": pick["option_id"], "probability": pick["probability"],
                "rules": pick["rules"], "distribution": rows}
