# Path: tests/test_capacity_model.py
# Purpose: Deterministic tests for the capacity model: committed derivation, phasing delta, phase advance.

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.capacity_phasing_policy import evaluate_capacity_phasing
from core.phase_advance_evaluator import propose_phase_advance
from core.progress_state import build_progress_state, _derive_committed_capacity
from planners.city_planner.ghost_projection_phase import _derive_delta_capacity, generate_ghost_plan

BUILD_INTENT = {
    "intents": [
        {"kind": "block_placeholder", "block_type": "circuits", "count": 60},
        {"kind": "block_placeholder", "block_type": "smelting", "count": 40},
    ]
}


def progress(current: int, committed: int, active: int, ultimate: int = 100) -> dict:
    return {
        "ultimate_capacity": ultimate,
        "current_capacity": current,
        "committed_capacity": committed,
        "active_phase_capacity": active,
        "completed_phases": [],
        "rationale": "test state",
    }


def test_committed_is_current_plus_pending_clamped_to_ultimate():
    assert _derive_committed_capacity(10, 5, 100) == 15
    assert _derive_committed_capacity(90, 50, 100) == 100
    assert _derive_committed_capacity(10, -3, 100) == 10


def test_delta_fills_active_phase_from_fresh_state():
    state = progress(current=10, committed=10, active=50)
    phasing = evaluate_capacity_phasing(state, BUILD_INTENT).to_dict()
    assert phasing["previous_active_capacity"] == 50
    assert phasing["desired_active_capacity"] == 50
    delta = _derive_delta_capacity(BUILD_INTENT, state, phasing)
    assert delta == 40


def test_delta_zero_when_phase_fully_committed_by_ghosts():
    state = progress(current=10, committed=50, active=50)
    phasing = evaluate_capacity_phasing(state, BUILD_INTENT).to_dict()
    delta = _derive_delta_capacity(BUILD_INTENT, state, phasing)
    assert delta == 0


def test_delta_after_authorized_phase_advance():
    # Phase 50 built out; an authorized advance moved the active phase to 100.
    state = progress(current=50, committed=50, active=100)
    phasing = evaluate_capacity_phasing(state, BUILD_INTENT).to_dict()
    assert phasing["desired_active_capacity"] == 100
    delta = _derive_delta_capacity(BUILD_INTENT, state, phasing)
    assert delta == 50


def test_phase_advance_gate():
    incomplete = propose_phase_advance(progress(current=10, committed=50, active=50), {"status": "OK"})
    assert incomplete.status == "INELIGIBLE"
    assert incomplete.reason == "phase_incomplete"

    complete = propose_phase_advance(progress(current=50, committed=50, active=50), {"status": "OK"})
    assert complete.status == "ELIGIBLE"
    assert complete.next_phase == 100


def test_generate_ghost_plan_emits_fill_delta():
    state = progress(current=10, committed=10, active=50)
    phasing = evaluate_capacity_phasing(state, BUILD_INTENT).to_dict()
    plan = generate_ghost_plan(build_intent=BUILD_INTENT, progress_state=state, capacity_phasing=phasing)
    assert len(plan.ghosts) == 40
    prototypes = {ghost["prototype"] for ghost in plan.ghosts}
    assert prototypes <= {"assembling-machine-1", "electric-furnace"}


def test_build_progress_state_derives_from_snapshot_and_ghosts(tmp_path: Path):
    snapshot = {
        "tick": 123,
        "surface": "nauvis",
        "entities": [
            {"name": "assembling-machine-1", "type": "assembling-machine", "position": {"x": float(i), "y": 0.0}}
            for i in range(7)
        ]
        + [{"name": "iron-chest", "type": "container", "position": {"x": 0.0, "y": 5.0}}],
    }
    ghost_observation = {
        "tick": 123,
        "surface": "planner-sandbox",
        "ghosts": [
            {"prototype": "assembling-machine-1", "tags": {"block": "circuits", "phase": "capacity_phase", "capacity_slice": "40"}}
            for _ in range(5)
        ],
    }
    metrics = {"labs_count": 0}

    snapshot_path = tmp_path / "snapshot.json"
    metrics_path = tmp_path / "metrics.json"
    build_intent_path = tmp_path / "build_intent.json"
    observation_path = tmp_path / "ghost_observation.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
    build_intent_path.write_text(json.dumps(BUILD_INTENT), encoding="utf-8")
    observation_path.write_text(json.dumps(ghost_observation), encoding="utf-8")

    state = build_progress_state(
        snapshot_path=snapshot_path,
        metrics_path=metrics_path,
        build_intent_path=build_intent_path,
        ghost_observation_path=observation_path,
    ).to_dict()

    assert state["ultimate_capacity"] == 100
    assert state["current_capacity"] == 7
    assert state["committed_capacity"] == 12
    assert state["active_phase_capacity"] == 50


def test_execution_readiness_allows_projection_for_fill_delta():
    from core.execution_readiness import propose_execution

    fresh = progress(current=10, committed=10, active=50)
    phasing = evaluate_capacity_phasing(fresh, BUILD_INTENT).to_dict()
    proposal = propose_execution(fresh, phasing, BUILD_INTENT, "OK").to_dict()
    assert "project_more_ghosts" in proposal["allowed_actions"]
    assert proposal["next_recommended_step"] == "project_more_ghosts"

    held = progress(current=10, committed=50, active=50)
    held_phasing = evaluate_capacity_phasing(held, BUILD_INTENT).to_dict()
    held_proposal = propose_execution(held, held_phasing, BUILD_INTENT, "OK").to_dict()
    assert "project_more_ghosts" not in held_proposal["allowed_actions"]
    assert held_proposal["next_recommended_step"] == "hold_position"


