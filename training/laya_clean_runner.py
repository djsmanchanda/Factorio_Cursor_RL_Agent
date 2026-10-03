# Path: training/laya_clean_runner.py
# Purpose: Clean Laya runner core: status board -> need-tagged legal options -> policy choice -> existing tooling executes -> measured, journaled outcome.

"""Clean Laya runner (docs/rl/Laya_Clean_Runner.md).

The world interface is the existing saved-world adapter, which observes the
copied save, validates plans live (collision, coverage, funding) and builds
funded ghosts through the mod's layout executor.  This module adds only:

* need tagging: every option names the status-board need it serves;
* a pluggable policy that chooses a need, then an option;
* a loop that journals before acting and turns every failure into evidence.

Nothing here ranks options.  The policy is the only chooser; until a
compatible Laya checkpoint exists it is seeded exploration, and the journal
says so.
"""

from __future__ import annotations

import json
import random
import time
import traceback
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from orchestrator.laya_knowledge import load_guideline_context
from orchestrator.laya_state import validate_state
from training.laya_factory_status import apply_to_state, compact_status, compile_status
from training.laya_mission import MISSION_ID, current_phase, phase_reached
from training.laya_power import power_variants
from training.laya_site_plan import base_zones, draw_command, load_plan, plan_options, reservation_reason, save_plan
from training.laya_saved_world import SavedWorldAdapter, SavedWorldError, compile_candidates, decision_state

ROOT = Path(__file__).resolve().parents[1]
MISSION_CATALOG = "docs/rl/laya_mission_guidelines.json"
WAIT_TICKS = (300, 1800)
EXPLORATION_MODEL = {"model_id": "laya-exploration-v1", "model_hash": "sha256:" + "0" * 64}
MAX_CONSECUTIVE_FAILURES = 3
# The entity each family exists to add; live checks credit entities already placed.
PRIMARY_ENTITY = {"expand": "roboport", "mine": "electric-mining-drill", "smelt": "electric-furnace",
                  "assemble": "assembling-machine-1"}
# Recipes the clean runner needs exported in addition to the shared helper's list.
RECIPE_LIST_END = '"chemical-plant","oil-refinery","pumpjack"}'
EXTRA_RECIPES = ("big-electric-pole", "iron-stick")
PAUSED_ASSERTION = 'assert(game.tick_paused and game.ticks_to_run == 0, "saved-world clock must be paused")'

Validator = Callable[[Sequence[Mapping]], Mapping[str, Mapping]]


class LiveWorld(SavedWorldAdapter):
    """Live play: the game clock runs unpaused while Laya observes and decides.

    Replaces the adapter's paused fixed-tick stepping; each measurement window
    is the real number of ticks that elapsed.
    """

    def __init__(self, root: Path, speed: float = 1.0) -> None:
        super().__init__(root)
        if not 0.25 <= speed <= 8:
            raise SavedWorldError("live speed must be 0.25..8")
        self.speed = speed
        # The shared helper requires a paused clock for stepped exactness. One
        # /sc command runs inside a single tick, so a live snapshot is still
        # atomic; drop only that assertion and fail closed if it changes.
        if PAUSED_ASSERTION not in self.lua_source:
            raise SavedWorldError("saved-world helper changed; live observation assertion not found")
        self.lua_source = self.lua_source.replace(PAUSED_ASSERTION, "")
        # Export big poles and iron sticks too, so they can become stock needs.
        if RECIPE_LIST_END not in self.lua_source:
            raise SavedWorldError("saved-world helper changed; recipe list end not found")
        extra = "".join(f',"{name}"' for name in EXTRA_RECIPES)
        self.lua_source = self.lua_source.replace(RECIPE_LIST_END, RECIPE_LIST_END[:-1] + extra + "}")

    def tick(self) -> int:
        return int(json.loads(self.bridge.command("/sc rcon.print(game.tick)")))

    def go_live(self) -> None:
        self.bridge.command(f'/sc game.tick_paused=false; game.ticks_to_run=0; game.speed={self.speed}; rcon.print("live")')

    def advance(self, ticks: int) -> None:
        if not isinstance(ticks, int) or not 1 <= ticks <= 18000:
            raise SavedWorldError("step must be 1..18000 ticks")
        start = self.tick()
        self.go_live()  # Idempotent; restores the clock if anything paused it.
        deadline = time.monotonic() + ticks / 60 / self.speed + 60
        while time.monotonic() < deadline:
            if self.tick() >= start + ticks:
                return
            time.sleep(0.5)
        raise SavedWorldError("live game clock did not advance")


