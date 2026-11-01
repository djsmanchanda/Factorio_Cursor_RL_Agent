# Path: core/fluid_topology.py | Purpose: Fail-closed fluid topology and attachment check from pinned prototype facts and an observed snapshot.
"""Derive fluid connections for planned ghosts and observed entities, then judge purity and attachment.

Inputs have different evidence levels and stay separated in the result:

* prototype facts come from caller-pinned runtime-catalog bytes (digest binds
  bytes, not origin or freshness);
* planned links (declared segment fluids, intended underground pairs and
  permitted attachments) are caller intent, never evidence;
* the world snapshot is an untrusted runtime observation with a tick, scope
  and completeness flag; only its built-entity segment fluids and filters
  count as observed identity.

The status is ``consistent`` only when no conflict and no unverified fact
remains. Even then the result is diagnostic: it does not authenticate the
snapshot, establish ownership, prove construction or authorize dispatch.
Everything not modelled (mirroring, linked connections, unexported
prototypes, malformed facts) fails closed as ``unverified``; concrete
contradictions are ``conflicts``. Terrain blocking of underground spans is
judged only from optional pinned mask facts plus the snapshot's tile scan
(``core.underground_tile_blocking``); otherwise it stays unverified. Blocked
spans are still joined for purity, which only over-approximates mixing.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
import math
import re

from core.underground_tile_blocking import (
    SPAN_ASSUMPTIONS, SPAN_NON_CLAIMS, UndergroundMaskFacts, judge_span, parse_tile_scan,
)

_DIRECTION_NAMES = {"north": 0, "east": 4, "south": 8, "west": 12}
_VECTORS = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
_CRAFTING_TYPES = frozenset({"assembling-machine", "furnace", "rocket-silo"})
_SNAPSHOT_VERSION = "fluid-topology-snapshot-v1"
_MAX_ACTIONS = 20000
_MAX_SNAPSHOT_ENTITIES = 20000
_MODEL_ASSUMPTIONS = (
    "prototype connection position i applies to entity direction north/east/south/west and is checked against rotation",
    "normal connections link when each targets the other's connection tile with opposite direction and a shared category",
    "an underground connection pairs with the nearest opposite-facing underground connection in reach; "
    "any other underground connection before it makes pairing ambiguous",
    "recipe fluids use explicit fluidbox_index within input/output boxes, otherwise the next free box in declared order",
    "fluidboxes joined by any connection are one identity network, regardless of flow direction or pumps",
)
NON_CLAIMS = (
    "snapshot, catalog and intent are not authenticated; hash binding is not live evidence",
    "underground tile blocking, mirroring, linked connections and unexported prototypes are not modelled",
    "a consistent result is a diagnostic, not ownership, funding, construction or dispatch authority",
)


class FluidTopologyError(ValueError):
    """Inputs are structurally unusable; callers must treat this as a rejection."""


@dataclass(frozen=True)
class PipeConnectionFact:
    positions: tuple[tuple[float, float], ...]
    direction: int
    connection_type: str
    categories: frozenset[str]
    flow: str
    max_underground_distance: int | None


@dataclass(frozen=True)
class FluidboxFact:
    index: int
    production_type: str | None
    filter: str | None
    connections: tuple[PipeConnectionFact, ...]


@dataclass(frozen=True)
class EntityFluidFacts:
    name: str
    type: str
    tile_width: int | None
    tile_height: int | None
    fluidboxes: tuple[FluidboxFact, ...]
    unknown: tuple[str, ...]


@dataclass(frozen=True)
class RecipeFluidParts:
    ingredients: tuple[tuple[str, int | None], ...]
    products: tuple[tuple[str, int | None], ...]


@dataclass(frozen=True)
class FluidPrototypeCatalog:
    sha256: str
    tick: int
    surface: str
    force: str
    entity_types: frozenset[str]
    entities: Mapping[str, EntityFluidFacts]
    recipes: Mapping[str, RecipeFluidParts] | None
    active_mods: Mapping[str, str] | None = None  # the pinned mod map the provenance matched


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FluidTopologyError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise FluidTopologyError(f"invalid JSON number: {value}")


def _finite(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _read_connection(raw) -> tuple[PipeConnectionFact | None, str | None]:
    if not isinstance(raw, dict):
        return None, "pipe connection is not an object"
    positions = raw.get("positions")
    if (not isinstance(positions, list) or len(positions) != 4
            or any(not isinstance(p, dict) or set(p) != {"x", "y"} or not all(_finite(v) for v in p.values())
                   for p in positions)):
        return None, "pipe connection positions unavailable"
    direction, kind, flow = raw.get("direction"), raw.get("connection_type"), raw.get("flow_direction")
    categories = raw.get("connection_category")
    if type(direction) is not int or direction not in _VECTORS:
        return None, "pipe connection direction is not cardinal"
    if kind not in {"normal", "underground", "linked"}:
        return None, "pipe connection type unavailable"
    if flow not in {"input", "output", "input-output"}:
        return None, "pipe connection flow direction unavailable"
    if (not isinstance(categories, list) or not categories
            or any(not isinstance(c, str) or not c for c in categories)):
        return None, "pipe connection category unavailable"
    reach = raw.get("max_underground_distance")
    if kind == "underground" and (type(reach) is not int or reach < 1):
        return None, "underground reach unavailable"
    points = tuple((float(p["x"]), float(p["y"])) for p in positions)
    for turn, point in enumerate(points):
        if _rotate(points[0], turn * 4) != point:
            return None, "pipe connection positions are not rotations of the north position"
    return PipeConnectionFact(points, direction, kind, frozenset(categories), flow,
                              reach if kind == "underground" else None), None


def _read_entity(raw) -> EntityFluidFacts:
    name, kind = raw["name"], raw["type"]
    unknown = [field for field in raw.get("unknown_fields", ())
               if isinstance(field, str) and ("fluidbox entry" in field or "pipe connection" in field
                                              or field.startswith("tile_"))]
    width, height = raw.get("tile_width"), raw.get("tile_height")
    if type(width) is not int or type(height) is not int or width < 1 or height < 1:
        unknown.append("tile size unavailable")
        width = height = None
    boxes = []
    raw_boxes = raw.get("fluidboxes")
    if not isinstance(raw_boxes, list):
        unknown.append("fluidboxes unavailable")
        raw_boxes = []
    for position, box in enumerate(raw_boxes, start=1):
        if not isinstance(box, dict) or box.get("index") != position:
            unknown.append("fluidbox index unavailable or out of order")
            continue
        connections = []
        for connection in box.get("pipe_connections", ()):
            fact, reason = _read_connection(connection)
            if reason:
                unknown.append(reason)
            else:
                connections.append(fact)
        flt = box.get("filter")
        boxes.append(FluidboxFact(position, box.get("production_type"),
                                  flt["name"] if isinstance(flt, dict) and isinstance(flt.get("name"), str) else None,
                                  tuple(connections)))
    return EntityFluidFacts(name, kind, width, height, tuple(boxes), tuple(sorted(set(unknown))))


def _read_parts(parts) -> tuple[tuple[str, int | None], ...]:
    if not isinstance(parts, list):
        raise FluidTopologyError("recipe fluid parts must be a list")
    result = []
    for part in parts:
        if (not isinstance(part, dict) or not isinstance(part.get("name"), str) or not part["name"]
                or set(part) - {"name", "fluidbox_index"}):
            raise FluidTopologyError("recipe fluid part is malformed")
        index = part.get("fluidbox_index")
        if index is not None and (type(index) is not int or index < 1):
            raise FluidTopologyError("recipe fluidbox_index must be a positive integer")
        result.append((part["name"], index))
    return tuple(result)


def load_fluid_prototype_catalog(catalog_bytes: bytes, *, expected_sha256: str, surface: str, force: str,
                                 active_mods: Mapping[str, str]) -> FluidPrototypeCatalog:
    """Parse fluid facts from caller-pinned runtime-catalog bytes.

    The digest, scope and mod set must match explicit expectations. That binds
    bytes to a declaration, not to a live server, save or tick. Older captures
    without ``recipe_fluid_boxes`` leave recipe-to-port assignment unknown.
    """
    if not isinstance(catalog_bytes, bytes):
        raise FluidTopologyError("catalog must be raw bytes")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise FluidTopologyError("expected catalog SHA-256 must be pinned in lowercase hex")
    digest = hashlib.sha256(catalog_bytes).hexdigest()
    if digest != expected_sha256:
        raise FluidTopologyError("catalog SHA-256 mismatch")
    if not all(isinstance(v, str) and v for v in (surface, force)):
        raise FluidTopologyError("explicit surface and force are required")
    if (not isinstance(active_mods, Mapping) or not active_mods
            or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in active_mods.items())):
        raise FluidTopologyError("explicit active mods are required")
    try:
        report = json.loads(catalog_bytes, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FluidTopologyError("invalid catalog JSON") from error
    if (not isinstance(report, dict) or report.get("kind") != "runtime_catalog"
            or report.get("schema_version") != "1.1.0" or report.get("ok") is not True):
        raise FluidTopologyError("unsupported or unsuccessful runtime catalog")
    provenance = report.get("provenance")
    scope = provenance.get("scope") if isinstance(provenance, dict) else None
    tick = report.get("tick")
    if (type(tick) is not int or tick < 0 or not isinstance(scope, dict)
            or provenance.get("export_tick") != tick or provenance.get("active_mods") != dict(active_mods)
            or scope.get("surface_requested") is not True or scope.get("surface") != surface
            or scope.get("force") != force or scope.get("entity_scope") != "selected_prototype_types"):
        raise FluidTopologyError("catalog provenance does not match expectations")
    types = scope.get("entity_types")
    if not isinstance(types, list) or not types or any(not isinstance(t, str) or not t for t in types):
        raise FluidTopologyError("invalid catalog entity scope")
    entities = {}
    for raw in report.get("entities", ()):
        if (not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw["name"]
                or raw["name"] in entities or raw.get("type") not in types):
            raise FluidTopologyError("duplicate or out-of-scope catalog entity")
        entities[raw["name"]] = _read_entity(raw)
    recipes = None
    if scope.get("recipe_scope") != ("unlocks_and_fluid_ports" if "recipe_fluid_boxes" in report else "unlocks_only"):
        raise FluidTopologyError("catalog recipe scope does not match its recipe fluid section")
    if "recipe_fluid_boxes" in report:
        recipes = {}
        for raw in report["recipe_fluid_boxes"]:
            if (not isinstance(raw, dict) or set(raw) != {"name", "ingredients", "products"}
                    or not isinstance(raw["name"], str) or not raw["name"] or raw["name"] in recipes):
                raise FluidTopologyError("malformed recipe fluid boxes")
            recipes[raw["name"]] = RecipeFluidParts(_read_parts(raw["ingredients"]), _read_parts(raw["products"]))
    return FluidPrototypeCatalog(digest, tick, surface, force, frozenset(types), entities, recipes, dict(active_mods))


def _rotate(point, direction):
    x, y = point
    return {0: (x, y), 4: (-y, x), 8: (-x, -y), 12: (y, -x)}[direction]


def recipe_box_assignment(facts: EntityFluidFacts, parts: RecipeFluidParts) -> dict[int, str] | None:
    """Map fluidbox index to recipe fluid, or None when the model cannot decide."""
    result = {}
    for role, fluids in (("input", parts.ingredients), ("output", parts.products)):
        boxes = [box.index for box in facts.fluidboxes if box.production_type == role]
        if any(box.production_type not in {"input", "output", "none", None} for box in facts.fluidboxes):
            return None
        taken = set()
        for fluid, index in fluids:
            if index is not None:
                if index > len(boxes) or index in taken:
                    return None
                taken.add(index)
                result[boxes[index - 1]] = fluid
        free = [i for i in range(1, len(boxes) + 1) if i not in taken]
        unindexed = [fluid for fluid, index in fluids if index is None]
        if len(unindexed) > len(free):
            return None
        for fluid, slot in zip(unindexed, free):
            result[boxes[slot - 1]] = fluid
    return result


@dataclass
class _Instance:
    origin: str  # "planned" | "existing"
    key: int
    name: str
    center: tuple[float, float]
    direction: int
    facts: EntityFluidFacts | None
    ghost: bool = False
    force: str | None = None
    unit_number: int | None = None
    recipe: str | None = None
    observed: Mapping[int, Mapping] | None = None

    def label(self):
        base = {"origin": self.origin, "entity": self.name, "position": {"x": self.center[0], "y": self.center[1]}}
        if self.origin == "existing":
            base.update({"ghost": self.ghost, "unit_number": self.unit_number})
        return base

    def footprint(self):
        width, height = self.facts.tile_width, self.facts.tile_height
        if self.direction in (4, 12):
            width, height = height, width
        left, top = self.center[0] - width / 2, self.center[1] - height / 2
        if left != math.floor(left) or top != math.floor(top):
            return None
        return {(int(left) + dx, int(top) + dy) for dx in range(width) for dy in range(height)}


def _tile(point) -> tuple[int, int]:
    return (math.floor(point[0]), math.floor(point[1]))


def _position(raw) -> tuple[float, float] | None:
    if not isinstance(raw, Mapping) or set(raw) != {"x", "y"} or not all(_finite(v) for v in raw.values()):
        return None
    return (float(raw["x"]), float(raw["y"]))


class _Union:
    def __init__(self):
        self.parent = {}

    def find(self, node):
        self.parent.setdefault(node, node)
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def join(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def _snapshot_problem(snapshot, catalog) -> str | None:
    if not isinstance(snapshot, Mapping) or snapshot.get("version") != _SNAPSHOT_VERSION:
        return "snapshot_invalid"
    area = snapshot.get("area")
    if (snapshot.get("surface") != catalog.surface or snapshot.get("force") != catalog.force):
        return "snapshot_scope_mismatch"
    if (type(snapshot.get("tick")) is not int or snapshot["tick"] < 0 or not isinstance(area, Mapping)
            or set(area) != {"x_min", "y_min", "x_max_exclusive", "y_max_exclusive"}
            or any(type(v) is not int for v in area.values())
            or area["x_min"] >= area["x_max_exclusive"] or area["y_min"] >= area["y_max_exclusive"]
            or not isinstance(snapshot.get("entities"), list)
            or len(snapshot["entities"]) > _MAX_SNAPSHOT_ENTITIES):
        return "snapshot_invalid"
    if snapshot.get("complete") is not True or snapshot.get("forces") != "all":
        return "snapshot_incomplete"
    return None


def validate_fluid_topology(plan: Mapping, *, catalog: FluidPrototypeCatalog, declared_segments: Iterable[Mapping],
                            snapshot: Mapping | None, reference_tick: int | None, max_snapshot_age_ticks: int,
                            permitted_attachments: Iterable[Mapping] = (),
                            underground_mask_facts: UndergroundMaskFacts | None = None) -> dict:
    """Judge purity, pairing and attachment for one ghost plan against one snapshot.

    ``declared_segments`` uses the planners' shape (``fluid``, ``tiles``,
    optional ``tunnel_endpoints``); ``separated_by_pump`` is ignored because the
    pump is modelled from prototypes instead. ``permitted_attachments`` are
    explicit intents of the form ``{"planned": {"entity", "position"},
    "existing": {"entity", "position", "unit_number"}, "fluid"}``.
    ``reference_tick`` must be a separately observed tick of the same runtime.

    Without ``underground_mask_facts`` planned underground spans stay
    ``underground_tile_blocking_unverified`` (the v1 behaviour). With them, the
    snapshot's ``tiles`` scan is judged span by span; see
    ``core.underground_tile_blocking`` for the evidence and its limits.
    """
    if not isinstance(catalog, FluidPrototypeCatalog):
        raise FluidTopologyError("a loaded fluid prototype catalog is required")
    if type(max_snapshot_age_ticks) is not int or max_snapshot_age_ticks < 0:
        raise FluidTopologyError("max_snapshot_age_ticks must be a nonnegative integer")
    conflicts, unverified = [], []

    def conflict(code, **detail):
        conflicts.append({"code": code, **detail})

    def unknown(code, **detail):
        unverified.append({"code": code, **detail})

    if not isinstance(plan, Mapping) or not isinstance(plan.get("phases"), list):
        raise FluidTopologyError("plan must contain phases")
    if plan.get("surface", catalog.surface) != catalog.surface or plan.get("force", catalog.force) != catalog.force:
        conflict("plan_scope_mismatch")
    actions = [a for phase in plan["phases"] for a in (phase.get("actions") or [])]
    if len(actions) > _MAX_ACTIONS:
        raise FluidTopologyError("plan exceeds the topology action bound")

    instances: list[_Instance] = []
    for action in actions:
        if not isinstance(action, Mapping) or action.get("action_type") not in {"place_ghost", "place_ghost_no_reconcile"}:
            raise FluidTopologyError("topology validation accepts only entity ghost actions")
        if {"mirror", "mirroring"} & set(action):
            unknown("mirroring_unsupported", entity=action.get("entity"))
            continue
        name, center = action.get("entity"), _position(action.get("position"))
        direction = _DIRECTION_NAMES.get(action.get("direction", "north"))
        if not isinstance(name, str) or center is None or direction is None:
            raise FluidTopologyError("planned action has malformed entity, position or direction")
        facts = catalog.entities.get(name)
        if facts is None:
            unknown("planned_prototype_unknown", entity=name)
            continue
        if not facts.fluidboxes and not facts.unknown:
            continue
        instances.append(_Instance("planned", len(instances), name, center, direction, facts,
                                   recipe=action.get("recipe")))

    # Snapshot: scope, completeness and freshness are reported separately.
    snapshot_info = {"status": "absent"}
    problem = "snapshot_absent" if snapshot is None else _snapshot_problem(snapshot, catalog)
    if problem:
        unknown(problem)
        snapshot_info = {"status": problem}
    else:
        snapshot_info = {"status": "read", "tick": snapshot["tick"], "area": dict(snapshot["area"])}
        if type(reference_tick) is not int:
            unknown("snapshot_freshness_unestablished")
        elif snapshot["tick"] > reference_tick:
            unknown("snapshot_after_reference_tick", snapshot_tick=snapshot["tick"], reference_tick=reference_tick)
        else:
            age = reference_tick - snapshot["tick"]
            snapshot_info["age_ticks"] = age
            if age > max_snapshot_age_ticks:
                unknown("snapshot_stale", age_ticks=age, max_age_ticks=max_snapshot_age_ticks)
        for raw in snapshot["entities"]:
            parsed = _existing_instance(raw, len(instances), catalog)
            if isinstance(parsed, str):
                unknown(parsed, entity=raw.get("name") if isinstance(raw, Mapping) else None)
                continue
            if parsed.facts is None:
                unknown("existing_prototype_unknown", entity=parsed.name)
            instances.append(parsed)

    # Occupancy and connection geometry.
    occupancy: dict[tuple[int, int], list[_Instance]] = {}
    reported_unknown: set[str] = set()
    for inst in instances:
        if inst.facts is None:
            occupancy.setdefault(_tile(inst.center), []).append(inst)
            continue
        if inst.facts.unknown and inst.name not in reported_unknown:
            reported_unknown.add(inst.name)
            unknown("prototype_fluid_facts_unknown", entity=inst.name, reasons=list(inst.facts.unknown))
        tiles = inst.footprint() if inst.facts.tile_width else None
        if tiles is None:
            unknown("footprint_unknown", **inst.label())
            tiles = {_tile(inst.center)}
        for tile in tiles:
            occupancy.setdefault(tile, []).append(inst)
    for tile, owners in occupancy.items():
        if len(owners) > 1:
            planned = [o for o in owners if o.origin == "planned"]
            existing = [o for o in owners if o.origin == "existing"]
            if len(planned) > 1:
                conflict("fluid_entity_overlap", tile=list(tile))
            elif len(existing) > 1:
                unknown("existing_overlap_unmodelled", tile=list(tile))
            elif any(e.name == p.name and e.center == p.center and e.direction == p.direction
                     for p in planned for e in existing):
                unknown("planned_entity_preexisting", tile=list(tile))
            else:
                conflict("fluid_entity_overlap", tile=list(tile))

    connections = []  # (inst, box_index, tile, world_direction, fact)
    for inst in instances:
        if inst.facts is None:
            continue
        tiles = inst.footprint() or {_tile(inst.center)}
        for box in inst.facts.fluidboxes:
            for fact in box.connections:
                if fact.connection_type == "linked":
                    unknown("linked_connection_unsupported", **inst.label())
                    continue
                offset = fact.positions[inst.direction // 4]
                point = (inst.center[0] + offset[0], inst.center[1] + offset[1])
                tile = _tile(point)
                if point[0] == math.floor(point[0]) or point[1] == math.floor(point[1]) or tile not in tiles:
                    unknown("connection_outside_footprint", **inst.label())
                    continue
                connections.append((inst, box.index, tile, (fact.direction + inst.direction) % 16, fact))

    normal_at: dict[tuple[int, int], list] = {}
    underground_at: dict[tuple[int, int], list] = {}
    for record in connections:
        (underground_at if record[4].connection_type == "underground" else normal_at).setdefault(
            record[2], []).append(record)

    union = _Union()
    for inst in instances:
        if inst.facts is not None:
            for box in inst.facts.fluidboxes:
                union.find((inst.key, box.index))
    edges = []
    for record in connections:
        inst, box, tile, direction, fact = record
        if fact.connection_type != "normal":
            continue
        vx, vy = _VECTORS[direction]
        target = (tile[0] + vx, tile[1] + vy)
        for other in occupancy.get(target, ()):
            if other.facts is None:
                unknown("adjacent_prototype_unknown", **inst.label())
        for other in normal_at.get(target, ()):
            o_inst, o_box, _, o_direction, o_fact = other
            if o_direction != (direction + 8) % 16 or not fact.categories & o_fact.categories:
                continue
            if (inst.key, box) < (o_inst.key, o_box):
                edges.append(("normal", record, other))

    pairs, ambiguous = _underground_pairs(connections, underground_at, occupancy)
    for record, other in pairs:
        edges.append(("underground", record, other))
    for record in ambiguous:
        inst = record[0]
        (conflict if inst.origin == "planned" else unknown)("underground_pairing_ambiguous", **inst.label())
    planned_underground = [r for r in connections if r[4].connection_type == "underground" and r[0].origin == "planned"]
    blocking = None
    if underground_mask_facts is not None:
        blocking = _check_underground_tiles(pairs, planned_underground, catalog, underground_mask_facts,
                                            snapshot if snapshot_info["status"] == "read" else None, conflict, unknown)
    elif planned_underground:
        unknown("underground_tile_blocking_unverified", planned_connections=len(planned_underground))

    # Existing pairings must survive the planned additions unchanged.
    existing_only = [r for r in connections if r[0].origin == "existing"]
    existing_index: dict[tuple[int, int], list] = {}
    for r in existing_only:
        if r[4].connection_type == "underground":
            existing_index.setdefault(r[2], []).append(r)
    before, _ = _underground_pairs(existing_only, existing_index,
                                   {t: [o for o in owners if o.origin == "existing"] for t, owners in occupancy.items()})

    def pair_keys(found):
        return {frozenset(((a[0].key, a[1]), (b[0].key, b[1]))) for a, b in found}
    after_existing = {key for key in pair_keys(pairs)
                      if all(instances[k].origin == "existing" for k, _ in key)}
    for key in pair_keys(before) - after_existing:
        conflict("existing_underground_repaired", endpoints=[instances[k].label() for k, _ in sorted(key)])

    for kind, a, b in edges:
        if (kind == "normal" and {a[4].flow, b[4].flow} in ({"input"}, {"output"})
                and "planned" in {a[0].origin, b[0].origin}):
            # Conservatively joined for purity; never counted as a realized intended link.
            unknown("connection_flow_incompatible", a=a[0].label(), b=b[0].label())
        union.join((a[0].key, a[1]), (b[0].key, b[1]))

    _check_intent(instances, connections, pairs, declared_segments, conflict, unknown, union)
    networks = _judge_networks(instances, edges, union, catalog, declared_segments, permitted_attachments,
                               conflict, unknown)
    _check_scope(instances, catalog, snapshot_info, unknown)

    status = "rejected" if conflicts else "unverified" if unverified else "consistent"
    result = {"version": "fluid-topology-check-v1", "status": status, "conflicts": conflicts,
              "unverified": unverified, "networks": networks,
              "underground_pairs": len(pairs), "snapshot": snapshot_info,
              "catalog": {"sha256": catalog.sha256, "tick": catalog.tick,
                          "recipe_fluid_boxes": catalog.recipes is not None},
              "assumptions": list(_MODEL_ASSUMPTIONS), "non_claims": list(NON_CLAIMS),
              "dispatch_authorized": False}
    if blocking is not None:
        result["underground_tile_blocking"] = blocking
        result["assumptions"] += list(SPAN_ASSUMPTIONS)
        result["non_claims"] = [c for c in NON_CLAIMS if not c.startswith("underground tile blocking")]
        result["non_claims"] += ["mirroring, linked connections and unexported prototypes are not modelled",
                                 *SPAN_NON_CLAIMS]
    return result


def _existing_instance(raw, key, catalog) -> _Instance | str:
    if not isinstance(raw, Mapping):
        return "snapshot_entity_invalid"
    name, center = raw.get("name"), _position(raw.get("position"))
    direction = _DIRECTION_NAMES.get(raw.get("direction"))
    ghost, force = raw.get("ghost"), raw.get("force")
    if (not isinstance(name, str) or not name or center is None or direction is None
            or not isinstance(ghost, bool) or not isinstance(force, str) or not force):
        return "snapshot_entity_invalid"
    if raw.get("mirroring") is not False:
        return "mirroring_unsupported"
    unit = raw.get("unit_number")
    if not ghost and (type(unit) is not int or unit < 1):
        return "snapshot_entity_invalid"
    recipe = raw.get("recipe")
    if recipe is not None and (not isinstance(recipe, str) or not recipe):
        return "snapshot_entity_invalid"
    observed = None
    if not ghost:
        boxes = raw.get("fluidboxes")
        if not isinstance(boxes, list):
            return "snapshot_entity_invalid"
        observed = {}
        for box in boxes:
            if (not isinstance(box, Mapping) or type(box.get("index")) is not int or box["index"] in observed
                    or not isinstance(box.get("readable"), bool)
                    or any(box.get(k) is not None and (not isinstance(box[k], str) or not box[k])
                           for k in ("segment_fluid", "filter"))):
                return "snapshot_entity_invalid"
            observed[box["index"]] = box
    return _Instance("existing", key, name, center, direction, catalog.entities.get(name), ghost=ghost,
                     force=force, unit_number=unit if not ghost else None, recipe=recipe, observed=observed)


def _underground_pairs(connections, underground_at, occupancy):
    """Return mutual nearest pairs and connections whose pairing is ambiguous."""
    nearest, ambiguous = {}, []
    for record in connections:
        inst, box, tile, direction, fact = record
        if fact.connection_type != "underground":
            continue
        vx, vy = _VECTORS[direction]
        found, blocked = None, False
        for distance in range(1, fact.max_underground_distance + 1):
            here = (tile[0] + vx * distance, tile[1] + vy * distance)
            if any(o.facts is None for o in occupancy.get(here, ())):
                blocked = True
                break
            candidates = [o for o in underground_at.get(here, ()) if o[0] is not inst]
            match = [o for o in candidates if o[3] == (direction + 8) % 16 and fact.categories & o[4].categories]
            if match:
                found = match[0] if len(match) == 1 else None
                blocked = len(match) != 1 or len(candidates) != 1
                break
            if candidates:
                blocked = True
                break
        nearest[id(record)] = (record, found, blocked)
    pairs = []
    for record, found, blocked in nearest.values():
        if blocked:
            ambiguous.append(record)
            continue
        if found is None:
            continue
        back = nearest.get(id(found))
        if back is None or back[1] is not record or back[2]:
            ambiguous.append(record)
        elif (record[0].key, record[1]) < (found[0].key, found[1]):
            pairs.append((record, found))
    return pairs, ambiguous


def _check_intent(instances, connections, pairs, declared_segments, conflict, unknown, union):
    """Compare intended links with derived planned connectivity."""
    by_tile = {}
    for inst in instances:
        if inst.origin == "planned" and inst.facts and inst.facts.tile_width == 1 == inst.facts.tile_height:
            by_tile.setdefault(_tile(inst.center), []).append(inst)
    derived = {}
    for a, b in pairs:
        if a[0].origin == b[0].origin == "planned":
            derived[_tile(a[0].center)] = _tile(b[0].center)
            derived[_tile(b[0].center)] = _tile(a[0].center)
    intended = {}
    for segment in declared_segments:
        tiles = [tuple(t) for t in segment.get("tiles", ())]
        for first, second in segment.get("tunnel_endpoints", ()):
            first, second = tuple(first), tuple(second)
            intended[first], intended[second] = second, first
        roots = {union.find((inst.key, 1)) for t in tiles for inst in by_tile.get(t, ())}
        if len(roots) > 1:
            conflict("planned_segment_disconnected", fluid=segment.get("fluid"), networks=len(roots))
    for record in connections:
        inst = record[0]
        if inst.origin != "planned" or record[4].connection_type != "underground":
            continue
        tile = _tile(inst.center)
        if tile not in intended:
            conflict("underground_unintended", **inst.label())
        elif derived.get(tile) != intended[tile]:
            conflict("underground_pairing_mismatch", **inst.label(), intended=list(intended[tile]))


def _judge_networks(instances, edges, union, catalog, declared_segments, permitted, conflict, unknown):
    declared = {}
    for segment in declared_segments:
        for tile in segment.get("tiles", ()):
            declared.setdefault(tuple(tile), set()).add(segment.get("fluid"))
    members: dict = {}
    identities: dict = {}
    recipe_gaps: set = set()

    def identify(node, fluid, basis):
        identities.setdefault(union.find(node), []).append((fluid, basis, node))

    for inst in instances:
        if inst.facts is None:
            continue
        assignment = None
        crafting = inst.facts.type in _CRAFTING_TYPES
        if crafting and inst.recipe is not None:
            if catalog.recipes is None:
                recipe_gaps.add(("recipe_fluid_boxes_unexported", inst.recipe))
            elif inst.recipe not in catalog.recipes:
                recipe_gaps.add(("recipe_fluid_parts_unknown", inst.recipe))
            else:
                assignment = recipe_box_assignment(inst.facts, catalog.recipes[inst.recipe])
                if assignment is None:
                    recipe_gaps.add(("recipe_box_assignment_undecidable", inst.recipe))
        for box in inst.facts.fluidboxes:
            node = (inst.key, box.index)
            members.setdefault(union.find(node), []).append(inst)
            if box.filter:
                identify(node, box.filter, "prototype_filter")
            if crafting:
                if assignment is not None and box.index in assignment:
                    basis = ("planned_recipe_assignment" if inst.origin == "planned" else
                             "ghost_recipe_assignment" if inst.ghost else "observed_recipe_assignment")
                    identify(node, assignment[box.index], basis)
                else:
                    identities.setdefault(union.find(node), []).append((None, "machine_box_unassigned", node))
            if inst.origin == "planned" and inst.facts.tile_width == 1 == inst.facts.tile_height:
                for fluid in declared.get(_tile(inst.center), ()):
                    identify(node, fluid, "declared_segment")
            if inst.origin == "existing" and not inst.ghost:
                seen = (inst.observed or {}).get(box.index)
                if seen is None or not seen["readable"]:
                    identities.setdefault(union.find(node), []).append((None, "observation_unreadable", node))
                    continue
                if seen.get("segment_fluid"):
                    identify(node, seen["segment_fluid"], "observed_segment")
                if seen.get("filter"):
                    identify(node, seen["filter"], "observed_filter")

    for code, recipe in sorted(recipe_gaps):
        unknown(code, recipe=recipe)
    permits = []
    for permit in permitted:
        try:
            planned = (permit["planned"]["entity"], _position(permit["planned"]["position"]))
            existing = (permit["existing"]["entity"], _position(permit["existing"]["position"]),
                        permit["existing"]["unit_number"])
            fluid = permit["fluid"]
        except (KeyError, TypeError) as error:
            raise FluidTopologyError("permitted attachment is malformed") from error
        if None in (planned[1], existing[1]) or not isinstance(fluid, str) or not fluid:
            raise FluidTopologyError("permitted attachment is malformed")
        permits.append((planned, existing, fluid))
    used_permits = set()
    crossing = {}
    for kind, a, b in edges:
        if a[0].origin == b[0].origin:
            continue
        p, e = (a, b) if a[0].origin == "planned" else (b, a)
        crossing.setdefault(union.find((p[0].key, p[1])), []).append((p[0], e[0]))

    results = []
    for root in sorted(set(members), key=lambda r: (min(i.key for i in members[r]), str(r))):
        insts = members[root]
        found = identities.get(root, [])
        fluids = sorted({f for f, _, _ in found if f})
        planned = any(i.origin == "planned" for i in insts)
        existing = [i for i in insts if i.origin == "existing"]
        if not planned:
            continue
        summary = {"fluids": fluids, "bases": sorted({b for _, b, _ in found}),
                   "planned_entities": sum(1 for i in insts if i.origin == "planned"),
                   "existing_entities": len(existing), "attachments": []}
        if len(fluids) > 1:
            conflict("fluid_mixing", fluids=fluids,
                     evidence=sorted({(f, b) for f, b, _ in found if f}))
        if len(insts) > 1 and any(b in {"machine_box_unassigned", "observation_unreadable"} for _, b, _ in found):
            unknown("network_identity_unknown", fluids=fluids,
                    reasons=sorted({b for f, b, _ in found if f is None}))
        for p, e in crossing.get(root, ()):
            label = {"planned": p.label(), "existing": e.label()}
            if e.ghost:
                conflict("foreign_ghost_attachment", **label)
                continue
            if e.force != catalog.force:
                conflict("foreign_force_attachment", **label)
                continue
            match = [n for n, (pp, ee, fluid) in enumerate(permits)
                     if pp == (p.name, p.center) and ee == (e.name, e.center, e.unit_number)]
            if not match:
                conflict("unpermitted_attachment", **label)
                continue
            used_permits.update(match)
            fluid = permits[match[0]][2]
            observed = {f for f, b, _ in found if b in {"observed_segment", "observed_filter"}}
            planned_fluids = {f for f, b, _ in found
                              if b in {"declared_segment", "planned_recipe_assignment", "prototype_filter"}}
            if (observed and observed != {fluid}) or (planned_fluids and planned_fluids != {fluid}):
                conflict("attachment_fluid_mismatch", **label, permitted=fluid)
            elif not observed:
                unknown("attachment_identity_unobserved", **label, permitted=fluid)
            else:
                summary["attachments"].append({**label, "fluid": fluid, "basis": "observed"})
        results.append(summary)
    for n, permit in enumerate(permits):
        if n not in used_permits:
            conflict("permitted_attachment_unrealized", planned=permit[0][0], existing=permit[1][0])
    return results


def _check_scope(instances, catalog, snapshot_info, unknown):
    """Require the snapshot area to cover every tile a planned connection could reach."""
    if snapshot_info.get("status") != "read":
        return
    reaches = [c.max_underground_distance or 1 for facts in catalog.entities.values()
               for box in facts.fluidboxes for c in box.connections]
    if any(facts.unknown for facts in catalog.entities.values() if facts.fluidboxes or facts.unknown):
        unknown("catalog_reach_incomplete")
    margin = max(reaches, default=1) + 1
    tiles = [t for inst in instances if inst.origin == "planned" and inst.facts
             for t in (inst.footprint() or {_tile(inst.center)})]
    if not tiles:
        return
    area = snapshot_info["area"]
    if (min(x for x, _ in tiles) - margin < area["x_min"] or min(y for _, y in tiles) - margin < area["y_min"]
            or max(x for x, _ in tiles) + margin >= area["x_max_exclusive"]
            or max(y for _, y in tiles) + margin >= area["y_max_exclusive"]):
        unknown("snapshot_area_insufficient", required_margin=margin)
    snapshot_info["required_margin"] = margin


def _check_underground_tiles(pairs, planned_underground, catalog, facts, snapshot, conflict, unknown) -> dict:
    """Judge terrain on every derived underground span with a planned endpoint.

    Planned-to-planned blocked spans are conflicts (the intended link cannot
    form). A blocked span reaching an existing entity is unverified because the
    engine's fallback pairing is not modelled. Missing facts keep the v1
    ``underground_tile_blocking_unverified`` code with explicit reasons.
    """
    if not isinstance(facts, UndergroundMaskFacts):
        raise FluidTopologyError("underground mask facts must be loaded UndergroundMaskFacts")
    summary = {"status": "not_applicable", "mask_facts_sha256": facts.sha256, "mask_source": facts.source,
               "attested_mods": dict(facts.attested_mods), "spans_checked": 0, "spans_clear": 0, "spans_blocked": 0}
    if not planned_underground:
        return summary
    reasons = set()
    if catalog.active_mods is None or dict(facts.active_mods) != dict(catalog.active_mods):
        reasons.add("mask_facts_mod_set_mismatch")
    scan = "snapshot_unavailable" if snapshot is None else parse_tile_scan(snapshot.get("tiles"),
                                                                          snapshot_tick=snapshot["tick"])
    if isinstance(scan, str):
        reasons.add(scan)
    if reasons:
        summary["status"] = "unverified"
        unknown("underground_tile_blocking_unverified", planned_connections=len(planned_underground),
                reasons=sorted(reasons))
        return summary
    summary["tile_scan"] = {"tick": scan.tick, "area": dict(zip(("x_min", "y_min", "x_max_exclusive",
                                                                 "y_max_exclusive"), scan.area))}

    def mask(record):
        inst, box, _, _, fact = record
        entry = facts.lookup(inst.name, box, fact.direction, fact.positions[0])
        if entry is None:
            return None, "mask_fact_missing"
        if entry.max_underground_distance != fact.max_underground_distance:
            return None, "mask_fact_connection_mismatch"
        return entry.layers, None

    unpaired = {id(r) for r in planned_underground}
    for a, b in pairs:
        if "planned" not in {a[0].origin, b[0].origin}:
            continue
        unpaired.discard(id(a))
        unpaired.discard(id(b))
        summary["spans_checked"] += 1
        endpoints = [a[0].label(), b[0].label()]
        masks = [mask(a), mask(b)]
        missing = sorted({reason for _, reason in masks if reason})
        if missing:
            reasons.update(missing)
            continue
        verdict = judge_span(scan, a[2], b[2], masks[0][0] | masks[1][0])
        if verdict["verdict"] == "clear":
            summary["spans_clear"] += 1
        elif verdict["verdict"] == "blocked":
            summary["spans_blocked"] += 1
            if a[0].origin == b[0].origin:
                conflict("underground_span_blocked", endpoints=endpoints, blocking_tiles=verdict["blocking_tiles"])
            else:
                unknown("underground_span_blocked_unmodelled", endpoints=endpoints,
                        blocking_tiles=verdict["blocking_tiles"])
        else:
            reasons.update(verdict["reasons"])
    summary["unpaired_planned_connections"] = len(unpaired)
    if reasons:
        unknown("underground_tile_blocking_unverified", planned_connections=len(planned_underground),
                reasons=sorted(reasons))
    summary["status"] = "unverified" if reasons else "checked"
    return summary
