# Path: tools/electronics_execution.py
# Purpose: Execute surveyed electronics bundles and validate measured live outcomes.

from __future__ import annotations

import copy
import json
import time
from collections import Counter
from pathlib import Path
from typing import Callable, Mapping, Sequence

from jsonschema import Draft7Validator

from orchestrator.game_bridge import GameBridge, load_json
from planners.electronics_world import ElectronicsWorldSpec
from planners.local_layout_planner import LocalLayoutPlanner
from planners.sandbox_infrastructure import build_layout_authorization, topology_is_compatible

REPO_ROOT = Path(__file__).resolve().parents[1]
_LIVE_REPORT_SCHEMA = REPO_ROOT / "schemas" / "live_execution_report.schema.json"


class ElectronicsExecutionError(RuntimeError):
    """A live execution or measured invariant failed."""

    def __init__(self, message: str, *, report: dict | None = None):
        super().__init__(message)
        self.report = report


def combine_named_plans(named_plans: Sequence[tuple[str, dict]]) -> dict:
    """Flatten named plans while preserving each plan/phase identity."""
    phases = [
        {
            "name": f"{plan_name}/{phase['name']}",
            "actions": copy.deepcopy(phase.get("actions", [])),
        }
        for plan_name, plan in named_plans
        for phase in plan.get("phases", [])
    ]
    if not phases or not any(phase["actions"] for phase in phases):
        raise ValueError("Cannot execute an empty electronics plan group")
    return {"phases": phases}


def production_materials(named_plans: Sequence[tuple[str, dict]]) -> dict[str, int]:
    """Count only ghost construction items; direct placements need no bot material."""
    planner = LocalLayoutPlanner()
    required: Counter[str] = Counter()
    for _, plan in named_plans:
        required.update(planner.material_requirements(plan))
    return dict(sorted(required.items()))


def scaffolding_with_materials(scaffolding: Mapping, materials: Mapping[str, int]) -> dict:
    """Attach construction materials to the canonical hub's managed chests."""
    payload = copy.deepcopy(dict(scaffolding))
    anchors = payload.get("anchors")
    if not isinstance(anchors, list) or not anchors:
        raise ValueError("Managed scaffolding needs at least one anchor")
    anchors[0]["materials"] = {
        str(item): int(count) for item, count in sorted(materials.items()) if int(count) > 0
    }
    return payload


def ore_seeding_payload(world: ElectronicsWorldSpec, *, amount: int = 100000) -> dict:
    """Derive the mod's ore_patches seeding payload straight from the surveyed
    WorldSpec, so seeded ore lands exactly on the surveyed rectangles the
    mining rows were placed against -- never guessed coordinates."""
    if not world.ore_patches:
        raise ValueError("WorldSpec declares no ore patches to seed")
    return {
        "ore_patches": [
            {
                "id": patch["id"],
                "item": patch["item"],
                "x1": patch["x1"],
                "y1": patch["y1"],
                "x2": patch["x2"],
                "y2": patch["y2"],
                "amount": amount,
            }
            for patch in world.ore_patches
        ]
    }


def drill_footprints_covered(world: ElectronicsWorldSpec) -> bool:
    """True iff every surveyed mining-drill position lies inside some patch
    rectangle the seeding payload will seed (i.e. no drill is left barren)."""
    patches = {patch["id"]: patch for patch in world.ore_patches}

    def _inside(position: tuple[float, float], patch_id: str) -> bool:
        patch = patches.get(patch_id)
        if patch is None:
            return False
        x, y = position
        return patch["x1"] <= x <= patch["x2"] and patch["y1"] <= y <= patch["y2"]

    for line in world.ore_lines:
        if not all(_inside(position, line["patch_id"]) for position in line["drill_positions"]):
            return False
    return all(_inside(position, world.coal_patch_id) for position in world.coal_drill_positions)


