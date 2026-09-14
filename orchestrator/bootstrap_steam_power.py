# Path: orchestrator/bootstrap_steam_power.py
# Purpose: Add a temporary, measured steam bridge only when the seed grid cannot meet demand.

"""Measured, fully funded early steam power.

The seed electric-energy-interface remains authoritative during bootstrap.  A
steam unit is therefore never a speculative power build: it is considered only
when the observed firm generation is below the safety-margin consumer target.
Each unit is one boiler and its two directly connected steam engines (1.8 MW).
Coal reaches the boiler on a direct belt from an owned coal mine, so no
requester chest or bot-delivered fuel is consumed before logistic production.

The geometry is intentionally isolated behind this module.  Its temporary
fuel entities are admitted through the narrow ``bootstrap-steam-v1`` build-plan
contract; all other planners remain electric-only.
"""

from __future__ import annotations

import math
import json
import os
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from orchestrator import chemical_survey, live_base
from orchestrator.extraction_transport import (
    planned_footprint_tiles,
    preflight_ingredient_transport,
)
from orchestrator.power_district import (
    SAFETY_MARGIN,
    network_peak_consumption_kw,
    usable_metrics,
)
from planners.plan_validation import validate_build_plan
from planners.resource_layouts import verified_offshore_pump_output_tile

Point = tuple[float, float]
Emit = Callable[[str], None]

STEAM_UNIT_KW = 1800.0
MIN_OBSERVED_LOAD_KW = 100.0
MAX_COAL_ROUTE_TILES = 400


@dataclass(frozen=True)
class SteamNeed:
    """One observed decision, kept separate from construction geometry."""

    firm_generation_kw: float
    peak_load_kw: float
    target_kw: float
    deficit_kw: float

    @property
    def required_units(self) -> int:
        return math.ceil(self.deficit_kw / STEAM_UNIT_KW - 1e-9)


def _state_path(script_output: Path | str) -> Path:
    return Path(script_output) / "logs" / "deterministic-bootstrap-steam.json"


def _load_state(
    script_output: Path | str, surface: str, force: str, *, episode_id: str | None = None,
) -> dict | None:
    path = _state_path(script_output)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("bootstrap steam state is unreadable; refusing duplicate build") from error
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise RuntimeError("bootstrap steam state has an unsupported version")
    if payload.get("surface") != surface or payload.get("force") != force:
        raise RuntimeError("bootstrap steam state scope differs from the active run")
    if episode_id is not None and payload.get("episode_id") != episode_id:
        raise RuntimeError("bootstrap steam state episode differs from the active run")
    plan = payload.get("plan")
    if not isinstance(plan, dict) or payload.get("plan_sha256") != _plan_sha256(plan):
        raise RuntimeError("bootstrap steam state plan checksum is invalid")
    if payload.get("lifecycle") not in {"prepared", "submitted", "active"}:
        raise RuntimeError("bootstrap steam state lifecycle is invalid")
    actions = payload.get("retirement_actions")
    if not isinstance(actions, list) or not actions:
        raise RuntimeError("bootstrap steam state has no owned retirement actions")
    return payload


def _plan_sha256(plan: dict) -> str:
    return hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()


def _save_state(script_output: Path | str, payload: dict) -> None:
    path = _state_path(script_output)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def solar_storage_is_night_sustainable(
    solar_generation_kw: float, storage_mj: float, peak_load_kw: float,
) -> bool:
    """Whether solar and storage alone can cover the measured night target.

    The existing seed source is deliberately excluded.  It is not retired, but
    it must not make a temporary coal plant look safe to dismantle.
    """
    metrics = usable_metrics(0.0, solar_generation_kw, storage_mj, peak_load_kw)
    return (
        metrics["usable_generation_kw"] + 1e-6 >= metrics["generation_target_kw"]
        and metrics["storage_mj"] + 1e-6 >= metrics["storage_target_mj"]
        and metrics["recharge_surplus_kw"] + 1e-6 >= metrics["recharge_target_kw"]
    )


