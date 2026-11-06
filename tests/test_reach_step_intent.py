# Path: tests/test_reach_step_intent.py
# Purpose: Prove the pure coverage/power reach-step planner reproduces the live drivers without I/O.

from __future__ import annotations

import copy
import hashlib
import json
import math

import pytest

from orchestrator import (
    autonomous_builder, live_base, reach_survey, resource_patches,
    roboport_placement, stage_services,
)

Point = tuple[float, float]

# Fixed fake world: one generated base network around the roboport at the
# origin, an unpowered island pole, a factory block on the first chain ideal,
# a rich ore patch on the power route and a packed consumer with no free
# hookup tile.
_BASE_PORT = (0.0, 0.0)
_POLES = {
    (2.0, 2.0): ("big-electric-pole", 1),
    (-20.0, 4.0): ("substation", 1),
    (-150.5, -20.5): ("medium-electric-pole", 7),
}
_GENERATION = {1: 50_000.0, 7: 0.0}
_BLOCK_TILES = {(x, y) for x in range(-46, -36) for y in range(-10, -2)}
_RICH_PATCH = {(x, y) for x in range(-30, -22) for y in range(-14, -6)}
_PACKED_CONSUMER = (-80.5, 40.5)
_PACKED_TILES = {(x, y) for x in range(-92, -69) for y in range(29, 52)}
_ENTITIES = {_PACKED_CONSUMER: {"name": "assembling-machine-2"}}


class _World:
    """Stateful fake live world; `calls` records every read in order."""

    def __init__(self, stock: dict[str, int], *, rich: bool = True,
                 merge_islands: bool = False) -> None:
        self.stock = dict(stock)
        self.rich = rich
        self.merge_islands = merge_islands
        self.ports = [_BASE_PORT]
        self.poles = dict(_POLES)
        self.calls: list[list] = []
        self.submitted: list[list] = []

    def log(self, name: str, *args) -> None:
        self.calls.append([name, *[_plain(arg) for arg in args]])

    def obstacles(self, *, resources: bool) -> set[tuple[int, int]]:
        tiles = set(_BLOCK_TILES) | _PACKED_TILES
        for position, (name, _net) in self.poles.items():
            size = 2 if name in {"big-electric-pole", "substation"} else 1
            tiles |= stage_services.footprint_tile_indices(position, size)
        for port in self.ports:
            tiles |= stage_services.footprint_tile_indices(port, 4)
        return tiles | (_RICH_PATCH if resources else set())

    def pole_at(self, position: Point):
        for pole, record in self.poles.items():
            if math.dist(pole, position) <= 0.5:
                return record
        return None


def _plain(value):
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _in(tile, lo, hi) -> bool:
    return lo[0] <= tile[0] <= hi[0] and lo[1] <= tile[1] <= hi[1]


