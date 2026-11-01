# Path: tests/test_fluid_topology.py | Purpose: Exercise fail-closed fluid topology, pairing and attachment checks.
from __future__ import annotations

import copy
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import pytest

from core.fluid_topology import (
    FluidTopologyError, load_fluid_prototype_catalog, recipe_box_assignment, validate_fluid_topology,
)

MODS = {"base": "2.1.21"}
ROT = (0, 4, 8, 12)
N, E, S, W = 0, 4, 8, 12


def _rotate(point, direction):
    x, y = point
    return {0: (x, y), 4: (-y, x), 8: (-x, -y), 12: (y, -x)}[direction]


def conn(position, direction, kind="normal", flow="input-output", reach=None):
    result = {"positions": [dict(zip("xy", _rotate(position, d))) for d in ROT], "direction": direction,
              "flow_direction": flow, "connection_type": kind, "connection_category": ["default"]}
    if reach:
        result["max_underground_distance"] = reach
    return result


def box(index, connections, production_type=None, flt=None):
    result = {"index": index, "pipe_connections": connections}
    if production_type:
        result["production_type"] = production_type
    if flt:
        result["filter"] = {"name": flt, "kind": "fluid"}
    return result


def entity(name, kind, size, boxes, unknown=()):
    return {"name": name, "type": kind, "placement": {"items": [{"name": name, "count": 1}]},
            "tile_width": size[0], "tile_height": size[1], "fluidboxes": boxes, "unknown_fields": list(unknown)}


# Transcribed from the pinned 2.1.21 base prototypes (entities.lua / recipe.lua).
ENTITIES = [
    entity("pipe", "pipe", (1, 1), [box(1, [conn((0, 0), d) for d in ROT])]),
    entity("pipe-to-ground", "pipe-to-ground", (1, 1),
           [box(1, [conn((0, 0), N), conn((0, 0), S, kind="underground", reach=10)])]),
    entity("pump", "pump", (1, 2), [box(1, [conn((0, -0.5), N, flow="output"), conn((0, 0.5), S, flow="input")])]),
    entity("storage-tank", "storage-tank", (3, 3),
           [box(1, [conn((-1, -1), N), conn((1, 1), E), conn((1, 1), S), conn((-1, -1), W)])]),
    entity("chemical-plant", "assembling-machine", (3, 3), [
        box(1, [conn((-1, -1), N, flow="input")], "input"), box(2, [conn((1, -1), N, flow="input")], "input"),
        box(3, [conn((-1, 1), S, flow="output")], "output"), box(4, [conn((1, 1), S, flow="output")], "output")]),
    entity("oil-refinery", "assembling-machine", (5, 5), [
        box(1, [conn((-1, 2), S, flow="input")], "input"), box(2, [conn((1, 2), S, flow="input")], "input"),
        box(3, [conn((-2, -2), N, flow="output")], "output"), box(4, [conn((0, -2), N, flow="output")], "output"),
        box(5, [conn((2, -2), N, flow="output")], "output")]),
    entity("assembling-machine-2", "assembling-machine", (3, 3), [
        box(1, [conn((0, -1), N, flow="input")], "input"), box(2, [conn((0, 1), S, flow="output")], "output")]),
    entity("medium-electric-pole", "electric-pole", (1, 1), []),
]


def parts(*names):
    return [{"name": n} if isinstance(n, str) else {"name": n[0], "fluidbox_index": n[1]} for n in names]


RECIPES = [
    {"name": "basic-oil-processing", "ingredients": parts(("crude-oil", 2)), "products": parts(("petroleum-gas", 3))},
    {"name": "advanced-oil-processing", "ingredients": parts("water", "crude-oil"),
     "products": parts("heavy-oil", "light-oil", "petroleum-gas")},
    {"name": "heavy-oil-cracking", "ingredients": parts("water", "heavy-oil"), "products": parts("light-oil")},
    {"name": "light-oil-cracking", "ingredients": parts("water", "light-oil"), "products": parts("petroleum-gas")},
    {"name": "sulfuric-acid", "ingredients": parts("water"), "products": parts("sulfuric-acid")},
    {"name": "plastic-bar", "ingredients": parts("petroleum-gas"), "products": []},
    {"name": "rocket-fuel", "ingredients": parts("light-oil"), "products": []},
]


