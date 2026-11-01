# Path: core/underground_tile_blocking.py | Purpose: Pinned underground-connection collision masks and observed tile scans for fail-closed span checks.
"""Facts needed to judge whether terrain blocks an underground fluid connection.

Evidence (repository-pinned Factorio 2.1.21 headless archive, SHA-256
``ff634a91048841483f5c000c070447999cc860d61b552d3f82b612c8a87372d8``, and
``data/factorio-2.1.21/runtime-api-2.1.21.json``):

* ``space-age/base-data-updates.lua:28-34`` sets
  ``pipe-to-ground.fluid_box.pipe_connections[2].underground_collision_mask =
  {layers={lava_tile=true, empty_space=true}}``. No other bundled mod sets the
  field for a fluid connection; ``base`` alone leaves it unset and the engine
  default is not documented in the pinned material.
* changelog 2.0.7: underground pipe connections "can be blocked by certain
  tiles"; changelog 2.0.24: connections with differing masks connect "as long as
  tiles between do not collide with any of them".
* The 2.1.21 *runtime* ``PipeConnectionDefinition`` does not expose
  ``underground_collision_mask``, so a runtime export cannot carry the mask.
  ``LuaTilePrototype.collision_mask`` (a ``TileCollisionMask``) is readable, so
  tile layers are observed facts.

The masks therefore come from a source audit bound to an exact active-mod map;
non-vanilla mods are a caller attestation that they do not change pipe
connections. Tile layers come from an untrusted, ticked, bounded scan. Neither
is authenticated, and this module never claims actual in-game connectivity.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import math
import re

MASK_FACTS_VERSION = "underground-collision-mask-facts-v1"
TILE_SCAN_VERSION = "fluid-tile-scan-v1"
MAX_TILE_SCAN_TILES = 262144
_VANILLA_2_1_21 = {"base", "core", "elevated-rails", "quality", "recycler", "space-age"}
_SOURCE_EVIDENCE = {
    "archive": "data/factorio-2.1.21/factorio-headless_linux_2.1.21.tar.xz",
    "archive_sha256": "ff634a91048841483f5c000c070447999cc860d61b552d3f82b612c8a87372d8",
    "definition": "factorio/data/space-age/base-data-updates.lua:28-34",
    "definition_sha256": "3186fadf83ec2ea17f5935f27aba584fd829a42bb067382f196f0a23b745bc7a",
    "geometry": "factorio/data/base/prototypes/entity/entities.lua pipe-to-ground pipe_connections[2]",
    "semantics": "changelog 2.0.7 (tile blocking) and 2.0.24 (tiles between checked against both masks)",
    "runtime_api": "runtime-api-2.1.21.json PipeConnectionDefinition lacks underground_collision_mask",
}
SPAN_ASSUMPTIONS = (
    "an underground pair is blocked when any tile strictly between its endpoint tiles has a layer in the union "
    "of both connections' underground masks (2.0.24 changelog)",
    "an endpoint tile carrying a masked layer, or any span tile with a pending tile ghost or tile deconstruction, "
    "is not judged",
)
SPAN_NON_CLAIMS = (
    "underground masks are a data-stage source audit plus caller attestation, not runtime-read prototype facts",
    "tile scan layers are an unauthenticated observation; a clear span is not proof of a live connection",
)


class UndergroundFactsError(ValueError):
    """Mask facts are structurally unusable; callers must treat this as a rejection."""


@dataclass(frozen=True)
class UndergroundMask:
    entity: str
    fluidbox_index: int
    direction: int
    position: tuple[float, float]
    max_underground_distance: int
    layers: frozenset[str]


@dataclass(frozen=True)
class UndergroundMaskFacts:
    sha256: str
    source: str
    active_mods: Mapping[str, str]
    attested_mods: Mapping[str, str]
    entries: tuple[UndergroundMask, ...]

    def lookup(self, entity: str, fluidbox_index: int, direction: int, position: tuple[float, float]):
        found = [e for e in self.entries if (e.entity, e.fluidbox_index, e.direction, e.position)
                 == (entity, fluidbox_index, direction, position)]
        return found[0] if len(found) == 1 else None


def _canonical(document) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def _mods(value, label) -> dict[str, str]:
    if (not isinstance(value, Mapping)
            or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in value.items())):
        raise UndergroundFactsError(f"{label} must map mod names to versions")
    return dict(value)


def space_age_2_1_21_mask_facts(active_mods: Mapping[str, str], *, attested_mods: Mapping[str, str]) -> bytes:
    """Return canonical mask-fact bytes for an exact 2.1.21 Space Age mod set.

    ``attested_mods`` must list exactly the active non-vanilla mods; including
    one is the caller's statement that it does not alter pipe connections.
    """
    mods, attested = _mods(active_mods, "active mods"), _mods(attested_mods, "attested mods")
    vanilla = {k: v for k, v in mods.items() if k in _VANILLA_2_1_21}
    if mods.get("base") != "2.1.21" or mods.get("space-age") != "2.1.21" or set(vanilla.values()) != {"2.1.21"}:
        raise UndergroundFactsError("pinned mask audit covers only base and space-age 2.1.21 vanilla mods")
    if attested != {k: v for k, v in mods.items() if k not in _VANILLA_2_1_21}:
        raise UndergroundFactsError("attested mods must equal the active non-vanilla mods")
    return _canonical({
        "version": MASK_FACTS_VERSION, "source": "data_stage_source_audit", "evidence": _SOURCE_EVIDENCE,
        "active_mods": mods, "attested_mods": attested,
        "entries": [{"entity": "pipe-to-ground", "fluidbox_index": 1, "direction": 8, "position": {"x": 0, "y": 0},
                     "max_underground_distance": 10, "layers": ["empty_space", "lava_tile"]}]})


def load_underground_mask_facts(raw: bytes, *, expected_sha256: str,
                                active_mods: Mapping[str, str]) -> UndergroundMaskFacts:
    """Parse caller-pinned mask-fact bytes; the digest binds bytes, not their truth."""
    if not isinstance(raw, bytes):
        raise UndergroundFactsError("mask facts must be raw bytes")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise UndergroundFactsError("expected mask-fact SHA-256 must be pinned in lowercase hex")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise UndergroundFactsError("mask-fact SHA-256 mismatch")
    try:
        doc = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UndergroundFactsError("invalid mask-fact JSON") from error
    if (not isinstance(doc, dict) or doc.get("version") != MASK_FACTS_VERSION
            or doc.get("source") != "data_stage_source_audit" or doc.get("evidence") != _SOURCE_EVIDENCE):
        raise UndergroundFactsError("unsupported mask-fact version, source or evidence")
    mods = _mods(doc.get("active_mods"), "active mods")
    if mods != _mods(active_mods, "expected active mods"):
        raise UndergroundFactsError("mask facts were recorded for a different mod set")
    attested = _mods(doc.get("attested_mods"), "attested mods")
    if raw != space_age_2_1_21_mask_facts(mods, attested_mods=attested):
        # Only the audited fact set is accepted; hand-edited entries are not evidence.
        raise UndergroundFactsError("mask facts differ from the pinned source audit")
    entries = []
    if not isinstance(doc.get("entries"), list):
        raise UndergroundFactsError("mask-fact entries must be a list")
    for raw_entry in doc["entries"]:
        if not isinstance(raw_entry, dict) or not isinstance(raw_entry.get("layers"), list):
            raise UndergroundFactsError("malformed mask-fact entry")
        try:
            position = raw_entry["position"]
            entry = UndergroundMask(raw_entry["entity"], raw_entry["fluidbox_index"], raw_entry["direction"],
                                    (float(position["x"]), float(position["y"])),
                                    raw_entry["max_underground_distance"], frozenset(raw_entry["layers"]))
        except (KeyError, TypeError, ValueError) as error:
            raise UndergroundFactsError("malformed mask-fact entry") from error
        if (not isinstance(entry.entity, str) or type(entry.fluidbox_index) is not int
                or entry.direction not in (0, 4, 8, 12) or type(entry.max_underground_distance) is not int
                or not all(isinstance(layer, str) and layer for layer in entry.layers)):
            raise UndergroundFactsError("malformed mask-fact entry")
        entries.append(entry)
    if not entries:
        raise UndergroundFactsError("mask facts contain no entries")
    return UndergroundMaskFacts(digest, doc["source"], mods, attested, tuple(entries))


@dataclass(frozen=True)
class TileScan:
    tick: int
    area: tuple[int, int, int, int]  # x_min, y_min, x_max_exclusive, y_max_exclusive (map tile indices)
    names: Mapping[tuple[int, int], str]
    layers: Mapping[str, frozenset[str]]
    pending: frozenset[tuple[int, int]]

    def contains(self, tile) -> bool:
        x0, y0, x1, y1 = self.area
        return x0 <= tile[0] < x1 and y0 <= tile[1] < y1


def _list_or_empty_object(value):
    # Factorio serializes an empty Lua table as {}.
    return [] if value == {} else value


def parse_tile_scan(raw, *, snapshot_tick: int) -> TileScan | str:
    """Return a scan or the problem code that keeps tile blocking unverified."""
    if raw is None:
        return "tile_scan_absent"
    if not isinstance(raw, Mapping) or raw.get("version") != TILE_SCAN_VERSION:
        return "tile_scan_invalid"
    if raw.get("complete") is not True:
        return "tile_scan_incomplete"
    area, tick = raw.get("area"), raw.get("tick")
    if (type(tick) is not int or not isinstance(area, Mapping)
            or set(area) != {"x_min", "y_min", "x_max_exclusive", "y_max_exclusive"}
            or any(type(v) is not int for v in area.values())):
        return "tile_scan_invalid"
    if tick != snapshot_tick:
        return "tile_scan_tick_mismatch"
    x0, y0, x1, y1 = area["x_min"], area["y_min"], area["x_max_exclusive"], area["y_max_exclusive"]
    width, height = x1 - x0, y1 - y0
    if width < 1 or height < 1 or width * height > MAX_TILE_SCAN_TILES:
        return "tile_scan_invalid"
    palette, rows, prototypes = raw.get("palette"), _list_or_empty_object(raw.get("rows")), raw.get("prototypes")
    if (not isinstance(palette, list) or not palette
            or any(not isinstance(n, str) or not n for n in palette) or len(set(palette)) != len(palette)
            or not isinstance(prototypes, Mapping) or set(prototypes) != set(palette)
            or not isinstance(rows, list) or len(rows) != height):
        return "tile_scan_invalid"
    layers = {}
    for name in palette:
        entry = prototypes[name]
        found = _list_or_empty_object(entry.get("layers")) if isinstance(entry, Mapping) else None
        if (not isinstance(entry, Mapping) or set(entry) != {"layers"} or not isinstance(found, list)
                or any(not isinstance(layer, str) or not layer for layer in found)):
            return "tile_scan_invalid"
        layers[name] = frozenset(found)
    names = {}
    for dy, row in enumerate(rows):
        # Each row is run-length encoded as [palette_index, count, ...] covering exactly the width.
        if not isinstance(row, list) or len(row) % 2 or any(type(v) is not int for v in row):
            return "tile_scan_invalid"
        x = x0
        for index, count in zip(row[::2], row[1::2]):
            if not 0 <= index < len(palette) or count < 1 or x + count > x1:
                return "tile_scan_invalid"
            for dx in range(count):
                names[(x + dx, y0 + dy)] = palette[index]
            x += count
        if x != x1:
            return "tile_scan_invalid"
    points = _list_or_empty_object(raw.get("pending"))
    if not isinstance(points, list):
        return "tile_scan_invalid"
    pending = set()
    for point in points:
        if (not isinstance(point, Mapping) or set(point) != {"x", "y"} or any(type(v) is not int for v in point.values())
                or not (x0 <= point["x"] < x1 and y0 <= point["y"] < y1)):
            return "tile_scan_invalid"
        pending.add((point["x"], point["y"]))
    return TileScan(tick, (x0, y0, x1, y1), names, layers, frozenset(pending))


def tiles_between(a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]]:
    """Tiles strictly between two endpoint tiles on one row or column."""
    if a[0] != b[0] and a[1] != b[1]:
        raise UndergroundFactsError("underground endpoints are not collinear")
    steps = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
    sx, sy = (int(math.copysign(1, b[0] - a[0])) if b[0] != a[0] else 0,
              int(math.copysign(1, b[1] - a[1])) if b[1] != a[1] else 0)
    return [(a[0] + sx * i, a[1] + sy * i) for i in range(1, steps)]


def judge_span(scan: TileScan, a: tuple[int, int], b: tuple[int, int], mask: frozenset[str]) -> dict:
    """Return ``{"verdict": "clear"|"blocked"|"unknown", ...}`` for one underground span."""
    between = tiles_between(a, b)
    if not all(scan.contains(t) for t in (a, b, *between)):
        return {"verdict": "unknown", "reasons": ["tile_scan_area_insufficient"]}
    blocking, reasons = [], set()
    for tile in between:
        hit = scan.layers[scan.names[tile]] & mask
        if hit and tile not in scan.pending:
            blocking.append({"tile": list(tile), "name": scan.names[tile], "layers": sorted(hit)})
        elif tile in scan.pending:
            reasons.add("span_tile_change_pending")
    for tile in (a, b):
        if tile in scan.pending:
            reasons.add("span_tile_change_pending")
        if scan.layers[scan.names[tile]] & mask:
            reasons.add("endpoint_tile_masked")
    if blocking:
        return {"verdict": "blocked", "blocking_tiles": blocking}
    if reasons:
        return {"verdict": "unknown", "reasons": sorted(reasons)}
    return {"verdict": "clear"}
