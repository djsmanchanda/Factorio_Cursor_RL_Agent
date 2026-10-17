# Path: training/laya_ops.py
# Purpose: Run the deterministic runtime's operations (starters, mine+refinery, extension, coverage, ...) as single Laya actions on a disposable world.

"""Deterministic operations as Laya actions.

The deterministic runtime's *tooling* -- siting, layout, reservations,
validation and bot-built submission -- is reused unchanged.  Its *decisions*
(gates, deferrals, rationing, the fixed stage order in ``run()``) are not:
Laya chooses which operation to run and when (docs/rl/Laya_Clean_Runner.md).

A session prepares the same per-run state ``orchestrator.autonomous_builder.run``
prepares (ledgers, recipe catalog, cleared caches) on a Laya world, then runs
one operation per call.  Failures the tooling signals (``WorkStateSignal``:
material shortage, deferral, stuck) come back as typed results, so they can
become observations instead of crashing the run.  The world must be live
(unpaused): operations wait on bots and power in wall-clock time.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from orchestrator import autonomous_builder as ab
from orchestrator import stage_services
from orchestrator.bootstrap_district import BootstrapDistrictLedger
from orchestrator.bootstrap_work import BootstrapWorkLedger
from orchestrator.game_bridge import GameBridge
from orchestrator.material_reservations import MaterialReservationLedger, set_active_material_ledger
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
    if operation in PRODUCING_OPERATIONS:
        code = "producing" if result is not None else "prerequisite_advanced"
    elif operation in SUBMITTING_OPERATIONS:
        code = "submitted"
    elif operation == "reserve_steel_seed":
        code = "reserved"
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
        ab._BOOTSTRAP_DISTRICT_LEDGER = BootstrapDistrictLedger(
            self.script_output, episode_id=self.episode_id, surface=SURFACE, force=FORCE, bootstrap_profile=PROFILE)
        ab._MATERIAL_RESERVATION_LEDGER = MaterialReservationLedger(
            self.script_output, episode_id=self.episode_id, surface=SURFACE, force=FORCE)
        ab._BOOTSTRAP_WORK_LEDGER = BootstrapWorkLedger(self.script_output, episode_id=self.episode_id)
        set_active_material_ledger(ab._MATERIAL_RESERVATION_LEDGER)
        self.client = RconClient("127.0.0.1", manifest["rcon_port"], password)
        self.bridge = GameBridge(script_output=self.script_output, host="127.0.0.1", port=manifest["rcon_port"],
                                 password=password, command_timeout=30.0, episode_id=self.episode_id)
        stage_services.reset_coverage_progress()
        ab._TRANSFERABLE_WAITS.clear()
        ab._STAGE_DELIVERY_ATTEMPTS.clear()
        # Construction targets of the run (what the mission will build): the steel fence reads them.
        self.mall_targets, _, _ = ab._open_the_run(self.client, self.bridge, SURFACE, FORCE, goal_item, (),
                                                   self.script_output, emit, bootstrap_profile_name=PROFILE,
                                                   reference_point=reference_point)
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
            "reserve_steel_seed": lambda: ab._reserve_steel_starter_power_seed(c, SURFACE, FORCE, self.mall_targets, emit),
            # The compact steel seed beside the iron line (gated by the tooling on the iron foundation).
            "steel": lambda stock_target=10: ab.ensure_produced(c, b, SURFACE, FORCE, "steel-plate", ref, emit,
                                                                stock_target=max(1, int(stock_target))),
            # Finish a submitted refinery and retire its plate's starter once the refinery delivers.
            "retire_starter": self._retire_starter,
            # Roboport chain toward a position (construction or logistic reach).
            "coverage": lambda x, y, purpose="construction": stage_services.extend_roboport_coverage(
                c, b, SURFACE, FORCE, (x, y), emit, purpose=purpose),
        }

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
        """What the tooling has committed: active material projects (reserved and still missing per item) and
        live mall loans (which cell is lent to make what). ``loans`` is None when the survey fails: unknown,
        not empty."""
        from orchestrator.mall_bootstrap import active_bootstrap_loans
        ledger = ab._MATERIAL_RESERVATION_LEDGER
        projects = {}
        for project in ledger.projects.values():
            if ledger._active(project):
                missing = {i: n - project.reserved.get(i, 0) for i, n in project.required.items()
                           if project.reserved.get(i, 0) < n}
                projects[project.project_id] = {"state": project.state, "reserved": dict(project.reserved),
                                                "missing": missing}
        try:
            loans = [{"lender": loan.original_recipe, "making": loan.target_item, "target": loan.target_count}
                     for loan in active_bootstrap_loans(self.client, SURFACE, FORCE)]
        except Exception:  # A failed survey is an unknown, never "no loans".
            loans = None
        return {"projects": projects, "loans": loans, "transactions": self.material_transactions()}

    def material_transactions(self) -> dict:
        """Current material delivery facts for selected immutable transactions; unknown stays explicit."""
        from orchestrator.oil_transaction import OilTransactionStore
        transactions = {}
        store = OilTransactionStore.for_bridge(self.bridge, SURFACE, FORCE)
        if store is not None:
            try:
                oil = store.material_facts(self.client, ab._MATERIAL_RESERVATION_LEDGER)
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
        try:
            snapshot = LiveCheckpointObservationAdapter(self.client, self.bridge, surface=SURFACE, force=FORCE,
                                                        reference_point=self.reference_point,
                                                        bootstrap_profile=PROFILE).observe().snapshot
        except ObservationUnsupported as error:  # Telemetry failure: unknown, never a failed milestone.
            return {"error": str(error)[:200]}
        self._baseline = self._baseline or snapshot
        self._history = (self._history + [snapshot])[-20:]
        return {m.checkpoint_id: self._evaluator.evaluate(m.checkpoint_id, snapshot, baseline=self._baseline,
                                                          history=self._history).status.value
                for m in self._evaluator.milestones}

    def close(self) -> None:
        set_active_material_ledger(None)
