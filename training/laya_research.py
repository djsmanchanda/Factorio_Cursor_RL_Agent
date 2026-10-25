# Path: training/laya_research.py | Purpose: Versioned earned research acceptance and measured science-demand curriculum.
from __future__ import annotations
import copy
import math

VERSION = "laya-research-consumption-v1"
SUPPORT = ("iron-plate", "copper-plate", "steel-plate", "electronic-circuit", "advanced-circuit", "plastic-bar")


def _stock(facts):
    total = {}
    for network in (facts.get("networks") or {}).values():
        for item, count in network.get("items", {}).items():
            total[item] = total.get(item, 0) + count
    return total


def _sample(observation, facts):
    if (not observation.get("available") or observation["tick"] != facts["tick"]
            or (facts.get("surface"), facts.get("force")) != ("nauvis", "player") or not facts.get("mods")):
        raise ValueError("fresh scoped research evidence unavailable")
    tech = observation["technology"]
    packs = tech["science_packs"]
    if facts.get("infrastructure_counts", {}).get("recycler", 0):
        raise ValueError("non-lab science consumption cannot be excluded")
    # Do not attribute pack statistics to labs if another installed machine
    # can consume the same packs. The complete live recipe catalog supplies this proof.
    recipes = facts.get("recipe_observations")
    if not isinstance(recipes, dict) or not recipes:
        raise ValueError("complete consumption recipe catalog unavailable")
    for recipe in recipes.values():
        if recipe.get("enabled") and any(i.get("name") in packs for i in recipe.get("ingredients", [])):
            if any(facts.get("infrastructure_counts", {}).get(m, 0) for m in recipe.get("machine_speeds", {})):
                raise ValueError("alternate pack consumer present")
    produced = {p: facts["production"][p] for p in packs}
    consumed = {p: facts["consumption"][p] for p in packs}
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in [*produced.values(), *consumed.values()]):
        raise ValueError("pack statistics unavailable")
    return {"tick": facts["tick"], "mods": facts["mods"], "packs": packs,
            "produced": produced, "consumed": consumed, "stock": _stock(facts),
            "lab_inventory": dict(observation["labs"]["input_inventory"]),
            "level": tech.get("current_level"), "canonical": tech["canonical_name"],
            "current": observation.get("current"), "progress": observation["progress"],
            "completed": tech["target_completed"], "units": tech.get("research_unit_count"),
            "labs": observation["labs"]["total"]}