def _seed_ore(
    bridge: GameBridge,
    world: ElectronicsWorldSpec,
    emit: Callable[[str], None],
) -> dict:
    """Seed every surveyed ore patch. Must run after any reset and before
    construction: drills placed on bare ground mine nothing."""
    if not drill_footprints_covered(world):
        raise ElectronicsExecutionError(
            "WorldSpec has a surveyed mining drill outside every declared ore patch; "
            "refusing to seed a payload that would leave a drill on bare ground"
        )
    report = _load_report(bridge.seed_ore_patches(ore_seeding_payload(world)))
    if not report.get("ok"):
        raise ElectronicsExecutionError(
            f"Ore seeding failed: {report.get('error', 'unknown error')}", report=report
        )
    emit(
        "ore seeding: "
        f"seeded_tiles={report.get('seeded_ore_tiles', 0)}, "
        f"by_resource={report.get('seeded_by_resource', {})}"
    )
    return report


def prepare_existing_topology(bridge: GameBridge, mode: str) -> dict:
    """Accept the canonical topology; mutate incompatible topology only explicitly."""
    topology = load_json(bridge.inspect_sandbox_topology())
    existing = (
        int(topology.get("planner_factory_entities", 0))
        + int(topology.get("player_factory_entities", 0))
    ) > 0
    if not existing or topology_is_compatible(topology):
        return topology
    if mode == "refuse":
        raise ElectronicsExecutionError(
            "planner-sandbox contains incompatible factory topology; inspect it and rerun "
            "with --existing-topology reconcile or reset"
        )
    result = load_json(bridge.reconcile_sandbox_topology(mode, confirm=True))
    if not result.get("ok"):
        raise ElectronicsExecutionError(
            f"Topology {mode} failed: {result.get('error', 'unknown error')}", report=result
        )
    updated = load_json(bridge.inspect_sandbox_topology())
    if mode == "reconcile" and not topology_is_compatible(updated):
        raise ElectronicsExecutionError(
            "Reconciled topology is not the exact canonical planner backbone; reset is required",
            report=updated,
        )
    return updated


def wait_for_game_ticks(
    bridge: GameBridge,
    tick_count: int,
    *,
    timeout_seconds: float = 300.0,
    max_stagnant_polls: int = 20,
    poll_seconds: float = 0.25,
) -> None:
    """Wait in Factorio ticks and fail quickly if a headless server is paused."""
    if tick_count < 0:
        raise ValueError("settle ticks must be non-negative")
    start_tick = int(bridge.command("/sc rcon.print(game.tick)").strip())
    target_tick = start_tick + tick_count
    deadline = time.monotonic() + timeout_seconds
    last_tick = start_tick
    stagnant_polls = 0
    while last_tick < target_tick:
        if time.monotonic() >= deadline:
            raise ElectronicsExecutionError(
                f"Factorio did not reach tick {target_tick} before the settle timeout"
            )
        time.sleep(poll_seconds)
        current_tick = int(bridge.command("/sc rcon.print(game.tick)").strip())
        if current_tick <= last_tick:
            stagnant_polls += 1
            if stagnant_polls >= max_stagnant_polls:
                raise ElectronicsExecutionError(
                    f"Factorio tick stalled at {current_tick}; set auto_pause=false"
                )
        else:
            stagnant_polls = 0
        last_tick = current_tick


def _load_report(value: Path | dict) -> dict:
    return value if isinstance(value, dict) else load_json(value)


def _placement_failure_lines(report: Mapping) -> list[str]:
    lines = []
    for failure in report.get("placement_failures", []):
        position = failure.get("position", {})
        lines.append(
            f"{failure.get('phase', '?')}: {failure.get('entity', '?')} at "
            f"({position.get('x', '?')}, {position.get('y', '?')}): "
            f"{failure.get('reason', 'unknown')}"
        )
    return lines