def catalog_bytes(*, recipes=True, entities=None, tick=50):
    report = {"schema_version": "1.1.0", "kind": "runtime_catalog", "ok": True, "tick": tick,
              "provenance": {"export_command": "export_runtime_catalog", "export_tick": tick, "active_mods": MODS,
                             "active_mods_count": 1, "mod_signature": "base@2.1.21",
                             "scope": {"force": "player", "surface": "nauvis", "surface_requested": True,
                                       "technology_scope": "all_force_technologies",
                                       "entity_scope": "selected_prototype_types",
                                       "entity_types": sorted({e["type"] for e in ENTITIES}),
                                       "fluid_scope": "all_loaded_fluids",
                                       "recipe_scope": "unlocks_and_fluid_ports" if recipes else "unlocks_only"},
                             "engine_version": {"available": False, "reason": "x", "base_mod_version": "2.1.21"}},
              "force_state": {}, "technologies": [], "entities": ENTITIES if entities is None else entities,
              "fluids": [], "unknowns": []}
    if recipes:
        report["recipe_fluid_boxes"] = RECIPES
    return json.dumps(report).encode()


def load(**kwargs):
    raw = catalog_bytes(**kwargs)
    return load_fluid_prototype_catalog(raw, expected_sha256=hashlib.sha256(raw).hexdigest(),
                                        surface="nauvis", force="player", active_mods=MODS)


def snapshot(entities=(), *, tick=1000, area=(-200, -200, 200, 260), **extra):
    result = {"version": "fluid-topology-snapshot-v1", "surface": "nauvis", "force": "player", "tick": tick,
              "area": dict(zip(("x_min", "y_min", "x_max_exclusive", "y_max_exclusive"), area)),
              "complete": True, "forces": "all", "entities": list(entities)}
    result.update(extra)
    return result


def built(name, x, y, *, direction="north", fluid=None, unit=7, force="player", boxes=1, recipe=None):
    result = {"name": name, "ghost": False, "force": force, "position": {"x": x, "y": y}, "direction": direction,
              "mirroring": False, "unit_number": unit,
              "fluidboxes": [{"index": i, "readable": True, "segment_fluid": fluid, "filter": None}
                             for i in range(1, boxes + 1)]}
    if recipe:
        result["recipe"] = recipe
    return result


def ghost(name, x, y, direction="north"):
    return {"name": name, "ghost": True, "force": "player", "position": {"x": x, "y": y},
            "direction": direction, "mirroring": False}


def place(name, x, y, direction="north", **extra):
    return {"action_type": "place_ghost", "entity": name, "position": {"x": x, "y": y}, "direction": direction, **extra}


def plan(*actions):
    return {"surface": "nauvis", "force": "player", "phases": [{"name": "p", "actions": list(actions)}]}


def check(the_plan, segments, snap=None, **kwargs):
    kwargs.setdefault("catalog", load())
    kwargs.setdefault("reference_tick", 1010)
    kwargs.setdefault("max_snapshot_age_ticks", 60)
    return validate_fluid_topology(the_plan, declared_segments=segments,
                                   snapshot=snapshot() if snap is None else snap, **kwargs)


def codes(result, key="conflicts"):
    return {entry["code"] for entry in result[key]}


PIPE_ROW = plan(place("pipe", 0.5, 0.5), place("pipe", 1.5, 0.5), place("pipe", 2.5, 0.5))
WATER_ROW = [{"fluid": "water", "tiles": [(0, 0), (1, 0), (2, 0)]}]


