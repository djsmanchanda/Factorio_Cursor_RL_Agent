# Path: tests/test_report_retention.py
# Purpose: Verify GameBridge bounds report-directory growth without ever discarding the report a command just collected.

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator.game_bridge import REPORT_RETENTION, GameBridge  # noqa: E402

SUBDIR = Path("factorio_mod") / "layout_reports"


def _bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs) -> GameBridge:
    """A GameBridge with its RCON socket stubbed out -- retention is pure filesystem work."""
    monkeypatch.setattr(
        "orchestrator.game_bridge.RconClient", lambda *_a, **_kw: object()
    )
    return GameBridge(script_output=tmp_path, **kwargs)


def test_bridge_keeps_optional_managed_episode_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _bridge(tmp_path, monkeypatch, episode_id="episode-test")

    assert bridge.episode_id == "episode-test"


def _write_reports(tmp_path: Path, count: int) -> list[Path]:
    """`count` reports with strictly increasing mtimes, oldest first."""
    directory = tmp_path / SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for index in range(count):
        item = directory / f"layout_{index:04d}.json"
        item.write_text("{}", encoding="utf-8")
        # Explicit mtimes: a fast loop can otherwise write several files inside
        # one filesystem timestamp tick, making "newest" ambiguous.
        os.utime(item, ns=(1_000_000_000 + index, 1_000_000_000 + index))
        written.append(item)
    return written


def test_prune_keeps_newest_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = _write_reports(tmp_path, 25)
    bridge = _bridge(tmp_path, monkeypatch, retain_reports=10)

    removed = bridge._prune_reports(SUBDIR, written[-1])

    survivors = sorted((tmp_path / SUBDIR).glob("*.json"))
    assert removed == 15
    assert survivors == sorted(written[-10:])


def test_prune_never_deletes_the_collected_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The oldest file is normally evicted first -- but not when it is the one
    the caller was just handed and is about to read."""
    written = _write_reports(tmp_path, 25)
    oldest = written[0]
    bridge = _bridge(tmp_path, monkeypatch, retain_reports=10)

    bridge._prune_reports(SUBDIR, oldest)

    assert oldest.exists()
    assert len(list((tmp_path / SUBDIR).glob("*.json"))) == 11


def test_prune_is_a_noop_below_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    written = _write_reports(tmp_path, 5)
    bridge = _bridge(tmp_path, monkeypatch, retain_reports=10)

    assert bridge._prune_reports(SUBDIR, written[-1]) == 0
    assert len(list((tmp_path / SUBDIR).glob("*.json"))) == 5


def test_prune_ignores_missing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _bridge(tmp_path, monkeypatch)
    assert bridge._prune_reports(SUBDIR, tmp_path / "absent.json") == 0


def test_prune_leaves_non_report_files_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the mod's own *.json reports are history; nothing else in the
    directory is ours to delete."""
    written = _write_reports(tmp_path, 25)
    keepsake = tmp_path / SUBDIR / "notes.txt"
    keepsake.write_text("hand-written", encoding="utf-8")
    bridge = _bridge(tmp_path, monkeypatch, retain_reports=10)

    bridge._prune_reports(SUBDIR, written[-1])

    assert keepsake.exists()


def test_collect_prunes_so_scan_cost_stays_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of retention: repeated collections must not grow the directory
    each command scans."""
    directory = tmp_path / SUBDIR
    _write_reports(tmp_path, 30)
    bridge = _bridge(tmp_path, monkeypatch, retain_reports=10)

    counter = {"tick": 0}

    def _fake_command(_text: str) -> str:
        counter["tick"] += 1
        fresh = directory / f"layout_9{counter['tick']:03d}.json"
        fresh.write_text("{}", encoding="utf-8")
        return ""

    monkeypatch.setattr(bridge, "command", _fake_command)

    for _ in range(8):
        collected = bridge._run_and_collect("/build_layout_plan {}", SUBDIR, timeout=5.0)
        assert collected.exists(), "the report just collected must survive pruning"
        assert len(list(directory.glob("*.json"))) <= 10


def test_retain_reports_must_keep_at_least_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="at least the file just collected"):
        _bridge(tmp_path, monkeypatch, retain_reports=0)


def test_default_window_is_generous_enough_for_post_mortems() -> None:
    assert REPORT_RETENTION >= 100
