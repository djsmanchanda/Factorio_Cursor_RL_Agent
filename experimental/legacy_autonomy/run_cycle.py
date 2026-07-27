# Path: experimental/legacy_autonomy/run_cycle.py
# Purpose: Run one deterministic observe -> supervise -> plan (-> authorized ghost execution) cycle against a live game.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jsonschema import Draft7Validator

from core.capacity_phasing_policy import evaluate_capacity_phasing
from core.execution_authorizer import authorize_execution
from core.execution_readiness import propose_execution
from core.factory_graph import FactoryGraph
from core.metrics import build_phase_metrics, compute_all_metrics
from core.progress_state import build_progress_state
from orchestrator.game_bridge import BridgeError, GameBridge, load_json
from planners.city_planner.capability_resolver import resolve_capabilities
from planners.city_planner.ghost_projection_phase import generate_ghost_plan
from planners.city_planner.intent_router import route_intents
from planners.city_planner.phase_orchestrator import build_planning_bundle
from planners.city_planner.plan_skeleton import generate_plan_skeleton
from planners.city_planner.planning_gate import decide_planning
from planners.supervisor.intent_generator import generate_intents
from planners.supervisor.policy_evaluator import PolicyEvaluator

# Deterministic default capability context: no city grid exists yet, so only
# grid-independent capabilities resolve as available.
DEFAULT_CAPABILITY_CONTEXT = {"city_grid": False}


def _validate_against(payload: dict, schema_name: str, label: str) -> None:
    schema_path = REPO_ROOT / "schemas" / schema_name
    with schema_path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    errors = list(Draft7Validator(schema).iter_errors(payload))
    if errors:
        messages = []
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {path}: {error.message}")
        raise ValueError(f"{label} validation FAILED:\n" + "\n".join(messages))


def observe(bridge: GameBridge, report_dir: Path, snapshot_timeout: float) -> dict:
    snapshot_path = bridge.request_snapshot(timeout=snapshot_timeout)
    snapshot = load_json(snapshot_path)
    _validate_against(snapshot, "snapshot.schema.json", "Snapshot")

    graph = FactoryGraph(snapshot)
    bot_metrics, production_metrics, power_metrics = compute_all_metrics(graph)
    phase_metrics = build_phase_metrics(graph)

    metrics_path = report_dir / "metrics.json"
    with metrics_path.open("w", encoding="utf-8") as handle:
        json.dump(phase_metrics, handle, indent=2, sort_keys=True)

    return {
        "snapshot_path": snapshot_path,
        "snapshot": snapshot,
        "graph": graph,
        "bot_metrics": bot_metrics,
        "production_metrics": production_metrics,
        "power_metrics": power_metrics,
        "phase_metrics": phase_metrics,
        "metrics_path": metrics_path,
    }


def supervise(bot_metrics) -> dict:
    evaluator = PolicyEvaluator()
    signals = evaluator.evaluate(bot_metrics)
    intents = generate_intents(signals)
    return {
        "signals": [signal.__dict__ for signal in signals],
        "intents": [intent.to_dict() for intent in intents],
    }


def plan(intents: List[dict], capability_context: dict) -> List[dict]:
    outcomes: List[dict] = []
    requests = route_intents(intents)
    for request in requests:
        request_dict = request.to_dict() if hasattr(request, "to_dict") else request.__dict__
        resolution = resolve_capabilities(request_dict, capability_context)
        resolution_dict = resolution.to_dict() if hasattr(resolution, "to_dict") else resolution.__dict__
        decision = decide_planning(resolution_dict)
        decision_dict = decision.to_dict() if hasattr(decision, "to_dict") else decision.__dict__

        outcome = {
            "request": request_dict,
            "resolution": resolution_dict,
            "gate_decision": decision_dict,
        }
        if decision_dict.get("status") == "ready":
            skeleton = generate_plan_skeleton(request_dict, resolution_dict, decision_dict)
            skeleton_dict = skeleton.to_dict() if hasattr(skeleton, "to_dict") else skeleton.__dict__
            bundle = build_planning_bundle(skeleton_dict, resolution_dict)
            outcome["plan_skeleton"] = skeleton_dict
            outcome["planning_bundle"] = bundle.to_dict() if hasattr(bundle, "to_dict") else bundle.__dict__
        outcomes.append(outcome)
    return outcomes