def need_of(candidate: Mapping) -> str | None:
    """The need an option serves: its product, coverage for expansion, none for waits."""
    family = candidate["family"]
    if family == "wait":
        return None
    if family == "expand":
        return "coverage"
    return candidate.get("product")


def _in_reach(port: Mapping, position: Mapping, radius: float) -> bool:
    # Chebyshev reach with the mine compiler's 6-tile margin.
    return max(abs(position["x"] - port["x"]), abs(position["y"] - port["y"])) <= radius - 6


def newly_reached(facts: Mapping, candidate: Mapping) -> set[str]:
    """Resources a coverage option's new roboport brings into logistic reach.

    Mines deliver into provider chests, which need logistic (not just
    construction) coverage, so logistic radius is the binding reach.
    """
    ports = [p for p in facts["ports"] if p["energy"] > 0]
    if not ports:
        return set()
    radius = ports[0]["logistic_radius"]
    new = [a["position"] for phase in candidate["plan"]["phases"] for a in phase["actions"] if a["entity"] == "roboport"]
    reached = set()
    for site in facts["resources"]:
        if any(_in_reach(p["position"], site["position"], p["logistic_radius"]) for p in ports):
            continue
        if any(_in_reach(n, site["position"], radius) for n in new):
            reached.add(site["item"])
    return reached


APPROACH_TILES = 16.0  # A hop "approaches" an unreachable ore when it closes at least this much distance.


def approaches(facts: Mapping, candidate: Mapping, ores: set[str]) -> set[str]:
    """Unmet ores with no site in logistic reach that this coverage hop moves substantially closer to."""
    ports = [p for p in facts["ports"] if p["energy"] > 0]
    new = [a["position"] for phase in candidate["plan"]["phases"] for a in phase["actions"] if a["entity"] == "roboport"]
    if not ports or not new:
        return set()
    result = set()
    for ore in ores:
        sites = [s["position"] for s in facts["resources"] if s["item"] == ore]
        if not sites or any(_in_reach(p["position"], s, p["logistic_radius"]) for p in ports for s in sites):
            continue
        gap = lambda origin: min(max(abs(s["x"] - origin["x"]), abs(s["y"] - origin["y"])) for s in sites)
        if min(gap(p["position"]) for p in ports) - min(gap(n) for n in new) >= APPROACH_TILES:
            result.add(ore)
    return result


def _sample(groups: Mapping[str, list], limit: int, rng: random.Random) -> list:
    picked = []
    for key in sorted(groups):
        items = list(groups[key])
        rng.shuffle(items)
        picked.extend(items[:limit])
    return picked


def _compile(facts: Mapping, products: set[str], seed: int) -> list[dict]:
    """Generator candidates for only the products a need can use (speed, not a choice)."""
    recipes = {k: v for k, v in facts["recipes"].items() if k in products}
    return compile_candidates({**facts, "recipes": recipes}, seed)


