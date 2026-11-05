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

import copy
import json
import math
import random
import re
from contextlib import contextmanager
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
from planners.plan_validation import actions, validate_build_plan
from training.laya_factory_status import apply_to_state, compact_status, compile_status, makeable
from training.laya_mission import MISSION_ID, current_phase, phase_reached
from training.laya_guided_policy import load_preferences, matches, option_features
from training.laya_power import POLES, power_variants, world_obstacles
from training.laya_experiment_context import reservation_view
from training.laya_site_plan import (base_zones, draw_command, load_plan, plan_options, reservation_reason,
                                    ring_expand_candidates, save_plan)
from training.laya_lines import clear_of, line_candidates
from training.laya_starters import STARTER_ORE, starter_bill, starter_candidates
from training.laya_saved_world import (SAVED_WORLD_GHOST_FIELDS, SavedWorldAdapter, SavedWorldError,
                                       compile_candidates, decision_state, evaluate_required_checks,
                                       required_check_reason)
from training import laya_mall_slots as mall_slots
from training.laya_mall_block import mall_cell_candidates

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
    request_multiplier: int | None = None
    dependency_context: bool = False
    # Gated, rotating mall slots (training/laya_mall_slots.py). Default since 2026-10-05: C2 was
    # 0/141 and 0/186 without them, 40/173 with them (data/laya-experiments/variety-20261005).
    mall_slots: bool = True
    land_model: str = "patch"  # "grid": 64-tile parcels on a corridor lattice (training/laya_land_grid.py)
    mall_gate_scale: float = 1.0
    mall_buildable_multiplier: int = 5
    context_view: str = 'full'
    reservation_mode: str = 'strict'
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
EXTRA_RECIPES = ("big-electric-pole", "iron-stick", "fast-inserter")  # fast-inserter: line output taps
# Idle furnaces return no current recipe; report the previous one so built-but-starved
# furnaces stay attributed to their product.
RECIPE_LINE = 'if e.type~="mining-drill" then local recipe=e.get_recipe(); m.recipe=recipe and recipe.name end'
RECIPE_LINE_LIVE = ('if e.type~="mining-drill" then local recipe=e.get_recipe(); m.recipe=recipe and recipe.name; '
                    'if not m.recipe and e.type=="furnace" then local ok,prev=pcall(function() return e.previous_recipe end); '
                    'if ok and prev then local n=prev.name; if type(n)~="string" then n=n and n.name end; m.recipe=n end end end')
COLLISION_RETURN = 'then return "collision" end'
# Live: the refused entity, its tile, and what is there (tile name plus non-ore entity types, e.g.
# "water" or "grass-1|tree+cliff"), so refusals the facts cannot explain become evidence. Minable
# rocks read as "rock" (the saved-world check clears trees and rocks; see clearable_blocker).
COLLISION_RETURN_LIVE = ('then return "collision:"..a.entity.."@"..p.x..","..p.y.."#"..(function() '
                         'local t=surface.get_tile(p.x,p.y) local kinds={} '
                         'for _,e in pairs(surface.find_entities_filtered{area={{p.x-2,p.y-2},{p.x+2,p.y+2}}}) do '
                         'if e.type~="resource" and e.type~="character" then '
                         'kinds[(e.type=="simple-entity" and clearable_obstacle(e)) and "rock" or e.type]=true end end '
                         'local names={} for k in pairs(kinds) do names[#names+1]=k end table.sort(names) '
                         'return ((t and t.valid and t.name) or "?")..(#names>0 and ("|"..table.concat(names,"+")) or "") '
                         'end)() end')
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
        # Optional capability (tools/optimizer_block_runtime.OptimizerBlocks), injected by the CLI; None = off.
        self.optimizer_blocks = None
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
        # Name the refused entity and where, so journals separate relay-pole from machine collisions
        # and the refused tile can be learned (split_refused_sites).
        if COLLISION_RETURN not in self.lua_source:
            raise SavedWorldError("saved-world helper changed; collision check not found")
        self.lua_source = self.lua_source.replace(COLLISION_RETURN, COLLISION_RETURN_LIVE)

    def configure(self, actions: list[dict]) -> dict:
        """Reconfigure existing entities through the mod's configure_entity (no placement, no items)."""
        from training.laya_saved_world import build_layout_authorization, load_json
        plan = {"surface": self.surface, "force": self.force, "atomic": True,
                "phases": [{"name": "laya-configure", "actions": actions}]}
        return load_json(self.bridge.build_layout(build_layout_authorization([plan]), plan, timeout=30))

    def execute(self, selected, presented, measure_ticks=1800, check_verifier=None):
        candidate = next((c for c in presented if c["option_id"] == selected), None)
        if candidate and candidate.get("family") == "retarget":
            return mall_slots.execute_retarget(self, candidate, measure_ticks)
        if candidate and candidate.get("family") == "optimizer-block" and check_verifier is None:
            # Laya's selection goes through the capability's verified, clock-paused transaction; the verified
            # adapter path below is only reached from inside it (with its own verifier).
            if self.optimizer_blocks is None:
                raise SavedWorldError("optimizer blocks are not enabled for this runner")
            try:
                return self.optimizer_blocks.execute(self, candidate, selected, measure_ticks)
            except SavedWorldError as error:
                if "ghost submission failed" in str(error):
                    self._close_failed_submissions()
                raise
        try:
            if check_verifier is None:
                return super().execute(selected, presented, measure_ticks)
            return super().execute(selected, presented, measure_ticks, check_verifier=check_verifier)
        except SavedWorldError as error:
            if "ghost submission failed" in str(error):
                self._close_failed_submissions()
            raise

    def _close_failed_submissions(self) -> None:
        """Close only executor failures known to occur before placement."""
        from training.laya_layout_stream import atomic_json
        blocked_path = self.root / "evidence" / BLOCKED_FOOTPRINTS
        blocked = json.loads(blocked_path.read_text()) if blocked_path.exists() else []
        for path in (self.root / "evidence").glob("transaction-*.json"):
            receipt = json.loads(path.read_text())
            submission = receipt.get("submission") or {}
            if (receipt.get("status") not in {"pending", "submission_failed"} or submission.get("ok") is not False
                    or submission.get("error") not in {"atomic_footprint_blocked", "direct_construction_forbidden"}):
                continue
            receipt.update(status="rejected", reason=f"submission_failed:{submission.get('error')}")
            atomic_json(path, receipt)
            blocked += [{"entity": f["entity"], "position": f["position"]}
                        for f in submission.get("placement_failures") or [] if f.get("position")]
        blocked_path.write_text(json.dumps(blocked) + "\n")

    def tick(self) -> int:
        return int(json.loads(self.bridge.command("/sc rcon.print(game.tick)")))

    def survey(self, origin: tuple[int, int], radius: int) -> dict:
        """Resources per land-grid parcel over a wide square (read-only, a few chunked commands)."""
        from training.laya_land_grid import survey
        return survey(self.bridge.command, origin, radius)

    def pause(self) -> int:
        """Pause the game clock and return the tick it is frozen at (fails closed if it keeps moving)."""
        self.bridge.command('/sc game.tick_paused=true; game.ticks_to_run=0; rcon.print("paused")')
        previous = None
        for _ in range(30):
            now = self.tick()
            if now == previous:
                return now
            previous = now
            time.sleep(0.1)
        raise SavedWorldError("game clock did not pause")

    @contextmanager
    def clock_paused(self):
        """Transaction boundary for real-time mutation: no tick elapses between a re-query and its submission.

        Yields the frozen tick and always restores the live clock, including on failure.
        """
        tick = self.pause()
        try:
            yield tick
        finally:
            self.go_live()

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


