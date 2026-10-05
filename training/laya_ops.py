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
                                 password=password, command_timeout=30.0, episode_id=self.episode_id)
        stage_services.reset_coverage_progress()
        ab._TRANSFERABLE_WAITS.clear()
        ab._STAGE_DELIVERY_ATTEMPTS.clear()
        ab._open_the_run(self.client, self.bridge, SURFACE, FORCE, goal_item, (), self.script_output, emit,
                         bootstrap_profile_name=PROFILE, reference_point=reference_point)
        ab._restore_bootstrap_reservations()
        ab._reconcile_bootstrap_work(self.client, SURFACE, FORCE)

    def _operations(self) -> dict[str, Callable[..., object]]:
        c, b, ref, emit = self.client, self.bridge, self.reference_point, self.emit
        return {
            # Direct starter: drills feeding a furnace, no belts or bots (iron, copper, stone).
            "starter": lambda recipe: ab._bootstrap_direct_plate_line(c, b, SURFACE, FORCE, recipe, ref, emit),
            # Opening mine + six-furnace refinery + haul belt, sited by the full transport bill.
            "foundation": lambda recipe: ab.build_mining_stage(c, b, SURFACE, FORCE, recipe, ref, emit, expand=False),
            # One more six-furnace module with its mine growth, in place.
            "extend": lambda recipe: ab.build_mining_stage(c, b, SURFACE, FORCE, recipe, ref, emit, expand=True),
            # Build one missing stage toward stocking ``item`` (deepest unmet ingredient first). Before the
            # independent mall, pre-core items are rotational mall batches (upgrade_bootstrap=False), not lines.
            "produce": lambda item, stock_target=1: ab.ensure_produced(c, b, SURFACE, FORCE, item, ref, emit,
                                                                       stock_target=max(1, int(stock_target)),
                                                                       upgrade_bootstrap=False),
            # A standing slot in the bootstrap mall pool (the deterministic runner's "production prep").
            "mall_slot": lambda item: ab.ensure_produced(c, b, SURFACE, FORCE, item, ref, emit, upgrade_bootstrap=False,
                                                         stock_target=1, minimum_machines=1, allow_promotion=False),
            # Roboport chain toward a position (construction or logistic reach).
            "coverage": lambda x, y, purpose="construction": stage_services.extend_roboport_coverage(
                c, b, SURFACE, FORCE, (x, y), emit, purpose=purpose),
        }

    def run(self, operation: str, **params) -> dict:
        """Run one operation; the tooling's own signals become a typed result."""
        try:
            result = self._operations()[operation](**params)
        except WorkStateSignal as signal:
            return {"ok": False, "operation": operation, "params": params, "code": getattr(signal, "code", None),
                    "classification": getattr(signal, "classification", None),
                    "state": getattr(signal, "state", None), "details": getattr(signal, "details", None),
                    "message": str(signal)[:500]}
        return {"ok": True, "operation": operation, "params": params, "result": result}

    def close(self) -> None:
        set_active_material_ledger(None)
