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
from training.laya_saved_world import speed_status
from training.laya_mall_slots import obtainable
from training.laya_mission import current_phase, phase_reached

PLATES = {"iron-plate": "iron-ore", "copper-plate": "copper-ore", "stone-brick": "stone"}
# The tooling is already making it (a mall batch or loan in progress, a borrow queued): wait, and it is progress.
IN_PROGRESS = {"temporary_mall_batch", "production_prerequisite_deferred", "rationed_mall_borrow_deferred",
               "rotating_mall_prerequisite_handoff", "prerequisite_advanced", "retirement_pending",
               # The ore's plate system is still being built by bots (stage_extraction.PendingSystemDeferred).
               "pending_system_construction"}
# Receipts that put something into the world (training/laya_ops.receipt); a reservation or a wait does not.
BUILT = {"producing", "submitted", "prerequisite_advanced"}
# Decision-idle stall budget: game ticks Laya spends waiting, not the game time one blocking tool
# call legitimately spends building (a foundation call can eat a whole window). Independent total
# game/wall caps still bound the run.
STALL_TICKS = 18000  # five idle game minutes without measurable progress (loan batches are slow by design)
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
               and isinstance(r.get("result"), Mapping) and r["result"].get("lifecycle") == "released" for r in results)


def pending_retirements(results: list[dict], districts: Mapping | None = None) -> set[str]:
    """Submitted replacements need reconciliation until released, independent of rate demand.

    Scoped ledger facts take precedence over local receipts: a shortage can return
    after submission, and bots can complete that work without another successful call.
    Offering reconciliation does not certify readiness or authorize early removal.
    """
    pending = {plate for plate, stage in plate_stages(results).items()
               if stage == "foundation" and not retired(results, plate)}
    for plate, state in (districts or {}).items():
        if plate in PLATES:
            pending.discard(plate)
            if state.get("replacement_submitted") is True and state.get("lifecycle_state") in {
                "provisioning", "validating", "retiring",
            }:
                pending.add(plate)
    return pending


def unchanged_failures(results: list[dict], stock: Mapping[str, int],
                       allocation: Mapping[str, tuple] | None = None) -> set[tuple]:
    """(operation, parameter) whose latest attempt was short of materials none of which has since increased.

    Retrying such an operation changes nothing in the world except, in the tooling's mall path, another
    power bridge to another abandoned slot.  ``allocation`` is the current owner-attributed claim per
    item (training/laya_ops_runner.reservation_allocation); when a required item's claim moved since the
    failure was recorded, its allocation changed, so the failure is no longer unchanged.
    """
    latest: dict[tuple, dict] = {}
    current_allocation = _canonical_claims(allocation) if allocation is not None else None
    for r in results:
        params = r.get("params") or {}
        latest[(r["operation"], params.get("recipe") or params.get("item"))] = r
    stuck = set()
    for key, r in latest.items():
        if not r.get("ok") and r.get("code") == "material_shortage":
            seen = r.get("stock_at") or {}
            required = (r.get("details") or {}).get("required") or {}
            if required and all(int(stock.get(item, 0)) <= int(seen.get(item, 0)) for item in required):
                if current_allocation is not None:
                    recorded = _canonical_claims(r.get("allocation_at"))
                    if any(
                        current_allocation.get(item, ()) != recorded.get(item, ())
                        for item in required
                    ):
                        continue  # the reservation owner/claim moved: worth reconsidering
                stuck.add(key)
    return stuck


def _canonical_claims(claims: Mapping | None) -> dict[str, tuple[tuple[str, int], ...]]:
    """Compare ledger claims consistently before or after a JSON round trip."""
    canonical = {}
    for item, entries in (claims or {}).items():
        if entries is None:
            continue
        canonical[item] = tuple(sorted((str(owner), int(count)) for owner, count in entries))
    return canonical


# The tooling found no batch it can build next (terminal); nothing is under construction.
NO_GEOMETRY = {"district_expansion_unbuildable"}