def _install(monkeypatch, world: _World) -> None:
    def pole_network_id(_c, _s, position):
        world.log("pole_network_id", position)
        record = world.pole_at(position)
        return record[1] if record else None

    def nearest_powered_pole(_c, _s, _f, near, exclude_network_id=None, *, avoid_resources=False):
        world.log("nearest_powered_pole", near, exclude_network_id, avoid_resources)
        options = [
            (math.dist(position, near), position, name)
            for position, (name, net) in world.poles.items()
            if _GENERATION.get(net, 0) > 0 and net != exclude_network_id
        ]
        return min(options)[1:] if options else None

    def network_generation_kw(_c, _s, _f, near):
        world.log("network_generation_kw", near)
        options = [
            (math.dist(position, near), net)
            for position, (_name, net) in world.poles.items()
            if math.dist(position, near) <= 10
        ]
        return _GENERATION.get(min(options)[1], 0.0) if options else None

    def entity_at(_c, _s, position):
        world.log("entity_at", position)
        if position in _ENTITIES:
            return dict(_ENTITIES[position])
        record = world.pole_at(position)
        return {"name": record[0]} if record else None

    def occupied_tiles(_c, _s, lo, hi, *, ignore_names=(), include_resources=False,
                       include_clutter=False, include_water=True):
        world.log("occupied_tiles", lo, hi, include_resources, include_clutter)
        return {t for t in world.obstacles(resources=include_resources) if _in(t, lo, hi)}

    def occupied_tile_owners(_c, _s, lo, hi, *, include_resources=False):
        world.log("occupied_tile_owners", lo, hi, include_resources)
        return {
            tile: ("assembling-machine-2", -80.5, 40.5)
            for tile in world.obstacles(resources=include_resources) if _in(tile, lo, hi)
        }

    def area_clear(_c, _s, lo, hi, *, avoid_resources=False, resource_clearance=0.0):
        world.log("area_clear", lo, hi, avoid_resources)
        box = {
            (x, y)
            for x in range(math.floor(lo[0]), math.ceil(hi[0]))
            for y in range(math.floor(lo[1]), math.ceil(hi[1]))
        }
        return not (box & world.obstacles(resources=avoid_resources))

    def box_has_reserved_patch(_c, _s, lo, hi, *, minimum_amount=0):
        world.log("box_has_reserved_patch", lo, hi, minimum_amount)
        return world.rich and any(_in(tile, lo, hi) for tile in _RICH_PATCH)

    def resource_tiles(_c, _s, lo, hi):
        world.log("resource_tiles", lo, hi)
        return {tile for tile in _RICH_PATCH if _in(tile, lo, hi)}

    def transferable_items(_c, _s, _f):
        world.log("transferable_items")
        return dict(world.stock)

    def nearest_roboport(_c, _s, _f, near):
        world.log("nearest_roboport", near)
        return min(world.ports, key=lambda port: (math.dist(port, near), port))

    def simple(name, value):
        def read(_c, _s, *args):
            world.log(name, *args[1:] if args and args[0] == "player" else args)
            return value
        return read

    def submit(_c, _b, _s, plan, name, _emit, **kwargs):
        if not world.submitted:
            world.first_submit_call = len(world.calls)
        world.submitted.append([name, copy.deepcopy(plan), sorted(kwargs)])
        for action in plan["phases"][0]["actions"]:
            point = (action["position"]["x"], action["position"]["y"])
            if action["entity"] == "roboport":
                world.ports.append(point)
            else:
                world.poles[point] = (action["entity"], 1)
                if world.merge_islands:
                    for position, (pole, _net) in list(world.poles.items()):
                        if math.dist(position, point) <= 32:
                            world.poles[position] = (pole, 1)
        return {"ok": True}

    for name, fake in {
        "pole_network_id": pole_network_id,
        "nearest_powered_pole": nearest_powered_pole,
        "network_generation_kw": network_generation_kw,
        "entity_at": entity_at,
        "occupied_tiles": occupied_tiles,
        "occupied_tile_owners": occupied_tile_owners,
        "area_clear": area_clear,
        "transferable_items": transferable_items,
        "nearest_roboport": nearest_roboport,
        "roboport_ghost_positions": simple("roboport_ghost_positions", []),
        "roboports_needing_power": simple("roboports_needing_power", []),
        "entity_status_name": simple("entity_status_name", "working"),
        "roboport_energy": simple("roboport_energy", 1e8),
    }.items():
        monkeypatch.setattr(live_base, name, fake)
    monkeypatch.setattr(resource_patches, "box_has_reserved_patch", box_has_reserved_patch)
    monkeypatch.setattr(resource_patches, "resource_tiles", resource_tiles)
    monkeypatch.setattr(stage_services, "_submit", submit)
    monkeypatch.setattr(stage_services, "_await_bot_built_infrastructure", lambda *_a, **_k: None)
    monkeypatch.setattr(stage_services, "_await_built_status", lambda *_a, **_k: "working")
    monkeypatch.setattr(stage_services, "_PENDING_POWER_BRIDGES", {})
    monkeypatch.setattr(stage_services, "_COVERAGE_PROGRESS", stage_services._CoverageProgressState())
    monkeypatch.setattr(stage_services.time, "sleep", lambda _s: None)
    monkeypatch.setattr(autonomous_builder, "_top_up_solar_generation", lambda *_a, **_k: None)


class _Client:
    """Live-capable RCON stand-in: the drivers only test for `command`."""

    def command(self, _lua: str) -> str:  # pragma: no cover - never reached
        raise AssertionError("fake world must answer every read")


