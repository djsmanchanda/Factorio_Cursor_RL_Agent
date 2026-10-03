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
import math
import random
from dataclasses import dataclass
import time
import traceback
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from orchestrator.laya_knowledge import load_guideline_context
from core.local_time import local_stamp
from orchestrator.laya_operations import sha256_hash
from orchestrator.laya_state import validate_state
from training.laya_factory_status import apply_to_state, compact_status, compile_status
from training.laya_mission import MISSION_ID, current_phase, phase_reached
from training.laya_guided_policy import load_preferences, matches, option_features
from training.laya_power import POLES, power_variants
from training.laya_site_plan import (base_zones, draw_command, load_plan, plan_options, reservation_reason,
                                    ring_expand_candidates, save_plan)
from training.laya_lines import line_candidates
from training.laya_starters import STARTER_ORE, starter_candidates
from training.laya_saved_world import SavedWorldAdapter, SavedWorldError, compile_candidates, decision_state

ROOT = Path(__file__).resolve().parents[1]
MISSION_CATALOG = "docs/rl/laya_mission_guidelines.json"


@dataclass(frozen=True)
class Settings:
    """Tunable runner settings; variants (training/laya_variants.py) override these.

    Defaults are the current workflow. ``until_phase`` / ``max_game_ticks`` bound
    experiment windows; a normal run leaves them unset.
    """

    catalog: str = MISSION_CATALOG
    guideline_limit: int = 15
    per_need_checked: int = 6
    per_need_shown: int = 2
    stall_decisions: int = 12
    stock_rate: float = 0.01
    rate_window_ticks: int = 7200
    until_phase: str | None = None
    max_game_ticks: int | None = None
WAIT_TICKS = (300, 1800)
MAX_MENU = 20  # laya_state schema: options maxItems
EXPLORATION_MODEL = {"model_id": "laya-exploration-v1", "model_hash": "sha256:" + "0" * 64}
MAX_CONSECUTIVE_FAILURES = 3
# The entity each family exists to add; live checks credit entities already placed.
PRIMARY_ENTITY = {"expand": "roboport", "mine": "electric-mining-drill", "smelt": "electric-furnace",
                  "starter": "electric-mining-drill", "line": "electric-furnace",
                  "assemble": "assembling-machine-1"}
# Recipes the clean runner needs exported in addition to the shared helper's list.
RECIPE_LIST_END = '"chemical-plant","oil-refinery","pumpjack"}'
EXTRA_RECIPES = ("big-electric-pole", "iron-stick")
# Idle furnaces return no current recipe; report the previous one so built-but-starved
# furnaces stay attributed to their product.
RECIPE_LINE = 'if e.type~="mining-drill" then local recipe=e.get_recipe(); m.recipe=recipe and recipe.name end'
RECIPE_LINE_LIVE = ('if e.type~="mining-drill" then local recipe=e.get_recipe(); m.recipe=recipe and recipe.name; '
                    'if not m.recipe and e.type=="furnace" then local ok,prev=pcall(function() return e.previous_recipe end); '
                    'if ok and prev then local n=prev.name; if type(n)~="string" then n=n and n.name end; m.recipe=n end end end')
COLLISION_RETURN = 'then return "collision" end'
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
        if RECIPE_LINE not in self.lua_source:
            raise SavedWorldError("saved-world helper changed; machine recipe line not found")
        self.lua_source = self.lua_source.replace(RECIPE_LINE, RECIPE_LINE_LIVE)
        # Name the colliding entity so journals separate relay-pole from machine collisions.
        if COLLISION_RETURN not in self.lua_source:
            raise SavedWorldError("saved-world helper changed; collision check not found")
        self.lua_source = self.lua_source.replace(COLLISION_RETURN, 'then return "collision:"..a.entity end')

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
    # Only resources with no site in reach today: coverage cannot help an ore already reachable.
    reachable_now = {site["item"] for site in facts["resources"]
                     if any(_in_reach(p["position"], site["position"], p["logistic_radius"]) for p in ports)}
    reached = set()
    for site in facts["resources"]:
        if site["item"] in reachable_now:
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


def _build_only(candidate: Mapping) -> dict:
    """The candidate's plan without the generator's power relay (substations/poles)."""
    phase = candidate["plan"]["phases"][0]
    actions = [a for a in phase["actions"] if a["entity"] not in POLES]
    return {**candidate["plan"], "phases": [{**phase, "actions": actions}]}