def test_fresh_isolated_pipes_are_consistent_but_never_authorize():
    result = check(PIPE_ROW, WATER_ROW)
    assert result["status"] == "consistent", result
    assert result["networks"] == [{"fluids": ["water"], "bases": ["declared_segment"], "planned_entities": 3,
                                   "existing_entities": 0, "attachments": []}]
    assert result["dispatch_authorized"] is False and result["non_claims"]
    with pytest.raises(FluidTopologyError, match="ghost"):
        check(plan({"action_type": "configure_entity", "entity": "pipe", "position": {"x": 0.5, "y": 0.5}}), [])


def test_permitted_attachment_requires_observed_matching_identity():
    permit = {"planned": {"entity": "pipe", "position": {"x": 2.5, "y": 0.5}},
              "existing": {"entity": "pipe", "position": {"x": 3.5, "y": 0.5}, "unit_number": 7}, "fluid": "water"}
    existing = built("pipe", 3.5, 0.5, fluid="water")
    assert codes(check(PIPE_ROW, WATER_ROW, snapshot([existing]))) == {"unpermitted_attachment"}
    ok = check(PIPE_ROW, WATER_ROW, snapshot([existing]), permitted_attachments=[permit])
    assert ok["status"] == "consistent", ok
    assert ok["networks"][0]["attachments"][0]["basis"] == "observed"
    empty = check(PIPE_ROW, WATER_ROW, snapshot([built("pipe", 3.5, 0.5)]), permitted_attachments=[permit])
    assert empty["status"] == "unverified" and codes(empty, "unverified") == {"attachment_identity_unobserved"}
    wrong = check(PIPE_ROW, WATER_ROW, snapshot([built("pipe", 3.5, 0.5, fluid="crude-oil")]),
                  permitted_attachments=[permit])
    assert {"fluid_mixing", "attachment_fluid_mismatch"} <= codes(wrong)
    renumbered = check(PIPE_ROW, WATER_ROW, snapshot([built("pipe", 3.5, 0.5, fluid="water", unit=8)]),
                       permitted_attachments=[permit])
    assert codes(renumbered) == {"unpermitted_attachment", "permitted_attachment_unrealized"}


def test_ghost_foreign_force_and_unreadable_contacts_fail_closed():
    assert codes(check(PIPE_ROW, WATER_ROW, snapshot([ghost("pipe", 3.5, 0.5)]))) == {"foreign_ghost_attachment"}
    other = built("pipe", 3.5, 0.5, fluid="water", force="enemy")
    assert codes(check(PIPE_ROW, WATER_ROW, snapshot([other]))) == {"foreign_force_attachment"}
    unreadable = built("pipe", 3.5, 0.5)
    unreadable["fluidboxes"][0]["readable"] = False
    result = check(PIPE_ROW, WATER_ROW, snapshot([unreadable]))
    assert "unpermitted_attachment" in codes(result)
    assert "network_identity_unknown" in codes(result, "unverified")


def test_non_connecting_neighbours_do_not_attach():
    # A pipe-to-ground whose normal side faces away does not join the row.
    side = built("pipe-to-ground", 3.5, 0.5, direction="east", fluid="crude-oil")
    result = check(PIPE_ROW, WATER_ROW, snapshot([side]))
    assert result["status"] == "consistent", result


def test_overlaps_conflict_only_when_the_plan_overlaps_itself_or_the_world():
    stacked = [built("pipe", 30.5, 0.5, fluid="water", unit=1), ghost("pipe", 30.5, 0.5)]
    assert codes(check(PIPE_ROW, WATER_ROW, snapshot(stacked)), "unverified") == {"existing_overlap_unmodelled"}
    assert "fluid_entity_overlap" in codes(check(PIPE_ROW, WATER_ROW, snapshot([built("pipe-to-ground", 1.5, 0.5)])))
    doubled = plan(*PIPE_ROW["phases"][0]["actions"], place("pipe", 0.5, 0.5))
    assert "fluid_entity_overlap" in codes(check(doubled, WATER_ROW))


