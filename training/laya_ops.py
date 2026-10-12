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

from planners.recipe_data import LINE_RECIPES

import json
import math
from collections.abc import Callable, Mapping
from pathlib import Path

from orchestrator import autonomous_builder as ab
from orchestrator import demand_siting
from orchestrator import live_base
from orchestrator import mall_bootstrap
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
PRODUCING_OPERATIONS = {"produce", "mall_slot", "mall_capacity", "steel"}
SUBMITTING_OPERATIONS = {"starter", "foundation", "extend"}  # their layout is submitted (bots build it)
# Outcomes that mean "the tooling is running a finite bootstrap-loan batch":
# the receipt must carry its own progress facts so a stopper can tell a newly
# funded or measurably advancing batch from a repeated unchanged one.
LOAN_BATCH_CODES = {"temporary_mall_batch", "rationed_mall_borrow_deferred"}

#: Sentinel distinguishing "read spendable stock for me" from a caller-supplied
#: (possibly None/unknown) shared stock map.
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


def receipt(operation: str, params: dict, result: object) -> dict:
    """A normal return as a typed receipt: ``ok`` only when the operation's own postcondition holds."""
    if operation in PRODUCING_OPERATIONS:
        code = "producing" if result is not None else "prerequisite_advanced"
    elif operation in SUBMITTING_OPERATIONS:
        code = "submitted"
    elif operation == "research":
        rejected = isinstance(result, Mapping) and result.get("ok") is False
        code = "research_rejected" if rejected else "queued"
        return {"ok": not rejected, "operation": operation, "params": params, "code": code,
                "result": dict(result) if isinstance(result, Mapping) else None}
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
        self.mall_targets, _, self.priorities = ab._open_the_run(self.client, self.bridge, SURFACE, FORCE, goal_item, (),
                                                   self.script_output, emit, bootstrap_profile_name=PROFILE,
                                                   reference_point=reference_point)
        ab._restore_bootstrap_reservations()
        ab._reconcile_bootstrap_work(self.client, SURFACE, FORCE)
        # Progress classification for repeated loan-batch receipts runs on this
        # process only; each entry is one borrowed cell's last observed digest.
        self._loan_batch_seen: dict[tuple, tuple] = {}

    def enable_queue(self, *, world_id: str):
        from orchestrator.work_queue import WorkQueue
        tick = int(self.client.command("/sc rcon.print(game.tick)").strip())
        self.queue = WorkQueue(self.script_output.parent / "logs" / "laya-work-queue.json",
                               world_id=world_id, tick=tick)
        self._queue_dependencies = ()
        self.queue.runtime['automatic_mall_growth'] = True
        self.queue.save()

    def queue_menu(self, menu):
        """Expose enqueue/reorder authority using the same feasible-operation menu."""
        from orchestrator.work_queue import WorkQueue
        options = []
        for option in menu["options"]:
            if option["family"] == "wait":
                options.append({**option, "wait_ticks": min(option["wait_ticks"], 120)})
                continue
            job = self.queue.jobs.get(WorkQueue.identity(option["family"], option["params"]))
            if job is not None and job.state != "complete":
                options.append({**option, "option_id": "queue.promote." + job.job_id,
                                "family": "queue_promote", "params": {"job_id": job.job_id},
                                "summary": "prioritize " + option["summary"]})
            elif (job is None or option["family"] == "extend" or
                  int(option["params"].get("stock_target", 0)) > job.target):
                options.append({**option, "summary": "queue " + option["summary"]})
        # Owned continuations remain reorderable even after phase/menu changes.
        seen = {o["params"].get("job_id") for o in options if o["family"] == "queue_promote"}
        for job in self.queue.jobs.values():
            if job.state != "complete" and not job.parents and job.job_id not in seen:
                options.append({"option_id": "queue.promote." + job.job_id, "family": "queue_promote",
                                "need_id": "queue:" + job.job_id, "product": job.item,
                                "summary": f"prioritize {job.operation} {job.item or ''}",
                                "params": {"job_id": job.job_id},
                                "features": {"goal_need": True, "waiting": job.state == "blocked",
                                             "unchanged_failure": job.blocked_signature is not None}})
        return {**menu, "options": options}

    def enqueue_choice(self, chosen, tick):
        if chosen["family"] == "queue_promote":
            self.queue.promote(chosen["params"]["job_id"], tick=tick)
            return
        params, operation = dict(chosen["params"]), chosen["family"]
        # Extending an already completed module is a new authorized transaction.
        identity = self.queue.identity(operation, params)
        previous = self.queue.jobs.get(identity)
        if previous is not None and previous.state == "complete" and operation == "extend":
            previous.state, previous.retry_tick = "ready", tick
        priority = 100 if chosen.get("features", {}).get("goal_need") else 50
        if operation == "produce" and params.get("item") == "fast-inserter":
            priority = max(priority, 80)
        self.queue.enqueue(operation, params, priority=priority, tick=tick)

    def _queue_snapshot(self, facts, economics):
        stock = economics.get("material_stock")
        owned = {}
        producers = {m.get("recipe") for m in facts.get("machines", [])
                     if m.get("name") in live_base.ASSEMBLER_TIERS and m.get("recipe")}
        loans = economics.get("loans") or []
        def pending_entities(job):
            receipt = job.checkpoint.get("receipt", {})
            return (job.checkpoint.get("pending_entities")
                    or ((receipt.get("details") or {}).get("pending", [])
                        if receipt.get("code") == "infrastructure_construction_wait" else []))
        pending_positions = tuple({tuple(e["position"]) for job in self.queue.jobs.values()
                                   if job.state != "complete" for e in pending_entities(job)})
        pending_names = (live_base.entity_names_at(self.client, SURFACE, pending_positions)
                         if pending_positions else {})
        for job in self.queue.jobs.values():
            proof = {"reserved": {}}
            if job.operation == "produce":
                proof["producer_present"] = job.item in producers
            pending = pending_entities(job) if job.state != "complete" else []
            if pending:
                proof["pending_ready"] = all(pending_names.get(tuple(e["position"])) == e["entity"] for e in pending)
            current_stock = int((stock or {}).get(job.item, 0))
            # A single finite item job serves its parents' aggregate bill. Loan
            # craft counters are scoped to its own machine and step baseline.
            lane_credits = job.checkpoint.setdefault("lane_credits", {})
            for loan in loans:
                if loan.get("target_item") != job.item or loan.get("step_recipe") != job.item:
                    continue
                if loan.get("crafts_done") is None:
                    continue
                lane = json.dumps([loan.get("machine_position"), loan.get("step_baseline_finished")])
                amount = float(LINE_RECIPES.get(job.item, {}).get("product_amount", 1))
                lane_credits[lane] = max(lane_credits.get(lane, 0),
                                        int(loan["crafts_done"] * amount)-job.checkpoint.get("lane_floors", {}).get(lane, 0))
            produced = sum(lane_credits.values())
            # Credit only the dispersed portion, never count the same output
            # again as both crafted and still held stock.
            proof["delivered"] = max(job.delivered, produced-current_stock)
            proof["stock"] = current_stock
            if job.operation == "retire_starter":
                proof["complete"] = (economics.get("bootstrap_districts", {}).get(job.item, {})
                                      .get("lifecycle_state") == "released")
            details = job.checkpoint.get("receipt", {}).get("details") or {}
            owner = details.get("stage") or details.get("owner")
            canonical = f"paired_mall_{job.item}"
            if (owner in {f"seed_vertical_mall_{job.item}_north", f"seed_vertical_mall_{job.item}_south"}
                    and canonical in economics.get("projects", {})):
                owner = canonical
            if owner in economics.get("projects", {}):
                proof["reserved"] = economics["projects"][owner]["reserved"]
                proof["funded"] = economics["projects"][owner]["state"] == "ready"
            owned[job.job_id] = proof
        return {"tick": facts["tick"], "stock": stock, "owned": owned,
                "claims": {item: [(owner, project["reserved"].get(item, 0))
                                  for owner, project in economics.get("projects", {}).items()
                                  if project["reserved"].get(item, 0) > 0]
                           for item in {i for p in economics.get("projects", {}).values() for i in p["reserved"]}},
                "dependency_budget": economics.get("dependency_budget", {}),
                "layout": [len(facts.get("machines", [])), len(facts.get("poles", []))]}

    def _queue_can_produce(self, item):
        from training.laya_mission import product_phase_ready
        return (item in LINE_RECIPES and item not in {"iron-plate", "copper-plate", "stone-brick"}
                and product_phase_ready(item, getattr(self, "reached_phases", ())))

    def _queue_preempt(self, lower, urgent):
        """Atomically hold an idle craft boundary, retain its credits and claims."""
        if urgent.operation != "produce" or not self._queue_can_produce(urgent.item):
            return False
        stock = live_base.transferable_items(self.client, SURFACE, FORCE)
        ledger = ab._MATERIAL_RESERVATION_LEDGER
        free = ledger.allocatable_stock(stock)
        spec = LINE_RECIPES[urgent.item]
        crafts = max(1, math.ceil((urgent.target-urgent.delivered-free.get(urgent.item, 0)) /
                                 float(spec.get("product_amount", 1))))
        if any(free.get(i, 0) < math.ceil(n*crafts) for i,n in
               zip(spec["ingredients"], spec["amounts"], strict=True)):
            return False
        loan = next((l for l in mall_bootstrap.active_bootstrap_loans(self.client, SURFACE, FORCE)
                     if l.target_item == lower.item), None)
        if loan is None:
            return False
        # One RCON transaction checks recipe, force and craft boundary before
        # disabling. No reset or recipe change is allowed during a craft.
        position = loan.machine_position
        lua = ("local s=game.surfaces[" + json.dumps(SURFACE) + "];"
               "local e=s.find_entities_filtered{position={" + str(position[0])+","+str(position[1]) +
               "},radius=0.1,type='assembling-machine',force="+json.dumps(FORCE)+",limit=1}[1];"
               "if e and e.get_recipe() and e.get_recipe().name=="+json.dumps(loan.step_recipe or loan.current_recipe)+
               " and e.crafting_progress==0 then e.disabled_by_script=true;rcon.print('held') else rcon.print('boundary_wait') end")
        lower.checkpoint["hold_position"] = list(position)
        self.queue.save()
        if self.client.command("/sc " + lua).strip() != "held":
            lower.checkpoint.pop("hold_position", None)
            lower.checkpoint["preemption_wait"] = {"for": urgent.job_id, "reason": "craft in progress"}
            from orchestrator.craft_boundary import request_drain
            request_drain(self.client, SURFACE, FORCE, [{"entity": loan.machine_name,
                          "position": {"x": position[0], "y": position[1]}, "recipe": urgent.item}])
            self.queue.save()
            return False
        lower.checkpoint.pop("preemption_wait", None)
        claims = ledger.projects.get("loan_inputs_" + lower.item)
        lower.state = "paused"
        self.queue.save()
        ab._restore_bootstrap_loan(self.client, self.bridge, SURFACE, FORCE, loan, self.emit,
                                  reference_point=self.reference_point, reason="higher-priority funded queue job")
        if claims is not None:
            ledger.declare(claims.project_id, claims.required, stock, target_item=claims.target_item,
                           priority=claims.priority, hold_until_producing=True)
        self._queue_release_hold(lower)
        return True

    def _queue_release_hold(self, job):
        position = job.checkpoint.get("hold_position")
        if position is None:
            return
        lua = ("local e=game.surfaces["+json.dumps(SURFACE)+"].find_entities_filtered{position={"+
               str(position[0])+","+str(position[1])+"},radius=0.1,type='assembling-machine',force="+
               json.dumps(FORCE)+",limit=1}[1];if e then e.disabled_by_script=false end")
        self.client.command("/sc " + lua)
        job.checkpoint.pop("hold_position", None)
        self.queue.save()

    def execute_queue(self, facts, economics, reached):
        from orchestrator.execution_slice import execution_slice
        self.reached_phases = tuple(reached)
        self._queue_assign_idle_malls(facts, economics)
        # Recovery from an interrupted atomic hold is local to its owned lane.
        for job in self.queue.jobs.values():
            if "hold_position" in job.checkpoint:
                self._queue_release_hold(job)
        active = [j for j in self.queue.jobs.values() if j.state != "complete"]
        self._queue_dependencies = tuple({j.item for j in active if j.item
                                          and (j.checkpoint.get("resume_construction") is True
                                               or all(self.queue.jobs[d].state == "complete" for d in j.dependencies))})
        ab._BLOCKING_MALL_ITEMS.update(j.item for j in active if j.parents and j.item)
        ledger = ab._MATERIAL_RESERVATION_LEDGER
        ab._refresh_logistic_chest_budget(self.client, SURFACE, FORCE, ledger, self.emit,
                                         dependency_items=self._queue_dependencies)
        stock = economics.get("material_stock")
        if stock is not None:
            ledger.allocatable_stock(stock)  # rebalance after forecast holds change
        economics = {**economics, "dependency_budget": dict(ledger.dependency_budget),
                     "projects": {p.project_id: {"state": p.state, "reserved": dict(p.reserved)}
                                  for p in ledger.projects.values() if ledger._active(p)}}
        snapshot = self._queue_snapshot(facts, economics)
        # Distinct committed item deficits, not surplus stock, justify another
        # lane. Exact kit/material/geometry admission stays in shared tooling.
        waiting = [j for j in active if j.operation == "produce" and j.parents
                   and not j.dependencies and int((stock or {}).get(j.item, 0))+j.delivered < j.target]
        capacity = economics.get("mall_capacity") or {}
        capacity_available = ((economics.get("free_slots") or 0) > 0
                              or (capacity.get("unlimited") is True and capacity.get("committed_slots") is not None))
        if len({j.item for j in waiting}) >= 2 and capacity_available:
            demanded = sorted(waiting, key=lambda j: (-j.priority, j.sequence))[0]
            existing = live_base.find_line(self.client, SURFACE, FORCE, demanded.item,
                                            LINE_RECIPES[demanded.item]["machine"])
            self.queue.enqueue("mall_capacity", {"item": demanded.item,
                                                 "minimum_machines": 1+(existing.machine_count if existing else 0)},
                               priority=demanded.priority+1, tick=facts["tick"], kind="capacity")
        def execute(job, params):
            with execution_slice(checkpoint=job.checkpoint, persist=self.queue.save, runtime=self.queue.runtime):
                result = self.run(job.operation, **params)
            if result.get("loan_batch") is not None:
                self._queue_snapshot(facts, {"material_stock": stock, "loans": [result["loan_batch"]]})
            self.emit(f"QUEUE {job.job_id} priority={job.priority}: {result.get('code')}"
                      + (f" -- {result.get('message', '')[:180]}" if not result.get("ok") else ""))
            return result
        with execution_slice(runtime=self.queue.runtime, persist=self.queue.save):
            receipts = self.queue.pulse(snapshot, execute, can_produce=self._queue_can_produce,
                                        preempt=self._queue_preempt)
        return receipts

    def _queue_assign_idle_malls(self, facts, economics):
        """Fill installed spare capacity from goals/precursors without a policy turn."""
        from orchestrator import mall_builder as mall
        from planners.recipe_data import machine_supports_recipe
        if not callable(getattr(self.client, "command", None)):
            return
        tick = int(facts["tick"])
        stock = economics.get("material_stock")
        if stock is None:
            return  # Unknown stock cannot fund even a first craft.
        self.queue.reconcile(self._queue_snapshot(facts, economics))
        active = [j for j in self.queue.jobs.values() if j.state != "complete"]
        claims = {}
        for job in sorted(active, key=lambda j: j.sequence):
            for key, value in list(job.checkpoint.items()):
                if key.startswith(f"mall_allocation:{SURFACE}:{FORCE}:"):
                    origin, side = value
                    claim = f"{SURFACE}:{FORCE}:{origin[0]}:{origin[1]}:{side}"
                    item = key.rsplit(":", 1)[1]
                    if (claim in claims and claims[claim] != item
                            and not mall.mall_allocation_is_committed(self.client, SURFACE, FORCE, (origin, side))):
                        # Old previews could assign several jobs the same empty
                        # half. Keep the first owner; replan only unbuilt claims.
                        job.checkpoint.pop(key)
                        continue
                    claims.setdefault(claim, item)
        self.queue.runtime["mall_allocations"] = claims
        idle = mall.idle_mall_allocations(self.client, SURFACE, FORCE, self.reference_point)
        free = ab._MATERIAL_RESERVATION_LEDGER.allocatable_stock(stock)
        # A missing direct ingredient inherits urgency. Repeating this walk
        # reaches ready ancestors without taking a slot for an unfunded child.
        pending = sorted(active, key=lambda j: (-j.priority, j.sequence))
        seen = set()
        while pending and idle:
            job = pending.pop(0)
            if job.job_id in seen:
                continue
            seen.add(job.job_id)
            if (job.operation not in {"produce", "mall_slot", "mall_capacity"}
                    or not self._queue_can_produce(job.item)
                    or job.checkpoint.get("pending_entities")):
                continue
            spec = LINE_RECIPES[job.item]
            if spec.get("machine") not in live_base.ASSEMBLER_TIERS or spec.get("fluid_ingredients"):
                continue
            key = f"mall_allocation:{SURFACE}:{FORCE}:{job.item}"
            assigned = job.checkpoint.get(key)
            existing = live_base.find_line(self.client, SURFACE, FORCE, job.item, spec["machine"])
            if existing is not None and job.operation != "produce":
                continue
            missing = [(i, math.ceil(n)) for i,n in zip(spec["ingredients"], spec["amounts"], strict=True)
                       if free.get(i, 0) < math.ceil(n)]
            if assigned is None and missing:
                for item, count in missing:
                    if self._queue_can_produce(item):
                        child = self.queue.enqueue("produce", {"item": item, "stock_target": count},
                            priority=job.priority+1, tick=tick, parent=job.job_id)
                        if child is not None:
                            pending.insert(0, child)
                continue
            if assigned is None and any(self.queue.jobs[d].state != "complete" for d in job.dependencies):
                continue
            selected = next((slot for slot in idle
                if (assigned is None or (list(slot[0]), slot[1]) == (assigned[0], assigned[1]))
                and claims.get(f"{SURFACE}:{FORCE}:{slot[0][0]}:{slot[0][1]}:{slot[1]}", job.item) == job.item
                and machine_supports_recipe(slot[2], job.item)), None)
            if selected is None:
                continue
            origin, side, _machine = selected
            if existing is not None:
                # A live lender/source does not make an installed empty lane
                # useless. A real finite deficit warrants parallel capacity.
                job = self.queue.enqueue("mall_capacity", {"item":job.item,
                    "minimum_machines":existing.machine_count+1}, priority=job.priority+1,
                    tick=tick, kind="capacity")
            job.checkpoint[key] = [list(origin), side]
            claims[f"{SURFACE}:{FORCE}:{origin[0]}:{origin[1]}:{side}"] = job.item
            job.state, job.reason, job.blocked_signature, job.retry_tick = "ready", "idle_mall_assignment", None, tick
            idle.remove(selected)
            for item,n in zip(spec["ingredients"], spec["amounts"], strict=True):
                free[item] = max(0, free.get(item, 0)-math.ceil(n))
            self.emit(f"QUEUE IDLE MALL: {job.item} owns {origin} {side}; continuing installed capacity")
        self.queue.save()

    def set_rate_targets(self, targets: Mapping[str, float]) -> None:
        """Declare the phase's measured products to shared stock maintenance."""
        self.rate_targets = {item for item, rate in targets.items() if rate > 0}

    def prepare_decision(self):
        """Use the deterministic queue's current survey and completed-loan handoff."""
        if hasattr(self, "queue"):
            loans = []
            active_loans = mall_bootstrap.active_bootstrap_loans(self.client, SURFACE, FORCE)
            from orchestrator.craft_boundary import recover_orphan_drains
            recover_orphan_drains(self.client, SURFACE, FORCE, active_loans)
            for loan in active_loans:
                facts = self._loan_batch_facts(loan)
                loans.append(facts)
            tick = int(self.client.command("/sc rcon.print(game.tick)").strip())
            self._queue_snapshot({"tick": tick}, {"material_stock": live_base.transferable_items(
                self.client, SURFACE, FORCE), "loans": loans})
            self.queue.save()
        ab._survey_pass(self.client, SURFACE, FORCE, self.mall_targets, self.priorities)
        ab._release_completed_construction_loans(self.client, self.bridge, SURFACE, FORCE,
                                                 self.reference_point, self.emit)
        # Wait turns still reconcile open bills, and restored cells rejoin
        # their standing role before the next measured observation.
        ab._refresh_owned_standing_gates(self.client, self.bridge, SURFACE, FORCE, self.emit,
                                        rate_targets=getattr(self, "rate_targets", ()),
                                        requested=getattr(self, "mall_targets", {}))

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
            "mall_capacity": lambda item, minimum_machines=1: ab.ensure_produced(
                c, b, SURFACE, FORCE, item, ref, emit, upgrade_bootstrap=False,
                minimum_machines=minimum_machines, allow_promotion=False, standing_mall=True),
            "reserve_steel_seed": lambda: ab._reserve_steel_starter_power_seed(c, SURFACE, FORCE, self.mall_targets, emit),
            # The compact steel seed beside the iron line (gated by the tooling on the iron foundation).
            "steel": lambda stock_target=10: ab.ensure_produced(c, b, SURFACE, FORCE, "steel-plate", ref, emit,
                                                                stock_target=max(1, int(stock_target))),
            # Finish a submitted refinery and retire its plate's starter once the refinery produces.
            "retire_starter": self._retire_starter,
            # Roboport chain toward a position (construction or logistic reach).
            "coverage": lambda x, y, purpose="construction": stage_services.extend_roboport_coverage(
                c, b, SURFACE, FORCE, (x, y), emit, purpose=purpose),
            # Queue the goal technology on this force through the mod's own research command.
            "research": lambda technology: self._set_research(technology),
        }

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

    def _retire_starter(self, recipe: str) -> dict:
        """The tooling retires the starter after the refinery's owned measured output."""
        state = ab._bootstrap_state(recipe)
        if state is None or state.replacement_origin is None:
            return {"lifecycle": getattr(state, "lifecycle_state", None), "submitted": False}
        ab._reconcile_submitted_bootstrap_replacement(self.client, self.bridge, SURFACE, FORCE, state, self.emit)
        return {"lifecycle": ab._bootstrap_state(recipe).lifecycle_state, "submitted": True}

    def run(self, operation: str, **params) -> dict:
        """Run one operation; the tooling's own signals become a typed result.

        ``progress`` records a funded construction plan the operation submitted
        through the shared material ledger, including side-effect submissions
        that happen before an incomplete (shortage or wait) return. A repeated
        bootstrap-loan batch additionally reports its own facts under
        ``loan_batch``: a newly sighted or measurably advancing batch counts as
        progress, a repeated unchanged one does not.
        """
        projects = getattr(ab._MATERIAL_RESERVATION_LEDGER, "projects", {}) or {}
        before = funded_plan_signature(projects)
        try:
            budget_args = ({"dependency_items": self._queue_dependencies}
                           if hasattr(self, "_queue_dependencies") else {})
            ab._refresh_logistic_chest_budget(self.client, SURFACE, FORCE, ab._MATERIAL_RESERVATION_LEDGER,
                                            self.emit, **budget_args)
            ab._refresh_owned_standing_gates(self.client, self.bridge, SURFACE, FORCE, self.emit,
                                            rate_targets=getattr(self, "rate_targets", ()),
                                            requested=getattr(self, "mall_targets", {}))
            # Starter teardown follows owned replacement output on every pass, not only when chosen.
            ab._advance_bootstrap_starter_retirements(self.client, self.bridge, SURFACE, FORCE, self.emit)
            result = self._operations()[operation](**params)
        except WorkStateSignal as signal:
            if isinstance(signal, ab.MaterialShortage):
                ab.add_demands(self.mall_targets, signal)
                ab._mark_binding_demands(signal)
            response = {"ok": False, "operation": operation, "params": params, "code": getattr(signal, "code", None),
                        "classification": getattr(signal, "classification", None),
                        "state": getattr(signal, "state", None), "details": getattr(signal, "details", None),
                        "message": str(signal)[:500]}
        else:
            response = receipt(operation, params, result)
        response["progress"] = funded_plan_progress(
            before, getattr(ab._MATERIAL_RESERVATION_LEDGER, "projects", {}) or {})
        return self._attach_loan_batch(response, params)

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

    def economics(self) -> dict:
        """Committed material bills, scoped replacement lifecycles and live mall loans.

        ``loans`` is None when the survey fails: unknown, not empty. District
        submission is a reconciliation obligation, not a live readiness claim.
        ``reservation_owners`` names the project holding each reserved item and
        ``demand`` is the shared demand-siting read (never training-local
        geometry). ``free_slots`` reports measured capacity, or None if unknown.
        """
        ledger = ab._MATERIAL_RESERVATION_LEDGER
        projects = {}
        for project in ledger.projects.values():
            if ledger._active(project):
                missing = {i: n - project.reserved.get(i, 0) for i, n in project.required.items()
                           if project.reserved.get(i, 0) < n}
                projects[project.project_id] = {"state": project.state, "reserved": dict(project.reserved),
                                                "missing": missing}
        loan_error = None
        spendable = None
        try:
            active = mall_bootstrap.active_bootstrap_loans(self.client, SURFACE, FORCE)
            try:
                spendable = live_base.transferable_items(self.client, SURFACE, FORCE)
            except Exception:  # noqa: BLE001 -- known-unknown, not zero stock
                spendable = None
            loans = []
            for loan in active:
                facts = self._loan_batch_facts(loan, available=spendable)
                # Read-only classification: economics never advances the run's
                # own seen-state, or an acceptance survey would mask progress.
                facts["status"] = self._loan_batch_status(loan, facts)
                loans.append(facts)
        except Exception as error:  # A failed survey is an unknown, never "no loans".
            loans = None
            loan_error = f"{type(error).__name__}: {error}"[:200]
        # This scoped ledger survives shortage returns while bots finish submitted work.
        # Reading it does not assert readiness; the retirement tool verifies live output.
        districts = {}
        for state in ab._BOOTSTRAP_DISTRICT_LEDGER.states():
            pending = None
            if callable(getattr(self.client, "command", None)):
                plan = (ab._submitted_bootstrap_plan(state) if state.replacement_actions else
                        {"phases": [{"actions": state.pioneer_actions}]})
                try:
                    pending = bool(live_base.unbuilt_plan_items(self.client, SURFACE, FORCE, plan))
                except Exception:
                    pass  # unknown never certifies that owned construction finished
            districts[state.recipe] = {"lifecycle_state": state.lifecycle_state,
                                        "replacement_planned": bool(state.replacement_actions),
                                        "replacement_submitted": state.replacement_submitted,
                                        "construction_pending": pending}
        try:
            from orchestrator.execution_slice import execution_slice
            if hasattr(self, "queue"):
                with execution_slice(runtime=self.queue.runtime):
                    limit = ab._bootstrap_mall_slot_limit(self.client, SURFACE, FORCE)
            else:
                limit = ab._bootstrap_mall_slot_limit(self.client, SURFACE, FORCE)
            capacity = ab._mall_capacity_facts(self.client, SURFACE, self.reference_point, limit)
        except Exception:
            capacity = {"committed_slots": None, "slot_limit": None, "free_slots": None, "unlimited": None}
        targets = dict(getattr(self, "mall_targets", {}))
        return {"projects": projects, "loans": loans, "loan_error": loan_error,
                "mall_targets": targets, "blocking_items": sorted(ab._BLOCKING_MALL_ITEMS),
                "precursors": {item: sorted(ab._queued_mall_prerequisites(item)) for item in targets},
                "material_stock": spendable,
                "bootstrap_districts": districts, "reservation_owners": reservation_owners(projects),
                "demand": self._demand_facts(), "free_slots": capacity["free_slots"],
                "mall_capacity": {key: capacity[key] for key in ("committed_slots", "slot_limit", "unlimited")}}

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
