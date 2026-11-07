# Path: tests/test_oil_cell_intent.py
# Purpose: Prove the pure oil-cell intent planner reproduces the live planner without I/O.

from __future__ import annotations

import hashlib
import json

import pytest

from orchestrator import stage_chemical

Point = tuple[float, float]

# Fixed fake terrain: entity obstacles, clutter (rocks) and a small pond.
_ENTITY_TILES = {
    (-240, -100), (-241, -100), (-236, -70),
    *((x, -75) for x in range(-280, -270)), *((-275, y) for y in range(-100, -93)),
}
_CLUTTER_TILES = {(-266, -106), (-265, -106), (-290, -50), (-288, -98), (-287, -98)}
_POND_TILES = {
    *((x, y) for x in range(-232, -226) for y in range(-56, -50)),
    *((x, y) for x in range(-262, -258) for y in range(-72, -64)),
}
_WATER = {
    "position": (-217.5, -38.5), "output": (-218, -40),
    "resource": "water", "direction": "south",
}
_CRUDE_TILES = [(-293.5, -95.5), (-286.5, -104.5), (-298.5, -102.5)]
_COAL = (-300.0, -20.0)


def _in(tile: tuple[int, int], lo: Point, hi: Point) -> bool:
    return lo[0] <= tile[0] <= hi[0] and lo[1] <= tile[1] <= hi[1]


def _fake_occupied(_client, _surface, lo, hi, *, include_resources=False,
                   include_clutter=False, include_water=True, ignore_names=()):
    tiles = set(_ENTITY_TILES)
    if include_clutter:
        tiles |= _CLUTTER_TILES
    if include_water:
        tiles |= _POND_TILES
    return {tile for tile in tiles if _in(tile, lo, hi)}


def _fake_water(_client, _surface, lo, hi):
    return {tile for tile in _POND_TILES if _in(tile, lo, hi)}


def _fake_preflight(_client, _surface, _force, recipe, ingredient, source, feed,
                    count, **kwargs):
    # Window- and reservation-sensitive so a wrong request changes the digest.
    return ([{
        "action_type": "place_ghost", "entity": "transport-belt",
        "position": {"x": feed[0], "y": feed[1]},
        "direction": kwargs["destination_belt_direction"],
        "note": [recipe, ingredient, list(source), count,
                 kwargs["max_belt_route_tiles"], kwargs["mode"],
                 kwargs["destination_is_belt"],
                 len(kwargs["additional_blocked"])],
    }], "transport-belt")


def _live_world(monkeypatch) -> None:
    """The recorded world the pre-refactor planner was run against."""
    monkeypatch.setattr(stage_chemical, "_existing_outputs", lambda *_a: None)
    monkeypatch.setattr(
        stage_chemical.live_base, "nearest_resource",
        lambda *_a: ((-290.5, -98.5), (-299.0, -105.0), (-286.0, -95.0)),
    )
    monkeypatch.setattr(stage_chemical, "_find_oil_cell_site", lambda *_a: (-258.0, -108.0))
    monkeypatch.setattr(stage_chemical, "ensure_coal_mine", lambda *_a, **_k: _COAL)
    monkeypatch.setattr(stage_chemical, "_find_plastic_site", lambda *_a: (-285.0, -60.0))
    monkeypatch.setattr(
        stage_chemical.chemical_survey, "nearest_offshore_pump_site",
        lambda *_a: dict(_WATER),
    )
    monkeypatch.setattr(stage_chemical, "_crude_patch_tiles", lambda *_a: list(_CRUDE_TILES))
    monkeypatch.setattr(stage_chemical.live_base, "occupied_tiles", _fake_occupied)
    monkeypatch.setattr(stage_chemical.live_base, "water_tiles", _fake_water)
    monkeypatch.setattr(stage_chemical, "preflight_ingredient_transport", _fake_preflight)


# Recorded from the pre-refactor `_plan_opening_oil_cell` on this fixture
# (November 7, 2026); the refactor must reproduce them byte for byte.
# Re-recorded 2026-11-07: pumpjack outputs and mirrored machines corrected for Factorio 2.x
# (fluid geometry only; diffed against the old intent).
_GOLDEN = {
    "plastic-bar": "4b2fb87818b15894ed000497a5bd7b3ceb83450717d8434a94220f552cacb5f7",
    "sulfur": "208bb9678a728fbf61f1336350fbebaea16447e80cfcbe0f1d47d4259d231975",
}
_GOLDEN_LOG = "a89ef3f1e54b8cfec41125757fd87e2cad96197e49657b5f983615b8bf525c41"


