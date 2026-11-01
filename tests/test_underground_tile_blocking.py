# Path: tests/test_underground_tile_blocking.py | Purpose: Exercise pinned underground masks, tile scans and span blocking in the topology validator.
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from core.fluid_topology import load_fluid_prototype_catalog
from core.underground_tile_blocking import (
    UndergroundFactsError, load_underground_mask_facts, parse_tile_scan, space_age_2_1_21_mask_facts, tiles_between,
)
from tests.test_fluid_topology import (
    ENTITIES, MODS as BASE_ONLY, box, built, catalog_bytes, check, codes, conn, entity, oil_block, place, plan,
    snapshot,
)
from tests.test_laya_saved_world import _FLUID_SNAPSHOT_SETUP, _SURVEY_LUA_FIXTURE

VANILLA = {"base": "2.1.21", "elevated-rails": "2.1.21", "quality": "2.1.21", "recycler": "2.1.21",
           "space-age": "2.1.21"}
OURS = {"factorio_cursor_rl_agent": "0.1.0", "factorio_training_lab": "0.1.0"}
RUN_MODS = {**VANILLA, **OURS}
LAYERS = {"grass-1": ["ground_tile"], "water": ["item", "player", "resource", "water_tile"],
          "lava": ["doodad", "item", "lava_tile", "player", "rail", "resource", "water_tile"],
          "empty-space": ["empty_space", "ground_tile", "water_tile"]}


def run_catalog(mods=RUN_MODS, entities=None):
    report = json.loads(catalog_bytes(entities=entities))
    report["provenance"]["active_mods"] = mods
    raw = json.dumps(report).encode()
    return load_fluid_prototype_catalog(raw, expected_sha256=hashlib.sha256(raw).hexdigest(),
                                        surface="nauvis", force="player", active_mods=mods)


def mask_facts(mods=RUN_MODS, attested=OURS):
    raw = space_age_2_1_21_mask_facts(mods, attested_mods=attested)
    return load_underground_mask_facts(raw, expected_sha256=hashlib.sha256(raw).hexdigest(), active_mods=mods)


def tile_scan(area=(-10, -10, 20, 10), *, tick=1000, overrides=None, pending=(), fill="grass-1"):
    """Encode a scan the way the Lua collector does: RLE rows of 0-based palette indices."""
    x0, y0, x1, y1 = area
    overrides = overrides or {}
    palette = sorted({fill, *overrides.values()})
    rows = []
    for y in range(y0, y1):
        row = []
        for x in range(x0, x1):
            index = palette.index(overrides.get((x, y), fill))
            if row and row[-2] == index:
                row[-1] += 1
            else:
                row += [index, 1]
        rows.append(row)
    return {"version": "fluid-tile-scan-v1", "tick": tick, "complete": True,
            "area": dict(zip(("x_min", "y_min", "x_max_exclusive", "y_max_exclusive"), area)),
            "palette": palette, "rows": rows, "prototypes": {n: {"layers": LAYERS[n]} for n in palette},
            "pending": [{"x": x, "y": y} for x, y in pending] or {}}


TUNNEL = plan(place("pipe-to-ground", 0.5, 0.5, "west"), place("pipe-to-ground", 4.5, 0.5, "east"))
SEGMENT = [{"fluid": "water", "tiles": [(0, 0), (4, 0)], "tunnel_endpoints": [((0, 0), (4, 0))]}]


def run(the_plan=TUNNEL, segments=SEGMENT, tiles=None, *, entities=(), facts=..., catalog=None, snap=...):
    if snap is ...:
        snap = snapshot(entities, **({} if tiles is None else {"tiles": tiles}))
    return check(the_plan, segments, snap, catalog=catalog or run_catalog(),
                 underground_mask_facts=mask_facts() if facts is ... else facts)


def blocking_reasons(result):
    return [e.get("reasons") for e in result["unverified"] if e["code"] == "underground_tile_blocking_unverified"]


# --- Pinned facts ------------------------------------------------------------------------------------------------

