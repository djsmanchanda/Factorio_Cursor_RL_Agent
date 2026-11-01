# Path: orchestrator/optimizer_submission_transaction.py | Purpose: Journal one exact, fully held optimizer submission and recover it without resending.
"""Standalone, fail-closed bookkeeping for a bound optimizer submission.

Boundary: this primitive is not wired to selection, dispatch, GameBridge or any
runner. Its request is the tagged layout preview, whose authorization denies
every action; it creates no authority. ``submitter`` is any object providing
``submit(envelope) -> receipt`` and ``read_receipt(envelope) -> receipt | None``.
A real adapter must correlate receipts by request ID and digest on the runtime
side; none exists yet.

Ordering: a ``prepared`` attempt is durable before the ledger fence; the fence
holds the cooperative ledger lock and rechecks the exact persisted revision
and full hold; ``submitting`` is durable before the single send; a correlated
receipt is then journaled. Anything sent or possibly sent is never resent, and
this module never releases, refreshes or otherwise mutates the reservation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from jsonschema import Draft7Validator

from core.optimizer_artifact_binding import BoundArtifactPlan
from orchestrator.material_reservations import MaterialReservationError, MaterialReservationLedger
from orchestrator.optimizer_layout_preview import prepare_optimizer_layout_preview

try:
    import fcntl
except ImportError:  # Journal locking is required; Windows is unsupported here.
    fcntl = None

VERSION = "optimizer-submission-transaction-v1"
_SCHEMA = Path(__file__).resolve().parents[1] / "schemas/optimizer_submission_transaction.schema.json"
_MAX_JOURNAL = 16 * 1024 * 1024
# A transaction may start a new attempt only when every earlier attempt is known unsent.
_UNSENT = "not_sent"


class OptimizerSubmissionError(RuntimeError):
    """Refused before this call sent anything; the reservation hold is unchanged."""


class OptimizerSubmissionUncertain(OptimizerSubmissionError):
    """The request may have reached the runtime; never resend, hold, and review or recover."""


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _validator():
    return Draft7Validator(json.loads(_SCHEMA.read_text(encoding="utf-8")))


def journal_path(ledger: MaterialReservationLedger, transaction_id: str) -> Path:
    """Place the journal beside its ledger so it cannot name another episode's ledger."""
    if not isinstance(transaction_id, str) or not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
        raise OptimizerSubmissionError("transaction_id must be 32 lowercase hex characters")
    return ledger.path.parent / f"{ledger.episode_id}.optimizer-submissions" / f"{transaction_id}.json"


def _write(path: Path, record: dict) -> None:
    """Validate, then atomically replace the journal and fsync its directory."""
    _validator().validate(record)
    content = json.dumps(record, sort_keys=True, indent=2, allow_nan=False).encode("utf-8") + b"\n"
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        name = None
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if name is not None:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass


def _read(path: Path) -> dict | None:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate journal field")
            result[key] = value
        return result
    def reject(_value):
        raise ValueError("non-finite journal number")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise OptimizerSubmissionError(f"optimizer submission journal is unsafe: {path.name}") from error
    try:
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise OptimizerSubmissionError(f"optimizer submission journal is not a regular file: {path.name}")
            raw = handle.read(_MAX_JOURNAL + 1)
        if len(raw) > _MAX_JOURNAL:
            raise OptimizerSubmissionError(f"optimizer submission journal exceeds size limit: {path.name}")
        record = json.loads(raw, object_pairs_hook=unique, parse_constant=reject)
    except (OSError, ValueError) as error:
        raise OptimizerSubmissionError(f"optimizer submission journal is unreadable: {path.name}") from error
    if not _validator().is_valid(record) or record["transaction_id"] != path.stem:
        raise OptimizerSubmissionError(f"optimizer submission journal is malformed: {path.name}")
    for number, attempt in enumerate(record["attempts"], start=1):
        if attempt["attempt"] != number or attempt["request_sha256"] != _request_digest(record, attempt):
            raise OptimizerSubmissionError(f"optimizer submission journal digest mismatch: {path.name}")
        if attempt["status"] != _UNSENT and number != len(record["attempts"]):
            raise OptimizerSubmissionError(f"optimizer submission journal has a sent attempt before the latest: {path.name}")
    return record