def _retire_if_solar_is_ready(
    client: object, bridge: object, surface: str, force: str,
    reference_point: Point, peak_load_kw: float, emit: Emit,
) -> bool:
    """Bot-retire only exact owned steam entities after a solar-night survey."""
    state = _load_state(
        getattr(bridge, "script_output", Path("")), surface, force,
        episode_id=getattr(bridge, "episode_id", None),
    )
    if state is None:
        return False
    if state["lifecycle"] != "active":
        return False
    try:
        firm = live_base.network_firm_generation_kw(client, surface, force, reference_point)
        total = live_base.network_generation_kw(client, surface, force, reference_point)
        storage = live_base.network_accumulator_storage_mj(client, surface, force, reference_point)
    except live_base.TelemetryError as error:
        emit(f"BOOTSTRAP STEAM RETIREMENT deferred: invalid telemetry: {error}")
        return False
    if firm is None or total is None or storage is None:
        emit("BOOTSTRAP STEAM RETIREMENT deferred: solar/storage survey unavailable")
        return False
    solar = max(0.0, total - firm)
    if not solar_storage_is_night_sustainable(solar, storage, peak_load_kw):
        return False
    from orchestrator.recoverable_retirement import retire_entities_via_bots

    plan = {"surface": surface, "force": force, "phases": [{
        "name": "retire_bootstrap_steam", "actions": state["retirement_actions"],
    }]}
    retired = retire_entities_via_bots(
        client, bridge, surface, force, plan, "bootstrap_steam", emit,
    )
    if retired <= 0:
        raise RuntimeError("bootstrap steam retirement reported no owned entities")
    try:
        _state_path(getattr(bridge, "script_output", Path(""))).unlink()
    except OSError as error:
        raise RuntimeError("bootstrap steam retired but its state could not be cleared") from error
    emit(
        "BOOTSTRAP STEAM RETIRED: solar="
        f"{solar:.0f}kW storage={storage:.1f}MJ retired={retired}; seed source retained"
    )
    return retired > 0


def _core_placements(plan: dict) -> tuple[tuple[str, Point], ...]:
    return tuple(
        (str(action["entity"]), (float(action["position"]["x"]), float(action["position"]["y"])))
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") in {"boiler", "steam-engine", "inserter", "medium-electric-pole"}
        and action.get("action_type") == "place_ghost"
    )


def _resume_steam_state(
    state: dict, client: object, bridge: object, surface: str, force: str, emit: Emit,
) -> bool:
    """Resubmit the same atomic plan, then join and observe its generated grid."""
    from orchestrator.autonomous_builder import _ensure_plan_construction_coverage, _submit
    from orchestrator.stage_services import extend_power

    plan = state["plan"]
    if state['lifecycle'] == 'prepared':
        _submit(
            client, bridge, surface, plan, state["project_id"], emit,
            stage_coverage=lambda: _ensure_plan_construction_coverage(
                client, bridge, surface, force, plan, emit,
                reserved_tiles=planned_footprint_tiles(plan),
            ),
            require_funded=True,
        )
        state['lifecycle'] = 'submitted'
        _save_state(getattr(bridge, 'script_output', Path('')), state)
    expected = _core_placements(plan)
    signatures = live_base.entity_signatures_at(
        client, surface, force, tuple(position for _entity, position in expected),
    )
    if any(signatures.get(position, {}).get("name") != entity for entity, position in expected):
        emit("BOOTSTRAP STEAM: owned ghosts are still under construction")
        return False
    pole = next(position for entity, position in expected if entity == "medium-electric-pole")
    extend_power(client, bridge, surface, force, pole, emit)
    generation = live_base.network_generation_kw(client, surface, force, pole)
    if generation is None or generation <= 0:
        raise RuntimeError("bootstrap steam grid remained disconnected after pole hookup")
    if not engines_ready(client, surface, force, plan, pole):
        emit("BOOTSTRAP STEAM: waiting for steam at both connected engines; yielding to production")
        return False
    state["lifecycle"] = "active"
    _save_state(getattr(bridge, "script_output", Path("")), state)
    emit(f"BOOTSTRAP STEAM ACTIVE: generated network observed at {generation:.0f}kW")
    return True