def outcome_category(result: Mapping | None) -> str:
    """One category for an operation's last result (the runner contract, not its raw code).

    Distinguishes a wait on construction, a terminal no-geometry refusal, a delivery
    (a build/producer), and a material failure.  A missing result or code stays
    "unknown": unknown is never rendered as a failure.
    """
    if result is None:
        return "unknown"
    if result.get("ok"):
        return "delivery"
    code = result.get("code")
    if not code:
        return "unknown"
    if in_progress(code) or result.get("state") == "constructing":
        return "constructing"
    if code in NO_GEOMETRY or code.endswith("_unbuildable"):
        return "no_geometry"
    if code == "material_shortage":
        return "material_failure"
    return "failed"


def option_outcomes(results: list[dict]) -> dict[tuple, str]:
    """Categorical last outcome per (operation, main parameter); unrelated waits are ignored."""
    latest: dict[tuple, dict] = {}
    for r in results:
        if r.get("operation") == "wait":
            continue
        key = (r["operation"], (r.get("params") or {}).get("recipe") or (r.get("params") or {}).get("item"))
        latest[key] = r
    return {key: outcome_category(r) for key, r in latest.items()}


def reservation_allocation(economics: Mapping | None) -> dict[str, tuple]:
    """item -> sorted ``(owner, reserved count)`` claims from the active material ledger.

    This is the owner-attributed allocation ``unchanged_failures`` compares: a moved
    claim (shrank, grew, or changed owner) clears an otherwise-unchanged material failure.
    """
    claims: dict[str, list[tuple[str, int]]] = {}
    for owner, project in ((economics or {}).get("projects") or {}).items():
        for item, count in (project.get("reserved") or {}).items():
            if int(count) > 0:
                claims.setdefault(item, []).append((owner, int(count)))
    return {item: tuple(sorted(values)) for item, values in claims.items()}


def shorts_of(results: list[dict]) -> dict[tuple, dict]:
    """Per option, preserve the bill and missing items observed at its last short attempt."""
    bills: dict[tuple, dict] = {}
    for r in results:
        if r.get("ok") or r.get("code") != "material_shortage":
            continue
        details = r.get("details") or {}
        required = details.get("required")
        available = details.get("available")
        if not isinstance(required, Mapping):
            continue
        bill = {name: int(count) for name, count in required.items()}
        historical_missing = None
        historical_available = None
        if isinstance(available, Mapping):
            historical_available = {
                name: int(available.get(name, 0)) for name in bill
            }
            historical_missing = {
                name: count - int(available.get(name, 0))
                for name, count in bill.items()
                if count > int(available.get(name, 0))
            }
        bills[_key(r)] = {
            "bill": bill, "last_attempt_missing": historical_missing,
            "last_attempt_available": historical_available,
            "reported_owner": details.get("project_id") or details.get("claimant"),
            "reported_stage": details.get("stage"),
        }
    return bills


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
        "LIFECYCLE": ("mission.ops-retire-starter", "mission.phase-goals-first"),
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
        sections["LIFECYCLE"] = [f"{plate} replacement submitted; reconcile before starter release"
                                 for plate in sorted(pending_retirements(
                                     results, economics.get("bootstrap_districts")))]
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


