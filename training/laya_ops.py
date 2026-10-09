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

import math
from collections.abc import Callable, Mapping
from pathlib import Path

from orchestrator import autonomous_builder as ab
from orchestrator import demand_siting
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
PRODUCING_OPERATIONS = {"produce", "mall_slot", "steel"}
SUBMITTING_OPERATIONS = {"starter", "foundation", "extend"}  # their layout is submitted (bots build it)


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


def receipt(operation: str, params: dict, result: object) -> dict:
    """A normal return as a typed receipt: ``ok`` only when the operation's own postcondition holds."""
    if operation in PRODUCING_OPERATIONS:
        code = "producing" if result is not None else "prerequisite_advanced"
    elif operation in SUBMITTING_OPERATIONS:
        code = "submitted"
    elif operation == "reserve_steel_seed":
        code = "reserved"
    elif operation == "retire_starter":
        code = "released" if isinstance(result, Mapping) and result.get("lifecycle") == "released" else "retirement_pending"
    else:
        code = "done"
    return {"ok": code not in {"prerequisite_advanced", "retirement_pending"},
            "operation": operation, "params": params, "code": code,
            "result": dict(result) if isinstance(result, Mapping) else None if result is None else str(result)[:200]}


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
                                 password=password, command_timeout=30.0, episode_id=self.episode_id,
                                 runtime="laya")
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
            "produce": lambda item, stock_target=1: ab.ensure_produced(c, b, SURFACE, FORCE, item, ref, emit,
                                                                       stock_target=max(1, int(stock_target)),
                                                                       upgrade_bootstrap=False),
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
        """Run one operation; the tooling's own signals become a typed result.

        ``progress`` records a funded construction plan the operation submitted
        through the shared material ledger, including side-effect submissions
        that happen before an incomplete (shortage or wait) return.
        """
        projects = getattr(ab._MATERIAL_RESERVATION_LEDGER, "projects", {}) or {}
        before = funded_plan_signature(projects)
        try:
            result = self._operations()[operation](**params)
        except WorkStateSignal as signal:
            response = {"ok": False, "operation": operation, "params": params, "code": getattr(signal, "code", None),
                        "classification": getattr(signal, "classification", None),
                        "state": getattr(signal, "state", None), "details": getattr(signal, "details", None),
                        "message": str(signal)[:500]}
            response["progress"] = funded_plan_progress(
                before, getattr(ab._MATERIAL_RESERVATION_LEDGER, "projects", {}) or {})
            return response
        response = receipt(operation, params, result)
        response["progress"] = funded_plan_progress(
            before, getattr(ab._MATERIAL_RESERVATION_LEDGER, "projects", {}) or {})
        return response

    def economics(self) -> dict:
        """Committed material bills, scoped replacement lifecycles and live mall loans.

        ``loans`` is None when the survey fails: unknown, not empty. District
        submission is a reconciliation obligation, not a live readiness claim.
        ``reservation_owners`` names the project holding each reserved item and
        ``demand`` is the shared demand-siting read (never training-local
        geometry).  ``free_slots`` is left for the mall slot-growth integration.
        """
        from orchestrator.mall_bootstrap import active_bootstrap_loans
        ledger = ab._MATERIAL_RESERVATION_LEDGER
        projects = {}
        for project in ledger.projects.values():
            if ledger._active(project):
                missing = {i: n - project.reserved.get(i, 0) for i, n in project.required.items()
                           if project.reserved.get(i, 0) < n}
                projects[project.project_id] = {"state": project.state, "reserved": dict(project.reserved),
                                                "missing": missing}
        loan_error = None
        try:
            loans = [{"lender": loan.original_recipe, "making": loan.target_item, "target": loan.target_count}
                     for loan in active_bootstrap_loans(self.client, SURFACE, FORCE)]
        except Exception as error:  # A failed survey is an unknown, never "no loans".
            loans = None
            loan_error = f"{type(error).__name__}: {error}"[:200]
        # This scoped ledger survives shortage returns while bots finish submitted work.
        # Reading it does not assert readiness; the retirement tool verifies live output.
        districts = {state.recipe: {"lifecycle_state": state.lifecycle_state,
                                   "replacement_submitted": state.replacement_submitted}
                     for state in ab._BOOTSTRAP_DISTRICT_LEDGER.states()}
        return {"projects": projects, "loans": loans, "loan_error": loan_error,
                "bootstrap_districts": districts, "reservation_owners": reservation_owners(projects),
                "demand": self._demand_facts(), "free_slots": None}

    # Item reads are bounded per decision; demand for an item with no built
    # requester (or a failed read) is None -- unknown, never a zero distance.
    DEMAND_ITEMS = ("iron-plate", "copper-plate", "stone-brick", "iron-gear-wheel",
                    "copper-cable", "electronic-circuit", "transport-belt", "splitter")

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

    def acceptance(self) -> dict:
        """The deterministic runtime's own milestone predicates (orchestrator/checkpoint_milestones.py) on the
        live world: passed / failed / unknown per C0..C6, monotonic once passed. The runner's C phases are rate
        proxies; these are the acceptance checks results are compared on. The first call is the baseline."""
        from orchestrator.checkpoint_milestones import CheckpointEvaluator
        from orchestrator.checkpoint_observer import LiveCheckpointObservationAdapter, ObservationUnsupported
        from training.laya_acceptance import acceptance_packet
        self.acceptance_details = None  # A failed survey must not reuse an earlier snapshot.
        if not hasattr(self, "_evaluator"):
            self._evaluator, self._baseline, self._history = CheckpointEvaluator(), None, []
        try:
            observed = LiveCheckpointObservationAdapter(self.client, self.bridge, surface=SURFACE, force=FORCE,
                                                        reference_point=self.reference_point,
                                                        bootstrap_profile=PROFILE).observe()
        except ObservationUnsupported as error:  # Telemetry failure: unknown, never a failed milestone.
            return {"error": str(error)[:200]}
        snapshot = observed.snapshot
        self._baseline = self._baseline or snapshot
        self._history = (self._history + [snapshot])[-20:]
        packet = acceptance_packet(observed, self._evaluator, baseline=self._baseline, history=self._history,
                                   scope={"episode_id": self.episode_id, "surface": SURFACE, "force": FORCE,
                                          "server_root": str(self.script_output.parent), "bootstrap_profile": PROFILE})
        try:
            packet["tooling"] = self.economics()
        except Exception as error:
            packet["tooling"] = {"error": f"{type(error).__name__}: {error}"[:200]}
        packet["mall_targets"] = dict(getattr(self, "mall_targets", {}))
        self.acceptance_details = packet
        return packet["statuses"]

    def close(self) -> None:
        set_active_material_ledger(None)
