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

from orchestrator.operation_observation import economics_observation, item_service_observation

import math
import time
import dataclasses
import copy
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from orchestrator.laya_knowledge import load_guideline_context
from training.laya_clean_runner import (ROOT, Settings, _append, _goal_gap, _status_line, _training_record,
                                        _write_status, rolling_measurement)
from training.laya_saved_world import speed_status
from training.laya_mall_slots import RAW, obtainable
from training.laya_mission import current_phase, phase_reached
from training.laya_goal import (
    GOAL_VERSION, goal_evidence, observation_window, research_goal_satisfied, useful_state,
)
from training.laya_factory_status import STATUS_BUCKETS, compact_status, compile_status
from training.laya_mission import PHASES
from training.laya_phase_acceptance import LayaC6Acceptance, ops_phase_reached

PLATES = {"iron-plate": "iron-ore", "copper-plate": "copper-ore", "stone-brick": "stone"}
# The tooling is already making it (a mall batch or loan in progress, a borrow queued): wait, and it is progress.
IN_PROGRESS = {"temporary_mall_batch", "production_prerequisite_deferred", "rationed_mall_borrow_deferred",
               "rotating_mall_prerequisite_handoff", "prerequisite_advanced", "retirement_pending",
               # The ore's plate system is still being built by bots (stage_extraction.PendingSystemDeferred).
               "pending_system_construction"}
# Machine statuses of a recipe waiting on a fluid ingredient (laya_factory_status's "starved" bucket).
FLUID_STARVED = ("fluid_ingredient_shortage", "no_input_fluid")
# Receipts that put something into the world (training/laya_ops.receipt); a reservation or a wait does not.
BUILT = {"producing", "submitted", "prerequisite_advanced"}
STALL_TICKS = 18000  # five game minutes without measurable progress (loan batches are slow by design)
# Game time a run may go without a new proxy phase or strict acceptance, however busy it is. Cohort 7 part a
# (96 runs, 2.1.22): the longest gap that still ended in a new milestone was 206,803 ticks (base-s78010; the C5
# runs 144,255 and 110,392); four C0 loops went 379k-405k ticks without one. 80 minutes sits between.
# Off by default (None): the 2026-10-20 review separated it from rung progress, since an arm-specific
# stop changes the opportunity to reach C5/C6. Set it only in a variant that tests it on its own.
PHASE_BUDGET_TICKS: int | None = None
# Outcomes of the deterministic ladder step: their "rung" is the first missing predecessor, so its ladder
# index is the number of rungs already established.
LADDER_WAIT_CODES = {"chemical_capability_batch", "chemical_capability_wait", "chemical_ladder_borrow_deferred"}
WAIT_EXPIRY = 1800  # ticks after which a "waiting" operation is worth retrying  # the tooling is already making it: wait, and count it as progress


# The tooling made the item by lending another mall cell, not by standing a slot (the lent cell stops its own item).
BORROW_CODES = {"temporary_mall_batch", "rationed_mall_borrow_deferred"}


def in_progress(code: str | None) -> bool:
    return bool(code) and (code.endswith("_wait") or code in IN_PROGRESS)
WAIT_TICKS = (300, 1800)
# Bootstrap-district lifecycle states where a replacement plan has already claimed the plate's
# immutable pioneer identity: a fresh starter recomputes a different plan the tooling rejects as a
# lifecycle conflict (episode 20261010T102842Z-68b348c2). The district is reconciled, not re-opened.
ACTIVE_DISTRICT_STATES = frozenset({"provisioning", "validating", "retiring"})
# Rules for operations: the general mission rules plus mission.ops-*, quoting the same strategy doc
# (the full catalog's geometry rules -- power variants, line sites, quad slots -- do not apply here).
OPS_CATALOG = "docs/rl/laya_ops_guidelines.json"
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
            if (state.get("replacement_submitted") is True
                    and state.get("lifecycle_state") in ACTIVE_DISTRICT_STATES):
                pending.add(plate)
    return pending


def unchanged_failures(results: list[dict], stock: Mapping[str, int],
                       allocation: Mapping[str, tuple] | None = None,
                       layout: Sequence[int] | None = None) -> set[tuple]:
    """Attempts whose required stock/allocation or refused layout has not changed.

    Retrying such an operation changes nothing in the world except, in the tooling's mall path, another
    power bridge to another abandoned slot.  ``allocation`` is the current owner-attributed claim per
    item (training/laya_ops_runner.reservation_allocation); when a required item's claim moved since the
    failure was recorded, its allocation changed, so the failure is no longer unchanged.
    """
    latest: dict[tuple, dict] = {}
    current_allocation = _canonical_claims(allocation) if allocation is not None else None
    for r in results:
        params = r.get("params") or {}
        latest[(r["operation"], params.get("recipe") or params.get("item") or params.get("technology"))] = r
    stuck = set()
    for key, r in latest.items():
        if outcome_category(r) == "no_geometry":
            seen_layout = r.get("layout_at")
            moved = (layout is not None and seen_layout is not None
                     and tuple(layout) != tuple(seen_layout))
            if not moved and not _layout_changed_since(results, r):
                stuck.add(key)
            continue
        for bill in _demand_bills(r):
            seen = bill.get("stock_at", r.get("stock_at"))
            required = bill.get("required") or {}
            if seen is not None and required and all(
                    int(stock.get(item, 0)) <= int(seen.get(item, 0)) for item in required):
                if current_allocation is not None:
                    recorded = _canonical_claims(r.get("allocation_at"))
                    if any(
                        current_allocation.get(item, ()) != recorded.get(item, ())
                        for item in required
                    ):
                        continue  # the reservation owner/claim moved: worth reconsidering
                stuck.add(key)
    return stuck


def layout_signature(facts: Mapping) -> tuple[int, int] | None:
    """Built machines and poles: completed construction changes it, a queued request does not."""
    if not isinstance(facts.get("machines"), list) or not isinstance(facts.get("poles"), list):
        return None
    return len(facts["machines"]), len(facts["poles"])


def _changes_layout(result: Mapping) -> bool:
    """A successful receipt that built or removed something: a submitted plan or a shape-changing operation."""
    if (not result.get("ok") or result.get("operation") in {"wait", "research"}
            or result.get("progress") is False):
        return False
    return bool(result.get("progress")) or result.get("operation") in MAJOR


def _layout_changed_since(results: list[dict], attempt: Mapping) -> bool:
    """Whether a layout-changing receipt followed ``attempt`` (the layout it saw may have changed)."""
    last = max((i for i, r in enumerate(results) if r is attempt), default=-1)
    return any(_changes_layout(r) for r in results[last + 1:])


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


def unbuildable_cause(results: list[dict], operation: str, plate: str) -> str | None:
    """The typed cause of this operation's latest outcome when it was an unbuildable district."""
    last = next((r for r in reversed(results) if _key(r) == (operation, plate)), None)
    if last is None or last.get("ok") or last.get("code") != "district_expansion_unbuildable":
        return None
    return (last.get("details") or {}).get("cause") or "untyped"


def _demand_bills(result: Mapping):
    """Material bills reported by a failed operation, including a rejected mall claim."""
    if result.get("ok"):
        return
    details = result.get("details") or {}
    if result.get("code") == "material_shortage":
        yield details
    claim = details.get("slot_claim")
    if isinstance(claim, Mapping) and isinstance(claim.get("required"), Mapping):
        yield claim


def batch_advanced(previous: Mapping[tuple, int], current: Mapping[tuple, int]) -> bool:
    """Whether any batch seen last check has crafted since (a first sighting is not progress)."""
    return any(count > previous[key] for key, count in current.items() if key in previous)


def ladder_rungs(result: Mapping) -> int | None:
    """Chemical-ladder rungs established when this outcome waits at a rung, else None."""
    from orchestrator.baseline_production import CHEMICAL_BOOTSTRAP_LADDER
    details = result.get("details") or {}
    rung = details.get("rung") or details.get("predecessor")
    if result.get("code") not in LADDER_WAIT_CODES or rung not in CHEMICAL_BOOTSTRAP_LADDER:
        return None
    return CHEMICAL_BOOTSTRAP_LADDER.index(rung)


