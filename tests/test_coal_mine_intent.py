# Path: tests/test_coal_mine_intent.py
# Purpose: Prove the pure belt coal-mine planner reproduces the live `ensure_coal_mine` without I/O.

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from orchestrator import resource_patches, stage_chemical

Point = tuple[float, float]

# Fixed fake terrain: a coal patch box with ore holes and an obstacle strip near its east edge.
_PATCH = resource_patches.ResourcePatch((-330.5, 10.5), (-370.0, 0.0), (-326.0, 44.0), 812_345)
_HOLES = {(x, y) for x in range(-336, -330) for y in range(8, 14)}
_COAL_TILES = {
    (x, y) for x in range(-370, -325) for y in range(0, 45)
} - _HOLES
_BLOCKED = ((-345.0, 4.0), (-338.0, 20.0))  # Rectangle `area_clear` reports as occupied.
_EAST_REFERENCE = (-192.5, -91.0)
_WEST_REFERENCE = (-420.0, 30.0)


def _fake_resource_tiles(_client, _surface, lo, hi):
    return {t for t in _COAL_TILES if lo[0] <= t[0] <= hi[0] and lo[1] <= t[1] <= hi[1]}


def _fake_area_clear(_client, _surface, lo, hi):
    (bx0, by0), (bx1, by1) = _BLOCKED
    return hi[0] < bx0 or lo[0] > bx1 or hi[1] < by0 or lo[1] > by1