def _facts(target_output: str) -> stage_chemical.OilCellFacts:
    return stage_chemical.OilCellFacts(
        target_output=target_output, oil_position=(-290.5, -98.5),
        cell_origin=(-258.0, -108.0), coal=_COAL, plastic_site=(-285.0, -60.0),
        water=dict(_WATER), patch_tiles=tuple(_CRUDE_TILES),
    )


def _digest(payload) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=list)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _live_intent(monkeypatch, target_output: str) -> tuple[dict, list[str]]:
    _live_world(monkeypatch)
    emitted: list[str] = []
    intent = stage_chemical._plan_opening_oil_cell(
        object(), object(), "nauvis", "player", (3.0, -1.0),
        lambda *_a, **_k: None, emitted.append, target_output=target_output,
    )
    return intent, emitted


def _record_surveys(monkeypatch, target_output: str) -> dict:
    """Answer the pure planner from the fake live world, keeping every answer."""
    _live_world(monkeypatch)
    recorded: dict = {}

    def answer(survey):
        recorded[survey] = stage_chemical._read_oil_survey(
            object(), "nauvis", "player", survey,
        )
        return recorded[survey]

    stage_chemical._drive_oil_cell_planner(
        stage_chemical._oil_cell_planner(_facts(target_output), lambda _m: None),
        answer,
    )
    monkeypatch.undo()
    return recorded


def _forbid_io(monkeypatch) -> None:
    def touched(*_a, **_k):
        raise AssertionError("pure oil planner touched the live world")

    for name in ("occupied_tiles", "water_tiles", "nearest_resource", "find_clear_area"):
        monkeypatch.setattr(stage_chemical.live_base, name, touched)
    for name in (
        "preflight_ingredient_transport", "ensure_coal_mine", "_crude_patch_tiles",
        "_find_oil_cell_site", "_find_plastic_site", "_submit",
    ):
        monkeypatch.setattr(stage_chemical, name, touched)
    monkeypatch.setattr(stage_chemical.chemical_survey, "nearest_offshore_pump_site", touched)


@pytest.mark.parametrize("target_output", ["plastic-bar", "sulfur"])
def test_live_planner_still_freezes_the_recorded_intent(monkeypatch, target_output) -> None:
    intent, emitted = _live_intent(monkeypatch, target_output)

    assert _digest(intent) == _GOLDEN[target_output]
    assert _digest(emitted) == _GOLDEN_LOG


@pytest.mark.parametrize("target_output", ["plastic-bar", "sulfur"])
def test_pure_planner_reproduces_live_intent_without_io(monkeypatch, target_output) -> None:
    live, emitted = _live_intent(monkeypatch, target_output)
    monkeypatch.undo()
    surveys = _record_surveys(monkeypatch, target_output)
    _forbid_io(monkeypatch)

    plan = stage_chemical.plan_oil_cell_intent(_facts(target_output), surveys)

    assert plan.intent == live
    assert _digest(plan.intent) == _GOLDEN[target_output]
    assert list(plan.log) == emitted
    assert [survey.kind for survey in surveys] == [
        "occupied", "occupied", "occupied", "water", "coal_route",
    ]


def test_pure_planner_names_the_first_missing_survey(monkeypatch) -> None:
    _forbid_io(monkeypatch)

    with pytest.raises(stage_chemical.MissingOilSurvey) as missing:
        stage_chemical.plan_oil_cell_intent(_facts("plastic-bar"), {})

    survey = missing.value.survey
    assert survey.kind == "occupied" and survey.best_effort
    assert dict(survey.flags)["include_clutter"] is True


def test_oil_cell_plan_exposes_bill_footprint_power_and_bounds(monkeypatch) -> None:
    surveys = _record_surveys(monkeypatch, "plastic-bar")
    plan = stage_chemical.plan_oil_cell_intent(_facts("plastic-bar"), surveys)

    bill = plan.bill_of_materials
    assert bill["pumpjack"] == 3 and bill["oil-refinery"] == 4
    assert bill["chemical-plant"] == 2 and bill["offshore-pump"] == 1
    power_entities = {
        action["entity"] for phase in plan.power_plan["phases"]
        for action in phase["actions"]
    }
    assert power_entities and power_entities <= stage_chemical._POWER_ENTITIES
    (min_x, min_y), (max_x, max_y) = plan.bounds
    jacks = stage_chemical._positions(plan.intent["plans"][0], "pumpjack")
    assert len(jacks) == 3
    assert all(min_x <= x <= max_x and min_y <= y <= max_y for x, y in jacks)
    assert {
        (int(x - 0.5), int(y - 0.5)) for x, y in jacks
    } <= plan.footprint_tiles
