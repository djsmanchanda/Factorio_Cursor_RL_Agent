# Path: tests/test_research_commands.py
# Purpose: Verify real-base research command payloads preserve force isolation without a live game.

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.game_bridge import GameBridge, RESEARCH_REPORT_SUBDIR

REPO_ROOT = Path(__file__).resolve().parents[1]


def _recording_bridge() -> tuple[GameBridge, list[tuple[str, Path, float]]]:
    bridge = GameBridge.__new__(GameBridge)
    calls: list[tuple[str, Path, float]] = []

    def collect(command: str, subdir: Path, timeout: float) -> Path:
        calls.append((command, subdir, timeout))
        return Path("report.json")

    bridge._run_and_collect = collect  # type: ignore[method-assign]
    return bridge, calls


def test_set_research_sends_explicit_existing_force() -> None:
    bridge, calls = _recording_bridge()

    bridge.set_research("automation", force="player")

    assert calls == [
        ('/set_research {"technology":"automation","force":"player"}', RESEARCH_REPORT_SUBDIR, 60.0)
    ]


def test_research_status_sends_force_and_requested_technology() -> None:
    bridge, calls = _recording_bridge()

    bridge.research_status(force="player", technology="logistics")

    assert calls == [
        ('/research_status {"force":"player","technology":"logistics"}', RESEARCH_REPORT_SUBDIR, 60.0)
    ]


def test_research_status_preserves_legacy_planner_command_when_unscoped() -> None:
    bridge, calls = _recording_bridge()

    bridge.research_status()

    assert calls == [("/research_status", RESEARCH_REPORT_SUBDIR, 60.0)]


def test_lua_research_commands_resolve_supplied_force_without_creating_it() -> None:
    source = (REPO_ROOT / "factorio_mod" / "research.lua").read_text(encoding="utf-8")

    assert "get_or_create_planner_force(force_name)" in source
    assert "set_research(payload.technology, payload.force)" in source
    assert "build_research_status(payload.force, payload.technology)" in source
    assert "force = force.name" in source
    assert "technology = technology" in source

def test_increase_command_fails_before_opening_a_game_connection() -> None:
    from tools.autonomous_run import main

    with pytest.raises(SystemExit) as error:
        main(["increase", "automation-science-pack", "1"])

    assert error.value.code == 2