def planned_sites(facts: Mapping, plan: Mapping | None) -> Mapping:
    """Facts whose resources are only the planned ore zones' sites, for ores that have one.

    Builds go on planned zones, so coverage reaches or approaches an ore only
    toward those sites, not toward the small patches the plan ignores.
    """
    if not plan:
        return facts
    zones: dict[str, list] = defaultdict(list)
    for zone in plan["zones"]:
        if zone["purpose"].startswith("ore:"):
            zones[zone["purpose"][4:]].append(zone["rect"])
    kept = [site for site in facts["resources"] if site["item"] not in zones or any(
        r[0] <= site["position"]["x"] <= r[2] and r[1] <= site["position"]["y"] <= r[3] for r in zones[site["item"]])]
    return {**facts, "resources": kept}


def _need_key(facts: Mapping, candidate: dict, unmet: set[str]) -> str:
    need = need_of(candidate)
    if need == "coverage":
        reached = newly_reached(facts, candidate) & unmet
        approached = approaches(facts, candidate, unmet) if not reached else set()
        candidate["coverage_reach"] = {"reaches": sorted(reached), "approaches": sorted(approached)}  # Journaled.
        ores = reached or approached
        return f"item:{sorted(ores)[0]}" if ores else "coverage"
    return f"item:{need}"


def _compile(facts: Mapping, products: set[str], seed: int) -> list[dict]:
    """Generator candidates for only the products a need can use (speed, not a choice)."""
    recipes = {k: v for k, v in facts["recipes"].items() if k in products}
    return compile_candidates({**facts, "recipes": recipes}, seed)