def requested_items(candidate: Mapping) -> dict[str, int]:
    """Items the build's requester chests will pull from the network once built (beyond its bill)."""
    draws: dict[str, int] = {}
    for phase in (candidate.get("plan") or {}).get("phases", []):
        for action in phase["actions"]:
            sections = [{"requests": action.get("logistic_requests") or []}] + list(action.get("logistic_sections") or [])
            for section in sections:
                for request in section["requests"]:
                    count = int(request.get("count", 0)) * int(section.get("multiplier", 1))
                    draws[request["name"]] = draws.get(request["name"], 0) + count
    return draws


def planned_sites(facts: Mapping, plan: Mapping | None, starters: frozenset | set = frozenset()) -> Mapping:
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
    # After a plate's starter, its next capacity is a smelting line whose taps need logistic
    # coverage: those tap sites count as the plate's sites for coverage credit.
    from training.laya_lines import tap_sites
    kept += [{"item": plate, "position": {"x": x, "y": y}} for plate in sorted(starters)
             for x, y in tap_sites(plan, plate)]
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


def _compile(facts: Mapping, products: set[str], seed: int, request_multiplier: int | None = None) -> list[dict]:
    """Generator candidates for only the products a need can use (speed, not a choice)."""
    recipes = {k: v for k, v in facts["recipes"].items() if k in products}
    candidates = compile_candidates({**facts, "recipes": recipes}, seed)
    if request_multiplier is not None:
        from training.laya_experiment_context import with_request_multiplier
        candidates = [with_request_multiplier(c, request_multiplier) for c in candidates]
    return candidates


def _candidate_funding_shortfalls(candidate: Mapping) -> dict[str, int]:
    """Validate the persisted allocation facts attached to a partial hold."""
    if "funding_status" not in candidate:
        return {}
    funding, bill = candidate["funding_status"], candidate.get("bill")
    if (not isinstance(funding, Mapping) or set(funding) != {"state", "reserved", "shortfalls"}
            or funding["state"] != "supply_wait" or not isinstance(bill, Mapping) or not bill
            or not isinstance(funding["reserved"], Mapping) or not isinstance(funding["shortfalls"], Mapping)
            or any(not isinstance(item, str) or not item or type(count) is not int or count <= 0
                   for item, count in bill.items())
            or any(item not in bill or type(count) is not int or count < 0 or count > bill[item]
                   for item, count in funding["reserved"].items())
            or any(not isinstance(item, str) or not item or type(count) is not int or count <= 0
                   for item, count in funding["shortfalls"].items())):
        raise ValueError("invalid held allocation")
    missing = {item: count - funding["reserved"].get(item, 0) for item, count in bill.items()
               if count > funding["reserved"].get(item, 0)}
    if not missing or dict(funding["shortfalls"]) != missing:
        raise ValueError("inconsistent held allocation")
    return missing


def _placement_bill(candidate: Mapping) -> dict[str, int]:
    """Entities the candidate's plan places (the validator returns no bill when an item is short)."""
    bill: Counter = Counter()
    for phase in (candidate.get("plan") or {}).get("phases", []):
        for action in phase.get("actions", []):
            if action.get("action_type") in {"place_entity", "place_ghost", "place_ghost_no_reconcile"}:
                bill[str(action["entity"])] += 1
    return dict(bill)