def engines_ready(client, surface: str, force: str, plan: dict, pole: Point) -> bool:
    """Observe actual steam and shared grid membership at the exact engines."""
    positions = [position for entity, position in _core_placements(plan) if entity == 'steam-engine']
    encoded = '{' + ','.join('{x=' + str(x) + ',y=' + str(y) + '}' for x, y in positions) + '}'
    query = (
        '/sc local ok,result=pcall(function() '
        f'local s=game.surfaces[{json.dumps(surface)}];local f=game.forces[{json.dumps(force)}];'
        f'local p=s.find_entities_filtered{{type="electric-pole",force=f,position={{{pole[0]},{pole[1]}}},radius=0.1}}[1];'
        'if not p or not p.electric_network_id then return false end;'
        f'for _,pos in ipairs({encoded}) do '
        'local e=s.find_entities_filtered{name="steam-engine",force=f,position=pos,radius=0.1}[1];'
        'if not e or e.electric_network_id~=p.electric_network_id then return false end;'
        'local steam=e.fluidbox[1];if not steam or steam.name~="steam" or steam.amount<=0 '
        'or steam.temperature<100 then return false end end;return true end);'
        'rcon.print(ok and result and "READY" or "WAIT")'
    )
    return len(positions) == 2 and client.command(query).strip() == 'READY'


def measured_steam_need(firm_generation_kw: float, peak_load_kw: float) -> SteamNeed:
    """Return the smallest number of 1.8 MW steam units a real deficit needs."""
    if not all(math.isfinite(value) and value >= 0.0 for value in (
        firm_generation_kw, peak_load_kw,
    )):
        raise ValueError("bootstrap steam requires finite non-negative power telemetry")
    target_kw = max(MIN_OBSERVED_LOAD_KW, peak_load_kw) * SAFETY_MARGIN
    return SteamNeed(
        firm_generation_kw=firm_generation_kw,
        peak_load_kw=peak_load_kw,
        target_kw=target_kw,
        deficit_kw=max(0.0, target_kw - firm_generation_kw),
    )