class ResearchObjective:
    def __init__(self, requested="mining-productivity-4", *, progressive=True, sustain_ticks=18000, state=None):
        if type(sustain_ticks) is not int or sustain_ticks < 300:
            raise ValueError("research sustain window must be >=300 ticks")
        self.state = copy.deepcopy(state) if state is not None else {
            "version": VERSION, "requested": requested, "progressive": progressive,
            "sustain_ticks": sustain_ticks, "target": 0.25, "achieved": 0.0,
            "initial": None, "previous": None, "window": [], "rungs": [], "earned": False,
            "available": False, "research_units": 0.0}
        if (self.state.get("version") != VERSION or self.state.get("requested") != requested
                or self.state.get("progressive") != progressive or self.state.get("sustain_ticks") != sustain_ticks):
            raise ValueError("incompatible research objective state")

    def snapshot(self):
        return copy.deepcopy(self.state)

    def brief(self):
        return copy.deepcopy({k: v for k, v in self.state.items() if k not in {"window", "previous", "initial"}})

    def targets(self, observation):
        return {p: self.state["target"] * coefficient
                for p, coefficient in observation.get("technology", {}).get("science_packs", {}).items()}

    def observe(self, observation, facts, *, service_ok=False, clean=True):
        s = self.state
        s["advanced"] = False
        s["progressed"] = False
        try:
            sample = _sample(observation, facts)
            initial, previous = s["initial"], s["previous"]
            if initial is None:
                s["initial"] = initial = sample
                s["ineligible_start"] = sample["completed"]
            if any(sample[k] != initial[k] for k in ("mods", "packs", "canonical")):
                raise ValueError("research provenance changed")
            if previous:
                if (sample["tick"] < previous["tick"] or any(sample[part][p] < previous[part][p]
                        for part in ("produced", "consumed") for p in sample["packs"])):
                    raise ValueError("research tick/counter regressed")
                if sample["tick"] == previous["tick"]:
                    return self.snapshot()
                before, now = previous["level"], sample["level"]
                if before is not None and now is not None and now < before:
                    raise ValueError("research level regressed")
                units = 0.0
                if previous["current"] == sample["canonical"] and previous["units"]:
                    if before == now:
                        end = 1.0 if sample["completed"] else sample["progress"] if sample["current"] == sample["canonical"] else previous["progress"]
                        units = max(0, end - previous["progress"]) * previous["units"]
                    elif before is not None and now == before + 1:
                        units = (1 - previous["progress"]) * previous["units"]
                        if sample["current"] == sample["canonical"] and sample["units"]:
                            units += sample["progress"] * sample["units"]
                s["research_units"] += units
                s["progressed"] = units > 0
            s["previous"] = sample
            s["available"] = True
            s["reason"] = "observing"
            earned_supply = all(sample[part][p] > initial[part][p]
                                for part in ("produced", "consumed") for p in sample["packs"])
            s["earned"] = bool(not s["ineligible_start"] and sample["completed"] and sample["labs"] > 0
                               and sample["tick"] > initial["tick"] and earned_supply and s["research_units"] > 0)
            if s["earned"]:
                s["reason"] = "requested_level_completed_with_earned_supply"
                return self.snapshot()
            window = s["window"]
            if not service_ok or not clean or not sample["labs"] or s["ineligible_start"]:
                window.clear()
                return self.snapshot()
            if window:
                base = window[0]["sample"]
                if (any(sample["stock"].get(p, 0) < base["stock"].get(p, 0) for p in (*SUPPORT, *sample["packs"]))
                        or any(sample["lab_inventory"].get(p, 0) < base["lab_inventory"].get(p, 0) for p in sample["packs"])):
                    window.clear()
            window.append({"sample": sample, "units": s["research_units"]})
            while len(window) > 1 and sample["tick"] - window[1]["sample"]["tick"] >= s["sustain_ticks"]:
                window.pop(0)
            base = window[0]["sample"]
            seconds = (sample["tick"] - base["tick"]) / 60
            if seconds * 60 < s["sustain_ticks"]:
                return self.snapshot()
            rates = {part: {p: (sample[part][p] - base[part][p]) / seconds for p in sample["packs"]}
                     for part in ("produced", "consumed")}
            delivered = {p: rates["consumed"][p] + (sample["lab_inventory"].get(p, 0) - base["lab_inventory"].get(p, 0)) / seconds
                         for p in sample["packs"]}
            units_rate = (s["research_units"] - window[0]["units"]) / seconds
            s["rates"] = {**rates, "delivered_to_labs": delivered, "research_units": units_rate}
            target = s["target"]
            if (units_rate + 1e-9 >= target and all(rates[k][p] + 1e-9 >= target * amount
                    for k in rates for p, amount in sample["packs"].items())
                    and all(delivered[p] + 1e-9 >= target * amount for p, amount in sample["packs"].items())):
                s["achieved"] = max(s["achieved"], target)
                if not s["rungs"] or s["rungs"][-1]["rate"] != target:
                    s["rungs"].append({"rate": target, "tick": sample["tick"]})
                if s["progressive"]:
                    s["target"] *= 2
                    s["advanced"] = True
                    window.clear()
            return self.snapshot()
        except (KeyError, TypeError, ValueError) as error:
            s.update(available=False, earned=False, reason=str(error), window=[])
            return self.snapshot()


