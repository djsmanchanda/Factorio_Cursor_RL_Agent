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
import random
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

    def starves_goal(option: Mapping) -> bool:
        """Its bill leaves too little of some item for every legal goal build to still be affordable."""
        bill = option.get("bill") or {}
        if not goal_builds or is_goal(option) or not bill:
            return False
        left = {name: stock.get(name, 0) - count for name, count in bill.items()}
        return all(any(left.get(name, stock.get(name, 0)) < count for name, count in (g.get("bill") or {}).items())
                   for g in goal_builds)

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
            "stock_target": item in set(status.get("stock_targets") or []),
            # A bill that would use more than half of what is left of any item spends scarce stock.
            "scarce": any(count > 0.5 * stock.get(name, 0) for name, count in (option.get("bill") or {}).items()),
            "bill_items": sum((option.get("bill") or {}).values())}
    return features


def matches(when: Mapping, features: Mapping) -> bool:
    for key, expected in when.items():
        value = features.get(key)
        if key == "need":
            patterns = expected if isinstance(expected, list) else [expected]
            if not any(fnmatch(value or "", p) for p in patterns):
                return False
        elif isinstance(expected, list):
            if value not in expected:
                return False
        elif value != expected:
            return False
    return True


class GuidedPolicy:
    """Sample options in proportion to doc-rule weights, with an exploration floor."""

    authority = "guided_exploration"
    model_ref = GUIDED_MODEL

    def __init__(self, seed: int, epsilon: float = 0.15, preferences: Mapping[str, list] | None = None) -> None:
        if not 0 < epsilon <= 1:
            raise ValueError("epsilon must be in (0, 1]")
        self.rng = random.Random(seed)
        self.epsilon = epsilon
        self.preferences = dict(preferences) if preferences is not None else load_preferences()

    def distribution(self, state: Mapping, menu: Mapping) -> list[dict]:
        seen = {g["rule_id"] for g in state.get("guidelines", [])}
        active = {rule: prefs for rule, prefs in self.preferences.items() if rule in seen}
        features = option_features(state, menu)
        rows = []
        for option in menu["options"]:
            f = features[option["option_id"]]
            weight, hits = 1.0, []
            for rule, prefs in active.items():
                for pref in prefs:
                    if matches(pref["when"], f):
                        weight *= float(pref["weight"])
                        hits.append(rule)
            rows.append({"option_id": option["option_id"], "need_id": f["need"], "weight": weight,
                         "rules": hits, "features": f})
        total = sum(r["weight"] for r in rows)
        for row in rows:
            row["probability"] = (1 - self.epsilon) * row["weight"] / total + self.epsilon / len(rows)
        return rows

    def choose(self, state: Mapping, menu: Mapping) -> dict:
        rows = self.distribution(state, menu)
        pick = self.rng.choices(rows, weights=[r["probability"] for r in rows])[0]
        return {"need_id": pick["need_id"], "option_id": pick["option_id"], "probability": pick["probability"],
                "rules": pick["rules"], "distribution": rows}