def with_economics(menu: dict, results: list[dict], economics: Mapping | None,
                   *, stock: Mapping[str, int] | None = None) -> dict:
    """Attach what each option is known to cost and displace (audit finding 3).

    ``bill`` and ``last_attempt_missing_items`` preserve the last reported shortage.
    ``missing_items``/``inputs_missing`` describe current availability only when the
    attempt's reported project, current network stock, and active reservations make
    that calculation knowable. Otherwise they are None. ``reservation_owner`` names
    only the reported claimant. ``demand_point``/``demand_distance`` relay the shared
    demand-siting fact. ``cell_lent`` means the item's own mall cell is lent elsewhere.
    """
    bills = shorts_of(results)
    loans = (economics or {}).get("loans")
    lent = {loan["lender"] for loan in loans or ()}
    projects = (economics or {}).get("projects")
    demand = (economics or {}).get("demand") or {}
    for option in menu["options"]:
        if option["family"] == "wait":
            continue
        params = option["params"]
        key = (option["family"], params.get("recipe") or params.get("item"))
        short = bills.get(key)
        option["features"]["bill_known"] = short is not None
        if short is not None:
            option["bill"] = dict(short["bill"])
            option["features"]["last_attempt_missing_items"] = (
                None if short["last_attempt_missing"] is None
                else dict(short["last_attempt_missing"])
            )
            option["features"]["last_attempt_available_items"] = (
                None if short["last_attempt_available"] is None
                else dict(short["last_attempt_available"])
            )
            claim = short["reported_owner"]
            if claim is None and isinstance(projects, Mapping):
                stage = short["reported_stage"]
                claim = stage if stage in projects else None
            claim = str(claim) if claim else None
            claim_owners = (
                {item: [claim] for item in short["bill"]}
                if claim else None
            )
            option["features"]["reservation_owner"] = claim_owners
            current_available = None
            current_missing = None
            if claim and isinstance(stock, Mapping) and isinstance(projects, Mapping):
                foreign_reserved: Counter = Counter()
                for project_id, project in projects.items():
                    if project_id != claim:
                        foreign_reserved.update(project.get("reserved") or {})
                current_available = {
                    item: max(0, int(stock.get(item, 0)) - int(foreign_reserved.get(item, 0)))
                    for item in short["bill"]
                }
                current_missing = {
                    item: required - current_available[item]
                    for item, required in short["bill"].items()
                    if required > current_available[item]
                }
            option["features"]["available_items"] = current_available
            option["features"]["missing_items"] = current_missing
            option["features"]["inputs_missing"] = (
                None if current_missing is None else bool(current_missing)
            )
        else:
            option["features"]["last_attempt_available_items"] = None
            option["features"]["last_attempt_missing_items"] = None
            option["features"]["available_items"] = None
            option["features"]["missing_items"] = None
            option["features"]["inputs_missing"] = None
            option["features"]["reservation_owner"] = None
        fact = demand.get(option.get("product")) or demand.get(params.get("recipe") or params.get("item"))
        point = (fact or {}).get("point")
        option["features"]["demand_point"] = list(point) if point else None
        option["features"]["demand_distance"] = (float(fact["distance"])
                                                 if fact and "distance" in fact else None)
        if option["family"] in ("produce", "mall_slot"):
            option["features"]["cell_lent"] = None if loans is None else option["product"] in lent
    # Free mall-slot capacity, where the survey knows it (main integrates the growth operation itself).
    menu.setdefault("facts", {})["free_slots"] = (economics or {}).get("free_slots")
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