def test_snapshot_scope_freshness_and_completeness_are_separate_unknowns():
    assert codes(check(PIPE_ROW, WATER_ROW, snapshot(tick=900)), "unverified") == {"snapshot_stale"}
    assert codes(check(PIPE_ROW, WATER_ROW, snapshot(tick=2000)), "unverified") == {"snapshot_after_reference_tick"}
    assert codes(check(PIPE_ROW, WATER_ROW, reference_tick=None), "unverified") == {"snapshot_freshness_unestablished"}
    assert codes(check(PIPE_ROW, WATER_ROW, snapshot(complete=False)), "unverified") == {"snapshot_incomplete"}
    assert codes(check(PIPE_ROW, WATER_ROW, snapshot(forces="player")), "unverified") == {"snapshot_incomplete"}
    assert codes(check(PIPE_ROW, WATER_ROW, snapshot(surface="vulcanus")), "unverified") == {"snapshot_scope_mismatch"}
    small = check(PIPE_ROW, WATER_ROW, snapshot(area=(-5, -5, 8, 8)))
    assert codes(small, "unverified") == {"snapshot_area_insufficient"}
    assert check(PIPE_ROW, WATER_ROW, snapshot(area=(-12, -12, 15, 13)))["status"] == "consistent"
    absent = validate_fluid_topology(PIPE_ROW, catalog=load(), declared_segments=WATER_ROW, snapshot=None,
                                     reference_tick=1, max_snapshot_age_ticks=1)
    assert codes(absent, "unverified") == {"snapshot_absent"}
    mirrored = built("pipe", 30.5, 0.5)
    mirrored["mirroring"] = True
    assert codes(check(PIPE_ROW, WATER_ROW, snapshot([mirrored])), "unverified") == {"mirroring_unsupported"}


def test_unknown_prototypes_and_incomplete_connection_facts_fail_closed():
    odd = plan(place("pipe", 0.5, 0.5), place("modded-pipe", 1.5, 0.5))
    assert codes(check(odd, WATER_ROW), "unverified") == {"planned_prototype_unknown"}
    broken = copy.deepcopy(ENTITIES)
    broken[0]["fluidboxes"][0]["pipe_connections"][1].pop("positions")
    result = check(PIPE_ROW, WATER_ROW, catalog=load(entities=broken))
    assert "prototype_fluid_facts_unknown" in codes(result, "unverified")
    stray = check(PIPE_ROW, WATER_ROW, snapshot([{**built("pipe", 30.5, 0.5), "name": "modded-tank"}]))
    assert codes(stray, "unverified") == {"existing_prototype_unknown"}
    linked = copy.deepcopy(ENTITIES)
    linked[0]["fluidboxes"][0]["pipe_connections"][0]["connection_type"] = "linked"
    assert "linked_connection_unsupported" in codes(check(PIPE_ROW, WATER_ROW, catalog=load(entities=linked)),
                                                    "unverified")


def test_underground_pairs_follow_intent_and_reject_ambiguity():
    tunnel = plan(place("pipe-to-ground", 0.5, 0.5, "west"), place("pipe-to-ground", 4.5, 0.5, "east"))
    segment = [{"fluid": "water", "tiles": [(0, 0), (4, 0)], "tunnel_endpoints": [((0, 0), (4, 0))]}]
    result = check(tunnel, segment)
    assert result["underground_pairs"] == 1
    assert codes(result, "unverified") == {"underground_tile_blocking_unverified"} and not result["conflicts"]
    assert codes(check(tunnel, [{"fluid": "water", "tiles": [(0, 0), (4, 0)]}])) == {"underground_unintended"}
    between = plan(*tunnel["phases"][0]["actions"], place("pipe-to-ground", 2.5, 0.5, "west"))
    assert "underground_pairing_ambiguous" in codes(check(between, segment))
    # A planned endpoint inserted inside an existing pair would re-pair it.
    existing = [built("pipe-to-ground", 10.5, 0.5, direction="west", fluid="crude-oil", unit=1),
                built("pipe-to-ground", 16.5, 0.5, direction="east", fluid="crude-oil", unit=2)]
    inserted = plan(place("pipe-to-ground", 13.5, 0.5, "east"))
    result = check(inserted, [{"fluid": "water", "tiles": [(13, 0)]}], snapshot(existing))
    assert "existing_underground_repaired" in codes(result)


