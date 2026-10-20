# Path: tests/test_second_collector.py
# Purpose: Reproduce the recorded C9 copper district stall and prove the second-collector, reserve and cause fixes.

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from orchestrator import extraction_capacity, extraction_state, live_base, resource_patches, stage_extraction
from orchestrator.extraction_state import ResourceMine
from planners.belt_bridge import RouteFailure, plan_belt_route
from planners.plan_validation import ENTITY_FOOTPRINTS, actions
from orchestrator.stage_extraction import direct_mine_plan
from planners.transport_occupancy import Occupant, RouteOccupancy
from training.laya_ops_runner import compile_options

FIXTURE = Path(__file__).parent / "fixtures" / "c9_batch_s79300_copper_district.json"
# The owned collector as classified live: three columns at 81.5..87.5 on belt
# y=-39.5, tail 77.5, haul head 89.5, growth west; the default ten-column claim.
OWNED = ResourceMine(
    output=(77.5, -39.5), drill_count=3, row_capacity=13, belt_y=-39.5,
    first_column_x=81.5, haul_head=(89.5, -39.5), growth_direction=-1,
)


def _footprint(x: float, y: float, size: int) -> set[tuple[int, int]]:
    half = size / 2
    return {
        (tx, ty)
        for tx in range(math.floor(x - half), math.ceil(x + half))
        for ty in range(math.floor(y - half), math.ceil(y + half))
    }


class RecordedWorld:
    """Recorded entities, plan reservations and 4-tile ore samples."""

    def __init__(self) -> None:
        data = json.loads(FIXTURE.read_text())
        self.ore: dict[tuple[int, int], str] = {}
        for sample in data["resource_samples"]:
            x, y = math.floor(sample["x"]), math.floor(sample["y"])
            for tx in range(x - 2, x + 2):
                for ty in range(y - 2, y + 2):
                    self.ore[(tx, ty)] = sample["item"]
        self.entities: set[tuple[int, int]] = set()
        for entity in data["entities"]:
            self.entities |= _footprint(entity["x"], entity["y"], ENTITY_FOOTPRINTS.get(entity["name"], 1))
        self.reserved = frozenset(
            tuple(tile) for entry in data["reservations"] for tile in entry["tiles"]
        )
        # Reservations stand in for built belts and plans the snapshot omits.
        self.occupied = self.entities | set(self.reserved)
        copper = [tile for tile, item in self.ore.items() if item == "copper-ore" and tile[1] < -20]
        self.patch = resource_patches.ResourcePatch(
            (77.5, -40.5),
            (min(x for x, _ in copper) + 0.5, min(y for _, y in copper) + 0.5),
            (max(x for x, _ in copper) + 0.5, max(y for _, y in copper) + 0.5),
            500_000,
        )

    def area_clear(self, _client, _surface, lower, upper, **_kwargs) -> bool:
        return not any(
            (x, y) in self.occupied
            for x in range(math.floor(lower[0]), math.ceil(upper[0]))
            for y in range(math.floor(lower[1]), math.ceil(upper[1]))
        )

    def conflicts(self, _client, _surface, resource, centres):
        found = []
        for x, y in centres:
            if not any(self.ore.get(tile) == resource for tile in _footprint(x, y, 3)):
                found.append(((x, y), "no ore"))
            elif any(self.ore.get(tile, resource) != resource for tile in _footprint(x, y, 5)):
                found.append(((x, y), "foreign ore"))
        return found

    def link(self, source, destination, avoid):
        occupancy = RouteOccupancy((
            Occupant("live_entity", frozenset(self.occupied)),
            *((Occupant("pending_plan", frozenset(avoid)),) if avoid else ()),
        ))
        route = plan_belt_route(
            (source[0] + 1, source[1]), destination, belt_type="transport-belt",
            occupancy=occupancy, district_id="test", source_heading="east",
            destination_heading="east", max_route_tiles=200, underground_reach=5,
        )
        return f"{route.reason}: {route.detail}" if isinstance(route, RouteFailure) else route.actions