_RESERVED = frozenset({(x, y) for x in range(-60, -50) for y in range(-12, -4)})
_SCENARIOS = {
    "construction_reach": dict(
        call="coverage", target=(-297.0, -40.0), purpose="construction",
        stock={"big-electric-pole": 40, "medium-electric-pole": 40},
    ),
    "logistic_reach_medium_only": dict(
        call="coverage", target=(-60.0, 10.0), purpose="logistic",
        stock={"medium-electric-pole": 30}, world={"rich": False},
    ),
    "island_bridge": dict(
        call="power", near=(-150.5, -20.5),
        stock={"big-electric-pole": 40, "medium-electric-pole": 40},
        world={"merge_islands": True},
    ),
    "island_bridge_stays_dark": dict(
        call="power", near=(-150.5, -20.5),
        stock={"big-electric-pole": 40, "medium-electric-pole": 40},
    ),
    "offline_full_chain": dict(
        call="coverage", target=(-200.0, -30.0), purpose="construction",
        stock={}, offline=True,
    ),
    "packed_hookup": dict(
        call="power", near=_PACKED_CONSUMER, stock={"medium-electric-pole": 40},
    ),
}


def _run_live(monkeypatch, name: str, world: _World | None = None) -> dict:
    scenario = _SCENARIOS[name]
    world = world or _World(scenario["stock"], **scenario.get("world", {}))
    _install(monkeypatch, world)
    emitted: list[str] = []
    outcome: dict = {}
    try:
        if scenario["call"] == "coverage":
            result = stage_services.extend_roboport_coverage(
                None if scenario.get("offline") else _Client(), object(), "nauvis", "player", scenario["target"],
                emitted.append, purpose=scenario["purpose"],
                reserved_tiles=set(_RESERVED),
            )
        else:
            result = stage_services.extend_power(
                _Client(), object(), "nauvis", "player", scenario["near"],
                emitted.append, reserved_tiles=set(_RESERVED), detailed=True,
            )
            result = [result.ready, result.changed]
        outcome["result"] = result
    except Exception as error:  # the recorded behaviour includes the signal
        outcome["error"] = [
            type(error).__name__, str(error), getattr(error, "code", None),
            getattr(error, "state", None), getattr(error, "details", None),
        ]
    outcome.update(submitted=world.submitted, emitted=emitted, calls=world.calls)
    return outcome


def _digest(payload) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=_plain)
    return hashlib.sha256(canonical.encode()).hexdigest()


# Recorded from the pre-refactor `extend_roboport_coverage` / `extend_power`
# on this fixture (November 7, 2026); the refactor must reproduce them.
_GOLDEN = {
    "construction_reach": "34508060fdf7371e35a6fe94443452df312f23507447a48a0e04bbc1258ec42d",
    "island_bridge": "ab2ade87b463e261af4cf35bffd1d5eec3e7c2d5d8846f2dc43e79584292475b",
    "island_bridge_stays_dark": "6fb67bb552387ca201acfb2462655f64faac3c8354aada5486d89c05a45351f9",
    "logistic_reach_medium_only": "74767b33e73f81615aa023152f64b8a52c5f1789d41a404997d5a9a95049738d",
    "offline_full_chain": "df9d4125b44d11066493608d22d5580c382eaedd2ac4bcc64c370d11f2144d8b",
    "packed_hookup": "ba663443d0d8361bd7a5723f10e5be146f2eb414174d6c7130e6a40bba03ac16",
}


@pytest.mark.parametrize("name", sorted(_SCENARIOS))
def test_live_reach_drivers_still_reproduce_recorded_behaviour(monkeypatch, name) -> None:
    outcome = _run_live(monkeypatch, name)

    assert _digest(outcome) == _GOLDEN[name], json.dumps(outcome, default=_plain)[:4000]


# Reads the live driver makes itself before handing the hop to the planner.
_DRIVER_READS = 4  # roboports_needing_power, nearest_roboport, ghosts, status


def _facts(name: str) -> stage_services.ReachFacts:
    scenario = _SCENARIOS[name]
    return stage_services.ReachFacts(
        target=scenario["target"], source=_BASE_PORT, purpose=scenario["purpose"],
        reserved_tiles=_RESERVED,
    )


def _record_surveys(monkeypatch, name: str) -> tuple[dict, list]:
    """Answer the pure planner from the fake live world, keeping every answer."""
    scenario = _SCENARIOS[name]
    _install(monkeypatch, _World(scenario["stock"], **scenario.get("world", {})))
    recorded: dict = {}
    order: list = []

    def read(survey):
        order.append(survey)
        recorded[survey] = reach_survey.read_reach_survey(
            object(), "nauvis", "player", survey,
        )
        return recorded[survey]

    def answer(survey):
        if survey.kind != "chain_positions":
            return read(survey)
        return reach_survey.drive_reach_planner(
            roboport_placement.clear_chain_planner(survey.position, **dict(survey.flags)),
            read,
        )

    reach_survey.drive_reach_planner(
        stage_services._reach_step_planner(_facts(name), lambda _m: None), answer,
    )
    monkeypatch.undo()
    return recorded, order