def test_pump_does_not_separate_fluid_identity():
    pumped = plan(place("pipe", 0.5, -1.5), place("pump", 0.5, 0), place("pipe", 0.5, 1.5))
    segments = [{"fluid": "water", "tiles": [(0, -2)], "separated_by_pump": True},
                {"fluid": "crude-oil", "tiles": [(0, 1)], "separated_by_pump": True}]
    assert codes(check(pumped, segments)) == {"fluid_mixing"}


@pytest.mark.parametrize("recipe", ["basic-oil-processing", "advanced-oil-processing", "heavy-oil-cracking",
                                    "light-oil-cracking", "sulfuric-acid", "plastic-bar", "rocket-fuel"])
def test_recipe_assignment_reproduces_the_planners_live_verified_port_tiles(recipe):
    from planners.fluid_layouts import FLUID_RECIPES, VERIFIED_PIPE_TILES
    catalog = load()
    machine = FLUID_RECIPES[recipe]["machine"]
    facts = catalog.entities[machine]
    assignment = recipe_box_assignment(facts, catalog.recipes[recipe])
    targets = {}
    for fluid_box in facts.fluidboxes:
        for connection in fluid_box.connections:
            vx, vy = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}[connection.direction]
            px, py = connection.positions[0]
            targets.setdefault(assignment.get(fluid_box.index), set()).add((px + vx, py + vy))
    for role in ("inputs", "outputs"):
        for fluid, tile in VERIFIED_PIPE_TILES[recipe][role].items():
            assert tile in targets[fluid], (recipe, fluid)


def test_catalog_loader_binds_digest_scope_and_mods():
    raw = catalog_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    for kwargs in ({"expected_sha256": "0" * 64}, {"surface": "vulcanus"}, {"force": "enemy"},
                   {"active_mods": {"base": "2.0.0"}}):
        arguments = {"expected_sha256": digest, "surface": "nauvis", "force": "player", "active_mods": MODS, **kwargs}
        with pytest.raises(FluidTopologyError):
            load_fluid_prototype_catalog(raw, **arguments)
    duplicated = raw.replace(b'"kind": "runtime_catalog"', b'"kind": "runtime_catalog", "kind": "runtime_catalog"')
    with pytest.raises(FluidTopologyError, match="duplicate"):
        load_fluid_prototype_catalog(duplicated, expected_sha256=hashlib.sha256(duplicated).hexdigest(),
                                     surface="nauvis", force="player", active_mods=MODS)
    assert load(recipes=False).recipes is None
    mislabeled = raw.replace(b'"unlocks_and_fluid_ports"', b'"unlocks_only"')
    with pytest.raises(FluidTopologyError, match="recipe scope"):
        load_fluid_prototype_catalog(mislabeled, expected_sha256=hashlib.sha256(mislabeled).hexdigest(),
                                     surface="nauvis", force="player", active_mods=MODS)


# --- Real planner output -----------------------------------------------------------------------------

HISTORICAL = (Path(__file__).resolve().parents[1]
              / "data/factorio-2.1.21/probes/deterministic/server/script-output/factorio_mod"
              / "recipe_catalogs/recipe_catalog_243688.json")
OIL_RECIPES = ("advanced-oil-processing", "heavy-oil-cracking", "light-oil-cracking")
ORDER = ("heavy-oil", "light-oil", "petroleum-gas", "water")


