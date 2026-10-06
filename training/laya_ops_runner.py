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
from training.laya_factory_status import compact_status, compile_status
from training.laya_mall_slots import obtainable
from training.laya_mission import current_phase, phase_reached

PLATES = {"iron-plate": "iron-ore", "copper-plate": "copper-ore", "stone-brick": "stone"}
# The tooling is already making it (a mall batch or loan in progress, a borrow queued): wait, and it is progress.
IN_PROGRESS = {"temporary_mall_batch", "production_prerequisite_deferred", "rationed_mall_borrow_deferred",
               "rotating_mall_prerequisite_handoff"}
STALL_TICKS = 18000  # five game minutes without measurable progress (loan batches are slow by design)
WAIT_EXPIRY = 1800  # ticks after which a "waiting" operation is worth retrying  # the tooling is already making it: wait, and count it as progress


def in_progress(code: str | None) -> bool:
    return bool(code) and (code.endswith("_wait") or code in IN_PROGRESS)
WAIT_TICKS = (300, 1800)
# Rules for operations: the general mission rules plus mission.ops-*, quoting the same strategy doc
# (the full catalog's geometry rules -- power variants, line sites, quad slots -- do not apply here).
OPS_CATALOG = "docs/rl/laya_ops_guidelines.json"
MAX_PRODUCE = 4  # produce options per decision (largest shortages first)


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


def plates_in(item: str, recipes: Mapping, seen: frozenset = frozenset()) -> set[str]:
    """Plates an item's recipe chain consumes (iron gear -> iron plate; circuit -> iron and copper plate)."""
    if item in PLATES:
        return {item}
    recipe = recipes.get(item)
    if not recipe or item in seen:
        return set()
    return set().union(*(plates_in(i["name"], recipes, seen | {item}) for i in recipe.get("ingredients", [])))


def compile_options(board: Mapping, results: list[dict], tick: int | None = None, recipes: Mapping | None = None,
                    stock: Mapping[str, int] | None = None) -> dict:
    """Operations worth offering now, each tagged with the need it serves and the facts rules weigh."""
    unmet = {n["item"] for n in board["needs"] if n["condition"] != "satisfied"}
    goal = {n["item"] for n in board["needs"] if (n.get("goal_required_per_s") or 0) > 0}
    stuck = unchanged_failures(results, stock or {})
    sources: dict[str, set] = {}
    stages, codes, short = plate_stages(results), last_codes(results, tick), shortages(results, sources)
    waiting = any(in_progress(c) for c in codes.values())
    options = []
    for plate in PLATES:
        if plate not in unmet and PLATES[plate] not in unmet:
            continue
        # Hard lifecycle order of the tooling (its ledgers break otherwise): starter -> foundation -> extend,
        # and retiring the starter once the foundation stands.
        legal = {"none": ("starter",), "starter": ("foundation",),
                 "foundation": ("extend",) + (() if retired(results, plate) else ("retire_starter",))}[stages.get(plate, "none")]
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
                             "unchanged_failure": ("mall_slot", item) in stuck,
                             "shortage": short.get(item, 0),
                             "goal_need": goal_blocked or bool(sources.get(item, set()) & goal)}})
    # Borrowing a slot to batch an item needs standing slots to borrow from.
    slots = any(code == "ok" for (operation, _), code in codes.items() if operation == "mall_slot")
    # Producing an item whose chain needs an unstarted plate would make the tooling open that plate's
    # foundation ahead of its starter (bootstrap_lifecycle_conflict): not legal yet.
    # Nor an item whose chain needs something nothing can make yet (requester chests need advanced circuits,
    # so plastic): its loan never finishes and blocks every other batch (chemical_capability_handoff).
    producible = {i: m for i, m in short.items() if plates_in(i, recipes or {}) <= started
                  and obtainable(recipes, i, stock or {})} if recipes else short
    for item, missing in (sorted(producible.items(), key=lambda kv: -kv[1])[:MAX_PRODUCE] if slots else ()):
        options.append({
            "option_id": f"op.produce.{item}", "family": "produce", "product": item, "need_id": f"stock:{item}",
            "summary": f"produce {item} (short {missing})", "params": {"item": item, "stock_target": missing},
            # Items a goal plate's operation is blocked on serve that goal.
            "features": {"last_code": codes.get(("produce", item), "new"), "shortage": missing,
                         "waiting": in_progress(codes.get(("produce", item))),
                         "unchanged_failure": ("produce", item) in stuck,
                         "goal_need": bool(sources.get(item, set()) & goal)}})
    for ticks in WAIT_TICKS:
        options.append({"option_id": f"wait.{ticks}", "family": "wait", "need_id": "wait", "wait_ticks": ticks,
                        "summary": f"Wait {ticks / 60:g} game seconds", "features": {"constructing": waiting}})
    needs = sorted({o["need_id"] for o in options})
    return {"needs": [{"need_id": n, "condition": "available", "options": [o["option_id"] for o in options
                                                                          if o["need_id"] == n]} for n in needs],
            "options": options, "shortfalls": short}


def run(adapter: Any, policy: Any, *, journal: Path, decisions: int | None, measure_ticks: int, seed: int,
        run_id: str, emit: Callable[[str], None] = lambda _: None, should_stop: Callable[[], bool] = lambda: False,
        status_path: Path | None = None, settings: Settings = Settings(), session: Any = None) -> dict:
    """Observe -> board -> operations -> choose -> run one -> record, until the mission ends or stalls."""
    from training.laya_ops import DeterministicSession
    session = session or DeterministicSession(adapter.root, emit=lambda line: emit(f"  TOOL {line}"))
    records = journal.parent / "decision-states.jsonl"
    reached: list[str] = []
    results: list[dict] = []
    samples: list = []
    measurement = None
    summary: dict = {"decisions": 0, "failures": 0, "phases_reached": reached, "stop": "decision_budget"}
    stall = {"since": 0, "best_gap": math.inf, "tick": None, "machines": 0, "missing": math.inf, "made": {}}
    index = -1
    while decisions is None or index + 1 < decisions:
        index += 1
        if should_stop():
            summary["stop"] = "stopped"
            break
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
            emit(f"PHASE REACHED: {phase['phase_id']} {phase['name']} at tick {facts['tick']}")
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
        menu = compile_options(board, results, facts["tick"], facts.get("recipes"), stock)
        compact = compact_status(board)
        catalog = OPS_CATALOG if settings.catalog == Settings().catalog else settings.catalog
        guidance = load_guideline_context(ROOT, phase["scopes"], catalog=catalog, max_rules=32)
        state = {"observation_digest": {"factory_status": compact, "mission_phase": phase["phase_id"],
                                        "saved_world": {"provider_stock": {}}},
                 "guidelines": guidance["guidelines"]}
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
                         outcome={**{k: result.get(k) for k in ("ok", "code", "state", "details", "message")},
                                  "ticks": after["tick"] - facts["tick"]})
        _write_status(status_path, {"run_id": run_id, "index": index, "tick": facts["tick"], "phase": phase,
                                    "reached": reached, "board": compact, "choice": {**choice, "summary": chosen["summary"]},
                                    "menu": {o["option_id"]: o["summary"] for o in menu["options"]},
                                    "decisions": index + 1, "failures": summary["failures"],
                                    "authority": policy.authority})
        summary["decisions"] = index + 1
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
        progressed = (machines > stall["machines"] or missing < stall["missing"] or crafted
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
    session.close()
    emit(f"SUMMARY: stop={summary['stop']} decisions={summary['decisions']} failures={summary['failures']} "
         f"phases={','.join(reached) or 'none'}")
    return summary
