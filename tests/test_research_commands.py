# Path: tests/test_research_commands.py
# Purpose: Verify real-base research command payloads preserve force isolation without a live game.

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.game_bridge import (
    GameBridge,
    LOGISTIC_INVENTORY_REPORT_SUBDIR,
    RECIPE_CATALOG_SUBDIR,
    RESEARCH_REPORT_SUBDIR,
)



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


def test_research_options_sends_existing_force() -> None:
    bridge, calls = _recording_bridge()

    bridge.research_options(force="player")

    assert calls == [
        ('/research_options {"force":"player"}', RESEARCH_REPORT_SUBDIR, 60.0)
    ]


def test_logistic_inventory_sends_explicit_real_base_scope() -> None:
    bridge, calls = _recording_bridge()

    bridge.logistic_inventory("nauvis", "player")

    assert calls == [
        (
            '/logistic_inventory {"surface":"nauvis","force":"player"}',
            LOGISTIC_INVENTORY_REPORT_SUBDIR,
            60.0,
        )
    ]


def test_increase_command_fails_before_opening_a_game_connection() -> None:
    from tools.autonomous_run import main

    with pytest.raises(SystemExit) as error:
        main(["increase", "automation-science-pack", "1"])

    assert error.value.code == 2

def test_recipe_catalog_sends_existing_player_force() -> None:
    bridge, calls = _recording_bridge()

    bridge.export_recipe_catalog(force="player")

    assert calls == [
        ("/export_recipe_catalog player", RECIPE_CATALOG_SUBDIR, 120.0)
    ]


class _FakeResearchBridge:
    def __init__(self, reports: list[dict], queued: dict | None = None) -> None:
        self.reports = list(reports)
        self.queued = queued or {"ok": True}
        self.status_calls: list[tuple[str, str]] = []
        self.set_calls: list[tuple[str, str]] = []
        self.closed = False

    def research_status(self, *, force: str, technology: str) -> dict:
        self.status_calls.append((force, technology))
        return self.reports.pop(0)

    def set_research(self, technology: str, *, force: str) -> dict:
        self.set_calls.append((force, technology))
        return self.queued

    def close(self) -> None:
        self.closed = True


def _research_args(technology: str):
    from argparse import Namespace

    return Namespace(
        technology=technology,
        surface="nauvis",
        force="player",
        script_output=Path("script-output"),
        rcon_host="127.0.0.1",
        rcon_port=27017,
        rcon_password="test",
    )


def _install_fake_research_bridge(monkeypatch, bridge: _FakeResearchBridge, produced: list[str]) -> None:
    import tools.autonomous_run as autonomous_run

    monkeypatch.setattr(autonomous_run, "GameBridge", lambda **_kwargs: bridge)
    monkeypatch.setattr(autonomous_run, "load_json", lambda report: report)
    monkeypatch.setattr(
        autonomous_run, "_run_item",
        lambda _args, item, _emit: produced.append(item),
    )


def _ignore_emit(_message: str) -> None:
    pass

def test_repeatable_current_level_uses_alias_for_status_and_set(monkeypatch) -> None:
    from tools.autonomous_run import _research

    technology = {
        "name": "mining-productivity-3",
        "canonical_name": "mining-productivity-3",
        "requested_name": "mining-productivity-4",
        "requested_level": 4,
        "current_level": 4,
        "target_completed": False,
        "state": "current",
        "researched": False,
        "enabled": True,
        "science_packs": {"logistic-science-pack": 1, "automation-science-pack": 1},
    }
    bridge = _FakeResearchBridge(
        [{"ok": True, "technology": technology}, {"ok": True, "current_research": "mining-productivity-3"}]
    )
    produced: list[str] = []
    _install_fake_research_bridge(monkeypatch, bridge, produced)

    assert _research(_research_args("mining-productivity-4"), _ignore_emit) == 0

    assert produced == ["automation-science-pack", "logistic-science-pack"]
    assert bridge.status_calls == [
        ("player", "mining-productivity-4"),
        ("player", "mining-productivity-4"),
    ]
    assert bridge.set_calls == [("player", "mining-productivity-4")]