def recent_consumption_kw(client, surface: str, force: str, near: Point) -> float | None:
    """Measured one-minute grid demand; roboport charging maxima are not load.

    Electric statistics use J/tick. In Factorio 2.1 the input category is
    consumption, output is generation, and storage is a separate energy total.
    """
    query = (
        "/sc local ok,result=pcall(function() "
        f"local s=game.surfaces[{json.dumps(surface)}];local f=game.forces[{json.dumps(force)}];"
        f"local x,y={float(near[0])},{float(near[1])};local best,dist=nil,math.huge;"
        "for _,p in pairs(s.find_entities_filtered{type='electric-pole',force=f}) do "
        "local d=(p.position.x-x)^2+(p.position.y-y)^2;if d<dist then best=p;dist=d end end;"
        "if not best then return nil end;local stats=best.electric_network_statistics;local total=0;"
        "for name,_ in pairs(stats.input_counts) do "
        "if name~='accumulator' then total=total+stats.get_flow_count{name=name,category='input',"
        "precision_index=defines.flow_precision_index.one_minute} end end;return total*60/1000 end);"
        "rcon.print(ok and result and tostring(result) or 'NONE')"
    )
    raw = client.command(query).strip()
    if raw == 'NONE':
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def build_bootstrap_steam_unit(site: dict) -> tuple[dict, Point]:
    """Build one boiler/engine pair beside a verified offshore-pump outlet.

    This is the canonical north-facing Factorio 2.1.17 boiler geometry.  The
    boiler's west/east water ports and north steam port connect to two
    north-facing engines.  A direct belt and inserter feed coal from below.
    """
    output = verified_offshore_pump_output_tile(site)
    if output is None:
        raise ValueError("offshore pump has no verified land-side water output")
    pump_direction = str(site.get("direction", ""))
    if pump_direction == "west":
        boiler = (output[0] + 2.5, float(output[1]))
    elif pump_direction == "east":
        boiler = (output[0] - 1.5, float(output[1]))
    else:
        raise ValueError(
            "Bootstrap steam needs an east/west shoreline for its canonical "
            "north-facing boiler water port"
        )
    water_pipe = (output[0] + 0.5, output[1] + 0.5)
    engine_one = (boiler[0], boiler[1] - 3.5)
    engine_two = (boiler[0], boiler[1] - 8.5)
    fuel_inserter = (boiler[0], boiler[1] + 1.5)
    fuel_belt = (boiler[0], boiler[1] + 2.5)
    poles = (
        (boiler[0] + 3.5, engine_one[1]),
        (boiler[0] + 3.5, engine_two[1]),
        (boiler[0] + 3.5, fuel_inserter[1]),
    )
    plan = {
        "surface": "nauvis",
        "force": "player",
        "atomic": True,
        "power_contract": {
            "kind": "bootstrap-steam-v1", "boilers": 1, "steam_engines": 2,
        },
        "phases": [
            {"name": "bootstrap_steam_water_and_generation", "actions": [
                {"action_type": "place_ghost", "entity": "offshore-pump",
                 "position": {"x": site["position"][0], "y": site["position"][1]},
                 "direction": site["direction"]},
                {"action_type": "place_ghost", "entity": "pipe",
                 "position": {"x": water_pipe[0], "y": water_pipe[1]}},
                {"action_type": "place_ghost", "entity": "boiler",
                 "position": {"x": boiler[0], "y": boiler[1]},
                 "direction": "north"},
                {"action_type": "place_ghost", "entity": "steam-engine",
                 "position": {"x": engine_one[0], "y": engine_one[1]},
                 "direction": "north"},
                {"action_type": "place_ghost", "entity": "steam-engine",
                 "position": {"x": engine_two[0], "y": engine_two[1]},
                 "direction": "north"},
                *[
                    {"action_type": "place_ghost", "entity": "medium-electric-pole",
                     "position": {"x": x, "y": y}}
                    for x, y in poles
                ],
            ]},
            {"name": "bootstrap_steam_direct_coal_feed", "actions": [
                {"action_type": "place_ghost", "entity": "transport-belt",
                 "position": {"x": fuel_belt[0], "y": fuel_belt[1]},
                 "direction": "south"},
                {"action_type": "place_ghost", "entity": "inserter",
                 "position": {"x": fuel_inserter[0], "y": fuel_inserter[1]},
                 "direction": "south"},
            ]},
        ],
    }
    validate_build_plan(plan)
    return plan, fuel_belt