def test_pinned_mask_facts_bind_exact_space_age_mod_set_and_attestation():
    facts = mask_facts()
    (entry,) = facts.entries
    assert (entry.entity, entry.fluidbox_index, entry.direction, entry.position, entry.max_underground_distance) == (
        "pipe-to-ground", 1, 8, (0.0, 0.0), 10)
    assert entry.layers == {"lava_tile", "empty_space"} and facts.attested_mods == OURS
    with pytest.raises(UndergroundFactsError, match="base and space-age"):
        space_age_2_1_21_mask_facts(BASE_ONLY, attested_mods={})  # base-only default mask is not pinned
    with pytest.raises(UndergroundFactsError, match="base and space-age"):
        space_age_2_1_21_mask_facts({**RUN_MODS, "quality": "2.1.20"}, attested_mods=OURS)
    with pytest.raises(UndergroundFactsError, match="attested"):
        space_age_2_1_21_mask_facts({**RUN_MODS, "other": "1.0.0"}, attested_mods=OURS)
    raw = space_age_2_1_21_mask_facts(RUN_MODS, attested_mods=OURS)
    with pytest.raises(UndergroundFactsError, match="SHA-256 mismatch"):
        load_underground_mask_facts(raw, expected_sha256="0" * 64, active_mods=RUN_MODS)
    with pytest.raises(UndergroundFactsError, match="different mod set"):
        load_underground_mask_facts(raw, expected_sha256=hashlib.sha256(raw).hexdigest(), active_mods=VANILLA)
    for forged, reason in ((raw.replace(b"data_stage_source_audit", b"guess"), "source"),
                           (raw.replace(b'"lava_tile"', b'"water_tile"'), "pinned source audit")):
        with pytest.raises(UndergroundFactsError, match=reason):
            load_underground_mask_facts(forged, expected_sha256=hashlib.sha256(forged).hexdigest(),
                                        active_mods=RUN_MODS)


@pytest.mark.slow
def test_pinned_mask_facts_match_the_repository_archive():
    import tarfile
    archive = Path("data/factorio-2.1.21/factorio-headless_linux_2.1.21.tar.xz")
    if not archive.exists():
        pytest.skip("pinned 2.1.21 headless archive is unavailable")
    wanted = {"factorio/data/space-age/base-data-updates.lua", "factorio/data/changelog.txt"}
    found = {}
    with tarfile.open(archive) as tar:  # stream; read text members only, never extract or execute
        for member in tar:
            if member.name in wanted:
                found[member.name] = tar.extractfile(member).read()
                if len(found) == len(wanted):
                    break
    source = found["factorio/data/space-age/base-data-updates.lua"]
    assert hashlib.sha256(source).hexdigest() == (
        "3186fadf83ec2ea17f5935f27aba584fd829a42bb067382f196f0a23b745bc7a")
    text = source.decode()
    assert "local underground_collision_mask = {layers={lava_tile=true, empty_space=true}}" in text
    assert ('data.raw["pipe-to-ground"]["pipe-to-ground"].fluid_box.pipe_connections[2].underground_collision_mask'
            ' = underground_collision_mask') in text
    changelog = found["factorio/data/changelog.txt"].decode()
    assert "Underground belts and pipes-to-ground connections can be blocked by certain tiles." in changelog
    assert ("Underground fluid box connections with incompatible underground_collision_mask are allowed to connect "
            "as long as tiles between do not collide with any of them.") in changelog


def test_runtime_api_does_not_expose_the_underground_mask():
    api = Path("data/factorio-2.1.21/runtime-api-2.1.21.json")
    if not api.exists():
        pytest.skip("pinned runtime API is unavailable")
    concepts = {c["name"]: c for c in json.loads(api.read_text())["concepts"]}
    fields = {p["name"] for p in concepts["PipeConnectionDefinition"]["type"]["parameters"]}
    assert "max_underground_distance" in fields and "underground_collision_mask" not in fields
    assert {p["name"] for p in concepts["TileCollisionMask"]["type"]["parameters"]} == {"layers"}


# --- Tile scan shape ---------------------------------------------------------------------------------------------

