# Path: tests/test_stage_chemical.py
# Purpose: Verify chemical construction stages keep landfill and pipe ghosts ordered.

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import live_base, stage_chemical
from orchestrator.stage_services import _ghost_materials

Point = tuple[float, float]


def test_landfill_is_separated_from_dependent_pipe_ghosts() -> None:
    link = {"phases": [{"name": "fluid_link_water", "actions": [
        {"action_type": "place_tile_ghost", "tile": "landfill", "position": {"x": 8, "y": 4}},
        {"action_type": "place_ghost", "entity": "pipe-to-ground", "position": {"x": 8.5, "y": 4.5}},
    ]}]}

    foundation, fluid = stage_chemical._separate_landfill_ghosts(link)

    assert foundation == {"phases": [{"name": "oil_landfill_foundation", "actions": [
        {"action_type": "place_tile_ghost", "tile": "landfill", "position": {"x": 8, "y": 4}},
    ]}]}
    assert fluid["phases"][0]["actions"] == [
        {"action_type": "place_ghost", "entity": "pipe-to-ground", "position": {"x": 8.5, "y": 4.5}},
    ]
    assert _ghost_materials(foundation) == {"landfill": 1}


def test_chemical_coverage_targets_uncovered_positions_not_box_corners(monkeypatch) -> None:
    chained: list[Point] = []
    ports: list[list[Point]] = [[]]
    monkeypatch.setattr(
        stage_chemical.live_base, "roboport_positions",
        lambda *_args: list(ports[0]),
    )

    def fake_extend(_client, _bridge, _surface, _force, target, _emit):
        chained.append(target)
        ports[0].append((target[0], target[1] - 10))
        return True

    monkeypatch.setattr(stage_chemical, "extend_roboport_coverage", fake_extend)
    plan = {"phases": [{"name": "route", "actions": [
        {"action_type": "place_tile_ghost", "tile": "landfill", "position": {"x": -5, "y": 3}},
        {"action_type": "place_ghost", "entity": "pipe", "position": {"x": 12.5, "y": 18.5}},
    ]}]}

    stage_chemical._ensure_plan_construction_coverage(
        type("Client", (), {"command": object()})(), object(), "nauvis", "player", plan, lambda _line: None,
    )

    # One chain covers both actions, so the second needs no port of its own --
    # and the two bounding-box corners (-5, 18.5) / (12.5, 3) are demanded by
    # nobody: they were how roboports got strung across empty map.
    assert chained == [(-5.0, 3)]


def test_occupied_tiles_can_leave_water_for_fluid_routing() -> None:
    class Client:
        command_text = ""

        def command(self, text: str) -> str:
            self.command_text = text
            return ""

    client = Client()
    live_base.occupied_tiles(client, "nauvis", (0, 0), (2, 2), include_water=False)

    assert "find_tiles_filtered" not in client.command_text
    assert "find_entities_filtered" in client.command_text
