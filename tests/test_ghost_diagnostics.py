# Path: tests/test_ghost_diagnostics.py
# Purpose: Prove stalled construction ghosts report actionable live causes and material demand.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator import live_base  # noqa: E402


class _Client:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.commands: list[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        return self.reply


def test_ghost_blockages_decodes_missing_material() -> None:
    client = _Client("77.5|-18.5|transport-belt|missing_material:transport-belt:4:0")

    result = live_base.ghost_blockages(
        client, "nauvis", "player", ((70.0, -25.0), (80.0, -10.0)),
    )

    assert result == [{
        "position": (77.5, -18.5), "entity": "transport-belt",
        "reason": "missing_material:transport-belt:4:0",
        "item": "transport-belt", "required": 4, "available": 0,
    }]
    assert "entity-ghost" in client.commands[0]
    assert "area={{70.0,-25.0},{80.0,-10.0}}" in client.commands[0]


def test_diagnosis_promotes_missing_ghost_material_to_mall_demand(monkeypatch) -> None:
    monkeypatch.setattr(live_base, "nearest_roboport", lambda *_a: (0.0, 0.0))
    monkeypatch.setattr(live_base, "entity_status_name", lambda *_a: "working")
    monkeypatch.setattr(
        live_base, "ghost_blockages", lambda *_a: [{
            "position": (77.5, -18.5), "entity": "transport-belt",
            "reason": "missing_material:transport-belt:4:0",
            "item": "transport-belt", "required": 4, "available": 0,
        }],
    )

    issue = builder._diagnose_blockage(
        None, "nauvis", "player", (10.0, 10.0), (10.0, 12.0),
        [(11.0, 10.0)], area=((0.0, 0.0), (100.0, 100.0)),
    )

    assert issue == (
        "ghost transport-belt at (77.5, -18.5) needs 4 transport-belt, "
        "but its network has 0",
        "materials:transport-belt:4",
    )


def test_material_remedy_raises_shortage_when_base_lacks_item(monkeypatch) -> None:
    monkeypatch.setattr(live_base, "available_items", lambda *_a: {})

    with pytest.raises(builder.MaterialShortage) as raised:
        builder._apply_remedy(
            None, None, "nauvis", "player", "conversion_stone-brick",
            "materials:stone-brick:8", "missing bricks", (0.0, 0.0),
            (0.0, 0.0), [], [], None, lambda _message: None,
        )

    assert raised.value.required == {"stone-brick": 8}