@pytest.fixture
def world(monkeypatch) -> RecordedWorld:
    recorded = RecordedWorld()
    stage_extraction.clear_new_mine_cache()
    monkeypatch.setattr(extraction_state, "find_resource_mines", lambda *_a: [OWNED])
    monkeypatch.setattr(extraction_state, "resource_drill_count", lambda *_a: 6)
    monkeypatch.setattr(extraction_state, "mining_productivity_bonus", lambda *_a: 0.0)
    monkeypatch.setattr(extraction_state, "pending_plate_smelter", lambda *_a: False)
    monkeypatch.setattr(resource_patches, "patch_for_extraction", lambda *_a, **_k: recorded.patch)
    monkeypatch.setattr(live_base, "area_clear", recorded.area_clear)
    monkeypatch.setattr(live_base, "drill_siting_conflicts", recorded.conflicts)
    monkeypatch.setattr(
        live_base, "drill_footprints_have_resource",
        lambda *a: not recorded.conflicts(*a),
    )
    yield recorded
    stage_extraction.clear_new_mine_cache()


def _plan(client, recorded: RecordedWorld, **kwargs):
    return stage_extraction.plan_local_extraction(
        client, "nauvis", "player", "copper-plate", (0.0, 0.0), 3,
        belt_type="transport-belt", inserter_type="inserter", reuse_existing=False,
        owned_smelter_origin=(92.0, -30.0), **kwargs,
    )


def test_recorded_copper_district_was_terminal_without_a_second_collector(world) -> None:
    with pytest.raises(stage_extraction.DistrictExpansionUnbuildable) as raised:
        _plan(object(), world, reserved_tiles=world.reserved)
    # West of x=81.5 is one column of ore at most; yellow cannot merge a band.
    assert raised.value.details["causes"]["straight"] == "off_patch"
    assert raised.value.details["causes"]["parallel"] == "merge_reach"
    assert raised.value.details["cause"] == "no_second_site"


def test_recorded_copper_district_opens_a_linked_second_collector(world) -> None:
    planned = _plan(object(), world, reserved_tiles=world.reserved, link_route=world.link)

    drills = [
        (a["position"]["x"], a["position"]["y"]) for a in actions(planned.build_plan)
        if a["entity"] == "electric-mining-drill"
    ]
    assert planned.drill_count == len(drills) == 6
    owned = stage_extraction.owned_collector_tiles(OWNED)
    for x, y in drills:
        tiles = _footprint(x, y, 3)
        assert not tiles & (owned | world.occupied)
        assert any(world.ore.get(tile) == "copper-ore" for tile in tiles)
    # Away from the owned row: east of the x=90-91 haul, or north of the owned apron.
    assert all(x > 91 or y < -46 for x, y in drills)
    placed = set()
    for action in actions(planned.build_plan):
        if action["entity"] in {"transport-belt", "underground-belt", "substation"}:
            placed |= _footprint(action["position"]["x"], action["position"]["y"],
                                 ENTITY_FOOTPRINTS.get(action["entity"], 1))
    assert not placed & world.occupied
    link = next(p for p in planned.build_plan["phases"] if p["name"] == "second_collector_link")
    last = link["actions"][-1]
    assert (last["entity"], last["position"], last["direction"]) == (
        "transport-belt", {"x": 76.5, "y": -39.5}, "east",
    )
    # Ore rides the owned collector and haul into the owned refinery.
    assert planned.ore_output == OWNED.haul_head
    assert planned.expansion_positions == ()
    assert planned.shared_belt_y != OWNED.shared_belt_y


def test_unroutable_link_is_a_typed_cause(world) -> None:
    with pytest.raises(stage_extraction.DistrictExpansionUnbuildable) as raised:
        _plan(object(), world, reserved_tiles=world.reserved, link_route=lambda *_a: "blocked")
    assert raised.value.details["cause"] == "link_unroutable"


def test_exhausted_corridor_is_recorded_and_not_resurveyed(world, monkeypatch) -> None:
    client = object()
    with pytest.raises(stage_extraction.DistrictExpansionUnbuildable):
        _plan(client, world)
    surveyed = []
    monkeypatch.setattr(
        stage_extraction, "buildable_batch_prefix_with_cause",
        lambda *a: surveyed.append(a) or ((), "off_patch"),
    )
    [limited] = extraction_state.apply_growth_limits(client, "nauvis", "copper-ore", [OWNED])
    assert limited.row_capacity == limited.drill_count
    with pytest.raises(stage_extraction.DistrictExpansionUnbuildable) as raised:
        _plan(client, world)
    assert surveyed == []
    assert raised.value.details["causes"]["straight"] == "off_patch"


