# Path: tools/checkpoint_run_monitor.py | Purpose: Monotonic structured checkpoint run accounting.
"""Observe one deterministic lane at safe controller boundaries.

This module owns no Factorio process and performs no RCON/server/systemd
actions.  It evaluates structured observations, writes an atomic
``episode/fleet-result.json``, and delegates bundle capture only to an
injected safe capture interface.  A missing live adapter is an explicit
unsupported result, never a log-based pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import inspect
import tempfile
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

from orchestrator.checkpoint_milestones import (
    CheckpointEvaluator,
    CheckpointResult,
    DEFAULT_MILESTONES,
    FactorySnapshot,
    MilestoneDefinition,
    PredicateStatus,
)
from orchestrator.checkpoint_observer import (
    FileObservationAdapter,
    ObservationAdapter,
    ObservationUnsupported,
    StructuredObservation,
    UnsupportedObservationAdapter,
)
from tools.milestone_checkpoint import capture as capture_milestone_bundle


RESULT_SCHEMA_VERSION = 1
TERMINAL_STATUSES = {
    "passed", "functional_failed", "timed_out", "incompatible",
    "infrastructure_error", "cancelled",
}


class CheckpointMonitorError(ValueError):
    """The checkpoint run manifest or result contract is invalid."""


class SafeCapture(Protocol):
    """Capture exactly one milestone through a trusted capture boundary."""

    def __call__(self, request: "CaptureRequest") -> Any:
        ...


@dataclass(frozen=True)
class CaptureRequest:
    checkpoint_id: str
    predicate_version: str
    predicate_evidence: Mapping[str, Any]
    run_id: str
    origin_checkpoint_id: str
    capture_tick: int | None
    reason: str = "milestone"


def _safe_capture_component(value: object, *, fallback: str) -> str:
    """Make manifest identity safe for a generated bundle directory name."""
    rendered = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip())
    rendered = rendered.strip(".-")
    return rendered or fallback


def _lane_manifest(root: Path, fallback: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read the current lane manifest so started_at and deployed hashes stay fresh."""
    path = Path(root) / "episode" / "current.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    return value if isinstance(value, Mapping) else fallback


def lane_milestone_capture(
    root: Path,
    manifest: Mapping[str, Any],
) -> SafeCapture:
    """Build a capture callback for an already-connected lane RCON client.

    The callback is deliberately context-bound: the monitor supplies the live
    ``client`` only from a controller-safe pass boundary.  ``capture`` itself
    performs no lifecycle operation; the explicit gates below document that
    the caller has already quiesced its controller and keep its default PID
    gate from rejecting a capture made while the runner process is alive.
    """
    root = Path(root).resolve()
    fallback = dict(manifest)
    sequence: dict[tuple[str, str], int] = {}

    def capture_request(
        request: CaptureRequest,
        *,
        client: Any | None = None,
        bridge: Any | None = None,
    ) -> Any:
        del bridge  # The RCON client is the single save transport.
        if client is None:
            raise CheckpointMonitorError(
                "checkpoint capture requires the connected lane RCON client"
            )
        current = _lane_manifest(root, fallback)
        candidate_commit = str(
            current.get("candidate_repository_revision")
            or current.get("repository_revision")
            or ""
        ).lower()
        if not candidate_commit:
            raise CheckpointMonitorError(
                "lane manifest has no candidate repository revision for capture"
            )
        hashes: dict[str, str] = {}
        supplied_hashes = current.get("mod_hashes")
        if isinstance(supplied_hashes, Mapping):
            hashes.update({str(key): str(value) for key, value in supplied_hashes.items() if value})
        for key in (
            "deployed_factorio_mod_sha256",
            "deployed_factorio_training_lab_sha256",
        ):
            value = current.get(key)
            if value:
                hashes[key] = str(value)
        if not hashes:
            raise CheckpointMonitorError(
                "lane manifest has no deployed mod hashes for capture provenance"
            )
        lineage = _safe_capture_component(
            current.get("lineage_id") or current.get("episode_id") or request.run_id,
            fallback="lineage",
        )
        identity = (request.checkpoint_id, request.reason)
        sequence[identity] = sequence.get(identity, 0) + 1
        number = sequence[identity]
        generation = "capture-{}-{}-{}-{}-{:02d}".format(
            _safe_capture_component(request.checkpoint_id, fallback="checkpoint"),
            _safe_capture_component(candidate_commit[:8], fallback="commit"),
            _safe_capture_component(request.run_id, fallback="run"),
            _safe_capture_component(request.reason, fallback="capture"),
            number,
        )
        return capture_milestone_bundle(
            root,
            client=client,
            checkpoint_id=request.checkpoint_id,
            generation_id=generation,
            run_id=_safe_capture_component(request.run_id, fallback="run"),
            lineage_id=lineage,
            # The save command's returned tick is authoritative for the ZIP.
            # Keep the predicate observation tick as evidence instead of
            # claiming it is necessarily the exact server-save tick.
            capture_tick=None,
            predicate_version=request.predicate_version,
            predicate_evidence={
                **dict(request.predicate_evidence),
                "observation_tick": request.capture_tick,
            },
            creator_commit=candidate_commit,
            mod_hashes=hashes,
            reason=request.reason,
            started_at=current.get("started_at"),
            sequence=number,
            # The caller invokes us only after the autonomous controller has
            # returned to its safe boundary.  Do not inspect the process PID:
            # this callback runs inside that process by design.
            quiescence_check=lambda: True,
            controller_handshake=lambda: True,
        )

    return capture_request