def validate_layout_report(report: Mapping, label: str) -> dict:
    """Reject partial placement and expose every failed action."""
    normalized = dict(report)
    for kind in ("ghosts", "entities"):
        attempted = normalized.get(f"attempted_{kind}")
        placed = normalized.get(f"placed_{kind}")
        existing = normalized.get(f"already_present_{kind}")
        failed = normalized.get(f"failed_{kind}")
        if not all(isinstance(value, int) and value >= 0 for value in (
            attempted, placed, existing, failed,
        )):
            raise ElectronicsExecutionError(
                f"{label} returned malformed {kind} placement counters", report=normalized
            )
        if attempted != placed + existing + failed:
            raise ElectronicsExecutionError(
                f"{label} placement counters do not reconcile for {kind}", report=normalized
            )
    attempted_total = normalized["attempted_ghosts"] + normalized["attempted_entities"]
    if attempted_total == 0:
        raise ElectronicsExecutionError(f"{label} attempted zero placements", report=normalized)
    failures = normalized["failed_ghosts"] + normalized["failed_entities"]
    if not normalized.get("ok") or failures:
        details = _placement_failure_lines(normalized)
        suffix = "\n" + "\n".join(details) if details else ""
        raise ElectronicsExecutionError(
            f"{label} failed {failures} of {attempted_total} placement attempts{suffix}",
            report=normalized,
        )
    return normalized