def maybe_ensure_bootstrap_steam_power(
    client: object, bridge: object, surface: str, force: str,
    reference_point: Point, emit: Emit,
) -> bool:
    """Submit one funded steam unit when measured firm power is insufficient.

    A false return means no capacity action was safe.  Material shortages and
    typed construction deferrals intentionally propagate to the normal work
    scheduler, which retains their exact bill and lifecycle state.
    """
    # Narrow unit harnesses model only the solar policy and have no live RCON
    # surface to survey.  Steam must never infer a deficit from absent evidence.
    if not hasattr(client, "command"):
        return False
    try:
        firm = live_base.network_firm_generation_kw(
            client, surface, force, reference_point,
        )
        peak = recent_consumption_kw(client, surface, force, reference_point)
    except live_base.TelemetryError as error:
        emit(f"BOOTSTRAP STEAM DEFERRED: invalid power telemetry: {error}")
        return False
    if firm is None or peak is None:
        emit("BOOTSTRAP STEAM DEFERRED: no measured powered network is available")
        return False
    if _retire_if_solar_is_ready(
        client, bridge, surface, force, reference_point, peak, emit,
    ):
        return True
    state = _load_state(
        getattr(bridge, "script_output", Path("")), surface, force,
        episode_id=getattr(bridge, "episode_id", None),
    )
    if state is not None:
        if state["lifecycle"] == "active":
            return False
        return _resume_steam_state(state, client, bridge, surface, force, emit)
    need = measured_steam_need(firm, peak)
    if need.required_units <= 0:
        emit(
            "BOOTSTRAP STEAM not needed: "
            f"firm={firm:.0f}kW target={need.target_kw:.0f}kW"
        )
        return False

    # A coal mine is itself a fully funded, bot-built primitive.  Reuse it
    # when present; when absent, the existing coal-stage controller submits
    # that prerequisite and returns None for a fresh observation next pass.
    from orchestrator.stage_chemical import ensure_coal_mine
    from orchestrator.autonomous_builder import _ensure_plan_construction_coverage, _submit, bring_stage_up

    coal_source = ensure_coal_mine(
        client, bridge, surface, force, reference_point, bring_stage_up, emit,
    )
    if coal_source is None:
        emit("BOOTSTRAP STEAM deferred: coal mine construction is in flight")
        return True
    site = chemical_survey.nearest_offshore_pump_site(
        client, surface, reference_point, allowed_directions=("east", "west"),
    )
    if site is None:
        emit("BOOTSTRAP STEAM DEFERRED: no legal shoreline was found")
        return False
    plan, fuel_belt = build_bootstrap_steam_unit(site)
    plan["surface"], plan["force"] = surface, force
    route = preflight_ingredient_transport(
        client, surface, force, "bootstrap-steam", "coal", coal_source,
        fuel_belt, 1, max_belt_route_tiles=MAX_COAL_ROUTE_TILES,
        additional_blocked=planned_footprint_tiles(plan), mode="belt",
        destination_is_belt=True,
        destination_belt_direction="south",
    )
    if route is None:
        raise RuntimeError("bootstrap steam requires a direct coal belt route")
    route_actions, belt_type = route
    plan["phases"].append({
        "name": "bridge_coal_to_bootstrap_steam",
        "actions": route_actions,
    })
    validate_build_plan(plan)
    emit(
        "BOOTSTRAP STEAM: measured deficit "
        f"{need.deficit_kw:.0f}kW; submitting one 1800kW boiler unit "
        f"with direct {belt_type} coal feed"
    )
    retirement_entities = {"offshore-pump", "pipe", "boiler", "steam-engine",
                           "transport-belt", "inserter"}
    retirement_actions = [
        {
            "action_type": "remove_entity", "entity": action["entity"],
            "position": dict(action["position"]),
        }
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") in retirement_entities
        and action.get("action_type") == "place_ghost"
        # The coal bridge remains: it may become a useful branch for plastic,
        # whereas local water/fuel hardware is exact bootstrap ownership.
        and phase["name"] != "bridge_coal_to_bootstrap_steam"
    ]
    script_output = getattr(bridge, "script_output", Path(""))
    project_id = "bootstrap-steam:" + str(getattr(bridge, "episode_id", "unscoped"))
    _save_state(script_output, {
        "version": 1, "surface": surface, "force": force,
        "episode_id": getattr(bridge, "episode_id", None), "lifecycle": "prepared",
        "project_id": project_id, "plan": plan, "plan_sha256": _plan_sha256(plan),
        "retirement_actions": retirement_actions,
    })
    try:
        _submit(
            client, bridge, surface, plan, project_id, emit,
            stage_coverage=lambda: _ensure_plan_construction_coverage(
                client, bridge, surface, force, plan, emit,
                reserved_tiles=planned_footprint_tiles(plan),
            ),
            require_funded=True,
        )
    except Exception:
        # Atomic submission either placed no partial infrastructure or has an
        # exact persisted retirement bill for the next reconciliation.  Keep
        # the state rather than risking a duplicate burner block after restart.
        raise
    _save_state(script_output, {
        "version": 1, "surface": surface, "force": force,
        "episode_id": getattr(bridge, "episode_id", None), "lifecycle": "submitted",
        "project_id": project_id, "plan": plan, "plan_sha256": _plan_sha256(plan),
        "retirement_actions": retirement_actions,
    })
    return True