def test_tile_scan_parsing_fails_closed_and_tolerates_factorio_empty_tables():
    scan = parse_tile_scan(tile_scan(overrides={(2, 0): "lava"}, pending=[(3, 0)]), snapshot_tick=1000)
    assert scan.names[(2, 0)] == "lava" and scan.names[(-10, -10)] == "grass-1" and scan.names[(19, 9)] == "grass-1"
    assert (20, 0) not in scan.names and scan.pending == {(3, 0)} and "lava_tile" in scan.layers["lava"]
    empty_layers = tile_scan()
    empty_layers["prototypes"]["grass-1"] = {"layers": {}}
    assert parse_tile_scan(empty_layers, snapshot_tick=1000).layers["grass-1"] == frozenset()
    assert parse_tile_scan(None, snapshot_tick=1000) == "tile_scan_absent"
    assert parse_tile_scan({**tile_scan(), "complete": False}, snapshot_tick=1000) == "tile_scan_incomplete"
    assert parse_tile_scan(tile_scan(tick=999), snapshot_tick=1000) == "tile_scan_tick_mismatch"
    short = tile_scan()
    short["rows"][0][-1] -= 1
    assert parse_tile_scan(short, snapshot_tick=1000) == "tile_scan_invalid"
    outside = tile_scan(pending=[(25, 0)])
    assert parse_tile_scan(outside, snapshot_tick=1000) == "tile_scan_invalid"
    unknown_layers = tile_scan()
    del unknown_layers["prototypes"]["grass-1"]
    assert parse_tile_scan(unknown_layers, snapshot_tick=1000) == "tile_scan_invalid"
    assert parse_tile_scan(tile_scan(area=(0, 0, 600, 600)), snapshot_tick=1000) == "tile_scan_invalid"


def test_tiles_between_excludes_endpoints_in_every_direction():
    assert tiles_between((0, 0), (4, 0)) == [(1, 0), (2, 0), (3, 0)]
    assert tiles_between((-1, 5), (-1, 2)) == [(-1, 4), (-1, 3)]
    assert tiles_between((3, 3), (4, 3)) == []
    with pytest.raises(UndergroundFactsError):
        tiles_between((0, 0), (1, 1))


# --- Validator ---------------------------------------------------------------------------------------------------

def test_without_mask_facts_the_v1_unverified_result_is_unchanged():
    result = check(TUNNEL, SEGMENT, snapshot(tiles=tile_scan()))
    assert result["unverified"] == [{"code": "underground_tile_blocking_unverified", "planned_connections": 2}]
    assert "underground_tile_blocking" not in result


def test_clear_terrain_including_water_removes_the_blocking_unknown():
    result = run(tiles=tile_scan(overrides={(1, 0): "water", (2, 0): "water"}))
    assert result["status"] == "consistent", result["unverified"]
    assert result["dispatch_authorized"] is False
    summary = result["underground_tile_blocking"]
    assert summary["status"] == "checked" and summary["spans_checked"] == summary["spans_clear"] == 1
    assert summary["tile_scan"]["tick"] == 1000 and summary["attested_mods"] == OURS
    assert any("source audit" in claim for claim in result["non_claims"])


@pytest.mark.parametrize("tile", ["lava", "empty-space"])
def test_masked_tile_between_planned_endpoints_is_a_conflict_with_exact_coordinates(tile):
    result = run(tiles=tile_scan(overrides={(3, 0): tile}))
    (blocked,) = [c for c in result["conflicts"] if c["code"] == "underground_span_blocked"]
    assert blocked["blocking_tiles"] == [{"tile": [3, 0], "name": tile, "layers": sorted(
        set(LAYERS[tile]) & {"lava_tile", "empty_space"})}]
    assert result["status"] == "rejected" and result["underground_tile_blocking"]["spans_blocked"] == 1


def test_vertical_negative_coordinate_spans_use_map_tile_indices():
    vertical = plan(place("pipe-to-ground", -2.5, -5.5, "north"), place("pipe-to-ground", -2.5, -1.5, "south"))
    segments = [{"fluid": "water", "tiles": [(-3, -6), (-3, -2)], "tunnel_endpoints": [((-3, -6), (-3, -2))]}]
    assert run(vertical, segments, tile_scan(overrides={(-3, -4): "lava"}))["conflicts"][0]["blocking_tiles"][0][
        "tile"] == [-3, -4]
    # Lava beside the span, or past its end, does not block it.
    clear = run(vertical, segments, tile_scan(overrides={(-2, -4): "lava", (-3, -1): "lava"}))
    assert clear["status"] == "consistent", (clear["conflicts"], clear["unverified"])


