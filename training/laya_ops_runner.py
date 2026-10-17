# Path: training/laya_ops_runner.py
# Purpose: Laya's decision loop over the deterministic runtime's operations: observe, offer operations, choose by the docs, run one, record.

"""Laya over the deterministic tooling.

Each decision:

1. observe the live world and compile the status board and mission phase;
2. offer operations (training/laya_ops.py): per unmet plate its starter,
   foundation (mine + six-furnace refinery + belt) or extension; ``produce``
   for items a blocked operation reported short; waits;
3. the doc-guided policy chooses (rules in docs/rl/laya_mission_guidelines.json
   weight the options through their ``features``);
4. the operation runs; its typed result (built, coverage wait, material
   shortage) becomes the next observation.

Same ``run`` signature as training/laya_clean_runner.run, so the CLI, console,
logs, ledger and helper are shared.  Sequencing lives in the docs, not here.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from orchestrator.laya_knowledge import load_guideline_context
from training.laya_clean_runner import (ROOT, Settings, _append, _goal_gap, _status_line, _training_record,
                                        _write_status, rolling_measurement)
from training.laya_factory_status import STATUS_BUCKETS, compact_status, compile_status
from training.laya_mall_slots import obtainable
from training.laya_mission import PHASES, current_phase, phase_reached

PLATES = {"iron-plate": "iron-ore", "copper-plate": "copper-ore", "stone-brick": "stone"}
# The tooling is already making it (a mall batch or loan in progress, a borrow queued): wait, and it is progress.
IN_PROGRESS = {"temporary_mall_batch", "production_prerequisite_deferred", "rationed_mall_borrow_deferred",
               "rotating_mall_prerequisite_handoff", "prerequisite_advanced",
               # The ore's plate system is still being built by bots (stage_extraction.PendingSystemDeferred).
               "pending_system_construction"}
# Receipts that put something into the world (training/laya_ops.receipt); a reservation or a wait does not.
BUILT = {"producing", "submitted", "prerequisite_advanced"}
STALL_TICKS = 18000  # five game minutes without measurable progress (loan batches are slow by design)
WAIT_EXPIRY = 1800  # ticks after which a "waiting" operation is worth retrying  # the tooling is already making it: wait, and count it as progress


# The tooling made the item by lending another mall cell, not by standing a slot (the lent cell stops its own item).
BORROW_CODES = {"temporary_mall_batch", "rationed_mall_borrow_deferred"}


def in_progress(code: str | None) -> bool:
    return bool(code) and (code.endswith("_wait") or code in IN_PROGRESS)
WAIT_TICKS = (300, 1800)
# Rules for operations: the general mission rules plus mission.ops-*, quoting the same strategy doc
# (the full catalog's geometry rules -- power variants, line sites, quad slots -- do not apply here).
OPS_CATALOG = "docs/rl/laya_ops_guidelines.json"
MAX_PRODUCE = 4  # produce options per decision (largest shortages first)
SATURATED_STOCK = 10  # a flat-out producer with fewer than this in the network is the limit, not a full mall


def plate_stages(results: list[dict]) -> dict[str, str]:
    """Per plate, the furthest operation that succeeded this run: none -> starter -> foundation."""
    stage: dict[str, str] = {}
    for r in results:
        plate = (r.get("params") or {}).get("recipe")
        if r.get("ok") and plate in PLATES:
            if r["operation"] == "starter" and stage.get(plate) is None:
                stage[plate] = "starter"
            elif r["operation"] in ("foundation", "extend"):
                stage[plate] = "foundation"
    return stage


def retired(results: list[dict], plate: str) -> bool:
    """The tooling reported the plate's starter retired (its district released)."""
    return any(r.get("ok") and r["operation"] == "retire_starter" and (r.get("params") or {}).get("recipe") == plate
               and (r.get("result") or {}).get("lifecycle") == "released" for r in results)


def unchanged_failures(results: list[dict], stock: Mapping[str, int]) -> set[tuple]:
    """(operation, parameter) whose latest attempt was short of materials none of which has since increased.

    Retrying such an operation changes nothing in the world except, in the tooling's mall path, another
    power bridge to another abandoned slot.
    """
    latest: dict[tuple, dict] = {}
    for r in results:
        params = r.get("params") or {}
        latest[(r["operation"], params.get("recipe") or params.get("item"))] = r
    stuck = set()
    for key, r in latest.items():
        if not r.get("ok") and r.get("code") == "material_shortage":
            seen = r.get("stock_at") or {}
            required = (r.get("details") or {}).get("required") or {}
            if required and all(int(stock.get(item, 0)) <= int(seen.get(item, 0)) for item in required):
                stuck.add(key)
    return stuck


def last_codes(results: list[dict], tick: int | None = None) -> dict[tuple, str]:
    """The latest outcome code per (operation, main parameter); a waiting code expires after WAIT_EXPIRY ticks."""
    codes = {}
    for r in results:
        key = (r["operation"], (r.get("params") or {}).get("recipe") or (r.get("params") or {}).get("item"))
        code = "ok" if r.get("ok") else (r.get("code") or "error")
        if tick is not None and in_progress(code) and tick - r.get("tick", tick) >= WAIT_EXPIRY:
            code = "wait_expired"
        codes[key] = code
    return codes