@dataclass(frozen=True)
class FleetRunManifest:
    """Validated runner-facing checkpoint contract."""

    run_id: str
    ordered_checkpoint_ids: tuple[str, ...]
    predicate_versions: Mapping[str, str]
    origin_checkpoint_id: str
    result_path: Path
    capture_policy: Mapping[str, Any]
    commit: str = "unknown"
    suite_id: str | None = None
    log_path: Path | None = None
    registry_version: str | None = None
    factorio_version: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, root: Path | None = None) -> "FleetRunManifest":
        fleet = value.get("checkpoint_fleet", value.get("fleet", value))
        if not isinstance(fleet, Mapping):
            raise CheckpointMonitorError("fleet manifest checkpoint contract must be an object")
        raw_order = fleet.get("ordered_checkpoint_ids", fleet.get("checkpoint_ids"))
        versions = fleet.get("predicate_versions", {})
        ordered: list[str] = []
        if isinstance(raw_order, Sequence) and not isinstance(raw_order, (str, bytes)):
            for item in raw_order:
                if isinstance(item, Mapping):
                    checkpoint_id = item.get("id", item.get("checkpoint_id"))
                    version = item.get("predicate_version")
                    if checkpoint_id is not None:
                        ordered.append(str(checkpoint_id))
                        if version is not None and isinstance(versions, Mapping):
                            versions = dict(versions)
                            versions.setdefault(str(checkpoint_id), str(version))
                else:
                    ordered.append(str(item))
        if not ordered or len(set(ordered)) != len(ordered):
            raise CheckpointMonitorError("fleet manifest must declare unique ordered checkpoint IDs")
        if not isinstance(versions, Mapping):
            raise CheckpointMonitorError("fleet manifest predicate_versions must be an object")
        predicate_versions = {str(key): str(item) for key, item in versions.items()}
        missing_versions = [item for item in ordered if not predicate_versions.get(item)]
        if missing_versions:
            raise CheckpointMonitorError(
                "fleet manifest is missing predicate versions: " + ", ".join(missing_versions)
            )
        origin = str(fleet.get("origin_checkpoint_id", fleet.get("origin_checkpoint", "")))
        if origin not in ordered:
            raise CheckpointMonitorError("fleet manifest origin checkpoint is not in its ordered timeline")
        result_raw = fleet.get("result_path", "episode/fleet-result.json")
        result = Path(str(result_raw))
        if result.is_absolute() or ".." in result.parts or not result.parts:
            raise CheckpointMonitorError("fleet result path must be a safe relative path")
        capture = fleet.get("capture_policy", {})
        if not isinstance(capture, Mapping):
            raise CheckpointMonitorError("fleet manifest capture_policy must be an object")
        log_raw = fleet.get("log_path", value.get("log_path"))
        log_path = None if log_raw in (None, "") else Path(str(log_raw))
        if log_path is not None and log_path.is_absolute() is False and root is not None:
            log_path = Path(root) / log_path
        return cls(
            run_id=str(value.get("run_id", fleet.get("run_id", ""))),
            ordered_checkpoint_ids=tuple(ordered),
            predicate_versions=predicate_versions,
            origin_checkpoint_id=origin,
            result_path=result,
            capture_policy=dict(capture),
            commit=str(value.get("candidate_repository_revision", value.get("repository_revision", "unknown"))),
            suite_id=None if value.get("suite_id") is None else str(value["suite_id"]),
            log_path=log_path,
            registry_version=None if fleet.get("registry_version") is None else str(fleet["registry_version"]),
            factorio_version=None if fleet.get("factorio_version") is None else str(fleet["factorio_version"]),
        )

    def as_mapping(self) -> dict[str, Any]:
        return {
            "ordered_checkpoint_ids": list(self.ordered_checkpoint_ids),
            "predicate_versions": dict(self.predicate_versions),
            "origin_checkpoint_id": self.origin_checkpoint_id,
            "result_path": self.result_path.as_posix(),
            "capture_policy": dict(self.capture_policy),
            "registry_version": self.registry_version,
            "factorio_version": self.factorio_version,
        }