def compile_menu(facts: Mapping, board: Mapping, validate: Validator, seed: int, *,
                 site_plan: Mapping | None = None, per_need_checked: int = 6, per_need_shown: int = 2) -> dict:
    """Group legal options by the unmet need they serve.

    Each sampled build is offered with its power alternatives (training/laya_power.py).
    Construction items that legal checks report missing (``item_shortfall``)
    become ``stock:<item>`` needs served by assembling that item.  Sampling per
    need is seeded exploration of sites; it never ranks needs or options.
    """
    rng = random.Random(seed)
    unmet = {n["item"] for n in board["needs"] if n["condition"] != "satisfied"}
    proposed = _compile(facts, unmet, seed)
    by_need: dict[str, list] = defaultdict(list)
    for candidate in proposed:
        need = need_of(candidate)
        if need == "coverage":
            ores = (newly_reached(facts, candidate) & unmet) or approaches(facts, candidate, unmet)
            # A coverage hop that reaches (or closes on) an unmet resource serves it.
            by_need[f"item:{sorted(ores)[0]}" if ores else "coverage"].append(candidate)
        elif need in unmet:
            by_need[f"item:{need}"].append(candidate)

    rejected: Counter = Counter()
    shortfalls: Counter = Counter()
    reasons: dict[str, Counter] = defaultdict(Counter)

    def check(groups):
        expanded = {}
        for need in sorted(groups):
            builds = list(groups[need])
            rng.shuffle(builds)
            variants = [v for c in builds[:per_need_checked] for v in power_variants(c, facts)]
            if builds and not variants:
                reasons[need]["no_power_layout"] += 1
            expanded[need] = []
            for variant in variants:
                # Land reservation is a hard rule: reject builds on another purpose's land.
                reason = reservation_reason(site_plan, variant) if site_plan else None
                if reason:
                    reasons[need][reason] += 1
                    rejected[reason.split(":", 1)[0]] += 1
                else:
                    expanded[need].append(variant)
        sample = [v for variants in expanded.values() for v in variants]
        results = validate(sample) if sample else {}
        legal: dict[str, list] = defaultdict(list)
        for need, candidates in expanded.items():
            for candidate in candidates:
                result = results.get(candidate["option_id"])
                if result is None:
                    continue
                reason = result.get("reason")
                primary = PRIMARY_ENTITY.get(candidate["family"])
                if not reason and (not result.get("bill") or (primary and primary not in result["bill"])):
                    reason = "no_op"  # Its main entity already exists; only power pieces would be new.
                if reason:
                    reasons[need][reason] += 1
                    rejected[reason.split(":", 1)[0]] += 1
                    if reason.startswith("item_shortfall:"):
                        shortfalls[reason.split(":", 1)[1]] += 1
                    continue
                legal[need].append({**candidate, "bill": result.get("bill") or {}, "need_id": need})
        return legal

    legal = check(by_need)
    stock_groups: dict[str, list] = defaultdict(list)
    for candidate in _compile(facts, set(shortfalls), seed) if shortfalls else ():
        if candidate["family"] == "assemble" and candidate.get("product") in shortfalls:
            stock_groups[f"stock:{candidate['product']}"].append(candidate)
    legal.update(check(stock_groups))

    conditions = {f"item:{n['item']}": n["condition"] for n in board["needs"]}
    needs, options = [], []
    for need in sorted(legal):
        shown = _sample({need: legal[need]}, per_need_shown, rng)
        options.extend(shown)
        needs.append({"need_id": need, "condition": conditions.get(need, "shortfall" if need.startswith("stock:") else "available"),
                      "options": [c["option_id"] for c in shown]})
    waits = [{"option_id": f"wait.{ticks}", "family": "wait", "summary": f"Wait {ticks / 60:g} game seconds",
              "wait_ticks": ticks, "need_id": "wait"} for ticks in WAIT_TICKS]
    options.extend(waits)
    needs.append({"need_id": "wait", "condition": "available", "options": [w["option_id"] for w in waits]})
    # Needs with no legal option, and why: observation, not a remedy.
    blocked = {need: dict(reasons[need]) for need in sorted(set(by_need) | set(stock_groups)) if need not in legal}
    for item in sorted(unmet):
        if f"item:{item}" not in by_need:
            blocked[f"item:{item}"] = {"no_candidate": 1}
    return {"needs": needs, "options": options, "proposed": len(proposed), "blocked": blocked,
            "rejected": dict(rejected), "shortfalls": dict(shortfalls)}


class Policy(Protocol):
    authority: str
    model_ref: Mapping[str, str]

    def choose(self, state: Mapping, menu: Mapping) -> dict: ...


class ExplorationPolicy:
    """Seeded uniform choice of a need, then of one of its options.

    This is the explicit stand-in until a mall/saved-world-compatible Laya
    checkpoint exists; its decisions are training data, not learned policy.
    """

    authority = "exploration"
    model_ref = EXPLORATION_MODEL

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)

    def choose(self, state: Mapping, menu: Mapping) -> dict:
        groups = [n for n in menu["needs"] if n["options"]]
        need = self.rng.choice(groups)
        option = self.rng.choice(need["options"])
        return {"need_id": need["need_id"], "option_id": option,
                "probability": 1.0 / len(groups) / len(need["options"])}


def build_state(facts: Mapping, menu: Mapping, board: Mapping, phase: Mapping, run_id: str,
                model_ref: Mapping, history: Sequence[Mapping], measurement: Mapping | None) -> dict:
    """Laya decision state: saved-world facts plus status board and mission guidelines."""
    objective = f"{MISSION_ID} phase {phase['phase_id']} ({phase['name']}): " + ", ".join(
        f"{item} {rate:g}/s" for item, rate in sorted(phase["targets"].items()))
    state = decision_state(facts, menu["options"], run_id, model_ref, objective, history, measurement)
    apply_to_state(state, board)
    guidance = load_guideline_context(ROOT, phase["scopes"], catalog=MISSION_CATALOG, max_rules=15)  # +1 adapter rule <= 16
    state["guidelines"] = state["guidelines"] + [
        {"rule_id": rule["rule_id"], "kind": rule["kind"], "statement": rule["statement"]}
        for rule in guidance["guidelines"]]
    # Need tags travel with each option so a model can learn need -> action.
    need_index = {option_id: n["need_id"] for n in menu["needs"] for option_id in n["options"]}
    for option in state["options"]:
        option["summary"] = f"[{need_index.get(option['option_id'], 'none')}] {option['summary']}"
    validate_state(state)
    return state