def test_zone_stride_exceeds_placeholder_footprint():
    from core.sandbox_zoning import derive_sandbox_zones
    from core.block_prototypes import PROTOTYPE_FOOTPRINTS, placeholder_prototype

    zones = derive_sandbox_zones(["circuits", "smelting", "science"])
    for block_id, zone in zones.items():
        footprint = PROTOTYPE_FOOTPRINTS[placeholder_prototype(block_id)]
        assert zone.stride_x > footprint, f"{block_id} ghosts would overlap"
        assert zone.stride_y > footprint, f"{block_id} ghosts would overlap"


def test_ghost_plan_skips_satisfied_blocks_and_continues_zone_indices():
    # Phase 50 already built as circuits; the next batch must not re-project
    # circuits cells 0..49, and new circuit ghosts continue at cell 50.
    state = progress(current=50, committed=50, active=100)
    phasing = evaluate_capacity_phasing(state, BUILD_INTENT).to_dict()
    plan = generate_ghost_plan(
        build_intent=BUILD_INTENT,
        progress_state=state,
        capacity_phasing=phasing,
        existing_by_block={"circuits": 50},
    )
    assert len(plan.ghosts) == 50
    by_block: dict = {}
    for ghost in plan.ghosts:
        block = ghost["tags"]["block"]
        by_block.setdefault(block, []).append(int(ghost["tags"]["zone_index"]))
    assert sorted(by_block["circuits"]) == list(range(50, 60))
    assert sorted(by_block["smelting"]) == list(range(0, 40))


def test_line_layout_is_deterministic_and_collision_free():
    from planners.local_layout_planner import LocalLayoutPlanner

    planner = LocalLayoutPlanner()
    plan = planner.generate_line_layout("iron-gear-wheel", 8, 0, 40)
    plan2 = planner.generate_line_layout("iron-gear-wheel", 8, 0, 40)
    assert plan == plan2

    ghosts = [a for p in plan["phases"] for a in p["actions"] if a["action_type"] == "place_ghost"]
    machines = [g for g in ghosts if g["entity"] == "assembling-machine-2"]
    assert len(machines) == 8
    assert all(g["recipe"] == "iron-gear-wheel" for g in machines)

    # 1x1 entities must not share a tile; 3x3 machines must not overlap anything.
    small = [(g["position"]["x"], g["position"]["y"]) for g in ghosts if g["entity"] != "assembling-machine-2"]
    assert len(small) == len(set(small))
    machine_tiles = set()
    for m in machines:
        mx, my = m["position"]["x"], m["position"]["y"]
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                machine_tiles.add((mx + dx, my + dy))
    assert not machine_tiles & set(small)

    materials = planner.material_requirements(plan)
    assert materials["assembling-machine-2"] == 8
    assert materials["fast-inserter"] == 16
    # Gear demand is 24 plates/s; fast feeders move ~4/s, so the input belt
    # extends west to host 8 feed points (25% headroom): 33 input + 24 output.
    assert materials["transport-belt"] == 57


def test_feeder_count_scales_with_ingredient_demand():
    from planners.local_layout_planner import LocalLayoutPlanner

    planner = LocalLayoutPlanner()
    plan = planner.generate_line_layout(
        "electronic-circuit", 6, 0, 0, belt_type="express-transport-belt", inserter_type="stack-inserter"
    )
    actions = [a for p in plan["phases"] for a in p["actions"]]
    cable_chests = [a for a in actions if a.get("infinity_filter") == "copper-cable"]
    plate_chests = [a for a in actions if a.get("infinity_filter") == "iron-plate"]
    # 6 machines x 1.5 crafts/s x 3 cables = 27/s -> 3 stack feeders; plates 9/s -> 1.
    assert len(cable_chests) == 3
    assert len(plate_chests) == 1


def test_collectors_scale_with_output_demand():
    from planners.local_layout_planner import LocalLayoutPlanner

    planner = LocalLayoutPlanner()
    # 12 gear machines = 18 gears/s; stack collectors drain ~12/s -> 2 collectors.
    plan = planner.generate_line_layout(
        "iron-gear-wheel", 12, 0, 0, belt_type="express-transport-belt", inserter_type="stack-inserter"
    )
    actions = [a for p in plan["phases"] for a in p["actions"]]
    chests = [a for a in actions if a["entity"] == "steel-chest"]
    assert len(chests) == 2


def test_mining_fed_smelting_line_layout():
    from planners.local_layout_planner import LocalLayoutPlanner

    planner = LocalLayoutPlanner()
    plan = planner.generate_line_layout("iron-plate", 6, 96, 40, mining_feed=True)
    actions = [a for p in plan["phases"] for a in p["actions"]]

    drills = [a for a in actions if a["entity"] == "electric-mining-drill"]
    assert len(drills) == 6
    assert all(d["direction"] == "south" for d in drills)
    # Drill drop tile (center + 2 south) must be the input belt row (y=40).
    assert all(d["position"]["y"] == 38.5 for d in drills)

    furnaces = [a for a in actions if a["entity"] == "electric-furnace"]
    assert len(furnaces) == 6
    assert all("recipe" not in f for f in furnaces)

    # Miner-fed lines have no infinity chest for the ore.
    assert not any(a["entity"] == "infinity-chest" for a in actions)


def test_desired_capacity_never_exceeds_ultimate():
    state = progress(current=10, committed=10, active=50, ultimate=40)
    with pytest.raises(ValueError):
        phasing = evaluate_capacity_phasing(state, BUILD_INTENT).to_dict()
        _derive_delta_capacity(BUILD_INTENT, state, phasing)
