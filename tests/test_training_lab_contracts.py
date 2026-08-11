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
    assert all("factorio_cursor_rl_agent" not in item for item in info["dependencies"])
    assert not (ROOT / "factorio_mod" / "episode_world.lua").exists()


def test_control_registers_only_training_namespaced_commands() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in LAB.glob("*.lua"))

    assert 'commands.add_command("training_provision"' in source
    assert 'commands.add_command("training_observe"' in source
    assert 'commands.add_command("training_recycle"' in source
    assert 'commands.add_command("training_focus"' in source
    assert 'commands.add_command("training_upload"' in source
    assert 'commands.add_command("training_execute"' in source
    assert 'commands.add_command("training_view"' in source
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
            "budget_overruns": 0, "fixtures_valid": True, "power_connected": True,
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


def test_training_control_registers_orphan_surface_watchdog() -> None:
    control = (LAB / "control.lua").read_text(encoding="utf-8")
    world = (LAB / "episode_world.lua").read_text(encoding="utf-8")

    assert "world.cleanup_orphan_surfaces(event.tick)" in control
    assert "ORPHAN_GRACE_TICKS = 3600" in world
    assert "game.delete_surface(surface)" in world
    assert "cleanup_orphan_forces" in world
    assert "training_cleanup_orphans" in world
    assert "RECYCLE_STALE_TRAINING_SURFACES" in world


def test_force_recycling_waits_for_the_merge_event() -> None:
    world = (LAB / "episode_world.lua").read_text(encoding="utf-8")
    control = (LAB / "control.lua").read_text(encoding="utf-8")

    assert "pending_force_merges" in world
    assert "game.merge_forces(force, game.forces.neutral)" in world
    assert "defines.events.on_forces_merged" in control
    assert "complete_force_merge(event)" in control


def test_joined_observers_are_given_training_surface_visibility() -> None:
    world = (LAB / "episode_world.lua").read_text(encoding="utf-8")
    control = (LAB / "control.lua").read_text(encoding="utf-8")

    assert "reveal_surface_to_connected_players" in world
    assert "reveal_active_episodes = reveal_active_episodes" in world
    assert "defines.events.on_player_joined_game" in control
    assert "world.reveal_active_episodes(player)" in control


def test_audit_scans_the_entire_training_surface() -> None:
    source = (LAB / "episode_measurement.lua").read_text(encoding="utf-8")

    assert "surface.find_entities()" in source
    assert "entity.force ~= force" in source
    assert "neutral_resource" in source


def test_training_surfaces_use_visible_tiles_and_evict_observers_before_recycling() -> None:
    world = (LAB / "episode_world.lua").read_text(encoding="utf-8")

    assert 'surface.generate_with_lab_tiles = false' in world
    assert 'local LAB_TILE_A = "lab-dark-1"' in world
    assert 'local LAB_TILE_B = "lab-dark-2"' in world
    assert "return (x + y) % 2 == 0 and LAB_TILE_A or LAB_TILE_B" in world
    assert "fill_visible_floor(surface, environment.bounds)" in world
    assert "evacuate_players(surface)" in world
    assert "reveal_surface_to_connected_players(" in world
    assert 'local OBSERVATORY_SURFACE = "training-observatory"' in world
    assert "player.set_controller({ type = defines.controllers.spectator })" in world


def test_fixture_audit_uses_surface_identity_not_global_unit_lookup() -> None:
    source = (LAB / "episode_measurement.lua").read_text(encoding="utf-8")

    assert "surface.find_entities_filtered" in source
    assert "game.get_entity_by_unit_number" not in source
    assert "prototypes.entity[name]" in (LAB / "training_geometry.lua").read_text(encoding="utf-8")

def test_execution_report_is_schema_valid() -> None:
    report = {
        "version": "1.0.0", "kind": "execution", "request_id": "request-1",
        "episode_id": "episode-1", "tick": 60, "ok": True, "status": "ready",
        "scenario_id": "mining-delivery-00000001",
        "surface": "training/mining-delivery-00000001",
        "force": "training-mining-delivery-00000001",
        "execution": {
            "attempted_placements": 2, "succeeded_placements": 2,
            "already_present_placements": 0, "failed_placements": 0,
            "placement_failures": [],
        },
    }

    assert list(_validator().iter_errors(report)) == []

def test_execution_report_accepts_factorio_empty_table_for_no_failures() -> None:
    report = {
        "version": "1.0.0", "kind": "execution", "request_id": "request-1",
        "episode_id": "episode-1", "tick": 60, "ok": True, "status": "ready",
        "scenario_id": "mining-delivery-00000001",
        "surface": "training/mining-delivery-00000001",
        "force": "training-mining-delivery-00000001",
        "execution": {
            "attempted_placements": 1, "succeeded_placements": 1,
            "already_present_placements": 0, "failed_placements": 0,
            "placement_failures": {},
        },
    }

    assert list(_validator().iter_errors(report)) == []

def test_training_force_completes_only_finite_primary_research() -> None:
    world = (LAB / "episode_world.lua").read_text(encoding="utf-8")

    assert "INFINITE_TECH_LEVEL = 4294967295" in world
    assert "technology.level = max_level" in world
    assert "force.reset_technology_effects()" in world


def test_power_disconnects_are_structured_training_evidence() -> None:
    source = (LAB / "episode_measurement.lua").read_text(encoding="utf-8")

    assert "local function power_state" in source
    assert 'episode.failure_kind = "power_unconnected"' in source
    assert "power_connected = powered" in source
    assert "POWER_STORAGE_TYPES" in source
    assert "power storage is disconnected from the power source" in source
    assert "POWER_CONSUMER_TYPES" in source
    assert "electricity consumer is disconnected from the power source" in source
    assert "RATE_WINDOW_TICKS = 600" in source