def build_fleet_manifest_fields(
    ordered_checkpoint_ids: Sequence[str],
    predicate_versions: Mapping[str, str],
    *,
    origin_checkpoint_id: str,
    result_path: str = "episode/fleet-result.json",
    capture_policy: Mapping[str, Any] | None = None,
    registry_version: str | None = None,
    factorio_version: str | None = None,
) -> dict[str, Any]:
    """Build the required manifest subsection for lane preparation code."""
    contract = FleetRunManifest(
        run_id="manifest-preview",
        ordered_checkpoint_ids=tuple(str(item) for item in ordered_checkpoint_ids),
        predicate_versions=dict(predicate_versions),
        origin_checkpoint_id=origin_checkpoint_id,
        result_path=Path(result_path),
        capture_policy=dict(capture_policy or {"on_milestone": True, "on_terminal": True}),
        registry_version=registry_version,
        factorio_version=factorio_version,
    )
    return {"checkpoint_fleet": contract.as_mapping()}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _tail(path: Path | None, limit: int = 100) -> list[str]:
    if path is None or not path.is_file():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []


class CheckpointRunMonitor:
    """Monotonically account for one lane's structured checkpoint progress."""

    def __init__(
        self,
        manifest: FleetRunManifest | Mapping[str, Any],
        *,
        root: Path,
        adapter: ObservationAdapter | None = None,
        capture: SafeCapture | None = None,
        milestones: Sequence[MilestoneDefinition] = DEFAULT_MILESTONES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = Path(root).resolve()
        self.manifest = manifest if isinstance(manifest, FleetRunManifest) else FleetRunManifest.from_mapping(manifest, root=self.root)
        if self.manifest.log_path is not None and not self.manifest.log_path.is_absolute():
            self.manifest = FleetRunManifest(
                **{**self.manifest.__dict__, "log_path": self.root / self.manifest.log_path}
            )
        if not self.manifest.run_id:
            raise CheckpointMonitorError("fleet manifest run_id is required")
        self.result_path = self.root / self.manifest.result_path
        if not self.result_path.resolve().is_relative_to(self.root):
            raise CheckpointMonitorError("fleet result path escapes lane root")
        self.adapter = adapter or UnsupportedObservationAdapter()
        self.capture = capture
        self.milestones = tuple(milestones)
        known = {item.checkpoint_id for item in self.milestones}
        missing = [item for item in self.manifest.ordered_checkpoint_ids if item not in known]
        if missing:
            raise CheckpointMonitorError("no structured predicate is registered for: " + ", ".join(missing))
        registered_versions = {
            item.checkpoint_id: item.predicate_version for item in self.milestones
        }
        mismatched = [
            checkpoint for checkpoint in self.manifest.ordered_checkpoint_ids
            if self.manifest.predicate_versions[checkpoint] != registered_versions[checkpoint]
        ]
        if mismatched:
            raise CheckpointMonitorError(
                "fleet manifest predicate version does not match registered evaluator: "
                + ", ".join(mismatched)
            )
        self.evaluator = CheckpointEvaluator(self.milestones)
        self.clock = clock
        self.started_monotonic = clock()
        self._last_transition_monotonic = self.started_monotonic
        self.baseline: FactorySnapshot | None = None
        self.history: list[FactorySnapshot] = []
        origin_index = self.manifest.ordered_checkpoint_ids.index(self.manifest.origin_checkpoint_id)
        self.reached = list(self.manifest.ordered_checkpoint_ids[: origin_index + 1])
        self._seeded = set(self.reached)
        self.evaluator.reached.update(self.reached)
        self.checkpoint_results: dict[str, dict[str, Any]] = {
            checkpoint: {
                "checkpoint_id": checkpoint,
                "predicate_version": self.manifest.predicate_versions[checkpoint],
                "status": "passed",
                "evidence": {"origin_seed": True} if checkpoint == self.manifest.origin_checkpoint_id else {"prior_checkpoint_seed": True},
                "missing": [],
            }
            for checkpoint in self.reached
        }
        self.transition_ticks: dict[str, int] = {}
        self.transition_durations: dict[str, float] = {}
        self.captures: dict[str, Any] = {}
        self.warnings: list[str] = []
        self.unsupported_evidence: list[dict[str, Any]] = []
        self.status = "running"
        self.failure_reason: str | None = None
        self.started_at = _now()
        self._last_observation: StructuredObservation | None = None
        self._last_client: Any | None = None
        self._last_bridge: Any | None = None
        self._last_periodic_monotonic = self.started_monotonic
        self._periodic_sequence = 0
        self._write()

    def _payload(self) -> dict[str, Any]:
        passed = [item for item in self.reached if item != self.manifest.origin_checkpoint_id]
        payload: dict[str, Any] = {
            "version": RESULT_SCHEMA_VERSION,
            "run_id": self.manifest.run_id,
            "commit": self.manifest.commit,
            "start_checkpoint_id": self.manifest.origin_checkpoint_id,
            "status": self.status,
            "passed_checkpoints": passed,
            "contiguous_reached_checkpoints": list(self.reached),
            "transition_ticks": dict(self.transition_ticks),
            "transition_durations_seconds": dict(self.transition_durations),
            "checkpoint_results": dict(self.checkpoint_results),
            "captures": dict(self.captures),
            "warnings": list(self.warnings),
            "unsupported_evidence": list(self.unsupported_evidence),
            "started_at": self.started_at,
            "log_tail": _tail(self.manifest.log_path),
        }
        if self.manifest.suite_id is not None:
            payload["suite_id"] = self.manifest.suite_id
        if self.failure_reason is not None:
            payload["failure_reason"] = self.failure_reason
        if self.status in TERMINAL_STATUSES:
            payload["ended_at"] = _now()
        return payload

    def _write(self) -> None:
        _atomic_json(self.result_path, self._payload())

    def _next_checkpoint(self) -> str | None:
        index = len(self.reached)
        ordered = self.manifest.ordered_checkpoint_ids
        return ordered[index] if index < len(ordered) else None

    def _capture(
        self,
        checkpoint_id: str,
        result: CheckpointResult,
        observation: StructuredObservation,
        *,
        reason: str = "milestone",
    ) -> None:
        if reason == "milestone" and self.manifest.capture_policy.get("on_milestone", True) is False:
            return
        if reason != "milestone" and self.capture is not None and self.manifest.capture_policy.get(
            f"on_{reason}", True
        ) is False:
            return
        if self.capture is None:
            warning = f"capture unavailable for {checkpoint_id}: no safe capture interface configured"
            if warning not in self.warnings:
                self.warnings.append(warning)
            return
        request = CaptureRequest(
            checkpoint_id=checkpoint_id,
            predicate_version=result.predicate_version,
            predicate_evidence=result.evidence,
            run_id=self.manifest.run_id,
            origin_checkpoint_id=self.manifest.origin_checkpoint_id,
            capture_tick=observation.snapshot.tick,
            reason=reason,
        )
        try:
            # Legacy injected callbacks remain one-argument callables.  The
            # live lane callback opts into context so it can use the already
            # connected RCON client without opening a second connection.
            if self._last_client is not None:
                capture = self.capture
                parameters = inspect.signature(capture).parameters
                accepts_context = (
                    "client" in parameters or "bridge" in parameters
                    or any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values())
                )
                captured = (
                    capture(request, client=self._last_client, bridge=self._last_bridge)
                    if accepts_context else capture(request)
                )
            else:
                captured = self.capture(request)
        except Exception as error:
            self.warnings.append(
                f"capture failed for {checkpoint_id}: {type(error).__name__}: {error}"
            )
            return
        key = checkpoint_id if reason == "milestone" else f"{reason}-{checkpoint_id}-{self._periodic_sequence:02d}"
        self.captures[key] = str(captured) if captured is not None else True

    def observe_boundary(self, client: Any | None = None, bridge: Any | None = None) -> dict[str, Any]:
        """Collect one structured observation at a controller-safe boundary."""
        if self.status in TERMINAL_STATUSES:
            return self._payload()
        self._last_client = client
        self._last_bridge = bridge
        try:
            # The live adapter accepts context; file/callable adapters keep
            # the historical no-argument contract used by replay tests.
            if client is None and bridge is None:
                observation = self.adapter.observe()
            else:
                observe = self.adapter.observe
                parameters = inspect.signature(observe).parameters
                accepts_context = (
                    "client" in parameters or "bridge" in parameters
                    or any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values())
                )
                # Current live adapters bind their transport in __init__;
                # newer adapters may accept the boundary context directly.
                observation = (
                    observe(client=client, bridge=bridge)
                    if accepts_context else observe()
                )
        except ObservationUnsupported as error:
            self.unsupported_evidence.append({"reason": str(error), **error.evidence})
            self.finish("incompatible", f"structured checkpoint observation unsupported: {error}")
            return self._payload()
        except Exception as error:
            self.finish("infrastructure_error", f"structured checkpoint observation failed: {type(error).__name__}: {error}")
            return self._payload()
        if self.baseline is None:
            self.baseline = observation.snapshot
        self._last_observation = observation
        self.history.append(observation.snapshot)
        next_checkpoint = self._next_checkpoint()
        while next_checkpoint is not None:
            result = self.evaluator.evaluate(
                next_checkpoint, observation.snapshot,
                baseline=self.baseline, history=self.history,
            )
            self.checkpoint_results[next_checkpoint] = result.as_mapping()
            if not result.passed:
                break
            self.reached.append(next_checkpoint)
            tick = observation.snapshot.tick
            if tick is not None:
                self.transition_ticks[next_checkpoint] = tick
            now = self.clock()
            self.transition_durations[next_checkpoint] = max(
                0.0, now - self._last_transition_monotonic
            )
            self._last_transition_monotonic = now
            self._capture(next_checkpoint, result, observation)
            next_checkpoint = self._next_checkpoint()
        self._capture_periodic(observation)
        self._write()
        return self._payload()

    def _capture_periodic(self, observation: StructuredObservation) -> None:
        """Capture a non-promotional rolling save at a safe poll boundary."""
        raw_interval = self.manifest.capture_policy.get("periodic_seconds")
        try:
            interval = float(raw_interval)
        except (TypeError, ValueError):
            return
        if interval <= 0 or not self.reached:
            return
        now = self.clock()
        if now - self._last_periodic_monotonic < interval:
            return
        self._last_periodic_monotonic = now
        self._periodic_sequence += 1
        checkpoint_id = self.reached[-1]
        version = self.manifest.predicate_versions[checkpoint_id]
        evidence = {
            "furthest_checkpoint": checkpoint_id,
            "observation": dict(observation.raw),
            "non_promotional": True,
        }
        result = CheckpointResult(
            checkpoint_id, version, PredicateStatus.PASSED, evidence,
        )
        self._capture(checkpoint_id, result, observation, reason="periodic")

    def finish(
        self,
        status: str,
        reason: str | None = None,
        *,
        client: Any | None = None,
        bridge: Any | None = None,
    ) -> dict[str, Any]:
        if status not in TERMINAL_STATUSES:
            raise CheckpointMonitorError(f"unsupported terminal status: {status}")
        if self.status in TERMINAL_STATUSES:
            return self._payload()
        # Terminal capture is opt-in for callers that still hold a live
        # connection.  In autonomous_run the builder closes its RCON client
        # before finalization, so omitting it correctly skips this optional
        # save rather than issuing a command on a closed transport.
        if client is not None and self._last_observation is not None and self.reached:
            self._last_client = client
            self._last_bridge = bridge
            checkpoint_id = self.reached[-1]
            version = self.manifest.predicate_versions[checkpoint_id]
            terminal = CheckpointResult(
                checkpoint_id, version, PredicateStatus.PASSED,
                {"furthest_checkpoint": checkpoint_id, "terminal": True},
            )
            self._capture(
                checkpoint_id, terminal, self._last_observation, reason="terminal",
            )
        self.status = status
        self.failure_reason = reason
        self._write()
        return self._payload()

    def finalize(self, status: str, reason: str | None = None) -> dict[str, Any]:
        return self.finish(status, reason)