def _fake_drills_have_resource(_client, _surface, resource, drills):
    assert resource == "coal"
    return all((int(x // 1), int(y // 1)) in _COAL_TILES for x, y in drills)


def _live_world(monkeypatch, record: dict) -> None:
    """The recorded world the pre-refactor `ensure_coal_mine` was run against."""
    monkeypatch.setattr(stage_chemical, "retire_depleted_mines", lambda *_a, **_k: 0)
    monkeypatch.setattr(stage_chemical.extraction_state, "find_resource_mine", lambda *_a: None)
    monkeypatch.setattr(stage_chemical.resource_patches, "nearest_viable_patch", lambda *_a: _PATCH)
    monkeypatch.setattr(stage_chemical.resource_patches, "resource_tiles", _fake_resource_tiles)
    monkeypatch.setattr(stage_chemical.live_base, "area_clear", _fake_area_clear)
    monkeypatch.setattr(
        stage_chemical.live_base, "drill_footprints_have_resource", _fake_drills_have_resource,
    )

    def submit(_client, _bridge, surface, plan, name, _emit, *, stage_coverage):
        record["submitted"] = {"surface": surface, "name": name, "plan": copy.deepcopy(plan)}

    monkeypatch.setattr(stage_chemical, "_submit", submit)


def _digest(payload) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=list)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _live_run(monkeypatch, reference: Point) -> dict:
    record: dict = {}
    _live_world(monkeypatch, record)
    emitted: list[str] = []

    def service_stage(*args, **kwargs):
        record["service"] = {"args": list(args[4:9]), "kwargs": kwargs}

    record["result"] = stage_chemical.ensure_coal_mine(
        object(), object(), "nauvis", "player", reference, service_stage, emitted.append,
        prefer_nearest_patch=True,
    )
    record["emitted"] = emitted
    return record


# Recorded from the pre-refactor `ensure_coal_mine` on this fixture (November 7, 2026);
# the refactor must reproduce them byte for byte.
_GOLDEN = {
    "east": "084ca158f88ed5a5e24bc9f02d4767428a7f8b3f86a35a84479e6faa27899918",
    "west": "3188eded0d2014665c47d059e7384b97eabeebd3c789c93869ef3d815ed27f13",
}


@pytest.mark.parametrize("side,reference", [("east", _EAST_REFERENCE), ("west", _WEST_REFERENCE)])
def test_live_coal_mine_still_submits_the_recorded_plan(monkeypatch, side, reference) -> None:
    record = _live_run(monkeypatch, reference)

    assert record["result"] is None
    assert _digest(record) == _GOLDEN[side]


def _facts(reference: Point) -> stage_chemical.CoalMineFacts:
    return stage_chemical.CoalMineFacts(reference, _PATCH.minimum, _PATCH.maximum, _PATCH.amount)


def _record_surveys(monkeypatch, reference: Point) -> dict:
    """Answer the pure planner from the fake live world, keeping every answer."""
    _live_world(monkeypatch, {})
    recorded: dict = {}

    def answer(survey):
        recorded[survey] = stage_chemical._read_coal_survey(object(), "nauvis", survey)
        return recorded[survey]

    stage_chemical._coal_mine_planner(_facts(reference), answer)
    monkeypatch.undo()
    return recorded


def _forbid_io(monkeypatch) -> None:
    def touched(*_a, **_k):
        raise AssertionError("pure coal-mine planner touched the live world")

    for name in ("area_clear", "drill_footprints_have_resource", "transport_belt_direction_at"):
        monkeypatch.setattr(stage_chemical.live_base, name, touched)
    for name in ("resource_tiles", "nearest_viable_patch"):
        monkeypatch.setattr(stage_chemical.resource_patches, name, touched)
    monkeypatch.setattr(stage_chemical.extraction_state, "find_resource_mine", touched)
    for name in ("retire_depleted_mines", "_submit", "_ensure_plan_construction_coverage",
                 "_coal_compatible_mining_origins"):
        monkeypatch.setattr(stage_chemical, name, touched)


@pytest.mark.parametrize("side,reference", [("east", _EAST_REFERENCE), ("west", _WEST_REFERENCE)])
def test_pure_coal_planner_reproduces_the_live_plan_without_io(monkeypatch, side, reference) -> None:
    live = _live_run(monkeypatch, reference)
    monkeypatch.undo()
    surveys = _record_surveys(monkeypatch, reference)
    _forbid_io(monkeypatch)

    mine = stage_chemical.plan_coal_mine_intent(_facts(reference), surveys)

    plan = {**copy.deepcopy(mine.plan), "surface": "nauvis", "force": "player"}
    assert plan == live["submitted"]["plan"]
    assert mine.output_side == side and mine.count == 2
    assert live["service"]["args"][0] == "mining stage for coal"
    kinds = [survey.kind for survey in surveys]
    # One bulk ore survey, then bounded probes; the obstacle and ore holes force rejected origins.
    assert kinds[0] == "origins" and kinds.count("origins") == 1
    assert kinds.count("clear") > 1 and set(kinds) <= {"origins", "clear", "drill_ore"}


def test_pure_coal_planner_names_the_first_missing_survey(monkeypatch) -> None:
    _forbid_io(monkeypatch)

    with pytest.raises(stage_chemical.MissingCoalSurvey) as missing:
        stage_chemical.plan_coal_mine_intent(_facts(_EAST_REFERENCE), {})

    survey = missing.value.survey
    assert survey.kind == "origins" and survey.points == (_EAST_REFERENCE,)


def test_coal_plan_exposes_bill_footprint_bounds_and_belt_source(monkeypatch) -> None:
    surveys = _record_surveys(monkeypatch, _EAST_REFERENCE)
    mine = stage_chemical.plan_coal_mine_intent(_facts(_EAST_REFERENCE), surveys)

    assert mine.bill_of_materials["electric-mining-drill"] == 4
    assert mine.bill_of_materials["substation"] == 1
    (min_x, min_y), (max_x, max_y) = mine.bounds
    belts = stage_chemical._positions(mine.plan, "transport-belt")
    assert all(min_x <= x <= max_x and min_y <= y <= max_y for x, y in belts)
    # East flow: the belt source is the collector's downstream (east) end.
    assert mine.output == (max(x for x, _ in belts), belts[0][1])
    assert {(int(x // 1), int(y // 1)) for x, y in belts} <= mine.footprint_tiles


def test_unsited_coal_mine_is_a_stuck_error(monkeypatch) -> None:
    _forbid_io(monkeypatch)
    no_origins = {stage_chemical.CoalSurvey("origins", _PATCH.minimum, _PATCH.maximum, (_EAST_REFERENCE,)): []}
    with pytest.raises(stage_chemical.StuckError, match="ore_compatible_candidates=0"):
        stage_chemical.plan_coal_mine_intent(_facts(_EAST_REFERENCE), no_origins)
