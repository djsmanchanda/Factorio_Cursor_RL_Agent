# Path: training/laya_factory_status.py
# Purpose: Compile Laya's factory status board (goal demand, measured supply, machine health, build/scale/upgrade needs) without ranking actions.

"""Factory status board for Laya.

The deterministic runner decides "what next" with hand-written priority rules.
Laya instead needs the *facts* those rules consume: what each goal requires,
what is actually produced, which machines are starved, blocked, unpowered or
undersized, and which upgrades are possible.  This module derives those facts
from live saved-world observations and recipe arithmetic.

It never ranks needs or selects an operation.  Needs are ordered by goal-tree
depth and item name; diagnoses describe the measured condition, not a remedy.
Unknown quantities stay ``None`` rather than becoming zero.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from planners.recipe_data import MACHINE_SPEEDS

from orchestrator import mall_growth

VERSION = "laya-factory-status-v1"
RAW_ITEMS = frozenset({"iron-ore", "copper-ore", "stone", "coal"})
SMELTED_ITEMS = frozenset({"iron-plate", "copper-plate", "stone-brick", "steel-plate"})
FURNACE_SPEEDS = {"stone-furnace": 1.0, "steel-furnace": 2.0, "electric-furnace": 2.0}
ASSEMBLER_TIERS = ("assembling-machine-1", "assembling-machine-2", "assembling-machine-3")
# Satisfied means measured supply covers this share of the required rate.
SATISFIED_SHARE = 0.95
# Output-blocked producers with this many seconds of their demand already in
# accessible network stock are backed up, not short (full chests read as zero
# throughput). Less stock than that means output is not being delivered.
BACKLOG_COVER_SECONDS = 120.0
# Producers already outrunning their consumers: added capacity cannot raise delivery.
BACKPRESSURE_CONDITIONS = frozenset({"output_backed_up", "output_blocked"})

# Factorio entity_status names grouped into the conditions Laya must separate.
STATUS_BUCKETS = {
    "working": {"working", "normal"},
    "power": {"no_power", "low_power"},
    "starved": {"item_ingredient_shortage", "fluid_ingredient_shortage", "no_ingredients", "no_input_fluid"},
    "blocked": {"full_output", "waiting_for_space_in_destination"},
    "control": {"disabled_by_control_behavior"},
}


class FactoryStatusError(ValueError):
    """The goal or recipe facts cannot produce a trustworthy status board."""


def _bucket(status: str) -> str:
    return next((name for name, members in STATUS_BUCKETS.items() if status in members), "other")


def goal_demand(targets: Mapping[str, float], recipes: Mapping[str, Mapping]) -> dict[str, dict]:
    """Expand target rates through enabled solid recipes into per-item demand.

    Returns ``{item: {"required_per_s", "depth", "consumers"}}``.  Items with no
    enabled recipe are leaves; the board marks non-raw leaves as unavailable.
    """
    demand: dict[str, float] = defaultdict(float)
    depth: dict[str, int] = {}
    consumers: dict[str, set[str]] = defaultdict(set)

    def visit(item: str, rate: float, level: int, path: tuple[str, ...]) -> None:
        if item in path:
            raise FactoryStatusError(f"recipe cycle through {item}: {' -> '.join(path + (item,))}")
        demand[item] += rate
        depth[item] = min(depth.get(item, level), level)
        recipe = recipes.get(item)
        if recipe is None or item in RAW_ITEMS:
            return
        crafts = rate / (float(recipe["product_amount"]) * (1 + float(recipe.get('productivity_bonus',0))))
        for part in recipe["ingredients"]:
            consumers[part["name"]].add(item)
            visit(part["name"], crafts * float(part["amount"]), level + 1, path + (item,))

    for item, rate in sorted(targets.items()):
        if not rate or rate <= 0:
            raise FactoryStatusError(f"goal rate for {item} must be positive")
        visit(item, float(rate), 0, ())
    return {item: {"required_per_s": demand[item], "depth": depth[item], "consumers": sorted(consumers[item])}
            for item in demand}


def machine_rate(machine: str, item: str, recipe: Mapping | None) -> float | None:
    """Theoretical items/s of one machine on ``item``; ``None`` when unknown."""
    if recipe is None:
        return None
    speed = FURNACE_SPEEDS.get(machine) if item in SMELTED_ITEMS else None
    speed = speed if speed is not None else MACHINE_SPEEDS.get(machine)
    if not speed:
        return None
    return speed * float(recipe["product_amount"]) * (1 + float(recipe.get('productivity_bonus',0))) * 60.0 / float(recipe["energy_ticks"])


def accessible_stock(facts: Mapping) -> dict[str, int]:
    """Provider/storage stock not already reserved by pending ghosts."""
    stock: dict[str, int] = defaultdict(int)
    for network in (facts.get("networks") or {}).values():
        reserved = network.get("reserved") or {}
        for item, count in (network.get("items") or {}).items():
            stock[item] += max(0, int(count) - int(reserved.get(item, 0)))
    return dict(stock)


def construction_shortfall(facts: Mapping) -> dict[str, int]:
    """Items pending ghosts reserve beyond the stock their network holds."""
    shortfall: dict[str, int] = defaultdict(int)
    for network in (facts.get("networks") or {}).values():
        items = network.get("items") or {}
        for item, count in (network.get("reserved") or {}).items():
            missing = int(count) - int(items.get(item, 0))
            if missing > 0:
                shortfall[item] += missing
    return dict(shortfall)


def _measured(measurement: Mapping | None, kind: str, item: str) -> float | None:
    if measurement is None:
        return None
    entry = (measurement.get(kind) or {}).get(item)
    return None if entry is None else float(entry["per_second"])


def _upgrade_fact(machines: Sequence[Mapping], item: str, recipe: Mapping | None,
                  recipes: Mapping, stock: Mapping[str, int]) -> dict | None:
    """Next assembler tier for this item's lowest-tier machines, as facts only."""
    tiers = [m["name"] for m in machines if m["name"] in ASSEMBLER_TIERS]
    if not tiers:
        return None
    lowest = min(tiers, key=ASSEMBLER_TIERS.index)
    index = ASSEMBLER_TIERS.index(lowest)
    if index + 1 >= len(ASSEMBLER_TIERS):
        return None
    target = ASSEMBLER_TIERS[index + 1]
    before, after = machine_rate(lowest, item, recipe), machine_rate(target, item, recipe)
    return {"from": lowest, "to": target, "eligible_machines": tiers.count(lowest),
            "gain_per_machine_per_s": None if before is None or after is None else after - before,
            "target_in_stock": int(stock.get(target, 0)), "target_recipe_enabled": target in recipes}


