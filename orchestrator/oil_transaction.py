# Path: orchestrator/oil_transaction.py
# Purpose: Persist immutable episode-owned opening oil construction intent.

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

import jsonschema


class OilTransactionError(ValueError):
    """Unsafe or unreadable ownership state must never trigger re-siting."""


def _encoded(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class OilTransactionStore:
    def __init__(self, script_output: Path, episode_id: str, surface: str, force: str):
        self.identity = {"episode_id": episode_id, "surface": surface, "force": force}
        if not all(isinstance(v, str) and v for v in self.identity.values()):
            raise OilTransactionError("Oil transaction requires explicit runtime identity")
        token = hashlib.sha256(_encoded(self.identity).encode()).hexdigest()
        self.path = Path(script_output).parent / "logs" / "oil-transactions" / f"{token}.json"

    @classmethod
    def for_bridge(cls, bridge, surface: str, force: str):
        episode_id = getattr(bridge, "episode_id", None)
        # Non-campaign callers use the same legacy mode as the district ledgers.
        if episode_id is None:
            return None
        return cls(bridge.script_output, episode_id, surface, force)

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text())
            schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "oil_transaction.schema.json").read_text())
            jsonschema.validate(data, schema)
            if data["identity"] != self.identity:
                raise OilTransactionError("Oil transaction identity mismatch")
            digest = hashlib.sha256(_encoded(data["intent"]).encode()).hexdigest()
            if digest != data["intent_sha256"]:
                raise OilTransactionError("Oil transaction intent checksum mismatch")
            return data
        except (OSError, ValueError, TypeError, jsonschema.ValidationError) as error:
            raise OilTransactionError(f"Invalid oil transaction {self.path}: {error}") from error

    def save(self, intent: dict, *, complete: bool) -> None:
        # Normalize tuples before comparing, and reject replacement after submission.
        intent = json.loads(_encoded(intent))
        previous = self.load()
        if previous is not None and previous["intent"] != intent:
            raise OilTransactionError("Cannot replace reserved opening oil geometry")
        data = {
            "version": 1, "identity": self.identity, "intent": intent,
            "intent_sha256": hashlib.sha256(_encoded(intent).encode()).hexdigest(),
            "complete": complete,
        }
        schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "oil_transaction.schema.json").read_text())
        jsonschema.validate(data, schema)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(_encoded(data))
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
            # Persist the directory entry as well as the file before submission.
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