def compile_menu(facts: Mapping, board: Mapping, validate: Validator, seed: int, *,
                 site_plan: Mapping | None = None, starters: frozenset | set = frozenset(),
                 per_need_checked: int = 6, per_need_shown: int = 2) -> dict:
    """Group legal options by the unmet need they serve.

    Each sampled build is offered with its power alternatives (training/laya_power.py).
    Construction items that legal checks report missing (``item_shortfall``)
    become ``stock:<item>`` needs served by assembling that item.  Sampling per
    need is seeded exploration of sites; it never ranks needs or options.
    """
    rng = random.Random(seed)
    unmet = {n["item"] for n in board["needs"] if n["condition"] != "satisfied"}
    proposed = _compile(facts, unmet, seed)
    # Iron, copper and stone start with direct starters (drills feeding furnaces); the
    # generator's single-drill-into-chest mines and requester-fed furnaces are not offered.
    proposed = [c for c in proposed if not ((c["family"] == "smelt" and c.get("product") in STARTER_ORE)
                                            or (c["family"] == "mine" and c.get("product") in STARTER_ORE.values()))]
    plates = {plate for plate, ore in STARTER_ORE.items() if plate in unmet or ore in unmet}
    proposed += starter_candidates(facts, site_plan, plates, set(starters))
    # After a plate's starter, more capacity comes from smelting lines (6 or 18 furnaces), added in parallel.
    proposed += line_candidates(facts, site_plan, {p for p in plates if p in starters})
    proposed += ring_expand_candidates(facts, site_plan)
    pre_rejected: dict[str, Counter] = defaultdict(Counter)
    land_examples: dict[str, list] = defaultdict(list)

    def on_free_land(candidates: list[dict], key_of: Callable[[dict], str]) -> list[dict]:
        """Land check on the build itself before sampling (the generator's relays are replaced later)."""
        if not site_plan:
            return candidates
        kept = []
        for candidate in candidates:
            detail: list = []
            reason = reservation_reason(site_plan, {**candidate, "plan": _build_only(candidate)}, detail)
            if reason:
                key = key_of(candidate)
                pre_rejected[key][reason] += 1
                if len(land_examples[key]) < 3:  # Exact rejected entities for the journal.
                    land_examples[key].append(detail[0])
            else:
                kept.append(candidate)
        return kept

    reach_facts = planned_sites(facts, site_plan)
    proposed = on_free_land(proposed, lambda c: _need_key(reach_facts, c, unmet))
    by_need: dict[str, list] = defaultdict(list)
    for candidate in proposed:
        need = need_of(candidate)
        # A coverage hop that reaches (or closes on) an unmet resource serves it.
        if need == "coverage" or need in unmet:
            by_need[_need_key(reach_facts, candidate, unmet)].append(candidate)

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
    stock = [c for c in (_compile(facts, set(shortfalls), seed) if shortfalls else ())
             if c["family"] == "assemble" and c.get("product") in shortfalls]
    for candidate in on_free_land(stock, lambda c: f"stock:{c['product']}"):
        if f"item:{candidate['product']}" in legal:
            continue  # Already a board need with its own options; one need per product.
        stock_groups[f"stock:{candidate['product']}"].append(candidate)
    legal.update(check(stock_groups))

    for need, counts in pre_rejected.items():
        reasons[need].update(counts)
        rejected.update({r.split(":", 1)[0]: c for r, c in counts.items()})
    conditions = {f"item:{n['item']}": n["condition"] for n in board["needs"]}
    # The decision state holds at most MAX_MENU options (waits included). Order needs by the
    # board's goal-tree order (not by gap), then stock and coverage; shrink to one option per
    # need when crowded, and journal what did not fit.
    board_order = [f"item:{n['item']}" for n in board["needs"]]
    ordered = sorted(legal, key=lambda n: (board_order.index(n) if n in board_order else len(board_order),
                                           n.startswith("coverage"), n))
    room = MAX_MENU - len(WAIT_TICKS)
    per_need = per_need_shown if len(ordered) * per_need_shown <= room else 1
    needs, options, seen, omitted_needs = [], [], set(), []
    for need in ordered:
        shown = [c for c in _sample({need: legal[need]}, per_need, rng) if c["option_id"] not in seen]
        if not shown:
            continue
        if len(options) + len(shown) > room:
            omitted_needs.append(need)
            continue
        seen.update(c["option_id"] for c in shown)
        options.extend(shown)
        needs.append({"need_id": need, "condition": conditions.get(need, "shortfall" if need.startswith("stock:") else "available"),
                      "options": [c["option_id"] for c in shown]})
    waits = [{"option_id": f"wait.{ticks}", "family": "wait", "summary": f"Wait {ticks / 60:g} game seconds",
              "wait_ticks": ticks, "need_id": "wait"} for ticks in WAIT_TICKS]
    options.extend(waits)
    needs.append({"need_id": "wait", "condition": "available", "options": [w["option_id"] for w in waits]})
    # Needs with no legal option, and why: observation, not a remedy.
    blocked = {need: dict(reasons[need]) for need in sorted(set(by_need) | set(stock_groups) | set(pre_rejected))
               if need not in legal}
    for item in sorted(unmet):
        if f"item:{item}" not in by_need:
            blocked[f"item:{item}"] = {"no_candidate": 1}
    return {"needs": needs, "options": options, "proposed": len(proposed), "blocked": blocked,
            "rejected": dict(rejected), "shortfalls": dict(shortfalls),
            "land_rejections": {k: v for k, v in land_examples.items() if k in blocked},
            "omitted_needs": omitted_needs}


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


def select_guidelines(rules: list[dict], state: Mapping, menu: Mapping, limit: int,
                      preferences: Mapping | None = None) -> tuple[list[dict], list[str]]:
    """Keep the rules most relevant to this decision when the phase has more than fit.

    Order: hard rules, rules whose preferences match a current option, text-only
    rules, then rules that match nothing on this menu.  Ties keep catalog order.
    """
    if len(rules) <= limit:
        return rules, []
    preferences = preferences if preferences is not None else load_preferences()
    features = list(option_features(state, menu).values())

    def rank(item: tuple[int, dict]) -> tuple[int, int]:
        index, rule = item
        if rule["kind"] == "hard":
            return 0, index
        prefs = preferences.get(rule["rule_id"])
        if not prefs:
            return 2, index
        return (1 if any(matches(p["when"], f) for p in prefs for f in features) else 3), index

    ranked = [rule for _, rule in sorted(enumerate(rules), key=rank)]
    return ranked[:limit], [rule["rule_id"] for rule in ranked[limit:]]