def _available(facts: Mapping, candidate: Mapping, item: str) -> int:
    """Unreserved network stock of ``item`` for the candidate's network (all networks if it names none)."""
    networks = facts.get("networks") or {}
    own = networks.get(str(candidate.get("network")))
    return sum(int(n.get("items", {}).get(item, 0)) - int((n.get("reserved") or {}).get(item, 0))
               for n in ([own] if own else networks.values()))


def compile_menu(facts: Mapping, board: Mapping, validate: Validator, seed: int, *,
                 site_plan: Mapping | None = None, starters: frozenset | set = frozenset(),
                 per_need_checked: int = 6, per_need_shown: int = 2,
                 retargets: Sequence[Mapping] = (), placed: Sequence[Mapping] = (),
                 request_multiplier: int | None = None, reservation_mode: str = 'strict',
                 additional_candidates: Sequence[Mapping] = (), observe_unlinked: bool = False,
                 additional_observations: Mapping | None = None,
                 external_check: Callable[[Mapping, Mapping], tuple[str | None, Mapping | None]] | None = None,
                 external_shortfalls: Mapping[str, int] | None = None) -> dict:
    """Group legal options by the unmet need they serve.

    Each sampled build is offered with its power alternatives (training/laya_power.py).
    Construction items that legal checks report missing (``item_shortfall``)
    become ``stock:<item>`` needs served by assembling that item.  Sampling per
    need is seeded exploration of sites; it never ranks needs or options.

    ``external_check`` is the opt-in capability for external (optimizer) candidates: it re-runs their
    required checks on the same query that just validated them and returns ``(reason, evidence)``.
    Without it external candidates are observation only, exactly as before.

    ``external_shortfalls`` (item -> units lacking) is a build the provider could not offer because
    the network cannot fund it; it counts as one more blocked candidate, so its items get stock needs.
    """
    rng = random.Random(seed)
    if additional_observations is not None and not isinstance(additional_observations, Mapping):
        raise ValueError("additional observations must be a mapping")
    additional_observations = additional_observations or {}
    from training.laya_experiment_context import reservation_view
    land_plan = reservation_view(site_plan, reservation_mode)
    unmet = {n["item"] for n in board["needs"] if n["condition"] != "satisfied"}
    known_needs = {n["item"] for n in board["needs"]}
    proposed = _compile(facts, unmet, seed, request_multiplier)
    # Iron, copper and stone start with direct starters (drills feeding furnaces); the
    # generator's single-drill-into-chest mines and requester-fed furnaces are not offered.
    proposed = [c for c in proposed if not ((c["family"] == "smelt" and c.get("product") in STARTER_ORE)
                                            or (c["family"] == "mine" and c.get("product") in STARTER_ORE.values()))]
    plates = {plate for plate, ore in STARTER_ORE.items() if plate in unmet or ore in unmet}
    # Sites must clear what is already placed: machines, poles, ports, and Laya's own submitted builds.
    obstacles = world_obstacles(facts) + list(placed)
    proposed += starter_candidates(facts, site_plan, plates, set(starters), obstacles=obstacles)
    # Mall cells anywhere in the mall block (the shared generator only offers fixed sites per roboport).
    proposed += mall_cell_candidates(facts, site_plan, unmet, obstacles=obstacles)
    # After a plate's starter, more capacity comes from smelting lines (6 or 18 furnaces), added in parallel.
    line_diagnostics = {}
    proposed += line_candidates(facts, site_plan, {p for p in plates if p in starters}, obstacles=obstacles,diagnostics=line_diagnostics)
    proposed += ring_expand_candidates(facts, site_plan)
    # Coverage hops clear placed and refused footprints like every other family; the generators'
    # fixed hops and the ring know no obstacles, so a refused roboport site was offered forever.
    proposed = [c for c in proposed if c["family"] != "expand" or clear_of(_build_only(c)["phases"][0]["actions"], obstacles)]
    # Power relays route around the same obstacles (a refused pole tile is nudged, not re-offered).
    power_facts = {**facts, "layout_obstacles": [*(facts.get("layout_obstacles") or ()), *placed]}
    pre_rejected: dict[str, Counter] = defaultdict(Counter)
    land_examples: dict[str, list] = defaultdict(list)

    def on_free_land(candidates: list[dict], key_of: Callable[[dict], str]) -> list[dict]:
        """Land check on the build itself before sampling (the generator's relays are replaced later)."""
        if not site_plan:
            return candidates
        kept = []
        for candidate in candidates:
            detail: list = []
            reason = reservation_reason(land_plan, {**candidate, "plan": _build_only(candidate)}, detail)
            if reason:
                key = key_of(candidate)
                pre_rejected[key][reason] += 1
                if len(land_examples[key]) < 3:  # Exact rejected entities for the journal.
                    land_examples[key].append(detail[0])
            else:
                kept.append(candidate)
        return kept

    reach_facts = planned_sites(facts, site_plan, starters)
    proposed = on_free_land(proposed, lambda c: _need_key(reach_facts, c, unmet))
    by_need: dict[str, list] = defaultdict(list)
    for candidate in proposed:
        need = need_of(candidate)
        # A coverage hop that reaches (or closes on) an unmet resource serves it.
        if need == "coverage" or need in unmet:
            by_need[_need_key(reach_facts, candidate, unmet)].append(candidate)

    rejected: Counter = Counter()
    shortfalls: Counter = Counter()  # Blocked candidates per short item.
    unit_shortfalls: dict[str, int] = {}  # Most units of the item any blocked candidate lacks (bill - stock).
    pending_units: dict[str, int] = {}  # Most units of the item any blocked candidate needs in total.

    def note_units(item: str, candidate: Mapping, bill: Mapping, missing: Mapping | None = None) -> None:
        needed = int(bill.get(item, 0))
        lacking = int((missing or {}).get(item) or max(1, needed - _available(facts, candidate, item)))
        if needed:
            pending_units[item] = max(pending_units.get(item, 0), needed)
        unit_shortfalls[item] = max(unit_shortfalls.get(item, 0), lacking)

    reasons: dict[str, Counter] = defaultdict(Counter)
    external_results = []
    external_needs = set()
    admitted_external: list[tuple[str, dict]] = []
    seen_external = {c["option_id"] for c in proposed}
    for candidate in additional_candidates:
        if (not isinstance(candidate, Mapping) or not isinstance(candidate.get("option_id"), str)
                or not candidate["option_id"] or not isinstance(candidate.get("product"), str)
                or not candidate["product"] or not isinstance(candidate.get("plan"), Mapping)):
            raise ValueError("additional candidate requires option ID, product and plan")
        option_id = candidate["option_id"]
        if option_id in seen_external:
            raise ValueError("additional candidate option ID is not unique")
        seen_external.add(option_id)
        unlinked = candidate["product"] not in unmet
        if unlinked and ((not observe_unlinked and external_check is None) or candidate["product"] in known_needs):
            continue
        need = None if unlinked else f"item:{candidate['product']}"
        if unlinked and external_check is not None:
            need = f"optimizer:{candidate['product']}"  # Selectable though the board has no such need; Laya may still choose it.
        # This path is observation only until it can recheck the binding and
        # ledger immediately before dispatch and validate the entire site.
        required_reason = required_check_reason(candidate, external=True)
        plan = candidate["plan"]
        validation = None
        missing = {}
        admitted = None
        try:
            missing = _candidate_funding_shortfalls(candidate)
        except (ValueError, TypeError, KeyError):
            reason = "external_funding_invalid"
        else:
            try:
                validate_build_plan(plan)
                placements = list(actions(plan))
                if (plan.get("surface") != "nauvis" or plan.get("force") != "player" or plan.get("atomic") is not True
                        or not placements or any(a.get("action_type") != "place_ghost"
                                                 or set(a) - SAVED_WORLD_GHOST_FIELDS for a in placements)):
                    raise ValueError("external candidate requires explicitly scoped funded ghosts")
            except (ValueError, TypeError, KeyError):
                reason = "external_plan_invalid"
                missing = {}
            else:
                land_reason = (reservation_reason(land_plan, {**candidate, "plan": _build_only(candidate)})
                               if external_check is not None and site_plan else None)
                if land_reason:
                    reason = land_reason  # Land reserved for another purpose is a hard rule here too.
                    missing = {}
                elif required_reason in {"invalid_required_checks", "optimizer_required_checks_missing"}:
                    reason = required_reason
                    missing = {}
                else:
                    results = validate([copy.deepcopy(candidate)])
                    validation = results.get(option_id) if isinstance(results, Mapping) else None
                    if not isinstance(validation, Mapping):
                        reason = "external_validation_missing"
                        validation = None
                    elif validation.get("reason") is not None and not isinstance(validation["reason"], str):
                        reason = "external_validation_invalid"
                    elif validation.get("reason"):
                        reason = validation["reason"]
                    elif "bill" in candidate and validation.get("bill") != candidate["bill"]:
                        reason = "construction_bill_changed"
                    elif missing:
                        reason = "construction_reservation_shortfall"
                    elif external_check is not None:
                        reason, evidence = external_check(candidate, validation)
                        if not reason and candidate.get("check_evidence") is not None and evidence != candidate["check_evidence"]:
                            reason = "required_check_facts_changed"  # A fact the offer relied on moved since it was composed.
                        if not reason:
                            admitted = {**candidate, "check_evidence": evidence, "bill": validation.get("bill") or candidate["bill"],
                                        "draws": requested_items(candidate), "need_id": need}
                    else:
                        reason = required_reason or "external_dispatch_unimplemented"
        if admitted is not None:
            admitted_external.append((need, admitted))
        elif not unlinked or external_check is not None:
            external_needs.add(need)
            reasons[need][reason] += 1
            rejected[reason.split(":", 1)[0]] += 1
            missing_items = set(missing)
            if reason.startswith("item_shortfall:"):
                missing_items.add(reason.split(":", 1)[1])
            for item in missing_items:
                shortfalls[item] += 1
                note_units(item, candidate, candidate.get("bill") or _placement_bill(candidate), missing)
        summary = {"option_id": option_id, "need_id": need, "reason": reason,
                   "required_check_reason": required_reason, "dispatch_reason": "external_dispatch_unimplemented"}
        if admitted is not None:
            summary.update(selectable=True, dispatch_reason=None, required_check_reason=None)
        if unlinked and external_check is None:
            summary["demand_status"] = "not_in_status_board"
            summary["product"] = candidate["product"]
        if option_id in additional_observations:
            model_demand = additional_observations[option_id]
            if not isinstance(model_demand, Mapping):
                raise ValueError("additional demand observation must be a mapping")
            summary["model_demand"] = copy.deepcopy(model_demand)
        if validation is not None:
            summary["validation"] = copy.deepcopy(validation)
            snapshot = summary["validation"].get("fluid_topology_snapshot")
            if isinstance(snapshot, Mapping):
                # Journal a bounded digest; the raw observation is evidence for the validator, not the log.
                summary["validation"]["fluid_topology_snapshot"] = {
                    "sha256": sha256_hash(snapshot), "tick": snapshot.get("tick"), "complete": snapshot.get("complete"),
                    "entities": len(snapshot.get("entities") or ())}
            power_snapshot = summary["validation"].get("power_topology_snapshot")
            if isinstance(power_snapshot, Mapping):
                summary["validation"]["power_topology_snapshot"] = {
                    "sha256": sha256_hash(power_snapshot), "tick": power_snapshot.get("tick"),
                    "complete": power_snapshot.get("complete"), "poles": len(power_snapshot.get("poles") or ())}
        for key in ("funding_context", "funding_status", "stock_observation", "optimizer_binding", "optimizer_artifact_sha256", "bill"):
            if key in candidate:
                summary[key] = copy.deepcopy(candidate[key])
        external_results.append(summary)

    def check(groups):
        expanded = {}
        for need in sorted(groups):
            builds = list(groups[need])
            rng.shuffle(builds)
            variants = [v for c in builds[:per_need_checked] for v in power_variants(c, power_facts)]
            if builds and not variants:
                reasons[need]["no_power_layout"] += 1
            expanded[need] = []
            for variant in variants:
                # Land reservation is a hard rule: reject builds on another purpose's land.
                reason = reservation_reason(land_plan, variant) if site_plan else None
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
                        # The live check lists every item the network cannot cover (``missing``), so each
                        # becomes a stock need at once instead of surfacing one after another.
                        missing = result.get("missing") if isinstance(result.get("missing"), Mapping) else {}
                        bill = candidate.get("bill") or _placement_bill(candidate)
                        for item in {reason.split(":", 1)[1], *missing}:
                            shortfalls[item] += 1
                            note_units(item, candidate, bill, missing)
                    continue
                legal[need].append({**candidate, "bill": result.get("bill") or {}, "draws": requested_items(candidate),
                                     "need_id": need})
        return legal

    legal = check(by_need)
    for item, lacking in (external_shortfalls or {}).items():  # An unfunded external build, e.g. an optimizer block.
        unit_shortfalls[item] = max(unit_shortfalls.get(item, 0), int(lacking))
        if not makeable(item, facts.get("recipes") or {}):
            continue  # Visible as a shortfall, but no stock need: nothing enabled can make it yet.
        shortfalls[item] += 1
        pending_units[item] = max(pending_units.get(item, 0), _available(facts, {}, item) + int(lacking))
    for need, option in admitted_external:  # Only with the external_check capability: verified, selectable options.
        legal.setdefault(need, []).append(option)
    stock_groups: dict[str, list] = defaultdict(list)
    stock = [c for c in [*(_compile(facts, set(shortfalls), seed, request_multiplier) if shortfalls else ()),
                         *mall_cell_candidates(facts, site_plan, set(shortfalls), obstacles=obstacles)]
             if c["family"] == "assemble" and c.get("product") in shortfalls]
    for candidate in on_free_land(stock, lambda c: f"stock:{c['product']}"):
        if f"item:{candidate['product']}" in legal:
            continue  # Already a board need with its own options; one need per product.
        stock_groups[f"stock:{candidate['product']}"].append(candidate)
    legal.update(check(stock_groups))
    # Retargeting an existing mall slot serves the same needs without placing anything.
    for candidate in retargets:
        product = candidate["product"]
        need = f"item:{product}" if product in unmet else f"stock:{product}" if product in shortfalls else None
        if need:
            legal[need].append({**candidate, "need_id": need})

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
        shown = []
        # Verified site alternatives are all shown ("where" is the policy's choice, not a sampling outcome);
        # ordinary options of the same need are still sampled as before.
        blocks = [c for c in legal[need] if c["family"] == "optimizer-block"]
        rest = [c for c in legal[need] if c["family"] != "optimizer-block"]
        for c in blocks + (_sample({need: rest}, per_need, rng) if rest else []):
            if c["option_id"] not in seen and all(c["option_id"] != s["option_id"] for s in shown):
                shown.append(c)  # One copy per option id, within a need as well as across needs.
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
    # Needs that have options but also blocked candidates: the blocked reasons stay visible (top 4 per need).
    blocked_alternatives = {
        need: dict(sorted(reasons[need].items(), key=lambda kv: (-kv[1], kv[0]))[:4])
        for need in sorted(legal) if legal[need] and reasons.get(need)}
    # Needs with no legal option, and why: observation, not a remedy.
    blocked = {need: dict(reasons[need]) for need in sorted(set(by_need) | set(stock_groups) | set(pre_rejected) | external_needs)
               if need not in legal}
    for item in sorted(unmet):
        if f"item:{item}" not in by_need and f"item:{item}" not in legal and f"item:{item}" not in external_needs:
            blocked[f"item:{item}"] = {"no_candidate": 1}
    # Kit items the phase's still-unbuilt starters will need, so side builds can see what they would take.
    goal_reserve: dict[str, int] = {}
    for plate in STARTER_ORE:
        need = next((n for n in board["needs"] if n["item"] == plate), None)
        if need and need.get("goal_required_per_s", need.get("required", 0)) > 0 and plate not in starters:
            for name, count in starter_bill(plate).items():
                goal_reserve[name] = goal_reserve.get(name, 0) + count
    return {"needs": needs, "options": options, "proposed": len(proposed) + len(external_results), "blocked": blocked,
            "blocked_alternatives": blocked_alternatives,
            "rejected": dict(rejected), "shortfalls": dict(shortfalls), "unit_shortfalls": unit_shortfalls,
            "pending_units": pending_units, "goal_reserve": goal_reserve,
            "land_rejections": {k: v for k, v in land_examples.items() if k in blocked},
            "line_site_diagnostics": line_diagnostics,
            "additional_candidate_results": external_results,
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
    visible_history = [{k:v for k,v in h.items() if k!='experiment_outcome'} for h in history]
    state = decision_state(facts, menu["options"], run_id, model_ref, objective, visible_history, measurement)
    apply_to_state(state, board)
    model_demands = [{"option_id": item["option_id"], **item["model_demand"]}
                     for item in menu.get("additional_candidate_results", ()) if "model_demand" in item]
    if model_demands:
        state["observation_digest"]["optimizer_demand"] = model_demands[:8]
    power_fields = ("status", "reason", "snapshot_tick", "anchor_grid", "generator_type_present",
                    "planned_poles", "same_grid_candidates", "potential_other_grid_count", "reasons", "evidence_limit",
                    "dispatch_authorized")
    power_diagnostics = [{"option_id": item["option_id"], **{
        key: item["validation"]["power_topology_diagnostic"][key] for key in power_fields
        if key in item["validation"]["power_topology_diagnostic"]}}
        for item in menu.get("additional_candidate_results", ())
        if isinstance(item.get("validation"), Mapping)
        and isinstance(item["validation"].get("power_topology_diagnostic"), Mapping)]
    if power_diagnostics:
        state["observation_digest"]["optimizer_power"] = power_diagnostics[:8]
    blocks = [{"option_id": o["option_id"], **o["optimizer_facts"]} for o in menu["options"] if o.get("optimizer_facts")]
    if blocks:
        state["observation_digest"]["optimizer_blocks"] = blocks[:3]
    supply = {key: menu[key] for key in ("unit_shortfalls", "blocked_alternatives") if menu.get(key)}
    if supply:  # Per-item units short and why needs with options still have blocked candidates.
        state["observation_digest"]["supply_facts"] = supply
    if menu.get('line_site_diagnostics'):state['observation_digest']['line_site_diagnostics']=menu['line_site_diagnostics']
    if settings.dependency_context:
        from training.laya_experiment_context import dependency_context
        state['observation_digest']['dependency_context'] = dependency_context(facts, menu['options'], board)
    from training.laya_experiment_context import apply_view
    apply_view(state,facts,menu['options'],board,history,settings.context_view)
    if settings.reservation_mode!='strict':state['observation_digest']['reservation_experiment']=settings.reservation_mode
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
            "bill": option.get("bill"), "draws": option.get("draws"), "wait_ticks": option.get("wait_ticks"),
            "plan_hash": sha256_hash(plan) if plan else None, "coverage_reach": option.get("coverage_reach"),
            "slot": option.get("slot"), "next_slot": option.get("next_slot"),
            "entities": dict(Counter(a["entity"] for a in plan["phases"][0]["actions"])) if plan else None,
            **({"optimizer_facts": option["optimizer_facts"]} if option.get("optimizer_facts") else {})}


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
    # Oldest first: a retarget moves a slot only after the build that made it.
    for path in sorted(Path(root, "evidence").glob("transaction-*.json"), key=lambda p: p.stat().st_mtime):
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
        elif receipt.get("status") in ("observed", "applied") and candidate.get("family") == "retarget":
            counts[candidate["product"]] += 1  # The slot now makes this product, not its old one.
            old = (candidate.get("slot") or {}).get("from")
            if old and counts[old] > 0:
                counts[old] -= 1
    return dict(counts)