def add_research_options(menu, board, observation, facts, objective):
    """Expose independent mechanics and measured capacity facts without choosing one."""
    if not observation.get("available"):
        return menu
    from planners.research_labs import logistic_lab_row
    from orchestrator.material_reservations import plan_material_bill
    options = list(menu["options"])
    packs = observation["technology"]["science_packs"]
    current_goal = bool(set(packs) & set(board["targets"]))
    labs = observation["labs"]["total"]
    capacity = observation.get("capacity")
    wanted = 1 if not labs else labs
    if capacity:
        wanted = max(1, math.ceil(objective.state["target"] * capacity["unit_energy"] /
                                 (60 * capacity["lab_speed"] * (1 + capacity["speed_bonus"]))))
    if labs < wanted or not observation["labs"]["working"]:
        count = min(256, max(labs + 1, min(labs + 6, wanted)))
        options.append({"option_id": f"op.research_labs.{count}", "family": "research_labs",
                        "need_id": "research:labs", "product": "lab",
                        "summary": f"fund and supply owned labs through {count} (currently {labs})",
                        "params": {"count": count, "packs": packs},
                        "bill": plan_material_bill(logistic_lab_row(min(6, count - labs), packs)),
                        "features": {"goal_need": current_goal, "lab_capacity_needed": wanted}})
    # Repair/reconfigure existing owned packets without inventing a fresh site.
    if labs:
        options.append({"option_id": "op.research_labs.reconcile", "family": "research_labs", "product": "lab",
                        "need_id": "research:labs", "summary": "reconcile funded lab power and pack requests",
                        "params": {"count": labs, "packs": packs}, "features": {"goal_need": current_goal}})
    for candidate in observation.get("selection_candidates", []):
        target = candidate["technology"]
        canonical = candidate["canonical_name"]
        if observation.get("current") == canonical or canonical in observation["queue"]:
            continue
        replaces = bool(observation.get("current") or observation["queue"])
        options.append({"option_id": f"op.research_select.{target}", "family": "research_select", "product": target,
                        "need_id": "research:selection", "summary": f"select {target}" + (" replacing observed queue" if replaces else ""),
                        "params": {"technology": target, "expected_queue": observation["queue"],
                                   "expected_current": observation.get("current"), "replace_queue": replaces},
                        "features": {"goal_need": current_goal, "replaces_queue": replaces}})
    for need in board["needs"]:
        item = need["item"]
        present = sum(need.get("machines", {}).values())
        if not present or need.get("condition") != "under_capacity" or not need.get("machines_needed"):
            continue
        count = min(256, max(present + 1, min(need["machines_needed"].values())))
        if count <= present:
            continue
        options.append({"option_id": f"op.scale_production.{item}.{count}", "family": "scale_production", "product": item,
                        "need_id": need["need_id"], "summary": f"scale {item} capacity through {count} machines",
                        "params": {"item": item, "machines": count,
                                   "stock_target": max(1, math.ceil(need["required_per_s"] * 300))},
                        "features": {"goal_need": item in board["targets"], "in_bottleneck": True,
                                     "required_per_s": need["required_per_s"], "capacity_per_s": need["capacity_per_s"]}})
    return {**menu, "options": options}


def finite_material_progress(memory, results, transactions, production):
    """Bound manufactured progress by current owned obligations, not background output."""
    from training.laya_ops_runner import material_results, _demand_bills
    demands = []
    for result in material_results(results, transactions):
        for bill in _demand_bills(result):
            params = result.get("params") or {}
            identity = (result["operation"], params.get("recipe") or params.get("item"), bill.get("stage"))
            for item, required in bill.get("required", {}).items():
                missing = max(0, required - bill.get("available", {}).get(item, 0))
                if missing:
                    demands.append(((*identity, item), item, missing))
    for tid, tx in transactions.items():
        if not tx.get("error") and tx.get("intent_sha256"):
            for item, missing in tx.get("missing", tx.get("remaining", {})).items():
                if missing:
                    demands.append(((tid, tx["intent_sha256"], item), item, missing))
    advanced = []
    for key, item, missing in demands:
        count = production.get(item)
        if type(count) not in (int, float) or count < 0:
            continue
        previous = memory.get(key)
        if previous is None:
            memory[key] = {"baseline": count, "required": missing, "credited": 0, "item": item}
            continue
        credit = min(previous["required"], max(0, count - previous["baseline"]))
        if credit > previous["credited"]:
            previous["credited"] = credit
            advanced.append({"obligation": key, **previous})
    return advanced
