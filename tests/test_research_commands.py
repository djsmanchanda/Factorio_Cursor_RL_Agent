# Path: tests/test_research_commands.py
# Purpose: Verify real-base research command payloads preserve force isolation without a live game.

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.game_bridge import (
    BridgeError,
    GameBridge,
    LOGISTIC_INVENTORY_REPORT_SUBDIR,
    RECIPE_CATALOG_SUBDIR,
    RESEARCH_REPORT_SUBDIR,
)



def _recording_bridge() -> tuple[GameBridge, list[tuple[str, Path, float]]]:
    bridge = GameBridge.__new__(GameBridge)
    calls: list[tuple[str, Path, float]] = []
    bridge.expected_names = []

    def collect(
        command: str, subdir: Path, timeout: float, *,
        expected_name: str | None = None,
    ) -> Path:
        calls.append((command, subdir, timeout))
        bridge.expected_names.append(expected_name)
        return Path("report.json")

    bridge._run_and_collect = collect  # type: ignore[method-assign]
    return bridge, calls


def test_set_research_sends_explicit_existing_force() -> None:
    bridge, calls = _recording_bridge()

    bridge.set_research("automation", force="player")

    assert calls == [
        ('/set_research {"technology":"automation","force":"player"}', RESEARCH_REPORT_SUBDIR, 60.0)
    ]


def _writing_bridge(
    tmp_path: Path, payload: dict, *, write_name: str | None = None,
) -> tuple[GameBridge, list[tuple[str, Path, float]]]:
    bridge = GameBridge.__new__(GameBridge)
    calls: list[tuple[str, Path, float]] = []
    bridge.expected_names = []

    def collect(
        command: str, subdir: Path, timeout: float, *,
        expected_name: str | None = None,
    ) -> Path:
        calls.append((command, subdir, timeout))
        bridge.expected_names.append(expected_name)
        path = tmp_path / (write_name or expected_name or "report.json")
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    bridge._run_and_collect = collect  # type: ignore[method-assign]
    return bridge, calls


def test_set_research_guarded_call_correlates_request_id_and_exact_response(
    tmp_path, monkeypatch,
) -> None:
    import orchestrator.game_bridge as game_bridge

    monkeypatch.setattr(
        game_bridge.uuid, "uuid4",
        lambda: SimpleNamespace(hex="request42"),
    )
    bridge, calls = _writing_bridge(tmp_path, {
        "request_id": "request42", "ok": True, "force": "player",
        "technology": "automation", "canonical_name": "automation", "tick": 101,
    })

    path = bridge.set_research(
        "automation", force="player", expected_current=None,
        expected_queue=["logistics"], replace_queue=True,
    )

    assert calls == [
        (
            '/set_research {"technology":"automation","force":"player",'
            '"expected_queue":["logistics"],"expected_current":null,"replace_queue":true,'
            '"request_id":"request42"}',
            RESEARCH_REPORT_SUBDIR,
            60.0,
        )
    ]
    assert bridge.expected_names == ["research_request42.json"]
    assert path.name == "research_request42.json"


def test_set_research_guarded_call_rejects_wrong_request_id(tmp_path) -> None:
    bridge, _calls = _writing_bridge(tmp_path, {
        "request_id": "other", "ok": True, "force": "player",
        "technology": "automation", "canonical_name": "automation", "tick": 101,
    })

    with pytest.raises(BridgeError, match="does not match request_id"):
        bridge.set_research(
            "automation", force="player", expected_current=None,
            expected_queue=[], replace_queue=False,
        )


def test_set_research_guarded_call_rejects_failed_guard_response(
    tmp_path, monkeypatch,
) -> None:
    import orchestrator.game_bridge as game_bridge

    monkeypatch.setattr(
        game_bridge.uuid, "uuid4",
        lambda: SimpleNamespace(hex="request42"),
    )
    bridge, _calls = _writing_bridge(tmp_path, {
        "request_id": "request42", "ok": False,
        "error": "Research queue changed since observation: expected 0 entries, found 1",
    })

    with pytest.raises(BridgeError, match="queue changed since observation"):
        bridge.set_research(
            "automation", force="player", expected_current=None,
            expected_queue=[], replace_queue=False,
        )


def test_wait_for_new_file_selects_exact_response_over_unrelated_same_tick_file(
    tmp_path,
) -> None:
    subdir = Path("factorio_mod") / "research_reports"
    directory = tmp_path / subdir
    directory.mkdir(parents=True)
    (directory / "research_100.json").write_text('{"ok":true}', encoding="utf-8")
    unrelated = directory / "research_request99.json"
    unrelated.write_text('{"ok":true}', encoding="utf-8")
    expected = directory / "research_request42.json"
    expected.write_text('{"ok":true}', encoding="utf-8")
    unrelated.touch()
    expected.touch()
    bridge = GameBridge.__new__(GameBridge)
    bridge.script_output = tmp_path
    bridge._poll_interval = 0

    collected = bridge._wait_for_new_file(
        subdir, {}, 1.0, expected_name="research_request42.json",
    )

    assert collected == expected


