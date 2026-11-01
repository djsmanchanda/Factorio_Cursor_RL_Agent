# Path: tools/optimizer_block_proposals.py | Purpose: Shared composition of the requester-fed red-science optimizer block (catalogs, site screening, power anchor, binding, reservation, verified offers) for the paused trial driver and the clean runner.
"""Deterministic block proposals shared by ``tools/optimizer_block_trial.py`` and the clean runner.

Everything here is geometry, legality and accounting: it captures the live
catalogs, screens site origins inside the roboport logistic radius, attaches
the block to the nearest powered substation, binds the artifact, holds its bill
in a ledger and asks the engine verifier whether the result is legal. It
chooses nothing: callers decide whether, when and where to build.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from core.catalog_demand import mod_set_sha256, plan_catalog_demand
from core.optimizer_artifact_binding import bind_artifact_to_construction_plan
from core.optimizer_construction_state import ConstructionScope
from core.science_line_candidate import build_science_line_candidate
from orchestrator.game_bridge import load_json
from orchestrator.material_reservations import MaterialReservationLedger
from orchestrator.optimizer_reservations import reserve_optimizer_artifact
from planners.ghost_line_artifact import derive_ghost_artifact_from_runtime_catalog
from planners.optimizer_power_attachment import compose_optimizer_power_attachment
from planners.plan_validation import actions, entity_footprint_tiles
from planners.science_block_geometry import build_science_block_geometry
from tools.optimizer_research_capture import PAUSE_ASSERTION, _parse, _scope
from tools.optimizer_saved_world import optimizer_candidate_provider, optimizer_check_verifier
from training.laya_layout_stream import _locked as lock_stream
from training.laya_saved_world import owned_root, verify_instance, SavedWorldError

SURFACE, FORCE = "nauvis", "player"
PACK = "automation-science-pack"
TARGET_RATE = Fraction(1, 5)  # 0.2 packs/s: two assembling-machine-1 at speed 0.5 over a 5 s recipe.
ANCHOR_ENTITY = "substation"
POLE_ENTITY = "medium-electric-pole"
LINE_SPEC = {"recipe": PACK, "machine_count": 2, "machine": "assembling-machine-1", "belt_type": "transport-belt",
             "inserter_type": "inserter", "mining_feed": False, "feed_style": "requester",
             "direct_bus_ingredients": [], "chained_ingredients": [], "terminal_collector": True}
# Upstream recipes are mapped only so the balance sizing is explicit and complete; only the pack line is built.
MACHINES = {PACK: "assembling-machine-1", "copper-plate": "electric-furnace",
            "iron-gear-wheel": "assembling-machine-2", "iron-plate": "electric-furnace"}
PREFERENCES = {PACK: PACK, "copper-plate": "copper-plate", "iron-gear-wheel": "iron-gear-wheel",
               "iron-plate": "iron-plate"}
BOUNDARIES = [{"name": "copper-ore", "cap": 5}, {"name": "iron-ore", "cap": 5}]
SCREENED_ENTITY_TYPES_EXCLUDED = ("resource", "character", "corpse", "item-entity")
PACKS_PER_CRAFT = 1  # automation-science-pack yields one pack per finished craft.


class TrialError(RuntimeError):
    """A step failed closed; receipts already written are left in place."""


class LuaError(TrialError):
    """The server ran a read/write Lua command and reported a Lua error."""


class UnfundedError(TrialError):
    """Network stock cannot hold the block's bill; ``short`` maps each missing item to its count."""

    def __init__(self, message, short):
        super().__init__(message)
        self.short = dict(short)


# --------------------------------------------------------------------------- Lua access

_PRELUDE_BASE = (
    'local surface=assert(game.surfaces.nauvis); local force=assert(game.forces.player); ')
_PRELUDE_HELPERS = (
    'local function contents(inventory) local result={} for key,v in pairs(inventory.get_contents()) do '
    'if type(v)=="number" then result[key]=v elseif v.quality=="normal" then result[v.name]=(result[v.name] or 0)+v.count end end '
    'return result end; ')
_PRELUDE = _PRELUDE_BASE + PAUSE_ASSERTION + '; ' + _PRELUDE_HELPERS
_PRELUDE_LIVE = _PRELUDE_BASE + _PRELUDE_HELPERS  # One command runs inside a single tick, so reads stay atomic unpaused.