def monitor_from_manifest(
    manifest_path: Path,
    *,
    root: Path,
    log_path: Path | None = None,
    adapter: ObservationAdapter | None = None,
    capture: SafeCapture | None = None,
) -> CheckpointRunMonitor | None:
    """Create a monitor only for manifests carrying a fleet contract.

    Legacy deterministic episodes remain untouched. A fleet manifest may
    configure a structured JSON report via ``observation_path``. Live runners
    otherwise bind their structured RCON adapter at the first controller-safe
    boundary; callers which never bind one still fail closed explicitly.
    """
    try:
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CheckpointMonitorError(f"fleet manifest is unreadable: {manifest_path}") from error
    if not isinstance(payload, Mapping) or "checkpoint_fleet" not in payload:
        return None
    contract = payload["checkpoint_fleet"]
    if not isinstance(contract, Mapping):
        raise CheckpointMonitorError("checkpoint_fleet manifest field must be an object")
    observation_path = contract.get("observation_path")
    if adapter is None and observation_path:
        path = Path(str(observation_path))
        adapter = FileObservationAdapter(path if path.is_absolute() else Path(root) / path)
    manifest = FleetRunManifest.from_mapping(payload, root=root)
    if log_path is not None and manifest.log_path is None:
        manifest = FleetRunManifest(**{**manifest.__dict__, "log_path": Path(log_path)})
    return CheckpointRunMonitor(manifest, root=root, adapter=adapter, capture=capture)


__all__ = [
    "CaptureRequest",
    "CheckpointMonitorError",
    "CheckpointRunMonitor",
    "FleetRunManifest",
    "SafeCapture",
    "build_fleet_manifest_fields",
    "lane_milestone_capture",
    "monitor_from_manifest",
]