def build_state(facts: Mapping, menu: Mapping, board: Mapping, phase: Mapping, run_id: str,
                model_ref: Mapping, history: Sequence[Mapping], measurement: Mapping | None,
                settings: Settings = Settings()) -> dict:
    """Laya decision state: saved-world facts plus status board and mission guidelines."""
    objective = f"{MISSION_ID} phase {phase['phase_id']} ({phase['name']}): " + ", ".join(
        f"{item} {rate:g}/s" for item, rate in sorted(phase["targets"].items()))
    state = decision_state(facts, menu["options"], run_id, model_ref, objective, history, measurement)
    apply_to_state(state, board)
    state["observation_digest"]["mission_phase"] = phase["phase_id"]
    # Fetch every rule in scope; select_guidelines keeps the 15 most relevant to this decision.
    guidance = load_guideline_context(ROOT, phase["scopes"], catalog=settings.catalog, max_rules=32)
    rules, omitted = select_guidelines(guidance["guidelines"], state, menu, limit=settings.guideline_limit,
                                       preferences=load_preferences(ROOT / settings.catalog))
    state["guidelines"] = state["guidelines"] + [
        {"rule_id": rule["rule_id"], "kind": rule["kind"], "statement": rule["statement"]} for rule in rules]
    state["observation_digest"]["guidelines_omitted"] = omitted
    # Need tags travel with each option so a model can learn need -> action.
    need_index = {option_id: n["need_id"] for n in menu["needs"] for option_id in n["options"]}
    for option in state["options"]:
        option["summary"] = f"[{need_index.get(option['option_id'], 'none')}] {option['summary']}"
    validate_state(state)
    return state


def _option_record(option: Mapping) -> dict:
    """Option facts kept for fine-tuning (the plan itself is identified by hash)."""
    plan = option.get("plan")
    return {"option_id": option["option_id"], "family": option["family"], "need_id": option.get("need_id"),
            "product": option.get("product"), "power": option.get("power"), "summary": option.get("summary"),
            "bill": option.get("bill"), "wait_ticks": option.get("wait_ticks"),
            "plan_hash": sha256_hash(plan) if plan else None, "coverage_reach": option.get("coverage_reach"),
            "entities": dict(Counter(a["entity"] for a in plan["phases"][0]["actions"])) if plan else None}


def _training_record(path: Path, **fields: Any) -> None:
    """One self-contained line per decision: model input, options, policy distribution, outcome."""
    _append(path, {"version": "laya-decision-record-v1", "recorded_at": local_stamp(), **fields})


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


STALL_DECISIONS = 12
STOCK_RATE = 0.01  # items/s of demand per short construction item: enough to expose its inputs
RATE_WINDOW_TICKS = 7200  # Board rates span at least 2 game minutes; bot deliveries are bursty.


def rolling_measurement(samples: list[tuple[int, Mapping, Mapping]], window: int = RATE_WINDOW_TICKS) -> dict | None:
    """Rates from cumulative engine counters over the newest span of at least ``window`` ticks.

    Uses the newest sample at or before ``now - window``, else the oldest one;
    ``None`` until two samples exist.
    """
    if len(samples) < 2:
        return None
    now_tick, production, consumption = samples[-1]
    base = next((s for s in reversed(samples[:-1]) if s[0] <= now_tick - window), samples[0])
    span = now_tick - base[0]
    if span <= 0:
        return None

    def rates(now: Mapping, then: Mapping) -> dict:
        return {k: {"count": now[k] - then.get(k, 0), "per_second": (now[k] - then.get(k, 0)) * 60 / span} for k in now}

    return {"window_ticks": span, "production": rates(production, base[1]), "consumption": rates(consumption, base[2])}


def _goal_gap(board: Mapping) -> float:
    """Remaining goal rate across phase targets; unmeasured counts as the whole target."""
    needs = {n["item"]: n for n in board["needs"]}
    total = 0.0
    for item in board["targets"]:
        need = needs[item]
        goal = need.get("goal_required_per_s", need["required_per_s"])
        total += goal if need["measured_per_s"] is None else max(0.0, goal - need["measured_per_s"])
    return total


def _built_counts(adapter: Any) -> dict[str, int]:
    """Machines the runner itself built per product, from its observed receipts."""
    root = getattr(adapter, "root", None)
    counts: Counter = Counter()
    if root is None:
        return {}
    for path in Path(root, "evidence").glob("transaction-*.json"):
        try:
            receipt = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        candidate = receipt.get("candidate") or {}
        if receipt.get("status") == "observed" and candidate.get("family") in ("smelt", "assemble", "starter") \
                and candidate.get("product"):
            machines = [a for a in candidate["plan"]["phases"][0]["actions"]
                        if a["entity"] in ("electric-furnace", "assembling-machine-1", "assembling-machine-2")]
            counts[candidate["product"]] += max(1, len(machines))
    return dict(counts)


