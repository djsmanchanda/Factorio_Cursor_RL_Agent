# Path: tests/test_laya_observer.py
# Purpose: Verify the Laya page is served by the RL Observatory and status stays read-only.

from __future__ import annotations

from pathlib import Path

from tools.training_observer import _ASSET_MAP
from training.observation import build_training_snapshot


def test_laya_page_is_an_rl_observatory_asset() -> None:
    assets = Path(__file__).resolve().parents[1] / "tools"
    assert _ASSET_MAP["/laya"] == ("laya_observer.html", "text/html; charset=utf-8")
    assert _ASSET_MAP["/laya/"] == ("laya_observer.html", "text/html; charset=utf-8")
    assert _ASSET_MAP["/laya.js"] == ("laya_observer.js", "text/javascript; charset=utf-8")
    assert _ASSET_MAP["/laya.css"] == ("laya_observer.css", "text/css; charset=utf-8")
    assert "Laya Director" in (assets / "laya_observer.html").read_text(encoding="utf-8")
    assert "held_out" in (assets / "laya_observer.js").read_text(encoding="utf-8")


def test_snapshot_exposes_read_only_laya_status(tmp_path, monkeypatch) -> None:
    cases = tmp_path / "cases.jsonl"
    cases.write_text('{"split":"train"}\n{"split":"held_out"}\n', encoding="utf-8")
    monkeypatch.setenv("LAYA_CASES_PATH", str(cases))
    snapshot = build_training_snapshot(tmp_path / "missing.db", tmp_path / "live")
    assert snapshot["laya"]["case_total"] == 2
    assert snapshot["laya"]["case_counts"] == {"train": 1, "held_out": 1}
    assert snapshot["laya"]["preflight"]["status"] == "blocked"


def test_snapshot_does_not_call_an_empty_checkpoint_directory_trained(tmp_path, monkeypatch) -> None:
    cases = tmp_path / "cases.jsonl"
    cases.write_text('{"split":"train"}\n', encoding="utf-8")
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    monkeypatch.setenv("LAYA_CASES_PATH", str(cases))
    monkeypatch.setenv("LAYA_CHECKPOINT_DIR", str(checkpoint))
    snapshot = build_training_snapshot(tmp_path / "missing.db", tmp_path / "live")
    assert snapshot["laya"]["checkpoint_present"] is False
    assert snapshot["laya"]["training"]["status"] == "not_started"


def test_observer_documents_port_fallback_in_source() -> None:
    source = (Path(__file__).resolve().parents[1] / "tools/training_observer.py").read_text(encoding="utf-8")
    assert "requested_port" in source
    assert "EADDRINUSE" in source