_ENTITIES_LUA = (
    'local a=request.area; local result={}; '
    'for _,e in pairs(surface.find_entities_filtered{area={{a.x_min,a.y_min},{a.x_max,a.y_max}}}) do '
    'if e.type~="resource" and e.type~="character" and e.type~="corpse" and e.type~="item-entity" then '
    'local b=e.bounding_box; local name=e.name; if e.type=="entity-ghost" then name=e.ghost_name end; '
    'result[#result+1]={name=name,type=e.type,ghost=(e.type=="entity-ghost"),unit_number=e.unit_number,'
    'x=e.position.x,y=e.position.y,box={b.left_top.x,b.left_top.y,b.right_bottom.x,b.right_bottom.y}} end end; '
    'return {tick=game.tick,entities=result}')

_BLOCK_STATE_LUA = (
    'local status_names={}; for name,id in pairs(defines.entity_status) do status_names[id]=name end; '
    'local rows={}; for _,spec in ipairs(request.entities) do '
    'local row={name=spec.name,x=spec.x,y=spec.y,found=false}; '
    'for _,e in pairs(surface.find_entities_filtered{position={spec.x,spec.y},name=spec.name,force=force}) do '
    'if e.position.x==spec.x and e.position.y==spec.y then row.found=true; row.status=status_names[e.status] or "unknown"; '
    'local gok,gid=pcall(function() return e.electric_network_id end); if gok and gid then row.grid=tostring(gid) end; '
    'if e.type=="logistic-container" then row.contents=contents(e.get_inventory(defines.inventory.chest)); '
    'local rok,reqs=pcall(function() local out={}; local sections=e.get_logistic_sections(); '
    'if sections then for _,section in pairs(sections.sections) do if section.valid then '
    'for i=1,section.filters_count do local slot=section.get_slot(i); '
    'local name=slot and slot.value and (slot.value.name or slot.value); '
    'if name then out[#out+1]={name=name,count=tonumber(slot.min)} end end end end end; return out end); '
    'if rok then row.requests=reqs else row.requests_error=tostring(reqs) end end; '
    'if e.type=="assembling-machine" then local r=e.get_recipe(); row.recipe=r and r.name; '
    'row.products_finished=e.products_finished end end end; rows[#rows+1]=row end; '
    'return {tick=game.tick,rows=rows}')


def _as_list(value):
    return [] if value in (None, {}) else list(value)


def lua_json(adapter, source, request=None, *, paused=True):
    """Run one read-or-write Lua command on the verified adapter and return its JSON value.

    ``paused=False`` drops the paused-clock assertion for single-command reads on a live clock.
    """
    adapter._check_instance()
    name = f"optimizer_block_trial/{uuid.uuid4().hex}.json"
    payload = json.dumps(request or {}, separators=(",", ":"))
    delimiter = "="
    while f"]{delimiter}]" in payload:
        delimiter += "="
    prelude = _PRELUDE if paused else _PRELUDE_LIVE
    body = f"local request=helpers.json_to_table([{delimiter}[{payload}]{delimiter}]); " + prelude + source
    # Lua comments would swallow the rest of a joined RCON line; the sources above carry none.
    body = " ".join(line.split("--", 1)[0] for line in body.splitlines())
    command = (f'/sc local ok,value=pcall(function() {body} end); helpers.write_file("{name}",'
               f'helpers.table_to_json({{ok=ok,value=ok and value or nil,error=not ok and tostring(value) or nil}}),false); '
               'rcon.print("optimizer-block-trial")')
    adapter.bridge.command(command)
    result = load_json(adapter.bridge.script_output / name)
    if not result.get("ok"):
        raise LuaError(f"Lua command failed: {result.get('error')}")
    return result["value"]


def verify_disposable(root):
    """Refuse unless the root is an owned, process-verified, non-production disposable saved world."""
    try:
        root = owned_root(Path(root))
        manifest = verify_instance(root)
    except (SavedWorldError, OSError, ValueError, KeyError) as error:
        raise TrialError(f"refusing: not a verified disposable saved world: {error}") from error
    if manifest.get("production_authority") is not False or manifest.get("runtime") != "disposable_saved_world":
        raise TrialError("refusing: manifest does not declare a non-production disposable saved world")
    return root, manifest


# --------------------------------------------------------------------------- catalogs

