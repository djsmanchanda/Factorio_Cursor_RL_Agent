# Path: experimental/legacy_autonomy/loop_daemon.py
# Purpose: Recurring sandbox expansion loop: observe -> project -> construct -> reconcile -> phase advance, until the build intent is satisfied.

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.block_prototypes import placeholder_prototype
from core.capacity_phasing_policy import evaluate_capacity_phasing
from core.execution_authorizer import authorize_execution
from core.execution_readiness import propose_execution
from core.phase_advance_evaluator import authorize_phase_advance, propose_phase_advance
from core.progress_state import build_progress_state
from core.sandbox_zoning import derive_sandbox_zones
from orchestrator.game_bridge import BridgeError, GameBridge, load_json
from planners.city_planner.ghost_projection_phase import generate_ghost_plan
from planners.sandbox_infrastructure import (
    build_layout_authorization,
    compose_managed_sandbox,
    require_compatible_topology,
)

SANDBOX_SURFACE = "planner-sandbox"


def _block_types(build_intent: dict) -> list[str]:
    return sorted({str(i.get("block_type", "")) for i in build_intent.get("intents", []) if i.get("block_type")})


def _material_targets(build_intent: dict, current_by_prototype: dict) -> dict:
    # Stock enough of each placeholder item to finish the whole intent.
    targets: dict = {}
    for intent in build_intent.get("intents", []):
        prototype = placeholder_prototype(str(intent.get("block_type", "")))
        remaining = int(intent.get("count", 0)) - int(current_by_prototype.get(prototype, 0))
        if remaining > 0:
            targets[prototype] = targets.get(prototype, 0) + remaining
    return targets


def _count_by_prototype(snapshot: dict, build_intent: dict) -> dict:
    prototypes = {placeholder_prototype(str(i.get("block_type", ""))) for i in build_intent.get("intents", [])}
    counts: dict = {}
    for entity in snapshot.get("entities", []):
        name = entity.get("name")
        if name in prototypes:
            counts[name] = counts.get(name, 0) + 1
    return counts