BLOCKED_FOOTPRINTS = "blocked-footprints.json"  # where the game refused an atomic build


REFUSED_SITE = re.compile(r"^(collision:([^@:]+))@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)(?:#(.*))?$")


UNBUILDABLE_TILE = re.compile(r"water|lava|space|void|out-of-map")  # terrain no robot can clear


def clearable_blocker(blocker: str | None) -> bool:
    """True when a live refusal's blocker ("tile|kind+kind") is only trees and minable rocks on buildable
    ground: construction robots clear those under a ghost, so the footprint is not blocked for good."""
    tile, _, kinds = (blocker or "").partition("|")
    return bool(kinds) and set(kinds.split("+")) <= {"tree", "rock"} and not UNBUILDABLE_TILE.search(tile)


def split_refused_sites(validations: Mapping[str, Mapping]) -> tuple[dict, list[dict]]:
    """Strip ``@x,y`` from live collision reasons; return the plain results and the refused footprints.

    The live check names the candidate's own entity that the world refused (not the thing in the
    way), so the footprint is where Laya may not place that entity again.
    """
    plain, refused = {}, []
    for option_id, result in validations.items():
        match = REFUSED_SITE.match(result.get("reason") or "") if isinstance(result, Mapping) else None
        if match:
            result = {**result, "reason": match.group(1)}
            # Trees and rocks alone are cleared by robots under the ghost, so they never block a footprint for good.
            if not clearable_blocker(match.group(5)):
                refused.append({"entity": match.group(2), "position": {"x": float(match.group(3)), "y": float(match.group(4))},
                                **({"blocker": match.group(5)} if match.group(5) else {})})
        plain[option_id] = result
    return plain, refused