def compile_options(board: Mapping, results: list[dict], tick: int | None = None, recipes: Mapping | None = None,
                    stock: Mapping[str, int] | None = None, *, bootstrap_districts: Mapping | None = None,
                    allocation: Mapping[str, tuple] | None = None,
                    measurement: Mapping | None = None) -> dict:
    """Operations worth offering now, each tagged with the need it serves and the facts rules weigh.

    ``allocation`` is the owner-attributed reservation claim (see ``reservation_allocation``); it clears
    an unchanged material failure when the claim moved.  ``measurement`` supplies the window's measured
    plate surplus vs mall consumption, where available.
    """
    unmet = {n["item"] for n in board["needs"] if n["condition"] != "satisfied"}
    goal = {n["item"] for n in board["needs"] if (n.get("goal_required_per_s") or 0) > 0}
    stuck = unchanged_failures(results, stock or {}, allocation)
    sources: dict[str, set] = {}
    stages, codes, short = plate_stages(results), last_codes(results, tick), shortages(results, sources)
    pending = pending_retirements(results, bootstrap_districts)
    waiting = any(in_progress(c) for c in codes.values())
    # A mall batch or loan is under way: asking for other items makes the loans preempt each other.
    batch_active = any(in_progress(code) for (operation, _), code in codes.items() if operation in ("produce", "mall_slot"))
    options = []
    # A foundation reserves its whole bill when placed (earmarked), including the drills a starter needs, so
    # foundations wait until the iron and copper starters stand (docs/deterministic/planning.md: startup builds
    # the starters before any mine-to-refinery foundation).
    starters_done = {"iron-plate", "copper-plate"} <= {p for p, s in stages.items() if s != "none"}
    for plate in PLATES:
        # New capacity follows demand; finishing owned work survives rate/phase changes.
        legal = ()
        if plate in unmet or PLATES[plate] in unmet:
            legal = {"none": ("starter",), "starter": ("foundation",) if starters_done else (),
                     "foundation": ("extend",)}[stages.get(plate, "none")]
        if plate in pending:
            legal += ("retire_starter",)
        for operation in legal:
            lifecycle_pending = operation == "retire_starter"
            options.append({
                "option_id": f"op.{operation}.{plate}", "family": operation, "product": plate,
                "need_id": f"{'lifecycle' if lifecycle_pending else 'item'}:{plate}",
                "summary": f"{operation} {plate}", "params": {"recipe": plate},
                "features": {"plate_stage": stages.get(plate, "none"), "last_code": codes.get((operation, plate), "new"),
                             "goal_need": plate in goal or lifecycle_pending,
                             "lifecycle_pending": lifecycle_pending,
                             "lifecycle_state": ((bootstrap_districts or {}).get(plate) or {}).get("lifecycle_state"),
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

    def mall_slot(item: str) -> None:
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

    for item in baseline_build_order():
        chain = plates_in(item, recipes or {})
        if codes.get(("mall_slot", item)) != "ok" and chain and chain <= started and (
                not recipes or obtainable(recipes, item, stock or {})):
            mall_slot(item)
    # Borrowing a slot to batch an item needs standing slots to borrow from.
    slots = any(code == "ok" for (operation, _), code in codes.items() if operation == "mall_slot")
    # Producing an item whose chain needs an unstarted plate would make the tooling open that plate's
    # foundation ahead of its starter (bootstrap_lifecycle_conflict): not legal yet.
    # Nor an item whose chain needs something nothing can make yet (requester chests need advanced circuits,
    # so plastic): its loan never finishes and blocks every other batch (chemical_capability_handoff).
    producible = {i: m for i, m in short.items() if plates_in(i, recipes or {}) <= started
                  and obtainable(recipes, i, stock or {})} if recipes else short
    # A short item may also get its own standing slot, as the deterministic runner's demand-owned slots do
    # (live 2026-10-10: splitter was only ever offered as a borrowed batch, 112 offers, no standing producer).
    # The tooling's slot-capacity and funding gates still decide whether the slot can be placed.
    core = set(baseline_build_order())
    for item in sorted(producible):
        if item not in core and codes.get(("mall_slot", item)) != "ok" and plates_in(item, recipes or {}):
            mall_slot(item)
    for item, missing in (sorted(producible.items(), key=lambda kv: -kv[1])[:MAX_PRODUCE] if slots else ()):
        options.append({
            "option_id": f"op.produce.{item}", "family": "produce", "product": item, "need_id": f"stock:{item}",
            "summary": f"produce {item} (short {missing})", "params": {"item": item, "stock_target": missing},
            # Items a goal plate's operation is blocked on serve that goal.
            "features": {"last_code": codes.get(("produce", item), "new"), "shortage": missing,
                         "waiting": in_progress(codes.get(("produce", item))),
                         "unchanged_failure": ("produce", item) in stuck, "batch_active": batch_active,
                         "goal_need": bool(sources.get(item, set()) & goal)}})
    outcomes = option_outcomes(results)
    for option in options:  # Laya's own recent decisions, per option
        params = option["params"]
        key = (option["family"], params.get("recipe") or params.get("item"))
        option["features"].update(history_features(results, key, option["family"]))
        category = outcomes.get(key, "unknown")
        if category == "material_failure" and option["features"].get("unchanged_failure"):
            category = "unchanged_material_failure"
        option["features"]["last_outcome"] = category
    stuck = since_build(results) >= STUCK_DECISIONS
    for ticks in WAIT_TICKS:
        options.append({"option_id": f"wait.{ticks}", "family": "wait", "need_id": "wait", "wait_ticks": ticks,
                        "summary": f"Wait {ticks / 60:g} game seconds", "features": {"constructing": waiting, "stuck": stuck}})
    needs = sorted({o["need_id"] for o in options})
    # Facts for slot-growth decisions (main integrates growth itself): measured force rates remain
    # per-item unknown when the observation did not include that item.
    measured = measurement or {}
    production = measured.get("production") or {}
    consumption = measured.get("consumption") or {}
    def measured_rate(source: Mapping, item: str) -> float | None:
        record = source.get(item)
        if not isinstance(record, Mapping) or record.get("per_second") is None:
            return None
        return round(float(record["per_second"]), 4)

    plate_net = None
    force_consumption = None
    if measurement is not None:
        plate_net = {}
        force_consumption = {}
        for plate in PLATES:
            produced = measured_rate(production, plate)
            consumed = measured_rate(consumption, plate)
            plate_net[plate] = None if produced is None or consumed is None else round(produced - consumed, 4)
            force_consumption[plate] = consumed
    facts = {
        "measured_plate_net": plate_net,
        "measured_force_consumption": force_consumption,
        "shortfall_feasible": (None if not recipes else {item: item in producible for item in short}),
    }
    return {"needs": [{"need_id": n, "condition": "available", "options": [o["option_id"] for o in options
                                                                          if o["need_id"] == n]} for n in needs],
            "options": options, "shortfalls": short, "facts": facts}


def _live_tick(adapter, fallback: int) -> int:
    """Read the execution boundary without taking a full factory snapshot."""
    tick = getattr(adapter, "tick", None)
    try:
        return int(tick() if callable(tick) else tick) if tick is not None else fallback
    except (TypeError, ValueError, RuntimeError):
        return fallback


def run(adapter: Any, policy: Any, *, journal: Path, decisions: int | None, measure_ticks: int, seed: int,
        run_id: str, emit: Callable[[str], None] = lambda _: None, should_stop: Callable[[], bool] = lambda: False,
        status_path: Path | None = None, settings: Settings = Settings(), session: Any = None,
        until_accepted: str | None = None) -> dict:
    """Observe -> board -> operations -> choose -> run one -> record, until the mission ends or stalls."""
    from training.laya_ops import DeterministicSession
    session = session or DeterministicSession(adapter.root, emit=lambda line: emit(f"  TOOL {line}"))
    records = journal.parent / "decision-states.jsonl"
    reached: list[str] = []
    results: list[dict] = []
    samples: list = []
    measurement = None
    summary: dict = {"decisions": 0, "failures": 0, "phases_reached": reached, "stop": "decision_budget"}
    # The stall clock tracks decision-idle game time and decisions without genuine progress, not the
    # game ticks one blocking tool call spends building. Total game/wall caps bound the run separately.
    stall = {"idle_ticks": 0, "since": 0, "tool_ticks": 0, "best_gap": math.inf, "machines": 0,
             "missing": math.inf, "made": {}, "last_tick": None}
    accept = getattr(session, "acceptance", None)
    acceptance_samples = 0

    def check_acceptance(event: str) -> None:
        """Deterministic milestone predicates beside the rate proxies (audit finding 7)."""
        nonlocal acceptance_samples
        if accept is None:
            return
        try:
            statuses = accept()
        except Exception as error:  # Observation must never stop the run.
            statuses = {"error": f"{type(error).__name__}: {error}"[:200]}
        summary["acceptance"] = statuses
        passed = [c for c, status in statuses.items() if status == "passed"]
        summary["accepted"] = sorted(set(summary.get("accepted", [])) | set(passed))
        details = getattr(session, "acceptance_details", None)
        reference = None
        if details is not None:
            from training.laya_acceptance import summary_details
            directory = journal.parent / "acceptance" / run_id
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{acceptance_samples:04d}.json"
            acceptance_samples += 1
            import json
            with path.open("x", encoding="utf-8") as out:
                json.dump({**details, "run_id": run_id, "at": event}, out, sort_keys=True)
                out.write("\n")
            reference = str(path.resolve())
            summary["acceptance_details"] = summary_details(details, reference)
            c1 = details["results"]["C1"]
            checks = c1["evidence"]["checks"]
            failed = [name for name, value in checks.items() if value is False]
            unknown = [name for name, value in checks.items() if value is None]
            emit(f"C1 CHECK [{event}]: current={c1['status']} failed={failed} unknown={unknown} evidence={reference}")
        else:
            summary.pop("acceptance_details", None)
        _append(journal, {"run_id": run_id, "event": "acceptance", "at": event, "statuses": statuses,
                          "details_path": reference})
    check_acceptance("start")
    index = -1
    while decisions is None or index + 1 < decisions:
        index += 1
        if until_accepted and until_accepted in summary.get("accepted", []):
            summary["stop"] = "accepted_window_reached"
            break
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
            emit(f"PHASE REACHED: {phase['phase_id']} {phase['name']} at tick {facts['tick']} (rate proxy)")
            check_acceptance(f"proxy {phase['phase_id']}")
            emit(f"ACCEPTANCE: {summary.get('acceptance')}")
            stall.update(since=0, best_gap=math.inf)
            phase = current_phase(reached)
            board = compile_status(facts, phase["targets"], measurement) if phase else None
        if until_accepted and until_accepted in summary.get("accepted", []):
            summary["stop"] = "accepted_window_reached"
            break
        if phase is None:
            summary["stop"] = "proxy_mission_complete_without_acceptance" if until_accepted else "mission_complete"
            break
        if settings.until_phase and settings.until_phase in reached:
            summary["stop"] = "window_reached"
            break
        stock: dict[str, int] = {}
        for network in (facts.get("networks") or {}).values():
            for name, count in (network.get("items") or {}).items():
                stock[name] = stock.get(name, 0) + int(count)
        economics = session.economics() if hasattr(session, "economics") else None
        districts = (economics or {}).get("bootstrap_districts")
        allocation = reservation_allocation(economics) if economics is not None else None
        menu = with_economics(compile_options(board, results, facts["tick"], facts.get("recipes"), stock,
                                             bootstrap_districts=districts, allocation=allocation,
                                             measurement=measurement),
                              results, economics, stock=stock if economics is not None else None)
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
                                                        "reserved_stock": {"network": dict(reserved)},
                                                        "bootstrap_districts": districts}},
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
        if chosen["family"] == "retire_starter":
            check_acceptance(f"before retirement {index}")
        tool_start_tick = _live_tick(adapter, facts["tick"])
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
        if result.get("code") == "material_shortage":  # Preserve known snapshots for retry comparison.
            required = (result.get("details") or {}).get("required") or {}
            result["stock_at"] = ({item: stock.get(item, 0) for item in required}
                                   if economics is not None else None)
            result["allocation_at"] = ({item: allocation.get(item, ()) for item in required}
                                       if allocation is not None else None)
        results.append(result)
        tool_end_tick = _live_tick(adapter, tool_start_tick)
        after = adapter.query()
        emit(f"  OUTCOME: {'ok' if result.get('ok') else result.get('code')}"
             + (f" -- {result.get('message', '')[:200]}" if not result.get("ok") else ""))
        _training_record(records, run_id=run_id, index=index, tick=facts["tick"], phase=phase["phase_id"],
                         state=state, options=[{k: o.get(k) for k in ("option_id", "family", "need_id", "product",
                                                                      "params", "features")} for o in menu["options"]],
                         distribution=choice["distribution"], choice=choice,
                         outcome={**{k: result.get(k) for k in ("ok", "code", "classification", "state", "details",
                                                                 "message", "result")},
                                  "progress": bool(result.get("progress")),
                                  "ticks": after["tick"] - facts["tick"]})
        _write_status(status_path, {"run_id": run_id, "index": index, "tick": facts["tick"], "phase": phase,
                                    "reached": reached, "board": compact, "brief": state["brief"], "choice": {**choice, "summary": chosen["summary"]},
                                    "menu": {o["option_id"]: o["summary"] for o in menu["options"]},
                                    "decisions": index + 1, "failures": summary["failures"],
                                    "authority": policy.authority, "speed": speed_status(adapter)})
        summary["decisions"] = index + 1
        if chosen["family"] == "retire_starter":
            check_acceptance(f"after retirement {index}")
        elif summary["decisions"] % 10 == 0:
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
        # A newly funded, committed construction plan may progress before an incomplete return.
        # Repeating a construction status or merely reserving a ready bill is not progress.
        submitted = bool(result.get("progress"))
        working = False  # A repeated wait code alone is not observed progress.
        progressed = (machines > stall["machines"] or missing < stall["missing"] or crafted
                      or gap < stall["best_gap"] * 0.95 or submitted or working)
        stall["machines"], stall["missing"] = max(stall["machines"], machines), min(stall["missing"], missing)
        stall["best_gap"] = min(stall["best_gap"], gap)
        # The stall budget is decision-idle game time, not tool execution: one blocking deterministic call
        # can spend most of a window on real work (live 2026-10-10: one foundation call ate ~12k of 18k).
        stall["tool_ticks"] = max(0, tool_end_tick - tool_start_tick)
        prior_tick = stall["last_tick"] if stall["last_tick"] is not None else facts["tick"]
        elapsed = max(0, after["tick"] - prior_tick)
        stall["last_tick"] = after["tick"]
        stall["idle_ticks"] += max(0, elapsed - (0 if chosen["family"] == "wait" else stall["tool_ticks"]))  # a wait is deliberate idle; a tool call is not
        if progressed:
            stall["idle_ticks"], stall["since"] = 0, 0
        else:
            stall["since"] += 1
        if stall["idle_ticks"] >= STALL_TICKS or stall["since"] >= settings.stall_decisions:
            summary["stop"] = "stalled"
            emit(f"STALLED: {stall['idle_ticks']} idle game ticks / {stall['since']} decisions without a "
                 "funded submission, work in progress or goal progress")
            break
        if summary["failures"] >= 3 and all(r.get("code") == "tool_error" for r in results[-3:]):
            summary["stop"] = "safe_stop_after_repeated_failures"
            break
    check_acceptance("end")
    if until_accepted and until_accepted in summary.get("accepted", []):
        summary["stop"] = "accepted_window_reached"
    session.close()
    # Typed outcomes, not only caught exceptions: an untyped_stuck is a tool fault even when nothing raised.
    summary["codes"] = dict(Counter(r.get("code") or "error" for r in results if not r.get("ok")))
    summary["stall"] = {"idle_ticks": stall["idle_ticks"], "decisions": stall["since"],
                        "tool_ticks": stall["tool_ticks"], "version": "decision-idle-v2",
                        "idle_tick_limit": STALL_TICKS, "decision_limit": settings.stall_decisions}
    emit(f"SUMMARY: stop={summary['stop']} decisions={summary['decisions']} failures={summary['failures']} "
         f"phases={','.join(reached) or 'none'} accepted={','.join(summary.get('accepted', [])) or 'none'}")
    return summary
