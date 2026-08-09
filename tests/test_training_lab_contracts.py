# Path: tests/test_training_lab_contracts.py
# Purpose: Pin the separate training mod boundary and its versioned report contract.

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "factorio_training_lab"
SCHEMA = ROOT / "schemas" / "training_episode_report.schema.json"


def _validator() -> Draft7Validator:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


def test_training_mod_is_separate_and_depends_on_the_executor_mod() -> None:
    info = json.loads((LAB / "info.json").read_text(encoding="utf-8"))

    assert info["name"] == "factorio_training_lab"
    assert "factorio_cursor_rl_agent >= 0.2.0" in info["dependencies"]
    assert not (ROOT / "factorio_mod" / "episode_world.lua").exists()


def test_control_registers_only_training_namespaced_commands() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in LAB.glob("*.lua"))

    assert 'commands.add_command("training_provision"' in source
    assert 'commands.add_command("training_observe"' in source
    assert 'commands.add_command("training_recycle"' in source
    assert "training commands are RCON-only" in source
    assert 'game.surfaces["nauvis"]' not in source
    assert 'game.forces["player"]' not in source


def test_observation_report_is_schema_valid() -> None:
    report = {
        "version": "1.0.0", "kind": "observation",
        "request_id": "request-1", "episode_id": "episode-1",
        "tick": 1800, "ok": True, "status": "running",
        "scenario_id": "mining-delivery-00000001",
        "surface": "training/mining-delivery-00000001",
        "force": "training-mining-delivery-00000001",
        "started_tick": 0, "elapsed_ticks": 1800,
        "objective": {
            "item": "iron-ore", "target_rate_per_tick": (2 / 60),
            "sustain_ticks": 1800,
        },
        "metrics": {
            "delivered_items": 60, "sample_ticks": 60, "sample_items": 2,
            "rate_per_tick": (2 / 60), "sustained_ticks": 1200,
            "resource_remaining": 1_000_000,
            "built_entities": {"transport-belt": 42},
            "forbidden_entities": 0, "out_of_bounds_entities": 0,
            "budget_overruns": 0, "fixtures_valid": True,
        },
        "failure": {"kind": "none", "reason": ""},
    }

    assert list(_validator().iter_errors(report)) == []


def test_failed_report_requires_an_error() -> None:
    report = {
        "version": "1.0.0", "kind": "provision", "request_id": "request-1",
        "episode_id": "episode-1", "tick": 0, "ok": False, "status": "failed",
    }

    errors = list(_validator().iter_errors(report))
    assert any("error" in error.message for error in errors)


def test_report_paths_are_tick_and_sequence_qualified() -> None:
    source = (LAB / "training_shared.lua").read_text(encoding="utf-8")

    assert "report_sequence = state.report_sequence + 1" in source
    assert 'string.format("%010d_%06d", game.tick, state.report_sequence)' in source


def test_force_recycling_waits_for_the_merge_event() -> None:
    world = (LAB / "episode_world.lua").read_text(encoding="utf-8")
    control = (LAB / "control.lua").read_text(encoding="utf-8")

    assert "pending_force_merges" in world
    assert "game.merge_forces(force, game.forces.neutral)" in world
    assert "defines.events.on_forces_merged" in control
    assert "complete_force_merge(event)" in control


def test_audit_scans_the_entire_training_surface() -> None:
    source = (LAB / "episode_measurement.lua").read_text(encoding="utf-8")

    assert "surface.find_entities()" in source
    assert "entity.force ~= force" in source
    assert "neutral_resource" in source