def batch_advanced(previous: Mapping[tuple, int], current: Mapping[tuple, int]) -> bool:
    """Whether any batch seen last check has crafted since (a first sighting is not progress)."""
    return any(count > previous[key] for key, count in current.items() if key in previous)


def shortages(results: list[dict], sources: dict | None = None) -> dict[str, int]:
    """Items the latest material shortage of each operation still asks for (``sources``: item -> plates blocked)."""
    latest: dict[tuple, dict] = {}
    for r in results:
        params = r.get("params") or {}
        latest[(r["operation"], params.get("recipe") or params.get("item"))] = r
    short: Counter = Counter()
    for r in latest.values():
        if not r.get("ok") and r.get("code") == "material_shortage":
            details = r.get("details") or {}
            available = details.get("available") or {}
            for item, count in (details.get("required") or {}).items():
                missing = int(count) - int(available.get(item, 0))
                if missing > 0:
                    short[item] = max(short[item], missing)
                    if sources is not None:
                        params = r.get("params") or {}
                        sources.setdefault(item, set()).add(params.get("recipe") or params.get("item"))
    return dict(short)


def shortage_levels(results: list[dict]) -> dict[str, int]:
    """Network level each short item needed when its operation failed: the stock then plus the shortfall.

    Fixed at failure time. A level read off today's stock drifts upward forever when the blocked operation is
    not retried (live 2026-10-15: "splitter short 1" made one splitter per loan up to 158).
    """
    latest: dict[tuple, dict] = {}
    for r in results:
        latest[_key(r)] = r
    levels: dict[str, int] = {}
    for r in latest.values():
        if r.get("ok") or r.get("code") != "material_shortage" or "stock_at" not in r:
            continue
        details = r.get("details") or {}
        available = details.get("available") or {}
        for item, count in (details.get("required") or {}).items():
            missing = int(count) - int(available.get(item, 0))
            if missing > 0:
                levels[item] = max(levels.get(item, 0), int(r["stock_at"].get(item, 0)) + missing)
    return levels


def named_prerequisites(results: list[dict]) -> dict[str, dict]:
    """Outstanding typed handoffs, retired by the parent's next outcome, independent of proxy phase.

    A rung without a quantity names a capability to establish, not a claim that one item pays its bill.
    The toolkit retains its recipe/geometry admission and internal batch sizing.
    """
    latest = {_key(r): r for r in results}
    demands: dict[str, dict] = {}
    for key, r in latest.items():
        if r.get("ok") or r.get("code") not in {"chemical_capability_handoff", "rotating_mall_prerequisite_handoff"}:
            continue
        details = r.get("details") or {}
        item = details.get("rung") or details.get("prerequisite")
        if not isinstance(item, str) or not item or item == key[1]:
            continue
        quantity = details.get("required")
        target = int(quantity) if isinstance(quantity, (int, float)) and quantity > 0 else None
        demand = demands.setdefault(item, {"target": 1, "capability": False, "parents": []})
        demand["target"] = max(demand["target"], target or 1)
        demand["capability"] |= target is None
        demand["parents"].append(f"{key[0]}:{key[1]}")
    return demands


def recipe_demand(results: list[dict], economics: Mapping | None = None) -> tuple[str, ...]:
    """Prototype queries follow mission data and outstanding work; no phase-specific recipe whitelist."""
    items = {item for phase in PHASES for item in phase["targets"]}
    items.update(shortages(results))
    items.update(named_prerequisites(results))
    for project in ((economics or {}).get("projects") or {}).values():
        items.update(project.get("missing") or {})
    return tuple(sorted(items))


RECENT = 8  # decisions kept verbatim in the history; older ones survive only in the per-operation summary
STUCK_DECISIONS = 6  # decisions without a built operation after which Laya counts itself stuck


def _key(r: Mapping) -> tuple:
    params = r.get("params") or {}
    return (r["operation"], params.get("recipe") or params.get("item"))


MAJOR = {"starter", "foundation", "extend", "steel", "retire_starter"}  # operations that change the factory's shape
BRIEF_CHARS = 8000  # ~2k tokens: the brief's room, reserved before the doc rules
CONTEXT_CHARS = 16000  # brief + doc rules; rules past what fits are left out, lowest priority first
# Doc rules that act on each brief line: the brief carries the values, the rules the strategy, so neither repeats
# the other. A line names only the rules shown in the same decision.
USES = {"GOAL": ("mission.goal", "mission.science"),
        "NOW": ("mission.phase-goals-first", "mission.serve-bottleneck"),
        "MAJOR": ("mission.ops-foundation", "mission.ops-extend", "mission.ops-retire-starter",
                  "mission.ops-starters-before-foundations"),
        "RECENT": ("mission.ops-history-no-repeat", "mission.ops-history-follow-through", "mission.ops-one-batch",
                   "mission.ops-retry-after-wait"),
        "TRIED": ("mission.ops-no-blind-retry", "mission.ops-no-idle-loans"),
        "MALL": ("mission.ops-no-idle-loans", "mission.ops-one-batch"),
        "RESERVED": ("mission.ops-shortage",),
        "SINCE LAST BUILD": ("mission.ops-history-unstick",)}