def remember_refused_sites(root: Any, refused: Sequence[Mapping]) -> None:
    """Append footprints the live check refused to the run's blocked footprints, so every generator
    clears them from the next decision on (otherwise the same nearest sites are offered and refused forever)."""
    if not root or not refused:
        return
    path = Path(root, "evidence", BLOCKED_FOOTPRINTS)
    known = json.loads(path.read_text()) if path.exists() else []
    merged = known + [r for i, r in enumerate(refused) if r not in known and r not in refused[:i]]
    if len(merged) > len(known):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(merged) + "\n")


def _submitted_entities(adapter: Any) -> list[dict]:
    """Entities of every build Laya submitted (belts, inserters, chests are not in the facts), plus
    footprints the game refused at submission, so those sites are not offered again."""
    root = getattr(adapter, "root", None)
    placed: list[dict] = []
    blocked = Path(root, "evidence", BLOCKED_FOOTPRINTS) if root else None
    if blocked and blocked.exists():
        placed += json.loads(blocked.read_text())
    for path in Path(root, "evidence").glob("transaction-*.json") if root else ():
        try:
            receipt = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if (receipt.get("submission") or {}).get("ok"):
            placed += [{"entity": a["entity"], "position": a["position"]}
                       for phase in receipt["candidate"]["plan"]["phases"] for a in phase["actions"]]
    return placed


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