def test_fresh_row_records_its_verified_reserve_not_ten_columns(monkeypatch) -> None:
    stage_extraction.clear_new_mine_cache()
    client = object()
    plan, head = direct_mine_plan((80, -40), 3, belt_type="transport-belt",
                                  inserter_type="inserter", reserved_pair_columns=1, output_side="east")
    plan["verified_reserve_columns"] = 1
    monkeypatch.setattr(stage_extraction, "_new_direct_mine", lambda *_a, **_k: ((80, -40), 6, plan, head))
    monkeypatch.setattr(extraction_state, "find_resource_mines", lambda *_a: [])
    monkeypatch.setattr(extraction_state, "resource_drill_count", lambda *_a: 0)
    monkeypatch.setattr(extraction_state, "mining_productivity_bonus", lambda *_a: 0.0)
    monkeypatch.setattr(resource_patches, "patch_for_extraction", lambda *_a, **_k: resource_patches.ResourcePatch(
        (80.0, -40.0), (70.0, -60.0), (110.0, -25.0), 500_000))
    stage_extraction.plan_local_extraction(
        client, "nauvis", "player", "copper-plate", (0.0, 0.0), 3,
        belt_type="transport-belt", inserter_type="inserter", owned_smelter_origin=(92.0, -30.0),
    )
    built = ResourceMine((76.5, -39.5), 3, row_capacity=13, belt_y=-39.5, first_column_x=81.5,
                         haul_head=head, growth_direction=-1)
    [limited] = extraction_state.apply_growth_limits(client, "nauvis", "copper-ore", [built])
    assert limited.row_capacity == 4
    stage_extraction.clear_new_mine_cache()


def test_east_collector_reserves_its_verified_corridor_without_continuation() -> None:
    plan, _head = direct_mine_plan((80, -40), 3, belt_type="transport-belt",
                                   inserter_type="inserter", reserved_pair_columns=2, output_side="east")
    reserved = {tuple(tile) for tile in plan["reserved_tiles"]}
    # Two spare columns west of x=81.5 (75.5, 78.5) and the drill rows themselves.
    assert (74, -40) in reserved and (73, -40) not in reserved
    assert (89, -44) in reserved


@pytest.mark.parametrize(("belt_type", "reachable"), [
    ("transport-belt", False), ("fast-transport-belt", True), ("express-transport-belt", True),
])
def test_parallel_band_needs_an_underground_spanning_pitch_minus_one(belt_type, reachable) -> None:
    assert extraction_capacity.parallel_band_merge_reachable(belt_type) is reachable


def test_unbuildable_cause_is_an_extend_option_fact() -> None:
    board = {"needs": [{"item": "copper-plate", "condition": "under_capacity", "goal_required_per_s": 1.0}]}
    base = [
        {"operation": op, "params": {"recipe": plate}, "ok": True, "code": "ok", "tick": 1}
        for plate in ("iron-plate", "copper-plate") for op in ("starter", "foundation", "retire_starter")
    ]
    failed = {"operation": "extend", "params": {"recipe": "copper-plate"}, "ok": False,
              "code": "district_expansion_unbuildable", "tick": 2,
              "details": {"cause": "no_second_site", "causes": {"straight": "off_patch"}}}
    options = compile_options(board, base + [failed], tick=3)["options"]
    extend = next(o for o in options if o["option_id"] == "op.extend.copper-plate")
    assert extend["features"]["unbuildable_cause"] == "no_second_site"
    succeeded = {**failed, "ok": True, "code": "ok", "details": {}, "tick": 4}
    options = compile_options(board, base + [failed, succeeded], tick=5)["options"]
    extend = next(o for o in options if o["option_id"] == "op.extend.copper-plate")
    assert "unbuildable_cause" not in extend["features"]