def _starters_submitted(adapter: Any) -> set[str]:
    """Plates whose direct starter was submitted (built or still under construction)."""
    root = getattr(adapter, "root", None)
    started: set[str] = set()
    for path in Path(root, "evidence").glob("transaction-*.json") if root else ():
        try:
            receipt = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        candidate = receipt.get("candidate") or {}
        if candidate.get("family") == "starter" and (receipt.get("submission") or {}).get("ok"):
            started.add(candidate["product"])
    return started


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
                   run_id: str, history: list, emit: Callable[[str], None], settings: Settings = Settings()) -> dict:
    """One planning decision: the policy chooses the mall site; ore and smelting land is reserved."""
    options = plan_options(facts)
    if not options:
        emit("PLAN: no mall site clears the reserved land; ore and smelting zones reserved, mall unzoned")
        return {"version": "laya-site-plan-v1", "mall_site": None, "zones": base_zones(facts)}
    menu = {"needs": [{"need_id": "plan:site", "condition": "unplanned", "options": [o["option_id"] for o in options]}],
            "options": options, "blocked": {}, "rejected": {}, "shortfalls": {}}
    state = build_state(facts, menu, board, phase, run_id, policy.model_ref, history, None, settings)
    choice = policy.choose(state, menu)
    chosen = next(o for o in options if o["option_id"] == choice["option_id"])
    distribution = choice.pop("distribution", None)
    _training_record(journal.parent / "decision-states.jsonl", run_id=run_id, index=record["index"], tick=facts["tick"],
                     phase=phase["phase_id"], authority=policy.authority, state=state,
                     options=[{"option_id": o["option_id"], "family": "plan", "summary": o["summary"],
                               "site_plan": o["site_plan"]} for o in options],
                     distribution=distribution, choice=choice, outcome={"status": "planned"})
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
        should_stop: Callable[[], bool] = lambda: False, status_path: Path | None = None,
        settings: Settings = Settings()) -> dict:
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
    records = journal.parent / "decision-states.jsonl"
    site_plan = load_plan(plan_path)
    if site_plan:
        _draw_zones(adapter, site_plan, emit)
    summary = {"decisions": 0, "failures": 0, "phases_reached": reached, "stop": "decision_budget"}
    stall = {"since": 0, "best_gap": math.inf}
    samples: list[tuple[int, Mapping, Mapping]] = []
    stock_demand: dict[str, float] = {}
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
            samples.append((facts["tick"], dict(facts["production"]), dict(facts["consumption"])))
            measurement = rolling_measurement(samples, settings.rate_window_ticks) or measurement
            if settings.max_game_ticks and facts["tick"] - samples[0][0] >= settings.max_game_ticks:
                summary["stop"] = "window_timeout"
                emit(f"WINDOW END: {settings.max_game_ticks} game ticks elapsed")
                break
            phase = current_phase(reached)
            built = _built_counts(adapter)
            board = compile_status(facts, phase["targets"], measurement, built, stock_demand) if phase else None
            while phase and phase_reached(board):
                stall.update(since=0, best_gap=math.inf)
                reached.append(phase["phase_id"])
                _append(journal, {"run_id": run_id, "event": "phase_reached", "phase": phase["phase_id"], "tick": facts["tick"]})
                emit(f"PHASE REACHED: {phase['phase_id']} {phase['name']} at tick {facts['tick']}")
                summary.setdefault("phase_ticks", {})[phase["phase_id"]] = facts["tick"] - samples[0][0]
                phase = current_phase(reached)
                board = compile_status(facts, phase["targets"], measurement, built, stock_demand) if phase else None
            if phase is None:
                summary["stop"] = "mission_complete"
                break
            if settings.until_phase and settings.until_phase in reached:
                summary["stop"] = "window_reached"
                emit(f"WINDOW END: reached {settings.until_phase}")
                break
            if site_plan is None:
                # Planning comes first: reserve land before anything is built.
                site_plan = _plan_decision(facts, board, phase, policy, record, journal, run_id, history, emit, settings)
                save_plan(plan_path, site_plan)
                _draw_zones(adapter, site_plan, emit)
                summary["decisions"] += 1
                continue
            menu = compile_menu(facts, board, lambda cs: adapter.query(list(cs))["validations"], seed + index,
                                site_plan=site_plan, starters=_starters_submitted(adapter),
                                per_need_checked=settings.per_need_checked, per_need_shown=settings.per_need_shown)
            # Items the checks found short become small demand targets on the next board.
            stock_demand = {item: settings.stock_rate for item in menu["shortfalls"]}
            state = build_state(facts, menu, board, phase, run_id, policy.model_ref, history, measurement, settings)
            choice = policy.choose(state, menu)
            distribution = choice.pop("distribution", None)
            chosen = next(o for o in menu["options"] if o["option_id"] == choice["option_id"])
            compact = compact_status(board)
            record.update(event="decision", tick=facts["tick"], phase=phase["phase_id"], status="pending",
                          board=compact, menu={n["need_id"]: n["options"] for n in menu["needs"]},
                          rejected=menu["rejected"], shortfalls=menu["shortfalls"], blocked=menu["blocked"],
                          land_rejections=menu.get("land_rejections"), choice=choice,
                          no_legal_build=not any(o["family"] != "wait" for o in menu["options"]),
                          omitted_needs=menu.get("omitted_needs"),
                          summary=chosen["summary"])
            _append(journal, record)  # Durable before any mutation.
            emit(f"DECISION {index} phase={phase['phase_id']} tick={facts['tick']} need={choice['need_id']} "
                 f"p={choice['probability']:.3f} authority={policy.authority}"
                 + (f" rules={','.join(sorted(set(choice['rules'])))}" if choice.get("rules") else "")
                 + f": {chosen['summary']}")
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
            no_legal_build = not any(o["family"] != "wait" for o in menu["options"])
            training = {"run_id": run_id, "index": index, "tick": facts["tick"], "phase": phase["phase_id"],
                        "no_legal_build": no_legal_build,
                        "authority": policy.authority, "state": state, "options": [_option_record(o) for o in menu["options"]],
                        "blocked": menu["blocked"], "land_rejections": menu.get("land_rejections"),
                        "distribution": distribution, "choice": choice}
            try:
                receipt = adapter.execute(choice["option_id"], menu["options"], measure_ticks)
            except Exception as error:
                _training_record(records, **training, outcome={"status": "failed", "error_type": type(error).__name__,
                                                               "error": str(error)[:500]})
                raise
            window = receipt["measurement"]  # This decision's own window; the board uses rolling rates.
            after = receipt.get("after")
            if after:
                samples.append((after["tick"], dict(after["production"]), dict(after["consumption"])))
                measurement = rolling_measurement(samples, settings.rate_window_ticks) or measurement
            record.update(status=receipt["status"], measured_per_s={
                k: round(v["per_second"], 3) for k, v in window["production"].items() if v["per_second"]})
            _append(journal, record)
            _training_record(records, **training, outcome={
                "status": receipt["status"], "window_ticks": window["window_ticks"],
                "measured_per_s": record["measured_per_s"], "construction": receipt.get("construction")})
            emit(f"  OUTCOME: {receipt['status']} over {window['window_ticks']} ticks; produced "
                 + (", ".join(f"{k}={v}/s" for k, v in sorted(record["measured_per_s"].items())) or "nothing"))
            history.append({"decision_id": f"decision.{index}", "tick": facts["tick"],
                            "summary": record["summary"][:120],
                            "outcome": "completed" if receipt["status"] == "observed" else "pending"})
            summary["decisions"] += 1
            failures = 0
            # Stall: no need-serving build, no phase and no goal-gap improvement for STALL_DECISIONS decisions.
            gap = _goal_gap(compile_status(facts, phase["targets"], measurement, built))
            # Untagged coverage serves no current need, so it does not count as progress.
            served = chosen["family"] != "wait" and chosen.get("need_id") != "coverage"
            progressed = (served and receipt["status"] == "observed") or gap < stall["best_gap"] * 0.95
            stall["best_gap"] = min(stall["best_gap"], gap)
            stall["since"] = 0 if progressed else stall["since"] + 1
            if stall["since"] >= settings.stall_decisions:
                summary["stop"] = "stalled"
                emit(f"STALLED: {settings.stall_decisions} decisions without a new build or goal progress; "
                     f"bottleneck {' | '.join(compact['bottleneck']) or 'none'}; blocked {blocked or 'none'}")
                break
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