@contextmanager
def _locked(path: Path):
    if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
        raise OptimizerSubmissionError("optimizer submission journal locking is unavailable")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise OptimizerSubmissionError("optimizer submission journal directory is a symlink")
    try:
        descriptor = os.open(path.with_name(f".{path.name}.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        raise OptimizerSubmissionError("optimizer submission journal lock is unavailable") from error
    with os.fdopen(descriptor, "r+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OptimizerSubmissionError("another writer owns this optimizer submission journal") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _request_digest(record: dict, attempt: dict) -> str:
    """Bind request bytes to the artifact, binding, request ID and funded ledger revision."""
    return hashlib.sha256(b"optimizer-submission-request-v1\0" + _canonical({
        "transaction_id": record["transaction_id"], "episode_id": record["episode_id"],
        "binding": record["binding"], "artifact_sha256": record["artifact_sha256"],
        "request_id": attempt["request_id"], "ledger_revision": attempt["ledger_revision"],
        "funding": attempt["funding"], "request": attempt["request"]})).hexdigest()


def _envelope(record: dict, attempt: dict) -> dict:
    return copy.deepcopy({"transaction_id": record["transaction_id"], "request_id": attempt["request_id"],
                          "request_sha256": attempt["request_sha256"], **attempt["request"]})


def _correlated(record: dict, attempt: dict, receipt) -> bool:
    return (isinstance(receipt, dict) and receipt.get("transaction_id") == record["transaction_id"]
            and receipt.get("request_id") == attempt["request_id"]
            and receipt.get("request_sha256") == attempt["request_sha256"] and type(receipt.get("ok")) is bool)


def _stored_receipt(receipt) -> dict:
    try:
        stored = json.loads(_canonical(receipt))
    except (TypeError, ValueError):
        return {"unserializable_receipt_type": type(receipt).__name__}
    return stored if isinstance(stored, dict) else {"non_object_receipt": stored}


def _settle(path: Path, record: dict, attempt: dict, receipt, status: str) -> dict:
    """Journal a submit/read result; any uncorrelated reply becomes terminal ambiguity."""
    if _correlated(record, attempt, receipt):
        attempt.update(status=status, receipt=_stored_receipt(receipt))
    else:
        attempt.update(status="ambiguous", receipt=_stored_receipt(receipt), reason="receipt_not_correlated")
    try:
        _write(path, record)
    except Exception as error:
        raise OptimizerSubmissionUncertain(
            "submission reply was not journaled; recover by reading the receipt, never by resending") from error
    if attempt["status"] == "ambiguous":
        raise OptimizerSubmissionUncertain("submission reply is not correlated with the journaled request")
    return copy.deepcopy(record)


def submit_optimizer_transaction(bound: BoundArtifactPlan, ledger: MaterialReservationLedger, *, episode_id: str,
                                 expected_ledger_revision: int, submitter) -> dict:
    """Send one exact request for a fully held bound project, at most once per attempt.

    The caller's ``expected_ledger_revision`` must match the persisted ledger at
    preparation and inside the fence. Stale ledgers, competing reservations,
    partial holds and prior sent or unresolved attempts refuse before sending.
    """
    if type(expected_ledger_revision) is not int or ledger.revision != expected_ledger_revision:
        raise OptimizerSubmissionError("ledger revision differs from the caller's funded view")
    try:
        preview = prepare_optimizer_layout_preview(bound, ledger, episode_id=episode_id, include_evidence_tags=True)
    except (MaterialReservationError, ValueError) as error:
        raise OptimizerSubmissionError(f"optimizer submission is not fully held: {error}") from error
    binding = bound.plan.binding
    project_id = f"optimizer:{binding.project_id}"
    project = ledger.projects[project_id]
    funding = {"state": project.state, "required": dict(project.required), "reserved": dict(project.reserved),
               "project_revision": project.revision}
    path = journal_path(ledger, binding.transaction_id)
    with _locked(path):
        record = _read(path)
        identity = {"version": VERSION, "transaction_id": binding.transaction_id, "episode_id": episode_id,
                    "project_id": project_id, "binding": binding.to_dict(), "artifact_sha256": bound.artifact_sha256}
        if record is None:
            record = {**identity, "attempts": []}
        elif {key: record[key] for key in identity} != identity:
            raise OptimizerSubmissionError("optimizer submission journal belongs to a different artifact or binding")
        elif any(attempt["status"] != _UNSENT for attempt in record["attempts"]):
            raise OptimizerSubmissionError("transaction already has a sent, uncertain or unrecovered attempt")
        attempt = {"attempt": len(record["attempts"]) + 1, "request_id": secrets.token_hex(16), "status": "prepared",
                   "ledger_revision": expected_ledger_revision, "funding": funding,
                   "request": {"authorization": preview["authorization"], "build_plan": preview["build_plan"]}}
        attempt["request_sha256"] = _request_digest(record, attempt)
        record["attempts"].append(attempt)
        try:
            _write(path, record)
        except Exception as error:
            raise OptimizerSubmissionError("prepared attempt was not journaled; nothing was sent") from error
        try:
            with ledger.revision_fence(expected_ledger_revision) as ledger_sha256:
                if ledger.projects.get(project_id) != project:
                    raise MaterialReservationError("bound project funding changed after preparation")
                attempt.update(status="submitting", ledger_sha256=ledger_sha256)
                try:
                    _write(path, record)
                except Exception as error:
                    # Nothing was sent. If the replace landed before the error,
                    # recovery treats the attempt as uncertain, which is safe.
                    raise OptimizerSubmissionError("submitting state was not journaled; nothing was sent") from error
                try:
                    receipt = submitter.submit(_envelope(record, attempt))
                except Exception as error:
                    raise OptimizerSubmissionUncertain(
                        "submission outcome is unknown; recover by reading the receipt, never by resending") from error
        except MaterialReservationError as error:
            attempt.update(status=_UNSENT, reason="ledger_changed_before_send")
            try:
                _write(path, record)
            except Exception:
                pass  # The durable prepared attempt resolves to not_sent during recovery.
            raise OptimizerSubmissionError(f"ledger changed before send; nothing was sent: {error}") from error
        return _settle(path, record, attempt, receipt, "received")


def recover_optimizer_transaction(ledger: MaterialReservationLedger, transaction_id: str, submitter) -> dict | None:
    """Resolve the latest attempt from durable evidence only; never resends.

    A ``prepared`` attempt was never sent and becomes ``not_sent``. A
    ``submitting`` attempt is resolved only by a correlated receipt read; an
    unavailable read leaves it uncertain. The ledger is never changed.
    """
    path = journal_path(ledger, transaction_id)
    with _locked(path):
        record = _read(path)
        if record is None:
            return None
        if record["episode_id"] != ledger.episode_id:
            raise OptimizerSubmissionError("optimizer submission journal belongs to a different episode")
        attempt = record["attempts"][-1]
        if attempt["status"] == "prepared":
            attempt.update(status=_UNSENT, reason="interrupted_before_send")
            try:
                _write(path, record)
            except Exception as error:
                raise OptimizerSubmissionError("unsent attempt was not journaled; retry recovery") from error
            return copy.deepcopy(record)
        if attempt["status"] != "submitting":
            return copy.deepcopy(record)
        try:
            receipt = submitter.read_receipt(_envelope(record, attempt))
        except Exception as error:
            raise OptimizerSubmissionUncertain("receipt is unavailable; outcome remains unknown") from error
        if receipt is None:
            raise OptimizerSubmissionUncertain("no correlated receipt exists; outcome remains unknown")
        return _settle(path, record, attempt, receipt, "recovered")