def _append(path: Path, record: Mapping) -> None:
    with path.open("a") as out:
        out.write(json.dumps(record, sort_keys=True) + "\n")


def _stock(facts: Mapping) -> dict[str, int]:
    stock: Counter = Counter()
    for network in facts["networks"].values():
        stock.update({k: int(v) for k, v in network["items"].items()})
    return dict(sorted(stock.items()))


def _write_status(path: Path | None, payload: Mapping) -> None:
    if path is None:
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n")
    tmp.replace(path)


def _status_line(board: Mapping) -> str:
    return " | ".join(f"{n['item']} {n['condition']} {n['measured'] if n['measured'] is not None else '?'}/{n['required']:g}"
                      for n in board["needs"])


def _draw_zones(adapter: Any, plan: Mapping, emit: Callable[[str], None]) -> None:
    """Outline reserved zones in game for spectators; drawing failures never stop the run."""
    bridge = getattr(adapter, "bridge", None)
    if bridge is None:
        return
    try:
        bridge.command(draw_command(plan))
    except Exception as error:  # Rendering is cosmetic.
        emit(f"  ZONES NOT DRAWN: {type(error).__name__}: {str(error)[:160]}")


def _plan_decision(facts: Mapping, board: Mapping, phase: Mapping, policy: "Policy", record: dict, journal: Path,
                   run_id: str, history: list, emit: Callable[[str], None]) -> dict:
    """One planning decision: the policy chooses the mall site; ore and smelting land is reserved."""
    options = plan_options(facts)
    if not options:
        emit("PLAN: no mall site clears the reserved land; ore and smelting zones reserved, mall unzoned")
        return {"version": "laya-site-plan-v1", "mall_site": None, "zones": base_zones(facts)}
    menu = {"needs": [{"need_id": "plan:site", "condition": "unplanned", "options": [o["option_id"] for o in options]}],
            "options": options, "blocked": {}, "rejected": {}, "shortfalls": {}}
    state = build_state(facts, menu, board, phase, run_id, policy.model_ref, history, None)
    choice = policy.choose(state, menu)
    chosen = next(o for o in options if o["option_id"] == choice["option_id"])
    record.update(event="decision", tick=facts["tick"], phase=phase["phase_id"], status="planned", choice=choice,
                  summary=chosen["summary"], menu={"plan:site": [o["option_id"] for o in options]},
                  site_plan=chosen["site_plan"])
    _append(journal, record)
    emit(f"PLAN DECISION {record['index']} p={choice['probability']:.3f} authority={policy.authority}: {chosen['summary']}")
    for zone in chosen["site_plan"]["zones"]:
        emit(f"  ZONE {zone['zone_id']} {zone['purpose']} rect={[round(v, 1) for v in zone['rect']]}")
    return chosen["site_plan"]


