# Path: training/laya_layout_stream.py | Purpose: Persist one in-flight disposable layout submission before sending it.
"""Single-writer, crash-aware sequencing; this does not authenticate a runtime."""

import copy
import json
import os
import re
import secrets
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from orchestrator.game_bridge import GameBridge

try:
    import fcntl
except ImportError:  # The saved-world runner currently requires its Linux lifecycle.
    fcntl = None


class LayoutStreamError(RuntimeError):
    pass


def atomic_json(path: Path, value: dict) -> None:
    """Replace a journal record after flushing its bytes and directory entry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as handle:
            name = handle.name
            json.dump(value, handle, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        name = None
        if hasattr(os, "O_DIRECTORY"):
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
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


@contextmanager
def _locked(root: Path):
    if fcntl is None:
        raise LayoutStreamError("layout stream locking is unavailable")
    evidence = Path(root) / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    if evidence.is_symlink() or not hasattr(os, "O_NOFOLLOW"):
        raise LayoutStreamError("layout stream directory or no-follow support is unsafe")
    try:
        descriptor = os.open(evidence / ".layout-stream.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        raise LayoutStreamError("layout stream lock is unavailable") from error
    with os.fdopen(descriptor, "r+") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise LayoutStreamError("layout stream lock is not a regular file")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LayoutStreamError("another layout submission owns this stream") from error
        try:
            yield evidence / "layout-stream.json"
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load(path: Path) -> dict:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate journal field")
            result[key] = value
        return result
    def invalid_number(_value):
        raise ValueError("non-finite journal number")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise LayoutStreamError("layout stream journal is not a regular file")
            raw = handle.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise LayoutStreamError("layout stream journal exceeds size limit")
        state = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_number)
    except FileNotFoundError:
        return {"version": 1, "stream_id": secrets.token_hex(8), "last_sequence": 0, "pending": None}
    except (OSError, ValueError) as error:
        raise LayoutStreamError("layout stream journal is unreadable") from error
    if (not isinstance(state, dict) or set(state) != {"version", "stream_id", "last_sequence", "pending"}
            or type(state["version"]) is not int or state["version"] != 1
            or not isinstance(state["stream_id"], str) or not re.fullmatch(r"[0-9a-f]{16}", state["stream_id"])
            or type(state["last_sequence"]) is not int or not 0 <= state["last_sequence"] <= 4294967295):
        raise LayoutStreamError("layout stream journal is malformed")
    pending = state["pending"]
    if pending is not None:
        if (not isinstance(pending, dict)
                or set(pending) != {"sequence", "request_id", "authorization", "build_plan", "transaction"}
                or type(pending["sequence"]) is not int or pending["sequence"] != state["last_sequence"] + 1
                or pending["sequence"] > 4294967295
                or pending["request_id"] != f"{state['stream_id']}{pending['sequence']:016x}"
                or not isinstance(pending["authorization"], dict) or not isinstance(pending["build_plan"], dict)
                or not isinstance(pending["transaction"], str)
                or not re.fullmatch(r"transaction-[0-9a-f]{32}\.json", pending["transaction"])):
            raise LayoutStreamError("pending layout submission is malformed")
    return state


def submit(root: Path, bridge, authorization: dict, build_plan: dict, *, transaction: str, prepared) -> dict:
    """Persist a unique stream request, notify its transaction, then submit once."""
    if not isinstance(transaction, str) or not re.fullmatch(r"transaction-[0-9a-f]{32}\.json", transaction):
        raise ValueError("transaction must be a generated transaction filename")
    if not isinstance(authorization, dict) or not isinstance(build_plan, dict):
        raise ValueError("authorization and build_plan must be objects")
    authorization, build_plan = json.loads(json.dumps([authorization, build_plan], allow_nan=False))
    with _locked(root) as path:
        state = _load(path)
        if state["pending"] is not None:
            raise LayoutStreamError("an earlier layout submission has an unknown outcome")
        sequence = state["last_sequence"] + 1
        if sequence > 4294967295:
            raise LayoutStreamError("layout stream sequence exhausted")
        request_id = f"{state['stream_id']}{sequence:016x}"
        # Reject locally malformed requests before creating an ambiguous journal entry.
        GameBridge._layout_receipt_request(authorization, build_plan, request_id,
                                          stream_id=state["stream_id"], sequence=sequence)
        pending = {"sequence": sequence, "request_id": request_id, "authorization": authorization,
                   "build_plan": build_plan, "transaction": transaction}
        state["pending"] = pending
        atomic_json(path, state)
        prepared(copy.deepcopy({"stream_id": state["stream_id"], **pending}))
        report = bridge.build_layout_correlated(authorization, build_plan, request_id=request_id,
                                                stream_id=state["stream_id"], sequence=sequence, timeout=30)
        state["last_sequence"] = sequence
        state["pending"] = None
        atomic_json(path, state)
        return report


def recover(root: Path, bridge, *, resolved=None) -> tuple[dict, dict] | None:
    """Resolve a pending stream only from an existing correlated file; never resend."""
    with _locked(root) as path:
        state = _load(path)
        pending = state["pending"]
        if pending is None:
            return None
        report = bridge.read_layout_receipt(pending["authorization"], pending["build_plan"],
                                            request_id=pending["request_id"], stream_id=state["stream_id"],
                                            sequence=pending["sequence"])
        request = copy.deepcopy({"stream_id": state["stream_id"], **pending})
        if resolved is not None:
            resolved(request, report)
        state["last_sequence"] = pending["sequence"]
        state["pending"] = None
        atomic_json(path, state)
        return request, report