def run_iteration(bridge: GameBridge, build_intent: dict, build_intent_path: Path, work_dir: Path, iteration: int) -> dict:
    iter_dir = work_dir / f"iter_{iteration:03d}"
    iter_dir.mkdir(parents=True, exist_ok=True)

    # Observation first: it creates the sandbox surface if missing, so the
    # surface-scoped snapshot below cannot fail on a fresh save.
    ghost_observation_path = bridge.export_ghost_observation()

    snapshot_path = bridge.request_snapshot(surface=SANDBOX_SURFACE, timeout=120.0)
    snapshot = load_json(snapshot_path)
    metrics_path = iter_dir / "metrics.json"
    metrics_path.write_text(json.dumps({"labs_count": 0}), encoding="utf-8")

    progress = build_progress_state(
        snapshot_path=snapshot_path,
        metrics_path=metrics_path,
        build_intent_path=build_intent_path,
        ghost_observation_path=ghost_observation_path,
    ).to_dict()

    current = int(progress["current_capacity"])
    committed = int(progress["committed_capacity"])
    ultimate = int(progress["ultimate_capacity"])
    pending = committed - current

    status = {
        "iteration": iteration,
        "tick": snapshot.get("tick"),
        "current": current,
        "committed": committed,
        "pending_ghosts": pending,
        "ultimate": ultimate,
        "active_phase": int(progress["active_phase_capacity"]),
        "actions": [],
    }

    if current >= ultimate:
        status["done"] = True
        return status

    # Authorized phase advancement when the active phase is fully built.
    if current >= int(progress["active_phase_capacity"]):
        proposal = propose_phase_advance(progress, {"status": "OK"}).to_dict()
        if proposal["status"] == "ELIGIBLE":
            advance = authorize_phase_advance(
                proposal, approved=True, authorization_source="policy", reason="phase_complete"
            ).to_dict()
            progress["active_phase_capacity"] = int(advance["approved_next_phase"])
            status["active_phase"] = progress["active_phase_capacity"]
            status["actions"].append(f"phase_advance->{progress['active_phase_capacity']}")

    ghost_observation = load_json(ghost_observation_path)
    pending_by_block: dict = {}
    for entry in ghost_observation.get("ghosts", []):
        block = entry.get("tags", {}).get("block", "?")
        pending_by_block[block] = pending_by_block.get(block, 0) + 1

    current_by_prototype = _count_by_prototype(snapshot, build_intent)
    built_by_block = {
        block: current_by_prototype.get(placeholder_prototype(block), 0) for block in _block_types(build_intent)
    }
    existing_by_block = {
        block: built_by_block.get(block, 0) + pending_by_block.get(block, 0) for block in _block_types(build_intent)
    }

    phasing = evaluate_capacity_phasing(progress, build_intent).to_dict()
    ghost_plan = generate_ghost_plan(
        build_intent=build_intent,
        progress_state=progress,
        capacity_phasing=phasing,
        existing_by_block=existing_by_block,
    )

    # Scaffolding is provisioned every iteration; the mod command is idempotent.
    # Each zone anchor gets its own network and its own material stock.
    zones = derive_sandbox_zones(_block_types(build_intent))
    intent_totals = {
        str(i.get("block_type", "")): int(i.get("count", 0)) for i in build_intent.get("intents", [])
    }
    anchor_payload = []
    for block_id, zone in sorted(zones.items()):
        remaining = max(0, intent_totals.get(block_id, 0) - built_by_block.get(block_id, 0))
        anchor_materials = {placeholder_prototype(block_id): remaining} if remaining else {}
        anchor_payload.append({"x": zone.origin_x + 10, "materials": anchor_materials})
    materials = _material_targets(build_intent, current_by_prototype)
    require_compatible_topology(bridge)
    composition = compose_managed_sandbox(
        [], anchor_payload, materials, bots_per_roboport=30
    )
    infrastructure_authorization = build_layout_authorization(
        composition["infrastructure"]
    )
    for name, infrastructure_plan in composition["infrastructure"]:
        report = load_json(bridge.build_layout(infrastructure_authorization, infrastructure_plan))
        if not report.get("ok"):
            raise BridgeError(f"Managed infrastructure {name} failed: {report.get('error')}")
    bridge.ensure_scaffolding(composition["scaffolding"])
    status["actions"].append("managed_scaffolding_ensured")

    # Full decision trace: every intermediate payload the pipeline reasoned
    # with, so external viewers (dashboard) can show the agent's thinking.
    ghost_summary: dict = {}
    for ghost in ghost_plan.ghosts:
        block = ghost["tags"].get("block", "?")
        ghost_summary[block] = ghost_summary.get(block, 0) + 1
    map_names = {placeholder_prototype(b) for b in _block_types(build_intent)} | {
        "roboport", "passive-provider-chest", "storage-chest", "substation", "electric-energy-interface"
    }
    intent_counts: dict = {}
    for intent in build_intent.get("intents", []):
        block = str(intent.get("block_type", ""))
        intent_counts[block] = intent_counts.get(block, 0) + int(intent.get("count", 0))

    status["thinking"] = {
        "intent_counts": intent_counts,
        "progress_state": dict(progress),
        "capacity_phasing": phasing,
        "fill_delta": len(ghost_plan.ghosts),
        "ghosts_planned_by_block": ghost_summary,
        "pending_ghosts_by_block": pending_by_block,
        "materials_requested": materials,
        "built_by_prototype": current_by_prototype,
        "zones": {block_id: zone.to_dict() for block_id, zone in zones.items()},
        "map_entities": [
            {"name": e["name"], "x": e["position"]["x"], "y": e["position"]["y"]}
            for e in snapshot.get("entities", [])
            if e.get("name") in map_names
        ],
    }

    if ghost_plan.ghosts:
        proposal = propose_execution(progress, phasing, build_intent, "OK").to_dict()
        status["thinking"]["execution_proposal"] = proposal
        if "project_more_ghosts" in proposal.get("allowed_actions", []):
            authorization = authorize_execution(
                proposal=proposal,
                approved_actions=["project_more_ghosts"],
                authorization_source="policy",
            ).to_dict()
            status["thinking"]["authorization"] = authorization
            execution_report_path = bridge.execute_ghost_plan(authorization, {"ghosts": list(ghost_plan.ghosts)})
            execution_report = load_json(execution_report_path)
            placed = execution_report["actions"][0].get("count", 0)
            status["actions"].append(f"projected_{placed}_ghosts")

            construction_report_path = bridge.execute_construction(authorization, execution_report)
            construction_report = load_json(construction_report_path)
            if construction_report.get("blocked"):
                status["actions"].append(f"construction_blocked:{construction_report.get('blocked_reason')}")
            else:
                status["actions"].append(f"construction_started_{construction_report.get('started', 0)}")

    (iter_dir / "status.json").write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Recurring sandbox expansion loop against a live Factorio server.")
    parser.add_argument("--script-output", required=True)
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27015)
    parser.add_argument("--rcon-password", required=True)
    parser.add_argument("--build-intent", required=True)
    parser.add_argument("--interval", type=float, default=15.0, help="Seconds between iterations")
    parser.add_argument("--max-iterations", type=int, default=40)
    parser.add_argument("--stall-limit", type=int, default=6, help="Abort after this many iterations without progress")
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args()

    build_intent_path = Path(args.build_intent)
    build_intent = load_json(build_intent_path)

    work_dir = Path(args.work_dir) if args.work_dir else REPO_ROOT / "runs" / f"loop_{int(time.time())}"
    work_dir.mkdir(parents=True, exist_ok=True)

    bridge = GameBridge(
        script_output=Path(args.script_output),
        host=args.rcon_host,
        port=args.rcon_port,
        password=args.rcon_password,
    )

    last_progress = (-1, -1)
    stalled = 0
    try:
        for iteration in range(1, args.max_iterations + 1):
            status = run_iteration(bridge, build_intent, build_intent_path, work_dir, iteration)
            print(
                f"[iter {status['iteration']:03d}] tick={status['tick']} built={status['current']}/{status['ultimate']} "
                f"pending={status['pending_ghosts']} phase={status['active_phase']} actions={','.join(status['actions']) or 'none'}"
            )
            if status.get("done"):
                print(f"BUILD INTENT SATISFIED: {status['current']}/{status['ultimate']} built.")
                return 0

            progress_key = (status["current"], status["committed"])
            if progress_key == last_progress:
                stalled += 1
                if stalled >= args.stall_limit:
                    print(
                        f"STALLED: no progress for {stalled} iterations "
                        f"(built={status['current']}, committed={status['committed']}). Aborting.",
                        file=sys.stderr,
                    )
                    return 2
            else:
                stalled = 0
                last_progress = progress_key

            time.sleep(args.interval)

        print("Reached max iterations without satisfying the build intent.", file=sys.stderr)
        return 3
    except (BridgeError, ValueError) as exc:
        print(f"LOOP FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        bridge.close()


if __name__ == "__main__":
    sys.exit(main())