def shortages(results: list[dict], sources: dict | None = None) -> dict[str, int]:
    """Items the latest material shortage of each operation still asks for (``sources``: item -> plates blocked)."""
    latest: dict[tuple, dict] = {}
    for r in results:
        params = r.get("params") or {}
        latest[(r["operation"], params.get("recipe") or params.get("item"))] = r
    short: Counter = Counter()
    for r in latest.values():
        for details in _demand_bills(r):
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
        for details in _demand_bills(r):
            stock_at = details.get("stock_at", r.get("stock_at"))
            if stock_at is None:
                continue
            available = details.get("available") or {}
            for item, count in (details.get("required") or {}).items():
                missing = int(count) - int(available.get(item, 0))
                if missing > 0:
                    levels[item] = max(levels.get(item, 0), int(stock_at.get(item, 0)) + missing)
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
    for transaction in ((economics or {}).get("transactions") or {}).values():
        items.update(transaction.get("remaining") or {})
    return tuple(sorted(items))


def material_results(results: list[dict], transactions: Mapping | None) -> list[dict]:
    """A current owned remaining bill supersedes its historical shortage, never another consumer's."""
    stages = {stage for tx in (transactions or {}).values() if not tx.get("error")
              for stage in tx.get("replaces_shortage_stages", ())}
    return [r for r in results if r.get("code") != "material_shortage"
            or (r.get("details") or {}).get("stage") not in stages]


RECENT = 8  # decisions kept verbatim in the history; older ones survive only in the per-operation summary
STUCK_DECISIONS = 6  # decisions without a built operation after which Laya counts itself stuck


def _key(r: Mapping) -> tuple:
    params = r.get("params") or {}
    return (r["operation"], params.get("recipe") or params.get("item"))


MAJOR = {"starter", "foundation", "extend", "steel", "retire_starter"}  # operations that change the factory's shape
PLATE_OPERATIONS = {"starter", "foundation", "extend", "retire_starter"}
NO_PROGRESS_CAP = 5  # blocked-attempt streaks are capped so catalog rules can match exact values
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
        "CAPACITY": ("mission.rotating-mall", "mission.ops-no-idle-loans"),
        "STARTERS": ("mission.ops-starters-before-foundations",),
        "BLOCKED": ("mission.ops-history-no-repeat", "mission.ops-no-blind-retry"),
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



def mall_capacity(economics: Mapping | None, results: list[dict]) -> dict:
    """Mall capacity facts: slots, observed cells, recipes covered, and why the latest borrow was refused.

    Unknown surveys stay None. ``lendable`` counts observed non-anchor cells not already lent; it is an
    observation, not the tooling's borrowability decision, which ``borrow_blocked`` reports when it refused.
    """
    economics = economics or {}
    slots, cells, loans = economics.get("slots") or {}, economics.get("cells"), economics.get("loans")
    fact = {"slot_capacity": slots.get("capacity"), "slots_committed": slots.get("occupied"),
            "slots_free": slots.get("free"), "unlimited": slots.get("unlimited"),
            "cells": None, "anchors": None, "loaned": None, "lendable": None, "covered_recipes": None,
            "active_loans": None if loans is None else len(loans), "borrow_blocked": None}
    if cells is not None:
        fact.update(cells=len(cells), anchors=sum(bool(c.get("anchor")) for c in cells),
                    loaned=sum(bool(c.get("loaned")) for c in cells),
                    lendable=sum(not c.get("anchor") and not c.get("loaned") for c in cells),
                    covered_recipes=sorted({c["recipe"] for c in cells if c.get("recipe")}))
    last = next((r for r in reversed(results) if r["operation"] in ("produce", "mall_slot")), None)
    if last is not None and last.get("code") == "rationed_mall_borrow_deferred":
        details = last.get("details") or {}
        reason = (f"no borrowable assembler for {details.get('item') or _key(last)[1]}"
                  f" ({len(details.get('active_loans') or ())} active loan(s))")
        claim = (details.get("slot_claim") or {}).get("required")
        if isinstance(claim, Mapping) and claim:
            reason += "; a new slot is short of " + ", ".join(f"{i} {n}" for i, n in sorted(claim.items()))
        fact["borrow_blocked"] = reason
    return fact


def no_progress(attempts: list[dict]) -> int:
    """Trailing attempts that ended blocked with the latest one's unchanged outcome (nothing ok or built)."""
    count = 0
    for r in reversed(attempts):
        if r.get("ok") or r.get("code") in BUILT or _outcome(r) != _outcome(attempts[-1]):
            break
        count += 1
    return count


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
        value = lambda n: "?" if n is None else str(n)
        loans = economics.get("loans")
        sections["MALL"] = (["loans unknown (survey failed)"] if loans is None else
                            [f"{loan['lender']} cell lent to make {loan['making']} (to {loan['target']})" for loan in loans]
                            or ["no loans"])
        # Keep the old loan vocabulary while adding identity and measured stock progress.
        if loans:
            sections["MALL"] = [text + (f" owner={loan['owner']} step={loan.get('current_recipe')}"
                                       f" network={value(loan.get('network_stock'))}/{loan['target']}"
                                       f" remaining={value(loan.get('remaining_to_target'))}"
                                       f" crafts={value(loan.get('step_completed_crafts'))}/{value(loan.get('step_required_crafts'))}"
                                       f" status={value(loan.get('machine_status'))}"
                                       f" credits={loan.get('completed_step_targets', {})}"
                                       if "owner" in loan else "")
                                for text, loan in zip(sections["MALL"], loans)]
        slots = economics.get("slots")
        if slots is not None:
            sections["SLOTS"] = [f"{slots.get('scope', 'bootstrap')} capacity="
                                 + ("unlimited" if slots.get("unlimited") else value(slots.get("capacity")))
                                 + f" occupied={value(slots.get('occupied'))} free={value(slots.get('free'))}"
                                   " (committed incl ghosts; materials/borrowability separate)"]
        stock = economics.get("stock")
        if stock is not None:
            def count(field, item):
                values = stock.get(field)
                return "?" if values is None else str(values.get(item, 0))
            sections["STOCK"] = [f"{item} network={count('network', item)} reserved={count('reserved', item)}"
                                 f" unreserved={count('unreserved', item)}"
                                 for item in ("iron-plate", "copper-plate")]
        projects = economics.get("projects")
        sections["RESERVED"] = (["owners unknown"] if projects is None else
                                [f"{pid} {project['state']} missing "
                                 + (", ".join(f"{i} {n}" for i, n in project["missing"].items()) or "none")
                                 + " reserved " + (", ".join(f"{i} {n}" for i, n in project["reserved"].items()) or "none")
                                 for pid, project in projects.items()] or ["no active owners"])
        sections["BILLS"] = [f"{tid}: " + (f"unknown ({tx['error']})" if tx.get("error") else
                             "remaining " + str(tx.get("remaining", {})) + "; missing " + str(tx.get("missing", {}))
                             + ("" if "packets_pending" not in tx else "; packets_pending "
                                + value(None if tx["packets_pending"] is None else len(tx["packets_pending"]))))
                             for tid, tx in (economics.get("transactions") or {}).items()]
        capacity = economics.get("capacity")
        if capacity is not None:
            sections["CAPACITY"] = [
                f"cells={value(capacity['cells'])} anchors={value(capacity['anchors'])}"
                f" loaned={value(capacity['loaned'])} lendable={value(capacity['lendable'])}"
                f" covers {'?' if capacity['covered_recipes'] is None else ', '.join(capacity['covered_recipes']) or 'nothing'}"]
            if capacity.get("borrow_blocked"):
                sections["CAPACITY"].append(f"last borrow refused: {capacity['borrow_blocked']}")
        starters = economics.get("starters")
        if starters is not None:
            sections["STARTERS"] = [f"{name} {fact['blocker']} out={value(fact.get('output_count'))}"
                                    for name, fact in starters.items()]
    blocked = []
    for key in dict.fromkeys(_key(r) for r in results if r["operation"] != "wait"):
        attempts = [r for r in results if r["operation"] != "wait" and _key(r) == key]
        if (count := no_progress(attempts)) >= 2:
            blocked.append(f"{' '.join(str(k) for k in key if k)} {_outcome(attempts[-1])} x{count}")
    if blocked:
        sections["BLOCKED"] = blocked
    sections["SINCE LAST BUILD"] = [f"{since_build(results)} decisions"]

    def render() -> str:
        lines = []
        for name, entries in sections.items():
            tags = [rule for rule in USES.get(name, ()) if rule in rules]
            lines.append(f"{name}{' [' + ', '.join(tags) + ']' if tags else ''}: {' | '.join(entries) or 'none yet'}")
        return "\n".join(lines)
    # Over budget: drop the least recent attempts summary first, then the oldest major change and recent move.
    trim = ("TRIED", "MAJOR", "RECENT")
    while len(text := render()) > limit and any(sections.get(k) for k in trim):
        next(sections[k] for k in trim if sections.get(k)).pop(0)
    # Economic lists can grow independently of history. Mark omitted entries and
    # bound even unusually long names/bills; never silently claim complete coverage.
    for name in ("MALL", "RESERVED", "BLOCKED"):
        omitted = 0
        while len(text := render()) > limit and len(sections.get(name, [])) > 1:
            sections[name].pop()
            omitted += 1
        if omitted:
            sections[name].append(f"+{omitted} omitted")
    text = render()
    if len(text) > limit:
        marker = " … [brief truncated]"
        return (text[:max(0, limit - len(marker))] + marker)[:max(0, limit)]
    return text


