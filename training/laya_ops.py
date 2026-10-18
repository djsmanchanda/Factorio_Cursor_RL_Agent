# Path: training/laya_ops.py
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
import json
from collections.abc import Callable
from pathlib import Path

from orchestrator import autonomous_builder as ab
from orchestrator import construction_coverage, mall_control
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
PRODUCING_OPERATIONS = {"produce", "mall_slot", "steel", "grow"}
SUBMITTING_OPERATIONS = {"starter", "foundation", "extend"}  # their layout is submitted (bots build it)


def receipt(operation: str, params: dict, result: object) -> dict:
    """A normal return as a typed receipt: ``ok`` only when the operation's own postcondition holds."""
    if operation in {"raise_stock_gate", "reserve_steel_seed"}:
        return {"operation": operation, "params": params, **result}
    if operation in PRODUCING_OPERATIONS:
        code = "producing" if result is not None else "prerequisite_advanced"
    elif operation in SUBMITTING_OPERATIONS:
        code = "submitted"
    else:
        code = "done"
    return {"ok": code != "prerequisite_advanced", "operation": operation, "params": params, "code": code,
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


class DeterministicSession:
    """One Laya world with the deterministic runtime's per-run state."""

    growable = ab.GROWABLE_PRODUCERS  # producers the "grow" operation may add a cell to

    def __init__(self, root: Path, *, goal_item: str = "automation-science-pack",
                 reference_point: tuple[float, float] = (0.0, 0.0), emit: Callable[[str], None] = print) -> None:
        import json
        manifest = json.loads((root / "manifest.json").read_text())
        server = root / "server"
        password = (server / "rcon-password").read_text().strip()
        self.script_output = server / "script-output"
        self.episode_id = f"laya-{root.name}"
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
                                 password=password, command_timeout=30.0, episode_id=self.episode_id)
        construction_coverage.reset_coverage_progress()
        ab._TRANSFERABLE_WAITS.clear()
        ab._STAGE_DELIVERY_ATTEMPTS.clear()
        ab.initialize_operation_runtime(self.bridge, SURFACE, FORCE, goal_item, emit)
        ab._restore_bootstrap_reservations()
        ab._reconcile_bootstrap_work(self.client, SURFACE, FORCE)

    def _operations(self) -> dict[str, Callable[..., object]]:
        c, b, ref, emit = self.client, self.bridge, self.reference_point, self.emit
        return {
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
        try:
            result = self._operations()[operation](**params)
        except WorkStateSignal as signal:
            outcome = {"ok": False, "operation": operation, "params": params, "code": getattr(signal, "code", None),
                       "classification": getattr(signal, "classification", None),
                       "state": getattr(signal, "state", None), "details": getattr(signal, "details", None),
                       "message": str(signal)[:500]}
        else:
            outcome = receipt(operation, params, result)
        if operation != "raise_stock_gate":
            self._maintain_mall()
        return outcome

    def _maintain_mall(self) -> None:
        """After every operation: cap standing gear/cable cells, hand back cells whose loan is already stocked."""
        for name, upkeep in (
                ("MALL CHEST CAP", lambda: ab.refresh_feedstock_chest_caps(self.client, self.bridge, SURFACE, FORCE,
                                                                           self.emit)),
                ("LOAN RELEASE", lambda: ab.release_finished_bootstrap_loans(self.client, self.bridge, SURFACE, FORCE,
                                                                            self.reference_point, self.emit))):
            try:
                upkeep()
            except WorkStateSignal as signal:  # Not this operation's outcome; retried after the next one.
                self.emit(f"  {name} FAILED: {signal}")

    def active_batch_crafts(self) -> dict[tuple, int]:
        """Craft counters of the tooling's active finite batches that still owe their bill (a rising one is progress)."""
        try:
            return ab.active_batch_crafts(self.client, SURFACE, FORCE)
        except Exception:  # noqa: BLE001 -- an unreadable survey is no evidence of progress
            return {}

    def economics(self) -> dict:
        """Read-only mall facts; failed components stay null, independently of other surveys."""
        from orchestrator.mall_bootstrap import active_bootstrap_loans
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
            loans = []
            active = active_bootstrap_loans(self.client, SURFACE, FORCE)
            try:
                statuses, counters = ab.live_base.machine_health(self.client, SURFACE,
                                                                 [loan.machine_position for loan in active])
            except Exception:
                statuses, counters = {}, {}
            for loan in active:
                counter = counters.get(loan.machine_position)
                # machine_health encodes finished crafts * 1000 plus fractional progress.
                crafts = (int(counter // 1000) - loan.step_baseline_finished
                          if counter is not None and loan.step_baseline_finished is not None
                          and loan.current_recipe == loan.step_recipe else None)
                crafts = crafts if crafts is not None and crafts >= 0 else None
                loans.append({"lender": loan.original_recipe, "making": loan.target_item, "target": loan.target_count,
                              "owner": ab._material_project_id(loan.target_item),
                              "requester_position": list(loan.requester_position), "side": loan.side,
                              "current_recipe": loan.current_recipe, "production_target": loan.production_target,
                              "step_recipe": loan.step_recipe, "step_target": loan.step_target_count,
                              "step_required_crafts": loan.step_required_crafts,
                              "step_completed_crafts": crafts,
                              "machine_status": statuses.get(loan.machine_position),
                              "completed_step_targets": dict(loan.completed_step_targets)})
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
        return {"version": 1, "projects": projects, "loans": loans, "slots": slots, "cells": cells,
                "guarded_configuration": guarded_configuration, "transactions": transactions}

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
        from training.laya_ops_runner import economics_observation
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
                    transactions["opening_oil"] = oil
            except Exception as error:  # An unreadable bill is unknown, never paid or permission to re-site.
                transactions["opening_oil"] = {"error": f"{type(error).__name__}: {error}"[:200]}
        return transactions

    def acceptance(self) -> dict:
        """The deterministic runtime's own milestone predicates (orchestrator/checkpoint_milestones.py) on the
        live world: passed / failed / unknown per C0..C6, monotonic once passed. The runner's C phases are rate
        proxies; these are the acceptance checks results are compared on. The first call is the baseline."""
        from orchestrator.checkpoint_milestones import CheckpointEvaluator
        from orchestrator.checkpoint_observer import LiveCheckpointObservationAdapter, ObservationUnsupported
        if not hasattr(self, "_evaluator"):
            self._evaluator, self._baseline, self._history = CheckpointEvaluator(), None, []
        self.acceptance_checks = None  # never journal an earlier sample's checks beside a new error
        try:
            snapshot = LiveCheckpointObservationAdapter(self.client, self.bridge, surface=SURFACE, force=FORCE,
                                                        reference_point=self.reference_point,
                                                        bootstrap_profile=PROFILE).observe().snapshot
        except ObservationUnsupported as error:  # Telemetry failure: unknown, never a failed milestone.
            return {"error": str(error)[:200]}
        self._baseline = self._baseline or snapshot
        self._history = (self._history + [snapshot])[-20:]
        results = {m.checkpoint_id: self._evaluator.evaluate(m.checkpoint_id, snapshot, baseline=self._baseline,
                                                             history=self._history)
                   for m in self._evaluator.milestones}
        # Which check blocks each unpassed milestone: a bare "failed" cannot tell a missing fact from a real gap.
        self.acceptance_checks = {
            cid: {"failed": sorted(k for k, v in (r.evidence.get("checks") or {}).items() if v is False),
                  "unknown": sorted(r.missing),
                  **{k: v for k, v in r.evidence.items() if isinstance(v, (bool, int, float, str))}}
            for cid, r in results.items() if not r.passed}
        return {cid: r.status.value for cid, r in results.items()}

    def close(self) -> None:
        set_active_material_ledger(None)