def _diagnose(item: str, recipe: Mapping | None, buckets: Mapping[str, int], machine_count: int,
              required: float, capacity: float | None, measured: float | None,
              stock: int = 0) -> str:
    """Describe the measured condition of one need; never prescribe a remedy.

    ``stock`` is accessible (unreserved) network stock: full output with enough
    of it is ``output_backed_up``; full output without it is ``output_blocked``.
    """
    if measured is not None and measured >= required * SATISFIED_SHARE:
        return "satisfied"
    if item in RAW_ITEMS:
        return "short" if measured is not None else "unmeasured"
    if recipe is None:
        return "recipe_unavailable"
    if machine_count == 0:
        return "unbuilt"
    for bucket, condition in (("power", "power_limited"), ("starved", "input_starved"),
                              ("blocked", "output_blocked"), ("control", "control_disabled")):
        if buckets.get(bucket, 0) * 2 > machine_count:
            if condition == "output_blocked" and stock >= max(1.0, required * BACKLOG_COVER_SECONDS):
                return "output_backed_up"
            return condition
    if capacity is not None and capacity < required * SATISFIED_SHARE:
        return "under_capacity"
    return "short" if measured is not None else "unmeasured"


def compile_status(facts: Mapping, targets: Mapping[str, float], measurement: Mapping | None = None,
                   built: Mapping[str, int] | None = None, stock: Mapping[str, float] | None = None) -> dict:
    """Build the full status board for ``targets`` (items/s) from saved-world facts.

    ``built`` counts machines the runner itself built per product (from its
    receipts); machines that never ran report no recipe, so this separates
    "built but idle" from "unbuilt".
    """
    built = built or {}
    # Construction items reported short join the demand tree at a small rate, so their
    # inputs (e.g. steel for poles) become visible needs; goals stay the phase targets.
    stock_targets = {item: rate for item, rate in (stock or {}).items() if item not in targets}
    recipes = facts.get("recipes") or {}
    demand = goal_demand({**targets, **stock_targets}, recipes)
    goal_only = goal_demand(targets, recipes)  # Phase goals are judged on these rates alone.
    stock = accessible_stock(facts)
    by_recipe: dict[str, list[Mapping]] = defaultdict(list)
    for machine in facts.get("machines") or []:
        if machine.get("recipe"):
            by_recipe[machine["recipe"]].append(machine)

    needs = []
    for item, info in demand.items():
        recipe = None if item in RAW_ITEMS else recipes.get(item)
        machines = by_recipe.get(item, [])
        statuses: dict[str, int] = defaultdict(int)
        buckets: dict[str, int] = defaultdict(int)
        tiers: dict[str, int] = defaultdict(int)
        rates = []
        for machine in machines:
            statuses[machine["status"]] += 1
            buckets[_bucket(machine["status"])] += 1
            tiers[machine["name"]] += 1
            rates.append(machine_rate(machine["name"], item, recipe))
        capacity = None if not machines or None in rates else sum(rates)
        required = info["required_per_s"]
        measured = _measured(measurement, "production", item)
        machines_needed = {}
        candidates = list(FURNACE_SPEEDS) if item in SMELTED_ITEMS else list(ASSEMBLER_TIERS)
        for tier in candidates if recipe is not None else ():
            per_machine = machine_rate(tier, item, recipe)
            if per_machine:
                machines_needed[tier] = math.ceil(required / per_machine - 1e-9)
        condition = _diagnose(item, recipe, buckets, len(machines), required, capacity, measured,
                              int(stock.get(item, 0)))
        if condition == "unbuilt" and built.get(item, 0) > 0:
            condition = "built_idle"  # Built by the runner but never ran (usually starved of input).
        needs.append({
            "need_id": f"item:{item}", "item": item, "built": int(built.get(item, 0)), "depth": info["depth"], "consumers": info["consumers"],
            "required_per_s": required, "measured_per_s": measured,
            "goal_required_per_s": goal_only[item]["required_per_s"] if item in goal_only else 0.0,
            "consumed_per_s": _measured(measurement, "consumption", item),
            "gap_per_s": None if measured is None else max(0.0, required - measured),
            "capacity_per_s": capacity, "machines": dict(tiers), "status_counts": dict(statuses),
            "machines_needed": machines_needed, "stock": int(stock.get(item, 0)),
            "upgrade": None if recipe is None else _upgrade_fact(machines, item, recipe, recipes, stock),
            "condition": condition,
        })
    conditions = {n["item"]: n["condition"] for n in needs}
    for need in needs:
        recipe = recipes.get(need["item"]) if need["item"] not in RAW_ITEMS else None
        need["short_inputs"] = sorted(part["name"] for part in (recipe or {}).get("ingredients", ())
                                      if conditions.get(part["name"]) not in (None, "satisfied"))
    needs.sort(key=lambda n: (n["depth"], n["item"]))
    # Capacity/plate-surplus facts for choosing mall growth, in the same shape
    # the deterministic admission uses. Measured rates only: a missing counter
    # stays None, never a stock-delta substitute.
    plate_production = {
        item: _measured(measurement, "production", item)
        for item in mall_growth.PLATE_ITEMS
    }
    plate_consumption = {
        item: _measured(measurement, "consumption", item)
        for item in mall_growth.PLATE_ITEMS
    }
    growth_facts = {
        "version": mall_growth.VERSION,
        "measurement_scope": "surface/force counters, including all consumers",
        "plate_surplus_per_s": mall_growth.plate_surplus(
            plate_production, plate_consumption,
        ),
        "seed_ready": mall_growth.seed_ready(stock),
        "free_slots": None,  # live slot capacity is owned by the running controller
    }
    return {
        "version": VERSION, "targets": dict(sorted(targets.items())), "stock_targets": dict(sorted(stock_targets.items())),
        "measured": measurement is not None,
        "window_ticks": None if measurement is None else measurement.get("window_ticks"),
        "needs": needs,
        "mall_growth": growth_facts,
        "construction": {"pending_ghosts": int(facts.get("pending_ghosts", 0)),
                         "shortfall": construction_shortfall(facts)},
    }