def test_repeatable_prior_level_is_goal_met_without_production_or_set(monkeypatch) -> None:
    from tools.autonomous_run import _research

    bridge = _FakeResearchBridge(
        [{"ok": True, "technology": {
            "researched": False,
            "enabled": True,
            "science_packs": {"automation-science-pack": 1},
            "requested_level": 3,
            "current_level": 4,
            "target_completed": True,
            "state": "completed",
        }}]
    )
    produced: list[str] = []
    _install_fake_research_bridge(monkeypatch, bridge, produced)

    assert _research(_research_args("mining-productivity-3"), _ignore_emit) == 0
    assert produced == []
    assert bridge.set_calls == []


def test_repeatable_next_level_is_open_and_can_be_started(monkeypatch) -> None:
    from tools.autonomous_run import _research

    technology = {
        "name": "mining-productivity-3",
        "researched": True,
        "enabled": True,
        "science_packs": {"automation-science-pack": 1},
        "requested_level": 4,
        "current_level": 3,
        "target_completed": False,
        "state": "available",
    }
    bridge = _FakeResearchBridge(
        [{"ok": True, "technology": technology}, {"ok": True, "current_research": "mining-productivity-3"}]
    )
    produced: list[str] = []
    _install_fake_research_bridge(monkeypatch, bridge, produced)

    assert _research(_research_args("mining-productivity-4"), _ignore_emit) == 0
    assert produced == ["automation-science-pack"]
    assert bridge.set_calls == [("player", "mining-productivity-4")]


def test_repeatable_future_level_fails_before_production_or_set(monkeypatch) -> None:
    from orchestrator.autonomous_builder import StuckError
    from tools.autonomous_run import _research

    bridge = _FakeResearchBridge(
        [{"ok": True, "technology": {
            "researched": False,
            "enabled": True,
            "science_packs": {"automation-science-pack": 1},
            "requested_level": 5,
            "current_level": 4,
            "target_completed": False,
            "state": "future",
        }}]
    )
    produced: list[str] = []
    _install_fake_research_bridge(monkeypatch, bridge, produced)

    with pytest.raises(StuckError, match="future repeatable level"):
        _research(_research_args("mining-productivity-5"), _ignore_emit)

    assert produced == []
    assert bridge.set_calls == []


def test_finite_researched_status_remains_backward_compatible(monkeypatch) -> None:
    from tools.autonomous_run import _research

    bridge = _FakeResearchBridge(
        [{"ok": True, "technology": {
            "name": "automation",
            "researched": True,
            "enabled": True,
            "science_packs": {},
        }}]
    )
    produced: list[str] = []
    _install_fake_research_bridge(monkeypatch, bridge, produced)

    assert _research(_research_args("automation"), _ignore_emit) == 0
    assert produced == []
    assert bridge.set_calls == []


def test_set_research_failure_is_reported_after_pack_preparation(monkeypatch) -> None:
    from orchestrator.autonomous_builder import StuckError
    from tools.autonomous_run import _research

    bridge = _FakeResearchBridge(
        [{"ok": True, "technology": {
            "researched": False,
            "enabled": True,
            "science_packs": {"automation-science-pack": 1},
            "target_completed": False,
            "state": "available",
        }}],
        queued={"ok": False, "error": "add_research rejected target"},
    )
    produced: list[str] = []
    _install_fake_research_bridge(monkeypatch, bridge, produced)

    with pytest.raises(StuckError, match="add_research rejected target"):
        _research(_research_args("automation"), _ignore_emit)

    assert produced == ["automation-science-pack"]
    assert bridge.set_calls == [("player", "automation")]


def test_research_queue_processes_pending_items_in_order_and_skips_completed(tmp_path, monkeypatch) -> None:
    from argparse import Namespace
    from orchestrator.research_queue import load_queue, new_queue, update_item, write_queue
    from tools.autonomous_run import _research_queue

    queue_file = tmp_path / "research-queue.json"
    write_queue(queue_file, new_queue(["automation", "logistics", "chemical-science-pack"]))
    update_item(queue_file, "automation", "completed")
    calls: list[str] = []

    def fake_research(args, _emit):
        calls.append(args.technology)
        return 0

    import tools.autonomous_run as autonomous_run
    monkeypatch.setattr(autonomous_run, "_research", fake_research)
    args = Namespace(
        queue_file=queue_file, surface="nauvis", force="player", technology="unused",
    )

    assert _research_queue(args, _ignore_emit) == 0
    assert calls == ["logistics", "chemical-science-pack"]
    payload = load_queue(queue_file)
    assert all(item["status"] == "completed" for item in payload["items"])