def _outcome(r: Mapping) -> str:
    if r.get("ok"):
        return r.get("code") or "ok"
    details = r.get("details") or {}
    required, available = details.get("required"), details.get("available")
    # Only a material shortage carries item maps; other codes may report a bare count here.
    if not isinstance(required, Mapping):
        required = {}
    available = available if isinstance(available, Mapping) else {}
    missing = {i: int(n) - int(available.get(i, 0)) for i, n in required.items()}
    short = ", ".join(f"{i} {n}" for i, n in missing.items() if n > 0)
    return (r.get("code") or "error") + (f" ({short})" if short else "")


def brief(results: list[dict], board: Mapping | None, phase: Mapping | None, phase_ticks: Mapping[str, int],
          start_tick: int, rules: set[str] = frozenset(), limit: int = BRIEF_CHARS,
          economics: Mapping | None = None) -> str:
    """Laya's own run, layered and compact: goal, now, next, major changes, recent moves, older attempts.

    Older decisions survive only in TRIED (attempts and last outcome per operation). Each line is tagged with
    the shown doc rules (``rules``) that act on it.
    """
    from training.laya_mission import MISSION_ID, PHASES
    ids = [p["phase_id"] for p in PHASES]
    sections = {"GOAL": [f"{MISSION_ID.removeprefix('mod-playground-')} via {' > '.join(ids)}"]}
    if phase and board:
        needs = {n["item"]: n for n in board["needs"]}
        sections["NOW"] = [f"{phase['phase_id']} {phase['name']}: " + "; ".join(
            f"{item} {(needs.get(item) or {}).get('measured_per_s') or 0:.2f}/{rate}/s"
            + ("" if (needs.get(item) or {}).get("condition") == "satisfied" else " short")
            for item, rate in phase["targets"].items())]
        later = ids.index(phase["phase_id"]) + 1
        if later < len(PHASES):
            step = PHASES[later]
            sections["NEXT"] = [f"{step['phase_id']} {step['name']}: "
                                + ", ".join(f"{i} {r}/s" for i, r in step["targets"].items())]
    minutes = lambda tick: f"+{(tick - start_tick) / 3600:.1f}min"
    major = [(start_tick + t, f"{pid} reached") for pid, t in phase_ticks.items()]
    major += [(r.get("tick", start_tick), f"{r['operation']} {(r.get('params') or {}).get('recipe') or ''}".strip()
               + ("" if r.get("ok") else f" -> {_outcome(r)}"))
              for r in results if r["operation"] in MAJOR and (r.get("ok") or _last_of(results, r))]
    sections["MAJOR"] = [f"{minutes(t)} {text}" for t, text in sorted(major)]
    # Recent moves, oldest first; identical consecutive moves collapse into one with a count.
    recent: list[list] = []
    for r in results[-3 * RECENT:]:
        text = "wait" if r["operation"] == "wait" else f"{' '.join(str(k) for k in _key(r) if k)} -> {_outcome(r)}"
        if recent and recent[-1][0] == text:
            recent[-1][1] += 1
        else:
            recent.append([text, 1])
    sections["RECENT"] = [t + (f" x{n}" if n > 1 else "") for t, n in recent][-RECENT:]
    tried: dict[str, list] = {}  # operation -> [attempts, last code], in order of last attempt
    for r in results:
        if r["operation"] != "wait":
            name = " ".join(str(k) for k in _key(r) if k)
            attempts = tried.pop(name, [0])[0] + 1
            tried[name] = [attempts, r.get("code") or ("ok" if r.get("ok") else "error")]
    sections["TRIED"] = [f"{name} {n}x last {code}" for name, (n, code) in tried.items()]
    if economics is not None:
        loans = economics.get("loans")
        sections["MALL"] = (["loans unknown (survey failed)"] if loans is None else
                            [f"{loan['lender']} cell lent to make {loan['making']} (to {loan['target']})" for loan in loans]
                            or ["no loans"])
        sections["RESERVED"] = [f"{pid} {project['state']} missing "
                                + ", ".join(f"{i} {n}" for i, n in project["missing"].items())
                                for pid, project in (economics.get("projects") or {}).items() if project["missing"]]
    sections["SINCE LAST BUILD"] = [f"{since_build(results)} decisions"]

    def render() -> str:
        lines = []
        for name, entries in sections.items():
            tags = [rule for rule in USES.get(name, ()) if rule in rules]
            lines.append(f"{name}{' [' + ', '.join(tags) + ']' if tags else ''}: {' | '.join(entries) or 'none yet'}")
        return "\n".join(lines)
    # Over budget: drop the least recent attempts summary first, then the oldest major change and recent move.
    trim = ("TRIED", "RESERVED", "MAJOR", "RECENT")
    while len(text := render()) > limit and any(sections.get(k) for k in trim):
        next(sections[k] for k in trim if sections.get(k)).pop(0)
    return text


