# Path: orchestrator/operation_session.py
# Purpose: Run the deterministic runtime's operations (starters, mine+refinery, extension, coverage, ...) as single Laya actions on a disposable world.

"""Deterministic operations as Laya actions.

The deterministic runtime's *tooling* -- siting, layout, reservations,
validation and bot-built submission -- is reused unchanged.  Its *decisions*
(gates, deferrals, rationing, the fixed stage order in ``run()``) are not:
Laya chooses which operation to run and when (docs/rl/Laya_Clean_Runner.md).

A session initializes ledgers, the recipe catalog and cleared caches on a Laya
world without the deterministic opener's reserves or priority scheduling, then
runs one operation per call. Failures the tooling signals (``WorkStateSignal``:
material shortage, deferral, stuck) come back as typed results, so they can
become observations instead of crashing the run.  The world must be live
(unpaused): operations wait on bots and power in wall-clock time.
"""

from __future__ import annotations

import copy
from dataclasses import asdict
import json
import math
from collections.abc import Callable, Mapping
from pathlib import Path

from orchestrator import autonomous_builder as ab
from orchestrator import construction_coverage, mall_control, live_base, mall_bootstrap, demand_siting
from orchestrator.bootstrap_district import BootstrapDistrictLedger
from orchestrator.bootstrap_work import BootstrapWorkLedger
from orchestrator.game_bridge import GameBridge, load_json
from planners.sandbox_infrastructure import build_layout_authorization
from planners.plan_validation import validate_build_plan
from orchestrator.material_reservations import MaterialReservationLedger, set_active_material_ledger
from orchestrator.parts_mall import MaterialShortage
from orchestrator.work_state import WorkStateSignal
from tools.rcon_client import RconClient

SURFACE, FORCE = "nauvis", "player"
PROFILE = "reduced-v1"  # no seed stock: every construction item is produced in the world


# What each operation promises when it returns normally (training/laya_ops_runner reads ``code``):
# produce/mall_slot/steel promise a producing output; ensure_produced returns None after building only ONE
# missing stage, which is progress but not the requested postcondition.
PRODUCING_OPERATIONS = {"produce", "mall_slot", "steel", "grow", "scale_production"}
SUBMITTING_OPERATIONS = {"starter", "foundation", "extend"}  # their layout is submitted (bots build it)


def _journal_scalar(value: object) -> bool:
    """Journal scalar evidence and flat scalar maps (status histograms, starter blockers), not nested blobs."""
    if isinstance(value, dict):
        return all(isinstance(v, (bool, int, float, str)) or v is None for v in value.values())
    return isinstance(value, (bool, int, float, str))


def receipt(operation: str, params: dict, result: object) -> dict:
    """A normal return as a typed receipt: ``ok`` only when the operation's own postcondition holds."""
    if operation in {"raise_stock_gate", "reserve_steel_seed", "research_select", "research_labs", "reconcile_mall_loan"}:
        return {"operation": operation, "params": params, **result}
    if operation in PRODUCING_OPERATIONS:
        code = "producing" if result is not None else "prerequisite_advanced"
    elif operation in SUBMITTING_OPERATIONS:
        code = "submitted"
    elif operation == "research":
        rejected = isinstance(result, Mapping) and result.get("ok") is False
        return {"ok": not rejected, "operation": operation, "params": params,
                "code": "research_rejected" if rejected else "queued",
                "result": dict(result) if isinstance(result, Mapping) else None}
    elif operation == "retire_starter":
        code = "released" if isinstance(result, Mapping) and result.get("lifecycle") == "released" else "retirement_pending"
    else:
        code = "done"
    return {"ok": code not in {"prerequisite_advanced", "retirement_pending"}, "operation": operation, "params": params, "code": code,
            "result": _receipt_result(result)}


def _receipt_result(result: object) -> object:
    """Small JSON dicts stay readable (the runner reads ``lifecycle``); anything else is a short string."""
    if isinstance(result, dict):
        try:
            if len(json.dumps(result)) <= 200:
                return result
        except (TypeError, ValueError):
            pass
    return None if result is None else str(result)[:200]


LOAN_BATCH_CODES = {"temporary_mall_batch", "rationed_mall_borrow_deferred"}
_UNSET = object()


def funded_plan_signature(projects: Mapping) -> dict[str, int]:
    """Funded construction plans in the shared material ledger: project id -> reserved item count.

    A plan is funded when its whole bill is reserved and committed to construction (``constructing``). A queued unfunded earmark
    (``supply_wait``) is work in progress, not a funded submission.
    """
    return {project_id: sum(project.reserved.values())
            for project_id, project in projects.items()
            if project.state == "constructing" and project.reserved}


