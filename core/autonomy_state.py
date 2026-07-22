# Path: core/autonomy_state.py
# Purpose: Persist and reconcile deterministic autonomy phase progress atomically across restarts.

from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Mapping
from jsonschema import Draft7Validator

_ROOT = Path(__file__).resolve().parents[1]
_ALLOWED = {"pending": {"running"}, "running": {"verified", "failed"}, "failed": {"running"}, "verified": set()}

def _validate(state: Mapping) -> None:
    schema = json.loads((_ROOT / "schemas/autonomy_state.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft7Validator(schema).iter_errors(dict(state)))
    if errors: raise ValueError("AutonomyState validation failed: " + "; ".join(error.message for error in errors))

def initial_state(program: Mapping, snapshot_tick: int) -> dict:
    state = {"version": "1.0.0", "program_hash": program["program_hash"], "snapshot_tick": int(snapshot_tick), "last_verified_tick": None, "phases": [{"id": p["id"], "phase_hash": p["phase_hash"], "status": "pending", "attempts": 0} for p in program["phases"]]}
    _validate(state); return state

def transition(state: Mapping, phase_id: str, status: str, *, tick: int) -> dict:
    _validate(state)
    result = json.loads(json.dumps(state)); phases = result["phases"]
    index = next((i for i, phase in enumerate(phases) if phase["id"] == phase_id), None)
    if index is None: raise ValueError(f"Unknown phase {phase_id}")
    phase = phases[index]
    if status not in _ALLOWED[phase["status"]]: raise ValueError(f"Invalid transition {phase['status']} -> {status}")
    if status == "running" and any(p["status"] != "verified" for p in phases[:index]): raise ValueError("Earlier phases must be verified")
    phase["status"] = status
    if status == "running": phase["attempts"] += 1
    if status == "verified": result["last_verified_tick"] = int(tick)
    result["snapshot_tick"] = int(tick); _validate(result); return result

def reconcile(program: Mapping, state: Mapping, observed_verified: set[str], *, snapshot_tick: int) -> dict:
    _validate(state)
    if state["program_hash"] != program["program_hash"]: raise ValueError("State belongs to a different immutable program")
    expected = [(phase["id"], phase["phase_hash"]) for phase in program["phases"]]
    actual = [(phase["id"], phase["phase_hash"]) for phase in state["phases"]]
    if actual != expected: raise ValueError("State phase identities differ from immutable program")
    ids = [phase[0] for phase in expected]
    if not observed_verified <= set(ids): raise ValueError("Observed unknown phase")
    indices = sorted(ids.index(value) for value in observed_verified)
    if indices and indices != list(range(indices[-1] + 1)): raise ValueError("Observed verified phases must form a prefix")
    result = json.loads(json.dumps(state))
    for phase in result["phases"]:
        if phase["id"] in observed_verified: phase["status"] = "verified"
        elif phase["status"] in {"running", "verified"}: phase["status"] = "pending"
    result["snapshot_tick"] = int(snapshot_tick)
    result["last_verified_tick"] = int(snapshot_tick) if observed_verified else None
    _validate(result); return result

def save_atomic(path: Path, state: Mapping) -> None:
    _validate(state); path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)

def load_state(path: Path) -> dict:
    state = json.loads(Path(path).read_text(encoding="utf-8")); _validate(state); return state
