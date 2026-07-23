# Path: tests/test_water_seeding.py
# Purpose: Verify live electronics execution derives and seeds deterministic
# Nauvis-style water lakes behind every surveyed offshore-pump site.

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from planners.electronics_world import load_electronics_world_spec
from tools.electronics_execution import _mutated, _seed_water, water_seeding_payload

_FIXTURE = Path(__file__).parent / "fixtures" / "electronics_world_spec.json"


class FakeBridge:
    def __init__(self) -> None:
        self.seed_calls: list[dict] = []

    def seed_water_lakes(self, payload: dict, timeout: float = 120.0) -> dict:
        self.seed_calls.append(payload)
        tiles = sum(
            (lake["x2"] - lake["x1"] + 1) * (lake["y2"] - lake["y1"] + 1)
            for lake in payload["water_lakes"]
        )
        return {"tick": 0, "ok": True, "seeded_water_tiles": tiles}


def test_fixture_water_payload_matches_the_generated_world_lake() -> None:
    world = load_electronics_world_spec(_FIXTURE)

    assert water_seeding_payload(world) == {
        "water_lakes": [{
            "id": "offshore_pump_0",
            "tile": "water",
            "x1": 14,
            "y1": 84,
            "x2": 27,
            "y2": 96,
        }]
    }


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("north", (14, 84, 27, 96)),
        ("south", (14, 71, 27, 83)),
        ("east", (8, 77, 20, 90)),
        ("west", (21, 77, 33, 90)),
    ],
)
def test_water_payload_rotates_the_lake_behind_the_pump(
    direction: str, expected: tuple[int, int, int, int],
) -> None:
    world = SimpleNamespace(offshore_pump_sites=(
        {"position": (20.5, 83.5), "resource": "water", "direction": direction},
    ))

    lake = water_seeding_payload(world)["water_lakes"][0]

    assert (lake["x1"], lake["y1"], lake["x2"], lake["y2"]) == expected


def test_water_payload_rejects_unknown_direction() -> None:
    world = SimpleNamespace(offshore_pump_sites=(
        {"position": (20.5, 83.5), "resource": "water", "direction": "diagonal"},
    ))

    with pytest.raises(ValueError, match="Unknown offshore-pump direction"):
        water_seeding_payload(world)


def test_seed_water_uses_bridge_and_reports_mutation() -> None:
    world = load_electronics_world_spec(_FIXTURE)
    bridge = FakeBridge()
    messages: list[str] = []

    report = _seed_water(bridge, world, messages.append)

    assert bridge.seed_calls == [water_seeding_payload(world)]
    assert report["ok"] is True
    assert report["seeded_water_tiles"] == 182
    assert _mutated(report) is True
    assert any("water seeding" in message for message in messages)


def test_lua_water_command_is_idempotent_and_bounded() -> None:
    source = (Path(__file__).parents[1] / "factorio_mod" / "water_seeding.lua").read_text(
        encoding="utf-8"
    )

    assert 'existing ~= "water" and existing ~= "deepwater"' in source
    assert "surface.set_tiles(tiles, true)" in source
    assert "MAX_LAKE_TILES = 4096" in source
