# Path: tests/test_ore_seeding.py
# Purpose: Regression cover for M7's ore-seeding defect -- every surveyed mining
# drill must land on ore the execution flow actually seeds, using a FAKE bridge
# (no live game) so this stays a fast, deterministic unit test.

from __future__ import annotations

from pathlib import Path

import pytest

from planners.electronics_world import load_electronics_world_spec
from tests.support.fakes import RecordingSeedBridge
from tools.electronics_execution import (
    ElectronicsExecutionError,
    drill_footprints_covered,
    ore_seeding_payload,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "electronics_world_spec.json"


@pytest.fixture(scope="module")
def world():
    return load_electronics_world_spec(_FIXTURE)


def _patch_rect(patch: dict) -> tuple[float, float, float, float]:
    return patch["x1"], patch["y1"], patch["x2"], patch["y2"]


def _inside(position: tuple[float, float], rect: tuple[float, float, float, float]) -> bool:
    x, y = position
    x1, y1, x2, y2 = rect
    return x1 <= x <= x2 and y1 <= y <= y2


def test_payload_covers_every_ore_line_drill(world):
    """Every ore-line drill position must fall inside some patch rectangle
    the derived seeding payload will seed -- no drill left on bare ground."""
    payload = ore_seeding_payload(world)
    rects = [_patch_rect(patch) for patch in payload["ore_patches"]]

    for line in world.ore_lines:
        for position in line["drill_positions"]:
            assert any(_inside(position, rect) for rect in rects), (
                f"drill {position} for stage {line['stage']} is not covered by any seeded patch"
            )


def test_payload_covers_every_coal_drill(world):
    payload = ore_seeding_payload(world)
    rects = [_patch_rect(patch) for patch in payload["ore_patches"]]
    for position in world.coal_drill_positions:
        assert any(_inside(position, rect) for rect in rects), (
            f"coal drill {position} is not covered by any seeded patch"
        )


def test_payload_matches_every_declared_ore_patch(world):
    """The payload must seed exactly the WorldSpec's surveyed rectangles --
    not guessed coordinates -- with the same item and bounds."""
    payload = ore_seeding_payload(world)
    seeded_by_id = {patch["id"]: patch for patch in payload["ore_patches"]}
    pumpjack_ids = {f"pumpjack_{index}" for index in range(len(world.pumpjack_sites))}
    assert set(seeded_by_id) == {patch["id"] for patch in world.ore_patches} | pumpjack_ids
    for patch in world.ore_patches:
        seeded = seeded_by_id[patch["id"]]
        assert seeded["item"] == patch["item"]
        assert (seeded["x1"], seeded["y1"], seeded["x2"], seeded["y2"]) == (
            patch["x1"], patch["y1"], patch["x2"], patch["y2"],
        )
        assert seeded["amount"] > 0
    for index, site in enumerate(world.pumpjack_sites):
        seeded = seeded_by_id[f"pumpjack_{index}"]
        assert seeded["item"] == site["resource"]
        assert seeded["x1"] < site["position"][0] < seeded["x2"]
        assert seeded["y1"] < site["position"][1] < seeded["y2"]


def test_drill_footprints_covered_true_for_fixture(world):
    assert drill_footprints_covered(world) is True


def _fake_world(ore_patches, ore_lines, coal_patch_id, coal_drill_positions):
    """A minimal duck-typed stand-in for ElectronicsWorldSpec: the real dataclass
    refuses (at load time) to construct a spec with a drill outside its patch, so
    exercising the failure path in drill_footprints_covered needs a bypass."""
    from types import SimpleNamespace

    return SimpleNamespace(
        ore_patches=ore_patches, ore_lines=ore_lines,
        coal_patch_id=coal_patch_id, coal_drill_positions=coal_drill_positions,
    )


def test_drill_footprints_covered_false_when_drill_outside_patch(world):
    tampered = _fake_world(
        ore_patches=world.ore_patches,
        ore_lines=[
            {**line, "drill_positions": ((9999.5, 9999.5),) if index == 0 else line["drill_positions"]}
            for index, line in enumerate(world.ore_lines)
        ],
        coal_patch_id=world.coal_patch_id,
        coal_drill_positions=world.coal_drill_positions,
    )
    assert drill_footprints_covered(tampered) is False


def test_seed_ore_uses_fake_bridge_and_reports_per_resource(world):
    """Exercise the execution-flow helper end to end against a FAKE bridge:
    it must call /seed_ore_patches with the WorldSpec-derived payload and
    surface per-resource seeded counts."""
    from tools.electronics_execution import _seed_ore

    bridge = RecordingSeedBridge()
    messages: list[str] = []
    report = _seed_ore(bridge, world, messages.append)

    assert len(bridge.seed_calls) == 1
    assert bridge.seed_calls[0] == ore_seeding_payload(world)
    assert report["ok"] is True
    assert report["seeded_ore_tiles"] > 0
    assert set(report["seeded_by_resource"]) == {"iron-ore", "copper-ore", "coal", "crude-oil"}
    assert any("ore seeding" in message for message in messages)


def test_seed_ore_refuses_uncovered_drill(world):
    """A WorldSpec with a drill outside every declared patch must fail closed
    rather than seed a payload that leaves that drill on bare ground."""
    from tools.electronics_execution import _seed_ore

    tampered = _fake_world(
        ore_patches=world.ore_patches,
        ore_lines=world.ore_lines,
        coal_patch_id=world.coal_patch_id,
        coal_drill_positions=((9999.5, 9999.5),),
    )

    bridge = RecordingSeedBridge()
    with pytest.raises(ElectronicsExecutionError):
        _seed_ore(bridge, tampered, lambda _msg: None)
    assert bridge.seed_calls == []