def _journal_events(blocks: Any, journal: Path, run_id: str) -> None:
    """Persist the optimizer capability's funnel events (never_offered ... measured)."""
    for event in blocks.drain_events():
        _append(journal, {"run_id": run_id, **event})


def _plan_decision(facts: Mapping, board: Mapping, phase: Mapping, policy: "Policy", record: dict, journal: Path,
                   run_id: str, history: list, emit: Callable[[str], None], settings: Settings = Settings(),
                   surveyed: Mapping | None = None) -> dict:
    """One planning decision: the policy chooses the mall site; ore and smelting land is reserved."""
    if settings.land_model == "grid":
        from training.laya_land_grid import grid_plan, grid_plan_options
        options = grid_plan_options(facts, surveyed)
        if not options:
            emit("PLAN: no mall site clears the grid corridors; parcels assigned, mall unzoned")
            return grid_plan(facts, surveyed=surveyed)
    else:
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
        settings: Settings = Settings(), initial_reached: Sequence[str] = (),
        candidate_provider: Callable[[Any, Mapping, Mapping], Sequence[Mapping]] | None = None) -> dict:
    """Observe -> board -> menu -> choose -> execute -> measure, journaling each step.

    ``decisions=None`` runs until the mission completes, the safe-stop rule
    fires or ``should_stop`` turns true.  Any failure is journaled and the
    loop continues; it stops safely after ``MAX_CONSECUTIVE_FAILURES`` in a
    row.  It never falls back to another decision maker.
    """
    # Opt-in capability (``--optimizer-blocks``): the provider also verifies, executes and reconciles its blocks.
    blocks = candidate_provider if getattr(candidate_provider, "execute", None) else None
    if blocks is not None:
        _append(journal, {"run_id": run_id, "event": "capability", "optimizer_blocks": True})
    reached: list[str] = list(initial_reached)
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
    stall = {"since": 0, "best_gap": math.inf, "best_short": math.inf}
    samples: list[tuple[int, Mapping, Mapping]] = []
    stock_demand: dict[str, float] = {}
    pending_units: dict[str, int] = {}  # Largest per-item need of blocked builds, from the previous menu.
    gates: dict = {}  # Mall slot gates applied this run: position -> (product, gated).
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
            if blocks is not None:
                blocks.reconcile(adapter, facts)  # Receipt- and entity-based; never resends.
                _journal_events(blocks, journal, run_id)
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
                stall.update(since=0, best_gap=math.inf, best_short=math.inf)
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
                surveyed = None
                if settings.land_model == "grid" and hasattr(adapter, "survey"):
                    from training.laya_land_grid import PITCH, PLAN_RADIUS, grid_origin
                    from training.laya_site_plan import _base
                    try:  # One wide survey per run; planning falls back to the local scan without it.
                        surveyed = adapter.survey(grid_origin(_base(facts)), (PLAN_RADIUS + 1) * PITCH)
                        (journal.parent / "land-survey.json").write_text(json.dumps(surveyed) + "\n")
                        emit(f"LAND SURVEY: {len(surveyed)} parcels with resources within "
                             f"{(PLAN_RADIUS + 1) * PITCH} tiles")
                    except Exception as error:
                        emit(f"LAND SURVEY FAILED: {type(error).__name__}: {str(error)[:160]}")
                site_plan = _plan_decision(facts, board, phase, policy, record, journal, run_id, history, emit, settings,
                                           surveyed)
                save_plan(plan_path, site_plan)
                _draw_zones(adapter, site_plan, emit)
                summary["decisions"] += 1
                continue
            retargets = (mall_slots.sync(adapter, facts, board, stock_demand, gates, emit,
                                        gate_scale=settings.mall_gate_scale,buildable_multiplier=settings.mall_buildable_multiplier,
                                        pending_units=pending_units)
                         if settings.mall_slots and hasattr(adapter, "configure") else [])
            last_query: dict = {}

            def validate_candidates(candidates):
                prepare = getattr(candidate_provider, "prepare_validation", None)
                requested = prepare(candidates) if prepare is not None else list(candidates)
                snapshot = adapter.query(requested)
                last_query["snapshot"] = snapshot  # The required checks read this same query.
                values, refused = split_refused_sites(snapshot["validations"])
                remember_refused_sites(getattr(adapter, "root", None), refused)
                diagnostic = getattr(candidate_provider, "validate", None)
                if diagnostic is not None:
                    values = diagnostic(adapter, facts, candidates, snapshot)
                if type(snapshot.get("tick")) is int:
                    return {key: {**value, "observed_tick": snapshot["tick"]} for key, value in values.items()}
                return values
            external_check = None
            if blocks is not None:
                if hasattr(blocks, "set_land_check"):
                    blocks.set_land_check(lambda pseudo: reservation_reason(
                        reservation_view(site_plan, settings.reservation_mode), {"family": "optimizer-block", "plan": pseudo}))
                external_check = lambda candidate, entry: evaluate_required_checks(  # noqa: E731
                    candidate, blocks.verify, adapter, entry, last_query["snapshot"], external=True)
            external = candidate_provider(adapter, facts, board) if candidate_provider else ()
            describe = getattr(candidate_provider, "describe", None)
            demand_observations = describe(facts, board, external) if describe is not None else {}
            last = getattr(candidate_provider, "last", None) or {}
            unfunded = (last.get("detail") or {}).get("short") if last.get("reason") == "unfunded" else None
            menu = compile_menu(facts, board, validate_candidates, seed + index,
                                site_plan=site_plan, starters=_starters_submitted(adapter), retargets=retargets,
                                placed=_submitted_entities(adapter),
                                per_need_checked=settings.per_need_checked, per_need_shown=settings.per_need_shown,
                                request_multiplier=settings.request_multiplier,reservation_mode=settings.reservation_mode,
                                additional_candidates=external,
                                observe_unlinked=bool(getattr(candidate_provider, "observe_unlinked", False)),
                                additional_observations=demand_observations, external_check=external_check,
                                external_shortfalls=unfunded)
            # Items the checks found short become small demand targets on the next board.
            stock_demand = {item: settings.stock_rate for item in menu["shortfalls"]}
            pending_units = menu.get("pending_units") or {}
            state = build_state(facts, menu, board, phase, run_id, policy.model_ref, history, measurement, settings)
            choice = policy.choose(state, menu)
            distribution = choice.pop("distribution", None)
            chosen = next(o for o in menu["options"] if o["option_id"] == choice["option_id"])
            compact = compact_status(board)
            record.update(event="decision", tick=facts["tick"], phase=phase["phase_id"], status="pending",
                          board=compact, menu={n["need_id"]: n["options"] for n in menu["needs"]},
                          rejected=menu["rejected"], shortfalls=menu["shortfalls"], blocked=menu["blocked"],
                          unit_shortfalls=menu.get("unit_shortfalls"), blocked_alternatives=menu.get("blocked_alternatives"),
                          land_rejections=menu.get("land_rejections"), choice=choice,
                          additional_candidate_results=menu["additional_candidate_results"],
                          no_legal_build=not any(o["family"] != "wait" for o in menu["options"]),
                          omitted_needs=menu.get("omitted_needs"),
                          summary=chosen["summary"])
            _append(journal, record)  # Durable before any mutation.
            if blocks is not None:
                blocks.observe_decision(index=index, tick=facts["tick"], menu=menu, chosen_id=choice["option_id"])
                _journal_events(blocks, journal, run_id)
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
                        "additional_candidate_results": menu["additional_candidate_results"],
                        "distribution": distribution, "choice": choice}
            try:
                receipt = adapter.execute(choice["option_id"], menu["options"], measure_ticks)
                if blocks is not None:
                    _journal_events(blocks, journal, run_id)
            except Exception as error:
                if blocks is not None:
                    _journal_events(blocks, journal, run_id)
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
                            "outcome": "completed" if receipt["status"] == "observed" else "pending",
                            "experiment_outcome": {"family":chosen['family'],'product':chosen.get('product'),
                                "status":receipt['status'],'produced':record['measured_per_s']}})
            summary["decisions"] += 1
            failures = 0
            # Stall: no need-serving build, no phase and no goal-gap improvement for STALL_DECISIONS decisions.
            gap = _goal_gap(compile_status(facts, phase["targets"], measurement, built))
            # Untagged coverage serves no current need, so it does not count as progress.
            # A retarget only re-plans an existing slot; it counts once the goal gap moves.
            served = chosen["family"] not in ("wait", "retarget") and chosen.get("need_id") != "coverage"
            # Funding blocked builds is progress too: fewer units short than ever before in this phase.
            short = sum((menu.get("unit_shortfalls") or {}).values())
            progressed = ((served and receipt["status"] == "observed") or gap < stall["best_gap"] * 0.95
                          or short < stall["best_short"])
            stall["best_gap"] = min(stall["best_gap"], gap)
            stall["best_short"] = min(stall["best_short"], short)
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
    if blocks is not None:
        summary["optimizer_blocks"] = {"funnel": dict(blocks.registry["funnel"]),
                                       "never_offered_reasons": dict(blocks.registry.get("never_offered_reasons", {})),
                                       "projects": {p["project_id"]: p["state"] for p in blocks.registry["projects"].values()}}
    _append(journal, {"run_id": run_id, "event": "summary", **summary})
    emit(f"SUMMARY: stop={summary['stop']} decisions={summary['decisions']} failures={summary['failures']} "
         f"phases={','.join(reached) or 'none'}")
    return summary