def project_ghosts(
    bridge: GameBridge,
    observation: dict,
    build_intent_path: Path,
    report_dir: Path,
    reconciliation_status: str,
    execute: bool,
    progress_state_path: Optional[Path] = None,
) -> dict:
    build_intent = load_json(build_intent_path)

    if progress_state_path is not None:
        # Hand-authored progress state override (same precedent as the inspect_* CLIs).
        progress_dict = load_json(progress_state_path)
        _validate_against(progress_dict, "progress_state.schema.json", "ProgressState")
    else:
        # Pending sandbox ghosts count toward committed capacity, so export the
        # current ghost observation before deriving progress.
        ghost_observation_path = bridge.export_ghost_observation()
        progress = build_progress_state(
            snapshot_path=observation["snapshot_path"],
            metrics_path=observation["metrics_path"],
            build_intent_path=build_intent_path,
            ghost_observation_path=ghost_observation_path,
        )
        progress_dict = progress.to_dict()

    phasing = evaluate_capacity_phasing(progress_dict, build_intent)
    phasing_dict = phasing.to_dict()

    ghost_plan = generate_ghost_plan(
        build_intent=build_intent,
        progress_state=progress_dict,
        capacity_phasing=phasing_dict,
    )
    ghost_plan_dict = ghost_plan.to_dict()

    result = {
        "progress_state": progress_dict,
        "capacity_phasing": phasing_dict,
        "ghost_plan": ghost_plan_dict,
        "executed": False,
    }

    if not ghost_plan_dict["ghosts"]:
        result["skipped_reason"] = "Ghost plan is empty (no delta capacity to project)."
        return result

    proposal = propose_execution(
        progress_state=progress_dict,
        capacity_phasing=phasing_dict,
        build_intent=build_intent,
        reconciliation_status=reconciliation_status,
    )
    proposal_dict = proposal.to_dict()
    result["execution_proposal"] = proposal_dict

    if "project_more_ghosts" not in proposal_dict.get("allowed_actions", []):
        result["skipped_reason"] = "Execution proposal does not allow project_more_ghosts."
        return result

    authorization = authorize_execution(
        proposal=proposal_dict,
        approved_actions=["project_more_ghosts"],
        authorization_source="policy",
    )
    authorization_dict = authorization.to_dict()
    result["authorization"] = authorization_dict

    if not execute:
        result["skipped_reason"] = "Dry run: pass --execute to send the authorized ghost plan to the game."
        return result

    execution_report_path = bridge.execute_ghost_plan(
        authorization_dict, {"ghosts": ghost_plan_dict["ghosts"]}
    )
    result["execution_report"] = load_json(execution_report_path)
    result["executed"] = True

    ghost_observation_path = bridge.export_ghost_observation()
    ghost_observation = load_json(ghost_observation_path)
    _validate_against(ghost_observation, "ghost_observation.schema.json", "GhostObservation")
    result["ghost_observation"] = ghost_observation
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one observe/supervise/plan cycle against a live Factorio server.")
    parser.add_argument("--script-output", required=True, help="Path to the server's script-output directory")
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27015)
    parser.add_argument("--rcon-password", required=True)
    parser.add_argument("--report-dir", default=None, help="Directory for cycle artifacts (default: runs/<tick>)")
    parser.add_argument("--build-intent", default=None, help="BuildIntent JSON path; enables the ghost projection stage")
    parser.add_argument("--progress-state", default=None, help="Hand-authored ProgressState JSON overriding snapshot-derived state")
    parser.add_argument("--capability-context", default=None, help="JSON file of capability context booleans")
    parser.add_argument("--reconciliation-status", default="OK", choices=["OK", "PARTIAL", "BLOCKED"])
    parser.add_argument("--execute", action="store_true", help="Send the authorized ghost plan to the game (sandbox surface only)")
    parser.add_argument("--snapshot-timeout", type=float, default=300.0)
    args = parser.parse_args()

    report_dir = Path(args.report_dir) if args.report_dir else None
    bridge = GameBridge(
        script_output=Path(args.script_output),
        host=args.rcon_host,
        port=args.rcon_port,
        password=args.rcon_password,
    )
    try:
        staging_dir = report_dir or (REPO_ROOT / "runs" / "staging")
        staging_dir.mkdir(parents=True, exist_ok=True)

        observation = observe(bridge, staging_dir, args.snapshot_timeout)
        tick = observation["snapshot"].get("tick", 0)

        if report_dir is None:
            report_dir = REPO_ROOT / "runs" / f"cycle_{tick}"
            report_dir.mkdir(parents=True, exist_ok=True)
            metrics_path = report_dir / "metrics.json"
            observation["metrics_path"].replace(metrics_path)
            observation["metrics_path"] = metrics_path

        supervision = supervise(observation["bot_metrics"])

        capability_context = dict(DEFAULT_CAPABILITY_CONTEXT)
        if args.capability_context:
            capability_context.update(load_json(Path(args.capability_context)))
        planning = plan(supervision["intents"], capability_context)

        cycle = {
            "tick": tick,
            "surface": observation["snapshot"].get("surface"),
            "entity_count": len(observation["snapshot"].get("entities", [])),
            "snapshot_file": str(observation["snapshot_path"]),
            "bot_metrics": observation["bot_metrics"].__dict__,
            "phase_metrics": observation["phase_metrics"],
            "supervision": supervision,
            "planning": planning,
        }

        if args.build_intent:
            cycle["ghost_projection"] = project_ghosts(
                bridge=bridge,
                observation=observation,
                build_intent_path=Path(args.build_intent),
                report_dir=report_dir,
                reconciliation_status=args.reconciliation_status,
                execute=args.execute,
                progress_state_path=Path(args.progress_state) if args.progress_state else None,
            )

        report_path = report_dir / "cycle_report.json"
        with report_path.open("w", encoding="utf-8") as handle:
            json.dump(cycle, handle, indent=2, sort_keys=True, default=str)

        print(f"Cycle complete at tick {tick}: {cycle['entity_count']} entities observed.")
        print(f"Policy signals: {len(supervision['signals'])}, intents: {len(supervision['intents'])}, planning outcomes: {len(planning)}")
        ghost = cycle.get("ghost_projection")
        if ghost:
            if ghost["executed"]:
                placed = ghost["execution_report"]["actions"][0].get("count")
                observed = len(ghost.get("ghost_observation", {}).get("ghosts", []))
                print(f"Ghost projection executed: {placed} ghosts placed, {observed} observed on planner-sandbox.")
            else:
                print(f"Ghost projection not executed: {ghost.get('skipped_reason')}")
        print(f"Report: {report_path}")
        return 0
    except (BridgeError, ValueError) as exc:
        print(f"CYCLE FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        bridge.close()


if __name__ == "__main__":
    sys.exit(main())
