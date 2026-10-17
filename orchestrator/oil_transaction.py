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

    def material_facts(self, client, ledger=None) -> dict | None:
        """Read the selected packet bill without replanning, advancing or certifying service completion.

        Installed placements credit only this immutable manifest. Requester/buffer WIP cannot fund it;
        packet reservations are its own claims, while other projects and ghosts retain their claims.
        """
        from orchestrator import live_base

        transaction = self.load()
        if transaction is None:
            return None
        base = {"identity": dict(self.identity), "intent_sha256": transaction["intent_sha256"],
                "target_item": transaction["intent"]["target_output"], "complete": transaction["complete"]}
        packet_ids = [name for name, _packet in transaction["intent"]["packets"]]
        base["packet_ids"] = packet_ids
        base["replaces_shortage_stages"] = ["chemical_oil_cell_packets", *packet_ids,
                                            *(f"{name}_foundation" for name in packet_ids)]
        if transaction["complete"]:
            return {**base, "remaining": {}, "missing": {}, "stock_levels": {}}
        packets = transaction["intent"]["packets"]
        plan = {"phases": [phase for _name, packet in packets for phase in packet["phases"]]}
        observed = live_base.plan_construction_status(client, self.identity["surface"], self.identity["force"], plan)
        stock = live_base.transferable_items(client, self.identity["surface"], self.identity["force"])
        owned = tuple((a["entity"], (a["position"]["x"], a["position"]["y"]))
                      for phase in plan["phases"] for a in phase["actions"]
                      if a.get("action_type") in {"place_entity", "place_ghost"})
        pending = live_base.pending_construction_items(
            client, self.identity["surface"], self.identity["force"], exclude=owned)
        uncommitted = {item: max(0, count - pending.get(item, 0)) for item, count in stock.items()}
        claimants = {name for name, _packet in packets} | {f"{name}_foundation" for name, _packet in packets}
        available = ledger.allocatable_stock(uncommitted, claimants=claimants) if ledger else uncommitted
        missing = {item: count - available.get(item, 0) for item, count in observed["remaining"].items()
                   if count > available.get(item, 0)}
        return {**base, **observed,
                "transferable_stock": stock, "available_to_owner": available,
                "foreign_pending": pending, "missing": missing,
                "stock_levels": {item: stock.get(item, 0) + count for item, count in missing.items()}}

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