def test_endpoint_pending_and_out_of_area_tiles_stay_unverified():
    assert blocking_reasons(run(tiles=tile_scan(overrides={(4, 0): "lava"}))) == [["endpoint_tile_masked"]]
    assert blocking_reasons(run(tiles=tile_scan(pending=[(2, 0)]))) == [["span_tile_change_pending"]]
    # A pending change on a blocking tile (e.g. foundation ghost over lava) is not judged as blocked.
    pending_lava = run(tiles=tile_scan(overrides={(2, 0): "lava"}, pending=[(2, 0)]))
    assert not pending_lava["conflicts"] and blocking_reasons(pending_lava) == [["span_tile_change_pending"]]
    assert blocking_reasons(run(tiles=tile_scan(area=(1, -1, 10, 2)))) == [["tile_scan_area_insufficient"]]


def test_missing_or_mismatched_scope_inputs_keep_blocking_unverified():
    assert blocking_reasons(run()) == [["tile_scan_absent"]]
    incomplete = snapshot(tiles=tile_scan(), complete=False)  # an unread snapshot cannot vouch for its tiles
    assert blocking_reasons(run(snap=incomplete)) == [["snapshot_unavailable"]]
    assert blocking_reasons(run(snap=snapshot(tiles=tile_scan(), surface="vulcanus"))) == [["snapshot_unavailable"]]
    stale_scan = snapshot(tiles=tile_scan(tick=900))
    assert blocking_reasons(run(snap=stale_scan)) == [["tile_scan_tick_mismatch"]]
    other_mods = {**VANILLA, "factorio_cursor_rl_agent": "0.2.0", "factorio_training_lab": "0.1.0"}
    result = run(tiles=tile_scan(), catalog=run_catalog(mods=other_mods))
    assert blocking_reasons(result) == [["mask_facts_mod_set_mismatch"]]


def test_unaudited_underground_prototypes_fail_closed():
    longer = [e if e["name"] != "pipe-to-ground" else entity(
        "pipe-to-ground", "pipe-to-ground", (1, 1), [box(1, [conn((0, 0), 0), conn((0, 0), 8, kind="underground",
                                                                                 reach=12)])])
        for e in ENTITIES]
    result = run(tiles=tile_scan(), catalog=run_catalog(entities=longer))
    assert blocking_reasons(result) == [["mask_fact_connection_mismatch"]]
    modded = [*ENTITIES, entity("modded-ptg", "pipe-to-ground", (1, 1),
                                [box(1, [conn((0, 0), 0), conn((0, 0), 8, kind="underground", reach=10)])])]
    tunnel = plan(place("modded-ptg", 0.5, 0.5, "west"), place("modded-ptg", 4.5, 0.5, "east"))
    result = run(tunnel, tiles=tile_scan(), catalog=run_catalog(entities=modded))
    assert blocking_reasons(result) == [["mask_fact_missing"]]


def test_blocked_span_to_an_existing_entity_is_unverified_not_trusted():
    existing = [built("pipe-to-ground", 4.5, 0.5, direction="east", fluid="water", unit=3)]
    lone = plan(place("pipe-to-ground", 0.5, 0.5, "west"))
    result = run(lone, [{"fluid": "water", "tiles": [(0, 0)]}], tile_scan(overrides={(2, 0): "lava"}),
                 entities=existing)
    assert "underground_span_blocked_unmodelled" in codes(result, "unverified")
    assert "underground_span_blocked" not in codes(result)


# --- Real planner output -----------------------------------------------------------------------------------------

def test_real_oil_block_is_judged_on_every_planned_span(oil_block):
    area = (-200, -200, 200, 260)
    clear = run(oil_block["plan"], oil_block["segments"], tile_scan(area=area, overrides={(-150, 0): "lava"}))
    assert clear["conflicts"] == [] and clear["unverified"] == [], clear["unverified"][:3]
    assert clear["status"] == "consistent" and clear["dispatch_authorized"] is False
    assert clear["underground_tile_blocking"]["spans_checked"] == 92 == clear["underground_pairs"]
    a, b = _planned_underground_pair(oil_block["plan"])
    lava = run(oil_block["plan"], oil_block["segments"], tile_scan(area=area, overrides={b: "lava"}))
    assert codes(lava) == {"underground_span_blocked"}
    assert any(t["tile"] == list(b) for c in lava["conflicts"] for t in c["blocking_tiles"])