def goal_met(need: Mapping) -> bool:
    """A goal item meets its phase rate (stock demand on the same item is not part of the goal)."""
    measured = need["measured_per_s"]
    goal = need.get("goal_required_per_s", need["required_per_s"])
    return measured is not None and measured >= goal * SATISFIED_SHARE


def bottleneck_chain(board: Mapping) -> list[str]:
    """One causal chain per unmet goal item, following short inputs to the deepest cause.

    Each chain reads ``goal:condition <- input:condition <- ...``.  This is
    causal attribution over measured facts, not a choice of remedy.
    """
    needs = {n["item"]: n for n in board["needs"]}
    chains: list[str] = []
    for item in [*sorted(board["targets"]), *sorted(board.get("stock_targets") or {})]:
        current = needs[item]
        links, seen = [], set()
        while current["condition"] != "satisfied" and current["item"] not in seen:
            seen.add(current["item"])
            links.append(f"{current['item']}:{current['condition']}")
            if not current["short_inputs"]:
                break
            current = needs[current["short_inputs"][0]]
        if links:
            chains.append(" <- ".join(links))
    return chains


def compact_status(board: Mapping, limit: int = 12) -> dict:
    """Bounded model projection: goal items first, then the deepest unmet needs.

    Rows past ``limit`` are dropped by depth order, so goal items always remain.
    """
    rows = []
    for need in board["needs"][:limit]:
        row = {"item": need["item"], "condition": need["condition"],
               "required": round(need["required_per_s"], 3),
               "measured": None if need["measured_per_s"] is None else round(need["measured_per_s"], 3),
               "capacity": None if need["capacity_per_s"] is None else round(need["capacity_per_s"], 3),
               "machines": sum(need["machines"].values()), "stock": need["stock"]}
        if "goal_required_per_s" in need and abs(need["goal_required_per_s"] - need["required_per_s"]) > 1e-9:
            row["goal"] = round(need["goal_required_per_s"], 3)  # Phase goal; 'required' also counts stock demand.
        if need["short_inputs"]:
            row["short_inputs"] = need["short_inputs"]
        if need["upgrade"]:
            row["upgrade"] = f"{need['upgrade']['from']}->{need['upgrade']['to']}x{need['upgrade']['eligible_machines']}"
        rows.append(row)
    return {"version": VERSION, "needs": rows, "omitted": max(0, len(board["needs"]) - limit),
            "stock_targets": sorted(board.get("stock_targets") or {}),
            "mall_growth": {
                "plate_surplus_per_s": {
                    item: (None if value is None else round(value, 3))
                    for item, value in (board.get("mall_growth") or {}).get(
                        "plate_surplus_per_s", {},
                    ).items()
                },
                "seed_ready": (board.get("mall_growth") or {}).get("seed_ready"),
            },
            "bottleneck": bottleneck_chain(board), "construction_shortfall": board["construction"]["shortfall"]}