def run(adapter: Any, policy: Policy, *, journal: Path, decisions: int | None, measure_ticks: int,
        seed: int, run_id: str, emit: Callable[[str], None] = lambda _: None,
        should_stop: Callable[[], bool] = lambda: False, status_path: Path | None = None) -> dict:
    """Observe -> board -> menu -> choose -> execute -> measure, journaling each step.

    ``decisions=None`` runs until the mission completes, the safe-stop rule
    fires or ``should_stop`` turns true.  Any failure is journaled and the
    loop continues; it stops safely after ``MAX_CONSECUTIVE_FAILURES`` in a
    row.  It never falls back to another decision maker.
    """
    reached: list[str] = []
    history: list[dict] = []
    measurement = None
    failures = 0
    last_status = last_blocked = None
    plan_path = journal.parent / "site-plan.json"
    site_plan = load_plan(plan_path)
    if site_plan:
        _draw_zones(adapter, site_plan, emit)
    summary = {"decisions": 0, "failures": 0, "phases_reached": reached, "stop": "decision_budget"}
    index = -1
    while decisions is None or index + 1 < decisions:
        index += 1
        if should_stop():
            summary["stop"] = "stopped"
            break
        record: dict[str, Any] = {"run_id": run_id, "index": index, "authority": policy.authority}
        try:
            adapter.reconcile()
            facts = adapter.query()
            phase = current_phase(reached)
            board = compile_status(facts, phase["targets"], measurement) if phase else None
            while phase and phase_reached(board):
                reached.append(phase["phase_id"])
                _append(journal, {"run_id": run_id, "event": "phase_reached", "phase": phase["phase_id"], "tick": facts["tick"]})
                emit(f"PHASE REACHED: {phase['phase_id']} {phase['name']} at tick {facts['tick']}")
                phase = current_phase(reached)
                board = compile_status(facts, phase["targets"], measurement) if phase else None
            if phase is None:
                summary["stop"] = "mission_complete"
                break
            if site_plan is None:
                # Planning comes first: reserve land before anything is built.
                site_plan = _plan_decision(facts, board, phase, policy, record, journal, run_id, history, emit)
                save_plan(plan_path, site_plan)
                _draw_zones(adapter, site_plan, emit)
                summary["decisions"] += 1
                continue
            menu = compile_menu(facts, board, lambda cs: adapter.query(list(cs))["validations"], seed + index,
                                site_plan=site_plan)
            state = build_state(facts, menu, board, phase, run_id, policy.model_ref, history, measurement)
            choice = policy.choose(state, menu)
            chosen = next(o for o in menu["options"] if o["option_id"] == choice["option_id"])
            compact = compact_status(board)
            record.update(event="decision", tick=facts["tick"], phase=phase["phase_id"], status="pending",
                          board=compact, menu={n["need_id"]: n["options"] for n in menu["needs"]},
                          rejected=menu["rejected"], shortfalls=menu["shortfalls"], blocked=menu["blocked"], choice=choice,
                          summary=chosen["summary"])
            _append(journal, record)  # Durable before any mutation.
            emit(f"DECISION {index} phase={phase['phase_id']} tick={facts['tick']} need={choice['need_id']} "
                 f"p={choice['probability']:.3f} authority={policy.authority}: {chosen['summary']}")
            status = _status_line(compact)
            if status != last_status:
                emit(f"  STATUS: {status}")
                last_status = status
            if compact["bottleneck"]:
                emit("  BOTTLENECK: " + " | ".join(compact["bottleneck"]))
            blocked = "; ".join(f"{need} " + ",".join(f"{r}={c}" for r, c in sorted(reasons.items()))
                                for need, reasons in sorted(menu["blocked"].items()))
            if blocked != last_blocked:
                emit(f"  BLOCKED: {blocked or 'none'}")
                last_blocked = blocked
            _write_status(status_path, {"run_id": run_id, "index": index, "tick": facts["tick"], "phase": phase,
                                        "reached": reached, "board": compact, "blocked": menu["blocked"],
                                        "menu": {n["need_id"]: [next(o["summary"] for o in menu["options"] if o["option_id"] == i)
                                                                for i in n["options"]] for n in menu["needs"]},
                                        "choice": {**choice, "summary": chosen["summary"]}, "stock": _stock(facts),
                                        "guidelines": state["guidelines"], "authority": policy.authority,
                                        "decisions": summary["decisions"], "failures": summary["failures"],
                                        "site_plan": site_plan,
                                        "measured_per_s": record.get("measured_per_s")})
            receipt = adapter.execute(choice["option_id"], menu["options"], measure_ticks)
            measurement = receipt["measurement"]
            record.update(status=receipt["status"], measured_per_s={
                k: round(v["per_second"], 3) for k, v in measurement["production"].items() if v["per_second"]})
            _append(journal, record)
            emit(f"  OUTCOME: {receipt['status']} over {measurement['window_ticks']} ticks; produced "
                 + (", ".join(f"{k}={v}/s" for k, v in sorted(record["measured_per_s"].items())) or "nothing"))
            history.append({"decision_id": f"decision.{index}", "tick": facts["tick"],
                            "summary": record["summary"][:120],
                            "outcome": "completed" if receipt["status"] == "observed" else "pending"})
            summary["decisions"] += 1
            failures = 0
        except Exception as error:  # Never-crash contract: failures are evidence.
            failures += 1
            summary["failures"] += 1
            record.update(event="failure", status="failed", error_type=type(error).__name__, error=str(error)[:500],
                          trace=traceback.format_exc().splitlines()[-6:])
            _append(journal, record)
            emit(f"FAILURE {failures}/{MAX_CONSECUTIVE_FAILURES}: {type(error).__name__}: {str(error)[:300]}")
            if failures >= MAX_CONSECUTIVE_FAILURES:
                summary["stop"] = "safe_stop_after_repeated_failures"
                emit(f"SAFE STOP: {MAX_CONSECUTIVE_FAILURES} consecutive failures; last: {str(error)[:200]}")
                break
    _append(journal, {"run_id": run_id, "event": "summary", **summary})
    emit(f"SUMMARY: stop={summary['stop']} decisions={summary['decisions']} failures={summary['failures']} "
         f"phases={','.join(reached) or 'none'}")
    return summary