def test_set_research_guard_requires_expected_queue() -> None:
    bridge, _calls = _recording_bridge()

    with pytest.raises(ValueError, match="require expected_queue"):
        bridge.set_research("automation", force="player", expected_current="logistics")


def test_research_status_correlates_force_and_technology_report(
    monkeypatch,
) -> None:
    import orchestrator.game_bridge as game_bridge

    monkeypatch.setattr(
        game_bridge.uuid, "uuid4",
        lambda: SimpleNamespace(hex="request42"),
    )
    bridge, calls = _recording_bridge()

    bridge.research_status(force="player", technology="logistics")

    assert calls == [
        (
            '/research_status {"force":"player","technology":"logistics",'
            '"request_id":"request42"}',
            RESEARCH_REPORT_SUBDIR,
            60.0,
        )
    ]
    assert bridge.expected_names == ["research_status_request42.json"]


def test_research_status_preserves_legacy_planner_command_when_unscoped() -> None:
    bridge, calls = _recording_bridge()

    bridge.research_status()

    assert calls == [("/research_status", RESEARCH_REPORT_SUBDIR, 60.0)]
    assert bridge.expected_names == [None]


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


def _finite_research_report(*, current: str | None = None, tick: int = 100) -> dict:
    return {
        "ok": True,
        "force": "player",
        "tick": tick,
        "current_research": current,
        "research_progress": 0,
        "research_queue": [],
        "technology": {
            "name": "automation",
            "canonical_name": "automation",
            "requested_name": "automation",
            "target_completed": False,
            "researched": False,
            "enabled": True,
            "state": "available",
            "science_packs": {"automation-science-pack": 1},
        },
    }


class _GuardedResearchBridge:
    def __init__(self, reports: list[dict], submission: dict) -> None:
        self.reports = list(reports)
        self.submission = submission
        self.status_calls: list[tuple[str, str]] = []
        self.set_calls: list[tuple[str, dict]] = []

    def research_status(self, *, force: str, technology: str) -> dict:
        self.status_calls.append((force, technology))
        return self.reports.pop(0)

    def set_research(self, technology: str, **kwargs) -> dict:
        self.set_calls.append((technology, kwargs))
        return self.submission


def test_select_research_passes_observation_guard_to_setter(monkeypatch) -> None:
    import orchestrator.research_operations as module

    monkeypatch.setattr(module, "load_json", lambda value: value)
    bridge = _GuardedResearchBridge(
        [_finite_research_report(), _finite_research_report(current="automation", tick=101)],
        {"ok": True, "force": "player", "technology": "automation",
         "canonical_name": "automation", "tick": 101},
    )

    outcome = module.select_research(
        bridge, force="player", technology="automation",
        expected_current=None, expected_queue=[],
    )

    assert outcome["verified"] and outcome["submitted"]
    assert bridge.set_calls == [
        ("automation", {"force": "player", "expected_current": None,
                        "expected_queue": [], "replace_queue": False})
    ]


class _RaisingResearchBridge(_GuardedResearchBridge):
    def set_research(self, technology: str, **kwargs) -> dict:
        self.set_calls.append((technology, kwargs))
        raise BridgeError(
            "Research command failed for request 'request42': "
            "Research queue changed since observation: expected 0 entries, found 1"
        )


def test_select_research_maps_atomic_guard_mismatch_raised_by_bridge(monkeypatch) -> None:
    import orchestrator.research_operations as module
    from orchestrator.work_state import WorkStateSignal

    monkeypatch.setattr(module, "load_json", lambda value: value)
    bridge = _RaisingResearchBridge([_finite_research_report()], {})

    with pytest.raises(WorkStateSignal) as error:
        module.select_research(
            bridge, force="player", technology="automation",
            expected_current=None, expected_queue=[],
        )

    assert error.value.code == "research_queue_changed"
    assert bridge.status_calls == [("player", "automation")]


def test_select_research_fails_closed_when_setter_guard_sees_queue_change(monkeypatch) -> None:
    import orchestrator.research_operations as module
    from orchestrator.work_state import WorkStateSignal

    monkeypatch.setattr(module, "load_json", lambda value: value)
    bridge = _GuardedResearchBridge(
        [_finite_research_report()],
        {"ok": False, "error": "Research queue changed since observation: expected 0 entries, found 1"},
    )

    with pytest.raises(WorkStateSignal) as error:
        module.select_research(
            bridge, force="player", technology="automation",
            expected_current=None, expected_queue=[],
        )

    assert error.value.code == "research_queue_changed"
    assert bridge.status_calls == [("player", "automation")]
    assert bridge.set_calls == [
        ("automation", {"force": "player", "expected_current": None,
                        "expected_queue": [], "replace_queue": False})
    ]


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
