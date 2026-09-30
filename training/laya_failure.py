# Path: training/laya_failure.py
# Purpose: Preserve bounded, provenance-carrying Laya failure replays and improvement proposals.

"""Offline failure evidence for the Laya self-improvement loop.

This module deliberately has no Factorio or runner side effects.  A failure
packet is a replay request for a disposable environment; an improvement
proposal is an auditable suggestion derived from that packet.  Neither type
grants execution authority or edits documentation/tools by itself.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator
from training.isolation import assert_training_identity


ROOT = Path(__file__).resolve().parents[1]
FAILURE_SCHEMA = json.loads(
    (ROOT / "schemas" / "laya_failure_packet.schema.json").read_text(encoding="utf-8")
)
PROPOSAL_SCHEMA = json.loads(
    (ROOT / "schemas" / "laya_improvement_proposal.schema.json").read_text(encoding="utf-8")
)
FAILURE_VALIDATOR = Draft7Validator(FAILURE_SCHEMA)
PROPOSAL_VALIDATOR = Draft7Validator(PROPOSAL_SCHEMA)


class LayaFailureError(ValueError):
    """Raised when failure evidence is malformed or exceeds safe bounds."""


class LayaLedgerError(ValueError):
    """Raised when an improvement ledger cannot safely read or append data."""


class FailureCause(StrEnum):
    """Root-cause buckets kept separate from the Factorio failure kind."""

    POLICY = "policy"
    OBSERVATION = "observation"
    TOOL = "tool"
    VALIDATOR = "validator"
    DOCUMENTATION = "documentation"
    ENVIRONMENT = "environment"


FAILURE_CAUSES = tuple(item.value for item in FailureCause)
PROPOSAL_STATUSES = ("proposed", "accepted", "rejected", "superseded")
MAX_PACKET_BYTES = 64 * 1024
MAX_PROPOSAL_BYTES = 32 * 1024


def is_safe_lab_timeout(report: Mapping[str, Any]) -> bool:
    """An observed episode horizon is policy evidence only with explicit health."""
    if report.get('status') != 'timed_out' or (report.get('failure') or {}).get('kind') != 'timeout':
        return False
    try:
        assert_training_identity(report.get('surface'),report.get('force'))
    except (ValueError,TypeError):
        return False
    metrics = report.get('metrics') or {}
    return (all(type(metrics.get(key)) is int and metrics[key] == 0 for key in
                ('forbidden_entities','out_of_bounds_entities','budget_overruns'))
            and metrics.get('fixtures_valid') is True and metrics.get('power_connected') is True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise LayaFailureError(f"evidence is not JSON serializable: {error}") from error


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _bounded(value: Any, *, depth: int = 0, path: str = "") -> Any:
    """Copy JSON-like evidence while rejecting unbounded or unsafe payloads."""

    if depth > 8:
        raise LayaFailureError(f"evidence exceeds maximum nesting at {path or '<root>'}")
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise LayaFailureError(f"non-finite number at {path or '<root>'}")
        if isinstance(value, str) and len(value) > 4096:
            raise LayaFailureError(f"string exceeds 4096 characters at {path or '<root>'}")
        return value
    if isinstance(value, Mapping):
        if len(value) > 64:
            raise LayaFailureError(f"object exceeds 64 keys at {path or '<root>'}")
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise LayaFailureError(f"invalid evidence key at {path or '<root>'}")
            result[key] = _bounded(item, depth=depth + 1, path=f"{path}.{key}" if path else key)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 64:
            raise LayaFailureError(f"array exceeds 64 items at {path or '<root>'}")
        return [
            _bounded(item, depth=depth + 1, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise LayaFailureError(f"unsupported evidence type at {path or '<root>'}: {type(value).__name__}")


def _id(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    normalized = "".join(char if char.isalnum() or char in "._:-" else "-" for char in text)
    normalized = normalized[:128]
    if not normalized or not normalized[0].isalnum():
        normalized = f"id-{normalized}" if normalized else fallback
    return normalized


def _ref(value: Any, *, fallback_id: str) -> dict[str, Any]:
    """Normalize a versioned artifact reference and fill missing hashes."""

    if isinstance(value, Mapping):
        ref = dict(value)
        ref_id = _id(ref.get("id"), fallback_id)
        version = str(ref.get("version") or "unknown")[:128]
        digest = str(ref.get("hash") or "")
        if not digest.startswith("sha256:"):
            digest = _hash({"id": ref_id, "version": version, "value": ref.get("value")})
        return {"id": ref_id, "version": version, "hash": digest}
    text = str(value or "")[:128]
    return {"id": _id(text, fallback_id), "version": "unknown", "hash": _hash(text)}


def _source_hash(provenance: Mapping[str, Any]) -> str:
    source = {key: value for key, value in provenance.items() if key not in {"source_hash"}}
    return _hash(source)


def _normalize_provenance(value: Mapping[str, Any] | None, *, packet_basis: Mapping[str, Any]) -> dict[str, Any]:
    source = dict(value or {})
    result: dict[str, Any] = {
        "run_id": _id(source.get("run_id"), "run-unknown"),
        "episode_id": _id(source.get("episode_id"), "episode-unknown"),
        "trajectory_id": _id(source.get("trajectory_id"), "trajectory-unknown"),
        "scenario_id": _id(source.get("scenario_id"), "scenario-unknown"),
        "scenario_seed": max(0, int(source.get("scenario_seed", source.get("seed", 0)) or 0)),
        "scenario_hash": str(source.get("scenario_hash") or _hash(packet_basis)),
        "repository_revision": str(source.get("repository_revision") or "unknown")[:256],
        "guideline_pack": _ref(source.get("guideline_pack"), fallback_id="guidelines.unknown"),
        "tool_registry": _ref(source.get("tool_registry"), fallback_id="tools.unknown"),
        "model": _ref(source.get("model"), fallback_id="model.unknown"),
    }
    result["source_hash"] = str(source.get("source_hash") or _source_hash(result))
    return result


def _validate(validator: Draft7Validator, payload: Mapping[str, Any], *, label: str) -> None:
    errors = sorted(validator.iter_errors(dict(payload)), key=lambda error: list(error.path))
    if errors:
        detail = "; ".join(
            f"{'/'.join(map(str, error.path)) or '<root>'}: {error.message}"
            for error in errors[:4]
        )
        raise LayaFailureError(f"invalid {label}: {detail}")


def _bounded_record(payload: Mapping[str, Any], *, max_bytes: int, label: str) -> dict[str, Any]:
    record = _bounded(dict(payload))
    size = len(_canonical(record).encode("utf-8"))
    if size > max_bytes:
        raise LayaFailureError(f"{label} exceeds {max_bytes} serialized bytes ({size})")
    return record


@dataclass(frozen=True, slots=True)
class FailurePacket:
    """A bounded failure and a disposable replay request derived from it."""

    packet_id: str
    cause: FailureCause
    failure_kind: str
    summary: str
    objective: Mapping[str, Any]
    replay_request: Mapping[str, Any]
    provenance: Mapping[str, Any]
    evidence: Mapping[str, Any]
    created_at: str
    version: str = "1.0.0"

    @classmethod
    def create(
        cls,
        *,
        cause: FailureCause | str,
        failure_kind: str,
        summary: str,
        objective: Mapping[str, Any],
        replay_request: Mapping[str, Any],
        provenance: Mapping[str, Any] | None = None,
        evidence: Mapping[str, Any] | None = None,
        packet_id: str | None = None,
        created_at: str | None = None,
    ) -> "FailurePacket":
        try:
            normalized_cause = FailureCause(str(cause))
        except ValueError as error:
            raise LayaFailureError(
                f"unknown failure cause {cause!r}; expected one of {', '.join(FAILURE_CAUSES)}"
            ) from error
        if not isinstance(objective, Mapping) or not isinstance(replay_request, Mapping):
            raise LayaFailureError("objective and replay_request must be objects")
        replay = _bounded(dict(replay_request))
        # Replays always run in disposable training; a packet cannot authorize
        # a real-Nauvis or legacy runtime request.
        replay.setdefault("runtime", "disposable_training")
        if replay.get("runtime") != "disposable_training":
            raise LayaFailureError("failure replay runtime must be disposable_training")
        replay.setdefault("variations", [{"variation_id": "baseline"}])
        replay.setdefault("limits", {"max_attempts": 8, "max_ticks": 60 * 60})
        replay.setdefault("scenario", {})
        replay.setdefault("seed", 0)
        replay.setdefault("initial_state", {})
        replay.setdefault("decision_context", {})
        basis = {"objective": objective, "replay_request": replay, "summary": summary}
        normalized_provenance = _normalize_provenance(provenance, packet_basis=basis)
        packet = cls(
            packet_id=_id(packet_id, f"failure-{_hash(basis)[7:19]}"),
            cause=normalized_cause,
            failure_kind=str(failure_kind).strip()[:128] or "unknown",
            summary=str(summary).strip()[:2048],
            objective=copy.deepcopy(dict(objective)),
            replay_request=replay,
            provenance=normalized_provenance,
            evidence=copy.deepcopy(dict(evidence or {"decision_trace": [], "execution": {}, "outcome": {}})),
            created_at=str(created_at or _now()),
        )
        packet.validate()
        return packet

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "FailurePacket":
        record = _bounded_record(payload, max_bytes=MAX_PACKET_BYTES, label="failure packet")
        _validate(FAILURE_VALIDATOR, record, label="failure packet")
        packet = cls(
            packet_id=str(record["packet_id"]),
            cause=FailureCause(str(record["cause"])),
            failure_kind=str(record["failure_kind"]),
            summary=str(record["summary"]),
            objective=record["objective"],
            replay_request=record["replay_request"],
            provenance=record["provenance"],
            evidence=record["evidence"],
            created_at=str(record["created_at"]),
            version=str(record["version"]),
        )
        return packet

    def to_dict(self, *, max_bytes: int = MAX_PACKET_BYTES) -> dict[str, Any]:
        payload = {
            "version": self.version,
            "packet_id": self.packet_id,
            "created_at": self.created_at,
            "cause": self.cause.value,
            "failure_kind": self.failure_kind,
            "summary": self.summary,
            "objective": copy.deepcopy(dict(self.objective)),
            "replay_request": copy.deepcopy(dict(self.replay_request)),
            "provenance": copy.deepcopy(dict(self.provenance)),
            "evidence": copy.deepcopy(dict(self.evidence)),
        }
        record = _bounded_record(payload, max_bytes=max_bytes, label="failure packet")
        _validate(FAILURE_VALIDATOR, record, label="failure packet")
        return record

    def validate(self) -> None:
        self.to_dict()

    def serialized(self, *, max_bytes: int = MAX_PACKET_BYTES) -> str:
        return _canonical(self.to_dict(max_bytes=max_bytes))

    @property
    def packet_hash(self) -> str:
        return _hash(self.to_dict())


@dataclass(frozen=True, slots=True)
class ImprovementProposal:
    """A reviewable change proposal; it never applies the proposed change."""

    proposal_id: str
    packet_id: str
    cause: FailureCause
    status: str
    summary: str
    rationale: str
    proposed_change: Mapping[str, Any]
    evidence: Mapping[str, Any]
    provenance: Mapping[str, Any]
    created_at: str
    version: str = "1.0.0"

    @classmethod
    def create(
        cls,
        *,
        packet: FailurePacket | Mapping[str, Any],
        summary: str,
        rationale: str,
        target: str,
        description: str,
        required_tests: Sequence[str] = (),
        kind: FailureCause | str | None = None,
        proposed_change: Mapping[str, Any] | None = None,
        provenance: Mapping[str, Any] | None = None,
        proposal_id: str | None = None,
        status: str = "proposed",
    ) -> "ImprovementProposal":
        failure = packet if isinstance(packet, FailurePacket) else FailurePacket.from_mapping(packet)
        try:
            proposal_cause = FailureCause(str(kind or failure.cause))
        except ValueError as error:
            raise LayaFailureError(f"unknown proposal cause: {kind!r}") from error
        if status not in PROPOSAL_STATUSES:
            raise LayaFailureError(f"unknown proposal status: {status!r}")
        change = dict(proposed_change or {})
        change.update({"kind": proposal_cause.value, "target": str(target).strip(), "description": str(description).strip()})
        change["required_tests"] = [str(test).strip()[:512] for test in required_tests][:32]
        evidence = {
            "packet_hash": failure.packet_hash,
            "replay_request_hash": _hash(failure.replay_request),
            "failure_kind": failure.failure_kind,
            "objective_gap": failure.objective.get("gap"),
        }
        if isinstance(provenance, Mapping):
            evidence["review"] = dict(provenance)
        result = cls(
            proposal_id=_id(proposal_id, f"proposal-{failure.packet_id}"),
            packet_id=failure.packet_id,
            cause=proposal_cause,
            status=status,
            summary=str(summary).strip()[:2048],
            rationale=str(rationale).strip()[:4096],
            proposed_change=change,
            evidence=evidence,
            provenance=_normalize_provenance(provenance, packet_basis={"packet_id": failure.packet_id}),
            created_at=_now(),
        )
        result.validate()
        return result

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ImprovementProposal":
        record = _bounded_record(payload, max_bytes=MAX_PROPOSAL_BYTES, label="improvement proposal")
        _validate(PROPOSAL_VALIDATOR, record, label="improvement proposal")
        return cls(
            proposal_id=str(record["proposal_id"]),
            packet_id=str(record["packet_id"]),
            cause=FailureCause(str(record["cause"])),
            status=str(record["status"]),
            summary=str(record["summary"]),
            rationale=str(record["rationale"]),
            proposed_change=record["proposed_change"],
            evidence=record["evidence"],
            provenance=record["provenance"],
            created_at=str(record["created_at"]),
            version=str(record["version"]),
        )

    def to_dict(self, *, max_bytes: int = MAX_PROPOSAL_BYTES) -> dict[str, Any]:
        payload = {
            "version": self.version,
            "proposal_id": self.proposal_id,
            "packet_id": self.packet_id,
            "created_at": self.created_at,
            "cause": self.cause.value,
            "status": self.status,
            "summary": self.summary,
            "rationale": self.rationale,
            "proposed_change": copy.deepcopy(dict(self.proposed_change)),
            "evidence": copy.deepcopy(dict(self.evidence)),
            "provenance": copy.deepcopy(dict(self.provenance)),
        }
        record = _bounded_record(payload, max_bytes=max_bytes, label="improvement proposal")
        _validate(PROPOSAL_VALIDATOR, record, label="improvement proposal")
        return record

    def validate(self) -> None:
        self.to_dict()


@contextmanager
def _locked(path: Path, mode: str) -> Iterator[Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode, encoding="utf-8") as handle:
        try:
            import fcntl

            lock_mode = fcntl.LOCK_EX if "a" in mode or "+" in mode else fcntl.LOCK_SH
            fcntl.flock(handle.fileno(), lock_mode)
        except (ImportError, OSError):
            # The JSONL contract remains valid on platforms without advisory
            # locks; POSIX runners use flock for concurrent append/read safety.
            pass
        try:
            yield handle
        finally:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass


class ImprovementLedger:
    """Append/read a separate proposal JSONL file with validation and fsync."""

    def __init__(self, path: Path | str, *, max_record_bytes: int = MAX_PROPOSAL_BYTES):
        if max_record_bytes < 1024:
            raise ValueError("max_record_bytes must be at least 1024")
        self.path = Path(path)
        self.max_record_bytes = max_record_bytes

    def append(self, proposal: ImprovementProposal | Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(proposal, ImprovementProposal):
            record = proposal.to_dict(max_bytes=self.max_record_bytes)
        else:
            record = ImprovementProposal.from_mapping(proposal).to_dict(max_bytes=self.max_record_bytes)
        line = _canonical(record) + "\n"
        encoded = line.encode("utf-8")
        if len(encoded) > self.max_record_bytes:
            raise LayaLedgerError("proposal exceeds ledger record limit")
        with _locked(self.path, "a+") as handle:
            handle.seek(0)
            existing_ids: set[str] = set()
            for line_number, raw in enumerate(handle, 1):
                if not raw.strip():
                    continue
                try:
                    existing_ids.add(str(json.loads(raw)["proposal_id"]))
                except (json.JSONDecodeError, KeyError, TypeError) as error:
                    raise LayaLedgerError(f"invalid existing ledger record at line {line_number}") from error
            if record["proposal_id"] in existing_ids:
                raise LayaLedgerError(f"duplicate proposal_id: {record['proposal_id']}")
            handle.seek(0, os.SEEK_END)
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return copy.deepcopy(record)

    def iter_proposals(self, *, status: str | None = None, packet_id: str | None = None) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with _locked(self.path, "r") as handle:
            for line_number, raw in enumerate(handle, 1):
                if not raw.strip():
                    continue
                try:
                    record = json.loads(raw)
                    proposal = ImprovementProposal.from_mapping(record)
                except (json.JSONDecodeError, LayaFailureError, TypeError, ValueError) as error:
                    raise LayaLedgerError(f"invalid ledger record at line {line_number}: {error}") from error
                if status is not None and proposal.status != status:
                    continue
                if packet_id is not None and proposal.packet_id != packet_id:
                    continue
                yield proposal.to_dict(max_bytes=self.max_record_bytes)

    def read(self, *, status: str | None = None, packet_id: str | None = None) -> list[dict[str, Any]]:
        return list(self.iter_proposals(status=status, packet_id=packet_id))


__all__ = [
    "FAILURE_CAUSES",
    "FailureCause",
    "FailurePacket",
    "ImprovementLedger",
    "ImprovementProposal",
    "LayaFailureError",
    "LayaLedgerError",
]