def funded_plan_progress(before: Mapping[str, int], projects: Mapping) -> bool:
    """Whether a funded plan appeared or grew its reservation since ``before``.

    Read from the shared material-reservation ledger, so a plan the operation
    submitted as a side effect counts even when it then returned a shortage or a
    wait. An attempted-but-unfunded ghost does not.
    """
    return any(total > before.get(project_id, 0)
               for project_id, total in funded_plan_signature(projects).items())


def reservation_owners(projects: Mapping | None) -> dict[str, list[str]]:
    """Which active projects reserve each item (the material ledger's owner facts)."""
    owners: dict[str, set[str]] = {}
    for project_id, project in (projects or {}).items():
        for item, count in (project.get("reserved") or {}).items():
            if int(count) > 0:
                owners.setdefault(item, set()).add(project_id)
    return {item: sorted(ids) for item, ids in owners.items()}


class DeterministicSession:
    """One Laya world with the deterministic runtime's per-run state."""

    DEMAND_ITEMS = ("iron-plate", "copper-plate", "stone-brick", "iron-gear-wheel",
                    "copper-cable", "electronic-circuit", "transport-belt", "splitter")

    def _loan_batch_facts(
        self, loan, *, available: Mapping[str, int] | None | object = _UNSET,
    ) -> dict:
        """Live facts for one active loan; every probe is guarded and unknown stays None.

        ``available`` may be a shared spendable-stock read so a survey of
        several loans does not re-query the network per cell.
        """
        if available is _UNSET:
            try:
                available = live_base.transferable_items(self.client, SURFACE, FORCE)
            except Exception:  # noqa: BLE001 -- a diagnostic read must not stop the loop
                available = None
        ledger = getattr(ab, "_MATERIAL_RESERVATION_LEDGER", None)
        available_items = getattr(ledger, "allocatable_stock", None)
        if available is not None and callable(available_items):
            # Transferable inventory is physical stock, not unallocated stock.
            # With no proven loan claimant, retain other projects' reservations.
            available = available_items(available)
        try:
            products_finished = ab._bootstrap_loan_products_finished(self.client, SURFACE, loan)
        except Exception:  # noqa: BLE001
            products_finished = None
        try:
            requester_contents = live_base.chest_contents(self.client, SURFACE, loan.requester_position)
        except Exception:  # noqa: BLE001
            requester_contents = None
        try:
            producer_status = live_base.entity_status_name(self.client, SURFACE, loan.machine_position)
        except Exception:  # noqa: BLE001
            producer_status = None
        return mall_bootstrap.bootstrap_loan_facts(
            loan, products_finished=products_finished, requester_contents=requester_contents,
            available=available, producer_status=producer_status,
        )


    @staticmethod
    def _loan_batch_key(loan) -> tuple:
        return (loan.original_recipe, loan.target_item,
                round(loan.machine_position[0], 2), round(loan.machine_position[1], 2))


    def _loan_batch_status(self, loan, facts: Mapping) -> str:
        """Read-only batch progress class against the run's remembered digest."""
        seen = getattr(self, "_loan_batch_seen", None) or {}
        previous = seen.get(self._loan_batch_key(loan))
        if previous is None:
            return "new"
        return "progressing" if previous != mall_bootstrap.bootstrap_loan_signature(facts) else "unchanged"


    def _loan_batch_diagnostics(self, item: str | None, *, remember: bool) -> dict | None:
        """The active loan making ``item``, with a truthful progress class.

        ``new`` -- first sight of this funded/active batch (a zero craft counter
        on its first sample is not itself a stall); ``progressing`` -- the step,
        counter, bill, inputs, or producer status moved since last seen;
        ``unchanged`` -- repeated with identical facts. Nothing is
        special-cased: any item's loan is described the same way.
        """
        if not item:
            return None
        try:
            loans = mall_bootstrap.active_bootstrap_loans(self.client, SURFACE, FORCE)
        except Exception:  # noqa: BLE001 -- a failed survey is unknown, not "no loan"
            return None
        loan = next((candidate for candidate in loans if candidate.target_item == item), None)
        if loan is None:
            return None
        facts = self._loan_batch_facts(loan)
        status = self._loan_batch_status(loan, facts)
        if remember:
            seen = getattr(self, "_loan_batch_seen", None)
            if seen is None:
                seen = self._loan_batch_seen = {}
            seen[self._loan_batch_key(loan)] = mall_bootstrap.bootstrap_loan_signature(facts)
        return {**facts, "status": status}


    def _set_research(self, technology: str) -> dict:
        """Set the research target via the mod's set_research; the report names the canonical target."""
        path = self.bridge.set_research(technology, force=FORCE)
        report = json.loads(Path(path).read_text())
        if not report.get("ok"):
            return {"ok": False, "technology": technology, "error": report.get("error")}
        return {"technology": report.get("canonical_name") or technology, "idempotent": report.get("idempotent")}


    def research_status(self, technology: str | None = None) -> dict:
        """Read live research state (optionally for one technology) through the mod's read-only command."""
        path = self.bridge.research_status(force=FORCE, technology=technology)
        return json.loads(Path(path).read_text())


    def science_status(self) -> dict:
        """Read live research/lab telemetry for this force."""
        path = self.bridge.science_status(SURFACE, FORCE)
        return json.loads(Path(path).read_text())


    def goal_status(self, technology: str) -> dict:
        """The goal technology's live research state (target_completed is the verified success flag)."""
        return self.research_status(technology)


    def _attach_loan_batch(self, response: dict, params: Mapping) -> dict:
        """Relay the active loan's truthful facts and let genuine batch work count.

        Only item-producing operations can borrow a mall cell, so the live loan
        survey is skipped for starters, foundations, coverage and retirement --
        and for a producing/mall receipt that already built something, since a
        success is its own progress. The batch's own advance is progress only
        for a loan-batch outcome, so a successful producing/mall receipt keeps
        its funded-plan progress meaning. A newly funded batch's zero first
        craft is not a stall.
        """
        if response.get("operation") not in PRODUCING_OPERATIONS or response.get("ok"):
            return response
        item = params.get("item") or params.get("recipe")
        loan_batch = self._loan_batch_diagnostics(item, remember=True)
        if loan_batch is None:
            return response
        response["loan_batch"] = loan_batch
        if response.get("code") in LOAN_BATCH_CODES:
            new_funded = loan_batch["status"] == "new" and loan_batch.get("inputs_covered") is True
            response["progress"] = bool(response.get("progress")) or new_funded or loan_batch["status"] == "progressing"
        return response


    def _demand_facts(self) -> dict[str, dict | None]:
        """Per item, the shared demand-siting point and its distance from the base anchor.

        Uses ``orchestrator.demand_siting`` (the deterministic runtime's own read);
        training only relays it and never plans geometry.
        """
        fallback = (math.nan, math.nan)
        facts: dict[str, dict | None] = {}
        for item in self.DEMAND_ITEMS:
            try:
                point = demand_siting.demand_point(self.client, SURFACE, FORCE, item, fallback)
            except Exception:  # noqa: BLE001 -- a diagnostic read must not stop the loop
                facts[item] = None
                continue
            if point is None or point[0] != point[0] or point[1] != point[1]:
                facts[item] = None  # no requester wants it, or the read failed
                continue
            facts[item] = {"point": [float(point[0]), float(point[1])],
                           "distance": float(demand_siting.consumer_distance(self.reference_point, point))}
        return facts


    growable = ab.GROWABLE_PRODUCERS  # producers the "grow" operation may add a cell to

    def __init__(self, root: Path, *, goal_item: str = "automation-science-pack",
                 reference_point: tuple[float, float] = (0.0, 0.0), emit: Callable[[str], None] = print,
                 logical_episode_id: str | None = None) -> None:
        import json
        manifest = json.loads((root / "manifest.json").read_text())
        server = root / "server"
        password = (server / "rcon-password").read_text().strip()
        self.script_output = server / "script-output"
        self.episode_id = logical_episode_id or f"laya-{root.name}"
        self.reference_point = reference_point
        self.emit = emit
        self.stock_networks = lambda: None
        ab._BOOTSTRAP_DISTRICT_LEDGER = BootstrapDistrictLedger(
            self.script_output, episode_id=self.episode_id, surface=SURFACE, force=FORCE, bootstrap_profile=PROFILE)
        ab._MATERIAL_RESERVATION_LEDGER = MaterialReservationLedger(
            self.script_output, episode_id=self.episode_id, surface=SURFACE, force=FORCE)
        ab._BOOTSTRAP_WORK_LEDGER = BootstrapWorkLedger(self.script_output, episode_id=self.episode_id)
        set_active_material_ledger(ab._MATERIAL_RESERVATION_LEDGER)
        self.client = RconClient("127.0.0.1", manifest["rcon_port"], password)
        self.bridge = GameBridge(script_output=self.script_output, host="127.0.0.1", port=manifest["rcon_port"],
                                 password=password, command_timeout=30.0, episode_id=self.episode_id, runtime="laya")
        self._loan_batch_seen = {}
        # Arm before operations produce anything; resume only reads retained evidence.
        if logical_episode_id is None:
            from orchestrator.checkpoint_observer import LiveCheckpointObservationAdapter
            LiveCheckpointObservationAdapter(self.client, self.bridge, surface=SURFACE,
                force=FORCE)._plastic_startup(self.client, arm=True)
        construction_coverage.reset_coverage_progress()
        ab._TRANSFERABLE_WAITS.clear()
        ab._STAGE_DELIVERY_ATTEMPTS.clear()
        ab.initialize_operation_runtime(self.bridge, SURFACE, FORCE, goal_item, emit)
        ab._restore_bootstrap_reservations()
        ab._reconcile_bootstrap_work(self.client, SURFACE, FORCE)
        # The mission's C4-C6 need plastic: fence its one output chest before mall cells spend the kit's.
        ab._reserve_plastic_output_chest(self.client, SURFACE, FORCE, {"plastic-bar": 1}, emit)

    def _operations(self) -> dict[str, Callable[..., object]]:
        from orchestrator.research_operations import build_labs, select_research, scale_production
        from orchestrator.mall_reconciliation import reconcile
        c, b, ref, emit = self.client, self.bridge, self.reference_point, self.emit
        return {
            "research": lambda technology: self._set_research(technology),
            "research_select": lambda **params: select_research(b, force=FORCE, **params),
            "research_labs": lambda **params: build_labs(self, **params),
            "scale_production": lambda **params: scale_production(self, **params),
            "reconcile_mall_loan": lambda **params: reconcile(self, **params),
            # Direct starter: drills feeding a furnace, no belts or bots (iron, copper, stone).
            "starter": lambda recipe: ab._bootstrap_direct_plate_line(c, b, SURFACE, FORCE, recipe, ref, emit),
            # Opening mine + six-furnace refinery + haul belt, sited by the full transport bill.
            # earmark_unfunded: place the ghosts even while items are missing; the persisted bill reserves
            # them and steers the mall, and the submitted refinery is what lets the steel seed break the
            # pole cycle (poles need steel, steel waits for the iron refinery).
            "foundation": lambda recipe: ab.build_mining_stage(c, b, SURFACE, FORCE, recipe, ref, emit, expand=False,
                                                               earmark_unfunded=True),
            # One more six-furnace module with its mine growth, in place.
            "extend": lambda recipe: ab.build_mining_stage(c, b, SURFACE, FORCE, recipe, ref, emit, expand=True,
                                                           earmark_unfunded=True),
            # Build one missing stage toward stocking ``item`` (deepest unmet ingredient first). Before the
            # independent mall, pre-core items are rotational mall batches (upgrade_bootstrap=False), not lines.
            # stock_target is the network level wanted; the cell is capped there, so it stops when the need is met.
            "produce": lambda item, stock_target=1, finite=True: ab.produce_to_demand(
                c, b, SURFACE, FORCE, item, ref, emit, stock_target=stock_target, finite=finite),
            # One more gear/cable cell when every existing one runs flat out and other cells starve for it.
            "grow": lambda item, machines: ab.grow_producer(c, b, SURFACE, FORCE, item, ref, emit, machines=machines),
            # A standing slot in the bootstrap mall pool (the deterministic runner's "production prep").
            "mall_slot": lambda item: ab.ensure_produced(c, b, SURFACE, FORCE, item, ref, emit, upgrade_bootstrap=False,
                                                         stock_target=1, minimum_machines=1, allow_promotion=False),
            # Fence the steel starter's poles in the material ledger before other work spends them
            # (poles need steel: without the fence the foundation/steel cycle cannot start).
            "reserve_steel_seed": self._reserve_steel_seed,
            # The compact steel seed beside the iron line (gated by the tooling on the iron foundation).
            "steel": lambda stock_target=10: ab.ensure_produced(c, b, SURFACE, FORCE, "steel-plate", ref, emit,
                                                                stock_target=max(1, int(stock_target))),
            # Finish a submitted refinery and retire its plate's starter once the refinery delivers.
            "retire_starter": self._retire_starter,
            "raise_stock_gate": self._raise_stock_gate,
            # Roboport chain toward a position (construction or logistic reach).
            "coverage": lambda x, y, purpose="construction": construction_coverage.extend_roboport_coverage(
                c, b, SURFACE, FORCE, (x, y), emit, purpose=purpose),
        }

    def _reserve_steel_seed(self) -> dict:
        """Verify this selected seed fence against current, allocated stock."""
        if ab._production_started(self.client, SURFACE, FORCE, "steel-plate"):
            # Not a build receipt: nothing was placed by this call.
            return {"ok": True, "code": "seed_producing", "state": "producing"}
        ledger = ab._MATERIAL_RESERVATION_LEDGER
        project_id = "conversion_steel-plate"
        # A planned or submitted seed already owns its plan bill (the belt-side seed bills one pole);
        # re-running the two-pole fence would re-declare and drop that bill.
        project = None if ledger is None else ledger.projects.get(project_id)
        if ledger is not None and (ledger.path.with_suffix(".steel-seed.json").exists()
                                   or (project is not None and project.state == "constructing")):
            return {"ok": True, "code": "seed_committed", "state": getattr(project, "state", None),
                    "project_id": project_id}
        ab._reserve_steel_starter_power_seed(
            self.client, SURFACE, FORCE, {"steel-plate": 1}, self.emit)
        project = None if ledger is None else ledger.projects.get(project_id)
        if project is None:
            raise WorkStateSignal("Steel seed reservation was not recorded", code="reservation_unverified",
                                  classification="bug", state="failed", details={"project_id": project_id})
        stock = ab._transferable_or_available_stock(self.client, SURFACE, FORCE)
        # Refresh allocations without shrinking an expanded construction bill.
        ledger.shortage_targets(project_id, stock)
        project = ledger.projects[project_id]
        bill = ab._steel_starter_power_seed_bill()
        if any(project.reserved.get(item, 0) < count for item, count in bill.items()):
            raise MaterialShortage(project_id, bill, project.reserved)
        return {"ok": True, "code": "reserved", "state": project.state, "project_id": project_id,
                "required": bill, "reserved": dict(project.reserved)}

    def _retire_starter(self, recipe: str) -> dict:
        """The tooling retires the starter only after the refinery's measured output and chest evidence."""
        state = ab._bootstrap_state(recipe)
        if state is None or state.replacement_origin is None:
            return {"lifecycle": getattr(state, "lifecycle_state", None), "submitted": False}
        ab._reconcile_submitted_bootstrap_replacement(self.client, self.bridge, SURFACE, FORCE, state, self.emit)
        return {"lifecycle": ab._bootstrap_state(recipe).lifecycle_state, "submitted": True}

    def run(self, operation: str, **params) -> dict:
        """Run one operation; the tooling's own signals become a typed result."""
        before = funded_plan_signature(getattr(ab._MATERIAL_RESERVATION_LEDGER, "projects", {}) or {})
        try:
            if operation not in {"raise_stock_gate", "reconcile_mall_loan"}:
                self._prepare_operation()
            result = self._operations()[operation](**params)
        except WorkStateSignal as signal:
            outcome = {"ok": False, "operation": operation, "params": params, "code": getattr(signal, "code", None),
                       "classification": getattr(signal, "classification", None),
                       "state": getattr(signal, "state", None), "details": getattr(signal, "details", None),
                       "message": str(signal)[:500]}
        else:
            outcome = receipt(operation, params, result)
        if operation not in {"raise_stock_gate", "reconcile_mall_loan"}:
            self._maintain_mall()
        outcome["progress"] = funded_plan_progress(before, getattr(ab._MATERIAL_RESERVATION_LEDGER, "projects", {}) or {})
        return self._attach_loan_batch(outcome, params)

    def _prepare_operation(self) -> None:
        """Refresh shared dependency funding before a selected operation can spend it."""
        ab._refresh_logistic_chest_budget(
            self.client, SURFACE, FORCE, ab._MATERIAL_RESERVATION_LEDGER, self.emit)
        ab._refresh_owned_standing_gates(self.client, self.bridge, SURFACE, FORCE, self.emit)
        # Retirements already authorized by the explicit operation keep draining:
        # only an empty retained provider with proven storage room is reclaimed.
        ab._maintain_retained_starter_providers(
            self.client, self.bridge, SURFACE, FORCE, self.emit)

    def _maintain_mall(self) -> None:
        """After every operation: cap standing gear/cable cells, hand back cells whose loan is already stocked,
        and release the reservations of mall cells that are already producing."""
        for name, upkeep in (
                ("MATERIAL PROJECT CLOSE", lambda: ab.complete_producing_material_projects(self.client, SURFACE, FORCE,
                                                                                            self.emit)),
                ("MALL CHEST CAP", lambda: ab.refresh_feedstock_chest_caps(self.client, self.bridge, SURFACE, FORCE,
                                                                           self.emit)),
                ("LOAN RELEASE", lambda: ab.release_finished_bootstrap_loans(self.client, self.bridge, SURFACE, FORCE,
                                                                            self.reference_point, self.emit))):
            try:
                upkeep()
            except WorkStateSignal as signal:  # Not this operation's outcome; retried after the next one.
                self.emit(f"  {name} FAILED: {signal}")

    def active_batch_crafts(self, *, bounded: bool = False) -> dict[tuple, int]:
        """Craft counters of the tooling's active finite batches that still owe their bill (a rising one is progress)."""
        try:
            return ab.active_batch_crafts(self.client, SURFACE, FORCE, bounded=bounded)
        except Exception:  # noqa: BLE001 -- an unreadable survey is no evidence of progress
            return {}

    def built_network(self) -> int | None:
        """Built poles and roboports surface-wide (a rising count is construction progress), or None."""
        from orchestrator.live_base import built_network_count
        try:
            return built_network_count(self.client, SURFACE, FORCE)
        except Exception:  # noqa: BLE001 -- an unreadable survey is no evidence of progress
            return None

    def economics(self) -> dict:
        """Read-only mall facts; failed components stay null, independently of other surveys."""
        ledger = ab._MATERIAL_RESERVATION_LEDGER
        projects = None if ledger is None else {}
        if ledger is not None:
            for project in ledger.projects.values():
                if ledger._active(project):
                    missing = {i: n - project.reserved.get(i, 0) for i, n in project.required.items()
                               if project.reserved.get(i, 0) < n}
                    projects[project.project_id] = {"state": project.state, "reserved": dict(project.reserved),
                                                  "missing": missing, "target_item": project.target_item,
                                                  "required": dict(project.required), "active": True}
        slots = {"capacity": None, "unlimited": None, "occupied": None, "free": None,
                 "scope": None, "includes_ghosts": True}
        independent = None
        try:
            independent = ab._independent_mall_ready(self.client, SURFACE, FORCE)
            slots["scope"] = "demand" if independent else "bootstrap"
            # The operational cap helper catches telemetry errors and defaults to eight.
            # Observe its admission facts strictly here, so failure cannot become capacity.
            advanced = independent or ab._production_started(self.client, SURFACE, FORCE, "advanced-circuit")
            slots["capacity"] = (None if advanced else ab.DEMAND_MALL_SLOT_TARGET
                                 if ab._dense_bootstrap_mall_enabled(self.client, SURFACE, FORCE)
                                 else ab._PRE_ADVANCED_CIRCUIT_MALL_SLOT_TARGET)
            slots["unlimited"] = bool(advanced)
        except Exception:
            pass
        try:
            if independent is not None:
                survey = ab.mall_demand_slot_count if independent else ab.mall_slot_count
                slots["occupied"] = survey(self.client, SURFACE, self.reference_point)
                if slots["capacity"] is not None:
                    slots["free"] = max(0, slots["capacity"] - slots["occupied"])
        except Exception:
            pass
        try:
            from orchestrator.mall_reconciliation import observe_loans
            loans = observe_loans(self.client, SURFACE, FORCE)
        except Exception:
            loans = None
        try:
            cells = mall_control.observe_cells(self.client, self.script_output, SURFACE, FORCE,
                                               self.reference_point, ab._MALL_RECIPE_ANCHORS)
        except Exception:
            cells = None
        try:
            from orchestrator.configuration_preconditions import supports_configuration_preconditions
            guarded_configuration = supports_configuration_preconditions(self.client)
        except Exception:
            guarded_configuration = None
        try:
            transactions = self.material_transactions()
        except Exception:
            transactions = None
        loan_error = None
        try:
            active = mall_bootstrap.active_bootstrap_loans(self.client, SURFACE, FORCE)
            try:
                spendable = live_base.transferable_items(self.client, SURFACE, FORCE)
            except Exception:
                spendable = None
            legacy = []
            for loan in active:
                fact = self._loan_batch_facts(loan, available=spendable)
                fact["status"] = self._loan_batch_status(loan, fact)
                legacy.append(fact)
            if loans is None:
                loans = legacy
            else:
                by_item = {(fact["lender"], fact["making"]): fact for fact in legacy}
                loans = [{**by_item.get((fact["lender"], fact["making"]), {}), **fact} for fact in loans]
        except Exception as error:
            loan_error = f"{type(error).__name__}: {error}"[:200]
        districts_ledger = getattr(ab, "_BOOTSTRAP_DISTRICT_LEDGER", None)
        districts = {state.recipe: {"lifecycle_state": state.lifecycle_state,
                                   "replacement_submitted": state.replacement_submitted}
                     for state in districts_ledger.states()} if districts_ledger is not None else {}
        return {"version": 1, "projects": projects, "loans": loans, "slots": slots, "cells": cells,
                "guarded_configuration": guarded_configuration, "transactions": transactions,
                "bootstrap_districts": districts, "reservation_owners": reservation_owners(projects),
                "demand": self._demand_facts(), "free_slots": slots["free"], "loan_error": loan_error,
                "mall_capacity": {"committed_slots": slots["occupied"], "slot_limit": slots["capacity"],
                                  "unlimited": slots["unlimited"]}}

    def _raise_stock_gate(self, item: str, preview: dict) -> dict:
        result = mall_control.execute_raise(item, preview, observe=self._stock_control_observation,
                                            submit=self._submit_stock_gate, floors=ab._MALL_STOCK_GATE_FLOORS)
        if result.get("submitted"):
            # Record the raise separately so a later finite produce cannot lower it.
            key = (SURFACE, FORCE, item)
            ab._RAISED_STOCK_GATE_FLOORS[key] = max(ab._RAISED_STOCK_GATE_FLOORS.get(key, 0),
                                                    ab._MALL_STOCK_GATE_FLOORS.get(key, 0))
        return result

    def _submit_stock_gate(self, plan: dict, name: str) -> dict:
        # Use the shared validator/authorization/bridge submission without the
        # construction wrapper's clutter, pole relocation or ledger side effects.
        validate_build_plan(plan)
        authorization = build_layout_authorization([(name, plan)])
        report = load_json(self.bridge.build_layout(authorization, plan))
        if not report.get("ok"):
            raise ValueError(str(report)[:200])
        return report

    def _stock_control_observation(self) -> dict:
        from orchestrator.operation_observation import economics_observation
        return economics_observation(self.economics(), self.stock_networks())

    def material_transactions(self) -> dict:
        """Current material delivery facts for selected immutable transactions; unknown stays explicit."""
        from orchestrator.oil_transaction import OilTransactionStore
        transactions = {}
        store = OilTransactionStore.for_bridge(self.bridge, SURFACE, FORCE)
        if store is not None:
            try:
                # A survey must not rebalance the live ledger: allocatable_stock rebalances in place, so
                # a raise preview and its pre-write recheck disagreed whenever stock moved (review 2026-10-18).
                ledger = ab._MATERIAL_RESERVATION_LEDGER
                oil = store.material_facts(self.client, None if ledger is None else copy.deepcopy(ledger))
                if oil is not None:
                    oil["packets_pending"] = self._packets_pending(store)
                    transactions["opening_oil"] = oil
            except Exception as error:  # An unreadable bill is unknown, never paid or permission to re-site.
                transactions["opening_oil"] = {"error": f"{type(error).__name__}: {error}"[:200]}
        return transactions

    def _packets_pending(self, store) -> list[str] | None:
        """Selected oil packets not yet released (a placement neither ghosted nor built), or None if unreadable."""
        from orchestrator import live_base
        try:
            transaction = store.load()
            if transaction["complete"]:
                return []
            return [name for name, packet in transaction["intent"]["packets"]
                    if live_base.plan_construction_status(self.client, SURFACE, FORCE, packet)["unplaced"]]
        except Exception:  # noqa: BLE001 -- an unreadable survey is no evidence
            return None

    def acceptance(self) -> dict:
        """The deterministic runtime's own milestone predicates (orchestrator/checkpoint_milestones.py) on the
        live world: passed / failed / unknown per C0..C6, monotonic once passed. The runner's C phases are rate
        proxies; these are the acceptance checks results are compared on. The first call is the baseline."""
        from orchestrator.checkpoint_milestones import (CheckpointEvaluator, _foundation_ready, evaluate_milestone,
                                                         starter_blocker)
        from orchestrator.checkpoint_observer import LiveCheckpointObservationAdapter, ObservationUnsupported
        if not hasattr(self, "_evaluator"):
            self._evaluator, self._baseline, self._history = CheckpointEvaluator(), None, []
        self.acceptance_details = None
        self.acceptance_checks = None  # never journal an earlier sample's checks beside a new error
        self.acceptance_fresh = None
        self.acceptance_tick = None
        self.acceptance_evidence = None
        self.foundation_observations = None
        try:
            observed = LiveCheckpointObservationAdapter(self.client, self.bridge, surface=SURFACE, force=FORCE,
                                                        reference_point=self.reference_point,
                                                        bootstrap_profile=PROFILE).observe()
            snapshot = observed.snapshot
        except ObservationUnsupported as error:  # Telemetry failure: unknown, never a failed milestone.
            return {"error": str(error)[:200]}
        # Persist the exact normalized inputs to readiness, including missing fields.
        self.foundation_observations = {name: {"tick": snapshot.tick,
            "observation": asdict(snapshot.foundations[name]) if name in snapshot.foundations else None,
            "ready": _foundation_ready(snapshot, name)} for name in ("iron", "copper", "stone")}
        self._baseline = self._baseline or snapshot
        self.acceptance_tick = snapshot.tick
        # Per-starter facts for the ops brief, from this acceptance sample (refreshed with each check).
        self.starter_facts = {name: {"present": snapshot.starters[name].present,
                                     "healthy": snapshot.starters[name].healthy,
                                     "producing": snapshot.starters[name].producing,
                                     "output_count": snapshot.starters[name].output_count,
                                     "blocker": starter_blocker(snapshot, name), "tick": snapshot.tick}
                              if name in snapshot.starters else {"blocker": "unobserved", "tick": snapshot.tick}
                              for name in ("iron", "copper", "stone")}
        # Observed foundation readiness per plate: bots may finish a foundation after its operation returned a
        # wait, which the operation history alone never records (C12: 24 runs never offered the starter retirement).
        self.foundation_facts = {plate: _foundation_ready(snapshot, name)
                                 for plate, name in (("iron-plate", "iron"), ("copper-plate", "copper"),
                                                     ("stone-brick", "stone"))}
        self._history = (self._history + [snapshot])[-20:]
        # Fresh: the stateless predicates on this sample. The evaluator's latch answers "passed" forever once
        # reached, so a latched final could not show a regression (review 2026-10-20).
        fresh = {m.checkpoint_id: evaluate_milestone(m.checkpoint_id, snapshot, baseline=self._baseline,
                                                     history=self._history, milestones=self._evaluator.milestones)
                 for m in self._evaluator.milestones}
        self._evaluator.reached.update(cid for cid, r in fresh.items() if r.passed)
        from orchestrator.checkpoint_evidence import acceptance_packet
        self.acceptance_details = acceptance_packet(
            observed, self._evaluator, baseline=self._baseline, history=self._history, current_results=fresh,
            scope={"episode_id": self.episode_id, "surface": SURFACE, "force": FORCE,
                   "server_root": str(self.script_output.parent), "bootstrap_profile": PROFILE})
        try:
            self.acceptance_details["tooling"] = self.economics()
        except Exception as error:
            self.acceptance_details["tooling"] = {"error": f"{type(error).__name__}: {error}"[:200]}
        self.acceptance_details["mall_targets"] = dict(getattr(self, "mall_targets", {}))
        self.acceptance_fresh = {cid: r.status.value for cid, r in fresh.items()}
        self.acceptance_evidence = {
            cid: {"predicate_version": result.predicate_version, **result.evidence}
            for cid, result in fresh.items() if cid == "C5"}
        # Which check blocks each milestone not passing now: a bare "failed" cannot tell a missing fact from a gap.
        self.acceptance_checks = {
            cid: {"failed": sorted(k for k, v in (r.evidence.get("checks") or {}).items() if v is False),
                  "unknown": sorted(r.missing),
                  **{k: v for k, v in r.evidence.items() if _journal_scalar(v)}}
            for cid, r in fresh.items() if not r.passed}
        # Historical (monotonic) statuses: reached once, passed for the rest of the run.
        return {cid: "passed" if cid in self._evaluator.reached else r.status.value for cid, r in fresh.items()}

    def close(self) -> None:
        set_active_material_ledger(None)