def with_economics(menu: dict, results: list[dict], economics: Mapping | None) -> dict:
    """Attach what each option is known to cost and displace (audit finding 3).

    ``bill`` is the item bill the operation last reported short of; without one, ``bill_known`` is False, so a
    missing bill never reads as "costs nothing" (the policy's scarce/starves_goal facts come from the bill).
    ``cell_lent``: the item's own mall cell is lent to make something else, so producing it waits on that loan.
    """
    bills = {}
    for r in results:
        required = (r.get("details") or {}).get("required")
        if r.get("code") == "material_shortage" and isinstance(required, Mapping):
            bills[_key(r)] = {i: int(n) for i, n in required.items()}
    loans = (economics or {}).get("loans")
    lent = {loan["lender"] for loan in loans or ()}
    for option in menu["options"]:
        if option["family"] == "wait":
            continue
        params = option["params"]
        bill = bills.get((option["family"], params.get("recipe") or params.get("item")))
        if bill:
            option["bill"] = bill
        option["features"]["bill_known"] = bill is not None
        if option["family"] in ("produce", "mall_slot"):
            option["features"]["cell_lent"] = None if loans is None else option["product"] in lent
    return menu


def fit_rules(guidelines: list[dict], brief_text: str, limit: int = CONTEXT_CHARS) -> list[dict]:
    """The doc rules that fit after the brief: hard rules always, soft ones in the loader's priority order."""
    import json
    room = limit - len(brief_text)
    kept = [g for g in guidelines if g.get("kind") == "hard"]
    for rule in (g for g in guidelines if g.get("kind") != "hard"):
        if len(json.dumps(kept + [rule])) <= room:
            kept.append(rule)
    return [g for g in guidelines if g in kept]


def _last_of(results: list[dict], r: Mapping) -> bool:
    """r is the latest attempt of its operation (a failed major operation stays visible until retried)."""
    return next(x for x in reversed(results) if _key(x) == _key(r)) is r


def since_build(results: list[dict]) -> int:
    """Decisions since the last operation that put something into the world (a BUILT receipt)."""
    for back, r in enumerate(reversed(results)):
        if r.get("code") in BUILT:
            return back
    return len(results)


def repeat_fails(results: list[dict], key: tuple) -> int:
    """How many of this option's latest attempts, in a row, failed with the same code (capped at 3)."""
    attempts = [r for r in results if r["operation"] != "wait" and _key(r) == key]
    count = 0
    for r in reversed(attempts):
        # A success or a built stage ends the streak; so does a different failure.
        if r.get("ok") or r.get("code") in BUILT or r.get("code") != attempts[-1].get("code"):
            break
        count += 1
    return min(count, 3)


def history_features(results: list[dict], key: tuple, family: str) -> dict:
    """What Laya's recent decisions say about one option (rules in docs/rl/laya_ops_guidelines.json weigh them)."""
    last = next((r for r in reversed(results) if r["operation"] != "wait"), None)
    return {"repeat_fails": repeat_fails(results, key),
            "after_success_same_family": bool(last and last.get("ok") and last["operation"] == family),
            "tried": any(r["operation"] != "wait" and _key(r) == key for r in results),
            "stuck": since_build(results) >= STUCK_DECISIONS}


def plates_in(item: str, recipes: Mapping, seen: frozenset = frozenset()) -> set[str]:
    """Plates an item's recipe chain consumes (iron gear -> iron plate; circuit -> iron and copper plate)."""
    if item in PLATES:
        return {item}
    recipe = recipes.get(item)
    if not recipe or item in seen:
        return set()
    return set().union(*(plates_in(i["name"], recipes, seen | {item}) for i in recipe.get("ingredients", [])))


def saturated_feedstock(machines: list[Mapping], recipes: Mapping, stock: Mapping[str, int],
                        growable: tuple[str, ...]) -> dict[str, dict]:
    """Growable items whose machines all run flat out while another mall machine starves for them.

    Facts only: the item's machine count, how many consumers are starved, and its network stock (almost none).
    """
    found = {}
    for item in growable:
        own = [m for m in machines if m.get("recipe") == item]
        if not own or any(m.get("status") not in STATUS_BUCKETS["working"] for m in own):
            continue
        if int(stock.get(item, 0)) >= SATURATED_STOCK:
            continue
        starved = [m for m in machines if m.get("status") in STATUS_BUCKETS["starved"]
                   and any(i.get("name") == item for i in (recipes.get(m.get("recipe")) or {}).get("ingredients", ()))]
        if starved:
            found[item] = {"machines": len(own), "starved_consumers": len(starved)}
    return found


