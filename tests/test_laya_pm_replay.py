# Path: tests/test_laya_pm_replay.py | Purpose: Protect the C4-to-PM replay driver's gates and evidence.

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import tools.laya_pm_replay as replay


class FakeSession:
    def __init__(self, outcomes, cells_at):
        self.outcomes = list(outcomes)
        self.cells_at = cells_at
        self.calls = []

    def run(self, operation, **params):
        self.calls.append((operation, params))
        return self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]

    def economics(self):
        index = len(self.calls)
        cells = [{"recipe": "productivity-module", "machines": 1}] if index >= self.cells_at else []
        return {"cells": cells}


def short(code="material_shortage", stage="conversion_productivity-module"):
    return {"ok": False, "code": code,
            "details": {"stage": stage, "required": {"assembling-machine-2": 7, "steel-chest": 12},
                        "available": {"assembling-machine-2": 6, "steel-chest": 11}}}


def test_plan_rejects_a_port_range_above_the_tcp_limit(tmp_path):
    with patch.object(replay, "preflight", return_value={"kind": "x", "capture_tick": 1}):
        with pytest.raises(replay.ReplayError, match="outside 1..65535"):
            replay.plan_replay(tmp_path / "bank", "gen", tmp_path / "root",
                               game_port=64000, rcon_port=65600, deadline=2 ** 40)


def test_plan_rejects_an_existing_root(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    with patch.object(replay, "preflight", return_value={"kind": "x", "capture_tick": 1}):
        with pytest.raises(replay.ReplayError, match="fresh"):
            replay.plan_replay(tmp_path / "bank", "gen", root,
                               game_port=64000, rcon_port=65000, deadline=2 ** 40)


def test_drive_journals_the_bill_and_stock_delta(tmp_path):
    session = FakeSession([short()], cells_at=99)
    summary = replay.drive(session, tmp_path / "evidence.jsonl", max_steps=3,
                           stock_reader=lambda s: {"steel-chest": 11, "assembling-machine-2": 6},
                           consumption_probe=None)
    assert summary["pm_placed"] is False and summary["pm_evidence_complete"] is False
    first = summary["steps"][0]
    assert first["bill"]["short"] == {"assembling-machine-2": 1, "steel-chest": 1}
    assert first["outcome"] == "material_shortage"
    assert len(summary["steps"]) == 3
    assert (tmp_path / "evidence.jsonl").read_text().count("pm_replay_step") == 3


def test_drive_succeeds_only_with_placement_and_pm_consumption(tmp_path):
    session = FakeSession([{"ok": True, "code": None}], cells_at=3)
    summary = replay.drive(session, tmp_path / "evidence.jsonl", max_steps=5,
                           stock_reader=lambda s: {},
                           consumption_probe=lambda: {"pm_consumption_verified": True,
                                                      "samples": [{"tick": 10, "crafts": 2}]})
    assert summary["pm_placed"] is True
    assert summary["pm_consumption_verified"] is True
    assert summary["pm_evidence_complete"] is True
    assert summary["steps"][-1]["pm_consumption"][0]["crafts"] == 2


def test_a_generic_c6_pass_is_not_pm_consumption(tmp_path):
    session = FakeSession([{"ok": True, "code": None}], cells_at=1)
    summary = replay.drive(session, tmp_path / "evidence.jsonl", max_steps=4,
                           stock_reader=lambda s: {},
                           consumption_probe=lambda: {"pm_consumption_verified": False, "samples": []})
    assert summary["pm_placed"] is True
    assert summary["pm_consumption_verified"] is False
    assert summary["pm_evidence_complete"] is False