@pytest.fixture(scope="module")
def oil_block():
    if not HISTORICAL.exists():
        pytest.skip("historical recipe export is unavailable")
    from core.bounded_recipe_balance import optimize_bounded_recipe_balance
    from planners.oil_block_artifact import build_oil_block_artifact
    catalog = json.loads(HISTORICAL.read_text())
    balance = optimize_bounded_recipe_balance(
        catalog, recipes={r: 10 for r in OIL_RECIPES}, sources={"crude-oil": 300, "water": 400},
        demand={"petroleum-gas": 195}, disposals={},
        objective={"recipes": {r: 1 for r in OIL_RECIPES}, "sources": {"crude-oil": -1, "water": -1},
                   "disposals": {}})
    result = build_oil_block_artifact(
        catalog, balance=balance, machine_speeds={r: Fraction(1) for r in OIL_RECIPES},
        rows={r: {"origin": (0, i * 60), "direction": "north", "mirror": None} for i, r in enumerate(OIL_RECIPES)},
        trunk_columns={f: -10 - 6 * i for i, f in enumerate(ORDER)}, route_order=ORDER, max_machines=22,
        item_mapping={"provenance": "synthetic", "tiles": {}, "entities": {n: n for n in (
            "pipe", "pipe-to-ground", "medium-electric-pole", "oil-refinery", "chemical-plant")}})
    return {"plan": {"surface": "nauvis", "force": "player", **result["artifact"]["plan"]},
            "segments": result["segments"], "ports": result["ports"]}


def test_real_oil_block_derives_pure_networks_with_only_tile_blocking_unverified(oil_block):
    result = check(oil_block["plan"], oil_block["segments"])
    assert result["conflicts"] == [], result["conflicts"][:5]
    assert codes(result, "unverified") == {"underground_tile_blocking_unverified"}
    assert result["status"] == "unverified" and result["dispatch_authorized"] is False
    assert result["underground_pairs"] == 92
    machine_networks = [n for n in result["networks"] if "planned_recipe_assignment" in n["bases"]]
    assert {f for n in machine_networks for f in n["fluids"]} == {
        "crude-oil", "water", "heavy-oil", "light-oil", "petroleum-gas"}
    connected = [n for n in result["networks"] if n["planned_entities"] > 1]
    assert sorted(f for n in connected for f in n["fluids"]) == sorted(ORDER + ("crude-oil",))
    idle = [n for n in result["networks"] if n["planned_entities"] == 1]
    assert len(idle) == 12 and all(n["bases"] == ["machine_box_unassigned"] for n in idle)  # unused plant outputs


def test_real_oil_block_without_recipe_ports_is_unverified_not_trusted(oil_block):
    result = check(oil_block["plan"], oil_block["segments"], catalog=load(recipes=False))
    assert result["conflicts"] == []
    assert {"recipe_fluid_boxes_unexported", "network_identity_unknown"} <= codes(result, "unverified")


def test_real_oil_block_rejects_wrong_recipe_and_unpermitted_crude_source(oil_block):
    swapped = copy.deepcopy(oil_block["plan"])
    for phase in swapped["phases"]:
        for action in phase["actions"]:
            if action.get("recipe") == "heavy-oil-cracking":
                action["recipe"] = "light-oil-cracking"
    assert "fluid_mixing" in codes(check(swapped, oil_block["segments"]))
    port = oil_block["ports"]["crude-oil"][0]  # open header end and the tile beyond it
    (ex, ey), (ax, ay) = port["west"], port["attach"]
    source = built("pipe", ax + 0.5, ay + 0.5, fluid="crude-oil", unit=99)
    result = check(oil_block["plan"], oil_block["segments"], snapshot([source]))
    assert "unpermitted_attachment" in codes(result)
    permit = {"planned": {"entity": "pipe", "position": {"x": ex + 0.5, "y": ey + 0.5}},
              "existing": {"entity": "pipe", "position": source["position"], "unit_number": 99}, "fluid": "crude-oil"}
    allowed = check(oil_block["plan"], oil_block["segments"], snapshot([source]), permitted_attachments=[permit])
    assert allowed["conflicts"] == [], allowed["conflicts"]
    attached = [n for n in allowed["networks"] if n["attachments"]]
    assert len(attached) == 1 and attached[0]["fluids"] == ["crude-oil"]
    wrong = check(oil_block["plan"], oil_block["segments"], snapshot([{**source, "fluidboxes": [
        {"index": 1, "readable": True, "segment_fluid": "water", "filter": None}]}]), permitted_attachments=[permit])
    assert {"fluid_mixing", "attachment_fluid_mismatch"} <= codes(wrong)