def apply_to_state(state: dict, board: Mapping) -> dict:
    """Fill a Laya decision state's goal/stock/production digest from the board.

    Mutates and returns ``state``; the caller still runs ``validate_state``.
    """
    needs = {n["item"]: n for n in board["needs"]}
    goals = [needs[item] for item in board["targets"]]
    measured = [g["measured_per_s"] for g in goals]
    current = None if None in measured else sum(measured)
    target = sum(g.get("goal_required_per_s", g["required_per_s"]) for g in goals)
    for goal in state["goal_stack"]:
        goal["gap"] = None if current is None else max(0.0, target - current)
        if current is not None and all(goal_met(g) for g in goals):
            goal["status"] = "satisfied"
    gap = state["acceptance_gap"]
    gap.update(current=current, target=target, remaining=None if current is None else max(0.0, target - current))
    # Stock shortages are item counts; rate gaps stay in the factory_status rows.
    shortages = sorted(((float(c), i) for i, c in board["construction"]["shortfall"].items()), reverse=True)
    state["state_digest"]["stock"]["top_shortages"] = [{"item": i, "amount": a} for a, i in shortages[:8]]
    production = state["state_digest"]["production"]
    production.update(rate_now=current, rate_target=target)
    chain = bottleneck_chain(board)
    # The state's hint is a bounded string (512); the full chains stay in factory_status.
    hint = " | ".join(chain)
    production["bottleneck_hint"] = (hint if len(hint) <= 512 else hint[:509] + "...") if chain else None
    state.setdefault("observation_digest", {})["factory_status"] = compact_status(board)
    return state