def validate_live_report(report: Mapping) -> dict:
    """Schema-check and independently enforce every measured M6 invariant."""
    normalized = dict(report)
    schema = json.loads(_LIVE_REPORT_SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(Draft7Validator(schema).iter_errors(normalized), key=lambda e: list(e.path))
    if errors:
        details = "; ".join(
            f"{'/'.join(map(str, error.path)) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ElectronicsExecutionError(
            f"Live execution report failed schema validation: {details}", report=normalized
        )

    violations: list[str] = []
    if normalized["power_sources"] != 1:
        violations.append(f"power_sources={normalized['power_sources']} (expected 1)")
    electric = normalized["electric_samples"]
    if len(electric["network_ids"]) != 1:
        violations.append(
            f"electric_network_ids={electric['network_ids']} (expected one distinct id)"
        )
    if electric["missing"]:
        violations.append(f"unpowered_samples={len(electric['missing'])}")
    for category in ("machines", "drills", "inserters", "roboports"):
        if electric["sampled_by_category"].get(category, 0) == 0:
            violations.append(f"no electric samples for {category}")

    logistic = normalized["logistic_roboports"]
    if len(logistic["network_ids"]) != 1 or logistic["missing"]:
        violations.append(
            f"logistic_network_ids={logistic['network_ids']}, missing={logistic['missing']}"
        )
    if normalized["ghosts"]["remaining"]:
        violations.append(f"remaining_ghosts={normalized['ghosts']['remaining']}")

    for machine in normalized["fluid_machines"]:
        for fluid_box in machine["input_boxes"]:
            if fluid_box["amount"] <= 0:
                violations.append(
                    f"empty input box {fluid_box['index']} on {machine['entity']} "
                    f"at {machine['position']}"
                )
        if machine["entity"] == "oil-refinery" and machine["status"] != "working":
            violations.append(
                f"refinery at {machine['position']} status={machine['status']} (expected working)"
            )
    if not normalized["fluid_machines"]:
        violations.append("no fluid machines measured")
    if normalized.get("violations"):
        violations.extend(normalized["violations"])
    if not normalized.get("ok") or violations:
        unique = list(dict.fromkeys(violations))
        raise ElectronicsExecutionError(
            "Live invariants failed: " + "; ".join(unique), report=normalized
        )
    return normalized


def _build_group(
    bridge: GameBridge,
    name: str,
    named_plans: Sequence[tuple[str, dict]],
    emit: Callable[[str], None],
) -> dict:
    combined = combine_named_plans(named_plans)
    authorization = build_layout_authorization(named_plans)
    report = _load_report(bridge.build_layout(authorization, combined))
    emit(
        f"{name}: attempted={report.get('attempted_placements', 0)}, "
        f"placed={report.get('succeeded_placements', 0)}, "
        f"existing={report.get('already_present_placements', 0)}, "
        f"failed={report.get('failed_placements', 0)}"
    )
    return validate_layout_report(report, name)


def _scaffold(
    bridge: GameBridge,
    scaffolding: Mapping,
    materials: Mapping[str, int],
    emit: Callable[[str], None],
) -> dict:
    report = _load_report(
        bridge.ensure_scaffolding(scaffolding_with_materials(scaffolding, materials))
    )
    if not report.get("ok"):
        raise ElectronicsExecutionError(
            f"Managed scaffolding failed: {report.get('error', 'unknown error')}", report=report
        )
    emit(
        "scaffolding: "
        f"created={report.get('created_entities', 0)}, "
        f"bots_inserted={report.get('bots_inserted', 0)}, "
        f"materials_inserted={sum(report.get('inserted', {}).values())}"
    )
    return report


def _mutated(*reports: Mapping) -> bool:
    for report in reports:
        if report.get("succeeded_placements", 0) or report.get("created_entities", 0):
            return True
        if report.get("bots_inserted", 0) or any(report.get("inserted", {}).values()):
            return True
        if report.get("seeded_ore_tiles", 0):
            return True
    return False


def _assert_idempotent(
    first: Mapping,
    replay_infrastructure: Mapping,
    replay_scaffolding: Mapping,
    replay_production: Mapping,
    second: Mapping,
) -> None:
    for label, report in (
        ("infrastructure", replay_infrastructure),
        ("production", replay_production),
    ):
        if report["succeeded_placements"] != 0:
            raise ElectronicsExecutionError(
                f"Idempotency replay created {report['succeeded_placements']} new {label} placements",
                report=dict(report),
            )
    if replay_scaffolding.get("created_entities", 0) or replay_scaffolding.get("bots_inserted", 0):
        raise ElectronicsExecutionError(
            "Idempotency replay created scaffolding entities or duplicate construction robots",
            report=dict(replay_scaffolding),
        )
    if first["structural_entities"] != second["structural_entities"]:
        raise ElectronicsExecutionError(
            "Idempotency replay changed the structural entity fingerprint", report=dict(second)
        )


def execute_electronics_bundle(
    bridge: GameBridge,
    bundle: Mapping,
    *,
    world: ElectronicsWorldSpec,
    existing_topology: str,
    settle_ticks: int,
    settle_timeout_seconds: float,
    emit: Callable[[str], None] = print,
) -> dict:
    """Seed ore, execute infrastructure first, build production, measure, replay, and save."""
    infrastructure = list(bundle["infrastructure"])
    production = list(bundle["plans"])
    materials = production_materials(production)
    reports: list[dict] = []
    try:
        prepare_existing_topology(bridge, existing_topology)
        ore_report = _seed_ore(bridge, world, emit)
        reports.append(ore_report)
        infrastructure_report = _build_group(bridge, "infrastructure", infrastructure, emit)
        reports.append(infrastructure_report)
        scaffold_report = _scaffold(bridge, bundle["scaffolding"], materials, emit)
        reports.append(scaffold_report)
        production_report = _build_group(bridge, "production", production, emit)
        reports.append(production_report)

        emit(f"settling for {settle_ticks} Factorio ticks")
        wait_for_game_ticks(bridge, settle_ticks, timeout_seconds=settle_timeout_seconds)
        first_live = validate_live_report(_load_report(bridge.verify_electronics_execution()))
        emit(
            "live: "
            f"power_sources={first_live['power_sources']}, "
            f"electric_networks={len(first_live['electric_samples']['network_ids'])}, "
            f"logistic_networks={len(first_live['logistic_roboports']['network_ids'])}, "
            f"ghosts={first_live['ghosts']['remaining']}"
        )

        replay_infrastructure = _build_group(
            bridge, "idempotency/infrastructure", infrastructure, emit
        )
        replay_scaffolding = _scaffold(bridge, bundle["scaffolding"], materials, emit)
        replay_production = _build_group(bridge, "idempotency/production", production, emit)
        second_live = validate_live_report(_load_report(bridge.verify_electronics_execution()))
        _assert_idempotent(
            first_live, replay_infrastructure, replay_scaffolding, replay_production, second_live
        )
        emit("idempotency: replay created zero entities, ghosts, or robots")
        return {
            "infrastructure": infrastructure_report,
            "scaffolding": scaffold_report,
            "production": production_report,
            "live": first_live,
            "idempotency": {
                "infrastructure": replay_infrastructure,
                "scaffolding": replay_scaffolding,
                "production": replay_production,
                "live": second_live,
            },
        }
    finally:
        if _mutated(*reports):
            bridge.save_game()