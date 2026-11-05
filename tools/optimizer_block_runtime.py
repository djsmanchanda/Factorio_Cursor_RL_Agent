# Path: tools/optimizer_block_runtime.py | Purpose: Clean-runner capability that offers verified optimizer red-science blocks as selectable options, executes the selected one behind a clock-pause transaction boundary, and reconciles, measures and journals it.
"""Optimizer blocks as a Laya option (capability OFF unless ``--optimizer-blocks``).

Division of labour: this module supplies facts, legality and an execution path.
It never ranks a block against other work, never defers it to a stage and never
decides to build; Laya's policy chooses whether, when and where.

Real-time safety. The clean runner's world is unpaused, so a same-query snapshot
is not atomic with a later submission. This module uses an enforced transaction
boundary instead of relaxed tick checks: composition (catalog capture, survey,
binding, reservation, verification) and execution (stock revalidation, the
required-check re-verification against option-time evidence, and ghost
submission) each run with the game clock paused through
``LiveWorld.clock_paused``. Because no tick elapses inside the boundary, the
re-query that ``execute_optimizer_candidate`` performs immediately before
submitting ghosts describes exactly the state the ghosts are submitted into; any
change in the bound facts since the option was shown rejects the selection. The
submission tick is compared with the boundary tick afterwards; a mismatch
halts all further optimizer submissions.

Durability. Each selected block has a deterministic project and transaction
identity and a record in ``evidence/optimizer-blocks.json`` written before any
mutation. A block whose record is unresolved is never re-offered or resent;
recovery reads transaction receipts only.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from dataclasses import dataclass, replace
from pathlib import Path

from orchestrator.material_reservations import MaterialReservationLedger
from tools import optimizer_block_proposals as proposals
from tools.optimizer_block_proposals import PACK, TARGET_RATE, TrialError, _short
from tools.optimizer_saved_world import execute_optimizer_candidate
from training.laya_layout_stream import atomic_json
from training.laya_saved_world import SavedWorldError

REGISTRY = "optimizer-blocks.json"
VERSION = "optimizer-blocks-v1"
FAMILY = "optimizer-block"
IN_FLIGHT = frozenset({"selected", "submitted", "unresolved"})
TERMINAL_FAILED = frozenset({"dispatch_failed", "rejected"})
RESOLVED_RECEIPT = frozenset({"observed", "rejected"})
STAGES = ("never_offered", "offered_not_selected", "selected", "dispatch_failed", "construction_incomplete",
          "configured", "measured")


@dataclass
class Offer:
    """What this process holds for one currently offered block; the menu copy is never trusted."""
    candidate: dict
    bound: object
    episode: str
    verifier: object
    fluid_capture: dict
    network_anchor: dict
    origin: tuple
    project_id: str
    transaction_id: str


def unresolved_receipts(root):
    """Transaction receipts that are neither observed nor rejected (mirrors the executor's own gate)."""
    bad = []
    for path in sorted((Path(root) / "evidence").glob("transaction-*.json")):
        try:
            if path.is_symlink() or json.loads(path.read_text(encoding="utf-8")).get("status") not in RESOLVED_RECEIPT:
                bad.append(path.name)
        except (OSError, json.JSONDecodeError, AttributeError):
            bad.append(path.name)
    return bad


class OptimizerBlocks:
    """Candidate provider, verifier host, executor and reconciler for requester-fed optimizer blocks."""

    def __init__(self, root, *, run_id, game_version=None, margin=2, max_options=3, max_site_attempts=8,
                 warmup_ticks=1800, window_ticks=3600, min_ratio=0.9, max_build_ticks=7200):
        self.root, _manifest = proposals.verify_disposable(root)
        self.run_id, self.game_version, self.margin = run_id, game_version, margin
        self.max_options, self.max_site_attempts = max_options, max_site_attempts
        self.warmup_ticks, self.window_ticks = warmup_ticks, window_ticks
        self.min_ratio, self.max_build_ticks = min_ratio, max_build_ticks
        self.base_episode = "ob-" + hashlib.sha256(str(self.root).encode()).hexdigest()[:10]
        self.path = self.root / "evidence" / REGISTRY
        self.registry = self._load()
        self.offers: dict[str, Offer] = {}
        self.last: dict = {"reason": "not_evaluated", "detail": {}}
        self.halted: str | None = None
        self._base = None
        self._land_check = None
        self.observe_unlinked = True
        self._events: list[dict] = []

    # ------------------------------------------------------------------ registry

    def _load(self):
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != VERSION:
                raise TrialError("optimizer block registry has an unknown version")
            return data
        return {"version": VERSION, "base_episode": self.base_episode, "projects": {}, "funnel": dict.fromkeys(STAGES, 0),
                "never_offered_reasons": {}}

    def _save(self):
        self.path.parent.mkdir(exist_ok=True)
        atomic_json(self.path, self.registry)

    def _event(self, stage, **fields):
        self._events.append({"event": "optimizer_funnel", "stage": stage, **fields})

    def drain_events(self):
        """Funnel events since the last call; the runner journals them (the module never writes the journal)."""
        events, self._events = self._events, []
        return events

    def _count(self, stage, reason=None):
        self.registry["funnel"][stage] = self.registry["funnel"].get(stage, 0) + 1
        if reason:
            reasons = self.registry.setdefault("never_offered_reasons", {})
            reasons[reason] = reasons.get(reason, 0) + 1

    def projects(self, *states):
        return [p for p in self.registry["projects"].values() if not states or p["state"] in states]

    def blocking_reason(self):
        """Why no block may be offered or submitted now; ``None`` when nothing blocks. Unknown means blocked."""
        if self.halted:
            return self.halted
        if any(p["state"] == "unresolved" for p in self.projects()):
            return "unresolved_transaction"
        if self.projects("selected", "submitted"):
            return "in_flight"
        if unresolved_receipts(self.root):
            return "unresolved_transaction"
        return None

    # ------------------------------------------------------------------ runner hooks

    def set_land_check(self, check):
        """``check(pseudo_plan)`` returns a reservation reason or ``None``; wired by the runner each decision."""
        self._land_check = check

    @staticmethod
    def _pause(adapter):
        pause = getattr(adapter, "clock_paused", None)
        return pause() if pause else nullcontext(None)

    def _none(self, reason, **detail):
        self.last = {"reason": reason, "detail": detail}
        return []

    def __call__(self, adapter, facts, board):
        self.offers = {}
        try:
            return self._offer(adapter, facts, board)
        except Exception as error:  # Offering is observation: a failure is evidence, never a crash.
            return self._none("error", error=f"{type(error).__name__}: {_short(error)}")

    def _offer(self, adapter, facts, board):
        blocked = self.blocking_reason()
        if blocked:
            return self._none(blocked, projects=[p["project_id"] for p in self.projects("selected", "submitted", "unresolved")])
        needs = [n for n in board["needs"] if n.get("item") == PACK]
        if needs and all(n.get("condition") == "satisfied" for n in needs):
            return self._none("need_satisfied")
        if (facts.get("enabled_recipes") or {}).get(PACK) is not True:
            return self._none("recipe_not_enabled")  # unknown counts as not enabled
        ports = [p for p in facts.get("ports") or [] if p.get("energy", 0) > 0 and isinstance(p.get("network"), str)]
        if not ports:
            return self._none("no_roboport")
        ports.sort(key=lambda p: (p["network"], p["position"]["x"], p["position"]["y"]))
        candidates, rejected, unfunded, counts, stops = [], [], None, {}, []
        with self._pause(adapter):  # Composition needs one fixed tick: catalogs, survey and verification agree.
            fresh = adapter.query()
            if self._base is None:
                self._base = proposals.build_context(adapter, self.root, fresh, base_episode=self.base_episode,
                                                     game_version=self.game_version, port=ports[0])
            dispatched = {tuple(p["origin"]) for p in self.registry["projects"].values()}  # never re-offered
            share, extra = divmod(self.max_options, len(ports))
            for index, port in enumerate(ports):
                quota = share + (1 if index < extra else 0)
                if quota < 1 and index > 0:
                    break
                ctx = replace(self._base, port=port, network_anchor=dict(port["position"]), network_id=port["network"])
                result = proposals.propose_sites(
                    adapter, ctx, fresh, max_options=max(1, quota), max_attempts=self.max_site_attempts, margin=self.margin,
                    land_check=self._land_check, exclude=dispatched | {tuple(c["origin"]) for c in candidates},
                    identity_for=lambda o: proposals.site_identity(self.base_episode, o))
                rejected += result["rejected"]
                stops.append(result["stop"])
                for key, value in result["counts"].items():
                    counts[key] = counts.get(key, 0) + value
                if result["unfunded"]:
                    unfunded = {**(unfunded or {}), **result["unfunded"]}
                for offer in result["offers"]:
                    candidates.append(self._register(offer, ctx, fresh, board))
        if candidates:
            self.last = {"reason": None, "detail": {"offered": [c["option_id"] for c in candidates], "rejected": rejected}}
            return candidates
        if unfunded:
            return self._none("unfunded", short=unfunded)
        if rejected:
            return self._none("check_failed", rejected=rejected[:6], counts=counts)
        return self._none("no_site", counts=counts, stops=stops)

    def _register(self, offer, ctx, fresh, board):
        pristine = offer["candidate"]
        self.offers[pristine["option_id"]] = Offer(
            candidate=pristine, bound=offer["bound"], episode=offer["episode"], verifier=offer["verifier"],
            fluid_capture=offer["fluid_capture"], network_anchor=dict(ctx.network_anchor), origin=tuple(offer["origin"]),
            project_id=offer["project_id"], transaction_id=offer["transaction_id"])
        stock_rows = (pristine.get("stock_observation") or {}).get("items") or {}
        demand = proposals.operating_demand(ctx.recipe_catalog)
        network_stock = (fresh["networks"].get(ctx.network_id) or {}).get("items") or {}
        facts = {"project_id": offer["project_id"], "origin": list(offer["origin"]), "product": PACK,
                 "expected_output_per_s": float(TARGET_RATE),
                 "construction_bill": dict(pristine["bill"]),
                 "operating_demand_per_s": demand,
                 "operating_inputs_in_network": {item: int(network_stock.get(item, 0)) for item in demand},
                 "network_available_after_claims": {i: r["available_after_claims"] for i, r in stock_rows.items()},
                 "network_shortage_vs_bill": {i: r["shortfall_after_claims"] for i, r in stock_rows.items()
                                              if r["shortfall_after_claims"]},
                 "held_reservations": {i: r["project_ledger_hold"] for i, r in stock_rows.items()},
                 "competing_holds": {i: r["competing_ledger_holds"] for i, r in stock_rows.items()
                                     if r["competing_ledger_holds"]},
                 "network": ctx.network_id}
        summary = (f"Build requester-fed {PACK} block at ({offer['origin'][0]},{offer['origin'][1]}): "
                   f"{float(TARGET_RATE):g}/s, bill {sum(pristine['bill'].values())} items, draws "
                   + ", ".join(f"{i} {v:g}/s" for i, v in sorted(demand.items())))
        return {**pristine, "summary": summary[:500], "optimizer_facts": facts}

    def prepare_validation(self, candidates):
        return [self.offers[c["option_id"]].verifier.prepare(c) if c.get("option_id") in self.offers else c
                for c in candidates]

    def verify(self, adapter, candidate, entry, response):
        """Required-check verifier host: only the option ID is read from the menu; the bound copy is checked."""
        offer = self.offers.get(candidate.get("option_id"))
        if offer is None:
            return {name: {"reason": "candidate_not_bound", "facts": {}} for name in ("fluid_purity", "safe_infrastructure_attachment")}
        return offer.verifier(adapter, offer.candidate, entry, response)

    # ------------------------------------------------------------------ funnel

    def observe_decision(self, *, index, tick, menu, chosen_id):
        """Journal the funnel stage this decision reached for the block, before any dispatch."""
        shown = [o["option_id"] for o in menu["options"] if o.get("family") == FAMILY]
        if not shown:
            reason, detail = self.last["reason"] or "check_failed", dict(self.last["detail"])
            if self.offers:  # Offered by the provider but removed by the menu's own legality checks.
                results = [{"option_id": r["option_id"], "reason": r.get("reason")}
                           for r in menu.get("additional_candidate_results", ())]
                # No result at all: the menu skipped the block because the board has no unmet need for its product.
                reason, detail = ("check_failed", {"menu_results": results}) if results else ("need_satisfied", {})
            stage = "never_offered"
        elif chosen_id in shown:
            stage, reason, detail = "selected", None, {"option_id": chosen_id}
        else:
            stage, reason, detail = "offered_not_selected", None, {"offered": shown}
        self._count(stage, reason)
        self._save()
        self._event(stage, index=index, tick=tick, **({"reason": reason} if reason else {}), detail=detail)

    # ------------------------------------------------------------------ execution

    def execute(self, adapter, candidate, selected, measure_ticks):
        blocked = self.blocking_reason()
        if blocked:
            raise SavedWorldError(f"optimizer submission blocked: {blocked}")
        offer = self.offers.get(selected)
        if offer is None:
            raise SavedWorldError("optimizer selection is not a block this runner currently offers")
        project = {"project_id": offer.project_id, "transaction_id": offer.transaction_id, "episode": offer.episode,
                   "origin": list(offer.origin), "option_id": selected, "state": "selected",
                   "artifact_sha256": offer.bound.artifact_sha256, "plan_sha256": offer.bound.binding_sha256,
                   "bill": dict(offer.candidate["bill"]), "expected_per_s": float(TARGET_RATE)}
        self.registry["projects"][offer.project_id] = project
        self._save()  # Durable identity before any mutation: recovery reads receipts and never resends.
        try:
            with self._pause(adapter) as boundary:
                ledger = MaterialReservationLedger(adapter.root / "server/script-output", episode_id=offer.episode,
                                                   surface=adapter.surface, force=adapter.force)
                receipt = execute_optimizer_candidate(
                    adapter, offer.bound, ledger, episode_id=offer.episode, network_anchor=offer.network_anchor,
                    candidate=offer.candidate, selected=selected, measure_ticks=measure_ticks,
                    fluid_capture=offer.fluid_capture)
        except Exception as error:
            receipt_path = find_receipt(self.root, selected)
            if receipt_path is None:
                project.update(state="dispatch_failed", error=f"{type(error).__name__}: {_short(error)}")
                self._count("dispatch_failed")
                self._event("dispatch_failed", project_id=offer.project_id, reason=project["error"])
            else:  # Some receipt exists: reconcile decides from it, never from the exception.
                project.update(state="submitted", receipt=receipt_path.name,
                               error=f"{type(error).__name__}: {_short(error)}")
            self._save()
            raise
        submission = receipt.get("submission") or {}
        before_tick, submitted_tick = (receipt.get("before") or {}).get("tick"), submission.get("tick")
        boundary_ok = None if boundary is None else (before_tick == boundary and submitted_tick in (None, boundary))
        stored = find_receipt(self.root, selected)
        project.update(state="submitted", receipt=stored.name if stored else None,
                       submitted_tick=before_tick, boundary={"tick": boundary, "before_tick": before_tick,
                                                             "submission_tick": submitted_tick, "ok": boundary_ok})
        if boundary_ok is False:
            self.halted = "boundary_violation"
            project["boundary_violation"] = True
        self._save()
        return receipt

    # ------------------------------------------------------------------ reconcile and measure

    def reconcile(self, adapter, facts):
        """Advance every recorded block from receipts and its own entities; resends nothing."""
        for project in list(self.registry["projects"].values()):
            try:
                self._advance(adapter, facts, project)
            except Exception as error:
                project["last_error"] = f"{type(error).__name__}: {_short(error)}"
        self._save()

    def _advance(self, adapter, facts, project):
        state, tick = project["state"], facts["tick"]
        if state in ("selected", "submitted", "unresolved"):
            path = find_receipt(self.root, project["option_id"])
            if path is None:
                if state == "selected":
                    project["state"] = "unresolved"  # Interrupted before a receipt: never assumed unsent.
                return
            project["receipt"] = path.name
            receipt = json.loads(path.read_text(encoding="utf-8"))
            status = receipt.get("status")
            if status == "rejected" or status == "submission_failed":
                project.update(state="dispatch_failed", reason=receipt.get("reason") or status)
                self._count("dispatch_failed")
                self._event("dispatch_failed", tick=tick, project_id=project["project_id"], reason=project["reason"])
                return
            project["state"] = "submitted"
            if status != "observed":
                started = project.get("submitted_tick") or tick
                if tick - started > self.max_build_ticks and not project.get("construction_incomplete"):
                    project["construction_incomplete"] = tick
                    self._count("construction_incomplete")
                    self._event("construction_incomplete", tick=tick, project_id=project["project_id"],
                                receipt_status=status, elapsed_ticks=tick - started)
                return
            if (receipt.get("configuration") or {}).get("ok") is not True:
                return  # Observed but configuration not (yet) confirmed: stays unresolved, not configured.
            plan = receipt["candidate"]["plan"]
            state_now = proposals.block_state(adapter, proposals.state_specs(plan), paused=False)
            requests = proposals.confirm_requests(plan, state_now)
            if requests["mismatch"]:
                project.update(state="configuration_mismatch", requests=requests)
                return
            if not requests["all_match"]:
                return
            project.update(state="configured", configured_tick=state_now["tick"])
            self._count("configured")
            self._event("configured", tick=state_now["tick"], project_id=project["project_id"],
                        construction=receipt.get("construction"))
            return
        if state == "configured":
            if tick < project["configured_tick"] + self.warmup_ticks:
                return
            plan = self._plan(project)
            specs = proposals.state_specs(plan)
            now = proposals.block_state(adapter, specs, paused=False)
            finished = proposals.own_output(now, specs)
            if finished is None:
                project.update(state="measured", measurement={"status": "invalid", "reason": "block_assemblers_unreadable"})
                return
            project.update(state="measuring", baseline={"tick": now["tick"], "crafts": finished})
            return
        if state == "measuring":
            base = project["baseline"]
            if tick < base["tick"] + self.window_ticks:
                return
            plan = self._plan(project)
            specs = proposals.state_specs(plan)
            now = proposals.block_state(adapter, specs, paused=False)
            finished = proposals.own_output(now, specs)
            span = now["tick"] - base["tick"]
            if finished is None or finished < base["crafts"] or span < self.window_ticks:
                measurement = {"status": "invalid", "reason": "block_assemblers_unreadable_or_counter_regressed",
                               "window_ticks": span}
            else:
                packs = (finished - base["crafts"]) * proposals.PACKS_PER_CRAFT
                rate = packs * 60 / span
                measurement = {"status": "measured", "window_ticks": span, "start_tick": base["tick"],
                               "end_tick": now["tick"], "packs": packs, "per_second": rate,
                               "expected_per_second": float(TARGET_RATE), "ratio": rate / float(TARGET_RATE),
                               "meets_expected": rate / float(TARGET_RATE) >= self.min_ratio,
                               "source": "products_finished of this block's assemblers"}
            project.update(state="measured", measurement=measurement)
            self._count("measured")
            self._event("measured", tick=now["tick"], project_id=project["project_id"], **measurement)

    def _plan(self, project):
        path = self.root / "evidence" / project["receipt"]
        return json.loads(path.read_text(encoding="utf-8"))["candidate"]["plan"]


def find_receipt(root, option_id):
    """Newest transaction receipt for this selection (the adapter does not return its path)."""
    matches = []
    for path in sorted((Path(root) / "evidence").glob("transaction-*.json"), key=lambda p: p.stat().st_mtime):
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("candidate", {}).get("option_id") == option_id:
                matches.append(path)
        except (OSError, json.JSONDecodeError):
            continue
    return matches[-1] if matches else None