def _forbid_io(monkeypatch) -> None:
    def touched(*_a, **_k):
        raise AssertionError("pure reach planner touched the live world")

    for name in (
        "pole_network_id", "nearest_powered_pole", "network_generation_kw",
        "entity_at", "occupied_tiles", "occupied_tile_owners", "area_clear",
        "transferable_items", "nearest_roboport", "roboport_ghost_positions",
        "entity_status_name", "roboport_energy", "roboports_needing_power",
    ):
        monkeypatch.setattr(live_base, name, touched)
    monkeypatch.setattr(resource_patches, "box_has_reserved_patch", touched)
    monkeypatch.setattr(resource_patches, "resource_tiles", touched)
    for module in (reach_survey, stage_services, roboport_placement):
        monkeypatch.setattr(module, "read_reach_survey", touched, raising=False)
    for name in ("_submit", "clear_chain_positions", "extend_power", "_await_built_status"):
        monkeypatch.setattr(stage_services, name, touched)


@pytest.mark.parametrize("name", ["construction_reach", "logistic_reach_medium_only"])
def test_pure_reach_step_reproduces_the_live_hop_without_io(monkeypatch, name) -> None:
    scenario = _SCENARIOS[name]
    world = _World(scenario["stock"], **scenario.get("world", {}))
    live = _run_live(monkeypatch, name, world)
    monkeypatch.undo()
    surveys, order = _record_surveys(monkeypatch, name)
    _forbid_io(monkeypatch)

    step = stage_services.plan_reach_step(_facts(name), surveys)

    assert step.outcome == "hop"
    assert [list(p) for p in step.placements] == [
        [action["entity"], (action["position"]["x"], action["position"]["y"])]
        for _name, plan, _kw in live["submitted"]
        for action in plan["phases"][0]["actions"]
    ]
    assert list(step.log) == live["emitted"]
    # Same reads, same order, as the live driver between its own
    # observations and its first write.
    assert [
        [survey.kind] for survey in order
    ] == [call[:1] for call in live["calls"][_DRIVER_READS:world.first_submit_call]]


def test_pure_reach_step_names_the_first_missing_survey(monkeypatch) -> None:
    _forbid_io(monkeypatch)

    with pytest.raises(reach_survey.MissingReachSurvey) as missing:
        stage_services.plan_reach_step(_facts("construction_reach"), {})

    survey = missing.value.survey
    assert survey.kind == "area_clear"
    assert dict(survey.flags) == {"avoid_resources": True}


def test_reach_step_exposes_bill_and_bounds(monkeypatch) -> None:
    surveys, _order = _record_surveys(monkeypatch, "construction_reach")
    step = stage_services.plan_reach_step(_facts("construction_reach"), surveys)

    # Recorded surveys are reusable: planning never mutates an answer.
    assert stage_services.plan_reach_step(_facts("construction_reach"), surveys) == step

    assert step.bill_of_materials == {
        "big-electric-pole": 1, "medium-electric-pole": 1, "roboport": 1,
    }
    (min_x, min_y), (max_x, max_y) = step.bounds
    assert all(
        min_x <= x <= max_x and min_y <= y <= max_y for _n, (x, y) in step.placements
    )
    port = step.roboports[0]
    assert math.dist(_BASE_PORT, port) <= stage_services._ROBOPORT_LINK_DISTANCE
    assert step.chain[0] == port and len(step.chain) > 1


def test_reach_step_short_circuits_need_no_surveys(monkeypatch) -> None:
    _forbid_io(monkeypatch)
    plan = stage_services.plan_reach_step
    Facts = stage_services.ReachFacts

    assert plan(Facts(target=(10.0, 0.0), source=None), {}).outcome == "no_source"
    assert plan(Facts(target=(10.0, 0.0), source=_BASE_PORT), {}).outcome == "covered"
    flight = plan(Facts(
        target=(-297.0, -40.0), source=_BASE_PORT, roboport_ghosts=((-40.0, -5.0),),
    ), {})
    assert (flight.outcome, flight.in_flight) == ("in_flight", (-40.0, -5.0))