def compile_options(board: Mapping, results: list[dict], tick: int | None = None, recipes: Mapping | None = None,
                    stock: Mapping[str, int] | None = None, machines: list[Mapping] | None = None,
                    growable: tuple[str, ...] = (), enabled_recipes: Mapping | None = None) -> dict:
    """Operations worth offering now, each tagged with the need it serves and the facts rules weigh."""
    unmet = {n["item"] for n in board["needs"] if n["condition"] != "satisfied"}
    goal = {n["item"] for n in board["needs"] if (n.get("goal_required_per_s") or 0) > 0}
    stuck = unchanged_failures(results, stock or {})
    sources: dict[str, set] = {}
    stages, codes, short = plate_stages(results), last_codes(results, tick), shortages(results, sources)
    waiting = any(in_progress(c) for c in codes.values())
    # A mall batch or loan is under way: asking for other items makes the loans preempt each other.
    batch_active = any(in_progress(code) for (operation, _), code in codes.items() if operation in ("produce", "mall_slot"))
    options = []
    # A foundation reserves its whole bill when placed (earmarked), including the drills a starter needs, so
    # foundations wait until the iron and copper starters stand (docs/deterministic/planning.md: startup builds
    # the starters before any mine-to-refinery foundation).
    starters_done = {"iron-plate", "copper-plate"} <= {p for p, s in stages.items() if s != "none"}
    for plate in PLATES:
        # Hard lifecycle order of the tooling (its ledgers break otherwise): starter -> foundation -> extend,
        # and retiring the starter once the foundation stands.
        legal = {"none": ("starter",), "starter": ("foundation",) if starters_done else (),
                 "foundation": ("extend",) + (() if retired(results, plate) else ("retire_starter",))}[stages.get(plate, "none")]
        if plate not in unmet and PLATES[plate] not in unmet:
            # Retiring a starter is lifecycle cleanup, not production, so it outlives the plate's goal (live
            # 2026-10-15: once C4's board held only plastic, the iron starter was never retired).
            legal = tuple(operation for operation in legal if operation == "retire_starter")
        for operation in legal:
            options.append({
                "option_id": f"op.{operation}.{plate}", "family": operation, "product": plate,
                "need_id": f"item:{plate}", "summary": f"{operation} {plate}", "params": {"recipe": plate},
                "features": {"plate_stage": stages.get(plate, "none"), "last_code": codes.get((operation, plate), "new"),
                             "goal_need": plate in goal,
                             # The tooling said it waits on bots or coverage: retry after a wait, not at once.
                             "waiting": in_progress(codes.get((operation, plate))),
                             "unchanged_failure": (operation, plate) in stuck}})
    # Steel: the poles every foundation and power bridge needs are made from it. Fence the seed's poles first,
    # then build the seed once the iron foundation stands (the tooling's own order).
    steel_made = any(r.get("ok") and r["operation"] == "steel" for r in results)
    if not steel_made and codes.get(("reserve_steel_seed", None)) != "ok":
        options.append({"option_id": "op.reserve_steel_seed", "family": "reserve_steel_seed", "product": "steel-plate",
                        "need_id": "stock:steel-plate", "summary": "fence the steel starter's poles",
                        "params": {}, "features": {"goal_need": True}})
    if not steel_made and stages.get("iron-plate") == "foundation":
        options.append({"option_id": "op.steel", "family": "steel", "product": "steel-plate",
                        "need_id": "stock:steel-plate", "summary": "build the compact steel seed", "params": {},
                        "features": {"goal_need": True, "last_code": codes.get(("steel", None), "new"),
                                     "waiting": in_progress(codes.get(("steel", None)))}})
    # Standing bootstrap-mall slots (gear, cable, circuit, belt): which first is Laya's choice. Legal once the
    # starters for every plate in the item's chain exist; while a goal plate is short of materials they serve it.
    from orchestrator.baseline_production import baseline_build_order
    started = {plate for plate, stage in stages.items() if stage != "none"}
    goal_blocked = any(src & goal for src in sources.values())
    for item in baseline_build_order():
        chain = plates_in(item, recipes or {})
        if codes.get(("mall_slot", item)) != "ok" and chain and chain <= started and (
                not recipes or obtainable(recipes, item, stock or {})):
            options.append({
                "option_id": f"op.mall_slot.{item}", "family": "mall_slot", "product": item, "need_id": f"stock:{item}",
                "summary": f"mall slot for {item}", "params": {"item": item},
                "features": {"last_code": codes.get(("mall_slot", item), "new"),
                             "waiting": in_progress(codes.get(("mall_slot", item))),
                             "unchanged_failure": ("mall_slot", item) in stuck, "batch_active": batch_active,
                             "shortage": short.get(item, 0),
                             "borrows": next((r.get("code") for r in reversed(results)
                                              if _key(r) == ("mall_slot", item)), None) in BORROW_CODES,
                             "goal_need": goal_blocked or bool(sources.get(item, set()) & goal)}})
    # Borrowing a slot to batch an item needs standing slots to borrow from.
    slots = any(code == "ok" for (operation, _), code in codes.items() if operation == "mall_slot")
    # Producing an item whose chain needs an unstarted plate would make the tooling open that plate's
    # foundation ahead of its starter (bootstrap_lifecycle_conflict): not legal yet.
    # Nor an item whose chain needs something nothing can make yet (requester chests need advanced circuits,
    # so plastic): its loan never finishes and blocks every other batch (chemical_capability_handoff).
    producible = {i: m for i, m in short.items() if plates_in(i, recipes or {}) <= started
                  and obtainable(recipes, i, stock or {})} if recipes else short
    levels = shortage_levels(results)
    demands = {item: {"target": levels.get(item, int((stock or {}).get(item, 0)) + missing),
                      "missing": missing, "capability": False, "parents": []}
               for item, missing in (sorted(producible.items(), key=lambda kv: -kv[1])[:MAX_PRODUCE] if slots else ())}
    # Typed prerequisites must remain selectable even when four larger construction shortages exist.
    # They can establish upstream capability that obtainable() cannot yet prove (e.g. plastic before oil).
    for item, demand in named_prerequisites(results).items():
        if item in PLATES or not plates_in(item, recipes or {}) <= started:
            continue
        if enabled_recipes is not None and not enabled_recipes.get(item):
            continue
        existing = demands.setdefault(item, {**demand, "missing": 0})
        existing.update(target=max(existing["target"], demand["target"]),
                        capability=demand["capability"], parents=demand["parents"])
    for need in board["needs"]:
        item = need["item"]
        if (item in goal and item not in PLATES and need["condition"] in {"unbuilt", "recipe_unavailable"}
                and (enabled_recipes or recipes or {}).get(item) and plates_in(item, recipes or {}) <= started):
            demand = demands.setdefault(item, {"target": 1, "missing": 0, "parents": []})
            demand["capability"] = True
    for item, demand in demands.items():
        # The tooling's stock_target is a network level (the mall's stock gate), not a delta: asking for the
        # bare shortage gated a drill cell at 4 while the network held 6 and the starter needed 8.
        have = int((stock or {}).get(item, 0))
        target, missing = demand["target"], demand["missing"]
        if have >= target and not demand["capability"]:
            continue  # Made since the failure: retrying the blocked operation is the next step, not more stock.
        options.append({
            "option_id": f"op.produce.{item}", "family": "produce", "product": item, "need_id": f"stock:{item}",
            "summary": f"produce {item} (short {missing})", "params": {"item": item, "stock_target": target},
            # Items a goal plate's operation is blocked on serve that goal.
            "features": {"last_code": codes.get(("produce", item), "new"), "shortage": missing,
                         "prerequisite_for": sorted(demand["parents"]),
                         "waiting": in_progress(codes.get(("produce", item))),
                         "unchanged_failure": ("produce", item) in stuck, "batch_active": batch_active,
                         "goal_need": item in goal or bool(sources.get(item, set()) & goal)}})
    # A feedstock producer at its limit (live 2026-10-15: one gear AM1 behind every belt, inserter and drill).
    for item, fact in saturated_feedstock(machines or [], recipes or {}, stock or {}, growable).items():
        if any(r["operation"] == "grow" and (r.get("params") or {}) == {"item": item, "machines": fact["machines"]}
               for r in results):
            continue  # Growth from this count was already asked for; its cell is still being built.
        options.append({
            "option_id": f"op.grow.{item}", "family": "grow", "product": item, "need_id": f"stock:{item}",
            "summary": f"grow {item} ({fact['machines']} machine(s) flat out, {fact['starved_consumers']} starved)",
            "params": {"item": item, "machines": fact["machines"]},
            "features": {"last_code": codes.get(("grow", item), "new"), "saturated": True,
                         "starved_consumers": fact["starved_consumers"], "batch_active": batch_active,
                         "goal_need": bool(sources.get(item, set()) & goal)}})
    for option in options:  # Laya's own recent decisions, per option
        params = option["params"]
        option["features"].update(history_features(results, (option["family"], params.get("recipe") or params.get("item")),
                                                   option["family"]))
    stuck = since_build(results) >= STUCK_DECISIONS
    for ticks in WAIT_TICKS:
        options.append({"option_id": f"wait.{ticks}", "family": "wait", "need_id": "wait", "wait_ticks": ticks,
                        "summary": f"Wait {ticks / 60:g} game seconds", "features": {"constructing": waiting, "stuck": stuck}})
    needs = sorted({o["need_id"] for o in options})
    return {"needs": [{"need_id": n, "condition": "available", "options": [o["option_id"] for o in options
                                                                          if o["need_id"] == n]} for n in needs],
            "options": options, "shortfalls": short}