def _planned_underground_pair(the_plan):
    """Find one horizontal tunnel with a tile strictly between its endpoints."""
    ends = sorted((int(a["position"]["x"] // 1), int(a["position"]["y"] // 1), a.get("direction", "north"))
                  for phase in the_plan["phases"] for a in phase["actions"] if a["entity"] == "pipe-to-ground")
    for x, y, direction in ends:
        if direction == "west":
            partner = min((e for e in ends if e[1] == y and e[0] > x + 1 and e[2] == "east"), default=None)
            if partner:
                return (x, y), (x + 1, y)
    raise AssertionError("no horizontal tunnel found")


# --- Lua collector -----------------------------------------------------------------------------------------------

_TILE_SETUP = """
local lava={['3,1']=true}
surface.is_chunk_generated=function(c) return not (ungenerated and c.x==0 and c.y==0) end
surface.get_tile=function(x,y)
  return {name=lava[x..','..y] and 'lava' or 'grass-1',
    has_tile_ghost=function() return x==4 and y==1 end,
    to_be_deconstructed=function() return x==-2 and y==0 end}
end
prototypes.tile={['grass-1']={collision_mask={layers={ground_tile=true}}},
  lava={collision_mask={layers={lava_tile=true,water_tile=true}}}}
prototypes.entity['assembling-machine-1'].fluidbox_prototypes={}
prototypes.entity['electric-furnace'].fluidbox_prototypes={}
"""

_JSON = """
local function encode(v)
  if type(v)=='table' then
    if next(v)==nil then return '{}' end
    if #v>0 then local out={} for i=1,#v do out[i]=encode(v[i]) end return '['..table.concat(out,',')..']' end
    local keys,out={},{}
    for k in pairs(v) do keys[#keys+1]=k end
    table.sort(keys)
    for _,k in ipairs(keys) do out[#out+1]=string.format('%q',k)..':'..encode(v[k]) end
    return '{'..table.concat(out,',')..'}'
  elseif type(v)=='string' then return string.format('%q',v)
  elseif type(v)=='number' and math.type(v)=='integer' then return tostring(v)
  else return tostring(v) end
end
local s=facts.validations.fluid.fluid_topology_snapshot
print(encode({snapshot_complete=s.complete,snapshot_tick=s.tick,tiles=s.tiles}))
"""


def _collect(prefix=""):
    lua = shutil.which("lua")
    if lua is None:
        pytest.skip("Lua interpreter required for saved-world survey tests")
    fixture = _SURVEY_LUA_FIXTURE.replace("__EXTRA_RECIPES__", "{}").replace("__MALFORMED_RECIPES__", "")
    source = Path("training/laya_saved_world.lua").read_text()
    body = (prefix + fixture + "\n" + _FLUID_SNAPSHOT_SETUP + _TILE_SETUP
            + "\nrequest.candidates[1].inspect_underground_tiles=true"
            + "\nlocal facts=(function()\n" + source + "\nend)()\n" + _JSON)
    result = subprocess.run([lua, "-"], input=body, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.lua
def test_lua_tile_scan_round_trips_into_the_python_parser():
    out = _collect()
    assert out["snapshot_complete"] is True and out["tiles"]["complete"] is True
    scan = parse_tile_scan(out["tiles"], snapshot_tick=out["snapshot_tick"])
    assert not isinstance(scan, str), scan
    assert scan.area == (-16, -16, 19, 18) and len(scan.names) == 35 * 34
    assert scan.names[(3, 1)] == "lava" and scan.names[(2, 1)] == scan.names[(-16, -16)] == "grass-1"
    assert scan.layers["lava"] == {"lava_tile", "water_tile"} and scan.pending == {(4, 1), (-2, 0)}


@pytest.mark.lua
def test_lua_tile_scan_is_incomplete_on_ungenerated_chunks_without_touching_entity_completeness():
    out = _collect("ungenerated=true\n")
    assert out["snapshot_complete"] is True and out["tiles"]["complete"] is False
    assert parse_tile_scan(out["tiles"], snapshot_tick=out["snapshot_tick"]) == "tile_scan_incomplete"


def test_unhashable_palette_is_invalid_instead_of_raising():
    scan = tile_scan()
    scan["palette"] = [[]]
    assert parse_tile_scan(scan, snapshot_tick=1000) == "tile_scan_invalid"
