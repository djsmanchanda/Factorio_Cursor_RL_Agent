# Path: training/laya_mall_slots.py
# Purpose: Gated, rotating mall slots for Laya: every mall cell stops at a stock target, and an existing cell can be retargeted to the current shortage.

"""Mall slots (docs/rl/Laya_Strategy_Guidelines.md, Mall expansion).

The deterministic mall is four machines: gear and cable slots are permanent,
the others rotate to whatever is short.  Laya's cells are the same idea:

* A **cell** is an assembler Laya built, with its requester section (group)
  and provider, read from the runner's observed receipts.
* A **stock gate** (planners/stock_gating.stock_gate) stops a cell while the
  logistic network holds its target, so finished intermediates stop eating
  plates.  Products the current phase must *measure* stay ungated.
* A **retarget** reconfigures an existing cell -- recipe, requester section,
  gate -- through the mod's ``configure_entity``.  It places nothing, so it
  works with no spare assemblers; that is what breaks the assembler deadlock
  (each new cell needs an assembler, including the cell that makes them).

Which cell to retarget, and when, is the policy's choice; the facts each
option carries (``slot``) are what the doc rules condition on.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from pathlib import Path

from orchestrator.laya_operations import sha256_hash
from planners.stock_gating import stock_gate

ASSEMBLERS = ("assembling-machine-1", "assembling-machine-2", "assembling-machine-3")
FURNACE_PRODUCTS = {"iron-plate", "copper-plate", "stone-brick", "steel-plate"}
FLUIDS = {"water", "steam", "crude-oil", "heavy-oil", "light-oil", "petroleum-gas", "lubricant", "sulfuric-acid"}
PERMANENT = ("iron-gear-wheel", "copper-cable")  # The deterministic mall's two fixed slots.
# Network stock at which a slot stops: intermediates feed other cells, buildables are bought in tens.
# Buildables are sized to one 6-furnace line (training/laya_lines.py): 137 belts, 17 inserters,
# 6 drills, 6 furnaces, 3 splitters -- a gate below that stalls lines on their last items.
GATE_STOCK = {"iron-gear-wheel": 100, "copper-cable": 200, "electronic-circuit": 100, "iron-stick": 50,
              "transport-belt": 150, "inserter": 30, "electric-mining-drill": 10, "electric-furnace": 10,
              "splitter": 6, "medium-electric-pole": 20, "passive-provider-chest": 10}
DEFAULT_GATE = 20
INTERMEDIATES = {"iron-gear-wheel", "copper-cable", "electronic-circuit", "iron-stick"}  # Feed other cells.
REQUEST_MULTIPLIER = 15  # What compile_candidates' quad cells request (sections x15).
BUILDABLE_MULTIPLIER = 5
# A slot is free to give away when gated (stocked), blocked on output, or without a recipe;
# waiting for ingredients is not idle -- its inputs are on the way.
IDLE_STATUSES = {"disabled_by_control_behavior", "full_output", "no_recipe"}
RECENT_TICKS = 7200  # A retargeted slot gets one rate window to fill before it is "recent" no more.


def gate_target(item: str, scale: float = 1.0) -> int:
    if not 0 < scale <= 4:raise ValueError('gate scale must be in (0,4]')
    return max(1,math.ceil(GATE_STOCK.get(item, DEFAULT_GATE)*scale))


def craftable(recipes: Mapping, item: str) -> bool:
    """An assembler can make it: a known recipe that is not smelting and takes no fluid."""
    recipe = recipes.get(item)
    return bool(recipe) and item not in FURNACE_PRODUCTS and not any(
        i["name"] in FLUIDS for i in recipe.get("ingredients", []))


def mall_cells(root: Path, facts: Mapping) -> list[dict]:
    """Laya-built assembler cells, with their current recipe and status from the live facts.

    Cells come from observed ``assemble`` receipts (one per assembler); the requester
    section group is what a retarget rewrites.
    """
    machines = {(m["position"]["x"], m["position"]["y"]): m for m in facts.get("machines", [])
                if m["name"] in ASSEMBLERS}
    cells, seen, retargeted = [], set(), {}
    for path in sorted(Path(root, "evidence").glob("transaction-*.json"), key=lambda p: p.stat().st_mtime):
        try:
            receipt = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        candidate = receipt.get("candidate") or {}
        if receipt.get("status") in ("observed", "applied") and candidate.get("family") == "retarget":
            position = next(a["position"] for a in candidate["plan"]["phases"][0]["actions"] if a["entity"] in ASSEMBLERS)
            retargeted[(position["x"], position["y"])] = (receipt.get("before") or {}).get("tick")
            continue
        if receipt.get("status") != "observed" or candidate.get("family") != "assemble":
            continue
        acts = [a for phase in candidate["plan"]["phases"] for a in phase["actions"]]
        requester = next((a for a in acts if a["entity"] == "requester-chest" and a.get("logistic_sections")), None)
        for machine in (a for a in acts if a["entity"] in ASSEMBLERS):
            key = (machine["position"]["x"], machine["position"]["y"])
            live = machines.get(key)
            if key in seen or live is None or requester is None:
                continue  # Removed, or not a requester-fed cell.
            seen.add(key)
            section = requester["logistic_sections"][0]
            cells.append({"entity": machine["entity"], "position": dict(machine["position"]),
                          "requester": dict(requester["position"]), "group": section["group"],
                          "multiplier": int(section.get("multiplier", REQUEST_MULTIPLIER)),
                          "product": live.get("recipe"), "status": live.get("status"),
                          "network": candidate.get("network"), "network_anchor": candidate.get("network_anchor")})
    for cell in cells:
        cell["retargeted_tick"] = retargeted.get((cell["position"]["x"], cell["position"]["y"]))
    return cells


def _configure(entity: str, position: Mapping, **fields) -> dict:
    return {"action_type": "configure_entity", "entity": entity, "position": dict(position), **fields}


def gate_action(cell: Mapping, product: str, gated: bool, gate_scale: float = 1.0) -> dict:
    fields = {"logistic_condition": stock_gate(product, gate_target(product,gate_scale))} if gated else {"clear_logistic_condition": True}
    return _configure(cell["entity"], cell["position"], recipe=product, **fields)


def retarget_plan(cell: Mapping, item: str, recipes: Mapping, gated: bool, gate_scale: float = 1.0,
                  buildable_multiplier: int = BUILDABLE_MULTIPLIER) -> dict:
    if type(buildable_multiplier) is not int or not 1<=buildable_multiplier<=15:raise ValueError('buildable request horizon must be 1..15')
    requests = [{"name": i["name"], "count": int(i["amount"])} for i in recipes[item]["ingredients"]]
    # Intermediates keep the cell's buffer (throughput); buildables are bought a few at a time.
    multiplier = cell["multiplier"] if item in INTERMEDIATES else min(cell["multiplier"], buildable_multiplier)
    section = {"group": cell["group"], "requests": requests, "multiplier": multiplier}
    return {"surface": "nauvis", "force": "player", "atomic": True, "phases": [{
        "name": f"laya-retarget-{item}",
        # Requests first: if the machine step then fails, the slot still runs its old recipe and stays offerable.
        "actions": [_configure("requester-chest", cell["requester"], logistic_sections=[section]),
                    gate_action(cell, item, gated,gate_scale)]}]}


def slot_facts(cell: Mapping, cells: Iterable[Mapping], stock: Mapping[str, int], goal_items: set[str],
               tick: int | None = None, gate_scale: float = 1.0) -> dict:
    """What a rule needs to judge giving this slot away."""
    product = cell["product"]
    same = sum(1 for c in cells if c["product"] == product)
    stocked = bool(product) and stock.get(product, 0) >= gate_target(product,gate_scale) // 2
    since = cell.get("retargeted_tick")
    return {"from": product,
            "idle": cell["status"] in IDLE_STATUSES,
            "recent": since is not None and tick is not None and tick - since < RECENT_TICKS and not stocked,
            "stocked": stocked,
            "permanent": product in PERMANENT and same == 1,
            "from_goal": product in goal_items}


def retarget_candidates(facts: Mapping, cells: list[dict], items: Iterable[str], stock: Mapping[str, int],
                        goal_items: set[str], ungated: set[str] | None = None, *, gate_scale: float = 1.0,
                        buildable_multiplier: int = BUILDABLE_MULTIPLIER) -> list[dict]:
    """One option per (cell, needed item) the cell does not already make."""
    recipes = facts.get("recipes") or {}
    candidates = []
    for item in sorted(i for i in set(items) if craftable(recipes, i)):
        for cell in cells:
            if cell["product"] == item:
                continue
            plan = retarget_plan(cell, item, recipes, gated=item not in (goal_items if ungated is None else ungated),
                                 gate_scale=gate_scale,buildable_multiplier=buildable_multiplier)
            section = plan["phases"][0]["actions"][0]["logistic_sections"][0]
            draws = {r["name"]: r["count"] * section["multiplier"] for r in section["requests"]}
            candidates.append({
                "option_id": f"retarget.{sha256_hash(plan)[7:19]}", "family": "retarget", "product": item,
                "summary": f"Retarget the {cell['product'] or 'idle'} slot at ({cell['position']['x']:g},"
                           f"{cell['position']['y']:g}) to {item}",
                "plan": plan, "network": cell["network"], "network_anchor": cell["network_anchor"],
                "bill": {}, "draws": draws, "slot": slot_facts(cell, cells, stock, goal_items, facts.get("tick"),gate_scale)})
    return candidates


def gate_updates(cells: list[dict], goal_items: set[str], applied: dict, gate_scale: float = 1.0) -> list[dict]:
    """Configure actions that bring every cell's gate in line: goals ungated, everything else gated.

    ``applied`` maps cell position to the (product, gated) last applied; unchanged cells are skipped.
    """
    actions = []
    for cell in cells:
        if not cell["product"]:
            continue
        gated=cell['product'] not in goal_items
        want = (cell["product"], gated,gate_target(cell['product'],gate_scale) if gated else None)
        key = (cell["position"]["x"], cell["position"]["y"])
        if applied.get(key) != want:
            actions.append(gate_action(cell,want[0],want[1],gate_scale))
    return actions


def sync(adapter, facts: Mapping, board: Mapping, stock_demand: Mapping[str, float], applied: dict,
         emit=lambda _: None, *, gate_scale: float = 1.0, buildable_multiplier: int = BUILDABLE_MULTIPLIER) -> list[dict]:
    """Bring gates in line with the phase, then return retarget options for what is short.

    Goal products of the current phase stay ungated (their rate is measured); every
    other slot stops at its stock target.  Retarget items are the board's unmet items
    and the construction items the last checks found short (``stock_demand``).
    """
    cells = mall_cells(adapter.root, facts)
    goal_items = {n["item"] for n in board["needs"] if (n.get("goal_required_per_s") or 0) > 0}
    # Items whose shortage blocks builds run ungated too: a gate below a build's bill stalls it.
    ungated = goal_items | set(stock_demand)
    updates = gate_updates(cells, ungated, applied,gate_scale)
    if updates:
        report = adapter.configure(updates)  # Raises on bridge errors: the cache stays untouched, so it retries.
        failed = report.get("failed_entities", 0) + report.get("failed_placements", 0)
        emit(f"MALL GATES: {len(updates)} slot(s) set, {failed} failed: " + ", ".join(
            f"{a['recipe']}{'<' + str(a['logistic_condition']['constant']) if 'logistic_condition' in a else ' ungated'}"
            for a in updates))
        if report.get("ok") and not failed:  # Remember only what certainly applied.
            for action in updates:
                applied[(action["position"]["x"], action["position"]["y"])] = (
                    action["recipe"], "logistic_condition" in action,(action.get('logistic_condition') or {}).get('constant'))
    stock: dict[str, int] = {}
    for network in (facts.get("networks") or {}).values():
        for name, count in (network.get("items") or {}).items():
            stock[name] = stock.get(name, 0) + int(count)
    unmet = {n["item"] for n in board["needs"] if n["condition"] != "satisfied"}
    return retarget_candidates(facts, cells, unmet | set(stock_demand), stock, goal_items, ungated,
                               gate_scale=gate_scale,buildable_multiplier=buildable_multiplier)


def execute_retarget(adapter, candidate: Mapping, measure_ticks: int) -> dict:
    """Reconfigure one existing slot, then measure like any decision.

    Only configure_entity actions on entities that already exist; nothing is placed
    or granted.  The receipt never stays ``pending`` (reconcile treats that as an
    interrupted build).
    """
    import uuid
    from training.laya_saved_world import SavedWorldError, measured_outcome
    acts = [a for phase in candidate["plan"]["phases"] for a in phase["actions"]]
    if not acts or any(a.get("action_type") != "configure_entity" for a in acts):
        raise SavedWorldError("a retarget may only reconfigure existing entities")
    before = adapter.query()
    path = adapter.root / "evidence" / f"transaction-{uuid.uuid4().hex}.json"
    receipt = {"candidate": candidate, "before": before, "status": "configuring"}
    path.write_text(json.dumps(receipt, indent=2) + "\n")
    try:
        report = adapter.configure(acts)
    except Exception as error:
        receipt.update(status="rejected", reason=f"configure_error:{type(error).__name__}")
        path.write_text(json.dumps(receipt, indent=2) + "\n")
        raise
    receipt["configuration"] = report
    if not report.get("ok") or report.get("failed_entities") or report.get("failed_placements"):
        receipt.update(status="rejected", reason="configuration_failed")
        path.write_text(json.dumps(receipt, indent=2) + "\n")
        raise SavedWorldError(f"retarget failed: {report.get('placement_failures') or report.get('error')}")
    receipt["status"] = "applied"  # Configured; the measurement window may still be interrupted.
    path.write_text(json.dumps(receipt, indent=2) + "\n")
    adapter.advance(measure_ticks)
    after = adapter.query()
    receipt.update(status="observed", after=after, measurement=measured_outcome(before, after))
    path.write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt
