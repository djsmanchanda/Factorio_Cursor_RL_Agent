# Path: orchestrator/bootstrap_district.py
# Purpose: Persist the build-validate-retire lifecycle and reserved footprint of each bootstrap resource district.

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Sequence

Point = tuple[float, float]
Tile = tuple[int, int]
LIFECYCLE_STATES = ("pioneer", "provisioning", "validating", "retiring", "released")
REQUIRED_RESERVATION_ROLES = frozenset({
    "mine_growth", "refinery_growth", "transport_service",
    "power_service", "roboport_service",
})
_TRANSITIONS = {
    "pioneer": {"pioneer", "provisioning"},
    "provisioning": {"provisioning", "validating"},
    "validating": {"validating", "retiring"},
    "retiring": {"retiring", "released"},
    "released": {"released"},
}


class BootstrapLifecycleError(RuntimeError):
    """Persisted bootstrap identity or lifecycle progress is contradictory."""


def _point(value: Sequence[object]) -> Point:
    if len(value) != 2:
        raise BootstrapLifecycleError("Bootstrap position must contain x and y")
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError) as error:
        raise BootstrapLifecycleError("Bootstrap position must be numeric") from error


def _tile(value: Sequence[object]) -> Tile:
    if len(value) != 2 or not all(isinstance(item, int) for item in value):
        raise BootstrapLifecycleError("Bootstrap reservation tile must contain two integers")
    return int(value[0]), int(value[1])


def _action_key(action: Mapping[str, object]) -> tuple[str, Point, str]:
    position = action.get("position")
    if not isinstance(position, Mapping):
        raise BootstrapLifecycleError("Owned bootstrap action has no position")
    entity = action.get("entity")
    action_type = action.get("action_type")
    if not isinstance(entity, str) or not entity:
        raise BootstrapLifecycleError("Owned bootstrap action has no entity")
    if action_type not in {"place_entity", "place_ghost"}:
        raise BootstrapLifecycleError("Bootstrap ownership may contain placements only")
    direction = action.get("direction")
    return (
        entity,
        _point((position.get("x"), position.get("y"))),
        direction if isinstance(direction, str) else "",
    )


def _canonical_actions(actions: Sequence[Mapping[str, object]]) -> tuple[dict, ...]:
    copied = tuple(json.loads(json.dumps(action, sort_keys=True)) for action in actions)
    keys = [_action_key(action) for action in copied]
    if len(keys) != len(set(keys)):
        raise BootstrapLifecycleError("Bootstrap ownership actions must be unique")
    return tuple(sorted(copied, key=lambda action: _action_key(action)))


@dataclass(frozen=True)
class BootstrapDistrictState:
    episode_id: str
    surface: str
    force: str
    bootstrap_profile: str
    recipe: str
    ore: str
    district_id: str
    lifecycle_state: str
    pioneer_actions: tuple[dict, ...]
    reservations: Mapping[str, frozenset[Tile]]
    replacement_origin: Point | None = None
    replacement_provider: Point | None = None
    replacement_furnaces: int = 0
    replacement_actions: tuple[dict, ...] = ()
    transport_source: Point | None = None
    transport_actions: tuple[dict, ...] = ()
    measured_output_count: int = 0
    revision: int = 1
    history: tuple[dict, ...] = ()
    version: int = 1

    def __post_init__(self) -> None:
        if self.version != 1:
            raise BootstrapLifecycleError(f"Unsupported bootstrap district version {self.version}")
        for label, value in {
            "episode_id": self.episode_id, "surface": self.surface,
            "force": self.force, "bootstrap_profile": self.bootstrap_profile,
            "recipe": self.recipe, "ore": self.ore, "district_id": self.district_id,
        }.items():
            if not isinstance(value, str) or not value:
                raise BootstrapLifecycleError(f"{label} must be non-empty")
        if self.lifecycle_state not in LIFECYCLE_STATES:
            raise BootstrapLifecycleError(f"Unknown bootstrap lifecycle state {self.lifecycle_state!r}")
        if self.revision < 1 or self.replacement_furnaces < 0 or self.measured_output_count < 0:
            raise BootstrapLifecycleError("Bootstrap counts and revision cannot be negative")
        if not self.pioneer_actions:
            raise BootstrapLifecycleError("Bootstrap district requires owned pioneer actions")
        _canonical_actions(self.pioneer_actions)
        _canonical_actions(self.replacement_actions)
        _canonical_actions(self.transport_actions)
        if bool(self.transport_source) != bool(self.transport_actions):
            raise BootstrapLifecycleError(
                "Bootstrap transport source and actions must be persisted together"
            )
        if self.lifecycle_state != "pioneer":
            if self.replacement_origin is None or self.replacement_provider is None:
                raise BootstrapLifecycleError("Provisioned bootstrap district requires replacement geometry")
            if self.replacement_furnaces <= 0 or not self.replacement_actions:
                raise BootstrapLifecycleError("Provisioned bootstrap district requires owned replacement actions")
            if (
                set(self.reservations) != REQUIRED_RESERVATION_ROLES
                or not all(self.reservations.values())
            ):
                raise BootstrapLifecycleError("Provisioned bootstrap district requires full reservations")
        if self.lifecycle_state in {"retiring", "released"} and self.measured_output_count <= 0:
            raise BootstrapLifecycleError("Starter retirement requires measured replacement output")

    def to_json(self) -> str:
        payload = {
            "version": self.version,
            "episode_id": self.episode_id,
            "surface": self.surface,
            "force": self.force,
            "bootstrap_profile": self.bootstrap_profile,
            "recipe": self.recipe,
            "ore": self.ore,
            "district_id": self.district_id,
            "lifecycle_state": self.lifecycle_state,
            "pioneer_actions": list(self.pioneer_actions),
            "reservations": {
                name: [list(tile) for tile in sorted(tiles)]
                for name, tiles in sorted(self.reservations.items())
            },
            "replacement_origin": list(self.replacement_origin) if self.replacement_origin else None,
            "replacement_provider": list(self.replacement_provider) if self.replacement_provider else None,
            "replacement_furnaces": self.replacement_furnaces,
            "replacement_actions": list(self.replacement_actions),
            "transport_source": list(self.transport_source) if self.transport_source else None,
            "transport_actions": list(self.transport_actions),
            "measured_output_count": self.measured_output_count,
            "revision": self.revision,
            "history": list(self.history),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, encoded: str) -> BootstrapDistrictState:
        try:
            payload = json.loads(encoded)
            reservations = {
                str(name): frozenset(_tile(tile) for tile in tiles)
                for name, tiles in payload["reservations"].items()
            }
            return cls(
                episode_id=str(payload["episode_id"]),
                surface=str(payload["surface"]),
                force=str(payload["force"]),
                bootstrap_profile=str(payload["bootstrap_profile"]),
                recipe=str(payload["recipe"]),
                ore=str(payload["ore"]),
                district_id=str(payload["district_id"]),
                lifecycle_state=str(payload["lifecycle_state"]),
                pioneer_actions=_canonical_actions(payload["pioneer_actions"]),
                reservations=reservations,
                replacement_origin=(
                    _point(payload["replacement_origin"])
                    if payload.get("replacement_origin") is not None else None
                ),
                replacement_provider=(
                    _point(payload["replacement_provider"])
                    if payload.get("replacement_provider") is not None else None
                ),
                replacement_furnaces=int(payload["replacement_furnaces"]),
                replacement_actions=_canonical_actions(payload["replacement_actions"]),
                transport_source=(
                    _point(payload["transport_source"])
                    if payload.get("transport_source") is not None else None
                ),
                transport_actions=_canonical_actions(payload.get("transport_actions", ())),
                measured_output_count=int(payload["measured_output_count"]),
                revision=int(payload["revision"]),
                history=tuple(payload.get("history", ())),
                version=int(payload["version"]),
            )
        except BootstrapLifecycleError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise BootstrapLifecycleError("Malformed persisted bootstrap district state") from error