# What a restarted controller must know about the world it inherits: which plates have starters, foundations or
# retired starters, which mall slots and growth it already stood. Demand, failures and waits are not carried.
LIFECYCLE_OPERATIONS = ("starter", "foundation", "extend", "retire_starter", "steel", "reserve_steel_seed",
                        "mall_slot", "grow")


def lifecycle_receipts(records: Path) -> list[dict]:
    """Successful lifecycle receipts of earlier controller segments on this world (jumpstart experiment)."""
    import json
    carried = []
    if not records.exists():
        return carried
    for line in records.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        chosen = next((o for o in record.get("options") or () if o.get("option_id") == record["choice"]["option_id"]),
                      None)
        outcome = record.get("outcome") or {}
        if chosen and chosen.get("family") in LIFECYCLE_OPERATIONS and outcome.get("ok"):
            carried.append({"ok": True, "operation": chosen["family"], "params": chosen.get("params") or {},
                            "code": outcome.get("code"), "result": outcome.get("result"), "tick": record.get("tick")})
    return carried


def run(adapter: Any, policy: Any, *, journal: Path, decisions: int | None, measure_ticks: int, seed: int,
        run_id: str, emit: Callable[[str], None] = lambda _: None, should_stop: Callable[[], bool] = lambda: False,
        status_path: Path | None = None, settings: Settings = Settings(), session: Any = None,
        carried: list[dict] = ()) -> dict:
    """Observe -> board -> operations -> choose -> run one -> record, until the mission ends or stalls.

    ``carried`` seeds the history with an earlier segment's lifecycle receipts when a fresh controller is
    restarted on the same world; everything else starts fresh.
    """
    from training.laya_ops import DeterministicSession
    session = session or DeterministicSession(adapter.root, emit=lambda line: emit(f"  TOOL {line}"))
    records = journal.parent / "decision-states.jsonl"
    reached: list[str] = []
    results: list[dict] = list(carried)
    if carried:
        emit(f"JUMPSTART: restarting on the same world with {len(carried)} lifecycle receipt(s) from earlier "
             f"segments: {', '.join(sorted({r['operation'] for r in carried}))}")
    samples: list = []
    measurement = None
    summary: dict = {"decisions": 0, "failures": 0, "phases_reached": reached, "stop": "decision_budget"}
    stall = {"since": 0, "best_gap": math.inf, "tick": None, "machines": 0, "missing": math.inf, "made": {},
             "batches": {}}
    accept = getattr(session, "acceptance", None)

    def check_acceptance(event: str) -> None:
        """Deterministic milestone predicates beside the rate proxies (audit finding 7)."""
        if accept is None:
            return
        try:
            statuses = accept()
        except Exception as error:  # Observation must never stop the run.
            statuses = {"error": f"{type(error).__name__}: {error}"[:200]}
        summary["acceptance"] = statuses
        passed = [c for c, status in statuses.items() if status == "passed"]
        summary["accepted"] = sorted(set(summary.get("accepted", [])) | set(passed))
        _append(journal, {"run_id": run_id, "event": "acceptance", "at": event, "statuses": statuses})
    check_acceptance("start")
    index = -1
    while decisions is None or index + 1 < decisions:
        index += 1
        if should_stop():
            summary["stop"] = "stopped"
            break
        economics = session.economics() if hasattr(session, "economics") else None
        adapter.extra_recipes = recipe_demand(results, economics)
        facts = adapter.query()
        samples.append((facts["tick"], dict(facts["production"]), dict(facts["consumption"])))
        measurement = rolling_measurement(samples, settings.rate_window_ticks) or measurement
        if settings.max_game_ticks and facts["tick"] - samples[0][0] >= settings.max_game_ticks:
            summary["stop"] = "window_timeout"
            break
        phase = current_phase(reached)
        board = compile_status(facts, phase["targets"], measurement) if phase else None
        while phase and phase_reached(board):
            reached.append(phase["phase_id"])
            summary.setdefault("phase_ticks", {})[phase["phase_id"]] = facts["tick"] - samples[0][0]
            _append(journal, {"run_id": run_id, "event": "phase_reached", "phase": phase["phase_id"], "tick": facts["tick"]})
            emit(f"PHASE REACHED: {phase['phase_id']} {phase['name']} at tick {facts['tick']} (rate proxy)")
            check_acceptance(f"proxy {phase['phase_id']}")
            emit(f"ACCEPTANCE: {summary.get('acceptance')}")
            stall.update(since=0, best_gap=math.inf)
            phase = current_phase(reached)
            board = compile_status(facts, phase["targets"], measurement) if phase else None
        if phase is None:
            summary["stop"] = "mission_complete"
            break
        if settings.until_phase and settings.until_phase in reached:
            summary["stop"] = "window_reached"
            break
        stock: dict[str, int] = {}
        for network in (facts.get("networks") or {}).values():
            for name, count in (network.get("items") or {}).items():
                stock[name] = stock.get(name, 0) + int(count)
        menu = with_economics(compile_options(board, results, facts["tick"], facts.get("recipes"), stock,
                                              facts.get("machines"), getattr(session, "growable", ()),
                                              enabled_recipes=facts.get("enabled_recipes")),
                              results, economics)
        reserved: Counter = Counter()
        for project in ((economics or {}).get("projects") or {}).values():
            reserved.update(project["reserved"])
        compact = compact_status(board)
        catalog = OPS_CATALOG if settings.catalog == Settings().catalog else settings.catalog
        guidance = load_guideline_context(ROOT, phase["scopes"], catalog=catalog, max_rules=32)
        # The brief comes first and has its room reserved; the doc rules fill what is left.
        shown = {g["rule_id"] for g in guidance["guidelines"]}
        told = brief(results, board, phase, summary.get("phase_ticks", {}), samples[0][0], shown,
                     economics=economics)
        rules = fit_rules(guidance["guidelines"], told)
        if len(rules) < len(shown):  # Tag only the rules that made it in.
            told = brief(results, board, phase, summary.get("phase_ticks", {}), samples[0][0],
                         {g["rule_id"] for g in rules}, economics=economics)
        state = {"brief": told,
                 "observation_digest": {"factory_status": compact, "mission_phase": phase["phase_id"],
                                        "saved_world": {"provider_stock": {"network": stock},
                                                        "reserved_stock": {"network": dict(reserved)}}},
                 "guidelines": rules}
        choice = policy.choose(state, menu)
        chosen = next(o for o in menu["options"] if o["option_id"] == choice["option_id"])
        emit(f"DECISION {index} phase={phase['phase_id']} tick={facts['tick']} need={chosen['need_id']} "
             f"p={choice['probability']:.3f} rules={','.join(sorted(set(choice['rules'])))}: {chosen['summary']}")
        status = _status_line(compact)
        emit(f"  STATUS: {status}")
        record = {"run_id": run_id, "event": "decision", "index": index, "tick": facts["tick"],
                  "phase": phase["phase_id"], "choice": {k: choice[k] for k in ("option_id", "need_id", "probability")},
                  "summary": chosen["summary"]}
        _append(journal, record)
        if chosen["family"] == "wait":
            adapter.advance(chosen["wait_ticks"])
            result = {"ok": True, "operation": "wait", "params": {"ticks": chosen["wait_ticks"]}}
        else:
            try:
                result = session.run(chosen["family"], **chosen["params"])
            except Exception as error:  # A tooling bug is recorded and the run continues; three in a row stop it.
                result = {"ok": False, "operation": chosen["family"], "params": chosen["params"],
                          "code": "tool_error", "message": f"{type(error).__name__}: {error}"[:400]}
                summary["failures"] += 1
        result["tick"] = facts["tick"]
        if result.get("code") == "material_shortage":  # What was in stock when it failed, to see change later.
            result["stock_at"] = {item: stock.get(item, 0) for item in ((result.get("details") or {}).get("required") or {})}
        results.append(result)
        after = adapter.query()
        emit(f"  OUTCOME: {'ok' if result.get('ok') else result.get('code')}"
             + (f" -- {result.get('message', '')[:200]}" if not result.get("ok") else ""))
        _training_record(records, run_id=run_id, index=index, tick=facts["tick"], phase=phase["phase_id"],
                         state=state, options=[{k: o.get(k) for k in ("option_id", "family", "need_id", "product",
                                                                      "params", "features")} for o in menu["options"]],
                         distribution=choice["distribution"], choice=choice,
                         outcome={**{k: result.get(k) for k in ("ok", "code", "classification", "state", "details",
                                                                 "message", "result")},
                                  "ticks": after["tick"] - facts["tick"]})
        _write_status(status_path, {"run_id": run_id, "index": index, "tick": facts["tick"], "phase": phase,
                                    "reached": reached, "board": compact, "brief": state["brief"], "choice": {**choice, "summary": chosen["summary"]},
                                    "menu": {o["option_id"]: o["summary"] for o in menu["options"]},
                                    "decisions": index + 1, "failures": summary["failures"],
                                    "authority": policy.authority})
        summary["decisions"] = index + 1
        if summary["decisions"] % 10 == 0:
            check_acceptance(f"decision {index}")
        gap = _goal_gap(compile_status(after, phase["targets"], measurement))
        # Progress is measurable change, not a busy signal (a loan that never finishes once kept a run alive for
        # 2 hours): more machines, fewer missing materials, or a closer goal.
        machines = len(after.get("machines") or [])
        short = shortages(results)
        missing = sum(short.values())
        # Crafting a short item is progress even before the blocked operation is retried.
        made = {item: int((after.get("production") or {}).get(item, 0)) for item in short}
        crafted = any(count > stall["made"].get(item, count) for item, count in made.items())
        stall["made"] = {**stall["made"], **made}
        # An active finite batch whose craft counter rose is progress the shortage map cannot see (internal
        # chemical-ladder batches); a first sighting or an unchanged counter is not.
        batches = session.active_batch_crafts() if hasattr(session, "active_batch_crafts") else {}
        advancing = batch_advanced(stall["batches"], batches)
        stall["batches"] = batches
        progressed = (machines > stall["machines"] or missing < stall["missing"] or crafted or advancing
                      or gap < stall["best_gap"] * 0.95)
        stall["machines"], stall["missing"] = max(stall["machines"], machines), min(stall["missing"], missing)
        stall["best_gap"] = min(stall["best_gap"], gap)
        if progressed or stall["tick"] is None:
            stall["tick"] = after["tick"]
        # Operations decide in seconds of game time, so the stall limit is game time, not a decision count.
        if after["tick"] - stall["tick"] >= STALL_TICKS:
            summary["stop"] = "stalled"
            emit(f"STALLED: {STALL_TICKS} ticks without a built operation, work in progress or goal progress")
            break
        if summary["failures"] >= 3 and all(r.get("code") == "tool_error" for r in results[-3:]):
            summary["stop"] = "safe_stop_after_repeated_failures"
            break
    check_acceptance("end")
    session.close()
    # Typed outcomes, not only caught exceptions: an untyped_stuck is a tool fault even when nothing raised.
    summary["codes"] = dict(Counter(r.get("code") or "error" for r in results if not r.get("ok")))
    emit(f"SUMMARY: stop={summary['stop']} decisions={summary['decisions']} failures={summary['failures']} "
         f"phases={','.join(reached) or 'none'} accepted={','.join(summary.get('accepted', [])) or 'none'}")
    return summary