def capture_catalogs(adapter, *, game_version=None):
    """Capture runtime and recipe catalogs at one paused tick through the bridge export methods."""
    with lock_stream(adapter.root):
        before = adapter.query()
        tick, mods = _scope(before, adapter)
        adapter._check_instance()
        runtime = adapter.bridge.export_runtime_catalog(adapter.surface, adapter.force)
        adapter._check_instance()
        recipes = adapter.bridge.export_recipe_catalog_capture(adapter.force)
        after = adapter.query()
        if _scope(after, adapter) != (tick, mods):
            raise TrialError("paused world tick, scope or mod set changed during catalog capture")
    runtime_report, recipe_report = _parse(runtime), _parse(recipes)
    if (runtime_report.get("tick") != tick or recipe_report.get("tick") != tick
            or recipe_report.get("force") != adapter.force or recipe_report.get("active_mods") != mods):
        raise TrialError("captured catalog tick, force or mod set does not match the paused world")
    version, basis = (game_version, "caller assertion") if game_version else (mods.get("base"), "base mod version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise TrialError("a MAJOR.MINOR.PATCH game version is required (pass --game-version)")
    entries = [{"name": name, "version": value} for name, value in sorted(mods.items())]
    provenance = {"catalog_sha256": hashlib.sha256(recipes).hexdigest(), "game_version": version,
                  "surface": adapter.surface, "force": adapter.force, "export_tick": tick,
                  "export_command": "/export_recipe_catalog", "mods": entries, "mod_set_sha256": mod_set_sha256(entries)}
    return {"source": "live_capture", "tick": tick, "mods": mods, "runtime_bytes": runtime, "recipe_bytes": recipes,
            "runtime_sha256": hashlib.sha256(runtime).hexdigest(), "recipe_provenance": provenance,
            "game_version": version, "game_version_basis": basis}


@dataclass
class Context:
    """Everything fixed for the whole run: scope, catalogs, sizing and the site survey."""
    root: Path
    scope: ConstructionScope
    catalogs: dict
    placement: dict
    recipe_catalog: dict
    demand: object
    pole_wire: float
    template: "Template"
    port: dict
    network_anchor: dict
    network_id: str
    base_episode: str


def placement_capture(catalogs, *, surface=SURFACE, force=FORCE):
    return {"catalog_bytes": catalogs["runtime_bytes"], "expected_sha256": catalogs["runtime_sha256"],
            "surface": surface, "force": force, "active_mods": dict(catalogs["mods"])}


def plan_demand(catalogs):
    """Explicit-provenance demand plan for 0.2 packs/s; failures here are site-independent."""
    recipe_catalog = json.loads(catalogs["recipe_bytes"])
    demand = plan_catalog_demand(catalogs["recipe_bytes"], provenance=catalogs["recipe_provenance"],
                                 demand={PACK: TARGET_RATE}, boundaries=BOUNDARIES, preferences=PREFERENCES)
    return recipe_catalog, demand


def operating_demand(recipe_catalog, rate=TARGET_RATE):
    """Ingredient items per second the pack line draws at ``rate`` packs/s, from the captured recipe."""
    recipe = next(r for r in recipe_catalog["recipes"] if r["name"] == PACK)
    crafts = Fraction(rate) / next(p["amount"] for p in recipe["products"] if p["name"] == PACK)
    return {i["name"]: float(crafts * i["amount"]) for i in recipe["ingredients"]}


def line_spec(origin):
    return {**LINE_SPEC, "origin_x": origin[0], "origin_y": origin[1]}


def build_candidate_artifact(ctx, origin):
    """Demand -> science line candidate -> ghost artifact (runtime-catalog item mapping) for one origin."""
    candidate = build_science_line_candidate(ctx.demand, ctx.recipe_catalog, recipe=PACK, line_spec=line_spec(origin),
                                             machine_by_recipe=MACHINES, catalog_bytes=ctx.catalogs["recipe_bytes"])
    if candidate.count_status != "adequate" or candidate.has_direct_actions:
        raise TrialError(f"line candidate is {candidate.count_status} (needs {candidate.required_machine_count}) "
                         f"or has direct actions")
    artifact = derive_ghost_artifact_from_runtime_catalog(candidate.geometry["plan"], placement_capture=ctx.placement)
    artifact["planning_context"] = {"version": "planner-context-v1", "trust": "unverified", "data": {
        "kind": "requester-red-block-v1", "origin": {"x": origin[0], "y": origin[1]},
        "required_machine_count": candidate.required_machine_count, "line_spec": candidate.geometry["spec"]}}
    return candidate, artifact


# --------------------------------------------------------------------------- site

@dataclass(frozen=True)
class Template:
    """The block footprint relative to its origin, taken from the same geometry the chain builds."""
    tiles: frozenset
    poles: tuple
    min_tx: int
    max_tx: int
    min_ty: int
    max_ty: int
    parts: tuple = ()  # (entity, x, y) of every non-pole piece, for land-reservation checks

    @property
    def center(self):
        return ((self.min_tx + self.max_tx + 1) / 2, (self.min_ty + self.max_ty + 1) / 2)

    def footprint(self, origin):
        return frozenset((tx + origin[0], ty + origin[1]) for tx, ty in self.tiles)

    def pseudo_plan(self, origin):
        """A plan-shaped view of the block's non-pole pieces at ``origin`` for the land-reservation rule."""
        return {"phases": [{"actions": [{"entity": name, "position": {"x": x + origin[0], "y": y + origin[1]}}
                                        for name, x, y in self.parts]}]}


def block_template():
    geometry = build_science_block_geometry(line_spec((0, 0)))
    placed = list(actions(geometry["plan"]))
    tiles = frozenset().union(*(entity_footprint_tiles(action) for action in placed))
    poles = tuple((action["position"]["x"], action["position"]["y"]) for action in placed if action["entity"] == POLE_ENTITY)
    xs, ys = [t[0] for t in tiles], [t[1] for t in tiles]
    parts = tuple((a["entity"], a["position"]["x"], a["position"]["y"]) for a in placed
                  if a["entity"] not in (POLE_ENTITY, ANCHOR_ENTITY))
    return Template(tiles, poles, min(xs), max(xs), min(ys), max(ys), parts)


def known_tiles(entities, *, eps=1e-6):
    """Map every tile touched by a known entity's box to the entity name that touches it."""
    blocked = {}
    for entity in entities:
        left, top, right, bottom = entity["box"]
        for x in range(math.floor(left + eps), math.ceil(right - eps)):
            for y in range(math.floor(top + eps), math.ceil(bottom - eps)):
                blocked.setdefault((x, y), entity["name"])
    return blocked


def logistic_window(port, margin):
    """Inclusive tile-edge window for a whole-block footprint with ``margin`` tiles inside the logistic area."""
    px, py, radius = port["position"]["x"], port["position"]["y"], port["logistic_radius"]
    if not 0 <= margin < radius:
        raise TrialError("margin must be non-negative and smaller than the logistic radius")
    return px - radius + margin, px + radius - margin, py - radius + margin, py + radius - margin


def origin_candidates(template, port, margin):
    """Integer origins whose whole footprint lies inside the margin-shrunk logistic area, nearest the roboport first."""
    x_lo, x_hi, y_lo, y_hi = logistic_window(port, margin)
    px, py = port["position"]["x"], port["position"]["y"]
    cx, cy = template.center
    origins = [(ox, oy)
               for ox in range(math.ceil(x_lo - template.min_tx), math.floor(x_hi - template.max_tx - 1) + 1)
               for oy in range(math.ceil(y_lo - template.min_ty), math.floor(y_hi - template.max_ty - 1) + 1)]
    return sorted(origins, key=lambda o: (math.hypot(o[0] + cx - px, o[1] + cy - py), o))


def power_anchors(poles, block_poles, *, pole_wire, entity=ANCHOR_ENTITY):
    """Existing powered ``entity`` poles whose wire reach covers a block pole, nearest first."""
    anchors = []
    for pole in poles:
        unit, position = pole.get("unit_number"), pole.get("position")
        if (pole.get("name") != entity or pole.get("generated") is not True or type(unit) is not int
                or pole.get("quality", "normal") != "normal" or pole.get("grid") in (None, "nil")
                or not isinstance(position, dict)):
            continue
        distance = min(math.hypot(px - position["x"], py - position["y"]) for px, py in block_poles)
        if distance <= min(pole["wire"], pole_wire):
            anchors.append({"entity": pole["name"], "position": {"x": position["x"], "y": position["y"]},
                            "unit_number": unit, "grid": pole["grid"], "distance": round(distance, 3)})
    return sorted(anchors, key=lambda a: (a["distance"], a["unit_number"]))


def screen_origin(origin, template, blocked, poles, *, pole_wire):
    """Cheap offline reasons an origin cannot work; the verifier still decides the survivors."""
    ox, oy = origin
    names = []
    for tx, ty in template.tiles:
        name = blocked.get((tx + ox, ty + oy))
        if name is not None and name not in names:
            names.append(name)
    reasons = ["known_entity_overlap:" + ",".join(sorted(names)[:3])] if names else []
    if not power_anchors(poles, [(px + ox, py + oy) for px, py in template.poles], pole_wire=pole_wire):
        reasons.append("no_substation_in_wire_reach")
    return reasons


def search_sites(origins, screen, attempt, *, max_attempts):
    """First origin that passes ``attempt``; every rejected origin is returned with its reason codes."""
    rejected, attempts = [], 0
    for origin in origins:
        reasons = screen(origin)
        if reasons:
            rejected.append({"origin": list(origin), "stage": "screen", "reasons": reasons})
            continue
        if attempts >= max_attempts:
            return None, None, rejected, "max_site_attempts_reached"
        attempts += 1
        outcome = attempt(origin, attempts)
        if outcome["reasons"]:
            rejected.append({"origin": list(origin), "stage": "verifier", "reasons": outcome["reasons"]})
            continue
        return origin, outcome, rejected, None
    return None, None, rejected, "no_candidate_origin_left"


def survey_entities(adapter, port, tick_expected=None, *, paused=True):
    window = {"x_min": port["position"]["x"] - port["logistic_radius"] - 2,
              "x_max": port["position"]["x"] + port["logistic_radius"] + 2,
              "y_min": port["position"]["y"] - port["logistic_radius"] - 2,
              "y_max": port["position"]["y"] + port["logistic_radius"] + 2}
    result = lua_json(adapter, _ENTITIES_LUA, {"area": window}, paused=paused)
    if tick_expected is not None and result.get("tick") != tick_expected:
        raise TrialError("entity survey was not taken at the facts tick")
    entities = _as_list(result.get("entities"))
    if len(entities) > 50000:
        raise TrialError("entity survey is too large to screen")
    return result["tick"], entities


def choose_port(facts):
    ports = [p for p in _as_list(facts.get("ports")) if p.get("energy", 0) > 0]
    if len(ports) != 1:
        raise TrialError(f"expected exactly one powered roboport, found {len(ports)}")
    return ports[0]


def pole_reach(runtime_bytes, entity=POLE_ENTITY):
    prototypes = {e["name"]: e for e in json.loads(runtime_bytes)["entities"]}
    spec = (prototypes.get(entity) or {}).get("electric_pole")
    if not isinstance(spec, dict) or spec.get("quality") != "normal" or type(spec.get("max_wire_distance")) not in (int, float):
        raise TrialError(f"runtime catalog lacks normal-quality wire reach for {entity}")
    return float(spec["max_wire_distance"])


def build_context(adapter, root, facts, *, base_episode, game_version=None, port=None):
    """Capture catalogs and size the block for a world whose clock is paused at ``facts['tick']``."""
    port = port or choose_port(facts)
    catalogs = capture_catalogs(adapter, game_version=game_version)
    recipe_catalog, demand = plan_demand(catalogs)
    from training.laya_saved_world import file_hash
    scope = ConstructionScope(str(root), file_hash(root / "manifest.json"), SURFACE, FORCE)
    return Context(root=root, scope=scope, catalogs=catalogs, placement=placement_capture(catalogs),
                   recipe_catalog=recipe_catalog, demand=demand, pole_wire=pole_reach(catalogs["runtime_bytes"]),
                   template=block_template(), port=port, network_anchor=dict(port["position"]),
                   network_id=port["network"], base_episode=base_episode)


# --------------------------------------------------------------------------- one site attempt

def _short(error):
    return str(error).replace("\n", " ")[:200]


def site_identity(base_episode, origin, salt=""):
    """Deterministic ledger episode, project and transaction identity for a block at ``origin``.

    The same origin in the same run always yields the same identity, so a block's
    durable project and transaction survive re-offers, restarts and recovery.
    """
    tag = f"{'n' if origin[0] < 0 else ''}{abs(origin[0])}_{'n' if origin[1] < 0 else ''}{abs(origin[1])}"
    project_id = f"red-block-{tag}"
    transaction = hashlib.sha256(f"{base_episode}|{salt}|{project_id}".encode()).hexdigest()[:32]
    return {"episode": f"{base_episode}-{tag}"[:64], "project_id": project_id, "transaction": transaction}


def attempt_site(adapter, ctx, facts, origin, number, *, identity=None):
    """Bind, power-attach, reserve and offer the block at ``origin``; reasons empty means the verifier passed."""
    identity = identity or {"episode": f"{ctx.base_episode}-s{number}", "project_id": f"red-block-{origin[0]}_{origin[1]}",
                            "transaction": uuid.uuid4().hex}
    episode, project_id, transaction = identity["episode"], identity["project_id"], identity["transaction"]
    candidate, artifact = build_candidate_artifact(ctx, origin)
    def bind(art):
        return bind_artifact_to_construction_plan(art, scope=ctx.scope, project_id=project_id, transaction_id=transaction,
            throughput_output=PACK, throughput_target=TARGET_RATE, throughput_unit="items/second")
    plain = bind(artifact)
    block_poles = [(a["position"]["x"], a["position"]["y"]) for a in actions(artifact["plan"]) if a["entity"] == POLE_ENTITY]
    anchors = power_anchors(_as_list(facts.get("poles")), block_poles, pole_wire=ctx.pole_wire)
    attached, anchor, failures = None, None, []
    for anchor in anchors:
        try:
            attached = compose_optimizer_power_attachment(
                plain, placement_capture=ctx.placement, pole_actions=[],
                anchor={k: anchor[k] for k in ("entity", "position", "unit_number")})
            break
        except (ValueError, KeyError, TypeError) as error:
            failures.append(f"{anchor['unit_number']}:{_short(error)}")
    if attached is None:
        return {"reasons": ["power_attachment_failed:" + (";".join(failures) or "no_anchor")], "episode": episode}
    bound = bind(attached["artifact"])
    ledger = MaterialReservationLedger(adapter.root / "server/script-output", episode_id=episode,
                                       surface=adapter.surface, force=adapter.force)
    network = facts["networks"][ctx.network_id]
    stock = {item: max(0, count - network["reserved"].get(item, 0)) for item, count in network["items"].items()}
    project = reserve_optimizer_artifact(bound, ledger, episode_id=episode, stock=stock)
    if project.state != "ready":
        short = {item: count - project.reserved.get(item, 0) for item, count in project.required.items()
                 if count > project.reserved.get(item, 0)}
        raise UnfundedError(f"network stock cannot fund the block bill; short {short} "
                            f"(a fresh start has no belts; the block test arm needs --prestock)", short)
    provide = optimizer_candidate_provider(bound, episode_id=episode, network_anchor=ctx.network_anchor)
    offered = provide(adapter, facts, {"needs": []})
    fluid_capture = {"sha256": ctx.catalogs["runtime_sha256"], "json": ctx.catalogs["runtime_bytes"].decode("utf-8")}
    verifier = optimizer_check_verifier(bound, episode_id=episode, network_anchor=ctx.network_anchor,
                                        fluid_capture=fluid_capture)
    if len(offered) != 1:
        return {"reasons": ["provider_offered_no_candidate"], "episode": episode}
    # Ports are blanked so options() evaluates only this external candidate; stock and anchor came from `facts`.
    legal, info = adapter.options({**facts, "ports": []}, 0, additional_candidates=offered, check_verifier=verifier)
    option_id = offered[0]["option_id"]
    chosen = next((c for c in legal if c["option_id"] == option_id), None)
    if chosen is None:
        entry = info["validations"].get(option_id) or {}
        reasons = [r for r in dict.fromkeys((entry.get("reason"), entry.get("required_check_reason"))) if r]
        return {"reasons": reasons or ["rejected_without_reason"], "episode": episode}
    return {"reasons": [], "episode": episode, "bound": bound, "ledger": ledger, "candidate": chosen,
            "anchor": anchor, "power": {k: v for k, v in attached.items() if k != "artifact"},
            "project_id": project_id, "transaction_id": transaction, "verifier": verifier,
            "fluid_capture": fluid_capture, "check_evidence": chosen.get("check_evidence")}


def propose_sites(adapter, ctx, facts, *, max_options=3, max_attempts=8, margin=2, land_check=None, exclude=(),
                  identity_for=None, paused=True):
    """Up to ``max_options`` footprint-disjoint verified sites, nearest the roboport first.

    ``land_check(plan)`` returns a reason when a site would take land reserved for another
    purpose. Offering alternatives chooses nothing: the caller's policy picks among them.
    Returns ``{offers, rejected, stop, unfunded, origins, counts}``; an unfunded bill stops the
    search because every site holds the same bill.
    """
    _tick, entities = survey_entities(adapter, ctx.port, tick_expected=facts["tick"], paused=paused)
    blocked = known_tiles(entities)
    poles = _as_list(facts.get("poles"))
    origins = origin_candidates(ctx.template, ctx.port, margin)
    taken, offers, rejected, attempts, stop, unfunded = set(), [], [], 0, None, None
    counts: dict = {}
    def count(key):
        counts[key] = counts.get(key, 0) + 1
    for origin in origins:
        if len(offers) >= max_options:
            break
        if origin in exclude:
            count("excluded")
            continue
        screen = screen_origin(origin, ctx.template, blocked, poles, pole_wire=ctx.pole_wire)
        if screen:
            count("screen:" + screen[0].split(":")[0])
            continue
        footprint = ctx.template.footprint(origin)
        if footprint & taken:
            count("overlaps_alternative")
            continue
        reason = land_check(ctx.template.pseudo_plan(origin)) if land_check else None
        if reason:
            count("land:" + reason)
            continue
        if attempts >= max_attempts:
            stop = "max_site_attempts_reached"
            break
        attempts += 1
        identity = identity_for(origin) if identity_for else None
        try:
            outcome = attempt_site(adapter, ctx, facts, origin, attempts, identity=identity)
        except UnfundedError as error:
            unfunded, stop = error.short, "unfunded"
            break
        if outcome["reasons"]:
            rejected.append({"origin": list(origin), "stage": "verifier", "reasons": outcome["reasons"]})
            continue
        outcome["origin"] = origin
        offers.append(outcome)
        taken |= footprint
    if stop is None and len(offers) < max_options:
        stop = "no_candidate_origin_left"
    return {"offers": offers, "rejected": rejected, "stop": stop, "unfunded": unfunded, "origins": len(origins),
            "attempts": attempts, "counts": counts}


# --------------------------------------------------------------------------- block state and own output

def state_specs(plan):
    keep = {"assembling-machine-1", "requester-chest", "passive-provider-chest", "inserter", POLE_ENTITY}
    return [{"name": a["entity"], "x": a["position"]["x"], "y": a["position"]["y"]}
            for a in actions(plan) if a["entity"] in keep]


def block_state(adapter, specs, *, paused=True):
    result = lua_json(adapter, _BLOCK_STATE_LUA, {"entities": specs}, paused=paused)
    return {"tick": result["tick"], "rows": _as_list(result.get("rows"))}


def requested_items(plan):
    """Each requester chest position with the request set the plan declared."""
    return {(a["position"]["x"], a["position"]["y"]): {r["name"]: r["count"] for r in a["logistic_requests"]}
            for a in actions(plan) if a["entity"] == "requester-chest"}


def confirm_requests(plan, state):
    """Compare read-back requester requests with the plan.

    ``match`` is True/False when the chest was readable and None when the read-back API gave nothing, so an
    unreadable chest is reported as unknown rather than assumed configured or misconfigured.
    """
    expected, rows, confirmed = requested_items(plan), {}, {}
    for row in state["rows"]:
        if row["name"] == "requester-chest":
            rows[(row["x"], row["y"])] = row
    for position, want in expected.items():
        row = rows.get(position)
        have = {r["name"]: r["count"] for r in _as_list(row.get("requests"))} if row and "requests" in row else None
        confirmed[f"{position[0]},{position[1]}"] = {"expected": want, "actual": have,
                                                     "match": None if have is None else have == want}
    matches = [c["match"] for c in confirmed.values()]
    return {"requesters": confirmed, "all_match": bool(matches) and all(m is True for m in matches),
            "mismatch": any(m is False for m in matches), "unreadable": (not matches) or any(m is None for m in matches)}


def own_output(state, specs):
    """Total finished crafts of exactly this block's assemblers, or ``None`` if any is missing or unreadable.

    This is the block's own counter (``products_finished`` per assembler), not force-wide production.
    """
    wanted = {(s["x"], s["y"]) for s in specs if s["name"] == "assembling-machine-1"}
    rows = {(r["x"], r["y"]): r for r in state["rows"] if r.get("name") == "assembling-machine-1"}
    if not wanted or set(rows) != wanted:
        return None
    total = 0
    for row in rows.values():
        finished = row.get("products_finished")
        if not row.get("found") or row.get("recipe") != PACK or type(finished) not in (int, float):
            return None
        total += int(finished)
    return total