def _district_id(episode_id: str, surface: str, force: str, recipe: str, ore: str) -> str:
    encoded = json.dumps(
        {"episode_id": episode_id, "surface": surface, "force": force,
         "recipe": recipe, "ore": ore},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "bootstrap-" + hashlib.sha256(encoded).hexdigest()[:16]


class BootstrapDistrictLedger:
    """Episode-scoped state store for iron, copper, and stone bootstrap districts."""

    def __init__(
        self, script_output: Path | str, *, episode_id: str, surface: str,
        force: str, bootstrap_profile: str,
    ) -> None:
        self.root = (
            Path(script_output).parent / "logs" / "deterministic-bootstrap-districts"
            / episode_id
        )
        self.episode_id = episode_id
        self.surface = surface
        self.force = force
        self.bootstrap_profile = bootstrap_profile

    def _path(self, recipe: str) -> Path:
        token = hashlib.sha256(recipe.encode("utf-8")).hexdigest()[:16]
        return self.root / f"{token}.json"

    def load(self, recipe: str) -> BootstrapDistrictState | None:
        path = self._path(recipe)
        if not path.is_file():
            return None
        try:
            state = BootstrapDistrictState.from_json(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise BootstrapLifecycleError(f"Bootstrap district is unreadable: {path}") from error
        if (
            state.episode_id != self.episode_id or state.surface != self.surface
            or state.force != self.force or state.recipe != recipe
            or state.bootstrap_profile != self.bootstrap_profile
        ):
            raise BootstrapLifecycleError("Persisted bootstrap district scope does not match this run")
        return state

    def states(self) -> tuple[BootstrapDistrictState, ...]:
        if not self.root.is_dir():
            return ()
        states = tuple(
            BootstrapDistrictState.from_json(path.read_text(encoding="utf-8"))
            for path in sorted(self.root.glob("*.json"))
        )
        if any(
            state.episode_id != self.episode_id
            or state.surface != self.surface
            or state.force != self.force
            or state.bootstrap_profile != self.bootstrap_profile
            for state in states
        ):
            raise BootstrapLifecycleError(
                "Persisted bootstrap district collection contains another run scope"
            )
        return states

    def _save(self, state: BootstrapDistrictState) -> BootstrapDistrictState:
        path = self._path(state.recipe)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(state.to_json() + "\n", encoding="utf-8")
        temporary.replace(path)
        return state

    @staticmethod
    def _history(state: BootstrapDistrictState, next_state: str, event: str) -> tuple[dict, ...]:
        return (*state.history, {
            "revision": state.revision + 1,
            "event": event,
            "from": state.lifecycle_state,
            "to": next_state,
        })

    def _transition(
        self, state: BootstrapDistrictState, next_state: str, event: str,
        **changes: object,
    ) -> BootstrapDistrictState:
        if next_state not in _TRANSITIONS[state.lifecycle_state]:
            raise BootstrapLifecycleError(
                f"Illegal bootstrap transition {state.lifecycle_state} -> {next_state}"
            )
        return self._save(replace(
            state,
            lifecycle_state=next_state,
            revision=state.revision + 1,
            history=self._history(state, next_state, event),
            **changes,
        ))

    def record_pioneer(
        self, recipe: str, ore: str, actions: Sequence[Mapping[str, object]],
    ) -> BootstrapDistrictState:
        owned = _canonical_actions(actions)
        state = self.load(recipe)
        if state is None:
            district_id = _district_id(
                self.episode_id, self.surface, self.force, recipe, ore,
            )
            return self._save(BootstrapDistrictState(
                episode_id=self.episode_id,
                surface=self.surface,
                force=self.force,
                bootstrap_profile=self.bootstrap_profile,
                recipe=recipe,
                ore=ore,
                district_id=district_id,
                lifecycle_state="pioneer",
                pioneer_actions=owned,
                reservations={},
                history=({"revision": 1, "event": "pioneer_recorded",
                          "from": None, "to": "pioneer"},),
            ))
        if state.lifecycle_state == "released":
            raise BootstrapLifecycleError(
                f"Released {recipe} bootstrap district cannot regain a pioneer"
            )
        if state.ore != ore or state.pioneer_actions != owned:
            raise BootstrapLifecycleError(
                f"Persisted {recipe} pioneer identity differs from the live starter"
            )
        return state

    def provision(
        self, recipe: str, *, reservations: Mapping[str, frozenset[Tile]],
        replacement_origin: Point, replacement_provider: Point,
        replacement_furnaces: int,
        replacement_actions: Sequence[Mapping[str, object]],
        transport_source: Point | None = None,
        transport_actions: Sequence[Mapping[str, object]] = (),
    ) -> BootstrapDistrictState:
        state = self.load(recipe)
        if state is None:
            raise BootstrapLifecycleError(f"{recipe} replacement cannot precede its pioneer")
        owned = _canonical_actions(replacement_actions)
        owned_transport = _canonical_actions(transport_actions)
        if bool(transport_source) != bool(owned_transport):
            raise BootstrapLifecycleError(
                "Bootstrap transport source and actions must be provisioned together"
            )
        if state.replacement_origin not in {None, replacement_origin}:
            raise BootstrapLifecycleError(
                f"{recipe} replacement site changed after its footprint was reserved"
            )
        if state.replacement_furnaces not in {0, replacement_furnaces}:
            raise BootstrapLifecycleError(
                f"{recipe} opening replacement capacity changed after reservation"
            )
        normalized_reservations = {
            name: frozenset(tiles) for name, tiles in reservations.items()
        }
        if (
            state.lifecycle_state == "provisioning"
            and state.reservations == normalized_reservations
            and state.replacement_origin == replacement_origin
            and state.replacement_provider == replacement_provider
            and state.replacement_furnaces == replacement_furnaces
            and state.replacement_actions == owned
            and state.transport_source == transport_source
            and state.transport_actions == owned_transport
        ):
            return state
        return self._transition(
            state, "provisioning", "replacement_reserved",
            reservations=normalized_reservations,
            replacement_origin=replacement_origin,
            replacement_provider=replacement_provider,
            replacement_furnaces=replacement_furnaces,
            replacement_actions=owned,
            transport_source=transport_source,
            transport_actions=owned_transport,
        )

    def mark_validating(self, recipe: str, measured_output_count: int) -> BootstrapDistrictState:
        if measured_output_count <= 0:
            raise BootstrapLifecycleError("Replacement validation requires measured output")
        state = self.load(recipe)
        if state is None:
            raise BootstrapLifecycleError(f"{recipe} has no bootstrap district to validate")
        return self._transition(
            state, "validating", "replacement_output_measured",
            measured_output_count=max(state.measured_output_count, measured_output_count),
        )

    def mark_retiring(self, recipe: str) -> BootstrapDistrictState:
        state = self.load(recipe)
        if state is None:
            raise BootstrapLifecycleError(f"{recipe} has no bootstrap district to retire")
        return self._transition(state, "retiring", "pioneer_retirement_started")

    def mark_released(self, recipe: str) -> BootstrapDistrictState:
        state = self.load(recipe)
        if state is None:
            raise BootstrapLifecycleError(f"{recipe} has no bootstrap district to release")
        return self._transition(state, "released", "pioneer_absence_verified")

    def update_replacement(
        self, recipe: str, *, replacement_provider: Point,
        replacement_furnaces: int,
        replacement_actions: Sequence[Mapping[str, object]],
    ) -> BootstrapDistrictState | None:
        state = self.load(recipe)
        if state is None:
            return None
        if replacement_furnaces < state.replacement_furnaces:
            raise BootstrapLifecycleError("Replacement ownership cannot regress in capacity")
        owned = _canonical_actions(replacement_actions)
        if (
            state.replacement_provider == replacement_provider
            and state.replacement_furnaces == replacement_furnaces
            and state.replacement_actions == owned
        ):
            return state
        return self._save(replace(
            state,
            replacement_provider=replacement_provider,
            replacement_furnaces=replacement_furnaces,
            replacement_actions=owned,
            revision=state.revision + 1,
            history=(*state.history, {
                "revision": state.revision + 1,
                "event": "replacement_ownership_updated",
                "from": state.lifecycle_state,
                "to": state.lifecycle_state,
            }),
        ))