def with_economics(menu: dict, results: list[dict], economics: Mapping | None,
                   *, stock: Mapping[str, int] | None = None, reconcile_loans: bool = False) -> dict:
    """Attach what each option is known to cost and displace (audit finding 3).

    ``bill`` and ``last_attempt_missing_items`` preserve the last reported shortage.
    ``missing_items``/``inputs_missing`` describe current availability only when the
    attempt's reported project, current network stock, and active reservations make
    that calculation knowable. Otherwise they are None. ``reservation_owner`` names
    only the reported claimant. ``demand_point``/``demand_distance`` relay the shared
    demand-siting fact. ``cell_lent`` means the item's own mall cell is lent elsewhere.
    """
    transaction_bills = {}
    for r in material_results(results, (economics or {}).get("transactions")):
        for demand in _demand_bills(r):
            required = demand.get("required")
            if isinstance(required, Mapping):
                transaction_bills[_key(r)] = {i: int(n) for i, n in required.items()}
    from orchestrator.mall_control import raise_options
    additions = raise_options(economics)
    if reconcile_loans:
        from orchestrator.mall_reconciliation import options as reconciliation_options
        additions += reconciliation_options(economics)
    menu["options"].extend(additions)
    for option in additions:
        need = next((n for n in menu["needs"] if n["need_id"] == option["need_id"]), None)
        if need is None:
            need = {"need_id": option["need_id"], "condition": "available", "options": []}
            menu["needs"].append(need)
        need["options"].append(option["option_id"])
    loans = (economics or {}).get("loans")
    lent = {loan["lender"] for loan in loans or ()}
    capacity = (economics or {}).get("capacity") or {}
    starters = (economics or {}).get("starters")
    bills = shorts_of(results)
    loans_by_making = {loan.get("making"): loan for loan in loans or ()}
    projects = (economics or {}).get("projects")
    demand = (economics or {}).get("demand") or {}
    for option in menu["options"]:
        if option["family"] in PLATE_OPERATIONS and option.get("product") in PLATES:
            starter = None if starters is None else starters.get(PLATES[option["product"]].removesuffix("-ore"))
            option["features"]["starter_blocker"] = None if starter is None else starter["blocker"]
        if option["family"] == "wait":
            continue
        if option["family"] == "raise_stock_gate":
            option["bill"] = {}
            option["features"]["bill_known"] = True
            continue
        if option["family"] == "reconcile_mall_loan":
            option["features"]["bill_known"] = True  # Existing-cell transaction, not a construction bill.
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
            # Generic batch facts: the item's own active loan, when it has one.
            # Unknown (no loan / failed survey) stays None, never "unchanged".
            loan = loans_by_making.get(option["product"])
            option["features"]["batch_status"] = (loan or {}).get("status")
            option["features"]["batch_step"] = (loan or {}).get("step_recipe")
            option["features"]["batch_crafts"] = (loan or {}).get("crafts_done")
            option["features"]["batch_remaining_bill"] = (loan or {}).get("remaining_bill")
            option["features"]["batch_missing_inputs"] = (loan or {}).get("missing_inputs")
        bill = transaction_bills.get(key)
        for tid, transaction in ((economics or {}).get("transactions") or {}).items():
            if (not transaction.get("error") and "remaining" in transaction
                    and option["family"] == "produce" and params.get("item") == transaction.get("target_item")):
                bill = transaction["remaining"]
                option["bill_owner"] = tid
        if bill is not None:
            option["bill"] = bill
            option["features"]["bill_known"] = True
        if option["family"] in ("produce", "mall_slot"):
            option["features"]["borrow_blocked"] = bool(capacity.get("borrow_blocked")) if capacity else None
            option["features"]["lendable_cells"] = capacity.get("lendable")
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
                    growable: tuple[str, ...] = (), enabled_recipes: Mapping | None = None,
                    transactions: Mapping | None = None, foundations: Mapping | None = None, *,
                    bootstrap_districts: Mapping | None = None, allocation: Mapping[str, tuple] | None = None,
                    measurement: Mapping | None = None, research_goal: str | None = None,
                    goal_status: Mapping | None = None, labs: int | None = None,
                    layout: Sequence[int] | None = None) -> dict:
    """Operations worth offering now, each tagged with the need it serves and the facts rules weigh.

    ``foundations`` (plate -> observed ready) advances a plate past "starter" when the world shows its foundation
    delivering, even if the foundation operation itself last returned a wait.
    """
    unmet = {n["item"] for n in board["needs"] if n["condition"] != "satisfied"}
    goal = {n["item"] for n in board["needs"] if (n.get("goal_required_per_s") or 0) > 0}
    goal_rate = {n["item"]: float(n.get("goal_required_per_s") or 0) for n in board["needs"]}
    pending = pending_retirements(results, bootstrap_districts)
    current_materials = material_results(results, transactions)
    stuck = unchanged_failures(current_materials, stock or {}, allocation, layout)
    sources: dict[str, set] = {}
    stages, codes, short = plate_stages(results), last_codes(results, tick), shortages(current_materials, sources)
    for plate, ready in (foundations or {}).items():
        if ready is True and stages.get(plate) == "starter":
            stages[plate] = "foundation"
            if not retired(results, plate):
                pending.add(plate)
    prerequisites = named_prerequisites(results)
    for item, demand in prerequisites.items():
        sources.setdefault(item, set()).update(parent.split(":", 1)[1] for parent in demand["parents"])
    for tx in (transactions or {}).values():
        if not tx.get("error") and tx.get("target_item"):
            for item in tx.get("missing", {}):
                sources.setdefault(item, set()).add(tx["target_item"])
    # A bill item or named prerequisite serves the same goals as the consumer it unblocks.
    # This is dependency evidence for the existing rules, not a new recipe priority or policy weight.
    changed = True
    while changed:
        changed = False
        for parents in sources.values():
            before = len(parents)
            parents.update(*(sources.get(parent, set()) for parent in tuple(parents)))
            changed |= len(parents) != before
    waiting = any(in_progress(c) for c in codes.values())
    # A mall batch or loan is under way: asking for other items makes the loans preempt each other.
    batch_active = any(in_progress(code) for (operation, _), code in codes.items() if operation in ("produce", "mall_slot"))
    options = []
    # A foundation reserves its whole bill when placed (earmarked), including the drills a starter needs, so
    # foundations wait until the iron and copper starters stand (docs/deterministic/planning.md: startup builds
    # the starters before any mine-to-refinery foundation).
    starters_done = {"iron-plate", "copper-plate"} <= {p for p, s in stages.items() if s != "none"}
    for plate in PLATES:
        district_state = ((bootstrap_districts or {}).get(plate) or {}).get("lifecycle_state")
        # New capacity follows demand; finishing owned work survives rate/phase changes.
        legal = ()
        if plate in unmet or PLATES[plate] in unmet:
            legal = {"none": ("starter",), "starter": ("foundation",) if starters_done else (),
                     "foundation": ("extend",)}[stages.get(plate, "none")]
        # A district whose replacement is already reserved/validating/retiring has claimed this
        # plate's pioneer identity; a fresh starter would recompute a plan the tooling rejects as
        # a lifecycle conflict. Skip it and let the policy reconcile or serve other work instead.
        if district_state in ACTIVE_DISTRICT_STATES:
            legal = tuple(operation for operation in legal if operation != "starter")
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
                             "lifecycle_state": district_state,
                             # The tooling said it waits on bots or coverage: retry after a wait, not at once.
                             "waiting": in_progress(codes.get((operation, plate))),
                             "unchanged_failure": (operation, plate) in stuck}})
        for option in options:
            if option["params"].get("recipe") == plate:
                cause = unbuildable_cause(results, option["family"], plate)
                if cause is not None:
                    option["features"]["unbuildable_cause"] = cause
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
        growth = board.get("mall_growth") or {}
        options.append({
            "option_id": f"op.mall_slot.{item}", "family": "mall_slot", "product": item, "need_id": f"stock:{item}",
            "summary": f"mall slot for {item}", "params": {"item": item},
            "features": {"last_code": codes.get(("mall_slot", item), "new"),
                         "waiting": in_progress(codes.get(("mall_slot", item))),
                         "unchanged_failure": ("mall_slot", item) in stuck, "batch_active": batch_active,
                         "shortage": short.get(item, 0),
                         # Demand/capacity facts Laya weighs when choosing growth, in the
                         # same shape the deterministic admission uses.
                         "plate_surplus_per_s": growth.get("plate_surplus_per_s"),
                         "seed_ready": growth.get("seed_ready"),
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
    # ``produce`` runs ``ensure_produced``, which needs an assembler or dedicated cell.  A raw
    # resource (ore, stone, coal, wood) has neither -- it is mined by a starter/mine/foundation,
    # never assembled -- so it is never a produce target even when goal_demand lists it as a
    # dependency (episode 20261010T101241Z-a476f94f offered op.produce.iron-ore, which raised
    # untyped_stuck at decision 0).
    producible = {i: m for i, m in short.items() if i not in RAW and plates_in(i, recipes or {}) <= started
                  and obtainable(recipes, i, stock or {})} if recipes else short
    core = set(baseline_build_order())
    for item in sorted(producible):
        if item not in RAW and item not in core and codes.get(("mall_slot", item)) != "ok" and plates_in(item, recipes or {}):
            mall_slot(item)
    levels = shortage_levels(current_materials)
    # A small construction blocker is still a legal choice beside larger unrelated bills.
    demands = {item: {"target": levels.get(item, int((stock or {}).get(item, 0)) + missing),
                      "missing": missing, "capability": False, "parents": []}
               for item, missing in (sorted(producible.items(), key=lambda kv: -kv[1]) if slots else ())}
    # Typed prerequisites remain selectable alongside construction shortages.
    # They can establish upstream capability that obtainable() cannot yet prove (e.g. plastic before oil).
    for item, demand in prerequisites.items():
        if item in PLATES or not plates_in(item, recipes or {}) <= started:
            continue
        if enabled_recipes is not None and not enabled_recipes.get(item):
            continue
        existing = demands.setdefault(item, {**demand, "missing": 0})
        existing.update(target=max(existing["target"], demand["target"]),
                        capability=demand["capability"], parents=demand["parents"])
    # A persisted selected bill supplies current demand before another operation has to fail.
    # Every missing bill item is a choice; neither proxy phase nor larger unrelated shortages hide it.
    for tid, transaction in (transactions or {}).items():
        if transaction.get("error") or transaction.get("complete") or not slots:
            continue
        for item, missing in (transaction.get("missing") or {}).items():
            if (item in PLATES or not plates_in(item, recipes or {}) <= started
                    or (recipes and not obtainable(recipes, item, stock or {}))
                    or (enabled_recipes is not None and not enabled_recipes.get(item))):
                continue
            target = transaction["stock_levels"][item]
            demand = demands.setdefault(item, {"target": target, "missing": missing, "capability": False,
                                                "parents": []})
            demand["target"] = max(demand["target"], target)
            demand["missing"] = max(demand["missing"], missing)
            demand.setdefault("transactions", []).append(tid)
    # Unreleased packets of each selected chemical transaction, by its output item (None: survey unreadable).
    packets_pending = {tx["target_item"]: None if tx["packets_pending"] is None else len(tx["packets_pending"])
                       for tx in (transactions or {}).values()
                       if not tx.get("error") and tx.get("target_item") and "packets_pending" in tx}
    for need in board["needs"]:
        item = need["item"]
        # Built but starved of a fluid, or its packets not all released: capability is still missing upstream
        # (cohort 7: plastic machines stood, petroleum never arrived, and the menu went wait-only).
        starved = need["condition"] == "input_starved" and (
            any((need.get("status_counts") or {}).get(status) for status in FLUID_STARVED)
            or packets_pending.get(item))
        if (item in goal and item not in PLATES and (need["condition"] in {"unbuilt", "recipe_unavailable"} or starved)
                and ((recipes or {}) if enabled_recipes is None else enabled_recipes).get(item)
                and plates_in(item, recipes or {}) <= started):
            demand = demands.setdefault(item, {"target": 1, "missing": 0, "parents": []})
            demand["capability"] = True
    if recipes:
        for item in sorted(goal):
            if item in PLATES or item in RAW or codes.get(("produce", item)) == "ok":
                continue
            if item not in demands:
                target = max(1, math.ceil(goal_rate.get(item, 0) * 60))
                demands[item] = {"target": target, "missing": target, "capability": True, "parents": []}
    for item, demand in demands.items():
        if item in RAW:
            continue
        # The tooling's stock_target is a network level (the mall's stock gate), not a delta: asking for the
        # bare shortage gated a drill cell at 4 while the network held 6 and the starter needed 8.
        have = int((stock or {}).get(item, 0))
        target, missing = demand["target"], demand["missing"]
        if have >= target and not demand["capability"] and not demand.get("transactions"):
            continue  # Made since the failure: retrying the blocked operation is the next step, not more stock.
        options.append({
            "option_id": f"op.produce.{item}", "family": "produce", "product": item, "need_id": f"stock:{item}",
            "summary": f"produce {item} (short {missing})", "params": {"item": item, "stock_target": target,
                                                                          **({"finite": False} if demand["capability"] else {})},
            # Items a goal plate's operation is blocked on serve that goal.
            "features": {"last_code": codes.get(("produce", item), "new"), "shortage": missing,
                         "prerequisite_for": sorted(demand["parents"]),
                         "transactions": sorted(demand.get("transactions", [])),
                         "waiting": in_progress(codes.get(("produce", item))),
                         "unchanged_failure": ("produce", item) in stuck, "batch_active": batch_active,
                         "goal_need": item in goal or bool(sources.get(item, set()) & goal),
                         **({"packets_pending": packets_pending[item]} if item in packets_pending else {})}})
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
    for option in options:  # Laya's own recent decisions, per option and per need
        params = option["params"]
        key = (option["family"], params.get("recipe") or params.get("item"))
        option["features"].update(history_features(results, key, option["family"]))
        mine = [r for r in results if r["operation"] != "wait" and _key(r) == key]
        served = [r for r in results if r["operation"] != "wait" and r.get("need_id") == option["need_id"]]
        # Capped like repeat_fails so catalog rules can match exact values; the brief shows the full count.
        option["features"].update(no_progress=min(no_progress(mine), NO_PROGRESS_CAP) if mine else 0,
                                  need_no_progress=min(no_progress(served), NO_PROGRESS_CAP) if served else 0)
    # Research and lab options for a goal run, authorized by the live research prerequisites: the tooling
    # only accepts a technology the force has enabled. A blind queue (unknown status) is not offered.
    if research_goal:
        technology = (goal_status or {}).get("technology") if isinstance(goal_status, Mapping) else None
        technology = technology if isinstance(technology, Mapping) else {}
        state = technology.get("state")
        candidate = research_goal if state in ("available", "current") else None
        if (state == "future" and technology.get("enabled") is True
                and isinstance(technology.get("current_level"), int)
                and isinstance(technology.get("requested_level"), int)
                and technology["current_level"] < technology["requested_level"]):
            candidate = technology.get("canonical_name")
        if candidate and not research_goal_satisfied(goal_status):
            options.append({
                "option_id": f"op.research.{candidate}", "family": "research", "product": candidate,
                "need_id": f"research:{candidate}", "summary": f"research {candidate} toward {research_goal}",
                "params": {"technology": candidate},
                "features": {"last_code": codes.get(("research", candidate), "new"),
                             "waiting": in_progress(codes.get(("research", candidate))), "goal_need": True}})
        # Provision a lab through the shared toolkit when the live fact says none exists yet.
        if labs == 0 and codes.get(("produce", "lab")) != "ok":
            options.append({
                "option_id": "op.produce.lab", "family": "produce", "product": "lab", "need_id": "stock:lab",
                "summary": "produce lab", "params": {"item": "lab", "stock_target": 1},
                "features": {"last_code": codes.get(("produce", "lab"), "new"),
                             "waiting": in_progress(codes.get(("produce", "lab"))),
                             "unchanged_failure": ("produce", "lab") in stuck, "batch_active": batch_active,
                             "goal_need": True}})
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


def _lab_count(facts: Mapping | None) -> int | None:
    """Observed lab count from the live facts; ``None`` when the observation cannot say."""
    facts = facts if isinstance(facts, Mapping) else {}
    counts = facts.get("infrastructure_counts")
    if isinstance(counts, Mapping):
        return int(counts.get("lab", 0))
    machines = facts.get("machines")
    if isinstance(machines, list):
        return sum(1 for machine in machines if isinstance(machine, Mapping) and machine.get("name") == "lab")
    labs = facts.get("labs")
    return labs if isinstance(labs, int) else None


def _goal_status(session: Any, technology: str) -> Mapping | None:
    """Read the goal's live research state; an unsupported or failed read is unknown, never success."""
    reader = getattr(session, "goal_status", None)
    if not callable(reader):
        return None
    try:
        return reader(technology)
    except Exception as error:  # noqa: BLE001 -- observation must never stop the run
        return {"error": f"{type(error).__name__}: {error}"[:200]}


def _live_tick(adapter, fallback: int) -> int:
    """Read the execution boundary without taking a full factory snapshot."""
    tick = getattr(adapter, "tick", None)
    try:
        return int(tick() if callable(tick) else tick) if tick is not None else fallback
    except (TypeError, ValueError, RuntimeError):
        return fallback


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
        carried: list[dict] = (), recovery: dict | None = None,
        checkpoint_dir: Path | None = None, checkpoint_ticks: int = 54000,
        recovery_wall_seconds: float = 0, until_accepted: str | None = None, goal: str | None = None) -> dict:
    """Observe -> board -> operations -> choose -> run one -> record, until the mission ends or stalls.

    ``carried`` seeds the history with an earlier segment's lifecycle receipts when a fresh controller is
    restarted on the same world; everything else starts fresh.
    """
    from training.laya_ops import DeterministicSession
    goal = goal or settings.goal_technology
    if recovery:
        terminal = recovery.get("terminal", {})
        if ((goal is not None and goal != terminal.get("goal"))
                or (until_accepted is not None and until_accepted != terminal.get("until_accepted"))):
            raise ValueError("ops recovery terminal objective differs")
        goal, until_accepted = terminal.get("goal"), terminal.get("until_accepted")
    if goal and settings.research_target:
        raise ValueError("choose one research objective: goal or measured research target")
    journal.parent.mkdir(parents=True, exist_ok=True)
    active_started = time.monotonic()
    if recovery:
        recovery = copy.deepcopy(recovery)
    if recovery and carried:
        raise ValueError("exact ops recovery cannot be mixed with jumpstart receipts")
    if checkpoint_ticks < 300:
        raise ValueError("checkpoint interval must be >=300 ticks")
    if checkpoint_dir is not None and (not settings.max_game_ticks or not settings.max_wall_seconds
                                     or settings.max_wall_seconds <= 0):
        raise ValueError("ops checkpoint requires finite cumulative game/wall budgets")
    if recovery and (recovery["settings"] != dataclasses.asdict(settings) or recovery["run_id"] != run_id
                     or recovery["decision_limit"] != decisions):
        raise ValueError("ops recovery settings/logical run mismatch")
    session = session or DeterministicSession(adapter.root, emit=lambda line: emit(f"  TOOL {line}"),
                                              logical_episode_id=recovery["session"]["episode_id"] if recovery else None)
    session.stock_networks = lambda: adapter.query().get("networks")
    records = journal.parent / "decision-states.jsonl"
    reached: list[str] = []
    results: list[dict] = list(carried)
    if carried:
        emit(f"JUMPSTART: restarting on the same world with {len(carried)} lifecycle receipt(s) from earlier "
             f"segments: {', '.join(sorted({r['operation'] for r in carried}))}")
    samples: list = []
    measurement = None
    summary: dict = {"decisions": 0, "failures": 0, "phases_reached": reached, "stop": "decision_budget",
                     "objective_version": (GOAL_VERSION if goal else "laya-research-consumption-v1"
                                           if settings.research_target else "legacy-science-readiness-v1")}
    stall = {"since": 0, "best_gap": math.inf, "tick": None, "machines": 0, "missing": math.inf, "made": {},
             "batches": {}, "installed": {}, "network": None, "rungs": None, "mark": None, "mark_tick": None}
    stall.update(idle_ticks=0, tool_ticks=0, last_tick=None, last_state=None, changed_tick=None)
    c6 = LayaC6Acceptance(run_id)
    from training.laya_research import ResearchObjective, SUPPORT, add_research_options
    from training.laya_mission import research_phase
    from orchestrator.research_operations import collect_research_reports, research_observation
    objective = (ResearchObjective(settings.research_target, progressive=settings.research_progressive,
                                  sustain_ticks=settings.research_sustain_ticks,
                                  state=recovery.get("research") if recovery else None) if settings.research_target else None)
    research = {}
    summary["laya_acceptance"] = {"C6": c6.snapshot()}

    def record_c6() -> None:
        evidence = c6.snapshot()
        if evidence != summary["laya_acceptance"]["C6"]:
            summary["laya_acceptance"]["C6"] = evidence
            _append(journal, {"run_id": run_id, "event": "laya_acceptance", "phase": "C6", **evidence})

    accept = getattr(session, "acceptance", None)
    acceptance_samples = int(recovery.get("acceptance_samples", 0)) if recovery else 0
    last_phase = PHASES[-1] if recovery and recovery.get("reached") else None
    goal_status = None

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
        summary["accepted_ever"] = list(summary["accepted"])
        # Final means passing on the latest sample (stateless predicates), not the latched history; a session
        # without fresh statuses reports unknown rather than echoing the latch.
        fresh = getattr(session, "acceptance_fresh", None)
        summary["accepted_final"] = (None if "error" in statuses or fresh is None
                                     else sorted(c for c, status in fresh.items() if status == "passed"))
        _append(journal, {"run_id": run_id, "event": "acceptance", "at": event, "statuses": statuses,
                          "details_path": reference, "fresh": fresh,
                          "tick": getattr(session, "acceptance_tick", None),
                          "evidence": getattr(session, "acceptance_evidence", None),
                          "checks": getattr(session, "acceptance_checks", None),
                          "production_service": summary.get("production_service"),
                          "foundations": getattr(session, "foundation_observations", None)})
    if not recovery:
        check_acceptance("start")
        _append(journal, {"run_id": run_id, "event": "laya_acceptance", "phase": "C6", **c6.snapshot()})
    previous_service_facts = None
    index = -1
    elapsed_before = recovery_wall_seconds
    last_checkpoint = None
    if recovery:
        from training.laya_ops_checkpoint import restore_session
        if recovery["policy"] != {"class": type(policy).__name__, "epsilon": getattr(policy, "epsilon", None),
                                  "preferences": getattr(policy, "preferences", None)}:
            raise ValueError("ops recovery policy differs")
        restore_session(session, recovery["session"])
        policy.rng.setstate(recovery["rng"])
        reached[:] = recovery["reached"]
        results, samples, measurement, summary, stall = (recovery[k] for k in ("results", "samples", "measurement", "summary", "stall"))
        summary["phases_reached"] = reached
        c6.evidence = recovery["c6"]
        if c6.evidence.get("run_id") != run_id:
            raise ValueError("C6 recovery belongs to a different logical run")
        previous_service_facts = recovery["previous_service_facts"]
        index = summary["decisions"] - 1
        elapsed_before += recovery["elapsed_wall"]
        last_checkpoint = recovery["checkpoint_tick"]
        _append(journal, {"run_id": run_id, "event": "ops_recovery", "index": index,
                          "cumulative_wall_seconds": elapsed_before})

    def save_checkpoint(tick, *, final=False):
        nonlocal last_checkpoint
        if checkpoint_dir is None or (not final and last_checkpoint is not None and tick - last_checkpoint < checkpoint_ticks):
            return
        from training.laya_ops_checkpoint import capture
        controller = {"run_id": run_id, "seed": seed, "settings": dataclasses.asdict(settings), "decision_limit": decisions, "reached": reached,
                      "terminal": {"goal": goal, "until_accepted": until_accepted},
                      "acceptance_samples": acceptance_samples,
                      "results": results, "samples": samples, "measurement": measurement, "summary": summary,
                      "stall": stall, "c6": c6.snapshot(), "previous_service_facts": previous_service_facts,
                      "research": objective.snapshot() if objective else None,
                      "elapsed_wall": elapsed_before + time.monotonic() - active_started,
                      "checkpoint_tick": tick, "extra_recipes": tuple(adapter.extra_recipes)}
        destination = Path(checkpoint_dir) / f"tick-{tick}-decision-{summary['decisions']}"
        if destination.exists():
            return
        meta = capture(adapter, policy, session, destination, controller)
        last_checkpoint = meta["tick"]
        summary["latest_checkpoint"] = {"path": str(destination), "tick": meta["tick"],
                                         "capture_seconds": meta["capture_seconds"]}

    while decisions is None or index + 1 < decisions:
        index += 1
        if until_accepted and until_accepted in summary.get("accepted", []):
            summary["stop"] = "accepted_window_reached"
            break
        if should_stop():
            summary["stop"] = "stopped"
            break
        if settings.max_wall_seconds and elapsed_before + time.monotonic() - active_started >= settings.max_wall_seconds:
            summary["stop"] = "wall_budget"
            break
        economics = session.economics() if hasattr(session, "economics") else None
        adapter.extra_recipes = recipe_demand(results, economics)
        reports = None
        if objective:
            reports = collect_research_reports(session.bridge, surface="nauvis", force="player", requested=settings.research_target)
            packs = ((reports.get("research") or {}).get("technology") or {}).get("science_packs", {})
            adapter.extra_recipes = (*adapter.extra_recipes, *packs, "lab")
        facts = adapter.query()
        if objective:
            research = research_observation(reports, tick=facts["tick"], surface="nauvis", force="player", requested=settings.research_target)
            support_board = compile_status(facts, objective.targets(research), measurement) if objective.targets(research) else None
            service_ok = bool(support_board and all(n["condition"] == "satisfied" for n in support_board["needs"]
                                                   if n["item"] in SUPPORT))
            evidence = objective.observe(research, facts, service_ok=service_ok,
                                         clean=not results or results[-1].get("classification") not in {"bug", "safety"})
            summary["research_acceptance"] = evidence
            if evidence["earned"]:
                if "R1" not in reached:
                    reached.append("R1")
                    summary.setdefault("phase_ticks", {})["R1"] = facts["tick"] - samples[0][0]
                    _append(journal, {"run_id": run_id, "event": "phase_reached", "phase": "R1",
                                      "tick": facts["tick"], "acceptance_version": evidence["version"]})
                _append(journal, {"run_id": run_id, "event": "research_acceptance", **evidence})
                emit(f"RESEARCH COMPLETE: {settings.research_target} at tick {facts['tick']}")
                summary["stop"] = "research_complete"
                break
        summary["production_service"] = item_service_observation(facts, previous_service_facts, "plastic-bar")
        previous_service_facts = facts
        c6.observe(facts)
        record_c6()
        samples.append((facts["tick"], dict(facts["production"]), dict(facts["consumption"])))
        measurement = rolling_measurement(samples, settings.rate_window_ticks) or measurement
        if settings.max_game_ticks and facts["tick"] - samples[0][0] >= settings.max_game_ticks:
            summary["stop"] = "window_timeout"
            break
        # The verified goal is a completed research target, never a rate proxy: check live research first.
        if goal:
            goal_status = _goal_status(session, goal)
            if research_goal_satisfied(goal_status):
                summary["stop"] = "goal_completed"
                summary["goal"] = goal_evidence(goal_status)
                emit(f"GOAL COMPLETE: {goal} verified target_completed")
                break
        get_phase = lambda: research_phase(reached, objective.targets(research)) if objective else current_phase(reached)
        phase = get_phase()
        board = compile_status(facts, phase["targets"], measurement) if phase else None
        while phase and phase["phase_id"] != "R1" and ops_phase_reached(phase, board, c6.snapshot()):
            last_phase = phase
            reached.append(phase["phase_id"])
            summary.setdefault("phase_ticks", {})[phase["phase_id"]] = facts["tick"] - samples[0][0]
            _append(journal, {"run_id": run_id, "event": "phase_reached", "phase": phase["phase_id"], "tick": facts["tick"],
                              **({"acceptance_version": c6.snapshot()["version"]} if phase["phase_id"] == "C6" else {})})
            emit(f"PHASE REACHED: {phase['phase_id']} {phase['name']} at tick {facts['tick']} ({'Laya choice/output' if phase['phase_id'] == 'C6' else 'rate proxy'})")
            check_acceptance(f"proxy {phase['phase_id']}")
            emit(f"ACCEPTANCE: {summary.get('acceptance')}")
            stall.update(since=0, best_gap=math.inf)
            phase = get_phase()
            board = compile_status(facts, phase["targets"], measurement) if phase else None
        if phase is not None:
            last_phase = phase
        if until_accepted and until_accepted in summary.get("accepted", []):
            summary["stop"] = "accepted_window_reached"
            break
        if phase is None:
            if not goal:
                summary["stop"] = "proxy_mission_complete_without_acceptance" if until_accepted else "mission_complete"
                break
            # A goal run keeps the final rate scope as an observation surface; the ladder is not the goal.
            if last_phase is None:
                summary["stop"] = "mission_complete"
                break
            phase = last_phase
            board = compile_status(facts, phase["targets"], measurement)
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
        economics = economics_observation(economics, facts.get("networks"))
        economics["capacity"] = mall_capacity(economics, results)
        from orchestrator.operation_observation import assembler_utilization
        economics["utilization"] = assembler_utilization(facts, economics)
        economics["starters"] = getattr(session, "starter_facts", None)
        menu = with_economics(compile_options(board, results, facts["tick"], facts.get("recipes"), stock,
                                              facts.get("machines"), getattr(session, "growable", ()),
                                              enabled_recipes=facts.get("enabled_recipes"),
                                              transactions=(economics or {}).get("transactions"),
                                              foundations=getattr(session, "foundation_facts", None),
                                              bootstrap_districts=districts, allocation=allocation,
                                              measurement=measurement, research_goal=goal,
                                              goal_status=goal_status, labs=_lab_count(facts),
                                              layout=layout_signature(facts)),
                              results, economics, stock=stock, reconcile_loans=settings.mall_loan_reconciliation)
        if objective:
            menu = add_research_options(menu, board, research, facts, objective)
        from training.laya_decision_pacing import apply_waits, multiplier
        elapsed_game_ticks = facts["tick"] - samples[0][0]
        menu = apply_waits(menu, settings, elapsed_game_ticks)
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
                                        "mall_economics": economics,
                                        "transactions": (economics or {}).get("transactions", {}),
                                        "saved_world": {"provider_stock": {"network": stock},
                                                        "source_networks": facts.get("networks", {}),
                                                        "reserved_stock": {"network": dict(reserved)},
                                                        "bootstrap_districts": districts}},
                 "guidelines": rules}
        if objective:
            state["observation_digest"]["research"] = {"observation": research, "objective": objective.brief()}
        if settings.decision_wait_schedule != "off":
            state["observation_digest"]["decision_pacing"] = {
                "schedule": settings.decision_wait_schedule, "clock": "elapsed_game_ticks",
                "elapsed_ticks": elapsed_game_ticks, "wait_multiplier": multiplier(settings, elapsed_game_ticks),
                "maximum_multiplier": settings.decision_wait_max_multiplier,
                "minimum_wait_ticks": settings.decision_wait_min_ticks}
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
        # Fresh baseline after selection, immediately before execution; never reuse a rate-window baseline.
        try:
            choice_facts = adapter.query() if c6.explicit_choice(chosen) else facts
        except Exception:  # Extra instrumentation failure must not prevent the selected operation.
            choice_facts = {}
        c6.choose(chosen, choice_facts, index=index)
        record_c6()
        if chosen["family"] == "wait":
            adapter.advance(chosen["wait_ticks"])
            result = {"ok": True, "operation": "wait", "params": {"ticks": chosen["wait_ticks"]}}
            if "base_wait_ticks" in chosen:
                result["params"]["base_ticks"] = chosen["base_wait_ticks"]
        else:
            try:
                result = session.run(chosen["family"], **chosen["params"])
            except Exception as error:  # A tooling bug is recorded and the run continues; three in a row stop it.
                result = {"ok": False, "operation": chosen["family"], "params": chosen["params"],
                          "code": "tool_error", "classification": "bug", "state": "failed",
                          "message": f"{type(error).__name__}: {error}"[:400]}
                summary["failures"] += 1
        result["tick"] = facts["tick"]
        result["need_id"] = chosen["need_id"]
        if result.get("code") == "material_shortage":  # Preserve known snapshots for retry comparison.
            required = (result.get("details") or {}).get("required") or {}
            result["stock_at"] = ({item: stock.get(item, 0) for item in required}
                                   if economics is not None else None)
            result["allocation_at"] = ({item: allocation.get(item, ()) for item in required}
                                       if allocation is not None else None)
        if outcome_category(result) == "no_geometry":
            signature = layout_signature(facts)
            result["layout_at"] = list(signature) if signature is not None else None
        results.append(result)
        tool_end_tick = _live_tick(adapter, tool_start_tick)
        after = adapter.query()
        if c6.explicit_choice(chosen):
            c6.outcome(result)
        c6.observe(after)
        record_c6()
        after_transactions = session.material_transactions() if hasattr(session, "material_transactions") else {}
        emit(f"  OUTCOME: {'ok' if result.get('ok') else result.get('code')}"
             + (f" -- {result.get('message', '')[:200]}" if not result.get("ok") else ""))
        _training_record(records, run_id=run_id, index=index, tick=facts["tick"], phase=phase["phase_id"],
                         state=state, options=[{k: o.get(k) for k in ("option_id", "family", "need_id", "product",
                                                                      "params", "features", "bill", "bill_owner",
                                                                      "wait_ticks", "base_wait_ticks")} for o in menu["options"]],
                         distribution=choice["distribution"], choice=choice,
                         outcome={**{k: result.get(k) for k in ("ok", "code", "classification", "state", "details",
                                                                 "message", "result")},
                                  **({'configuration_receipt': {
                                      k: result[k] for k in ('submitted', 'submission_attempted', 'verified',
                                                            'observed', 'error') if k in result}}
                                     if chosen['family'] == 'raise_stock_gate' else {}),
                                  **({'reconciliation_receipt': {
                                      k: result[k] for k in ('attempted', 'configuration_changed', 'observed', 'error')
                                      if k in result}} if chosen['family'] == 'reconcile_mall_loan' else {}),
                                  "progress": bool(result.get("progress")),
                                  "ticks": after["tick"] - facts["tick"], "material_transactions": after_transactions})
        summary["decisions"] = index + 1
        wall_elapsed = elapsed_before + time.monotonic() - active_started
        game_elapsed = after["tick"] - samples[0][0]
        summary["decision_timing"] = {
            "wall_seconds": wall_elapsed, "game_ticks": game_elapsed,
            "decisions_per_wall_second": summary["decisions"] / wall_elapsed if wall_elapsed > 0 else None,
            "decisions_per_game_second": summary["decisions"] * 60 / game_elapsed if game_elapsed > 0 else None,
            "wait_schedule": settings.decision_wait_schedule,
            "wait_multiplier": multiplier(settings, game_elapsed)}
        summary["mall_utilization"] = economics["utilization"]
        _write_status(status_path, {"run_id": run_id, "index": index, "tick": facts["tick"], "phase": phase,
                                    "research_acceptance": objective.brief() if objective else None,
                                    "reached": reached, "laya_acceptance": summary["laya_acceptance"],
                                    "production_service": summary.get("production_service"), "board": compact, "brief": state["brief"], "choice": {**choice, "summary": chosen["summary"]},
                                    "menu": {o["option_id"]: o["summary"] for o in menu["options"]},
                                    "mall_utilization": summary["mall_utilization"],
                                    "decision_timing": summary["decision_timing"],
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
        finite_credits = []
        if objective:
            from training.laya_research import finite_material_progress
            finite_credits = finite_material_progress(stall.setdefault("demand_credit", {}), results,
                                                      after_transactions, after.get("production") or {})
            crafted = bool(finite_credits)
        stall["made"] = {**stall["made"], **made}
        # An active finite batch whose craft counter rose is progress the shortage map cannot see (internal
        # chemical-ladder batches); a first sighting or an unchanged counter is not.
        batches = (session.active_batch_crafts(**({"bounded": True} if objective else {}))
                   if hasattr(session, "active_batch_crafts") else {})
        advancing = batch_advanced(stall["batches"], batches)
        stall["batches"] = batches
        installed = {(tid, tx["intent_sha256"]): sum(tx["installed"].values())
                     for tid, tx in after_transactions.items() if not tx.get("error") and "installed" in tx}
        delivered = batch_advanced(stall["installed"], installed)
        # Keep a high-water mark per immutable intent: demolition/rebuild cycles cannot reset the guard.
        for key, count in installed.items():
            stall["installed"][key] = max(stall["installed"].get(key, 0), count)
        # Poles and roboports the tooling chains out to a distant site (outside the facts' bounds) are
        # construction progress; a high-water mark keeps demolition/rebuild from counting twice.
        network = session.built_network() if hasattr(session, "built_network") else None
        extended = network is not None and stall["network"] is not None and network > stall["network"]
        if network is not None:
            stall["network"] = max(stall["network"] or 0, network)
        # A rung loan that finishes inside one decision never shows as an advancing batch; the next rung the
        # ladder waits at does. A high-water mark keeps a consumed, re-made rung from counting again.
        rungs = ladder_rungs(result)
        climbed = rungs is not None and stall["rungs"] is not None and rungs > stall["rungs"]
        if rungs is not None:
            stall["rungs"] = max(stall["rungs"] if stall["rungs"] is not None else rungs, rungs)
        progress_signals = {"research_units": bool(objective and objective.state.get("progressed")),
                            "machines": machines > stall["machines"], "missing_materials": missing < stall["missing"],
                            "goal_gap": gap < stall["best_gap"] * .95, "material_crafts": crafted,
                            "owned_batch": advancing, "installed": delivered, "network": extended, "ladder": climbed,
                            "funded_plan": bool(result.get("progress"))}
        # A newly funded, committed construction plan may progress before an incomplete return.
        # Repeating a construction status or merely reserving a ready bill is not progress.
        submitted = bool(result.get("progress"))
        working = False  # A repeated wait code alone is not observed progress.
        progressed = (machines > stall["machines"] or missing < stall["missing"] or crafted
                      or gap < stall["best_gap"] * 0.95 or submitted or working or any(progress_signals.values()))
        stall["machines"], stall["missing"] = max(stall["machines"], machines), min(stall["missing"], missing)
        stall["best_gap"] = min(stall["best_gap"], gap)
        if progressed or stall["tick"] is None:
            stall["tick"] = after["tick"]
            stall["last_reset"] = {"tick": after["tick"], "reasons": [name for name, value in progress_signals.items() if value],
                                  "material_obligations": finite_credits}
        summary["progress_guard"] = {"last_reset": stall.get("last_reset"), "observed_tick": after["tick"],
                                     "stall_after_ticks": STALL_TICKS, "remaining_shortages": short}
        stall["tool_ticks"] = max(0, tool_end_tick - tool_start_tick)
        if goal:
            # Measured complete stall: the whole relevant, productive state is unchanged across the
            # declared observation window. A pending status alone does not reset it.
            relevant = set(short) | set((board.get("construction") or {}).get("shortfall") or {})
            relevant |= {n["item"] for n in board["needs"] if (n.get("goal_required_per_s") or 0) > 0}
            after_stock: dict[str, int] = {}
            for network in (after.get("networks") or {}).values():
                for name, count in (network.get("items") or {}).items():
                    after_stock[name] = after_stock.get(name, 0) + int(count)
            # Tool calls may take minutes while research and loan steps advance.
            # Re-read them before deciding that the whole window was unchanged.
            goal_status = _goal_status(session, goal)
            after_economics = session.economics() if hasattr(session, "economics") else economics
            signature = useful_state(after, relevant_items=sorted(relevant), stock=after_stock,
                                     goal_status=goal_status, economics=after_economics)
            if signature != stall["last_state"]:
                stall["last_state"], stall["changed_tick"] = signature, after["tick"]
                stall["since"], stall["idle_ticks"] = 0, 0
            else:
                stall["since"] += 1
                base = stall["changed_tick"] if stall["changed_tick"] is not None else samples[0][0]
                stall["idle_ticks"] = max(0, after["tick"] - base)
            window = observation_window(settings)
            summary["stall"] = {"idle_ticks": stall["idle_ticks"], "decisions": stall["since"],
                                "tool_ticks": stall["tool_ticks"], "version": "useful-state-window-v1",
                                "observation_ticks": window, "changed_tick": stall["changed_tick"],
                                "useful": signature}
            if window and stall["idle_ticks"] >= window:
                summary["stop"] = "stalled"
                emit(f"STALLED: useful state unchanged over {stall['idle_ticks']} game ticks "
                     f"(declared window {window})")
                break
        else:
            # The stall budget is decision-idle game time, not tool execution: one blocking deterministic call
            # can spend most of a window on real work (live 2026-10-10: one foundation call ate ~12k of 18k).
            prior_tick = stall["last_tick"] if stall["last_tick"] is not None else facts["tick"]
            elapsed = max(0, after["tick"] - prior_tick)
            stall["last_tick"] = after["tick"]
            stall["idle_ticks"] += max(0, elapsed - (0 if chosen["family"] == "wait" else stall["tool_ticks"]))
            if progressed:
                stall["idle_ticks"], stall["since"] = 0, 0
            else:
                stall["since"] += 1
            if stall["idle_ticks"] >= STALL_TICKS or stall["since"] >= settings.stall_decisions:
                summary["stop"] = "stalled"
                emit(f"STALLED: {stall['idle_ticks']} idle game ticks / {stall['since']} decisions without a "
                     "funded submission, work in progress or goal progress")
                break
        if goal and (result.get("code") in {"tool_error", "untyped_stuck"}
                     or (result.get("classification") == "bug" and result.get("state") == "failed")):
            summary["stop"] = "hard_failure"
            summary["hard_failure"] = {"code": result.get("code"), "message": result.get("message"),
                                       "operation": result.get("operation"), "params": result.get("params")}
            break
        # Busy is not progress either: the mission must reach a new phase or acceptance within the budget.
        mark = (len(reached), len(summary.get("accepted", [])))
        if mark != stall["mark"]:
            stall["mark_tick"] = samples[0][0] if stall["mark"] is None else after["tick"]
            stall["mark"] = mark
        if PHASE_BUDGET_TICKS is not None and after["tick"] - stall["mark_tick"] >= PHASE_BUDGET_TICKS:
            summary["stop"] = "phase_budget"
            emit(f"PHASE BUDGET: {PHASE_BUDGET_TICKS} ticks without a new proxy phase or strict acceptance")
            break
        if summary["failures"] >= 3 and all(r.get("code") == "tool_error" for r in results[-3:]):
            summary["stop"] = "safe_stop_after_repeated_failures"
            break
        # Receipt, progress guard and acceptance state belong to one generation.
        save_checkpoint(after["tick"])
    check_acceptance("end")
    if checkpoint_dir is not None and samples:
        save_checkpoint(adapter.query()["tick"], final=True)
    summary["cumulative_wall_seconds"] = elapsed_before + time.monotonic() - active_started
    if until_accepted and until_accepted in summary.get("accepted", []):
        summary["stop"] = "accepted_window_reached"
    session.close()
    # Typed outcomes, not only caught exceptions: an untyped_stuck is a tool fault even when nothing raised.
    summary["codes"] = dict(Counter(r.get("code") or "error" for r in results if not r.get("ok")))
    if not goal:
        summary["stall"] = {"idle_ticks": stall["idle_ticks"], "decisions": stall["since"],
                            "tool_ticks": stall["tool_ticks"], "version": "decision-idle-v2",
                            "idle_tick_limit": STALL_TICKS, "decision_limit": settings.stall_decisions}
    elif "goal" not in summary:
        # Expose version and stall evidence even when the run ended on the decision budget or a failure.
        summary["goal"] = goal_evidence(goal_status) if goal_status is not None else {"version": GOAL_VERSION,
                                                                                     "verified": False}
    summary["classifications"] = dict(Counter(r.get("classification") or "unclassified" for r in results if not r.get("ok")))
    _append(journal, {"run_id": run_id, "event": "summary", **summary})
    emit(f"SUMMARY: stop={summary['stop']} decisions={summary['decisions']} failures={summary['failures']} "
         f"phases={','.join(reached) or 'none'} accepted={','.join(summary.get('accepted', [])) or 'none'} "
         f"accepted_final={'unknown' if summary.get('accepted_final') is None else ','.join(summary['accepted_final']) or 'none'}")
    return summary
