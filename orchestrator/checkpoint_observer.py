# Path: orchestrator/checkpoint_observer.py | Purpose: Injectable structured live evidence adapters for checkpoint runs.
"""Structured observation seams used by deterministic checkpoint monitors.

The monitor must never infer a milestone from runner log text.  This module
keeps the transport boundary deliberately small: live runners use read-only
RCON surveys, while tests and replay tools can inject mappings or snapshots
directly.  Unsupported telemetry remains an explicit infrastructure result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from orchestrator.checkpoint_milestones import FactorySnapshot
from orchestrator import live_base
from orchestrator.baseline_production import BASELINE_MACHINES
from orchestrator.bootstrap_district import (
    BootstrapDistrictLedger, BootstrapDistrictState, BootstrapLifecycleError,
)
from planners.bootstrap_smelting import direct_smelter_positions
from planners.recipe_data import LINE_RECIPES


class ObservationUnsupported(RuntimeError):
    """The live surface cannot provide the structured checkpoint evidence."""

    def __init__(self, message: str, *, evidence: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.evidence = dict(evidence or {})


@dataclass(frozen=True)
class StructuredObservation:
    """One normalized live observation and its transport provenance."""

    snapshot: FactorySnapshot
    observed_at: str
    source: str
    raw: Mapping[str, Any]

    def as_mapping(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at,
            "source": self.source,
            "snapshot": self.raw,
        }


class ObservationAdapter(Protocol):
    """Minimal boundary required by :class:`CheckpointRunMonitor`."""

    def observe(self, **_context: Any) -> StructuredObservation:
        ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize(value: FactorySnapshot | Mapping[str, Any], *, source: str) -> StructuredObservation:
    if isinstance(value, FactorySnapshot):
        snapshot = value
        raw: Mapping[str, Any] = {"tick": snapshot.tick}
    elif isinstance(value, Mapping):
        raw = dict(value)
        snapshot = FactorySnapshot.from_mapping(raw)
    else:
        raise ObservationUnsupported(
            "structured observation provider returned an unsupported value",
            evidence={"type": type(value).__name__, "source": source},
        )
    return StructuredObservation(snapshot=snapshot, observed_at=_now(), source=source, raw=raw)


class CallableObservationAdapter:
    """Adapt an injected callable without coupling tests to RCON."""

    def __init__(self, provider: Callable[[], FactorySnapshot | Mapping[str, Any]], *, source: str = "injected") -> None:
        self._provider = provider
        self._source = source

    def observe(self, **_context: Any) -> StructuredObservation:
        try:
            value = self._provider()
        except ObservationUnsupported:
            raise
        except Exception as error:
            raise ObservationUnsupported(
                f"structured observation provider failed: {type(error).__name__}: {error}",
                evidence={"source": self._source, "exception_type": type(error).__name__},
            ) from error
        return _normalize(value, source=self._source)


class FileObservationAdapter:
    """Read a structured JSON report emitted by an isolated lane adapter.

    This is useful for replay and for a future Lua report writer.  It never
    reads runner logs and it does not treat a missing or malformed report as a
    failed predicate; the monitor records the explicit unsupported evidence.
    """

    def __init__(self, path: Path, *, source: str = "structured-file") -> None:
        self.path = Path(path).resolve()
        self.source = source

    def observe(self, **_context: Any) -> StructuredObservation:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ObservationUnsupported(
                f"structured observation report is missing: {self.path}",
                evidence={"path": str(self.path), "reason": "missing"},
            ) from error
        except (OSError, json.JSONDecodeError) as error:
            raise ObservationUnsupported(
                f"structured observation report is unreadable: {self.path}",
                evidence={"path": str(self.path), "reason": "unreadable", "exception_type": type(error).__name__},
            ) from error
        if not isinstance(payload, Mapping):
            raise ObservationUnsupported(
                "structured observation report must contain an object",
                evidence={"path": str(self.path), "reason": "not-an-object"},
            )
        return _normalize(payload, source=self.source)


class GameBridgeObservationAdapter:
    """Use an explicit structured-report method on a game bridge.

    ``GameBridge`` currently exposes legacy snapshots, inventories, and
    reports, but not the complete checkpoint telemetry contract.  Silently
    mapping those partial reports would make milestone acceptance unsafe, so
    this adapter requires an explicitly named ``request_checkpoint_observation``
    method or an injected provider.
    """

    def __init__(self, bridge: Any, *, provider: Callable[[], FactorySnapshot | Mapping[str, Any]] | None = None) -> None:
        self.bridge = bridge
        self._provider = provider

    def observe(self, **_context: Any) -> StructuredObservation:
        provider = self._provider or getattr(self.bridge, "request_checkpoint_observation", None)
        if not callable(provider):
            raise ObservationUnsupported(
                "GameBridge has no structured checkpoint observation API; refusing log/snapshot inference",
                evidence={
                    "adapter": type(self).__name__,
                    "bridge_type": type(self.bridge).__name__,
                    "required_method": "request_checkpoint_observation",
                },
            )
        return CallableObservationAdapter(provider, source="game-bridge-structured").observe()


class UnsupportedObservationAdapter:
    """Explicit fail-closed adapter used when no live telemetry is configured."""

    def observe(self, **_context: Any) -> StructuredObservation:
        raise ObservationUnsupported(
            "no structured checkpoint observation adapter is configured",
            evidence={"reason": "adapter-not-configured"},
        )


class LiveCheckpointObservationAdapter:
    """Build a checkpoint snapshot from the live read-only RCON surface.

    This adapter deliberately uses the same narrow surveys as the
    deterministic builder: entity lines, exact starter geometry, machine
    health, and force production statistics.  It does not inspect runner
    logs.  A missing entity is measured negative evidence; an unavailable or
    malformed telemetry call raises :class:`ObservationUnsupported`, keeping
    infrastructure failure distinct from a failed checkpoint predicate.

    The RCON client is bound lazily by ``autonomous_run`` because the monitor
    is created from the manifest before the builder opens its connection.
    """

    _STARTERS = (
        ("iron", "iron-plate", "iron-ore"),
        ("copper", "copper-plate", "copper-ore"),
        ("stone", "stone-brick", "stone"),
    )
    _FOUNDATION_ORES = {
        "iron": "iron-ore",
        "copper": "copper-ore",
        "stone": "stone",
    }

    def __init__(
        self,
        client: Any | None = None,
        bridge: Any | None = None,
        *,
        surface: str = "nauvis",
        force: str = "player",
        reference_point: tuple[float, float] = (0.0, 0.0),
        bootstrap_profile: str = "reduced-v1",
    ) -> None:
        self.client = client
        self.bridge = bridge
        self.surface = surface
        self.force = force
        self.reference_point = reference_point
        self.bootstrap_profile = bootstrap_profile

    def bind(self, client: Any, bridge: Any | None = None) -> "LiveCheckpointObservationAdapter":
        """Bind a runner's already-open read-only clients and return self."""
        self.client = client
        self.bridge = bridge
        return self

    @staticmethod
    def _status_healthy(status: str | None) -> bool | None:
        if status is None:
            return None
        if status in {"missing", "unknown", "disabled", "marked-for-deconstruction"}:
            return False
        return True

    def _require_client(self) -> Any:
        if self.client is None or not callable(getattr(self.client, "command", None)):
            raise ObservationUnsupported(
                "live checkpoint observation has no bound RCON client",
                evidence={"adapter": type(self).__name__, "reason": "client-not-bound"},
            )
        return self.client

    def _foundation_survey(
        self, client: Any, districts: Mapping[str, BootstrapDistrictState] | None,
    ) -> dict[str, dict[str, Any]]:
        """Count real furnaces at the episode's exact replacement sites.

        Inventory/recipe visibility is transient, whereas the persisted
        district identifies even an empty furnace that has never crafted.
        Missing ownership stays unknown; it must not adopt nearby furnaces.
        Counters belong to the current live entities, not an immortal district
        total. Keep their IDs so removal/replacement remains distinguishable.
        """
        result = {
            name: {
                "machine_count": 0 if districts is not None else None,
                "healthy": False if districts is not None else None,
                "producing": False if districts is not None else None,
                "output_count": 0 if districts is not None else None,
                "working_count": 0 if districts is not None else None,
                "identity_source": "no-district" if districts is not None else "unavailable",
                "counter_basis": None,
                "machines": [],
            }
            for name in self._FOUNDATION_ORES
        }
        sites: list[tuple[str, str, float, float]] = []
        owned: set[tuple[float, float]] = set()
        for name, recipe, _ore in self._STARTERS:
            district = (districts or {}).get(recipe)
            if district is None:
                continue
            result[name].update(
                district_id=district.district_id, district_revision=district.revision,
                identity_source="bootstrap-replacement-v1", counter_basis="live-entity-products-finished",
            )
            machine = LINE_RECIPES[recipe]["machine"]
            for action in district.replacement_actions:
                if action.get("entity") != machine:
                    continue
                position = action["position"]
                point = (float(position["x"]), float(position["y"]))
                if point in owned:
                    raise ObservationUnsupported(
                        "foundation ownership contains duplicate furnace sites",
                        evidence={"survey": "foundations", "position": point},
                    )
                owned.add(point)
                sites.append((name, machine, *point))
        if not sites:
            return result

        surface = self.surface.replace("\\", "\\\\").replace("'", "\\'")
        force = self.force.replace("\\", "\\\\").replace("'", "\\'")
        positions = ",".join(
            "{" + str(x) + "," + str(y) + "," + json.dumps(machine) + "}"
            for _, machine, x, y in sites
        )
        lua = (
            "/sc local s=game.surfaces['" + surface + "'];"
            "local f=game.forces['" + force + "'];local names={};"
            "for k,v in pairs(defines.entity_status) do names[v]=k end;local out={};"
            "for i,p in ipairs({" + positions + "}) do local found=nil;"
            "for _,e in pairs(s.find_entities_filtered{position=p,radius=0.4,"
            "name=p[3],force=f}) do "
            "if e.type~='entity-ghost' and math.abs(e.position.x-p[1])<0.01 and "
            "math.abs(e.position.y-p[2])<0.01 then found=e break end end;"
            "local unit='';local status='missing';local made='';"
            "if found then unit=found.unit_number;status=names[found.status] or 'unknown';"
            "if found.to_be_deconstructed() then status='marked-for-deconstruction' end;"
            "local ok,n=pcall(function() return found.products_finished end);"
            "if ok and type(n)=='number' then made=n end end;"
            "out[#out+1]=i..'|'..unit..'|'..status..'|'..made end;"
            "rcon.print(table.concat(out,';'))"
        )
        try:
            raw = str(client.command(lua)).strip()
        except Exception as error:
            raise ObservationUnsupported(
                f"foundation telemetry request failed: {type(error).__name__}: {error}",
                evidence={"adapter": type(self).__name__, "survey": "foundations"},
            ) from error
        try:
            seen_sites: set[int] = set()
            seen_units: set[int] = set()
            for row in raw.split(";"):
                fields = row.split("|")
                if len(fields) != 4:
                    raise ValueError(f"expected 4 fields, got {len(fields)}")
                index_raw, unit_raw, status, made_raw = fields
                index = int(index_raw)
                if not 1 <= index <= len(sites) or index in seen_sites:
                    raise ValueError("invalid or duplicate foundation site")
                seen_sites.add(index)
                name, _machine, x, y = sites[index - 1]
                if status == "missing":
                    if unit_raw or made_raw:
                        raise ValueError("missing furnace has live evidence")
                    continue
                unit = int(unit_raw)
                made = int(made_raw) if made_raw else None
                if unit <= 0 or unit in seen_units or (made is not None and made < 0):
                    raise ValueError("invalid or duplicate foundation entity")
                seen_units.add(unit)
                evidence = result[name]
                evidence["machines"].append({
                    "unit_number": unit, "position": {"x": x, "y": y},
                    "status": status, "products_finished": made,
                })
                evidence["machine_count"] += 1
                evidence["healthy"] = bool(evidence["healthy"] or self._status_healthy(status))
                evidence["producing"] = bool(evidence["producing"] or status == "working")
                evidence["working_count"] += int(status == "working")
                evidence["output_count"] = (
                    evidence["output_count"] + made
                    if evidence["output_count"] is not None and made is not None else None
                )
            if len(seen_sites) != len(sites):
                raise ValueError("incomplete foundation survey")
        except (TypeError, ValueError) as error:
            raise ObservationUnsupported(
                "foundation telemetry response is malformed",
                evidence={"adapter": type(self).__name__, "survey": "foundations", "response": raw[:200]},
            ) from error
        # Preserve the v1 predicate's has-produced semantics. This lifetime
        # counter is not a rate; working_count separately records current work.
        for evidence in result.values():
            if evidence["output_count"] is not None and evidence["output_count"] > 0:
                evidence["producing"] = True
        return result

    def observe(
        self, client: Any | None = None, bridge: Any | None = None,
    ) -> StructuredObservation:
        # The runner monitor supplies the already-open clients at live
        # boundaries.  Rebinding here keeps direct adapter users and monitor
        # users on the same read-only connection.
        if client is not None:
            self.bind(client, bridge)
        client = self._require_client()
        try:
            tick = live_base.game_tick(client)
        except Exception as error:
            raise ObservationUnsupported(
                f"live checkpoint tick survey failed: {type(error).__name__}: {error}",
                evidence={"adapter": type(self).__name__, "survey": "game_tick"},
            ) from error

        starters: dict[str, dict[str, Any]] = {}
        district_states: dict[str, BootstrapDistrictState] | None = None
        if self.bridge is not None and getattr(self.bridge, "episode_id", None):
            try:
                ledger = BootstrapDistrictLedger(
                    self.bridge.script_output,
                    episode_id=self.bridge.episode_id,
                    surface=self.surface,
                    force=self.force,
                    bootstrap_profile=self.bootstrap_profile,
                )
                # A legacy/resumed episode may have no ownership sidecar.
                # Absence of that source cannot prove absence of its furnaces.
                if ledger.root.is_dir():
                    district_states = {state.recipe: state for state in ledger.states()}
            except (OSError, BootstrapLifecycleError) as error:
                raise ObservationUnsupported(
                    f"bootstrap district telemetry is unreadable: {type(error).__name__}: {error}",
                    evidence={"adapter": type(self).__name__, "survey": "bootstrap-districts"},
                ) from error
        for name, recipe, ore in self._STARTERS:
            try:
                starter = live_base.direct_plate_starter(
                    client, self.surface, self.force, recipe, ore, self.reference_point,
                )
            except Exception as error:
                raise ObservationUnsupported(
                    f"starter telemetry request failed for {name}: {type(error).__name__}: {error}",
                    evidence={"adapter": type(self).__name__, "survey": "starter", "starter": name},
                ) from error
            if starter is None:
                district = (district_states or {}).get(recipe)
                retiring = district is not None and district.lifecycle_state != "released"
                starters[name] = {
                    # The exact recognizer stops matching during partial
                    # deconstruction. The durable lifecycle prevents that
                    # transient shape from being misreported as fully absent.
                    "resource": name, "present": bool(retiring), "healthy": False,
                    "producing": False, "output_count": 0,
                }
                continue
            positions = direct_smelter_positions(
                starter.drill_position, starter.output_direction,
                pole_side=starter.pole_side,
                drill_count=2 if name == "iron" or starter.additional_drill_positions else 1,
                furnace_count=2 if name == "iron" else 1,
            )
            furnace_positions = [positions["furnace"]]
            if "secondary_furnace" in positions:
                furnace_positions.append(positions["secondary_furnace"])
            try:
                statuses, counters = live_base.machine_health(
                    client, self.surface, furnace_positions,
                )
            except Exception as error:
                raise ObservationUnsupported(
                    f"starter health survey failed for {name}: {type(error).__name__}: {error}",
                    evidence={"adapter": type(self).__name__, "survey": "starter-health", "starter": name},
                ) from error
            status_values = [statuses.get(position) for position in furnace_positions]
            starters[name] = {
                "resource": name,
                "present": True,
                "healthy": all(self._status_healthy(status) is True for status in status_values),
                "producing": any(status == "working" for status in status_values),
                "output_count": int(sum(counters.get(position, 0.0) for position in furnace_positions)),
            }

        mall: list[dict[str, Any]] = []
        for recipe in BASELINE_MACHINES:
            spec = LINE_RECIPES.get(recipe)
            if spec is None:
                continue
            try:
                line = live_base.find_line(
                    client, self.surface, self.force, recipe, spec["machine"],
                    include_ghosts=False,
                )
                if line is None:
                    continue
                statuses, counters = live_base.machine_health(
                    client, self.surface, line.machine_positions,
                )
            except Exception as error:
                raise ObservationUnsupported(
                    f"mall telemetry request failed for {recipe}: {type(error).__name__}: {error}",
                    evidence={"adapter": type(self).__name__, "survey": "mall", "recipe": recipe},
                ) from error
            for position in line.machine_positions:
                status = statuses.get(position)
                mall.append({
                    "recipe": recipe,
                    "working": None if status is None else status == "working",
                    "healthy": self._status_healthy(status),
                    "producing": None if status is None else status == "working",
                    "craft_count": int(counters[position]) if position in counters else None,
                    "name": recipe,
                    "status": status,
                })

        production: dict[str, dict[str, int]] = {}
        try:
            available_items = live_base.available_items(
                client, self.surface, self.force,
            )
        except Exception as error:
            raise ObservationUnsupported(
                f"provider inventory survey failed: {type(error).__name__}: {error}",
                evidence={"adapter": type(self).__name__, "survey": "provider-inventory"},
            ) from error
        providers: dict[str, dict[str, int]] = {}
        for item in ("plastic-bar", "advanced-circuit"):
            spec = LINE_RECIPES.get(item)
            line = None
            if spec is not None:
                try:
                    line = live_base.find_line(
                        client, self.surface, self.force, item, spec["machine"],
                        include_ghosts=False,
                    )
                except Exception as error:
                    raise ObservationUnsupported(
                        f"production telemetry request failed for {item}: {type(error).__name__}: {error}",
                        evidence={"adapter": type(self).__name__, "survey": "production", "item": item},
                    ) from error
            produced = int(line.produced_count) if line is not None else 0
            production[item] = {"produced": produced}
            # Force production statistics are not provider delivery.  Keep
            # the structured inventory aggregate under its honest field name.
            provider_evidence: dict[str, int] = {
                "available": int(available_items.get(item, 0)),
            }
            providers[item] = provider_evidence

        recipes: dict[str, dict[str, Any]] = {}
        for recipe, spec in LINE_RECIPES.items():
            ingredients = tuple(str(item) for item in spec.get("ingredients", ()))
            if "advanced-circuit" not in ingredients:
                continue
            try:
                line = live_base.find_line(
                    client, self.surface, self.force, recipe, spec["machine"],
                    include_ghosts=False,
                )
            except Exception as error:
                raise ObservationUnsupported(
                    f"consumer telemetry request failed for {recipe}: {type(error).__name__}: {error}",
                    evidence={"adapter": type(self).__name__, "survey": "advanced-circuit-consumer", "recipe": recipe},
                ) from error
            recipes[recipe] = {
                "name": recipe,
                "owned": line is not None,
                "ingredients": list(ingredients),
                "craft_count": int(line.produced_count) if line is not None else 0,
            }

        snapshot = {
            "tick": tick,
            "base_valid": True,
            "starters": starters,
            "mall_assemblers": mall,
            "foundations": self._foundation_survey(client, district_states),
            "production": production,
            "providers": providers,
            "recipes": recipes,
            "plastic_startup": self._plastic_startup(client),
        }
        return _normalize(snapshot, source="live-rcon-structured")

    def _plastic_startup(self, client: Any) -> Mapping[str, Any] | None:
        """Arm/read the bounded game-clock sampler for this logical episode.

        Missing sampler evidence affects C5 alone. Observing an old freeze
        cannot retroactively invent its startup rates.
        """
        run_id = getattr(self.bridge, "episode_id", None)
        if not run_id:
            return None
        args = ",".join(json.dumps(value) for value in
                        (self.surface, self.force, "plastic-bar", run_id))
        command = ('/sc if remote.interfaces.production_startup then '
                   'rcon.print(helpers.table_to_json(remote.call("production_startup","read",'
                   + args + '))) else rcon.print("null") end')
        try:
            result = json.loads(client.command(command))
        except (ValueError, TypeError, OSError):
            return None
        if not isinstance(result, Mapping):
            return None
        if (result.get("surface"), result.get("force"), result.get("run_id")) != (self.surface, self.force, run_id):
            return None
        # Factorio encodes an empty Lua table as {}, not necessarily [].
        if result.get("samples") == {}:
            result["samples"] = []
        return result


__all__ = [
    "CallableObservationAdapter",
    "FileObservationAdapter",
    "GameBridgeObservationAdapter",
    "LiveCheckpointObservationAdapter",
    "ObservationAdapter",
    "ObservationUnsupported",
    "StructuredObservation",
    "UnsupportedObservationAdapter",
]
